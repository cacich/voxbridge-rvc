"""Bounded audio transport. Inference never runs inside a PortAudio callback."""

from collections import deque
from dataclasses import dataclass, replace
import logging
import queue
import threading
import time

import numpy as np

from .settings import AppSettings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceInfo:
    key: str
    label: str
    index: int
    inputs: int
    outputs: int


def list_devices(audio_module=None) -> list[DeviceInfo]:
    if audio_module is None:
        import sounddevice as audio_module
    hosts = audio_module.query_hostapis()
    devices = []
    for index, device in enumerate(audio_module.query_devices()):
        host = hosts[device["hostapi"]]["name"]
        name = device["name"]
        devices.append(DeviceInfo(f"{host} :: {name}", f"{name} [{host}]", index,
                                  device["max_input_channels"], device["max_output_channels"]))
    return devices


def resolve_device(devices: list[DeviceInfo], key: str, direction: str) -> DeviceInfo:
    matches = [d for d in devices if d.key == key and getattr(d, direction) > 0]
    if len(matches) != 1:
        raise ValueError("找不到唯一的音訊裝置，請重新整理並選擇裝置：" + (key or "尚未選擇"))
    return matches[0]


class SampleBuffer:
    """Small FIFO supporting host callbacks of arbitrary frame length.

    When the consumer falls behind, discard stale audio instead of accumulating
    seconds of delay. The output and monitor own independent buffers.
    """

    def __init__(self, capacity: int):
        self.capacity = capacity
        self._chunks = deque()
        self._size = 0
        self._offset = 0
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._chunks.clear()
            self._size = self._offset = 0

    def put(self, samples: np.ndarray) -> bool:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
        with self._lock:
            dropped = self._size + samples.size > self.capacity
            if dropped:
                self._chunks.clear()
                self._size = self._offset = 0
            samples = samples[-self.capacity:]
            self._chunks.append(samples)
            self._size += samples.size
            return dropped

    def read(self, frames: int) -> tuple[np.ndarray, bool]:
        result = np.zeros(frames, dtype=np.float32)
        with self._lock:
            cursor = 0
            while cursor < frames and self._chunks:
                chunk = self._chunks[0]
                count = min(frames - cursor, len(chunk) - self._offset)
                result[cursor:cursor + count] = chunk[self._offset:self._offset + count]
                self._offset += count
                self._size -= count
                cursor += count
                if self._offset == len(chunk):
                    self._chunks.popleft()
                    self._offset = 0
            return result, cursor < frames


def _engine_factory(settings):
    from .engine import RvcEngine
    return RvcEngine(settings)


