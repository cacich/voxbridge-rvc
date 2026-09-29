"""RVC voice conversion for fixed-size, mono PCM blocks.

Weights are deliberately local. The caller owns microphone, virtual cable, and
monitoring I/O; this module owns only model inference and stream continuity.
"""

from __future__ import annotations

import math
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    return settings.get(name, default) if isinstance(settings, dict) else getattr(settings, name, default)


class RvcEngine:
    """Real RVC v1/v2 inference, with optional FAISS retrieval and RMVPE F0.

    `process` accepts and returns exactly one block of float32 mono samples at
    `sample_rate`. Inference can take longer than a block on slow hardware; the
    audio transport must handle that separately.
    """

    def __init__(self, settings: Any):
        self.model_path = Path(_setting(settings, "model_path", ""))
        self.index_path = Path(_setting(settings, "index_path", "")) if _setting(settings, "index_path") else None
        self.assets_dir = Path(_setting(settings, "assets_dir", "assets"))
        self.pitch = int(_setting(settings, "pitch", 0))
        self.index_rate = float(_setting(settings, "index_rate", 0.0))
        self.device_name = str(_setting(settings, "device", "cuda"))
        self.block_ms = int(_setting(settings, "block_ms", 200))
        self.sample_rate = int(_setting(settings, "sample_rate", 48000))
        if self.device_name not in {"cuda", "cpu"}:
            raise ValueError("device must be 'cuda' or 'cpu'")
        if self.sample_rate not in {32000, 40000, 44100, 48000, 96000}:
            raise ValueError("Unsupported input sample rate; use 32000, 40000, 44100, 48000, or 96000 Hz")
        if self.block_ms < 50 or self.block_ms > 1000 or self.block_ms % 10:
            raise ValueError("block_ms must be 50–1000 in 10 ms steps")
        if not 0 <= self.index_rate <= 1:
            raise ValueError("index_rate must be between 0 and 1")
        self.block_samples = self.sample_rate * self.block_ms // 1000
        self._loaded = False
        self.runtime_info: dict[str, Any] = {
            "loaded": False,
            "device": self.device_name,
            "sample_rate": self.sample_rate,
            "block_ms": self.block_ms,
            "processed_blocks": 0,
        }

    def load(self) -> None:
        """Validate and load all weights, then run one full synthetic inference."""
        if self._loaded:
            return
        if not self.model_path.is_file():
            raise FileNotFoundError(f"RVC model missing: {self.model_path}; select an exported .pth file")
        from .assets import verify_assets

        invalid_assets = verify_assets(self.assets_dir)
        if invalid_assets:
            raise FileNotFoundError(
                f"RVC inference assets missing or corrupt in {self.assets_dir}: "
                f"{', '.join(invalid_assets)}; use the app's base-model download action"
            )
        hubert_dir = self.assets_dir / "hubert_base"
        if self.index_rate and (self.index_path is None or not self.index_path.is_file()):
            raise FileNotFoundError("Index retrieval is enabled; select an existing added_*.index or set index_rate to 0")

        try:
            import torch
            import torch.nn.functional as F
            from scipy.signal import resample_poly
            from transformers import HubertModel
            from torch import nn
            from .vendor.module.models import (
                SynthesizerTrnMs256NSFsid, SynthesizerTrnMs256NSFsid_nono,
                SynthesizerTrnMs768NSFsid, SynthesizerTrnMs768NSFsid_nono,
            )
            from .vendor.rmvpe import RMVPE
        except ImportError as exc:
            raise RuntimeError(f"RVC inference dependency unavailable: {exc}; install requirements-engine.txt") from exc
        if self.device_name == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable; install a matching CUDA PyTorch build and NVIDIA driver, or explicitly select CPU")
        if self.device_name == "cpu" and torch.get_num_threads() > 4:
            torch.set_num_threads(4)
            self.runtime_info["cpu_threads"] = torch.get_num_threads()

        class HubertWithProjection(HubertModel):
            def __init__(self, config):
                super().__init__(config)
                self.final_proj = nn.Linear(config.hidden_size, config.classifier_proj_size)

        self._torch, self._F, self._resample_poly = torch, F, resample_poly
        self._device = torch.device(self.device_name)
        try:
            checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=True)
            if not isinstance(checkpoint, dict) or "config" not in checkpoint or "weight" not in checkpoint:
                raise ValueError("not an exported RVC checkpoint (config/weight missing)")
            version = checkpoint.get("version", "v1")
            f0 = int(checkpoint.get("f0", 1))
            if version not in {"v1", "v2"} or f0 not in {0, 1}:
                raise ValueError(f"unsupported RVC version/f0 combination: {version}/{f0}")
            config = list(checkpoint["config"])
            config[-3] = checkpoint["weight"]["emb_g.weight"].shape[0]
            synthesizers = {
                ("v1", 1): SynthesizerTrnMs256NSFsid,
                ("v1", 0): SynthesizerTrnMs256NSFsid_nono,
                ("v2", 1): SynthesizerTrnMs768NSFsid,
                ("v2", 0): SynthesizerTrnMs768NSFsid_nono,
            }
            cls = synthesizers[version, f0]
            net = cls(*config, is_half=False) if f0 else cls(*config)
            del net.enc_q
            missing, unexpected = net.load_state_dict(checkpoint["weight"], strict=False)
            # Training exports include enc_q; inference does not use it.
            if missing or any(not key.startswith("enc_q.") for key in unexpected):
                raise ValueError(f"RVC checkpoint weights do not match {version}: missing={missing[:5]}, unexpected={unexpected[:5]}")
            net = net.float().eval().to(self._device)
            net.remove_weight_norm()
            self._net = net
            self._target_rate = {"32k": 32000, "40k": 40000, "48k": 48000}.get(config[-1], config[-1])
            self._target_rate = int(self._target_rate)
            self._version, self._has_f0 = version, bool(f0)
            self._hubert = HubertWithProjection.from_pretrained(str(hubert_dir), local_files_only=True).float().eval().to(self._device)
            self._rmvpe = RMVPE(str(self.assets_dir / "rmvpe.pt"), is_half=False, device=self._device) if f0 else None
            self._index = self._index_vectors = None
            if self.index_rate:
                try:
                    import faiss
                except ImportError as exc:
                    raise RuntimeError("FAISS is required when index_rate > 0; install faiss-cpu") from exc
                self._index = faiss.read_index(str(self.index_path))
                if self._index.ntotal < 8:
                    raise ValueError("Index contains too few vectors; select an added_*.index")
                self._index_vectors = self._index.reconstruct_n(0, self._index.ntotal)
                if self._index.d != (256 if version == "v1" else 768):
                    raise ValueError(f"Index dimension {self._index.d} does not match {version} RVC features")
            self.reset()
            self._loaded = True
            self.runtime_info.update({"loaded": True, "model_version": version, "model_sample_rate": self._target_rate, "has_f0": bool(f0), "index_enabled": bool(self.index_rate)})
            # A full call catches incompatible weights/assets before starting I/O.
            probe = np.zeros(self.block_samples, dtype=np.float32)
            self.process(probe)
            self.reset()
            self.runtime_info["processed_blocks"] = 0
            self.runtime_info.pop("last_inference_ms", None)
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Unable to load RVC engine: {exc}") from exc

    def reset(self) -> None:
        """Drop context after a device interruption or stream restart."""
        self._context_ms = 2000
        self._crossfade_ms = 40
        self._search_ms = 10
        length = self.sample_rate * (self._context_ms + self.block_ms + self._crossfade_ms + self._search_ms) // 1000
        self._input = np.zeros(length, dtype=np.float32)
        self._fade_samples = self.sample_rate * self._crossfade_ms // 1000
        self._search_samples = self.sample_rate * self._search_ms // 1000
        self._sola = np.zeros(self._fade_samples, dtype=np.float32)
        phase = np.linspace(0.0, 1.0, self._fade_samples, dtype=np.float32)
        self._fade_in = np.sin(0.5 * np.pi * phase) ** 2
        self._pitch_cache = np.zeros(length // (self.sample_rate // 100), dtype=np.int64)
        self._pitchf_cache = np.zeros_like(self._pitch_cache, dtype=np.float32)

    def _pitch_for_block(self, input16: np.ndarray, frames: int) -> tuple[Any, Any]:
        torch = self._torch
        block16 = self.block_ms * 16
        extract_length = 5120 * ((block16 + 800 - 1) // 5120 + 1) - 160
        f0 = self._rmvpe.infer_from_audio(input16[-extract_length:], thred=0.03)
        f0 = np.asarray(f0, dtype=np.float32)
        voiced = f0 > 0
        if np.any(voiced):
            f0[~voiced] = np.interp(np.flatnonzero(~voiced), np.flatnonzero(voiced), f0[voiced])
        f0 *= 2 ** (self.pitch / 12)
        mel = 1127 * np.log1p(f0 / 700)
        lo = 1127 * np.log1p(50 / 700)
        hi = 1127 * np.log1p(1100 / 700)
        coarse = np.clip(np.rint(np.where(mel > 0, (mel - lo) * 254 / (hi - lo) + 1, 1)), 1, 255).astype(np.int64)
        shift = self.block_ms // 10
        self._pitch_cache[:-shift] = self._pitch_cache[shift:]
        self._pitchf_cache[:-shift] = self._pitchf_cache[shift:]
        recent = coarse[3:-1]
        recent_f = f0[3:-1]
        if len(recent):
            self._pitch_cache[-len(recent):] = recent
            self._pitchf_cache[-len(recent_f):] = recent_f
        return (
            torch.from_numpy(self._pitch_cache[-frames:].copy()).unsqueeze(0).to(self._device),
            torch.from_numpy(self._pitchf_cache[-frames:].copy()).unsqueeze(0).to(self._device),
        )

    def _infer(self, input16: np.ndarray) -> np.ndarray:
        torch, F = self._torch, self._F
        input_tensor = torch.from_numpy(input16.copy()).to(self._device).view(1, -1)
        with torch.inference_mode():
            outputs = self._hubert(input_values=input_tensor, output_hidden_states=self._version == "v1", return_dict=True)
            features = self._hubert.final_proj(outputs.hidden_states[9]) if self._version == "v1" else outputs.last_hidden_state
            features = torch.cat((features, features[:, -1:, :]), dim=1)
            skip = self._context_ms // 10
            if self._index is not None:
                query = features[0, skip // 2:].float().cpu().numpy()
                distances, ids = self._index.search(query, 8)
                if (ids < 0).any():
                    raise RuntimeError("FAISS returned missing neighbors; select a populated added_*.index")
                weights = 1.0 / np.maximum(distances, 1e-6) ** 2
                weights /= weights.sum(axis=1, keepdims=True)
                retrieved = (self._index_vectors[ids] * weights[..., None]).sum(axis=1)
                replacement = torch.from_numpy(retrieved).to(device=self._device, dtype=features.dtype)
                features[0, skip // 2:] = replacement * self.index_rate + features[0, skip // 2:] * (1 - self.index_rate)
            frames = len(input16) // 160
            features = F.interpolate(features.transpose(1, 2), scale_factor=2).transpose(1, 2)[:, :frames, :]
            length = torch.tensor([frames], device=self._device, dtype=torch.long)
            sid = torch.zeros(1, device=self._device, dtype=torch.long)
            return_frames = (self.block_ms + self._crossfade_ms + self._search_ms) // 10
            if self._has_f0:
                coarse, continuous = self._pitch_for_block(input16, frames)
                audio = self._net.infer(features, length, coarse, continuous, sid, skip, return_frames, return_frames)[0]
            else:
                audio = self._net.infer(features, length, sid, skip, return_frames, return_frames)[0]
        return audio.squeeze().float().cpu().numpy()

    def process(self, audio: np.ndarray) -> np.ndarray:
        if not self._loaded:
            raise RuntimeError("RVC engine is not loaded; call load() first")
        block = np.asarray(audio)
        if block.dtype != np.float32 or block.ndim != 1 or block.size != self.block_samples:
            raise ValueError(f"Expected float32 mono block of {self.block_samples} samples at {self.sample_rate} Hz")
        if not np.isfinite(block).all():
            raise ValueError("Input audio contains NaN or infinity")
        started = perf_counter()
        self._input[:-self.block_samples] = self._input[self.block_samples:]
        self._input[-self.block_samples:] = block
        div = math.gcd(self.sample_rate, 16000)
        input16 = self._resample_poly(self._input, 16000 // div, self.sample_rate // div).astype(np.float32)
        converted = self._infer(input16)
        div = math.gcd(self._target_rate, self.sample_rate)
        converted = self._resample_poly(converted, self.sample_rate // div, self._target_rate // div).astype(np.float32)
        required = self.block_samples + self._fade_samples + self._search_samples
        if converted.size < required:
            raise RuntimeError(f"RVC output was too short ({converted.size} < {required} samples)")
        # Search a 10 ms offset that best matches the previous 40 ms tail.
        candidates = np.lib.stride_tricks.sliding_window_view(converted[:self._fade_samples + self._search_samples], self._fade_samples)
        numerator = candidates @ self._sola
        denominator = np.linalg.norm(candidates, axis=1) + 1e-8
        offset = int(np.argmax(numerator / denominator))
        aligned = converted[offset:offset + self.block_samples + self._fade_samples].copy()
        aligned[:self._fade_samples] = aligned[:self._fade_samples] * self._fade_in + self._sola * (1 - self._fade_in)
        self._sola[:] = aligned[self.block_samples:]
        output = np.ascontiguousarray(aligned[:self.block_samples], dtype=np.float32)
        self.runtime_info["processed_blocks"] += 1
        self.runtime_info["last_inference_ms"] = round((perf_counter() - started) * 1000, 1)
        return output

    def close(self) -> None:
        self._loaded = False
        for name in ("_net", "_hubert", "_rmvpe", "_index", "_index_vectors"):
            if hasattr(self, name):
                delattr(self, name)
        self.runtime_info["loaded"] = False
