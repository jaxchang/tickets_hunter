# 平台進入點收斂為單一介面與註冊表派發

主迴圈以一段寫死的 if 鏈分派 11 個平台模組的主函式（`nodriver_tixcraft.py`），但各主函式的簽名有四種形狀（有無 url、ocr、Captcha_Browser 各異）、回傳有四種語意（quit 布林、tab、狀態 dict、無回傳），且呼叫端對回傳的處理逐平台不同：kktix/tixcraft 的 is_quit_bot 被呼叫端立刻中和（等於沒有作用）、ibon 的 tab 回傳被丟棄、kham 暫停路徑回傳 False 而呼叫端無條件覆寫 tab、ticketplus 的完成 latch 放在主迴圈裡靠狀態 dict 驅動。架構健檢（2026-11-19）候選 #1 主張在 dispatcher 後建立單一平台介面。我們決定：平台模組各自宣告 `PLATFORM_ENTRIES`（子字串 patterns、可選 match 函式、handler），主迴圈彙整為 `PLATFORM_REGISTRY` 並逐 entry 迭代派發；進入點契約統一為單一 `PlatformContext`（`nodriver_common`：tab、url、config_dict、ocr、captcha_browser），回傳新 tab 或 None（None 表示沿用當前 tab）。

保真是硬約束：註冊表順序與 run-all-matches 語意（無 elif、命中多個 entry 就依序都跑）與 if 鏈完全一致，matcher 對原始 URL 做大小寫敏感的子字串比對——包括其已知碰撞（kham 的 `ticket.com.tw` 是 `famiticket.com.tw` 的子字串，FamiTicket 頁面本來就會連 kham 主函式一起跑）也原樣保留並以特徵表測試釘住，是否修正另案決定。平台專屬結果回歸平台自身：ticketplus 的 purchase latch 移入其 `_state`（`purchase_done`），狀態 dict 回傳廢除；已死的 is_quit_bot 回傳直接移除。遷移分批進行：試點先切換整個 dispatcher，未遷移平台以各模組內的 legacy adapter 保真其現行有效語意（逐一註明丟棄什麼、透傳什麼），隨各平台遷移批次刪除 adapter；真正的行為變化（kham 的 False 覆寫修正、ibon 的 tab 回傳採納）只在各自批次發生並於 PR 標記。

## Considered Options

- **只統一簽名與回傳、保留 if 鏈**：dispatcher 的分派知識（哪個 URL 歸哪個平台）仍散在主迴圈，各平台的新增與更動都要改主檔，介面深度不足，否決。
- **matcher 集中在主迴圈的註冊表資料裡**：URL 型態是各售票系統自己的知識（家族別名、UTK RWD 的 host 解析、Facebook 的 login 前綴），集中宣告會讓主迴圈重新耦合各系統細節；由平台模組自宣告、主迴圈只彙整與迭代，否決集中式。
- **改為 first-match-wins（專屬派發）**：會改變現行 run-all 語意，且 FamiTicket/Kham 的子字串碰撞使「誰先命中」立刻成為行為問題；本案只搬機制不改語意，否決。
- **context 做成 dict 或位置參數全員對齊**：dict 無欄位契約、打錯字靜默失敗；位置參數讓不用 ocr 的平台也得收下五個參數且未來加欄位要動全部簽名。dataclass 型別的 context 只在進入點解包一次，內部程式碼不動，否決前兩者。

## Consequences

- 新增平台的形狀固定：在自己的模組宣告 entries、主函式吃 context 回 tab/None，主迴圈只加一行彙整（`docs/02-development/code-boundaries.md` 第 10 節流程同步更新）；CLAUDE.md 加強制規則守住契約。
- 派發語意第一次有測試：matcher 特徵表 + 註冊表順序/完整性 + dispatcher 組裝的結構檢查（測試環境無法 import 主迴圈模組，故以原始碼結構斷言守住兩邊不漂移）。
- ticketplus 的完成 latch 從主迴圈消失，主迴圈不再知道任何平台專屬結果；`purchase_completed` 的宣告理由同步改為 latch 自用。
- 上游同步摩擦：`nodriver_tixcraft.py` 的派發段整段改形、各平台模組尾端新增 entries 宣告；上游若改動派發段，同步時需人工對照註冊表順序與 matcher。
