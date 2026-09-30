import hashlib
import io
import json
from pathlib import Path

import pytest

from voxbridge import assets
from voxbridge.diagnostics import export_diagnostics
from voxbridge.settings import AppSettings, load_settings, save_settings


def test_settings_roundtrip_and_invalid_values(tmp_path):
    settings = AppSettings(monitor_enabled=True, pitch=-4, model_path="聲線.pth")
    path = tmp_path / "settings.json"
    save_settings(settings, path)
    assert load_settings(path) == settings
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["settings"]["monitor_enabled"] = "false"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)
    settings.index_rate = float("nan")
    with pytest.raises(ValueError):
        save_settings(settings, path)


def test_download_verifies_then_atomically_replaces_and_can_skip(tmp_path, monkeypatch):
    body = b"verified model"
    monkeypatch.setattr(assets, "MANIFEST", {"hubert/model.bin": (len(body), hashlib.sha256(body).hexdigest())})
    calls = []
    def fetch(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(body)
    monkeypatch.setattr(assets, "urlopen", fetch)
    assets.download_assets(tmp_path)
    assert not assets.verify_assets(tmp_path)
    assets.download_assets(tmp_path)
    assert len(calls) == 1


def test_download_corruption_preserves_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "model.bin"
    target.write_bytes(b"previous")
    monkeypatch.setattr(assets, "MANIFEST", {"model.bin": (3, hashlib.sha256(b"yes").hexdigest())})
    monkeypatch.setattr(assets, "urlopen", lambda *a, **kw: io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="校驗"):
        assets.download_assets(tmp_path)
    assert target.read_bytes() == b"previous"
    assert not (tmp_path / "model.bin.part").exists()


def test_diagnostics_json_redacts_home_and_excludes_model_contents(tmp_path):
    settings = AppSettings(model_path=str(Path.home() / "secret" / "voice.pth"))
    destination = export_diagnostics(tmp_path / "report.json", settings,
        {"error": f"Cannot load {Path.home() / 'secret' / 'voice.pth'}"}, [])
    text = destination.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["settings"]["model_path"] == "voice.pth"
    assert str(Path.home()) not in data["audio"]["error"]


def test_diagnostics_redacts_last_session_paths_without_mutating_snapshot(tmp_path):
    snapshot = {"session_settings": {
        "model_path": r"D:\private\voices\voice.pth",
        "index_path": r"D:\private\voices\voice.index",
        "assets_dir": r"D:\private\assets",
        "sample_rate": 48000,
    }}
    destination = export_diagnostics(tmp_path / "report.json", AppSettings(), snapshot, [])
    data = json.loads(destination.read_text(encoding="utf-8"))
    assert data["audio"]["session_settings"] == {
        "model_path": "voice.pth", "index_path": "voice.index",
        "assets_dir": "assets", "sample_rate": 48000,
    }
    assert snapshot["session_settings"]["model_path"].startswith("D:")
