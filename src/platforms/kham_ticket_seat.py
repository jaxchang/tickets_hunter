#encoding=utf-8
# =============================================================================
# KHAM Ticket Seat Module
# The ticket.com.tw seat selection flow and sign-in (issue #46,
# ADR 0006), split out of platforms/kham.py. Imports only the shared
# layer (platforms.kham_shared) and the common layers.
# =============================================================================

import json
import re
import traceback
from zendriver import cdp
import util
import config_views
from nodriver_common import (
    check_and_handle_pause,
    play_sound_while_ordering,
)
from platforms.kham_shared import (
    nodriver_kham_captcha,
)


async def nodriver_ticket_login(tab, account, password, config_dict):
    """
    年代售票登入

    [TESTED] 已完整測試 - T004 暫停機制補完
    [TESTED] 已完整測試 - 登入邏輯驗證

    URL: https://ticket.com.tw/application/utk13/utk1306_.aspx
    """
    # 函數開始檢查暫停 [T004修正]
    if await check_and_handle_pause(config_dict):
        return False

    ret = False
    debug = util.create_debug_logger(config_dict)

    # Find email/account input - Use ID selector
    el_email = None
    try:
        el_email = await tab.query_selector('#ctl00_ContentPlaceHolder1_M_ACCOUNT')
    except Exception as exc:
        debug.log("Find account input fail:", exc)

    # Input account
    is_email_sent = False
    if el_email:
        try:
            inputed_text = await tab.evaluate('document.querySelector("#ctl00_ContentPlaceHolder1_M_ACCOUNT").value')
            if not inputed_text or len(inputed_text) == 0:
                await el_email.send_keys(account)
                is_email_sent = True
            else:
                if inputed_text == account:
                    is_email_sent = True
        except Exception as exc:
            debug.log("Input account fail:", exc)

    # Find password input - Use ID selector
    el_pass = None
    if is_email_sent:
        try:
            el_pass = await tab.query_selector('#ctl00_ContentPlaceHolder1_M_PASSWORD')
        except Exception as exc:
            debug.log("Find password input fail:", exc)

    # Input password
    is_password_sent = False
    if el_pass:
        try:
            inputed_text = await tab.evaluate('document.querySelector("#ctl00_ContentPlaceHolder1_M_PASSWORD").value')
            if not inputed_text or len(inputed_text) == 0:
                await el_pass.click()
                if len(password) > 0:
                    await el_pass.send_keys(password)
                    is_password_sent = True
                await tab.sleep(0.1)
        except Exception as exc:
            debug.log("Input password fail:", exc)

    # Click login button - Use ID selector
    if is_password_sent:
        try:
            el_btn = await tab.query_selector('#ctl00_ContentPlaceHolder1_LOGIN_BTN')
            if el_btn:
                await el_btn.click()
                ret = True
                debug.log("[TICKET LOGIN] Login button clicked")
        except Exception as exc:
            debug.log("Click login button fail:", exc)

    return ret


