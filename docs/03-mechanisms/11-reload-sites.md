# 刷新站點地圖

各平台「重新載入頁面」的站點清單與分類。分類的定義（輪詢刷新／復原刷新／刷新冷卻）
見 repo 根目錄的 `GLOSSARY.md`；改動任何刷新行為前先查本頁，不要把兩類一起改。

## 分類依據

- **輪詢刷新**：刷新前會以 `_is_page_reload_enabled` 檢查「自動刷新頁面間隔」與
  coming-soon 開關（該函式的 docstring 即以此區分，源自 #396）。目的是等票出現，
  定速方式不得阻塞主迴圈。
- **復原刷新**：不做上述檢查。目的是從異常狀態（張數設不上、重試超限）脫困，
  維持原本的等待與刷新寫法，不在 #3 的遷移範圍。

## 輪詢刷新（#3 起改用非阻塞刷新冷卻）

冷卻決策由 `util.advance_reload_cooldown` 統一計算，截止時間存放在各模組的
`_state`。拓元與 TicketPlus 已改用頁面狀態宣告清除（#7，ADR 0002）：冷卻鍵在
模組頂部以 `declare_page` 宣告所屬頁面，主函式每輪 `set_page` 換頁時自動清除，
不再手寫清除邏輯。

| 平台 | 檔案 | 函式 | `_state` 冷卻鍵 | 備註 |
| --- | --- | --- | --- | --- |
| 拓元 | `src/platforms/tixcraft.py` | `nodriver_tixcraft_date_auto_select` | `date_reload_cooldown_until` | |
| 拓元 | `src/platforms/tixcraft.py` | `nodriver_tixcraft_area_auto_select` | `area_reload_cooldown_until` | |
| Ticketmaster | `src/platforms/tixcraft.py` | `nodriver_ticketmaster_date_auto_select` | `ticketmaster_date_reload_cooldown_until` | 未定速的連續載入會觸發 EPS 封鎖（#378） |
| Ticketmaster | `src/platforms/tixcraft.py` | `nodriver_ticketmaster_area_auto_select` | `ticketmaster_area_reload_cooldown_until` | |
| TicketPlus | `src/platforms/ticketplus.py` | `nodriver_ticketplus_date_auto_select` | `date_reload_cooldown_until` | 間隔 ≤ 0 時以 1 秒定速（歷史行為） |
| TicketPlus | `src/platforms/ticketplus.py` | `nodriver_ticketplus_order`（`[AUTO RELOAD]`） | `order_reload_cooldown_until` | 間隔 = 0 時每輪刷新；< 0 時不刷新 |

間隔 = 0 在拓元／Ticketmaster 站點代表停用輪詢刷新（由 `_is_page_reload_enabled`
擋下），主迴圈仍持續監控頁面。

## 復原刷新（不在 #3 範圍，維持原狀）

| 平台 | 檔案 | 位置 | 觸發原因 |
| --- | --- | --- | --- |
| 拓元 | `src/platforms/tixcraft.py` | `nodriver_tixcraft_ticket_main` | 張數無法設定時刷新重試（#174） |
| Ticketmaster | `src/platforms/tixcraft.py` | `nodriver_tixcraft_main` | 區域重試超過 30 次後刷新（#378） |

## 其他平台

其餘平台的輪詢刷新仍是在 handler 內等待間隔的舊寫法，尚未遷移；
需要比照 #3 處理時逐平台進行（上游追蹤議題 bouob/tickets_hunter#376）。
