import time

import numpy as np
import pytest

from voxbridge.audio import AudioService, SampleBuffer, list_devices, resolve_device
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
    def __init__(self):
        self.outputs = []
        self.inputs = []

    def query_hostapis(self):
        return [{"name": "WASAPI"}]

    def query_devices(self):
        return [dict(name=name, hostapi=0, max_input_channels=ins, max_output_channels=outs)
                for name, ins, outs in [("Mic", 1, 0), ("Cable", 0, 2), ("Headphones", 0, 2)]]

    def check_input_settings(self, **kwargs):
        pass

    def check_output_settings(self, **kwargs):
        pass

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
        fake.outputs[1].callback(monitor, frames, None, None)
        np.testing.assert_allclose(output, 0.2)
        np.testing.assert_array_equal(monitor, 0)
        service.set_monitor(True, 0.5)
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


def test_monitor_and_output_cannot_be_same_device():
    settings = configuration()
    settings.monitor_device = settings.output_device
    with pytest.raises(ValueError):
        AudioService(FakeEngine, FakeAudio()).start(settings)


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