async def nodriver_ticket_seat_type_auto_select(tab, config_dict, area_keyword_item):
    """
    年代售票 - 自動選擇票別 (UTK0205 座位選擇頁面)
    選擇票價類型，如原價、身障、陪同者等

    [TESTED] 已完整測試 - CDP DOMSnapshot 穿透驗證
    [TESTED] 已完整測試 - 排除關鍵字支援 (FR-022)
    [TESTED] 已完整測試 - 三層關鍵字匹配邏輯

    使用 CDP DOMSnapshot 穿透 DOM 結構，避免 JavaScript 載入時機問題
    """
    # 函數開始檢查暫停 [T004修正]
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)
    is_seat_type_assigned = False

    # Clean keyword quotes
    # NOTE: This function only supports single keyword or space-separated AND logic (e.g., "VIP 區")
    # For multiple keywords with OR logic, caller should use JSON parsing and iterate
    if area_keyword_item and len(area_keyword_item) > 0:
        try:
            area_keyword_clean = area_keyword_item.strip()
            if area_keyword_clean.startswith('"') and area_keyword_clean.endswith('"'):
                area_keyword_clean = area_keyword_clean[1:-1]

            # Use the cleaned keyword directly (no comma split to avoid incorrect AND logic)
            area_keyword_item = area_keyword_clean
        except Exception as e:
            debug.log(f"[TICKET SEAT TYPE] Keyword parse error: {e}")

    try:
        from zendriver import cdp

        # Step 1: Capture DOM snapshot
        debug.log("[TICKET SEAT TYPE] Capturing DOM snapshot...")

        documents, strings = await tab.send(cdp.dom_snapshot.capture_snapshot(
            computed_styles=[],
            include_dom_rects=True
        ))

        # Step 2: Search for ticket type buttons
        ticket_buttons = []

        if documents and len(documents) > 0:
            document_snapshot = documents[0]
            nodes = document_snapshot.nodes

            # Extract node information
            node_names = [strings[i] for i in nodes.node_name]
            node_values = [strings[i] if i >= 0 else '' for i in nodes.node_value]
            attributes_list = nodes.attributes
            backend_node_ids = list(nodes.backend_node_id)

            debug.log(f"[TICKET SEAT TYPE] Total nodes in snapshot: {len(node_names)}")

            # Step 3: Search for buttons with setType onclick
            for i, node_name in enumerate(node_names):
                if node_name.upper() == 'BUTTON':
                    # Parse attributes
                    attrs = {}
                    if i < len(attributes_list):
                        attr_indices = attributes_list[i]
                        for j in range(0, len(attr_indices), 2):
                            if j + 1 < len(attr_indices):
                                key = strings[attr_indices[j]]
                                val = strings[attr_indices[j + 1]]
                                attrs[key] = val

                    # Check if this is a ticket type button (onclick contains setType)
                    onclick = attrs.get('onclick', '')
                    button_class = attrs.get('class', '')
                    button_disabled = 'disabled' in attrs

                    if 'setType' in onclick:
                        # Get button text from child text nodes
                        # Strategy 1: Search for first non-empty text node in children
                        button_text = ''
                        for j in range(i + 1, min(i + 10, len(node_names))):  # Check next 10 nodes
                            if node_names[j] == '#text':
                                text_content = node_values[j].strip()
                                if text_content and len(text_content) > 0:
                                    button_text = text_content
                                    debug.log(f"[TICKET SEAT TYPE] Extracted text from node {j}: '{button_text}'")
                                    break
                            # Stop when encountering another element (DIV, BUTTON, etc.)
                            elif node_names[j] in ['DIV', 'BUTTON', 'INPUT', 'SPAN', 'A']:
                                break

                        # Strategy 2: Fallback - Extract from onclick attribute if text is empty
                        if not button_text and 'setType' in onclick:
                            import re
                            match = re.search(r"setType\('[^']*','([^']*)'\)", onclick)
                            if match:
                                button_text = match.group(1)
                                debug.log(f"[TICKET SEAT TYPE] Extracted text from onclick: '{button_text}'")

                        ticket_buttons.append({
                            'backend_node_id': backend_node_ids[i],
                            'class': button_class,
                            'onclick': onclick,
                            'disabled': button_disabled,
                            'text': button_text,
                            'index': i
                        })

                        debug.log(f"[TICKET SEAT TYPE] Found button #{len(ticket_buttons)}: text='{button_text}', disabled={button_disabled}")

        debug.log(f"[TICKET SEAT TYPE] Found {len(ticket_buttons)} ticket type button(s)")

        if len(ticket_buttons) == 0:
            debug.log("[TICKET SEAT TYPE] No ticket type buttons found")
            return False

        # Step 4: Filter disabled buttons, then apply keyword_exclude here so that
        # keyword matching and the first-button fallback below share one clean
        # candidate list -- an excluded seat type can never be auto-picked.
        #
        # Guard the empty text: is_row_match_keyword reports a match for empty
        # text, so a button whose label could not be extracted would otherwise
        # count as excluded and drop out of the first-button fallback too.
        enabled_buttons = []
        for btn in ticket_buttons:
            if btn['disabled']:
                continue
            button_text = btn['text']
            if button_text and util.reset_row_text_if_match_keyword_exclude(config_dict, button_text):
                debug.log(f"[TICKET SEAT TYPE] Excluded by keyword_exclude: {button_text}")
                continue
            enabled_buttons.append(btn)

        debug.log(f"[TICKET SEAT TYPE] Found {len(enabled_buttons)} enabled button(s)")

        if len(enabled_buttons) == 0:
            debug.log("[TICKET SEAT TYPE] All buttons are disabled or excluded")
            return False

        # Step 5: Match button using keyword logic
        matched_button = None

        for button in enabled_buttons:
            button_text = button['text']
            if not button_text:
                continue

            # Keyword matching logic
            is_match = True
            if area_keyword_item and len(area_keyword_item) > 0:
                keywords = area_keyword_item.split(' ')
                row_text = util.format_keyword_string(button_text)
                for kw in keywords:
                    formatted_kw = util.format_keyword_string(kw)
                    if formatted_kw not in row_text:
                        is_match = False
                        break

            if is_match:
                matched_button = button
                debug.log(f"[TICKET SEAT TYPE] Matched: {button_text}")
                break

        # If no keyword match, select first enabled button as fallback
        if matched_button is None:
            matched_button = enabled_buttons[0]
            debug.log(f"[TICKET SEAT TYPE] No keyword match, using first button: {matched_button['text']}")

        # Step 6: Click button using CDP
        try:
            # Initialize DOM
            await tab.send(cdp.dom.get_document())

            # Convert backend_node_id to node_id
            result = await tab.send(cdp.dom.push_nodes_by_backend_ids_to_frontend([matched_button['backend_node_id']]))
            node_id = result[0]

            debug.log(f"[TICKET SEAT TYPE] Button node_id: {node_id}")

            # Scroll element into view
            await tab.send(cdp.dom.scroll_into_view_if_needed(node_id=node_id))
            await tab.sleep(0.2)

            # Use CDP Runtime.callFunctionOn to click button
            from zendriver.cdp import runtime

            # Resolve node to RemoteObject
            resolved = await tab.send(cdp.dom.resolve_node(node_id=node_id))

            # Get object_id from resolved node
            if hasattr(resolved, 'object'):
                remote_object_id = resolved.object.object_id
            elif hasattr(resolved, 'object_id'):
                remote_object_id = resolved.object_id
            else:
                debug.log(f"[TICKET SEAT TYPE] Error: Could not find object_id in resolved node")
                return False

            debug.log(f"[TICKET SEAT TYPE] Resolved button object_id: {remote_object_id}")

            # Call click() on the remote object
            click_result = await tab.send(runtime.call_function_on(
                function_declaration='function() { this.click(); return true; }',
                object_id=remote_object_id,
                return_by_value=True
            ))

            debug.log(f"[TICKET SEAT TYPE] Button clicked, result: {click_result}")

            if click_result:
                is_seat_type_assigned = True
                debug.log(f"[TICKET SEAT TYPE] Successfully clicked: {matched_button['text']}")

                # Wait for seat table to load (initial wait for AJAX to start)
                await tab.sleep(1.5)

                # [Optimized] Smart wait using lightweight querySelector instead of DOM Snapshot
                seats_loaded = False
                for i in range(20):  # Max 10 seconds (20 * 0.5s)
                    try:
                        # Use querySelector - 10x faster than DOM Snapshot
                        check_result = await tab.evaluate('''
                            (() => {
                                const table = document.querySelector('#TBL');
                                // Check for TD with title attribute and cursor:pointer style
                                const seats = document.querySelectorAll('#TBL td[title][style*="cursor: pointer"]');
                                return {
                                    tableFound: !!table,
                                    seatCount: seats.length
                                };
                            })()
                        ''')

                        # Parse result using util function for nodriver CDP format
                        table_found = False
                        seat_count = 0
                        parsed_result = util.parse_nodriver_result(check_result)
                        if isinstance(parsed_result, dict):
                            table_found = parsed_result.get('tableFound', False)
                            seat_count = parsed_result.get('seatCount', 0)

                        # Success condition: table exists AND at least 1 seat found
                        if table_found and seat_count > 0:
                            seats_loaded = True
                            debug.log(f"[TICKET SEAT TYPE] Seats loaded: table found, {seat_count} available seats")
                            break
                        elif i == 19:
                            # Last attempt - show what's missing
                            debug.log(f"[TICKET SEAT TYPE] Warning: table_found={table_found}, seat_count={seat_count}")

                    except Exception as wait_exc:
                        if i == 19:
                            debug.log(f"[TICKET SEAT TYPE] querySelector error: {wait_exc}")

                    await tab.sleep(0.5)

                if not seats_loaded:
                    debug.log("[TICKET SEAT TYPE] Warning: Seats not fully loaded within 10 seconds")
            else:
                debug.log("[TICKET SEAT TYPE] Click failed")

        except Exception as click_exc:
            if debug.enabled:
                debug.log(f"[ERROR] CDP click error: {click_exc}")
                import traceback
                traceback.print_exc()

    except Exception as exc:
        debug.log(f"[ERROR] Ticket seat type selection error: {exc}")
        import traceback
        if debug.enabled:
            traceback.print_exc()

    debug.log(f"[TICKET SEAT TYPE] Assignment result: {is_seat_type_assigned}")

    return is_seat_type_assigned


