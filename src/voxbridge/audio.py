"""Bounded audio transport. Inference never runs inside a PortAudio callback."""

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
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
    hostapi: str = ""
    default_sample_rate: float = 48000


def list_devices(audio_module=None, show_advanced=False) -> list[DeviceInfo]:
    if audio_module is None:
        import sounddevice as audio_module
    hosts = audio_module.query_hostapis()
    raw = audio_module.query_devices()
    has_wasapi = any("WASAPI" in host["name"].upper() for host in hosts)
    devices = []
    for index, device in enumerate(raw):
        host = hosts[device["hostapi"]]["name"]
        if has_wasapi and not show_advanced and "WASAPI" not in host.upper():
            continue
        name = device["name"]
        devices.append(DeviceInfo(f"{host} :: {name}", f"{name} [{host}]", index,
                                  device["max_input_channels"], device["max_output_channels"],
                                  host, float(device.get("default_samplerate", 48000))))
    return devices


def refresh_devices(show_advanced=False, audio_module=None) -> list[DeviceInfo]:
    """Re-enumerate PortAudio while the audio service is stopped.

    Calling this with open streams invalidates their device indices; the UI must
    expose refresh only in the stopped state.
    """
    if audio_module is None:
        import sounddevice as audio_module
    terminate = getattr(audio_module, "_terminate", None)
    initialize = getattr(audio_module, "_initialize", None)
    if terminate and initialize:
        terminate()
        initialize()
    return list_devices(audio_module, show_advanced=show_advanced)


class StreamingResampler:
    """Stateful high-quality PCM converter with exact cumulative time base."""

    def __init__(self, input_rate: int, output_rate: int):
        self.input_rate = int(input_rate)
        self.output_rate = int(output_rate)
        self._stream = None
        if self.input_rate != self.output_rate:
            try:
                import soxr
            except ImportError as exc:
                raise RuntimeError("缺少音訊取樣率轉換套件 soxr；請安裝 requirements-engine.txt") from exc
            self._stream = soxr.ResampleStream(self.input_rate, self.output_rate, 1, dtype="float32")

    def process(self, samples: np.ndarray) -> np.ndarray:
        samples = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
        if self._stream is None:
            return samples
        return self._stream.resample_chunk(samples, last=False)

    def reset(self):
        if self._stream is not None:
            import soxr
            self._stream = soxr.ResampleStream(self.input_rate, self.output_rate, 1, dtype="float32")


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


def _negotiate(sd, device: DeviceInfo, direction: str, role: str):
    """Find an explicitly supported native stream format for one endpoint."""
    rates = list(dict.fromkeys([48000, int(round(device.default_sample_rate)), 44100,
                                32000, 24000, 16000, 8000]))
    channels = [n for n in (1, 2) if n <= device.inputs] if direction == "input" else [n for n in (2, 1) if n <= device.outputs]
    extra = None
    if "WASAPI" in device.hostapi.upper() and hasattr(sd, "WasapiSettings"):
        extra = sd.WasapiSettings(exclusive=False, auto_convert=True)
    check = sd.check_input_settings if direction == "input" else sd.check_output_settings
    for rate in rates:
        for count in channels:
            args = dict(device=device.index, channels=count, dtype="float32", samplerate=rate)
            if extra is not None:
                args["extra_settings"] = extra
            try:
                check(**args)
                return rate, count, extra
            except Exception:
                continue
    raise RuntimeError(f"{role}「{device.label}」不支援可用的取樣率／聲道組合；已試 {', '.join(map(str, rates))} Hz、{channels} 聲道。請在 Windows 音效設定確認裝置已連線並調整格式，或改選其他裝置")


def _construct_stream(sd, direction, args, role, device):
    try:
        stream_type = sd.InputStream if direction == "input" else sd.OutputStream
        return stream_type(**args)
    except Exception as exc:
        raise RuntimeError(f"無法開啟{role}「{device.label}」：{args['samplerate']} Hz、{args['channels']} 聲道；請檢查 Windows 裝置格式與連線狀態。PortAudio：{exc}") from exc


