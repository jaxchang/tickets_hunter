# TicketPlus 日期選擇維持雙路徑，僅修兩處已定性缺陷

`nodriver_ticketplus_date_auto_select` 有兩條選擇路徑：Vue 資料層路徑（讀 `.eventClass.__vue__.$data.sessions`，在 Python 端比對後直接導向 order 頁）與 DOM 路徑（收集容器文字、在 JS 內比對後點按鈕）。架構健檢候選 #4 原訂將它遷入 `date_selection` 核心，2026-11-19 讀畢 handler 後擱置：它的分歧在比對語義本身、且兩路徑彼此不一致。2026-11-20 重新盤點後定性如下表——兩項是缺陷、其餘是 per-system 有意保留（各售票系統的差異是設計、不是待統一的缺陷，見候選 #4 的結案原則）。

| 面向 | Vue 路徑（常態） | DOM 路徑（Vue 未命中才跑） | 定性 |
| --- | --- | --- | --- |
| `date_auto_select.mode` | 完全忽略，永遠取第一個命中場次 | 遵循（top/bottom/center/random） | **缺陷**：使用者設定在常態路徑失效 |
| 蜜罐守衛 | 命中 `KNOWN_FAKE_SESSION_IDS` 時跳過導航 | 無守衛，可能接著點到同一場次 | **缺陷**：守衛意圖未貫穿 |
| 關鍵字文法 | JSON 群組；群組內空格切分、全子字串 AND | 逗號切分、任一命中 OR；不支援群組/AND | 有意保留 |
| 比對順序 | 場次為主（第一個命中任一群組的場次勝出） | 列為主（收集命中列後再按 mode 挑） | 有意保留 |
| 空白正規化 | 僅 lowercase；空白是 AND 分隔符 | 剝除全部空白（含全角）再比對 | 有意保留 |
| 售完把關 | 候選集來自 Vue 資料（無可用性欄位）；前段逐列售完過濾只決定路徑是否啟動 | 候選集以「有 nextBtn」隱含過濾 | 有意保留 |
| fallback | 無命中且開啟 → 第一個（已排除過濾後）場次 | 無命中且開啟 → 全部（已排除過濾後）容器再挑 | 有意保留 |
| 排除（keyword_exclude） | 兩路徑共用 Python 側 `_ticketplus_filter_excluded`，先排除再比對/fallback | 同左 | 一致，無分歧 |

我們決定：不遷移 `date_selection` 核心、不統一兩路徑文法；只修兩處缺陷，且修法壓到最小——Vue 路徑對「有關鍵字命中」的集合套用 mode 挑選（共用 util 的 matched-list 挑選器；fallback 與空關鍵字的既有流程不動）；蜜罐 id 先從 Vue 候選集中濾除，僅當命中集全為假場次時才中止該輪並阻斷 DOM 路徑（回傳 False、不新增 `_state` 或冷卻）。修復前先以特徵化測試釘住非缺陷行為（`tests/test_ticketplus_date_auto_select.py`，FakeTab stub evaluate 驅動 Vue 路徑；DOM 路徑只釘其 Python 側的 allowed_indexes 計算，JS 內比對不做單元測試）；兩缺陷不寫釘住錯誤行為的測試，修復後直接以正確行為寫入。`KNOWN_FAKE_SESSION_IDS` 維持 handler 內常數：它是平台對抗細節，不是使用者設定。

## Considered Options

- **遷移 date_selection 核心（候選 #4 原案）**：核心是群組為主的首群組勝出，與 Vue 路徑的場次為主不同——遷移即改變優先序；DOM 路徑的 JS 內比對還得整段搬回 Python。改動面與行為風險都遠超缺陷本身，否決。
- **單向對齊文法（其一向另一統一）**：改變多數實際搶票行為，且牴觸 per-system 原則，否決。
- **現況全部文件化、缺陷也接受**：mode 失效與蜜罐繞過分別牴觸使用者設定與守衛意圖，不能以「設計差異」帶過，否決。
- **蜜罐命中即整輪放棄（不濾除續挑）**：比濾除更保守，但真場次與假場次並存時會白白放棄可買的場次；濾除＋全假才中止已保證永不導向假場次，否決。

## Consequences

- 修復後，使用者在 TicketPlus 設定的 date mode 在常態路徑生效；蜜罐場次永遠不會被導航或點按，全假時該輪直接放棄、主迴圈下一輪重試。
- 兩路徑的文法差異被正式記錄為設計：同一份 date_keyword 在兩路徑可能選到不同場次，這是已知且接受的行為，調整前應先改本 ADR。
- 特徵化測試成為此 handler 的回歸網：上游同步或平台改版動到此區段時，先跑測試再對照本表判斷是行為漂移還是刻意變更。