async def _analyze_seat_quality(tab, config_dict):
    """
    偵測舞台方向並收集可用座位
    職責: 讀取舞台方向圖示、列出可選座位的 title（評分與排序由 _find_best_seats_in_row 負責）

    Returns: dict {'direction': 'up'|'down'|'left'|'right',
                   'availableSeats': 可用座位 title 列表,
                   'totalSeats': 可用座位數}；JS 結果非 dict 時回傳 {}
    """
    ticket_number = config_views.ticket_number(config_dict)
    allow_non_adjacent = config_dict["advanced"]["disable_adjacent_seat"]
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    import json
    # 執行 JavaScript 分析座位品質
    result = await tab.evaluate(f'''
        (function() {{
            const ticketNumber = {ticket_number};
            const allowNonAdjacent = {json.dumps(allow_non_adjacent)};
            const showDebug = {json.dumps(show_debug)};
            const MIDDLE_AREA_MIN = 8;
            const MIDDLE_AREA_MAX = 18;

            // 偵測舞台方向
            const stageIcon = document.querySelector('#ctl00_ContentPlaceHolder1_lbStageArrow i');
            let stageDirection = 'up';

            if (showDebug) {{
                console.log('[TICKET SEAT] Stage icon element:', stageIcon ? 'found' : 'not found');
            }}

            if (stageIcon) {{
                if (stageIcon.classList.contains('fa-arrow-circle-up')) stageDirection = 'up';
                else if (stageIcon.classList.contains('fa-arrow-circle-down')) stageDirection = 'down';
                else if (stageIcon.classList.contains('fa-arrow-circle-left')) stageDirection = 'left';
                else if (stageIcon.classList.contains('fa-arrow-circle-right')) stageDirection = 'right';

                if (showDebug) {{
                    console.log('[TICKET SEAT] Detected stage direction:', stageDirection);
                }}
            }} else {{
                if (showDebug) {{
                    console.log('[TICKET SEAT] Stage icon not found, using default: up');
                }}
            }}

            // 【修復 1】取得所有可用座位 - 改用 JavaScript 過濾而不依賴 CSS 屬性選擇器
            const availableSeats = [];
            const allTableRows = document.querySelectorAll('table#TBL tbody tr, #locationChoice table tbody tr');

            Array.from(allTableRows).forEach(tr => {{
                const allTds = tr.children;
                for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                    const seat = allTds[colIndex];
                    const style = seat.getAttribute('style');
                    const title = seat.getAttribute('title');

                    // 使用 JavaScript 檢查（更可靠）
                    if (style && title &&
                        style.includes('cursor: pointer') &&
                        style.includes('icon_chair_empty')) {{
                        availableSeats.push(seat);
                    }}
                }}
            }});

            if (showDebug) {{
                console.log('[TICKET SEAT] Stage: ' + stageDirection + ', Available: ' + availableSeats.length);
            }}

            return {{
                direction: stageDirection,
                availableSeats: availableSeats.map(s => s.getAttribute('title')),
                totalSeats: availableSeats.length
            }};
        }})();
    ''')

    # 轉換 CDP 格式 using util function
    result_dict = util.parse_nodriver_result(result) if not isinstance(result, dict) else result
    return result_dict if isinstance(result_dict, dict) else {}


