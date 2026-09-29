"""Opt-in end-to-end model tests using verified base assets, never downloads.

Set VOXBRIDGE_TEST_ASSETS to the directory containing pinned HuBERT and RMVPE
files (as installed by the application) and run this module explicitly.
"""

import os
from pathlib import Path

import numpy as np
import pytest

from voxbridge.engine import RvcEngine


@pytest.mark.parametrize("version,has_f0", [(v, f0) for v in ("v1", "v2") for f0 in (0, 1)])
def test_exported_model_full_inference(tmp_path: Path, version: str, has_f0: int):
    assets = os.environ.get("VOXBRIDGE_TEST_ASSETS")
    if not assets:
        pytest.skip("Set VOXBRIDGE_TEST_ASSETS to the downloaded, pinned base-model directory")
    torch = pytest.importorskip("torch")
    pytest.importorskip("transformers")
    pytest.importorskip("librosa")
    from voxbridge.vendor.module.models import (
        SynthesizerTrnMs256NSFsid, SynthesizerTrnMs256NSFsid_nono,
        SynthesizerTrnMs768NSFsid, SynthesizerTrnMs768NSFsid_nono,
    )

    # Use an actual exported RVC checkpoint structure with small channels.
    # The 40 kHz upsample path still uses the standard 400x generator stride.
    config = [513, 32, 32, 32, 64, 2, 2, 3, 0, "1", [3], [[1, 3, 5]],
              [10, 10, 2, 2], 64, [16, 16, 4, 4], 1, 16, 40000]
    classes = {
        ("v1", 1): SynthesizerTrnMs256NSFsid,
        ("v1", 0): SynthesizerTrnMs256NSFsid_nono,
        ("v2", 1): SynthesizerTrnMs768NSFsid,
        ("v2", 0): SynthesizerTrnMs768NSFsid_nono,
    }
    torch.manual_seed(7)
    cls = classes[version, has_f0]
    model = cls(*config, is_half=False) if has_f0 else cls(*config)
    weights = {key: value for key, value in model.state_dict().items() if not key.startswith("enc_q.")}
    checkpoint = tmp_path / f"synthetic-{version}-f0{has_f0}.pth"
    torch.save({"config": config, "weight": weights, "version": version, "f0": has_f0}, checkpoint)
    del model, weights

    # Cover retrieval in one F0 and one non-F0 case, without a user's voice data.
    use_index = (version == "v1" and has_f0) or (version == "v2" and not has_f0)
    index_path = tmp_path / "聲線檢索.index"
    if use_index:
        import faiss
        dimensions = 256 if version == "v1" else 768
        index = faiss.IndexFlatL2(dimensions)
        index.add(np.random.default_rng(7).standard_normal((16, dimensions)).astype(np.float32))
        faiss.serialize_index(index).tofile(index_path)

    engine = RvcEngine(dict(model_path=str(checkpoint), index_path=str(index_path) if use_index else "", assets_dir=assets,
                            pitch=0, index_rate=0.5 if use_index else 0.0, device="cpu", block_ms=100, sample_rate=48000))
    try:
        engine.load()
        phase = np.arange(4800, dtype=np.float32)
        block = (0.02 * np.sin(2 * np.pi * 220 * phase / 48000)).astype(np.float32)
        converted = engine.process(block)
        assert converted.shape == block.shape
        assert converted.dtype == np.float32
        assert np.isfinite(converted).all()
        assert engine.runtime_info["model_version"] == version
        assert engine.runtime_info["has_f0"] == bool(has_f0)
        assert engine.runtime_info["index_enabled"] == bool(use_index)
        assert np.isfinite(engine.process(block)).all()
        engine.reset()
        assert np.isfinite(engine.process(block)).all()
    finally:
        engine.close()
