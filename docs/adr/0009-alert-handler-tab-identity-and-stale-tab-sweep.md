# 全域 alert handler 註冊綁分頁身分，主迴圈清掃高齡非工作分頁

zendriver 的 CDP handler 活在 tab 的連線（connection）上，但 tixcraft、ibon、kktix 三平台「只註冊一次」的守衛是模組 `_state` 裡的程序級布林旗標（`alert_handler_registered`），且永不重置。工作分頁在同一程序內被替換時（具體觸發點：cityline 的 `nodriver_cityline_close_second_tab` 關掉原分頁、主迴圈改用新分頁），新連線沒有 handler、舊旗標卻擋住重註冊，全域 alert 處理整場默默失效（CLAUDE.md 記載的陷阱）。同時主迴圈只用一個工作分頁，handler 換回新分頁時舊分頁不關閉，被棄置的分頁（彈窗、導向殘留）在長跑中累積，沒有回收機制。

我們決定兩件事（#94）。其一，註冊改綁分頁身分：三平台各有一個 `register_*_alert_handler(tab, config_dict)`（ibon 原有此形狀，tixcraft/kktix 比照抽出），`_state` 新 key `alert_handler_tab` 記住持有註冊的分頁物件，分頁不同即重新註冊（closure 隨新分頁重建——它捕捉的本就是分頁本身）；舊旗標 key、宣告與預設全數移除。其二，主迴圈清掃：`nodriver_common.close_stale_tabs` 以呼叫端維護的 first-seen 帳本記錄非工作分頁首次被看見的 pass 數，主迴圈每 pass 於 dispatch 後呼叫，非工作分頁超過 `STALE_TAB_MAX_AGE_PASSES`（600，約 30 秒）即關閉；工作分頁永不關閉也不記帳，已消失的分頁自動除帳。`driver.tabs` 是本地屬性、無 CDP 往返，每 pass 呼叫成本可忽略。瀏覽器重啟與連線重連不在本決策範圍。

## Considered Options

- **只在 cityline 換頁處重置布林旗標**：只補已知觸發點；其他任何換頁路徑（既有或未來）重演同一個靜默失效，否決。
- **記住「所有註冊過的分頁」集合、永不重註冊**：回到先前分頁時不重複註冊，但集合會持有已關閉分頁的參照而無界成長——正是本批要消除的長跑累積型問題，否決。
- **偵測連線死亡時重啟瀏覽器**：影響面大（登入態、佇列位置全失），且與本陷阱的最小修法不成比例；列為範圍外，未來若需要另開議題。

## Consequences

- 工作分頁 A→B→A 回頭時，A 上會再註冊一個 handler（zendriver 只增不去重，同 alert 可能被處理兩次）。接受此取捨：現行 dispatch 不會回到被棄置的分頁（cityline 換頁即關原分頁），且清掃會在約 600 pass 內關掉被棄置者；真要支援回頭分頁，應改為「註冊過的分頁集合＋清掃同步除名」的設計。
- 新平台或新全域 handler 必須比照 `register_*` 形狀綁分頁身分，不得再引入程序級布林註冊旗標（CLAUDE.md 陷阱條目已同步更新）。
- 清掃的年齡以「首次被清掃看見」起算而非分頁開啟時刻：剛被替換下來的舊工作分頁有一段完整寬限期，不會在換頁當下被誤關。
