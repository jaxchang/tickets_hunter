# 共用偵測器新增全頁式 Cloudflare 驗證的標記

2026-11-20 的 ibon 實戰：選完日期、點購買跳到 orders 頁後，頁面是 Cloudflare 全頁式驗證（interstitial）。ibon area 的本地守衛以頁面文字偵測到它，但只等 15 秒就交回主迴圈，形成 15 秒一輪的循環；而 ADR 0010 的主迴圈等待狀態沒有接手——共用偵測器 `detect_cloudflare_challenge` 看不見這種頁面：L1/L2 找的是 Turnstile iframe（全頁式驗證不是 widget，沒有 iframe），L3 只掃 HTML 原始碼裡的英文與 class 標記，中文版 interstitial 的標誌（標題「請稍候...」、「驗證您是否是人類」）不在清單內。

我們決定（#102）：L3 在同一次 in-page evaluate 內加掃 `document.title` 與 `document.body.innerText`，標記為標題含「請稍候」、內文含「驗證您是否是人類」，另在 HTML 標記清單加入與語系無關、專屬驗證頁的 `/cdn-cgi/challenge-platform/` 腳本路徑。標記的取捨以專一性優先於召回率：共用偵測器的誤判代價是整個 bot 進入等待狀態停擺，因此不收 ibon 守衛有用的「正在驗證」單獨成立（一般頁面也可能出現）。標記清單提升為模組常數（`CLOUDFLARE_HTML_INDICATORS`／`CLOUDFLARE_TITLE_MARKERS`／`CLOUDFLARE_TEXT_MARKERS`），使測試能釘住清單內容與產生的掃描腳本。ibon 的本地守衛不動，退居第二道防線。

## Considered Options

- **只依賴 ibon 本地守衛、主迴圈不補偵測**：其他平台沒有等價守衛，同樣的 interstitial 出現在別的平台仍是盲區；且守衛的 15 秒循環沒有通知與升級路徑，否決。
- **把中文標記也掃 HTML 原始碼就好（不掃 innerText）**：原始碼含未渲染的模板與腳本字串，誤判面較大；ibon 守衛實戰驗證過的是渲染文字（innerText），跟隨其表面，否決。
- **連「正在驗證」一起收，提高召回率**：該詞太泛，一般頁面文案即可命中；以等待狀態的停擺代價衡量不划算，否決。

## Consequences

- 全頁式驗證在 URL 變化與週期檢查時都能被主迴圈攔下，走 ADR 0010 的自動處理→等待狀態流程；本地守衛的 15 秒循環在這類頁面不再是唯一防線。
- 已知限制（另案再議）：週期檢查以 pass 計，handler 阻塞時會被稀釋（本次事故中每 pass 約 15 秒，200 pass 約 50 分鐘）——URL 變化型驗證不受影響，同頁中途出現的驗證在阻塞型平台上仍可能延遲接手。
- 誤判面：標題含「請稍候」的一般頁面極少（該標題是 Cloudflare 在中文語系的 interstitial 固定標題）；若未來出現誤判案例，調整的是標記常數，不需動偵測結構。
