"""Exercise the standalone Windows updater with a synthetic onedir runtime."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile


root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_update", root / "scripts" / "package-update.py")
package_update = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(package_update)


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def invoke(update_dir: Path, install_dir: Path, *, fail_after_first: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if fail_after_first:
        env["VOXBRIDGE_UPDATE_TEST_FAIL_AFTER_FIRST_COPY"] = "1"
    else:
        env.pop("VOXBRIDGE_UPDATE_TEST_FAIL_AFTER_FIRST_COPY", None)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(update_dir / "Apply-Update.ps1"), "-InstallDir", str(install_dir)],
        text=True, encoding="utf-8", errors="replace", capture_output=True, env=env, timeout=30,
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="voxbridge-updater-test-") as temp:
        root_dir = Path(temp)
        baseline = root_dir / "baseline"
        update_dir = root_dir / "patch"
        update_dir.mkdir()
        write(baseline / "VoxBridge.exe", b"old application")
        write(baseline / "_internal" / "python312.dll", b"stable runtime")
        write(baseline / "_internal" / "voxbridge" / "resources" / "voxbridge.ico", b"old icon")
        write(baseline / "_internal" / "voxbridge" / "vendor" / "LICENSE", b"old vendor data")
        for name, version in (("torch", "2.7.1+cu118"), ("PySide6", "6.8.3"),
                              ("transformers", "4.49.0"), ("faiss-cpu", "1.15.1")):
            write(baseline / "_internal" / f"{name}-{version}.dist-info" / "METADATA",
                  f"Name: {name}\nVersion: {version}\n".encode())
        package_update.create(baseline, root_dir / "original.zip")
        shutil.copytree(baseline, root_dir / "success")
        shutil.copytree(baseline, root_dir / "mismatch")
        shutil.copytree(baseline, root_dir / "rollback")
        shutil.copytree(baseline, root_dir / "damaged-runtime")
        shutil.copytree(baseline, root_dir / "missing-runtime")
        shutil.copytree(baseline, root_dir / "tampered-payload")

        patch_exe = b"new application"
        patch_icon = b"new icon"
        patch_vendor = b"new vendor data"
        write(update_dir / "VoxBridge.exe", patch_exe)
        write(update_dir / "_internal" / "voxbridge" / "resources" / "voxbridge.ico", patch_icon)
        write(update_dir / "_internal" / "voxbridge" / "vendor" / "LICENSE", patch_vendor)
        shutil.copy(root / "scripts" / "Apply-Update.ps1", update_dir / "Apply-Update.ps1")
        manifest = json.loads((baseline / "runtime.json").read_text())
        base_version = manifest["version"]
        version_parts = [int(part) for part in base_version.split(".")]
        version_parts[-1] += 1
        target_version = ".".join(str(part) for part in version_parts)
        update = {
            "format": 1,
            "version": target_version,
            "minimum_base_version": "0.2.0",
            "runtime_id": manifest["runtime_id"],
            "required_runtime_files": list(manifest["protected_files"]),
            "files": {
                "VoxBridge.exe": package_update.digest(update_dir / "VoxBridge.exe"),
                "_internal/voxbridge/resources/voxbridge.ico": package_update.digest(update_dir / "_internal/voxbridge/resources/voxbridge.ico"),
                "_internal/voxbridge/vendor/LICENSE": package_update.digest(update_dir / "_internal/voxbridge/vendor/LICENSE"),
            },
        }
        (update_dir / "update.json").write_text(json.dumps(update), encoding="utf-8")

        # Windows PowerShell 5.1 needs BOM to decode the updater's Chinese strings.
        updater = update_dir / "Apply-Update.ps1"
        updater.write_bytes(updater.read_text(encoding="utf-8").encode("utf-8-sig"))
        with ZipFile(root_dir / "original.zip") as archive:
            assert archive.read("Apply-Update.ps1").startswith(b"\xef\xbb\xbf")

        success = root_dir / "success"
        result = invoke(update_dir, success)
        assert result.returncode == 0, result.stderr
        assert (success / "VoxBridge.exe").read_bytes() == patch_exe
        assert (success / "_internal/voxbridge/vendor/LICENSE").read_bytes() == patch_vendor
        assert json.loads((success / "runtime.json").read_text(encoding="utf-8-sig"))["version"] == target_version
        assert (success / "_internal/python312.dll").read_bytes() == b"stable runtime"

        mismatch = root_dir / "mismatch"
        broken = json.loads((mismatch / "runtime.json").read_text())
        broken["runtime_id"] = "wrong"
        (mismatch / "runtime.json").write_text(json.dumps(broken), encoding="utf-8")
        result = invoke(update_dir, mismatch)
        assert result.returncode != 0
        assert (mismatch / "VoxBridge.exe").read_bytes() == b"old application"

        damaged = root_dir / "damaged-runtime"
        write(damaged / "_internal/python312.dll", b"broken runtime")
        result = invoke(update_dir, damaged)
        assert result.returncode != 0
        assert (damaged / "VoxBridge.exe").read_bytes() == b"old application"

        missing = root_dir / "missing-runtime"
        changed_update = dict(update)
        changed_update["required_runtime_files"] = update["required_runtime_files"] + ["_internal/new.dll"]
        (update_dir / "update.json").write_text(json.dumps(changed_update), encoding="utf-8")
        result = invoke(update_dir, missing)
        assert result.returncode != 0
        assert (missing / "VoxBridge.exe").read_bytes() == b"old application"
        (update_dir / "update.json").write_text(json.dumps(update), encoding="utf-8")

        tampered = root_dir / "tampered-payload"
        write(update_dir / "VoxBridge.exe", b"tampered")
        result = invoke(update_dir, tampered)
        assert result.returncode != 0
        assert (tampered / "VoxBridge.exe").read_bytes() == b"old application"
        write(update_dir / "VoxBridge.exe", patch_exe)

        rollback = root_dir / "rollback"
        result = invoke(update_dir, rollback, fail_after_first=True)
        assert result.returncode != 0
        assert (rollback / "VoxBridge.exe").read_bytes() == b"old application"
        assert (rollback / "_internal/voxbridge/resources/voxbridge.ico").read_bytes() == b"old icon"
        assert json.loads((rollback / "runtime.json").read_text())["version"] == base_version
        print("Updater success, incompatible/tampered refusal, rollback, and UTF-8 BOM checks passed")


if __name__ == "__main__":
    main()