async def _find_best_seats_in_row(tab, seat_analysis, config_dict):
    """
    在候選排/列中尋找最佳座位組合
    職責: 分組座位、分析品質、排優先度排序、搜尋最佳組合

    Args: seat_analysis 來自 _analyze_seat_quality 的結果
    Returns: dict 包含選定的座位集合
    """
    ticket_number = config_views.ticket_number(config_dict)
    allow_non_adjacent = config_dict["advanced"]["disable_adjacent_seat"]
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    stage_direction = seat_analysis.get('direction', 'up')
    available_titles = seat_analysis.get('availableSeats', [])

    debug.log(f"[TICKET SEAT] Finding best seats: tickets={ticket_number}, "
          f"stage={stage_direction}, adjacent={not allow_non_adjacent}, "
          f"available={len(available_titles)}")

    import json
    # 執行 JavaScript 尋找最佳座位
    result = await tab.evaluate(f'''
        (function() {{
            const ticketNumber = {ticket_number};
            const allowNonAdjacent = {json.dumps(allow_non_adjacent)};
            const showDebug = {json.dumps(show_debug)};
            const stageDirection = {json.dumps(stage_direction)};
            const MIDDLE_AREA_MIN = 8;
            const MIDDLE_AREA_MAX = 18;

            if (showDebug) {{
                console.log('[TICKET SEAT] Starting seat selection algorithm...');
                console.log('[TICKET SEAT] DOM position tracking enabled for aisle detection');
            }}

            // 取得所有可用座位 - 使用與 _analyze_seat_quality 一致的選擇器
            const availableSeats = [];
            const allTableRowsCheck = document.querySelectorAll('table#TBL tbody tr, #locationChoice table tbody tr');
            Array.from(allTableRowsCheck).forEach(tr => {{
                const allTds = tr.children;
                for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                    const seat = allTds[colIndex];
                    const style = seat.getAttribute('style');
                    const title = seat.getAttribute('title');
                    if (style && title && style.includes('cursor: pointer') && style.includes('icon_chair_empty')) {{
                        availableSeats.push(seat);
                    }}
                }}
            }});

            if (showDebug) {{
                console.log('[TICKET SEAT] Available seats found: ' + availableSeats.length);
            }}

            if (availableSeats.length < ticketNumber) {{
                return {{ success: false, found: availableSeats.length, selected: 0, reason: 'not_enough_seats' }};
            }}

            // 根據舞台方向分組及分析品質
            const selectedSeats = [];
            if (stageDirection === 'up' || stageDirection === 'down') {{
                // 按排分組（加入 DOM 位置追蹤）
                const rows = {{}};
                const allTableRows = document.querySelectorAll('table#TBL tbody tr, #locationChoice table tbody tr');

                Array.from(allTableRows).forEach((tr, rowIndexInTable) => {{
                    const allTds = tr.children;
                    for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                        const seat = allTds[colIndex];
                        const style = seat.getAttribute('style');
                        const title = seat.getAttribute('title');

                        // 檢查是否為可選座位
                        if (style && title &&
                            style.includes('cursor: pointer') &&
                            style.includes('icon_chair_empty')) {{

                            if (title.includes('排') && title.includes('號')) {{
                                const parts = title.split('-');
                                if (parts.length >= 3) {{
                                    const rowNum = parseInt(parts[1].replace('排', ''));
                                    const seatNum = parseInt(parts[2].replace('號', ''));
                                    if (!rows[rowNum]) rows[rowNum] = [];

                                    // 【改進 1】記錄真實 DOM 位置
                                    rows[rowNum].push({{
                                        elem: seat,
                                        num: seatNum,
                                        title: title,
                                        domIndex: colIndex  // 新增：true 列位置用於走道偵測
                                    }});
                                }}
                            }}
                        }}
                    }}
                }});

                if (showDebug) {{
                    console.log('[TICKET SEAT] Rows grouped: ' + Object.keys(rows).length);
                }}

                // 分析品質並排序
                const rowQuality = [];
                for (const [rowNum, rowSeats] of Object.entries(rows)) {{
                    rowSeats.sort((a, b) => a.num - b.num);
                    const totalSeats = rowSeats.length;
                    const middleSeats = rowSeats.filter(s => s.num >= MIDDLE_AREA_MIN && s.num <= MIDDLE_AREA_MAX);
                    const middleCount = middleSeats.length;
                    const middleRatio = totalSeats > 0 ? middleCount / totalSeats : 0;

                    rowQuality.push({{
                        rowNum: parseInt(rowNum),
                        totalSeats: totalSeats,
                        middleCount: middleCount,
                        middleRatio: middleRatio,
                        seats: rowSeats,
                        middleSeats: middleSeats
                    }});
                }}

                // 三層優先度排序
                rowQuality.sort((a, b) => {{
                    const aHasEnough = a.middleCount >= ticketNumber;
                    const bHasEnough = b.middleCount >= ticketNumber;
                    if (aHasEnough && !bHasEnough) return -1;
                    if (!aHasEnough && bHasEnough) return 1;

                    if (Math.abs(a.middleRatio - b.middleRatio) > 0.1) {{
                        return b.middleRatio - a.middleRatio;
                    }}

                    if (stageDirection === 'up') {{
                        return a.rowNum - b.rowNum;
                    }} else {{
                        return b.rowNum - a.rowNum;
                    }}
                }});

                if (showDebug) {{
                    console.log('[TICKET SEAT] RowQuality count: ' + rowQuality.length);
                    if (rowQuality.length > 0) {{
                        console.log('[TICKET SEAT] First row: ' + rowQuality[0].rowNum +
                                   ', seats=' + rowQuality[0].totalSeats +
                                   ', middle=' + rowQuality[0].middleCount);
                    }}
                }}

                // 搜尋最佳座位組合
                for (const row of rowQuality) {{
                    let found = false;

                    if (!allowNonAdjacent) {{
                        // 【改進 2】先按 DOM 位置排序確保順序正確
                        row.middleSeats.sort((a, b) => a.domIndex - b.domIndex);
                        row.seats.sort((a, b) => a.domIndex - b.domIndex);

                        // 先找中間區域的連續座位
                        if (row.middleSeats.length >= ticketNumber) {{
                            for (let i = 0; i <= row.middleSeats.length - ticketNumber; i++) {{
                                let continuous = true;
                                for (let j = 0; j < ticketNumber - 1; j++) {{
                                    // 【改進 2】使用 DOM 位置差（domGap）判斷連續性
                                    const currentDomIdx = row.middleSeats[i+j].domIndex;
                                    const nextDomIdx = row.middleSeats[i+j+1].domIndex;
                                    const domGap = nextDomIdx - currentDomIdx;

                                    if (domGap > 1) {{
                                        // DOM 位置不連續（中間有走道或空格）
                                        continuous = false;
                                        if (showDebug) {{
                                            console.log('[TICKET SEAT] Aisle detected: ' +
                                                       row.middleSeats[i+j].title + ' to ' +
                                                       row.middleSeats[i+j+1].title +
                                                       ' (domGap=' + domGap + ')');
                                        }}
                                        break;
                                    }}
                                }}
                                if (continuous) {{
                                    for (let j = 0; j < ticketNumber; j++) {{
                                        selectedSeats.push(row.middleSeats[i+j]);
                                    }}
                                    found = true;
                                    if (showDebug) {{
                                        console.log('[TICKET SEAT] Selected ' + ticketNumber +
                                                   ' seats from middle area');
                                    }}
                                    break;
                                }}
                            }}
                        }}

                        // 回退到全排搜尋
                        if (!found) {{
                            for (let i = 0; i <= row.seats.length - ticketNumber; i++) {{
                                let continuous = true;
                                for (let j = 0; j < ticketNumber - 1; j++) {{
                                    // 【改進 2】使用 DOM 位置差判斷連續性
                                    const currentDomIdx = row.seats[i+j].domIndex;
                                    const nextDomIdx = row.seats[i+j+1].domIndex;
                                    const domGap = nextDomIdx - currentDomIdx;

                                    if (domGap > 1) {{
                                        continuous = false;
                                        if (showDebug && i === 0) {{
                                            console.log('[TICKET SEAT] Gap in full row: ' +
                                                       row.seats[i+j].title + ' (domGap=' + domGap + ')');
                                        }}
                                        break;
                                    }}
                                }}
                                if (continuous) {{
                                    for (let j = 0; j < ticketNumber; j++) {{
                                        selectedSeats.push(row.seats[i+j]);
                                    }}
                                    found = true;
                                    if (showDebug) {{
                                        console.log('[TICKET SEAT] Selected ' + ticketNumber +
                                                   ' seats from full row');
                                    }}
                                    break;
                                }}
                            }}
                        }}
                    }} else {{
                        // 不連續模式：從中間選起
                        if (row.middleSeats.length >= ticketNumber) {{
                            const startIdx = Math.floor((row.middleSeats.length - ticketNumber) / 2);
                            for (let i = 0; i < ticketNumber; i++) {{
                                selectedSeats.push(row.middleSeats[startIdx + i]);
                            }}
                            found = true;
                        }} else {{
                            const startIdx = Math.max(0, Math.floor((row.totalSeats - ticketNumber) / 2));
                            for (let i = 0; i < Math.min(ticketNumber, row.totalSeats); i++) {{
                                if (startIdx + i < row.seats.length) {{
                                    selectedSeats.push(row.seats[startIdx + i]);
                                }}
                            }}
                            found = true;
                        }}
                    }}

                    if (selectedSeats.length >= ticketNumber) break;
                }}
            }} else {{
                // 【改進 3】按列分組（舞台在左/右）- 加入 DOM 位置追蹤與品質分析
                const columns = {{}};
                const MIDDLE_ROW_MIN = 5;
                const MIDDLE_ROW_MAX = 15;

                // 遍歷表格，按列收集座位並記錄 DOM 垂直位置
                const allTableRows = document.querySelectorAll('table#TBL tbody tr, #locationChoice table tbody tr');
                Array.from(allTableRows).forEach((tr, rowIndexInTable) => {{
                    const allTds = tr.children;
                    for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                        const seat = allTds[colIndex];
                        const style = seat.getAttribute('style');
                        const title = seat.getAttribute('title');

                        if (style && title &&
                            style.includes('cursor: pointer') &&
                            style.includes('icon_chair_empty')) {{

                            if (title.includes('排') && title.includes('號')) {{
                                const parts = title.split('-');
                                if (parts.length >= 3) {{
                                    const rowNum = parseInt(parts[1].replace('排', ''));
                                    const seatNum = parseInt(parts[2].replace('號', ''));
                                    if (!columns[seatNum]) columns[seatNum] = [];

                                    // 記錄垂直 DOM 位置（用於垂直連續性檢查）
                                    columns[seatNum].push({{
                                        elem: seat,
                                        rowNum: rowNum,
                                        title: title,
                                        domIndex: rowIndexInTable  // 垂直位置
                                    }});
                                }}
                            }}
                        }}
                    }}
                }});

                // 分析列品質（中間排優先）
                const columnQuality = [];
                for (const [seatNum, columnSeats] of Object.entries(columns)) {{
                    columnSeats.sort((a, b) => a.domIndex - b.domIndex);
                    const totalSeats = columnSeats.length;
                    const middleSeats = columnSeats.filter(s =>
                        s.rowNum >= MIDDLE_ROW_MIN && s.rowNum <= MIDDLE_ROW_MAX
                    );
                    const middleCount = middleSeats.length;
                    const middleRatio = totalSeats > 0 ? middleCount / totalSeats : 0;

                    columnQuality.push({{
                        seatNum: parseInt(seatNum),
                        totalSeats: totalSeats,
                        middleCount: middleCount,
                        middleRatio: middleRatio,
                        seats: columnSeats,
                        middleSeats: middleSeats
                    }});
                }}

                // 三層優先度排序
                columnQuality.sort((a, b) => {{
                    const aHasEnough = a.middleCount >= ticketNumber;
                    const bHasEnough = b.middleCount >= ticketNumber;
                    if (aHasEnough && !bHasEnough) return -1;
                    if (!aHasEnough && bHasEnough) return 1;

                    if (Math.abs(a.middleRatio - b.middleRatio) > 0.1) {{
                        return b.middleRatio - a.middleRatio;
                    }}

                    // 舞台方向排序
                    if (stageDirection === 'left') {{
                        return a.seatNum - b.seatNum;  // 左舞台：小號優先
                    }} else {{
                        return b.seatNum - a.seatNum;  // 右舞台：大號優先
                    }}
                }});

                if (showDebug) {{
                    console.log('[TICKET SEAT] Left/Right stage: ' + stageDirection +
                               ', columns found: ' + columnQuality.length);
                }}

                // 搜尋最佳座位組合（兩階段搜尋 + DOM 連續性檢查）
                for (const column of columnQuality) {{
                    let found = false;

                    if (!allowNonAdjacent) {{
                        // 先找中間排的連續座位
                        if (column.middleSeats.length >= ticketNumber) {{
                            for (let i = 0; i <= column.middleSeats.length - ticketNumber; i++) {{
                                let continuous = true;
                                for (let j = 0; j < ticketNumber - 1; j++) {{
                                    const domGap = column.middleSeats[i+j+1].domIndex -
                                                  column.middleSeats[i+j].domIndex;
                                    if (domGap > 1) {{
                                        continuous = false;
                                        break;
                                    }}
                                }}
                                if (continuous) {{
                                    for (let j = 0; j < ticketNumber; j++) {{
                                        selectedSeats.push(column.middleSeats[i+j]);
                                    }}
                                    found = true;
                                    if (showDebug) {{
                                        console.log('[TICKET SEAT] Selected from middle rows');
                                    }}
                                    break;
                                }}
                            }}
                        }}

                        // 回退到全列搜尋
                        if (!found) {{
                            for (let i = 0; i <= column.seats.length - ticketNumber; i++) {{
                                let continuous = true;
                                for (let j = 0; j < ticketNumber - 1; j++) {{
                                    const domGap = column.seats[i+j+1].domIndex -
                                                  column.seats[i+j].domIndex;
                                    if (domGap > 1) {{
                                        continuous = false;
                                        break;
                                    }}
                                }}
                                if (continuous) {{
                                    for (let j = 0; j < ticketNumber; j++) {{
                                        selectedSeats.push(column.seats[i+j]);
                                    }}
                                    found = true;
                                    if (showDebug) {{
                                        console.log('[TICKET SEAT] Selected from full column');
                                    }}
                                    break;
                                }}
                            }}
                        }}
                    }} else {{
                        // 不連續模式：從中間選起
                        if (column.middleSeats.length >= ticketNumber) {{
                            const startIdx = Math.floor((column.middleSeats.length - ticketNumber) / 2);
                            for (let i = 0; i < ticketNumber; i++) {{
                                selectedSeats.push(column.middleSeats[startIdx + i]);
                            }}
                            found = true;
                        }} else {{
                            const startIdx = Math.max(0, Math.floor((column.totalSeats - ticketNumber) / 2));
                            for (let i = 0; i < Math.min(ticketNumber, column.totalSeats); i++) {{
                                if (startIdx + i < column.seats.length) {{
                                    selectedSeats.push(column.seats[startIdx + i]);
                                }}
                            }}
                            found = true;
                        }}
                    }}

                    if (selectedSeats.length >= ticketNumber) break;
                }}
            }}

            // 【跨排選座】當允許不連續座位且仍未選到足夠座位時，從所有可用座位中選擇
            if (selectedSeats.length < ticketNumber && allowNonAdjacent && availableSeats.length >= ticketNumber) {{
                if (showDebug) {{
                    console.log('[TICKET SEAT] Cross-row selection: selecting from all available seats');
                }}
                selectedSeats.length = 0; // 清空之前的結果
                // 收集所有可用座位資訊
                const allSeatsWithInfo = [];
                const crossRowTableRows = document.querySelectorAll('table#TBL tbody tr, #locationChoice table tbody tr');
                Array.from(crossRowTableRows).forEach((tr, rowIdx) => {{
                    const allTds = tr.children;
                    for (let colIdx = 0; colIdx < allTds.length; colIdx++) {{
                        const seat = allTds[colIdx];
                        const style = seat.getAttribute('style');
                        const title = seat.getAttribute('title');
                        if (style && title && style.includes('cursor: pointer') && style.includes('icon_chair_empty')) {{
                            allSeatsWithInfo.push({{ title: title, rowIdx: rowIdx, colIdx: colIdx }});
                        }}
                    }}
                }});
                // 選擇前 ticketNumber 個座位
                for (let i = 0; i < Math.min(ticketNumber, allSeatsWithInfo.length); i++) {{
                    selectedSeats.push(allSeatsWithInfo[i]);
                }}
            }}

            if (showDebug) {{
                console.log('[TICKET SEAT] Algorithm result: found=' + availableSeats.length +
                           ', selected=' + selectedSeats.length);
                if (selectedSeats.length > 0) {{
                    console.log('[TICKET SEAT] Selected titles: ' +
                               selectedSeats.map(s => s.title).join(', '));
                }}
            }}

            return {{
                success: selectedSeats.length > 0,
                selectedSeats: selectedSeats.map(s => ({{ title: s.title }})),
                count: selectedSeats.length
            }};
        }})();
    ''')

    # 轉換 CDP 格式 using util function
    result_dict = util.parse_nodriver_result(result) if not isinstance(result, dict) else result

    debug.log(f"[TICKET SEAT] _find_best_seats result: {result_dict}")

    return result_dict if isinstance(result_dict, dict) else {}


