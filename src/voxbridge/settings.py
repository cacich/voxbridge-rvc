"""Versioned per-user settings; never stored alongside the installed executable."""

from dataclasses import asdict, dataclass, fields
import json
import math
import os
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "VoxBridge"


@dataclass
class AppSettings:
    model_path: str = ""
    index_path: str = ""
    assets_dir: str = ""
    input_device: str = ""
    output_device: str = ""
    monitor_device: str = ""
    monitor_enabled: bool = False
    monitor_gain: float = 0.7
    output_gain: float = 1.0
    pitch: int = 0
    index_rate: float = 0.0
    device: str = "cuda"
    block_ms: int = 200
    sample_rate: int = 48000

    def validate(self) -> None:
        if self.device not in ("cuda", "cpu"):
            raise ValueError("運算裝置必須是 cuda 或 cpu")
        if self.block_ms not in (100, 150, 200, 250, 300, 400):
            raise ValueError("不支援此音訊區塊長度")
        if self.sample_rate != 48000:
            raise ValueError("第一版音訊取樣率固定為 48000 Hz")
        for value, low, high, label in (
            (self.pitch, -24, 24, "音高"),
            (self.index_rate, 0, 1, "檢索比例"),
            (self.monitor_gain, 0, 2, "監聽音量"),
            (self.output_gain, 0, 2, "輸出音量"),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{label}超出有效範圍")


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or data_dir() / "settings.json"
    defaults = AppSettings(assets_dir=str(data_dir() / "assets"))
    if not path.exists():
        return defaults
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("不支援的設定檔格式；請重新設定")
    values = raw.get("settings")
    if not isinstance(values, dict):
        raise ValueError("設定檔內容不正確")
    allowed = {f.name for f in fields(AppSettings)}
    for key, value in values.items():
        if key in allowed:
            baseline = getattr(defaults, key)
            if isinstance(baseline, bool):
                valid = isinstance(value, bool)
            elif isinstance(baseline, float):
                valid = type(value) in (int, float)
            else:
                valid = type(value) is type(baseline)
            if not valid:
                raise ValueError(f"設定欄位 {key} 的型別不正確")
            setattr(defaults, key, value)
    defaults.validate()
    return defaults


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    settings.validate()
    path = path or data_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"schema_version": 1, "settings": asdict(settings)}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
