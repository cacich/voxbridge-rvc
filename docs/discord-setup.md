# Windows 11 與 Discord 設定

VoxBridge 需要一個虛擬音訊裝置，把程式播放的變聲結果轉成 Discord 可選的麥克風。可使用 [VB-Audio VB-CABLE](https://vb-audio.com/Cable/)；請照其官方安裝說明完成驅動安裝與必要的重開機。VoxBridge 安裝程式不會安裝虛擬音訊驅動。

| 位置 | 選擇 |
| --- | --- |
| VoxBridge「麥克風」 | 你的實體麥克風 |
| VoxBridge「變聲輸出」 | **CABLE Input**（Windows 播放裝置） |
| Discord → 使用者設定 → 語音與視訊 → 輸入裝置 | **CABLE Output**（Windows 錄音裝置） |
| Discord → 輸出裝置 | 你的耳機 |
| VoxBridge「監聽裝置」 | 你的耳機；只有勾選本機監聽才會聽到自己 |

`CABLE Input` / `CABLE Output` 的名稱方向看似相反，是 VB-CABLE 的播放端與錄音端；[VB-CABLE 官方手冊](https://vb-audio.com/Cable/VBCABLE_ReferenceManual.pdf)分別列在 Windows Playback 與 Recording。Discord 的裝置設定請參考[官方語音與視訊說明](https://support.discord.com/hc/en-us/articles/33030151293079-Discord-Voice-Video-Streaming-Guide)。

1. 先戴耳機，避免喇叭聲回到麥克風。
2. 在 VoxBridge 選擇 RVC `.pth`，若有 `.index` 再選索引檔。沒有索引時把「索引檢索比例」設為 `0%`。
3. 第一次啟動按「下載必要的引擎檔案」。選擇「NVIDIA GPU（CUDA，建議）」及正確的音訊裝置。
4. 按「開始變聲」，看麥克風與變聲輸出音量條是否有反應。先用 Discord 的麥克風測試，再進入通話。
5. 若想在耳機聽到自己的變聲，勾選「聽到自己的變聲」並調整監聽音量。關閉時 Discord 仍應收到變聲輸出。

若 Discord 收不到聲音，先核對 VoxBridge 輸出為 `CABLE Input`，Discord 輸入為 `CABLE Output`，再確認兩個程式沒有選到已拔除的裝置。若有爆音或斷續，先增加 VoxBridge 的處理區塊大小、關閉其他大量使用 GPU 的程式，並重新開始變聲。若仍異常，匯出診斷資料，按[驗收表](home-validation.md)回報。
