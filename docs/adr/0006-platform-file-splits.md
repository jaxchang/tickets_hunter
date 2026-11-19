# 多家族平台檔案以兄弟模組拆分，façade 維持公開表面

架構健檢（2026-11-19）候選 #7 指出兩個平台檔案各自裝了多個售票家族：`platforms/kham.py` 6,429 行含寬宏 core、三套 seat 流程（kham/udn/ticket）與中職 UTK RWD 家族；`platforms/tixcraft.py` 3,634 行含 TixCraft 與 Ticketmaster 兩家族（按階段交錯）與共用 OCR 叢集。其餘大檔經盤點不是多家族：hkticketing 是兩代 Softix 共用同一 main 與尾段、ibon 是單家族加兩個尾端小衛星、kktix 與 ticketplus 是單家族——本候選只拆 kham 與 tixcraft，其餘不拆的理由即在此。

我們決定：家族叢集搬到同層的兄弟模組（kham 為 `kham_shared`/`kham_rwd`/`kham_seat`/`kham_udn_seat`/`kham_ticket_seat`，tixcraft 為 `tixcraft_shared`/`ticketmaster`），原檔案保留為 façade：core、main 與 `PLATFORM_ENTRIES` 留在原檔案，並 import 回搬出的名稱，使其 `__all__` 公開表面全數仍可解析——測試與 dispatcher 零改動。每個被拆檔案配一個共用兄弟模組，持有相依封閉的共用集合：唯一的 `_state` 物件（所有家族模組 import 同一個物件，狀態身分不變）、跨叢集共用函式（如 kham 的 captcha 叢集、UTK tenant 查找）與共用常數。分層方向固定為 façade → 叢集 → 共用層，叢集模組不得反向 import façade（避免循環 import）。搬移紀律：逐字搬移（函式 body 位元組不變，僅增減 import 行）、一個叢集一個 issue/PR、表面測試釘住 `__all__` 解析與 `_state` 同一性；build spec 的 hiddenimports 與 structure.md 隨每次搬移同步。kham 拆完後 core 仍約 2,900 行，接受為單家族剩餘，core 再按階段拆分是刻意不做的非目標。

## Considered Options

- **改成 package（platforms/kham/ 目錄）**：import 路徑與打包設定全面變動，而收益與兄弟模組相同，否決。
- **seat 主函式升格為獨立平台模組並自帶 entries**：seat 流程不是 dispatcher 的進入點（只由 kham main 依頁型呼叫），升格會捏造不存在的派發關係，否決。
- **每個家族各持一份 _state**：`_state` 的 key 雖按叢集分區，但 main 的種子寫入與跨叢集讀取（如登入狀態）依賴同一物件；拆成多份會改變狀態語意，否決——共用層持有唯一物件、身分以測試釘住。
- **搬移時順手整理函式內容**：逐字搬移讓每批的行為保真可由 diff 機械驗證；任何內容整理另案進行，否決混入。

## Consequences

- kham 家族的導覽成本下降：RWD 家族自成一檔、共用層明確列出跨家族相依；新增家族叢集有固定形狀可循。
- kham.py 的 `CONST_KHAM_RELOAD_SETTLE` 目前由 core 與 kham seat 共用、暫留 façade；seat 批次時移入 kham_shared（屆時 seat 模組才不需碰 façade）。
- 上游同步摩擦：kham.py 大幅縮減且新增兄弟檔案；上游若改動 kham 的被搬移區段，同步時需人工對照新檔案位置。
