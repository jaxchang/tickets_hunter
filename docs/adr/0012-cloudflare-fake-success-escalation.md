# Cloudflare「假成功」重現達上限後，升級為等待狀態

2026-11-20 的 ibon 實戰（ADR 0011 合併後）：主迴圈偵測器已能攔下 orders 頁的 Turnstile，但陷入假成功循環——CDP 點擊打鉤後，widget 的 response field 消失，`wait_for_challenge_cleared` 以「field 消失」判定通過，handler 回報成功；然而 Turnstile 伺服器端未接受該次自動互動，widget 隨即重置，下一輪偵測又命中、又點擊、又「成功」，約 8 秒一輪無限循環。ADR 0010 的等待狀態以 handler 回報失敗為觸發條件，假成功下永遠不觸發；使用者手動打鉤則可通過（真人互動能通過 Turnstile 的風險判定）。

我們決定（#104）：主迴圈為「回報成功」記帳——handler 成功後，同一 URL 的覆檢又偵測到驗證即記一次「重現」（`cf_state` 的 `solved_url`／`reappearances`，邏輯在 `nodriver_common` 的 `cf_note_solved`／`cf_note_detected`／`cf_note_clean`）；重現達 2 次（`CF_REAPPEARANCE_LIMIT`）即不再呼叫 handler，直接進入 ADR 0010 的等待狀態等人手動完成。URL 變化或覆檢乾淨即結束該回合、計數歸零；進入等待亦清除記帳。等待的進入宣告（橫幅＋提示音）抽成 `announce_cf_wait`，失敗計數與重現兩條進入路徑共用。

同批修復的兩條壞路：`util.verify_cf_with_templates` 沿用舊 nodriver 的 `verify_cf` 簽名（`flash`／`template_image`），zendriver 的 `verify_cf` 沒有這些參數、每次呼叫立刻 TypeError——改為單次原生 `tab.verify_cf()` 呼叫，模板機制（zendriver 不支援）連同失去唯一呼叫者的 `get_cf_template_paths` 一併移除，函式名與回傳契約保留；`solve_turnstile_checkbox` 對尚未 layout 的 widget（0x0）照點盲座標——加上寬或高小於 10px 時不點、回傳 False 的守衛。

## Considered Options

- **收緊 `wait_for_challenge_cleared`，拿掉「field 消失」證據**：該證據是全頁 interstitial 的既有設計（該類驗證以離開頁面證明通過，field 消失是其在原頁的對應訊號），拿掉會改變多種頁型的成敗判定；假成功在主迴圈層即可收斂，否決。
- **handler 內部自己計重現**：重現的定義跨越「handler 回報成功→主迴圈覆檢」兩個層級，狀態放在主迴圈的 `cf_state` 才完整；且各平台可能各自呼叫偵測，集中在主迴圈只有一份帳，否決。
- **verify_cf 模板路徑保留簽名錯誤、只加 try 吞掉**：它本來就在 try 內、錯誤已被吞——問題是它永遠不可能成功；修成原生呼叫才讓第三順位 fallback 恢復存在意義，否決。

## Consequences

- 自動互動過不了的驗證，bot 最多嘗試約兩輪 handler（每輪含其內部重試）就會停下來等人，不再無限點擊 widget——同時降低被站方升級風控的風險。
- 若某頁的驗證在成功後本來就會短暫重現（例如 token 傳播較慢），重現 2 次後會被請去等待：代價是使用者需手動完成一次，換取不漏掉真假成功；門檻是常數，之後可依實戰調整。
- `verify_cf_with_templates` 的成功語意維持「未拋例外即 True」，真正的通過與否仍由呼叫端 `wait_for_challenge_cleared` 驗證，與修復前設計相同。
