"""Explicit, checksum-verified base-model downloads, pinned to one upstream revision."""

import hashlib
from pathlib import Path
from urllib.request import Request, urlopen

REVISION = "e6d0c1a17da07c33557852f9dfa2bd44cc75737d"
BASE_URL = f"https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/{REVISION}"
MANIFEST = {
    "hubert_base/config.json": (1492, "0346950779dfb7f9316fa74ed846e2b8a22a08eedfdc5387b73f327cb1a4a7cf"),
    "hubert_base/preprocessor_config.json": (225, "7c1976a680fb7acc757cd36fb08eef878fa36c70b4c9d2d595df9c608bbbbf0e"),
    "hubert_base/pytorch_model.bin": (189206711, "cc8c20f4b90a520757260197a3ff2505705a7adbd20ad9eeaa4e1a9b38442ef5"),
    "rmvpe.pt": (181184272, "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"),
}


def check_assets(directory) -> list[str]:
    root = Path(directory)
    return [name for name, (size, _) in MANIFEST.items()
            if not (root / name).is_file() or (root / name).stat().st_size != size]


def verify_assets(directory) -> list[str]:
    root = Path(directory)
    invalid = set(check_assets(root))
    for name, (_, digest) in MANIFEST.items():
        if name not in invalid:
            with (root / name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    invalid.add(name)
    return sorted(invalid)


def download_assets(directory, progress_callback=lambda message: None):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    for name, (expected_size, expected_digest) in MANIFEST.items():
        target = root / name
        if target.is_file() and target.stat().st_size == expected_size:
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == expected_digest:
                    progress_callback(f"已驗證：{name}")
                    continue
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        progress_callback(f"下載：{name}")
        request = Request(f"{BASE_URL}/{name}", headers={"User-Agent": "VoxBridge/0.1.0"})
        count = 0
        digest = hashlib.sha256()
        try:
            with urlopen(request, timeout=30) as response, part.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    if count > expected_size:
                        raise ValueError(f"下載檔案大小不符：{name}")
                    digest.update(chunk)
                    stream.write(chunk)
                    progress_callback(f"{name}：{count * 100 // expected_size}%")
            if count != expected_size or digest.hexdigest() != expected_digest:
                raise ValueError(f"校驗失敗，請重新下載：{name}")
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    progress_callback("基礎模型已下載並通過 SHA-256 校驗")
