import time

import numpy as np
import pytest

from voxbridge.audio import AudioService, SampleBuffer, StreamingResampler, list_devices, refresh_devices, resolve_device
from voxbridge.settings import AppSettings


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for audio worker")


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.active = False
        self.closed = False

    def start(self):
        self.active = True

    def abort(self):
        self.active = False

    def close(self):
        self.closed = True


class FakeAudio:
    def __init__(self, allowed=None, devices=None):
        self.outputs = []
        self.inputs = []
        self.allowed = allowed
        self.devices = devices
        self.terminations = 0
        self.initializations = 0

    def query_hostapis(self):
        return [{"name": "WASAPI"}]

    def query_devices(self):
        if self.devices is not None:
            return self.devices
        return [dict(name=name, hostapi=0, max_input_channels=ins, max_output_channels=outs)
                for name, ins, outs in [("Mic", 1, 0), ("Cable", 0, 2), ("Headphones", 0, 2)]]

    def check_input_settings(self, **kwargs):
        if self.allowed is not None and ("input", kwargs["device"], kwargs["samplerate"], kwargs["channels"]) not in self.allowed:
            raise ValueError("Invalid sample rate [-9997]")

    def check_output_settings(self, **kwargs):
        if self.allowed is not None and ("output", kwargs["device"], kwargs["samplerate"], kwargs["channels"]) not in self.allowed:
            raise ValueError("Invalid sample rate [-9997]")

    def _terminate(self):
        self.terminations += 1

    def _initialize(self):
        self.initializations += 1

    def InputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        self.inputs.append(stream)
        return stream

    def OutputStream(self, **kwargs):
        stream = FakeStream(**kwargs)
        self.outputs.append(stream)
        return stream


class FakeEngine:
    def __init__(self, settings):
        self.closed = False
        self.runtime_info = {"device": "fake"}

    def load(self):
        pass

    def process(self, audio):
        return audio * 2

    def close(self):
        self.closed = True


def configuration():
    return AppSettings(model_path="model.pth", input_device="WASAPI :: Mic",
        output_device="WASAPI :: Cable", monitor_device="WASAPI :: Headphones")


def test_sample_buffer_variable_frames_and_bounded_lag():
    buffer = SampleBuffer(6)
    assert not buffer.put(np.arange(4))
    np.testing.assert_array_equal(buffer.read(3)[0], [0, 1, 2])
    buffer.put(np.array([4, 5]))
    samples, underrun = buffer.read(5)
    np.testing.assert_array_equal(samples, [3, 4, 5, 0, 0])
    assert underrun
    buffer.put(np.arange(5))
    assert buffer.put(np.array([8, 9]))
    np.testing.assert_array_equal(buffer.read(2)[0], [8, 9])


def test_streaming_resampler_has_continuous_timing_across_blocks():
    pytest.importorskip("soxr")
    converter = StreamingResampler(44100, 48000)
    signal = np.sin(2 * np.pi * 220 * np.arange(88200) / 44100).astype(np.float32)
    output = np.concatenate([converter.process(chunk) for chunk in np.split(signal, 10)])
    whole = StreamingResampler(44100, 48000).process(signal)
    assert abs(output.size - 96000) < 1024  # SoXR's bounded filter delay.
    assert output.size == whole.size
    np.testing.assert_allclose(output, whole, atol=1e-5)
    assert np.max(np.abs(np.diff(output))) < 0.06


