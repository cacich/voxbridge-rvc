# Third-party notices

VoxBridge packages third-party Python packages under their respective licenses.
The release builder copies package metadata (including wheel-provided license
files) into the PyInstaller distribution. Qt libraries remain dynamically linked
in the onedir bundle. The following source code is vendored directly:

- RVC-Project/Retrieval-based-Voice-Conversion-WebUI, commit
  `81eed5e8f68b6bed1789f682fe78cdd324495afc`, MIT license.
  Source attribution and local modifications are documented in
  `src/voxbridge/vendor/PROVENANCE.md`; the license is at
  `src/voxbridge/vendor/LICENSE`.

PyTorch and TorchAudio, Qt for Python, Transformers, NumPy, SciPy, Librosa,
FAISS, SoundDevice, and their transitive dependencies retain their upstream
licenses. Refer to the bundled package metadata or their upstream repositories
for the license text applicable to a particular build.

Model files (`.pth`, `.index`), HuBERT weights, and RMVPE weights are not part of
this repository or its installers. Users supply their own model files.
