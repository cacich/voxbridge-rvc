"""Windows onedir bundle. Run from the repository root."""

from pathlib import Path
from importlib.util import find_spec

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata


root = Path.cwd()
hiddenimports = []
datas = [(str(root / "LICENSE"), "."), (str(root / "THIRD_PARTY_NOTICES.md"), ".")]
binaries = []

# Torch, Qt, NumPy and Librosa use PyInstaller's maintained package hooks. Faiss
# loads native SWIG modules dynamically; HuBERT is selected by Transformers'
# lazy importer. Collect only those two dynamic portions.
for package in ("torch", "faiss", "transformers"):
    if find_spec(package) is None:
        raise RuntimeError(f"Missing inference dependency: {package}")
faiss_datas, faiss_binaries, faiss_hidden = collect_all("faiss")
datas += faiss_datas
binaries += faiss_binaries
hiddenimports += faiss_hidden
hiddenimports += collect_submodules("transformers.models.hubert")

# Preserve runtime version metadata and the licenses shipped with each wheel.
# Qt remains dynamically linked in the onedir distribution.
for distribution in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6",
                     "torch", "faiss-cpu", "librosa", "soundfile", "transformers",
                     "sounddevice", "scipy", "numpy", "tokenizers", "safetensors",
                     "huggingface-hub", "packaging"):
    datas += copy_metadata(distribution)

datas += collect_data_files("voxbridge", includes=["vendor/**/*", "resources/**/*"])
a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook", "pytest", "tensorflow", "jax", "flax", "torchvision"],
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
    icon=str(root / "src" / "voxbridge" / "resources" / "voxbridge.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VoxBridge",
)