def test_monitor_toggle_does_not_mute_discord_and_no_stale_audio():
    fake = FakeAudio()
    service = AudioService(FakeEngine, fake)
    service.start(configuration())
    try:
        until(lambda: service.snapshot()["state"] == "running")
        frames = 9600
        block = np.full((frames, 1), 0.1, dtype=np.float32)
        fake.inputs[0].callback(block, frames, None, None)
        until(lambda: service.snapshot()["output_rms"] > 0)
        output = np.empty((frames, 2), np.float32)
        monitor = np.empty_like(output)
        fake.outputs[0].callback(output, frames, None, None)
        np.testing.assert_allclose(output, 0.2)
        assert len(fake.outputs) == 1  # Disabled monitor does not open hardware.
        service.set_monitor(True, 0.5)
        until(lambda: len(fake.outputs) == 2 and service.snapshot()["monitor_enabled"])
        fake.outputs[1].callback(monitor, frames, None, None)
        np.testing.assert_array_equal(monitor, 0)  # Does not replay old muted audio.
        block.fill(0.2)
        fake.inputs[0].callback(block, frames, None, None)
        until(lambda: service.snapshot()["output_rms"] > 0.3)
        fake.outputs[0].callback(output, frames, None, None)
        fake.outputs[1].callback(monitor, frames, None, None)
        np.testing.assert_allclose(output, 0.4)
        np.testing.assert_allclose(monitor, 0.2)
        service.set_monitor(False)
        fake.outputs[1].callback(monitor, frames, None, None)
        np.testing.assert_array_equal(monitor, 0)
        until(lambda: fake.outputs[1].closed)
    finally:
        service.stop()
        assert service.wait(3)
    assert all(s.closed for s in fake.inputs + fake.outputs)


def test_inference_failure_stops_output_and_closes_devices():
    class Broken(FakeEngine):
        def process(self, audio):
            raise RuntimeError("GPU unavailable")
    fake = FakeAudio()
    service = AudioService(Broken, fake)
    service.start(configuration())
    until(lambda: service.snapshot()["state"] == "running")
    fake.inputs[0].callback(np.ones((9600, 1), np.float32), 9600, None, None)
    until(lambda: service.snapshot()["state"] == "error")
    assert service.wait(3)
    assert "GPU unavailable" in service.snapshot()["error"]
    assert all(s.closed for s in fake.inputs + fake.outputs)
    out = np.ones((64, 2), np.float32)
    fake.outputs[0].callback(out, 64, None, None)
    np.testing.assert_array_equal(out, 0)


def test_missing_device_is_not_replaced_with_system_default():
    with pytest.raises(ValueError):
        resolve_device(list_devices(FakeAudio()), "old mic", "inputs")


def test_same_monitor_and_output_coalesce_into_one_stream():
    settings = configuration()
    settings.monitor_device = settings.output_device
    settings.monitor_enabled = True
    fake = FakeAudio()
    service = AudioService(FakeEngine, fake)
    service.start(settings)
    try:
        until(lambda: service.snapshot()["state"] == "running")
        assert len(fake.outputs) == 1
        service.set_monitor(False)
        fake.inputs[0].callback(np.ones((9600, 1), np.float32), 9600, None, None)
        until(lambda: service.snapshot()["output_rms"] > 0)
        out = np.empty((9600, 2), np.float32)
        fake.outputs[0].callback(out, 9600, None, None)
        np.testing.assert_allclose(out, 1.0)  # Main output is still active.
    finally:
        service.stop()
        assert service.wait(3)


def test_stop_during_model_loading_opens_no_audio_streams():
    import threading
    entered, release = threading.Event(), threading.Event()
    class Loading(FakeEngine):
        def load(self):
            entered.set()
            release.wait(3)
    fake = FakeAudio()
    service = AudioService(Loading, fake)
    service.start(configuration())
    assert entered.wait(3)
    service.stop()
    release.set()
    assert service.wait(3)
    assert service.snapshot()["state"] == "stopped"
    assert not fake.inputs and not fake.outputs


def test_device_list_prefers_wasapi_and_advanced_shows_legacy():
    class Mixed(FakeAudio):
        def query_hostapis(self):
            return [{"name": "Windows WASAPI"}, {"name": "Windows WDM-KS"}]

    raw = [dict(name="Ghost Bluetooth", hostapi=1, max_input_channels=1,
                max_output_channels=0, default_samplerate=48000),
           dict(name="Real Mic", hostapi=0, max_input_channels=1,
                max_output_channels=0, default_samplerate=44100)]
    fake = Mixed(devices=raw)
    assert [d.label for d in list_devices(fake)] == ["Real Mic [Windows WASAPI]"]
    assert len(refresh_devices(show_advanced=True, audio_module=fake)) == 2
    assert fake.terminations == fake.initializations == 1
    fake.devices = raw[:1]  # WASAPI host exists but has no enumerated endpoint.
    assert list_devices(fake) == []
    assert len(list_devices(fake, show_advanced=True)) == 1