async def _execute_seat_selection(tab, seats_to_click, config_dict):
    """
    執行座位點擊操作
    職責: 驗證座位、點擊座位、回報結果

    【修復 BUG-001】使用演算法選定的座位 title 列表而非簡單取前 N 個
    問題：原邏輯忽略 seats_to_click 參數，導致選到不連續座位
    解決：使用 selectedSeats 中的 title 列表精確定位並點擊

    Args: seats_to_click 來自 _find_best_seats_in_row 的座位集合
    Returns: bool 是否成功選擇座位
    """
    ticket_number = config_views.ticket_number(config_dict)
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    # 【修復 BUG-001】提取演算法選定的座位 title 列表
    selected_seat_titles = []
    use_algorithm_result = False

    debug.log(f"[TICKET SEAT] _execute input: type={type(seats_to_click)}, value={seats_to_click}")

    if seats_to_click and isinstance(seats_to_click, dict):
        if 'selectedSeats' in seats_to_click and seats_to_click['selectedSeats']:
            selected_seat_titles = [s['title'] for s in seats_to_click['selectedSeats']]
            use_algorithm_result = len(selected_seat_titles) > 0

            debug.log(f"[TICKET SEAT] Using algorithm selected seats: {selected_seat_titles}")
        else:
            debug.log(f"[TICKET SEAT] No selectedSeats in result, keys={seats_to_click.keys()}")

    import json
    # 執行 JavaScript 點擊座位
    result = await tab.evaluate(f'''
        (function() {{
            const ticketNumber = {ticket_number};
            const showDebug = {json.dumps(show_debug)};
            const selectedTitles = {json.dumps(selected_seat_titles)};
            const useAlgorithm = {json.dumps(use_algorithm_result)};

            let clickedCount = 0;
            const clickedTitles = [];

            // 【修復 BUG-001】優先使用演算法選定的座位
            if (useAlgorithm && selectedTitles.length > 0) {{
                for (const targetTitle of selectedTitles) {{
                    // 使用與 _analyze_seat_quality 一致的選擇器
                    let seat = document.querySelector(
                        `table#TBL td[title="${{targetTitle}}"][style*="cursor: pointer"]`
                    );
                    if (!seat) {{
                        seat = document.querySelector(
                            `#locationChoice table td[title="${{targetTitle}}"][style*="cursor: pointer"]`
                        );
                    }}
                    if (seat && seat.getAttribute('style').includes('icon_chair_empty')) {{
                        seat.click();
                        clickedCount++;
                        clickedTitles.push(targetTitle);
                        if (showDebug) {{
                            console.log('[SUCCESS] Selected algorithm seat: ' + targetTitle);
                        }}
                    }}
                }}
            }} else {{
                // [FALLBACK] Algorithm failed - ensure only selecting consecutive seats
                if (showDebug) {{
                    console.log('[FALLBACK] Algorithm result empty, using fallback with direction-aware check');
                }}

                // Step 1: Detect stage direction
                const stageIcon = document.querySelector('#ctl00_ContentPlaceHolder1_lbStageArrow i');
                let stageDirection = 'up'; // default
                if (stageIcon) {{
                    if (stageIcon.classList.contains('fa-arrow-circle-up')) stageDirection = 'up';
                    else if (stageIcon.classList.contains('fa-arrow-circle-down')) stageDirection = 'down';
                    else if (stageIcon.classList.contains('fa-arrow-circle-left')) stageDirection = 'left';
                    else if (stageIcon.classList.contains('fa-arrow-circle-right')) stageDirection = 'right';
                }}
                if (showDebug) {{
                    console.log('[FALLBACK] Stage direction: ' + stageDirection);
                }}

                // 使用與 _analyze_seat_quality 一致的選擇器
                const availableSeats = [];
                const allTableRowsFallback = document.querySelectorAll('table#TBL tbody tr, #locationChoice table tbody tr');
                Array.from(allTableRowsFallback).forEach(tr => {{
                    const allTds = tr.children;
                    for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                        const seat = allTds[colIndex];
                        const style = seat.getAttribute('style');
                        const title = seat.getAttribute('title');
                        if (style && title && style.includes('cursor: pointer') && style.includes('icon_chair_empty')) {{
                            availableSeats.push(seat);
                        }}
                    }}
                }});

                let foundSeats = [];

                if (stageDirection === 'up' || stageDirection === 'down') {{
                    // Stage at top/bottom: group by ROW, consecutive = same row
                    const rows = {{}};
                    availableSeats.forEach(seat => {{
                        const title = seat.getAttribute('title');
                        if (title && title.includes('-') && title.includes('排')) {{
                            const parts = title.split('-');
                            if (parts.length >= 2) {{
                                const rowNum = parseInt(parts[1].replace('排', ''));
                                if (!rows[rowNum]) rows[rowNum] = [];
                                const parent = seat.parentElement;
                                const colIdx = parent ? Array.from(parent.children).indexOf(seat) : 0;
                                rows[rowNum].push({{ elem: seat, title: title, colIdx: colIdx }});
                            }}
                        }}
                    }});

                    // Sort rows: up=smaller first, down=larger first
                    const sortedRowNums = Object.keys(rows).sort((a, b) => {{
                        return stageDirection === 'up' ? parseInt(a) - parseInt(b) : parseInt(b) - parseInt(a);
                    }});

                    for (const rowNum of sortedRowNums) {{
                        const rowSeats = rows[rowNum];
                        rowSeats.sort((a, b) => a.colIdx - b.colIdx);

                        for (let i = 0; i <= rowSeats.length - ticketNumber; i++) {{
                            let continuous = true;
                            for (let j = 0; j < ticketNumber - 1; j++) {{
                                if (rowSeats[i + j + 1].colIdx - rowSeats[i + j].colIdx > 1) {{
                                    continuous = false;
                                    break;
                                }}
                            }}
                            if (continuous) {{
                                foundSeats = rowSeats.slice(i, i + ticketNumber);
                                break;
                            }}
                        }}
                        if (foundSeats.length >= ticketNumber) break;
                    }}
                }} else {{
                    // Stage at left/right: group by SEAT NUMBER (column), consecutive = same column
                    const columns = {{}};
                    availableSeats.forEach(seat => {{
                        const title = seat.getAttribute('title');
                        if (title && title.includes('-') && title.includes('號')) {{
                            const parts = title.split('-');
                            if (parts.length >= 3) {{
                                const seatNum = parseInt(parts[2].replace('號', ''));
                                const rowNum = parseInt(parts[1].replace('排', ''));
                                if (!columns[seatNum]) columns[seatNum] = [];
                                const parent = seat.parentElement;
                                const rowIdx = parent && parent.parentElement ?
                                    Array.from(parent.parentElement.children).indexOf(parent) : 0;
                                columns[seatNum].push({{ elem: seat, title: title, rowIdx: rowIdx, rowNum: rowNum }});
                            }}
                        }}
                    }});

                    // Sort columns: left=smaller first, right=larger first
                    const sortedColNums = Object.keys(columns).sort((a, b) => {{
                        return stageDirection === 'left' ? parseInt(a) - parseInt(b) : parseInt(b) - parseInt(a);
                    }});

                    for (const colNum of sortedColNums) {{
                        const colSeats = columns[colNum];
                        colSeats.sort((a, b) => a.rowIdx - b.rowIdx);

                        for (let i = 0; i <= colSeats.length - ticketNumber; i++) {{
                            let continuous = true;
                            for (let j = 0; j < ticketNumber - 1; j++) {{
                                if (colSeats[i + j + 1].rowIdx - colSeats[i + j].rowIdx > 1) {{
                                    continuous = false;
                                    break;
                                }}
                            }}
                            if (continuous) {{
                                foundSeats = colSeats.slice(i, i + ticketNumber);
                                break;
                            }}
                        }}
                        if (foundSeats.length >= ticketNumber) break;
                    }}
                }}

                // Click the found seats
                for (const seatObj of foundSeats) {{
                    seatObj.elem.click();
                    clickedCount++;
                    clickedTitles.push(seatObj.title);
                    if (showDebug) {{
                        console.log('[FALLBACK] Selected seat: ' + seatObj.title);
                    }}
                }}
            }}

            if (showDebug) {{
                console.log('[TICKET SEAT] Clicked ' + clickedCount + '/' + ticketNumber + ' seats');
            }}

            return {{
                success: clickedCount > 0,
                clicked: clickedCount,
                titles: clickedTitles
            }};
        }})();
    ''')

    # 轉換 CDP 格式 using util function
    result_dict = util.parse_nodriver_result(result) if not isinstance(result, dict) else result
    if not isinstance(result_dict, dict):
        result_dict = {}
    return result_dict.get('success', False)