def _start_stream(stream, role, device, rate, channels):
    try:
        stream.start()
    except Exception as exc:
        raise RuntimeError(f"無法啟動{role}「{device.label}」：{rate} Hz、{channels} 聲道；請檢查裝置是否被其他程式占用。PortAudio：{exc}") from exc


def _close_stream(stream, role):
    for method in ("abort", "close"):
        try:
            getattr(stream, method)()
        except Exception:
            log.exception("Could not %s %s stream", method, role)


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
        self._output_buffer = None
        self._monitor_available = False
        self._output_mode = "discord"
        self._stats = self._initial_stats()

    @staticmethod
    def _initial_stats():
        return dict(state="stopped", error="", error_stage="", monitor_error="",
                    monitor_enabled=False, input_rms=0.0, output_rms=0.0,
                    process_ms=0.0, input_drops=0, output_underruns=0,
                    monitor_underruns=0, overruns=0, runtime_info={}, session_settings={})

    def snapshot(self):
        with self._lock:
            return {**self._stats, "runtime_info": deepcopy(self._stats["runtime_info"]),
                    "session_settings": dict(self._stats["session_settings"])}

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
            if not settings.input_device:
                raise ValueError("請先選擇麥克風")
            if settings.output_mode == "discord" and not settings.output_device:
                raise ValueError("Discord 模式請先選擇對外輸出裝置")
            if settings.output_mode == "monitor_only" and (not settings.monitor_enabled or not settings.monitor_device):
                raise ValueError("只在耳機測試模式需要啟用監聽並選擇耳機裝置")
            if settings.monitor_enabled and not settings.monitor_device:
                raise ValueError("請先選擇耳機監聽裝置")
            self._stats = self._initial_stats()
            self._stats["state"] = "loading"
            self._stats["session_settings"] = asdict(settings)
            self._stop.clear()
            self._monitor_available = bool(settings.monitor_device)
            self._output_mode = settings.output_mode
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
            if self._monitor_enabled != enabled and self._output_mode == "monitor_only" and self._output_buffer:
                self._output_buffer.clear()
            self._monitor_enabled = enabled
            if enabled:
                self._stats["monitor_error"] = ""
            if not enabled:
                self._stats["monitor_enabled"] = False
                self._stats["monitor_error"] = ""

    def set_output_gain(self, gain: float):
        if not np.isfinite(gain) or not 0 <= gain <= 2:
            raise ValueError("輸出音量超出範圍")
        with self._lock:
            self._output_gain = gain

    def _run(self, settings):
        streams = []
        monitor_stream = None
        monitor_converter = None
        engine = None
        failed = False
        stage = "裝置協商"
        try:
            sd = self._audio_module
            if sd is None:
                import sounddevice as sd
            devices = list_devices(sd, show_advanced=True)
            mic = resolve_device(devices, settings.input_device, "inputs")
            target_key = settings.output_device if settings.output_mode == "discord" else settings.monitor_device
            target = resolve_device(devices, target_key, "outputs")
            output_role = "Discord 對外輸出" if settings.output_mode == "discord" else "耳機監聽"
            input_rate, input_channels, input_extra = _negotiate(sd, mic, "input", "麥克風")
            output_rate, output_channels, output_extra = _negotiate(sd, target, "output", output_role)
            block = settings.sample_rate * settings.block_ms // 1000
            native_block = round(input_rate * settings.block_ms / 1000)
            incoming = queue.Queue(maxsize=3)
            outbound = SampleBuffer(round(output_rate * settings.block_ms / 1000) * 4)
            self._output_buffer = outbound
            monitoring = SampleBuffer(round(settings.sample_rate * settings.block_ms / 1000) * 4)
            self._monitor_buffer = monitoring
            input_converter = StreamingResampler(input_rate, settings.sample_rate)
            output_converter = StreamingResampler(settings.sample_rate, output_rate)
            input_pending = np.empty(0, dtype=np.float32)
            same_endpoint = settings.output_mode == "discord" and settings.monitor_device == settings.output_device
            audio_info = {
                "input": dict(name=mic.label, hostapi=mic.hostapi, rate=input_rate, channels=input_channels),
                "output": dict(name=target.label, hostapi=target.hostapi, rate=output_rate, channels=output_channels),
                "monitor": None,
            }
            self._update(runtime_info={"audio": audio_info})

            stage = "載入變聲模型"
            engine = self._engine_factory(settings)
            engine.load()
            self._update(runtime_info={**getattr(engine, "runtime_info", {}), "audio": audio_info})
            if self._stop.is_set():
                return
            sequence = 0

            def capture(indata, frames, timing, status):
                nonlocal sequence
                if self._stop.is_set():
                    return
                if status:
                    self._count("input_drops")
                    sequence += 1
                sequence += 1
                mono = np.mean(indata, axis=1, dtype=np.float32).copy()
                item = (sequence, time.monotonic(), mono)
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
                        enabled = self._monitor_enabled if (is_monitor or settings.output_mode == "monitor_only") else True
                        gain = self._monitor_gain if (is_monitor or settings.output_mode == "monitor_only") else self._output_gain
                    if not enabled:
                        return
                    samples, underrun = buffer.read(frames)
                    if underrun or status:
                        self._count("monitor_underruns" if is_monitor else "output_underruns")
                    outdata[:] = np.clip(samples * gain, -1.0, 1.0)[:, None]
                return callback

            stage = "開啟音訊串流"
            output_args = dict(device=target.index, channels=output_channels, samplerate=output_rate,
                               dtype="float32", blocksize=0, latency="low", callback=render(outbound))
            if output_extra is not None:
                output_args["extra_settings"] = output_extra
            streams.append(_construct_stream(sd, "output", output_args, output_role, target))
            input_args = dict(device=mic.index, channels=input_channels, samplerate=input_rate,
                              dtype="float32", blocksize=native_block, latency="low", callback=capture)
            if input_extra is not None:
                input_args["extra_settings"] = input_extra
            streams.append(_construct_stream(sd, "input", input_args, "麥克風", mic))
            descriptions = [(output_role, target, output_rate, output_channels),
                            ("麥克風", mic, input_rate, input_channels)]
            for stream, (role, device, rate, channels) in zip(streams, descriptions):
                if self._stop.is_set():
                    return
                _start_stream(stream, role, device, rate, channels)
            if same_endpoint and self._monitor_enabled:
                self._update(monitor_enabled=True)
            elif settings.output_mode == "monitor_only":
                self._update(monitor_enabled=True)
            with self._lock:
                if not self._stop.is_set():
                    self._stats["state"] = "running"
            stage = "音訊處理"
            previous = None
            previous_monitor_enabled = self._monitor_enabled

            def sync_monitor():
                nonlocal monitor_stream, monitor_converter
                with self._lock:
                    wanted = self._monitor_enabled
                if same_endpoint or settings.output_mode == "monitor_only":
                    self._update(monitor_enabled=wanted)
                    return
                if monitor_stream and (not wanted or not monitor_stream.active):
                    interrupted = wanted and not monitor_stream.active
                    old_monitor = monitor_stream
                    monitor_stream = None
                    monitor_converter = None
                    monitoring.clear()
                    audio_info["monitor"] = None
                    _close_stream(old_monitor, "monitor")
                    if interrupted:
                        with self._lock:
                            self._monitor_enabled = False
                        self._update(monitor_enabled=False, monitor_error="監聽裝置已中斷；請重新選擇或重新啟用監聽")
                        return
                    else:
                        self._update(monitor_enabled=False)
                if not wanted or monitor_stream or not settings.monitor_device:
                    return
                try:
                    monitor = resolve_device(devices, settings.monitor_device, "outputs")
                    rate, channels, extra = _negotiate(sd, monitor, "output", "耳機監聽")
                    monitor_converter = StreamingResampler(settings.sample_rate, rate)
                    monitoring.capacity = round(rate * settings.block_ms / 1000) * 4
                    args = dict(device=monitor.index, channels=channels, samplerate=rate,
                                dtype="float32", blocksize=0, latency="low", callback=render(monitoring, True))
                    if extra is not None:
                        args["extra_settings"] = extra
                    monitor_stream = _construct_stream(sd, "output", args, "耳機監聽", monitor)
                    _start_stream(monitor_stream, "耳機監聽", monitor, rate, channels)
                    audio_info["monitor"] = dict(name=monitor.label, hostapi=monitor.hostapi,
                                                  rate=rate, channels=channels)
                    self._update(monitor_enabled=True, monitor_error="",
                                 runtime_info={**getattr(engine, "runtime_info", {}), "audio": audio_info})
                except Exception as exc:
                    if monitor_stream:
                        _close_stream(monitor_stream, "monitor")
                    monitor_stream = None
                    monitor_converter = None
                    with self._lock:
                        self._monitor_enabled = False
                    self._update(monitor_enabled=False, monitor_error=f"監聽裝置無法使用：{exc}")

            while not self._stop.is_set():
                if any(not stream.active for stream in streams):
                    raise RuntimeError("音訊裝置已中斷；請檢查連線並重新選擇裝置")
                sync_monitor()
                with self._lock:
                    current_monitor_enabled = self._monitor_enabled
                if settings.output_mode == "monitor_only" and current_monitor_enabled != previous_monitor_enabled:
                    output_converter.reset()
                    outbound.clear()
                previous_monitor_enabled = current_monitor_enabled
                try:
                    sequence, recorded_at, recorded = incoming.get(timeout=0.05)
                except queue.Empty:
                    continue
                if time.monotonic() - recorded_at > settings.block_ms / 1000 * 2:
                    self._count("input_drops")
                    continue
                if previous is not None and sequence != previous + 1:
                    reset = getattr(engine, "reset", None)
                    if reset:
                        reset()
                    input_converter.reset()
                    output_converter.reset()
                    if monitor_converter:
                        monitor_converter.reset()
                    input_pending = np.empty(0, dtype=np.float32)
                    outbound.clear()
                    monitoring.clear()
                previous = sequence
                converted_input = input_converter.process(recorded)
                input_pending = np.concatenate((input_pending, converted_input))
                while input_pending.size >= block and not self._stop.is_set():
                    audio = input_pending[:block].copy()
                    input_pending = input_pending[block:]
                    began = time.perf_counter()
                    converted = np.asarray(engine.process(audio), dtype=np.float32)
                    process_ms = (time.perf_counter() - began) * 1000
                    if converted.shape != (block,) or not np.all(np.isfinite(converted)):
                        raise RuntimeError("變聲引擎回傳無效音訊；已停止輸出")
                    converted = np.clip(converted, -1.0, 1.0)
                    with self._lock:
                        monitor_enabled = self._monitor_enabled
                    if settings.output_mode == "discord" or monitor_enabled:
                        native_output = output_converter.process(converted)
                        if outbound.put(native_output):
                            self._count("overruns")
                    if monitor_stream and monitor_enabled and monitor_converter:
                        if monitoring.put(monitor_converter.process(converted)):
                            self._count("overruns")
                    if process_ms > settings.block_ms:
                        self._count("overruns")
                    self._update(input_rms=float(np.sqrt(np.mean(audio ** 2))),
                                 output_rms=float(np.sqrt(np.mean(converted ** 2))), process_ms=process_ms)
        except Exception as exc:
            failed = True
            log.exception("Voice conversion stopped at %s", stage)
            self._stop.set()
            self._update(error=str(exc), error_stage=stage)
        finally:
            if monitor_stream:
                _close_stream(monitor_stream, "monitor")
            for stream in reversed(streams):
                _close_stream(stream, "audio")
            if engine:
                try:
                    engine.close()
                except Exception:
                    log.exception("Could not release inference engine")
            self._monitor_buffer = None
            self._output_buffer = None
            self._update(state="error" if failed else "stopped", monitor_enabled=False,
                         input_rms=0.0, output_rms=0.0)
