# 同步工作以 asyncio.to_thread 卸載自 event loop，daemon thread 只給 fire-and-forget

效能盤點（2026-11-20）發現主迴圈的凍結源都在 handler 層把同步工作直接跑在 event loop 上：ibon livemap 每 pass 同步 `requests.get`（上限 3 秒）、OCR 推論在六個站點同步執行（單次數十到數百毫秒）。repo 既有的唯一卸載手段是 `threading.Thread(daemon=True)`，用於 Discord/Telegram 通知與音效——那些是 fire-and-forget、不需要回傳值的工作。

我們決定：async 流程內「需要回傳值、且流程要等它」的同步工作（HTTP 抓取、OCR 推論、解析）一律以 `asyncio.to_thread` 卸載（首例為 ibon livemap，#74；OCR 批次接續），呼叫端以 `_state` 記 in-flight 任務做 single-flight 與結果收割，不在 event loop 上直接執行同步 I/O 或 CPU 工作。fire-and-forget 的副作用維持 daemon thread，不改。卸載後的資料新鮮度以「上一份結果」為界（single-flight 的天然節流），需要即時性的決策保留既有的錯誤兜底機制（如 livemap 的 failed-area 拉黑），不另疊加 TTL 參數。

## Considered Options

- **維持同步呼叫、只加冷卻節流**：冷卻只降低凍結頻率，凍結本身（最長 3 秒）還是在開賣瞬間可能發生，否決。
- **背景常駐任務持續刷新快取**：多一條要管理生命週期的長駐任務（啟停、換頁、例外重啟），而 single-flight 的按需抓取已足夠，否決。
- **改用 aiohttp 等 async HTTP 函式庫**：新增相依且只解 HTTP 一種工作，OCR 的 CPU 卸載仍要 to_thread；兩種機制並存不如統一，否決。

## Consequences

- 新增同步工作時先問「流程要不要等它的回傳值」：要 → `asyncio.to_thread`；不要 → daemon thread。code review 依此檢查。
- 卸載的工作在執行緒中執行：傳入的物件以唯讀為原則（如 debug logger、設定 dict），結果經 task 回傳，不在執行緒內改 `_state`。
- 主迴圈的反應時間不再被單一 handler 的網路/CPU 工作綁架；代價是 fast-path 類決策最多舊一個抓取往返，屬已接受的取捨。
