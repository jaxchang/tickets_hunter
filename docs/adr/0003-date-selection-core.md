# 日期選擇抽出共用核心，平台只留 DOM adapter

13 個平台的日期選擇 handler 管線形狀一致（抽列→過濾→關鍵字比對→fallback/strict→模式挑選→點擊），但關鍵字優先比對演算法（群組依序嘗試、字串=單一子字串、清單=AND、首命中勝出）被複製貼上至少 6 份，各自演化；util 既有的 `get_matched_blocks_by_keyword` 全 repo 只有 hkticketing 一個呼叫者。架構健檢（2026-11-19）候選 #4 主張深化為一個模組。我們決定：在 `src/date_selection.py` 提供純函式核心 `select_date(rows, config_view) -> index | None`，平台 handler 只負責抽列（文字、adapter 自行計算的 `available`、不透明的 `payload`）與點擊被選中的 payload；相依方向 `platforms → date_selection → util`。試點 kktix 先行、ticketplus 接著，其餘平台分批遷移，每家遷移前先以特徵化測試釘住現行行為。

核心的編排（available 過濾→關鍵字排除→優先比對→fallback→模式挑選）中，排除述詞與模式挑選直接沿用 util 既有 helper（`reset_row_text_if_match_keyword_exclude`、`get_target_index_by_mode`）。但優先比對本身不沿用 `get_matched_blocks_by_keyword`：該函式直接對 live element 讀 `innerHTML`、收集命中群組的全部列再以模式挑選，與 handler 們實際的「文字列、首命中勝出、模式不介入關鍵字命中」是不同契約，強行收編會改變行為（例如 bottom 模式下關鍵字命中會改選最後一列）。核心因此實作文字列版的優先比對，語意以 kktix 特徵化測試釘定；後續平台遷移時若其複製品語意不同，以該平台的特徵化測試仲裁，不默默正規化。

## Considered Options

- **核心直接收編 `util.get_matched_blocks_by_keyword`**：該函式與 DOM element 耦合且命中基數與模式介入點都與 handler 現行行為不同，收編即行為變更，否決（見上）。
- **售完判定也進核心**：各平台售完訊號互不可比（文字清單、`disabled` 屬性、CSS class、JS 端過濾、按鈕存在與否），統一判定需要核心理解各平台 DOM，邊界即刻崩壞；改由 adapter 先算成 `available` 布林，否決進核心。
- **reload/無命中行為進核心**：各平台 reload 與冷卻、回傳值語意尚未統一（cityline 仍是舊式 sleep-reload、hkticketing 回傳 tuple），拉進核心等於被迫先統一另一批行為，超出本候選範圍，否決。
- **13 個 handler 一次全遷**：同時改 13 條搶票路徑，出問題無法定位是哪家的 adapter 還是核心；改為試點（kktix+ticketplus）後分批，否決一次全遷。

## Consequences

- 日期選擇的修復（關鍵字排除、售完把關、strict 行為）從此有一處 locality：核心改一次，遷移完成的 platform 全部受惠；尚未遷移的 platform 不受影響。
- 新 platform 的日期階段只需寫 adapter（抽列、算 available、點擊），不再複製比對邏輯。
- 核心是純函式：以 `tests/test_kktix_date_auto_select.py` 的特徵化測試為契約，後續每家遷移都先補自己的特徵化測試、換接後必須原樣全綠。
- 上游同步摩擦集中在各 platform 的日期 handler 區段（改為 adapter 形狀）；核心模組是 fork 新增檔案，無上游衝突面。