def test_unusable_sample_rate_reports_role_and_attempts():
    fake = FakeAudio(allowed=set())
    service = AudioService(FakeEngine, fake)
    service.start(configuration())
    until(lambda: service.snapshot()["state"] == "error")
    assert service.wait(3)
    snap = service.snapshot()
    assert "麥克風" in snap["error"] and "Mic" in snap["error"]
    assert "48000" in snap["error"] and "44100" in snap["error"]
    assert "-9997" not in snap["error"]
    assert snap["error_stage"] == "裝置協商"


def test_44100_stereo_input_and_16000_monitor_convert_to_engine_blocks():
    pytest.importorskip("soxr")
    raw = [dict(name="Mic", hostapi=0, max_input_channels=2, max_output_channels=0,
                default_samplerate=44100),
           dict(name="Cable", hostapi=0, max_input_channels=0, max_output_channels=2,
                default_samplerate=48000),
           dict(name="Headphones", hostapi=0, max_input_channels=0, max_output_channels=2,
                default_samplerate=16000)]
    allowed = {("input", 0, 44100, 2), ("output", 1, 48000, 2),
               ("output", 2, 16000, 2)}
    fake = FakeAudio(allowed=allowed, devices=raw)
    service = AudioService(FakeEngine, fake)
    config = configuration()
    config.monitor_enabled = True
    service.start(config)
    try:
        until(lambda: service.snapshot()["state"] == "running" and len(fake.outputs) == 2)
        assert fake.inputs[0].kwargs["samplerate"] == 44100
        assert fake.inputs[0].kwargs["channels"] == 2
        assert fake.inputs[0].kwargs["blocksize"] == 8820
        assert fake.outputs[0].kwargs["samplerate"] == 48000
        assert fake.outputs[1].kwargs["samplerate"] == 16000
        for _ in range(4):
            stereo = np.tile(np.array([0.1, 0.3], dtype=np.float32), (8820, 1))
            fake.inputs[0].callback(stereo, 8820, None, None)
            time.sleep(0.04)
        until(lambda: service.snapshot()["output_rms"] > 0.3)
        output = np.zeros((9600, 2), np.float32)
        monitor = np.zeros((3200, 2), np.float32)
        fake.outputs[0].callback(output, 9600, None, None)
        fake.outputs[1].callback(monitor, 3200, None, None)
        assert np.max(np.abs(output)) > 0.1
        assert np.max(np.abs(monitor)) > 0.05
        assert service.snapshot()["input_rms"] == pytest.approx(0.2, abs=0.01)
        assert service.snapshot()["runtime_info"]["audio"]["input"]["rate"] == 44100
        assert service.snapshot()["runtime_info"]["audio"]["monitor"]["rate"] == 16000
    finally:
        service.stop()
        assert service.wait(3)


def test_monitor_only_needs_no_discord_output_and_toggle_keeps_engine_running():
    fake = FakeAudio()
    service = AudioService(FakeEngine, fake)
    config = configuration()
    config.output_mode = "monitor_only"
    config.output_device = ""
    config.monitor_enabled = True
    config.monitor_gain = 0.5
    service.start(config)
    try:
        until(lambda: service.snapshot()["state"] == "running")
        assert len(fake.outputs) == 1
        assert fake.outputs[0].kwargs["device"] == 2
        fake.inputs[0].callback(np.full((9600, 1), 0.1, np.float32), 9600, None, None)
        until(lambda: service.snapshot()["output_rms"] > 0)
        out = np.zeros((9600, 2), np.float32)
        fake.outputs[0].callback(out, 9600, None, None)
        np.testing.assert_allclose(out, 0.1)
        service.set_monitor(False)
        fake.outputs[0].callback(out, 9600, None, None)
        np.testing.assert_array_equal(out, 0)
        assert service.snapshot()["state"] == "running"
        service.set_monitor(True)
        fake.inputs[0].callback(np.full((9600, 1), 0.2, np.float32), 9600, None, None)
        until(lambda: service.snapshot()["output_rms"] > 0.3)
        fake.outputs[0].callback(out, 9600, None, None)
        np.testing.assert_allclose(out, 0.2)
    finally:
        service.stop()
        assert service.wait(3)


