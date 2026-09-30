"""Create a runtime contract and a small application-only update archive.

The full installer installs runtime.json. Updates require it; 0.1.0 installations
without this marker must use one full 0.2.0 installer first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tomllib
from email.parser import Parser
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_VERSION = 1
APP_PREFIX = "_internal/voxbridge/"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def bundled_versions(bundle: Path) -> dict[str, str]:
    versions = {}
    for metadata in (bundle / "_internal").rglob("*.dist-info/METADATA"):
        message = Parser().parsestr(metadata.read_text(encoding="utf-8", errors="replace"), headersonly=True)
        name, version = message.get("Name"), message.get("Version")
        if name and version:
            versions[name.lower().replace("_", "-")] = version
    for required in ("torch", "pyside6", "transformers", "faiss-cpu"):
        if required not in versions:
            raise ValueError(f"Missing bundled distribution metadata: {required}")
    return dict(sorted(versions.items()))


def runtime_id(bundle: Path, versions: dict[str, str]) -> str:
    contract = {
        "format": CONTRACT_VERSION,
        "layout": "pyinstaller-onedir",
        "python": sys.version.split()[0],
        "versions": versions,
        "spec": digest(ROOT / "packaging" / "VoxBridge.spec"),
        "constraints": digest(ROOT / "constraints-windows.txt"),
        "engine_requirements": digest(ROOT / "requirements-engine.txt"),
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


def app_files(bundle: Path) -> list[str]:
    files = ["VoxBridge.exe"]
    package_data = bundle / "_internal" / "voxbridge"
    if not package_data.is_dir():
        raise ValueError("Missing bundled VoxBridge resources")
    for path in package_data.rglob("*"):
        if path.is_file():
            if path.suffix.lower() in {".dll", ".pyd"}:
                raise ValueError("Native application module requires a full installer")
            files.append(path.relative_to(bundle).as_posix())
    if "_internal/voxbridge/resources/voxbridge.ico" not in files:
        raise ValueError("Missing bundled icon")
    return sorted(files)


def protected_files(bundle: Path) -> dict[str, str]:
    # Every external dependency file in the installed runtime is checked before
    # applying a code-only patch. App-owned data under voxbridge/ is patched.
    internal = bundle / "_internal"
    if not internal.is_dir():
        raise ValueError("Missing _internal runtime directory")
    files = {
        p.relative_to(bundle).as_posix(): digest(p)
        for p in internal.rglob("*")
        if p.is_file()
        and not p.relative_to(bundle).as_posix().startswith(APP_PREFIX)
        and "__pycache__" not in p.parts
        and p.suffix.lower() not in {".pyc", ".nbc", ".nbi", ".log", ".tmp"}
    }
    if not files or not any(p.endswith(".dll") for p in files):
        raise ValueError("No native runtime files found; this is not a PyInstaller onedir build")
    return dict(sorted(files.items()))


def create(bundle: Path, destination: Path) -> tuple[Path, Path]:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if tuple(map(int, version.split("."))) < (0, 2, 0):
        raise ValueError("Small updates require an initial 0.2.x full runtime")
    update_files = app_files(bundle)
    for name in update_files:
        if not (bundle / name).is_file():
            raise ValueError(f"Missing application update file: {name}")
    versions = bundled_versions(bundle)
    marker = {
        "format": CONTRACT_VERSION,
        "version": version,
        "runtime_id": runtime_id(bundle, versions),
        "torch_version": versions["torch"],
        "exe_sha256": digest(bundle / "VoxBridge.exe"),
        "protected_files": protected_files(bundle),
    }
    marker_path = bundle / "runtime.json"
    marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    update = {
        "format": CONTRACT_VERSION,
        "version": version,
        "minimum_base_version": "0.2.0",
        "runtime_id": marker["runtime_id"],
        "required_runtime_files": list(marker["protected_files"]),
        "files": {name: digest(bundle / name) for name in update_files},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            archive.writestr("update.json", json.dumps(update, indent=2, sort_keys=True) + "\n")
            updater = (ROOT / "scripts" / "Apply-Update.ps1").read_text(encoding="utf-8")
            archive.writestr("Apply-Update.ps1", updater.encode("utf-8-sig"))
            for name in update_files:
                archive.write(bundle / name, name)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return marker_path, destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("update_zip", type=Path)
    args = parser.parse_args()
    marker, archive = create(args.bundle.resolve(), args.update_zip.resolve())
    print(marker)
    print(archive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