async def nodriver_ticket_seat_auto_select(tab, config_dict):
    """
    年代售票 - 自動選擇座位，優化策略：中間區域優先 + 舞台方向智慧選座

    [TESTED] 已完整測試 - T005 函數重構驗證
    [TESTED] 已完整測試 - 舞台方向感知選座 (T007)
    [TESTED] 已完整測試 - 中間區域優先策略

    Reference: docs/02-development/ticket_seat_selection_algorithm.md

    [T005 重構] 分解為 3 個子函數協調器
    - _analyze_seat_quality(): 偵測舞台方向、收集可用座位
    - _find_best_seats_in_row(): 尋找最佳座位
    - _execute_seat_selection(): 執行座位點擊
    """
    # 函數開始檢查暫停 [T004修正]
    if await check_and_handle_pause(config_dict):
        return False

    is_seat_assigned = False
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    try:
        # Step 1: 分析座位品質
        seat_analysis = await _analyze_seat_quality(tab, config_dict)

        debug.log(f"[TICKET SEAT] Stage direction: {seat_analysis.get('direction', 'unknown')}")
        debug.log(f"[TICKET SEAT] Found {seat_analysis.get('totalSeats', 0)} available seats")

        # Step 2: 尋找最佳座位組合
        best_seats = await _find_best_seats_in_row(tab, seat_analysis, config_dict)

        # Step 3: 執行座位選擇
        is_seat_assigned = await _execute_seat_selection(tab, best_seats, config_dict)

        if is_seat_assigned:
            debug.log(f"[TICKET SEAT] Selected {best_seats.get('count', 0)} seats successfully")

    except Exception as exc:
        debug.log(f"[ERROR] Seat selection error: {exc}")
        import traceback
        if debug.enabled:
            traceback.print_exc()

    return is_seat_assigned