def test_disabled_missing_monitor_does_not_block_main_stream():
    fake = FakeAudio()
    service = AudioService(FakeEngine, fake)
    config = configuration()
    config.monitor_device = "WASAPI :: Unplugged"
    service.start(config)
    try:
        until(lambda: service.snapshot()["state"] == "running")
        assert len(fake.outputs) == 1
        service.set_monitor(True)
        until(lambda: bool(service.snapshot()["monitor_error"]))
        assert service.snapshot()["state"] == "running"
        assert service.snapshot()["monitor_enabled"] is False
        assert len(fake.outputs) == 1
    finally:
        service.stop()
        assert service.wait(3)


def test_monitor_disconnect_does_not_stop_discord_or_retry_loop():
    fake = FakeAudio()
    service = AudioService(FakeEngine, fake)
    config = configuration()
    config.monitor_enabled = True
    service.start(config)
    try:
        until(lambda: service.snapshot()["state"] == "running" and len(fake.outputs) == 2)
        def abort_fails():
            raise RuntimeError("device vanished")
        fake.outputs[1].abort = abort_fails
        fake.outputs[1].active = False
        until(lambda: bool(service.snapshot()["monitor_error"]))
        assert service.snapshot()["state"] == "running"
        assert service.snapshot()["monitor_enabled"] is False
        assert fake.outputs[1].closed
        time.sleep(0.1)
        assert len(fake.outputs) == 2  # No immediate reopen loop.
        service.set_monitor(True)
        assert service.snapshot()["monitor_error"] == ""
        until(lambda: len(fake.outputs) == 3 and service.snapshot()["monitor_enabled"])
    finally:
        service.stop()
        assert service.wait(3)


def test_stream_open_failure_has_endpoint_context_after_successful_check():
    class FailingOutput(FakeAudio):
        def OutputStream(self, **kwargs):
            raise RuntimeError("Invalid sample rate [-9997]")

    service = AudioService(FakeEngine, FailingOutput())
    service.start(configuration())
    until(lambda: service.snapshot()["state"] == "error")
    assert service.wait(3)
    error = service.snapshot()["error"]
    assert "Discord 對外輸出" in error and "Cable" in error
    assert "48000 Hz" in error and "2 聲道" in error
    assert service.snapshot()["error_stage"] == "開啟音訊串流"


def test_snapshot_keeps_started_settings_when_ui_changes_later():
    fake = FakeAudio()
    service = AudioService(FakeEngine, fake)
    config = configuration()
    service.start(config)
    try:
        until(lambda: service.snapshot()["state"] == "running")
        config.input_device = "changed after start"
        first = service.snapshot()
        first["session_settings"]["input_device"] = "mutated snapshot"
        assert service.snapshot()["session_settings"]["input_device"] == "WASAPI :: Mic"
    finally:
        service.stop()
        assert service.wait(3)


def test_saved_advanced_device_still_resolves_for_running_stream():
    class Mixed(FakeAudio):
        def query_hostapis(self):
            return [{"name": "Windows WASAPI"}, {"name": "Windows MME"}]

    raw = [dict(name="Mic", hostapi=0, max_input_channels=1, max_output_channels=0),
           dict(name="Cable", hostapi=1, max_input_channels=0, max_output_channels=2)]
    fake = Mixed(devices=raw)
    config = configuration()
    config.input_device = "Windows WASAPI :: Mic"
    config.output_device = "Windows MME :: Cable"
    config.monitor_device = ""
    service = AudioService(FakeEngine, fake)
    service.start(config)
    try:
        until(lambda: service.snapshot()["state"] == "running")
        assert fake.outputs[0].kwargs["device"] == 1
    finally:
        service.stop()
        assert service.wait(3)
