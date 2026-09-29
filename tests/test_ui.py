"""Qt interface contract tests without audio hardware or model weights."""

import os
import json
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from voxbridge import app as ui
from voxbridge.audio import DeviceInfo
from voxbridge.settings import AppSettings


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _spin(qt_app, predicate, timeout=2.0):
    limit = time.monotonic() + timeout
    while not predicate() and time.monotonic() < limit:
        qt_app.processEvents()
        time.sleep(0.01)
    qt_app.processEvents()
    assert predicate()


class FakeService:
    def __init__(self):
        self.state = "stopped"
        self.started = None
        self.monitor_calls = []
        self.output_gains = []

    def start(self, settings):
        self.started = settings
        self.state = "loading"

    def stop(self):
        self.state = "stopping"

    def snapshot(self):
        return {"state": self.state}

    def set_monitor(self, enabled, gain=None):
        self.monitor_calls.append((enabled, gain))

    def set_output_gain(self, gain):
        self.output_gains.append(gain)


DEVICES = [
    DeviceInfo("wasapi :: Mic", "Mic [WASAPI]", 0, 1, 0),
    DeviceInfo("wasapi :: CABLE Input", "CABLE Input [WASAPI]", 1, 0, 2),
    DeviceInfo("wasapi :: Headphones", "Headphones [WASAPI]", 2, 0, 2),
]


def test_smoke_mode_does_not_write_settings(qt_app, monkeypatch):
    monkeypatch.setattr(ui, "save_settings", lambda *_: pytest.fail("smoke test wrote settings"))
    window = ui.VoxBridgeWindow(smoke_test=True)
    window.show()
    qt_app.processEvents()
    window.close()
    qt_app.processEvents()
    assert not window.isVisible()


def test_start_persists_routing_and_monitor_is_independent(qt_app, monkeypatch, tmp_path):
    model = tmp_path / "voice.pth"
    model.write_bytes(b"model")
    saved = []
    monkeypatch.setattr(ui, "load_settings", lambda: AppSettings(assets_dir=str(tmp_path)))
    monkeypatch.setattr(ui, "save_settings", lambda settings: saved.append(settings))
    monkeypatch.setattr("voxbridge.assets.check_assets", lambda _: [])
    service = FakeService()
    window = ui.VoxBridgeWindow(smoke_test=True, service=service, devices=DEVICES)
    window.model_edit.setText(str(model))
    window.input_combo.setCurrentIndex(1)
    window.output_combo.setCurrentIndex(1)
    window.monitor_combo.setCurrentIndex(2)
    window._start()
    assert service.state == "loading"
    assert saved[-1].output_device == "wasapi :: CABLE Input"
    assert saved[-1].monitor_device == "wasapi :: Headphones"
    assert not saved[-1].monitor_enabled
    assert not window.output_combo.isEnabled()
    window.monitor_checkbox.setChecked(True)
    window.monitor_slider.setValue(55)
    window.output_slider.setValue(120)
    assert service.monitor_calls[-1] == (True, 0.55)
    assert service.output_gains[-1] == 1.2
    assert service.started.output_device == "wasapi :: CABLE Input"
    window.monitor_checkbox.setChecked(False)
    assert service.monitor_calls[-1][0] is False
    assert service.state == "loading"  # Turning off monitoring leaves Discord output active.
    service.state = "stopped"
    window._poll()
    window.close()


def test_close_waits_for_background_download(qt_app, monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def download(_directory, callback):
        started.set()
        callback("下載中")
        assert release.wait(3)

    monkeypatch.setattr("voxbridge.assets.download_assets", download)
    monkeypatch.setattr(ui, "load_settings", lambda: AppSettings(assets_dir=str(tmp_path)))
    monkeypatch.setattr(ui, "save_settings", lambda *_: None)
    window = ui.VoxBridgeWindow(smoke_test=True, devices=DEVICES)
    window.show()
    window._download_assets()
    _spin(qt_app, started.is_set)
    window.close()
    assert window.isVisible()
    assert window._download_thread is not None
    release.set()
    _spin(qt_app, lambda: window._download_thread is None)
    _spin(qt_app, lambda: not window.isVisible())


def test_close_waits_for_audio_loading(qt_app, monkeypatch):
    monkeypatch.setattr(ui, "load_settings", lambda: AppSettings())
    monkeypatch.setattr(ui, "save_settings", lambda *_: None)
    service = FakeService()
    service.state = "loading"
    window = ui.VoxBridgeWindow(smoke_test=True, service=service, devices=DEVICES)
    window._state = "loading"
    window.show()
    window.close()
    assert window.isVisible()
    assert service.state == "stopping"
    service.state = "stopped"
    window._poll()
    _spin(qt_app, lambda: not window.isVisible())


def test_rejected_monitor_enable_restores_switch(qt_app, monkeypatch):
    monkeypatch.setattr(ui, "load_settings", lambda: AppSettings())
    service = FakeService()
    service.state = "running"

    def reject(*_):
        raise ValueError("監聽裝置已中斷")

    service.set_monitor = reject
    window = ui.VoxBridgeWindow(smoke_test=True, service=service, devices=DEVICES)
    window.monitor_combo.setCurrentIndex(2)
    window._state = "running"
    window.monitor_checkbox.setChecked(True)
    assert not window.monitor_checkbox.isChecked()
    service.state = "stopped"
    window._poll()
    window.close()


def test_engine_check_writes_report_for_windowed_build(monkeypatch, tmp_path):
    def missing(_name):
        raise ImportError("missing inference dependency")

    monkeypatch.setattr(ui.importlib, "import_module", missing)
    report = tmp_path / "engine-check.json"
    assert ui.main(["--check-engine", "--check-engine-report", str(report)]) == 1
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "ok": False,
        "error": "missing inference dependency",
    }