async def nodriver_ticket_seat_main(tab, config_dict, ocr, domain_name):
    """
    年代售票座位選擇主流程：票別選擇 -> 座位選擇 -> 驗證碼 -> 提交

    [TESTED] 已完整測試 - 主流程協調驗證
    [TESTED] 已完整測試 - 票別->座位->驗證碼->提交

    """
    # 函數開始檢查暫停 [T004修正]
    if await check_and_handle_pause(config_dict):
        return False, False, False

    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)
    ticket_number = config_views.ticket_number(config_dict)

    # Step 0: Check if seats are already selected (avoid duplicate selection)
    already_selected_count = 0
    try:
        check_result = await tab.evaluate('''
            (() => {
                const selectedSeats = document.querySelectorAll('#TBL td[style*="icon_chair_select"]');
                return selectedSeats.length;
            })()
        ''')
        if isinstance(check_result, int):
            already_selected_count = check_result
        elif isinstance(check_result, dict):
            already_selected_count = check_result.get('value', 0)

        debug.log(f"[TICKET SEAT] Already selected seats: {already_selected_count}")
    except Exception as exc:
        debug.log(f"[TICKET SEAT] Error checking selected seats: {exc}")

    # If already selected enough seats, skip seat selection and go to submit
    if already_selected_count >= ticket_number:
        debug.log(f"[TICKET SEAT] Already have {already_selected_count} seats (need {ticket_number}), skipping to submit")
        is_seat_type_assigned = True
        is_seat_assigned = True
    else:
        # Step 1: Select seat type
        area_config = config_views.area_view(config_dict)
        area_keyword = area_config["keyword"].strip()
        is_seat_type_assigned = await nodriver_ticket_seat_type_auto_select(
            tab, config_dict, area_keyword
        )

        # Step 2: Select seats
        is_seat_assigned = False
        if is_seat_type_assigned:
            # Additional wait for DOM to fully stabilize after AJAX update
            # Increased to 2s to ensure AJAX completes
            await tab.sleep(2.0)
            is_seat_assigned = await nodriver_ticket_seat_auto_select(tab, config_dict)

    # Step 3: Handle captcha (reuse KHAM OCR)
    captcha_config = config_views.captcha_view(config_dict)
    is_captcha_sent = False
    if is_seat_assigned and captcha_config["enable"]:
        try:
            # Find captcha input field
            captcha_input = await tab.query_selector('#ctl00_ContentPlaceHolder1_CHK')
            if captcha_input:
                # Reuse KHAM's OCR handling function
                model_name = "UTK0205"
                is_captcha_sent = await nodriver_kham_captcha(
                    tab, config_dict, ocr, model_name
                )
        except Exception as exc:
            debug.log(f"[ERROR] Captcha processing error: {exc}")

    # Step 4: Submit order
    is_submit_success = False
    if is_seat_assigned and (not captcha_config["enable"] or is_captcha_sent):
        try:
            # 【修復 2】使用純 JavaScript 實作以避免 Element 序列化問題，加入多個備用選擇器
            import json
            result = await tab.evaluate(f'''
                (function() {{
                    const showDebug = {json.dumps(show_debug)};

                    // [Optimized] Simplified selector - MCP test confirmed .sumitButton is sufficient
                    const submitButton = document.querySelector('button.sumitButton');

                    if (showDebug) {{
                        console.log('[TICKET SUBMIT] Button found:', !!submitButton);
                        if (submitButton) {{
                            console.log('[TICKET SUBMIT] Button classes:', submitButton.className);
                            console.log('[TICKET SUBMIT] Button text:', submitButton.textContent);
                            console.log('[TICKET SUBMIT] Button disabled:', submitButton.disabled);
                        }}
                    }}

                    if (submitButton && !submitButton.disabled) {{
                        if (showDebug) {{
                            console.log('[TICKET SUBMIT] Clicking submit button...');
                        }}
                        submitButton.click();
                        return true;
                    }}

                    if (showDebug) {{
                        console.log('[TICKET SUBMIT] Submit button not available');
                    }}
                    return false;
                }})();
            ''')

            # 轉換 CDP 格式 (布林值)
            if isinstance(result, list) and len(result) == 2:
                # CDP format: ['value', {'type': 'boolean', 'value': True/False}]
                is_submit_success = result[1].get('value', False) if isinstance(result[1], dict) else result
            else:
                is_submit_success = result

            if is_submit_success:
                debug.log("[TICKET SUBMIT] Order submitted successfully")

                # Check and close success dialog (year ticket shows "加入購物車完成" dialog)
                # Wait up to 5 seconds for dialog to appear
                dialog_closed = False
                for i in range(10):  # 10 attempts * 0.5s = 5 seconds
                    await tab.sleep(0.5)
                    try:
                        dialog_btn = await tab.query_selector('div.ui-dialog-buttonset > button[type="button"]')
                        if dialog_btn:
                            debug.log("[TICKET SUBMIT] Success dialog found, closing...")
                            await dialog_btn.click()
                            await tab.sleep(0.5)  # Wait for dialog close animation
                            dialog_closed = True
                            debug.log("[TICKET SUBMIT] Dialog closed successfully")
                            break
                    except Exception as e:
                        if i == 9:
                            debug.log(f"[TICKET SUBMIT] Dialog close attempt failed: {e}")
                        pass

                if not dialog_closed:
                    debug.log("[TICKET SUBMIT] No dialog appeared within 5 seconds")

                # Play sound if enabled
                if config_dict["advanced"]["play_sound"]["order"]:
                    play_sound_while_ordering(config_dict)

        except Exception as exc:
            debug.log(f"[ERROR] Submit exception: {exc}")
            # Fallback: use JavaScript to force submit (same as Chrome)
            try:
                await tab.evaluate('addShoppingCart1();')
                is_submit_success = True
                if config_dict["advanced"]["play_sound"]["order"]:
                    play_sound_while_ordering(config_dict)
                debug.log("[TICKET SUBMIT] Submitted via fallback method")
            except Exception as exc2:
                debug.log(f"[ERROR] Fallback submit error: {exc2}")

    # Step 5: Check for seat taken dialog and retry if needed
    if not is_submit_success:
        is_seat_taken = await nodriver_ticket_check_seat_taken_dialog(tab, config_dict)
        if is_seat_taken:
            # Seat was taken, retry seat selection
            await tab.sleep(0.5)
            is_seat_assigned = await nodriver_ticket_seat_auto_select(tab, config_dict)

            # Retry captcha if needed
            if is_seat_assigned and captcha_config["enable"]:
                try:
                    captcha_input = await tab.query_selector('#ctl00_ContentPlaceHolder1_CHK')
                    if captcha_input:
                        model_name = "UTK0205"
                        is_captcha_sent = await nodriver_kham_captcha(
                            tab, config_dict, ocr, model_name
                        )
                except Exception as exc:
                    debug.log(f"[ERROR] Retry captcha error: {exc}")

            # Retry submit
            if is_seat_assigned and (not captcha_config["enable"] or is_captcha_sent):
                try:
                    result = await tab.evaluate('''
                        (function() {
                            const button = document.querySelector('button.sumitButton[onclick*="addShoppingCart1"]');
                            if (button && !button.disabled) {
                                button.click();
                                return true;
                            }
                            return false;
                        })();
                    ''')

                    if isinstance(result, list) and len(result) == 2:
                        is_submit_success = result[1].get('value', False) if isinstance(result[1], dict) else result
                    else:
                        is_submit_success = result

                    if is_submit_success:
                        debug.log("[TICKET SUBMIT] Order submitted successfully after retry")

                        # Check and close success dialog (same logic as initial submit)
                        dialog_closed = False
                        for i in range(10):  # 10 attempts * 0.5s = 5 seconds
                            await tab.sleep(0.5)
                            try:
                                dialog_btn = await tab.query_selector('div.ui-dialog-buttonset > button[type="button"]')
                                if dialog_btn:
                                    debug.log("[TICKET SUBMIT RETRY] Success dialog found, closing...")
                                    await dialog_btn.click()
                                    await tab.sleep(0.5)
                                    dialog_closed = True
                                    debug.log("[TICKET SUBMIT RETRY] Dialog closed successfully")
                                    break
                            except Exception as e:
                                if i == 9:
                                    debug.log(f"[TICKET SUBMIT RETRY] Dialog close attempt failed: {e}")
                                pass

                        if not dialog_closed:
                            debug.log("[TICKET SUBMIT RETRY] No dialog appeared within 5 seconds")

                        # Play sound
                        if config_dict["advanced"]["play_sound"]["order"]:
                            play_sound_while_ordering(config_dict)

                except Exception as exc:
                    debug.log(f"[ERROR] Retry submit error: {exc}")

    debug.log(f"[TICKET SEAT MAIN] Type:{is_seat_type_assigned} "
          f"Seat:{is_seat_assigned} Submit:{is_submit_success}")

    return is_submit_success


