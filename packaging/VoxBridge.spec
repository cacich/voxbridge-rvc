"""Windows onedir bundle. Run from the repository root."""

from pathlib import Path
from importlib.util import find_spec

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata


root = Path.cwd()
hiddenimports = []
datas = [(str(root / "LICENSE"), "."), (str(root / "THIRD_PARTY_NOTICES.md"), ".")]
binaries = []

# RVC loads these packages through configuration paths after the GUI has started.
for package in ("torch", "torchaudio", "faiss", "librosa", "soundfile", "transformers"):
    if find_spec(package) is None:
        raise RuntimeError(f"Missing inference dependency: {package}")
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Preserve runtime version metadata and the licenses shipped with each wheel.
# Qt remains dynamically linked in the onedir distribution.
for distribution in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6",
                     "torch", "torchaudio", "faiss-cpu", "librosa", "soundfile",
                     "transformers", "sounddevice", "scipy"):
    datas += copy_metadata(distribution, recursive=True)

datas += collect_data_files("voxbridge", includes=["vendor/**/*"])
a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoxBridge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VoxBridge",
)
