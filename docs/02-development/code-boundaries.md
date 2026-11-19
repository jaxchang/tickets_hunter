**文件說明**：Tickets Hunter 的程式碼邊界規範，定義模組職責、依賴方向、命名慣例與禁止模式。提交 PR 前請先閱讀。

**最後更新**：2026-09-16

---

# 程式碼邊界

> 適用於所有新程式碼。歷史遺留程式碼可能早於本規則，看到不代表可以仿效。

---

## 1. 模組職責

- **`nodriver_tixcraft.py`** — 主迴圈與路由：URL 分派、瀏覽器啟動、設定熱重載。平台邏輯已全數拆至 `platforms/`。禁止：通用 DOM 工具（屬 `nodriver_common.py`）、設定 CRUD（屬 `settings.py`）。
- **`nodriver_common.py`** — 共用瀏覽器基礎設施：DOM 操作、暫停機制、Cloudflare、瀏覽器初始化、OCR 工廠、通知。禁止：平台業務邏輯、import `platforms/*` 或 `nodriver_tixcraft`。
- **`platforms/*.py`** — 各平台 12 階段流程，一平台一檔（家族平台同檔，如 tixcraft + ticketmaster）。狀態用模組層級 `_state`（已遷移模組以 `PageState` 宣告鍵的生命週期，見 ADR 0002）；平台常數定義在各自模組。禁止：跨平台 import、import `nodriver_tixcraft`。
- **`util.py`** — 純跨平台工具：文字比對、關鍵字解析、字串正規化、檔案 I/O、DebugLogger、CAPTCHA 數學猜測。禁止：平台選擇器／URL、業務邏輯、import `settings` 或 `nodriver_tixcraft`。此檔為所有平台共用，修改前務必評估跨平台影響。
- **`date_selection.py`** — 日期選擇共用核心（ADR 0003）：純函式 `select_date(rows, config_view)`，只處理純資料列（文字、available、payload），不碰 DOM。禁止：平台選擇器／URL、import `platforms/*` 或 `nodriver_common`。僅允許 `import util`。
- **`config_views.py`** — 設定檢視層（ADR 0004）：每共用管線階段一個純函式檢視（如 `date_view(config_dict)`），對當前 dict 現算、禁止快照/快取。禁止：import 任何專案模組（葉節點）、保存設定狀態。
- **`settings.py`** — 設定 CRUD 與 Tornado Web Server。禁止：搶票邏輯、import `nodriver_tixcraft`。僅允許 `import util`。

---

## 2. 依賴方向（嚴格 DAG）

```
nodriver_tixcraft  -->  nodriver_common, platforms/*, util, settings
platforms/*        -->  nodriver_common, util, date_selection, config_views
date_selection     -->  util
config_views       -->  (無專案內部匯入)
nodriver_common    -->  util, settings
settings           -->  util
util               -->  (無專案內部匯入)
```

禁止反向依賴。`platforms/*` 之間禁止互相 import。

---

## 3. 命名慣例

**平台動作函式**（`platforms/*.py`）：`nodriver_{platform}_{action}`

- 簽章以 `tab` 為首，接 `config_dict`，再接可選參數
- 除非純函式，否則一律 async

**共用 DOM 工具**（`nodriver_common.py`）：`nodriver_{action}`（無平台字首）

- 暫停機制：`check_and_handle_pause`、`sleep_with_pause_check` 等
- 瀏覽器初始化：`get_nodriver_browser_args`、`get_extension_config`

**共用工具函式**（`util.py`）：簡單動詞-名詞，無 `nodriver_` 字首

- 謂詞：`is_` / `has_` 開頭，回傳 `bool`
- 工廠：`create_` / `get_` 開頭

**常數**：`CONST_{CATEGORY}_{NAME}`，全大寫。業務邏輯禁止魔法字串。

---

## 4. 函式大小限制（僅新程式碼）

| 位置 | 建議 | 上限 |
|------|------|------|
| `util.py` 工具函式 | <= 30 行 | 50 行 |
| `platforms/*.py` 平台動作 | <= 50 行 | 100 行 |
| `nodriver_common.py` 共用工具 | <= 40 行 | 60 行 |
| `settings.py` 處理器方法 | <= 40 行 | 60 行 |

超過時提取子步驟為具名輔助函式，優先縱向分解（每階段一個函式）。

---

## 5. 平台模組狀態管理

各平台使用模組層級 `_state` 管理跨迴圈狀態。未遷移的模組使用純 dict：

```python
# platforms/xxx.py
_state = {}

async def nodriver_xxx_main(tab, url, config_dict):
    if "key" not in _state:
        _state.update({
            "key": default_value,
        })
```