class AudioService:
    def __init__(self, engine_factory=None, audio_module=None):
        self._engine_factory = engine_factory or _engine_factory
        self._audio_module = audio_module
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._monitor_enabled = False
        self._monitor_gain = 0.7
        self._output_gain = 1.0
        self._monitor_buffer = None
        self._monitor_available = False
        self._stats = self._initial_stats()

    @staticmethod
    def _initial_stats():
        return dict(state="stopped", error="", input_rms=0.0, output_rms=0.0,
                    process_ms=0.0, input_drops=0, output_underruns=0,
                    monitor_underruns=0, overruns=0, runtime_info={})

    def snapshot(self):
        with self._lock:
            return {**self._stats, "runtime_info": dict(self._stats["runtime_info"])}

    def _update(self, **values):
        with self._lock:
            self._stats.update(values)

    def _count(self, key):
        with self._lock:
            self._stats[key] += 1

    def start(self, settings: AppSettings):
        settings = replace(settings)
        settings.validate()
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("上一個音訊工作尚未停止")
            if not settings.model_path:
                raise ValueError("請先選擇 RVC 模型 .pth")
            if not settings.input_device or not settings.output_device:
                raise ValueError("請先選擇麥克風與對外輸出裝置")
            if settings.monitor_enabled and not settings.monitor_device:
                raise ValueError("請先選擇耳機監聽裝置")
            if settings.monitor_device and settings.monitor_device == settings.output_device:
                raise ValueError("監聽與對外輸出必須使用不同裝置，以免重複輸出")
            self._stats = self._initial_stats()
            self._stats["state"] = "loading"
            self._stop.clear()
            self._monitor_available = bool(settings.monitor_device)
            self._monitor_enabled = settings.monitor_enabled
            self._monitor_gain = settings.monitor_gain
            self._output_gain = settings.output_gain
            self._thread = threading.Thread(target=self._run, args=(settings,), name="VoxBridge audio", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._stats["state"] = "stopping"
                self._stop.set()

    def wait(self, timeout=None):
        thread = self._thread
        if thread:
            thread.join(timeout)
        return not (thread and thread.is_alive())

    def set_monitor(self, enabled: bool, gain: float | None = None):
        with self._lock:
            if enabled and self._stats["state"] in ("loading", "running") and not self._monitor_available:
                raise ValueError("請停止後選擇監聽裝置，再啟動變聲")
            if gain is not None:
                if not np.isfinite(gain) or not 0 <= gain <= 2:
                    raise ValueError("監聽音量超出範圍")
                self._monitor_gain = gain
            if self._monitor_enabled != enabled and self._monitor_buffer:
                self._monitor_buffer.clear()
            self._monitor_enabled = enabled

    def set_output_gain(self, gain: float):
        if not np.isfinite(gain) or not 0 <= gain <= 2:
            raise ValueError("輸出音量超出範圍")
        with self._lock:
            self._output_gain = gain

    def _run(self, settings):
        streams = []
        engine = None
        failed = False
        try:
            sd = self._audio_module
            if sd is None:
                import sounddevice as sd
            devices = list_devices(sd)
            mic = resolve_device(devices, settings.input_device, "inputs")
            output = resolve_device(devices, settings.output_device, "outputs")
            monitor = resolve_device(devices, settings.monitor_device, "outputs") if settings.monitor_device else None
            block = settings.sample_rate * settings.block_ms // 1000
            incoming = queue.Queue(maxsize=2)
            outbound = SampleBuffer(block * 3)
            monitoring = SampleBuffer(block * 3)
            self._monitor_buffer = monitoring
            output_channels = min(2, output.outputs)
            sd.check_input_settings(device=mic.index, channels=1, dtype="float32", samplerate=settings.sample_rate)
            sd.check_output_settings(device=output.index, channels=output_channels, dtype="float32", samplerate=settings.sample_rate)
            if monitor:
                sd.check_output_settings(device=monitor.index, channels=min(2, monitor.outputs), dtype="float32", samplerate=settings.sample_rate)
            engine = self._engine_factory(settings)
            engine.load()
            self._update(runtime_info=getattr(engine, "runtime_info", {}))
            if self._stop.is_set():
                return
            sequence = 0

            def capture(indata, frames, timing, status):
                nonlocal sequence
                if self._stop.is_set():
                    return
                if status:
                    self._count("input_drops")
                    sequence += 1  # Force inference state reset after hardware discontinuity.
                sequence += 1
                item = (sequence, time.monotonic(), indata[:, 0].copy())
                try:
                    incoming.put_nowait(item)
                except queue.Full:
                    try:
                        incoming.get_nowait()
                    except queue.Empty:
                        pass
                    self._count("input_drops")
                    try:
                        incoming.put_nowait(item)
                    except queue.Full:
                        self._count("input_drops")

            def render(buffer, is_monitor=False):
                def callback(outdata, frames, timing, status):
                    outdata.fill(0)
                    if self._stop.is_set():
                        return
                    with self._lock:
                        enabled = not is_monitor or self._monitor_enabled
                        gain = self._monitor_gain if is_monitor else self._output_gain
                    if not enabled:
                        return
                    audio, underrun = buffer.read(frames)
                    if underrun or status:
                        self._count("monitor_underruns" if is_monitor else "output_underruns")
                    outdata[:] = np.clip(audio * gain, -1.0, 1.0)[:, None]
                return callback

            streams.append(sd.OutputStream(device=output.index, channels=output_channels,
                samplerate=settings.sample_rate, dtype="float32", blocksize=0,
                latency="low", callback=render(outbound)))
            if monitor:
                streams.append(sd.OutputStream(device=monitor.index, channels=min(2, monitor.outputs),
                    samplerate=settings.sample_rate, dtype="float32", blocksize=0,
                    latency="low", callback=render(monitoring, True)))
            streams.append(sd.InputStream(device=mic.index, channels=1, samplerate=settings.sample_rate,
                dtype="float32", blocksize=block, latency="low", callback=capture))
            for stream in streams:
                if self._stop.is_set():
                    return
                stream.start()
            with self._lock:
                if not self._stop.is_set():
                    self._stats["state"] = "running"
            previous = None
            while not self._stop.is_set():
                if any(not stream.active for stream in streams):
                    raise RuntimeError("音訊裝置已中斷；請檢查連線並重新選擇裝置")
                try:
                    sequence, recorded_at, audio = incoming.get(timeout=0.1)
                except queue.Empty:
                    continue
                if time.monotonic() - recorded_at > settings.block_ms / 1000 * 2:
                    self._count("input_drops")
                    continue
                if previous is not None and sequence != previous + 1:
                    reset = getattr(engine, "reset", None)
                    if reset:
                        reset()
                    outbound.clear()
                    monitoring.clear()
                previous = sequence
                began = time.perf_counter()
                converted = np.asarray(engine.process(audio), dtype=np.float32)
                process_ms = (time.perf_counter() - began) * 1000
                if converted.shape != (block,) or not np.all(np.isfinite(converted)):
                    raise RuntimeError("變聲引擎回傳無效音訊；已停止輸出")
                if self._stop.is_set():
                    break
                converted = np.clip(converted, -1.0, 1.0)
                if outbound.put(converted):
                    self._count("overruns")
                with self._lock:
                    if self._monitor_enabled:
                        monitoring.put(converted)
                if process_ms > settings.block_ms:
                    self._count("overruns")
                self._update(input_rms=float(np.sqrt(np.mean(audio ** 2))),
                    output_rms=float(np.sqrt(np.mean(converted ** 2))), process_ms=process_ms)
        except Exception as exc:
            failed = True
            log.exception("Voice conversion stopped")
            self._stop.set()
            self._update(error=str(exc))
        finally:
            for stream in reversed(streams):
                try:
                    stream.abort()
                    stream.close()
                except Exception:
                    log.exception("Could not close audio stream")
            if engine:
                try:
                    engine.close()
                except Exception:
                    log.exception("Could not release inference engine")
            self._monitor_buffer = None
            self._update(state="error" if failed else "stopped", input_rms=0.0, output_rms=0.0)
