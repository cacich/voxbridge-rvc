# RVC source provenance

`module/*.py` and `rmvpe.py` derive from [RVC-Project/Retrieval-based-Voice-Conversion-WebUI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/tree/81eed5e8f68b6bed1789f682fe78cdd324495afc), commit `81eed5e8f68b6bed1789f682fe78cdd324495afc`, MIT license (see LICENSE). The source copies have relative imports adapted to this package. `rmvpe.py` removes the upstream device configuration import and makes device selection explicit. `cuda_graph.py` is a local eager implementation of the upstream call interface.

The HuBERT loading and streaming orchestration are implemented in `../engine.py` against the same upstream commit's `infer/hubert.py`, `infer/rtrvc.py`, and `realtime_gui.py`. HuBERT and RMVPE weights come from the pinned [VoiceConversionWebUI model repository](https://huggingface.co/lj1995/VoiceConversionWebUI/tree/e6d0c1a17da07c33557852f9dfa2bd44cc75737d) revision via the app's explicit download action. They are not redistributed here.
