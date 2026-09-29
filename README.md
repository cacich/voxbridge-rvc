# VoxBridge RVC

Windows 11 桌面即時 RVC 變聲器，主要用於 Discord。這是 **0.1.0 Beta**：開發與自動檢查可在此環境完成；實際的 RTX 4070 SUPER、麥克風、虛擬音訊裝置和 Discord 效果仍需在使用者私人電腦驗收。

介面可以載入既有的 RVC `.pth` 模型與可選的 `.index`、選擇麥克風與變聲輸出裝置、調整音高和處理區塊。獨立的「聽到自己的變聲」開關預設關閉；關閉只影響本機耳機監聽，變聲訊號仍輸出到 Discord 使用的虛擬音訊裝置。程式不會自動上傳語音、錄音或模型。

## 在 Windows 11 安裝

1. 在 GitHub 專案的 **Actions → Windows** 選擇成功的 `main`／版本建置，從 **Artifacts** 下載 `VoxBridge-0.1.0-beta-win64`。Artifact 會在流程指定期限後過期；目前不保證已產生可下載的安裝包。
2. 解壓 Artifact，執行 `VoxBridge-0.1.0-beta-win64-setup.exe`。這是目前使用者安裝，不需系統管理員權限。也可解壓 `VoxBridge-0.1.0-beta-win64-portable.zip` 後執行其中的 `VoxBridge.exe`。
3. 準備你已訓練並**匯出的 RVC 推理模型** `.pth`（不是訓練 checkpoint）與可選的 `.index`。在介面中指定兩者路徑。你的模型保持在自己的電腦，不加入 Git 儲存庫或安裝包。
4. 第一次使用時，按「下載必要的引擎檔案」，等待 HuBERT 與 RMVPE 基礎資產下載完成。這些大型權重不隨安裝包提供。
5. 按照 [Discord 設定與驗收](docs/discord-setup.md) 接上虛擬音訊裝置，再按「開始變聲」。

目標硬體：Ryzen 7 9800X3D、GeForce RTX 4070 SUPER、16 GB RAM、Windows 11。CUDA 路徑建議使用近期 NVIDIA 驅動；CPU 模式只供功能排錯，未承諾可即時通話。模型實際的音質、延遲與負載依模型、音訊裝置和同時執行的程式而異。

## 從原始碼啟動

安裝 64 位元 Python 3.12 後，在 PowerShell 執行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch==2.7.1+cu118 torchaudio==2.7.1+cu118 --index-url https://download.pytorch.org/whl/cu118
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -r requirements-engine.txt -c constraints-windows.txt
.\.venv\Scripts\python.exe -m voxbridge.app
```

上列 PyTorch CUDA 11.8 wheel 選擇依照[官方版本安裝表](https://pytorch.org/get-started/previous-versions/)；它包含執行所需的 CUDA 程式庫，但仍需相容的 NVIDIA 驅動。

## 自行製作安裝包

另需 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。在 Windows PowerShell 執行：

```powershell
.\scripts\build-windows.ps1
```

腳本建立獨立 `.venv-build`、安裝固定的 CUDA PyTorch、執行測試、用 PyInstaller 建置資料夾版程式、執行 `--smoke-test` 與 `--check-engine`，最後產生安裝程式與 portable ZIP。只想建立 ZIP 可加 `-SkipInstaller`。這些檢查驗證 GUI 與推理依賴能載入，無法替代私人電腦上的模型與 Discord 驗收。

更新時直接執行新版安裝包。設定檔位於使用者資料目錄，安裝程式不會移除個人模型或設定；卸載亦不刪除這些資料。虛擬音訊驅動需由使用者另外安裝。

## 專案狀態

此版本是供家中電腦實測的 Beta，尚未宣稱在指定硬體上通過 Discord 實際通話測試。請按 [驗收與問題回報](docs/home-validation.md) 測試並回報版本、診斷資料和重現步驟。

架構、完整推論測試與目前限制見 [開發與驗證](docs/development.md)。

授權：專案程式碼採 [MIT License](LICENSE)；沿用的 RVC 程式碼及第三方套件資訊見 [第三方聲明](THIRD_PARTY_NOTICES.md)。模型權重與聲線檔不隨專案分發。