已遷移的模組（tixcraft、TicketPlus、kktix、funone、ibon、cityline、fansigo、kham、hkticketing、famiticket）改用 `nodriver_common.PageState`（dict 相容）：
在模組頂部以 `declare_page` / `declare_session` 集中宣告每個鍵的生命週期，
主函式每輪以 `set_page(page_id)` 宣告目前頁面，換頁時頁面鍵自動清除（見 ADR 0002）。

禁止模式：

- `global xxx_dict; if not 'xxx_dict' in globals(): ...`（不可跨模組）
- 散落的 `global some_bool`（難追蹤、不可測試）
- 主迴圈預初始化平台 dict（平台自己管理自己的狀態）

---

## 6. 禁止的跨模組模式

- `util.py` 中加入平台特定邏輯（選擇器、URL）
- 業務邏輯中寫死設定值（必須從 `config_dict` 讀取）
- 可選欄位用 `config_dict["key"]` 而非 `.get()`（會 KeyError）
- 使用 `print()` 輸出除錯訊息（必須用 `DebugLogger`）
- `settings.py` 匯入 `nodriver_tixcraft.py`（循環依賴）
- `platforms/*.py` 匯入 `nodriver_tixcraft`（循環依賴）
- `platforms/*.py` 之間互相匯入（跨平台依賴）
- `nodriver_common.py` 匯入 `platforms/*`（反向依賴）

---

## 7. 新增設定欄位流程

1. `settings.py` 的 `get_default_config()` 加入欄位 + 安全預設值
2. 若取代舊欄位，在 `migrate_config()` 加遷移邏輯
3. 業務邏輯透過 `config_dict.get(...)` 讀取
4. 在面向使用者的訊息中使用設定項的 UI 名稱，不用內部鍵名

---

## 8. 12 階段函式字尾

主迴圈依函式名稱路由，新增平台時務必沿用下列字尾：

| 階段 | 字尾 |
|------|------|
| 2. 身分驗證 | `_signin` / `_login` |
| 3. 頁面監控 | `_main` |
| 4. 日期選擇 | `_date_auto_select` |
| 5. 區域選擇 | `_area_auto_select` |
| 6. 票數設定 | `_assign_ticket_number` |
| 7. CAPTCHA | `_verify` / `_captcha` |
| 8. 表單填寫 | `_auto_fill` / `_keyin` |
| 9. 條款同意 | `_agree` |
| 10. 訂單送出 | `_confirm_order` |
| 11. 排隊／付款 | `_check_queue_status` / `_booking` |

各階段的詳細機制見 `docs/03-mechanisms/`。

---

## 9. 注入腳本與程式碼安全

**注入腳本**：

- 所有 `tab.evaluate()` 注入的 JS 必須用 IIFE 包裝，不洩漏全域變數
- 不得對 `window.*` 指派或覆寫原生瀏覽器函式；注入腳本用 `const` / `let`，禁止 `var`
- JS 字串插值必須用 `json.dumps()` 傳遞資料，不可直接串接
- Promise 主體同樣要包成 IIFE（`(() => { ... })()`）。裸的箭頭函式運算式只會回傳函式物件本身，Promise 主體永遠不會執行，等待會靜默失效

**程式碼安全**：

- 禁止 `shell=True` 搭配使用者衍生字串；禁止對 `settings.json`、網路回應、DOM 資料動態求值
- `CLOUDFLARE_ENABLE_EXPERT_MODE` 預設 `False` — `--no-sandbox` 與 `--disable-web-security` 僅限受控環境
- 網路封鎖清單不得封鎖 HTTPS 憑證驗證端點或平台付款腳本
- `debug.log()` 禁止記錄密碼、Cookie、webhook URL
- 新 `import` 必須可追溯至 `requirement.txt`；版本精確固定，禁止 `>=`、`~=`、`*`

---

## 10. 新增平台模組流程

1. 建立 `src/platforms/{platform}.py`，函式命名 `nodriver_{platform}_{action}`，狀態用模組層級 `_state`（比照既有模組慣例；新模組可直接採 `PageState` 宣告生命週期）；主函式採平台介面契約（ADR 0005）：簽名 `nodriver_{platform}_main(ctx)`、回傳新 tab 或 None，並於模組內宣告 `PLATFORM_ENTRIES`（網址 patterns、可選 match 函式、handler）
2. 主檔新增 `from platforms.{platform} import *`，並將該模組的 `PLATFORM_ENTRIES` 以別名 import 加入 `PLATFORM_REGISTRY`（順序即派發順序）
3. `build_scripts/nodriver_tixcraft.spec` 的 `hiddenimports` 新增 `'platforms.{platform}'`
4. 驗證（`py_compile`、import 鏈、煙霧測試）後更新 `docs/02-development/structure.md` 函式索引

實作範本見 `docs/04-implementation/platform-examples/tixcraft-reference.md`，
12 階段規格見 `docs/02-development/ticket_automation_standard.md`。
