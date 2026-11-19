# 設定讀取收斂為每階段一個檢視（config_views）

各平台 handler 直接索引原始 `config_dict`（全 src 約 318 處 `config_dict[...]` 與 126 處 `.get()`，扣除 settings.py 本身），同一個鍵的預設值在各處以 `[]` 與 `.get()` 混寫、逐漸漂移（例如刷新間隔多數經 `util.get_reload_interval`，kktix/cityline 卻有 handler 直接讀 `advanced` 並自帶預設 3）。架構健檢（2026-11-19）候選 #2 主張深化 settings 為介面狹窄的設定模組。我們決定：新建葉節點模組 `src/config_views.py`，以「每共用管線階段一個檢視函式」逐步收斂讀取側；檢視是對當前 dict 的純函式，每次呼叫現算，禁止快照與快取。試點為日期檢視 `date_view(config_dict)`，產出 ADR 0003 核心所吃的 config 包；其餘檢視（刷新為第二批）分批跟進，每個檢視上線時在 CLAUDE.md 加一條對應的強制規則。

不快照是硬約束：主迴圈每圈呼叫 `reload_config()` 就地更新同一個 dict 的白名單欄位，設定 UI 的變更因此免重啟生效；任何在啟動時把 config 包成物件或快取檢視結果的設計都會切斷這條命脈。預設值以現行程式碼的實際行為為準逐鍵寫入檢視；同鍵不同預設的分歧（例如上述間隔 3 vs 0）只盤點標記、另案決定，不在遷移 PR 中偷偷統一——遷移不夾帶行為改變，與 ADR 0003 的特徵化紀律一致。檢視以共用階段切分、不以平台切分；平台專屬區段（如 `tixcraft`、`kktix` 節）與平台決定（例如 ticketmaster 不套用 keyword_exclude、kham 的 parse_error_all）留在各平台模組，檢視層不抹平各售票系統的差異。

## Considered Options

- **檢視放進 `settings.py`**：該檔已 1,155 行且兼 defaults、檔案/profile 管理、migration 與 Tornado 設定伺服器；handler 為了讀設定而 import 它等於把伺服器一起拖進來，相依方向也混亂，否決。獨立葉節點模組與 `date_selection` 的位置對稱。
- **啟動時建立 Config 物件/快照**：與 `reload_config()` 的就地更新衝突（見上），否決。
- **檢視做成 class 或屬性物件**：現行消費型態只有兩種——能算出答案的回答案、階段包回小 dict；class 只會多一層間接與生命週期問題，否決。
- **連存檔/UI/migration 一起重構**：寫入側是另一個表面（欄位級 endpoint 不存在、UI 以整個 JSON 存取），與讀取側檢視可分開演進；一次全改的風險不成比例，否決，讀取側先行。

## Consequences

- 每個鍵的預設值與來源從此有一處 locality：改預設、查漂移只看檢視層；新 handler 不需要再從各平台 body 反推 config 形狀。
- 檢視是純函式，成為全專案最好測的一層——補上設定層原本零測試的洞（defaults/migration 仍不在本候選範圍）。
- 缺 `date_auto_select` 節時 `date_view` 帶預設回傳而非拋例外（kktix/kham 原為直接索引）：實務上 `get_config_dict` + migration 保證節存在，此邊緣只出現在手寫 dict；行為差異已於試點 PR 明文標記。
- 上游同步摩擦：4 個已遷移平台的日期 handler 組包段改形；`config_views.py` 是 fork 新增檔案，無上游衝突面。
