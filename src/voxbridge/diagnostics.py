"""User-triggered local diagnostics export. No recordings or model contents."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path, PureWindowsPath
import platform
import re

from . import __version__


def _redact_paths(value):
    """Strip sensitive paths in both UI settings and the last session snapshot."""
    if isinstance(value, dict):
        return {
            key: (PureWindowsPath(str(item)).name if item else "")
            if key in {"model_path", "index_path", "assets_dir"}
            else _redact_paths(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_paths(item) for item in value]
    return value


def export_diagnostics(destination: Path, settings, snapshot, devices) -> Path:
    configuration = _redact_paths(asdict(settings))
    dependencies = {}
    for package in ("numpy", "PySide6", "sounddevice", "torch", "torchaudio", "transformers", "faiss-cpu"):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = "not installed"
    payload = {
        "version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
        "system": platform.system(), "os_version": platform.version(),
        "architecture": platform.machine(), "python": platform.python_version(),
        "settings": configuration, "audio": _redact_paths(snapshot),
        "devices": [asdict(d) if is_dataclass(d) else d for d in devices],
        "dependencies": dependencies,
        "notes": "Processing time is not measured end-to-end latency. No audio or model contents included.",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    # Exceptions may include a user path; strip the common home prefix as well.
    home = json.dumps(str(Path.home()), ensure_ascii=False)[1:-1]
    text = text.replace(home, "<HOME>")
    text = re.sub(r"(?i)([A-Z]:\\\\Users\\\\)[^\\]+", r"\1<USER>", text)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination
