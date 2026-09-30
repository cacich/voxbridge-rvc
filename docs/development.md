# 開發與驗證

## 模組分工

- `app.py`：PySide6 介面與下載工作執行緒；模型載入與音訊處理不在 Qt 主執行緒執行。
- `audio.py`：PortAudio 擷取／輸出、獨立監聽與有界佇列。模型處理不在音訊 callback 裡執行。主要輸入或輸出中斷會停止並回報錯誤；獨立監聽中斷只關閉監聽。程式不會偷偷改用系統預設裝置。
- `engine.py`：RVC v1／v2、RMVPE、選用 FAISS 索引、歷史音訊上下文及重疊對齊。第一版只處理 speaker ID 0。
- `assets.py`：固定 Hugging Face revision、SHA-256 驗證與原子替換下載。
- `settings.py`／`diagnostics.py`：每位使用者的設定與手動匯出診斷；不收錄錄音或模型內容。
- `vendor/`：最小 RVC 推論原始碼及來源、修改與 MIT 授權記錄。

引擎的輸入與輸出固定 48 kHz 單聲道；麥克風、主要輸出和獨立監聽各自協商裝置支援的取樣率與一／兩聲道。雙聲道擷取會平均成單聲道，輸出複製至兩聲道；soxr 串流重取樣器在音訊工作執行緒銜接不同取樣率。CUDA 使用 float32；後續需根據指定 RTX 4070 SUPER 上的結果決定是否提供半精度選項。

## 本機測試

一般測試不需要模型或麥克風：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src/voxbridge tests scripts
```

完整推論測試需先安裝引擎依賴並下載固定版本的基礎模型。設定資產位置後再執行：

```powershell
$env:VOXBRIDGE_TEST_ASSETS = 'D:\your-path\assets'
.\.venv\Scripts\python.exe -m pytest tests/test_engine_integration.py -q
```

測試自行建立小型隨機權重的 RVC 匯出 checkpoint，涵蓋 v1／v2、有／無 F0、FAISS 檢索、連續區塊與 reset。這驗證載入與張量／音訊流程，**不衡量聲線相似度、聽感或實際端到端延遲**。

封裝流程另外執行 `--smoke-test` 與 `--check-engine`，分別檢查 GUI 啟動與引擎依賴是否被打包。`--check-engine-report PATH` 可將檢查結果寫到 JSON。測試通過之後仍須按照家中驗收表測試 GPU、安裝、麥克風及 Discord。

## 版本與限制

`constraints-windows.txt` 固定目前驗證的 Windows Python 3.12 依賴。CPU 與 CUDA PyTorch wheel 分開指定，避免套件安裝時意外替換運算後端。GitHub 建置使用 CUDA 11.8 wheel；基礎模型另行下載，不隨安裝包提供。

目前是 0.2.0 Beta：不包含模型訓練、虛擬音訊驅動、程式內自動更新或多說話者選擇。私人聲線模型保持在使用者電腦，資料夾及大檔已由 `.gitignore` 排除。發版時以 `build-info.txt` 中的 commit、依賴與雜湊識別確切建置。0.2.0 完整安裝會加入 `runtime.json`；其後更新器只在執行環境識別、檔案清單及雜湊都相容時套用程式檔案。`python scripts/test-update.py` 透過 Windows PowerShell 5.1 驗證正常更新、拒絕不相容執行環境及失敗還原。