async def nodriver_ticket_check_seat_taken_dialog(tab, config_dict):
    """
    檢查並處理「座位已被訂走」對話框
    對話框內容：「有部分座位已被訂走, 請再重新選取座位 !」

    [TESTED] 已完整測試 - 座位已被訂走對話框處理
    """
    # 函數開始檢查暫停 [T004修正]
    if await check_and_handle_pause(config_dict):
        return False

    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)
    is_dialog_found = False

    try:
        # 使用純 JavaScript 偵測對話框並點擊 OK 按鈕
        result = await tab.evaluate('''
            (function() {
                const dialog = document.querySelector('#dialog-message');
                if (!dialog) return false;

                const text = dialog.textContent || '';
                if (text.includes('座位已被訂走') || text.includes('請再重新選取座位')) {
                    // 找到 OK 按鈕並點擊
                    const okButton = document.querySelector('.ui-dialog-buttonset button[type="button"]');
                    if (okButton) {
                        okButton.click();
                        console.log('[TICKET DIALOG] Seat taken dialog closed');
                        return true;
                    }
                }
                return false;
            })();
        ''')

        # 轉換 CDP 格式
        if isinstance(result, list) and len(result) == 2:
            is_dialog_found = result[1].get('value', False) if isinstance(result[1], dict) else result
        else:
            is_dialog_found = result

        if is_dialog_found:
            debug.log("[TICKET DIALOG] Seat taken dialog detected and closed, will retry seat selection")

    except Exception as exc:
        debug.log(f"[ERROR] Dialog check error: {exc}")

    return is_dialog_found


async def nodriver_ticket_close_dialog_with_retry(tab, config_dict, max_attempts=5, interval=0.3):
    """
    [ticket.com.tw] 多次檢查並關閉對話框

    解決問題：網頁操作太快，對話框出現速度慢，單次檢查會錯過

    Args:
        tab: NoDriver tab
        config_dict: 設定字典
        max_attempts: 最大嘗試次數 (預設 5 次)
        interval: 每次嘗試間隔秒數 (預設 0.3 秒)

    Returns:
        bool: 是否成功關閉對話框
    """
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)
    dialog_closed = False

    for attempt in range(max_attempts):
        try:
            # 嘗試多種選擇器
            dialog_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
            if not dialog_btn:
                dialog_btn = await tab.query_selector('div.ui-dialog-buttonset > button[type="button"]')
            if not dialog_btn:
                dialog_btn = await tab.query_selector('.ui-dialog-buttonset button')

            if dialog_btn:
                await dialog_btn.click()
                dialog_closed = True
                debug.log(f"[TICKET DIALOG] Closed on attempt {attempt + 1}/{max_attempts}")
                # 等待一下讓對話框關閉動畫完成
                await tab.sleep(0.2)
                break
        except Exception as e:
            if attempt == max_attempts - 1:
                debug.log(f"[TICKET DIALOG] Close failed after {max_attempts} attempts: {e}")

        # 等待後再嘗試
        if attempt < max_attempts - 1:
            await tab.sleep(interval)

    return dialog_closed


async def nodriver_ticket_allow_not_adjacent_seat(tab, config_dict):
    """
    允許非連續座位

    [TESTED] 已完整測試 - 非連續座位許可

    Similar to: nodriver_check_checkbox_enhanced in nodriver_common.py
    """
    # 函數開始檢查暫停 [T004修正]
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)

    # Use JavaScript to directly check (NoDriver recommended approach)
    is_checked = await tab.evaluate('''
        (function() {
            const checkbox = document.querySelector('div.panel > span > input[type="checkbox"]');
            if (!checkbox) return false;

            // If already checked, return true
            if (checkbox.checked) return true;

            // Try to click
            try {
                checkbox.click();
                return checkbox.checked;
            } catch(e) {
                // Fallback: directly set checked property
                checkbox.checked = true;
                return checkbox.checked;
            }
        })();
    ''')

    debug.log(f"[ALLOW NOT ADJACENT] Checkbox checked: {is_checked}")

    return is_checked


async def nodriver_ticket_switch_to_auto_seat(tab):
    """
    切換到自動選座模式
    """
    is_switch_to_auto_seat = False

    try:
        # Find switch button (input with value BUY_TYPE_2)
        btn = await tab.query_selector('input[value="BUY_TYPE_2"]')

        if btn:
            # Check if already selected
            # Element.apply, not tab.evaluate(script, element): Tab.evaluate's
            # second positional argument is await_promise, not a script argument.
            is_checked = await btn.apply('(elem) => elem.checked === true')

            if not is_checked:
                # Not selected, click it
                try:
                    await btn.click()
                    is_switch_to_auto_seat = True
                except Exception:
                    # Fallback: use JavaScript to force click
                    try:
                        await btn.apply('(elem) => elem.click()')
                        is_switch_to_auto_seat = True
                    except:
                        pass
            else:
                # Already selected
                is_switch_to_auto_seat = True

    except Exception:
        pass

    return is_switch_to_auto_seat
