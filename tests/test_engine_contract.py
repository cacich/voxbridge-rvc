"""Dependency-light checks for the public PCM and startup contract."""

from pathlib import Path

import numpy as np
import pytest

from voxbridge.engine import RvcEngine


def settings(**changes):
    result = dict(model_path="missing.pth", index_path="", assets_dir="missing-assets", pitch=0,
                  index_rate=0.0, device="cpu", block_ms=200, sample_rate=48000)
    result.update(changes)
    return result


def test_requires_model_before_expensive_imports(tmp_path: Path):
    engine = RvcEngine(settings(model_path=str(tmp_path / "missing.pth")))
    with pytest.raises(FileNotFoundError, match="exported .pth"):
        engine.load()


def test_rejects_bad_blocks_without_audio_passthrough():
    engine = RvcEngine(settings())
    with pytest.raises(RuntimeError, match="load"):
        engine.process(np.zeros(engine.block_samples, np.float32))
    engine._loaded = True
    for bad in (np.zeros(100, np.float32), np.zeros(engine.block_samples, np.float64),
                np.zeros((engine.block_samples, 1), np.float32)):
        with pytest.raises(ValueError, match="float32 mono"):
            engine.process(bad)
    noisy = np.zeros(engine.block_samples, np.float32)
    noisy[0] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        engine.process(noisy)


def test_reset_discards_stream_context():
    engine = RvcEngine(settings())
    engine.reset()
    engine._input[:] = 0.5
    engine._sola[:] = 0.5
    engine._pitch_cache[:] = 50
    engine.reset()
    assert np.count_nonzero(engine._input) == 0
    assert np.count_nonzero(engine._sola) == 0
    assert np.count_nonzero(engine._pitch_cache) == 0


def test_fixed_size_output_and_overlap_continuity():
    from scipy.signal import resample_poly

    engine = RvcEngine(settings())
    engine.reset()
    engine._loaded = True
    engine._target_rate = 48000
    engine._resample_poly = resample_poly
    engine._infer = lambda _: np.full(12000, 0.25, dtype=np.float32)
    block = np.zeros(engine.block_samples, dtype=np.float32)
    first = engine.process(block)
    second = engine.process(block)
    assert first.dtype == second.dtype == np.float32
    assert first.shape == second.shape == block.shape
    assert first[-1] == pytest.approx(second[0], abs=1e-4)
    assert engine.runtime_info["processed_blocks"] == 2
