"""User-triggered local diagnostics export. No recordings or model contents."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import re

from . import __version__


def export_diagnostics(destination: Path, settings, snapshot, devices) -> Path:
    configuration = asdict(settings)
    for key in ("model_path", "index_path", "assets_dir"):
        configuration[key] = Path(configuration[key]).name if configuration[key] else ""
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
        "settings": configuration, "audio": snapshot,
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
