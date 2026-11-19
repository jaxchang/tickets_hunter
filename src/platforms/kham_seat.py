#encoding=utf-8
# =============================================================================
# KHAM Seat Module
# The kham-family seat selection flow (issue #42, ADR 0006), split out of
# platforms/kham.py. Imports only the shared layer (platforms.kham_shared)
# and the common layers.
# =============================================================================

import json
import re
import traceback
import urllib.parse
from zendriver import cdp
import util
import config_views
from nodriver_common import (
    asyncio_sleep_with_pause_check,
    nodriver_force_check_checkbox,
    play_sound_while_ordering,
)
from platforms.kham_shared import (
    CONST_KHAM_RELOAD_SETTLE,
    CONST_UTK_RWD_EMPTY_SEAT_IMG,
    _handle_post_submit_dialog,
    _state,
    is_utk_rwd_url,
    nodriver_kham_captcha,
)


async def nodriver_kham_allow_not_adjacent_seat(tab, config_dict):
    """
    Check the "allow not adjacent seat" checkbox
    """
    debug = util.create_debug_logger(config_dict)

    agree_checkbox = None
    try:
        agree_checkbox = await tab.query_selector('table.eventTABLE > tbody > tr > td > input[type="checkbox"]')
    except Exception as exc:
        debug.log("Find kham adjacent_seat checkbox exception:", exc)

    is_finish_checkbox_click = await nodriver_force_check_checkbox(tab, agree_checkbox)

    return is_finish_checkbox_click


async def nodriver_kham_switch_to_auto_seat(tab):
    """
    Switch to auto seat selection mode
    """
    is_switch_to_auto_seat = False

    try:
        # Check if auto seat radio button exists
        btn_switch_result = await tab.evaluate('''
            (function() {
                const btn = document.querySelector('#BUY_TYPE_2');
                if (!btn) return { exists: false };

                const buttonClass = btn.getAttribute('class') || '';
                return {
                    exists: true,
                    isActive: buttonClass === 'red',
                    button: btn
                };
            })();
        ''')

        if btn_switch_result and btn_switch_result.get('exists'):
            if btn_switch_result.get('isActive'):
                is_switch_to_auto_seat = True
            else:
                # Click to switch to auto seat
                el_btn = await tab.query_selector('#BUY_TYPE_2')
                if el_btn:
                    await el_btn.click()
                    is_switch_to_auto_seat = True
    except Exception as exc:
        pass

    return is_switch_to_auto_seat


async def nodriver_kham_seat_type_auto_select(tab, config_dict, area_keyword_item):
    """
    寬宏售票 - 自動選擇票別 (UTK0205 座位選擇頁面)
    選擇票價類型，如原價、身障、陪同者等
    HTML structure: <button class="green" onclick="setType(...)">原價-NT$3,680</button>
    使用 CDP DOMSnapshot 穿透 DOM，避免 JavaScript 載入時機問題
    參考年代售票 nodriver_ticket_seat_type_auto_select 成功實作
    """
    debug = util.create_debug_logger(config_dict)
    is_seat_type_assigned = False
    # Tells nodriver_kham_seat_main apart "no seat type is usable" (the page
    # has loaded and this area is spent) from "no buttons yet" (still loading):
    # both return False here.
    _state["kham_seat_types_exhausted"] = False

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
            debug.log(f"[KHAM SEAT TYPE] Keyword parse error: {e}")

    try:
        from zendriver import cdp

        # Step 1: Capture DOM snapshot
        debug.log("[KHAM SEAT TYPE] Capturing DOM snapshot...")

        try:
            documents, strings = await tab.send(cdp.dom_snapshot.capture_snapshot(
                computed_styles=[],
                include_dom_rects=True
            ))
        except Exception as snapshot_exc:
            debug.log(f"[KHAM SEAT TYPE] ERROR capturing snapshot: {snapshot_exc}")
            # Fallback: try simple JavaScript method
            debug.log("[KHAM SEAT TYPE] Falling back to JavaScript method...")

            buttons_data = await tab.evaluate('''
                (function() {
                    const buttons = document.querySelectorAll('button[onclick*="setType"]');
                    const result = [];
                    buttons.forEach((btn) => {
                        result.push({
                            text: btn.textContent.trim(),
                            disabled: btn.disabled
                        });
                    });
                    return result;
                })();
            ''')

            # 轉換 CDP 格式為 Python list
            buttons_list = []
            if isinstance(buttons_data, list):
                for item in buttons_data:
                    if isinstance(item, list) and len(item) == 2:
                        val_obj = item[1]
                        if isinstance(val_obj, dict) and 'value' in val_obj:
                            obj_data = val_obj['value']
                            btn_info = {}
                            if isinstance(obj_data, list):
                                for prop in obj_data:
                                    if isinstance(prop, list) and len(prop) == 2:
                                        key = prop[0]
                                        val = prop[1].get('value') if isinstance(prop[1], dict) else prop[1]
                                        btn_info[key] = val
                            buttons_list.append(btn_info)

            debug.log(f"[KHAM SEAT TYPE] Found {len(buttons_list)} button(s) via JavaScript fallback")

            # 簡化邏輯：直接使用 JavaScript 找到的按鈕
            if len(buttons_list) > 0:
                # 直接點擊第一個可用按鈕
                result = await tab.evaluate('''
                    (function() {
                        const buttons = document.querySelectorAll('button[onclick*="setType"]');
                        for (let btn of buttons) {
                            if (!btn.disabled) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    })();
                ''')

                if isinstance(result, list) and len(result) == 2:
                    is_seat_type_assigned = result[1].get('value', False) if isinstance(result[1], dict) else result
                else:
                    is_seat_type_assigned = result

                debug.log(f"[KHAM SEAT TYPE] JavaScript fallback click result: {is_seat_type_assigned}")

                debug.log(f"[KHAM SEAT TYPE] Assignment result: {is_seat_type_assigned}")

                return is_seat_type_assigned

            raise snapshot_exc

        # Step 2: Search for ticket type buttons
        ticket_buttons = []

        debug.log(f"[KHAM SEAT TYPE] documents type: {type(documents)}, len: {len(documents) if documents else 0}")

        if documents and len(documents) > 0:
            document_snapshot = documents[0]
            nodes = document_snapshot.nodes

            # Extract node information
            node_names = [strings[i] for i in nodes.node_name]
            node_values = [strings[i] if i >= 0 else '' for i in nodes.node_value]
            attributes_list = nodes.attributes
            backend_node_ids = list(nodes.backend_node_id)

            debug.log(f"[KHAM SEAT TYPE] Total nodes in snapshot: {len(node_names)}")

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
                                    debug.log(f"[KHAM SEAT TYPE] Extracted text from node {j}: '{button_text}'")
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
                                debug.log(f"[KHAM SEAT TYPE] Extracted text from onclick: '{button_text}'")

                        ticket_buttons.append({
                            'backend_node_id': backend_node_ids[i],
                            'class': button_class,
                            'onclick': onclick,
                            'disabled': button_disabled,
                            'text': button_text,
                            'index': i
                        })

                        debug.log(f"[KHAM SEAT TYPE] Found button #{len(ticket_buttons)}: text='{button_text}', disabled={button_disabled}")

        debug.log(f"[KHAM SEAT TYPE] Found {len(ticket_buttons)} ticket type button(s)")

        if len(ticket_buttons) == 0:
            debug.log("[KHAM SEAT TYPE] No ticket type buttons found")
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
            button_text = btn.get('text', '')
            if button_text and util.reset_row_text_if_match_keyword_exclude(config_dict, button_text):
                debug.log(f"[KHAM SEAT TYPE] Excluded by keyword_exclude: {button_text}")
                continue
            enabled_buttons.append(btn)

        debug.log(f"[KHAM SEAT TYPE] Found {len(enabled_buttons)} enabled button(s)")

        if len(enabled_buttons) == 0:
            debug.log("[KHAM SEAT TYPE] All buttons are disabled or excluded")
            _state["kham_seat_types_exhausted"] = True
            return False

        # Step 5: Match and select button using Python logic
        matched_button = None

        for btn in enabled_buttons:
            button_text = btn.get('text', '')
            if not button_text:
                continue

            # 關鍵字匹配邏輯
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
                matched_button = btn
                debug.log(f"[KHAM SEAT TYPE] Matched: {button_text}")
                break

        # If no keyword match found, use first enabled button
        if matched_button is None and len(enabled_buttons) > 0:
            matched_button = enabled_buttons[0]
            debug.log(f"[KHAM SEAT TYPE] No keyword match, using first button: {matched_button.get('text', '')}")

        # Step 6: Click the matched button with JavaScript (btn.click()); the CDP steps below
        # only locate and scroll the element, no mouse event is dispatched
        if matched_button is not None:
            button_text = matched_button.get('text', '')
            backend_node_id = matched_button.get('backend_node_id')

            try:
                debug.log(f"[KHAM SEAT TYPE] Clicking button via CDP: {button_text}")

                is_seat_type_assigned = False

                # Step 6.1: Convert backend_node_id to node_id using CDP DOM
                if backend_node_id is not None:
                    try:
                        debug.log(f"[KHAM SEAT TYPE] Using backend_node_id={backend_node_id}")

                        # Get document first
                        await tab.send(cdp.dom.get_document())

                        # Convert backend_node_id to node_id
                        push_result = await tab.send(cdp.dom.push_nodes_by_backend_ids_to_frontend([backend_node_id]))
                        if push_result and len(push_result) > 0:
                            node_id = push_result[0]

                            debug.log(f"[KHAM SEAT TYPE] Converted to node_id={node_id}")

                            # Step 6.2: Scroll into view to ensure element is visible
                            try:
                                await tab.send(cdp.dom.scroll_into_view_if_needed(node_id=node_id))
                                debug.log(f"[KHAM SEAT TYPE] Scrolled button into view")
                            except Exception as scroll_exc:
                                debug.log(f"[KHAM SEAT TYPE] Scroll into view exception (non-critical): {scroll_exc}")

                            # Step 6.3: Get box model for precise click coordinates
                            try:
                                box_model = await tab.send(cdp.dom.get_box_model(node_id=node_id))
                                debug.log(f"[KHAM SEAT TYPE] Box model result: {type(box_model)}")

                                if box_model and hasattr(box_model, 'content') and box_model.content:
                                    quad = box_model.content
                                    # Calculate center coordinates from quad (8 values: [x1, y1, x2, y2, x3, y3, x4, y4])
                                    center_x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4.0
                                    center_y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4.0

                                    debug.log(f"[KHAM SEAT TYPE] Box model center: ({center_x:.1f}, {center_y:.1f})")

                                    # Step 6.4: The click itself is done by the JavaScript click below;
                                    # the box model center is only logged
                                    debug.log(f"[KHAM SEAT TYPE] Using JavaScript click (CDP input unavailable)")
                                else:
                                    debug.log(f"[KHAM SEAT TYPE] Failed to get box model content: {box_model}")
                                    debug.log("[KHAM SEAT TYPE] Falling back to JavaScript click...")

                            except Exception as box_exc:
                                debug.log(f"[KHAM SEAT TYPE] Box model exception: {box_exc}")
                        else:
                            debug.log(f"[KHAM SEAT TYPE] Failed to convert backend_node_id: push_result={push_result}")

                    except Exception as cdp_exc:
                        debug.log(f"[KHAM SEAT TYPE] CDP operation error: {cdp_exc}")
                else:
                    debug.log(f"[KHAM SEAT TYPE] backend_node_id is None, using JavaScript fallback")

                # Click the matched button with JavaScript (the only click path)
                if not is_seat_type_assigned:
                    try:
                        debug.log(f"[KHAM SEAT TYPE] Attempting JavaScript fallback click")
                        result = await tab.evaluate('''
                            (function() {
                                const buttons = document.querySelectorAll('button[onclick*="setType"]');
                                for (let btn of buttons) {
                                    if (btn.textContent.includes('%s')) {
                                        btn.click();
                                        return true;
                                    }
                                }
                                return false;
                            })();
                        ''' % button_text.replace("'", "\\'"))

                        if isinstance(result, list) and len(result) == 2:
                            is_seat_type_assigned = result[1].get('value', False) if isinstance(result[1], dict) else result
                        else:
                            is_seat_type_assigned = result

                        if is_seat_type_assigned:
                            debug.log(f"[KHAM SEAT TYPE] JavaScript fallback click succeeded")
                        else:
                            debug.log(f"[KHAM SEAT TYPE] JavaScript fallback click failed")
                    except Exception as js_exc:
                        debug.log(f"[KHAM SEAT TYPE] JavaScript fallback exception: {js_exc}")

            except Exception as click_exc:
                debug.log(f"[KHAM SEAT TYPE] CDP click exception: {click_exc}")

    except Exception as exc:
        debug.log(f"[ERROR] KHAM seat type selection error: {exc}")
        import traceback
        if debug.enabled:
            traceback.print_exc()

    debug.log(f"[KHAM SEAT TYPE] Assignment result: {is_seat_type_assigned}")

    return is_seat_type_assigned


async def nodriver_kham_seat_auto_select(tab, config_dict, rwd=False):
    """
    rwd=True: a 中職 RWD seat map, whose free seats carry no 'empty' class
    (see CONST_UTK_RWD_EMPTY_SEAT_IMG); everything else is shared.

    寬宏售票 - 自動選擇座位，優化策略：舞台方向智慧選座
    HTML structure:
    - Stage direction: <div class="stageDirection topright/topleft/downright/downleft/top/down/left/right">
    - Available seats: <table id="TBL"><td class="empty up/down/left/right" title="2樓黃2D區-3排-14號">
    - Sold seats: <td class="people up/down/left/right">

    舞台方向邏輯：
    - up: 座位圖上越上方的排越前，每排選中間座位
    - down: 座位圖上越下方的排越前，每排選中間座位（依畫面位置，不看排號：中信特攻的排號由球場往上遞增）
    - left: 座位號越小越前，每列選中間排
    - right: 座位號越大越前，每列選中間排
    - topright/topleft/downright/downleft: 組合方向，優先處理主要方向
    """
    is_seat_assigned = False
    ticket_number = config_dict["ticket_number"]
    allow_non_adjacent = config_dict["advanced"]["disable_adjacent_seat"]
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    try:
        # 使用純 JavaScript 執行全部邏輯：偵測方向 -> 分組 -> 排序 -> 選擇 -> 點擊
        import json
        result = await tab.evaluate(f'''
            (function() {{
                const ticketNumber = {ticket_number};
                const allowNonAdjacent = {json.dumps(allow_non_adjacent)};
                const showDebug = {json.dumps(show_debug)};
                const rwdSeat = {json.dumps(bool(rwd))};
                const rwdEmptyImg = {json.dumps(CONST_UTK_RWD_EMPTY_SEAT_IMG)};

                function isEmptySeat(td) {{
                    if (td.classList.contains('empty')) return true;
                    return rwdSeat && td.hasAttribute('title') &&
                        (td.style.backgroundImage || '').indexOf(rwdEmptyImg) >= 0;
                }}

                // Step 0: Detect stage direction
                const stageDiv = document.querySelector('.stageDirection');
                let stageDirection = 'up'; // default: stage at top
                if (stageDiv) {{
                    const classList = stageDiv.classList;
                    if (classList.contains('topright')) stageDirection = 'up';       // topright -> up (主要方向)
                    else if (classList.contains('topleft')) stageDirection = 'up';   // topleft -> up (主要方向)
                    else if (classList.contains('downright')) stageDirection = 'down';
                    else if (classList.contains('downleft')) stageDirection = 'down';
                    else if (classList.contains('top')) stageDirection = 'up';
                    else if (classList.contains('down')) stageDirection = 'down';
                    else if (classList.contains('left')) stageDirection = 'left';
                    else if (classList.contains('right')) stageDirection = 'right';
                }}

                if (showDebug) {{
                    console.log('[KHAM SEAT] Stage direction: ' + stageDirection);
                }}

                // Row labels seen on kham: "17排", "H排", and "B31排" (a letter
                // prefix on a numbered row, 6樓包廂 at 台北國際會議中心). Letter-only and
                // number-only rows keep their old values (H -> 8, 17 -> 17); a
                // prefixed row sorts as a block per letter (B31 -> 2031).
                function parseRowNum(rowText) {{
                    const m = rowText.match(/([A-Z]*)(\d*)排/);
                    if (!m || (!m[1] && !m[2])) return NaN;
                    let letters = 0;
                    for (let i = 0; i < m[1].length; i++) {{
                        letters = letters * 26 + (m[1].charCodeAt(i) - 64);
                    }}
                    return m[2] ? letters * 1000 + parseInt(m[2]) : letters;
                }}

                // Step 1: Find all available seats with REAL DOM positions
                // Important: Use real TD column index, not filtered array index
                // This correctly handles spacer TDs (<td>&nbsp;</td>) that represent aisles/separators
                const allTableRows = document.querySelectorAll('table#TBL tr');
                let totalAvailableSeats = 0;

                // First pass: count available seats
                Array.from(allTableRows).forEach(tr => {{
                    const allTds = tr.children;
                    for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                        if (isEmptySeat(allTds[colIndex])) {{
                            totalAvailableSeats++;
                        }}
                    }}
                }});

                if (showDebug) {{
                    console.log('[KHAM SEAT] Found ' + totalAvailableSeats + ' available seats');
                }}

                if (totalAvailableSeats < ticketNumber) {{
                    return {{ success: false, found: totalAvailableSeats, selected: 0, direction: stageDirection }};
                }}

                const selectedSeats = [];
                let usedFallback = false;

                // Step 2-4: Group, sort and select based on stage direction
                if (stageDirection === 'up' || stageDirection === 'down') {{
                    // GROUP BY ROW: For top/bottom stages
                    const rows = {{}};
                    // Map position (table row index) of each row label: the
                    // labels can run either way (中信特攻: I排 drawn above E排
                    // with the court below), so front/back follows the map.
                    const rowPos = {{}};

                    // Second pass: collect seats with REAL DOM column index
                    Array.from(allTableRows).forEach((tr, trIndex) => {{
                        const allTds = tr.children;
                        for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                            const seat = allTds[colIndex];
                            if (isEmptySeat(seat)) {{
                                const title = seat.getAttribute('title');
                                if (title && title.includes('排') && title.includes('號')) {{
                                    const parts = title.split('-');
                                    if (parts.length >= 3) {{
                                        const rowNum = parseRowNum(parts[1]); // e.g. "H排", "17排", "B31排"
                                        const seatNum = parseInt(parts[2].replace('號', ''));
                                        if (!isNaN(rowNum) && !isNaN(seatNum)) {{
                                            if (!rows[rowNum]) rows[rowNum] = [];
                                            if (!(rowNum in rowPos)) rowPos[rowNum] = trIndex;
                                            // CRITICAL: Use REAL colIndex as domIndex, not filtered array index
                                            rows[rowNum].push({{ elem: seat, num: seatNum, title: title, domIndex: colIndex }});
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }});

                    // Helper function: calculate row quality score
                    function calculateRowQuality(rowSeats) {{
                        const totalSeats = rowSeats.length;

                        // Find max continuous segment (based on DOM index, not seat number)
                        let maxContinuous = 1;
                        let currentContinuous = 1;
                        for (let i = 0; i < rowSeats.length - 1; i++) {{
                            const domGap = rowSeats[i + 1].domIndex - rowSeats[i].domIndex;
                            if (domGap === 1) {{
                                // Adjacent in DOM
                                currentContinuous++;
                                maxContinuous = Math.max(maxContinuous, currentContinuous);
                            }} else {{
                                // Gap found
                                currentContinuous = 1;
                            }}
                        }}

                        // Calculate middle seats ratio (seats in middle 50%)
                        const middleStart = Math.floor(totalSeats * 0.25);
                        const middleEnd = Math.floor(totalSeats * 0.75);
                        let middleCount = 0;
                        rowSeats.forEach((seat, idx) => {{
                            if (idx >= middleStart && idx < middleEnd) middleCount++;
                        }});
                        const middleRatio = middleCount / Math.max(1, (middleEnd - middleStart));

                        // Three-tier quality score:
                        // 1. Priority: has enough continuous seats
                        // 2. Priority: middle seat ratio
                        // 3. Priority: stage direction preference
                        return {{
                            maxContinuous: maxContinuous,
                            totalSeats: totalSeats,
                            middleRatio: middleRatio,
                            score: (maxContinuous >= ticketNumber) ? 100 + middleRatio : maxContinuous + middleRatio
                        }};
                    }}

                    // Sort rows by stage direction: rows drawn nearest the stage first
                    const sortedRows = Object.entries(rows).sort((a, b) => stageDirection === 'up'
                        ? rowPos[a[0]] - rowPos[b[0]]    // stage at top: topmost row first
                        : rowPos[b[0]] - rowPos[a[0]]);  // stage at bottom: lowest row first

                    // Select seats from front rows
                    for (const [rowNum, rowSeats] of sortedRows) {{
                        // Sort by DOM order (domIndex) to maintain continuity
                        rowSeats.sort((a, b) => a.domIndex - b.domIndex);
                        const seatCount = rowSeats.length;

                        if (allowNonAdjacent) {{
                            // Non-adjacent mode: select from middle of row
                            const startIdx = Math.max(0, Math.floor((seatCount - ticketNumber) / 2));
                            for (let i = 0; i < Math.min(ticketNumber, seatCount); i++) {{
                                if (startIdx + i < rowSeats.length) {{
                                    selectedSeats.push(rowSeats[startIdx + i]);
                                    if (selectedSeats.length >= ticketNumber) break;
                                }}
                            }}
                        }} else {{
                            // Adjacent mode: find continuous seats in DOM order
                            for (let startIdx = 0; startIdx <= rowSeats.length - ticketNumber; startIdx++) {{
                                let continuous = true;
                                for (let i = 0; i < ticketNumber - 1; i++) {{
                                    const currentDomIdx = rowSeats[startIdx + i].domIndex;
                                    const nextDomIdx = rowSeats[startIdx + i + 1].domIndex;
                                    const domGap = nextDomIdx - currentDomIdx;
                                    if (domGap > 1) {{
                                        // Seats are not adjacent in DOM
                                        continuous = false;
                                        break;
                                    }}
                                }}

                                if (continuous) {{
                                    for (let i = 0; i < ticketNumber; i++) {{
                                        selectedSeats.push(rowSeats[startIdx + i]);
                                    }}
                                    break;
                                }}
                            }}
                        }}

                        if (selectedSeats.length >= ticketNumber) break;
                    }}

                    // Non-adjacent fallback: adjacent mode found 0 seats but seats exist
                    if (!allowNonAdjacent && selectedSeats.length < ticketNumber) {{
                        for (const [rowNum, rowSeats] of sortedRows) {{
                            rowSeats.sort((a, b) => a.domIndex - b.domIndex);
                            const seatCount = rowSeats.length;
                            const startIdx = Math.max(0, Math.floor((seatCount - ticketNumber) / 2));
                            for (let i = 0; i < seatCount; i++) {{
                                if (startIdx + i < rowSeats.length) {{
                                    selectedSeats.push(rowSeats[startIdx + i]);
                                    if (selectedSeats.length >= ticketNumber) break;
                                }}
                            }}
                            if (selectedSeats.length >= ticketNumber) break;
                        }}
                        if (selectedSeats.length >= ticketNumber) usedFallback = true;
                    }}

                }} else if (stageDirection === 'left' || stageDirection === 'right') {{
                    // GROUP BY SEAT NUMBER (column): For left/right stages
                    const columns = {{}};

                    // Collect seats with REAL DOM row index (for vertical continuity check)
                    let rowIndexInTable = 0;
                    Array.from(allTableRows).forEach(tr => {{
                        const allTds = tr.children;
                        for (let colIndex = 0; colIndex < allTds.length; colIndex++) {{
                            const seat = allTds[colIndex];
                            if (isEmptySeat(seat)) {{
                                const title = seat.getAttribute('title');
                                if (title && title.includes('排') && title.includes('號')) {{
                                    const parts = title.split('-');
                                    if (parts.length >= 3) {{
                                        const rowNum = parseRowNum(parts[1]); // e.g. "H排", "17排", "B31排"
                                        const seatNum = parseInt(parts[2].replace('號', ''));
                                        if (!isNaN(rowNum) && !isNaN(seatNum)) {{
                                            if (!columns[seatNum]) columns[seatNum] = [];
                                            // CRITICAL: Use real rowIndexInTable as domIndex for vertical continuity
                                            columns[seatNum].push({{ elem: seat, rowNum: rowNum, title: title, domIndex: rowIndexInTable }});
                                        }}
                                    }}
                                }}
                            }}
                        }}
                        rowIndexInTable++;
                    }});

                    // Sort columns by stage direction
                    let sortedColumns;
                    if (stageDirection === 'left') {{
                        // Stage at left: smaller seat number = closer to stage
                        sortedColumns = Object.entries(columns).sort((a, b) => parseInt(a[0]) - parseInt(b[0]));
                    }} else {{
                        // Stage at right: larger seat number = closer to stage
                        sortedColumns = Object.entries(columns).sort((a, b) => parseInt(b[0]) - parseInt(a[0]));
                    }}

                    // Select seats from front columns
                    for (const [seatNum, columnSeats] of sortedColumns) {{
                        // Sort by DOM order (domIndex) to maintain continuity
                        columnSeats.sort((a, b) => a.domIndex - b.domIndex);
                        const rowCount = columnSeats.length;

                        if (allowNonAdjacent) {{
                            // Non-adjacent mode: select from middle rows of column
                            const startIdx = Math.max(0, Math.floor((rowCount - ticketNumber) / 2));
                            for (let i = 0; i < Math.min(ticketNumber, rowCount); i++) {{
                                if (startIdx + i < columnSeats.length) {{
                                    selectedSeats.push(columnSeats[startIdx + i]);
                                    if (selectedSeats.length >= ticketNumber) break;
                                }}
                            }}
                        }} else {{
                            // Adjacent mode: find continuous rows in DOM order (not by row number)
                            for (let startIdx = 0; startIdx <= columnSeats.length - ticketNumber; startIdx++) {{
                                let continuous = true;
                                for (let i = 0; i < ticketNumber - 1; i++) {{
                                    const currentDomIdx = columnSeats[startIdx + i].domIndex;
                                    const nextDomIdx = columnSeats[startIdx + i + 1].domIndex;
                                    const domGap = nextDomIdx - currentDomIdx;
                                    if (domGap > 1) {{
                                        // Seats are not adjacent in DOM
                                        continuous = false;
                                        break;
                                    }}
                                }}

                                if (continuous) {{
                                    for (let i = 0; i < ticketNumber; i++) {{
                                        selectedSeats.push(columnSeats[startIdx + i]);
                                    }}
                                    break;
                                }}
                            }}
                        }}

                        if (selectedSeats.length >= ticketNumber) break;
                    }}

                    // Non-adjacent fallback: adjacent mode found 0 seats but seats exist
                    if (!allowNonAdjacent && selectedSeats.length < ticketNumber) {{
                        for (const [seatNum, columnSeats] of sortedColumns) {{
                            columnSeats.sort((a, b) => a.domIndex - b.domIndex);
                            const rowCount = columnSeats.length;
                            const startIdx = Math.max(0, Math.floor((rowCount - ticketNumber) / 2));
                            for (let i = 0; i < rowCount; i++) {{
                                if (startIdx + i < columnSeats.length) {{
                                    selectedSeats.push(columnSeats[startIdx + i]);
                                    if (selectedSeats.length >= ticketNumber) break;
                                }}
                            }}
                            if (selectedSeats.length >= ticketNumber) break;
                        }}
                        if (selectedSeats.length >= ticketNumber) usedFallback = true;
                    }}
                }}

                // Step 5: Click selected seats
                let clickedCount = 0;
                const clickedTitles = [];
                for (const seat of selectedSeats.slice(0, ticketNumber)) {{
                    // 寬宏座位點擊後會改變 class（empty -> 其他狀態）
                    seat.elem.click();
                    clickedCount++;
                    clickedTitles.push(seat.title);
                }}

                return {{
                    success: clickedCount > 0,
                    found: totalAvailableSeats,
                    selected: clickedCount,
                    titles: clickedTitles,
                    direction: stageDirection,
                    usedFallback: usedFallback
                }};
            }})();
        ''')

        # 轉換 CDP 格式為 Python dict
        result_dict = {}
        if isinstance(result, list):
            for item in result:
                if isinstance(item, list) and len(item) == 2:
                    key = item[0]
                    val_obj = item[1]
                    if isinstance(val_obj, dict) and 'value' in val_obj:
                        value = val_obj['value']
                        # 處理陣列類型
                        if val_obj.get('type') == 'array' and isinstance(value, list):
                            result_dict[key] = [
                                v.get('value') if isinstance(v, dict) else v
                                for v in value
                            ]
                        else:
                            result_dict[key] = value
        elif isinstance(result, dict):
            result_dict = result

        is_seat_assigned = result_dict.get('success', False)

        if result_dict.get('usedFallback'):
            debug.log("[KHAM SEAT] Adjacent seats not available, used non-adjacent fallback")

        if debug.enabled:
            stage_dir = result_dict.get('direction', 'unknown')
            debug.log(f"[KHAM SEAT] Stage direction: {stage_dir}")
            debug.log(f"[KHAM SEAT] Found {result_dict.get('found', 0)} available seats")
            debug.log(f"[KHAM SEAT] Selected {result_dict.get('selected', 0)}/{ticket_number} seats")
            if result_dict.get('titles'):
                for title in result_dict['titles']:
                    debug.log(f"[SUCCESS] Selected seat: {title}")

    except Exception as exc:
        debug.log(f"[ERROR] KHAM seat selection error: {exc}")
        import traceback
        if debug.enabled:
            traceback.print_exc()

    return is_seat_assigned


# 追蹤每個票區的刷新次數：{area_key: reload_count}
KHAM_SEAT_RELOAD_TRACKER = {}


def clear_kham_seat_reload_tracker(url):
    """清除指定票區或網址的刷新次數記錄"""
    global KHAM_SEAT_RELOAD_TRACKER
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(str(url))
        params = urllib.parse.parse_qs(parsed.query)
        area_id = params.get("PERFORMANCE_PRICE_AREA_ID", [None])[0]
        if area_id:
            KHAM_SEAT_RELOAD_TRACKER.pop(area_id, None)
        KHAM_SEAT_RELOAD_TRACKER.pop(str(url), None)
    except Exception:
        pass


async def nodriver_kham_handle_seat_unavailable(tab, config_dict):
    """
    寬宏售票 UTK0205 座位頁面空位不足時的處理機制：
    1. 首次發現無位時：最多點擊「更新座位」(.btn-update / top.location.reload) 刷新一次。
    2. 刷新一次後仍無位時：返回票區列表（UTK0204）重新找其他位置。
    """
    global KHAM_SEAT_RELOAD_TRACKER
    debug = util.create_debug_logger(config_dict)

    current_url = str(tab.target.url) if tab and tab.target else ""
    area_key = current_url

    # 嘗試由網址取得 PERFORMANCE_PRICE_AREA_ID
    import urllib.parse
    try:
        parsed_url = urllib.parse.urlparse(current_url)
        params = urllib.parse.parse_qs(parsed_url.query)
        area_id = params.get("PERFORMANCE_PRICE_AREA_ID", [None])[0]
        if area_id:
            area_key = area_id
    except Exception:
        pass

    # 取得票區名稱以利記錄日誌
    area_name = ""
    try:
        name_res = await tab.evaluate('''
            (() => {
                const el = document.querySelector('#AREA_NAME');
                return el ? el.textContent.trim() : '';
            })()
        ''')
        parsed_name = util.parse_nodriver_result(name_res)
        if isinstance(parsed_name, str):
            area_name = parsed_name
    except Exception:
        pass

    area_display = f"「{area_name}」({area_key})" if area_name else f"({area_key})"
    reload_count = KHAM_SEAT_RELOAD_TRACKER.get(area_key, 0)

    if reload_count == 0:
        # 第一次發現空位不足：最多更新座位刷新一次
        KHAM_SEAT_RELOAD_TRACKER[area_key] = 1
        debug.log(f"[KHAM SEAT] 票區 {area_display} 可用空位不足，嘗試點擊「更新座位」刷新一次...")

        try:
            await tab.evaluate('''
                (() => {
                    const btnUpdate = document.querySelector('.btn-update, div[onclick*="reload"]');
                    if (btnUpdate) {
                        btnUpdate.click();
                        return { success: true, method: 'btn_click' };
                    }
                    if (typeof top !== 'undefined' && top.location) {
                        top.location.reload();
                        return { success: true, method: 'top_reload' };
                    }
                    window.location.reload();
                    return { success: true, method: 'window_reload' };
                })()
            ''')
        except Exception as exc:
            debug.log(f"[KHAM SEAT] 更新座位失敗: {exc}")

        # Floor the wait as the area page does: a seat map read before the
        # reload finishes is empty and would abandon an area that has seats.
        reload_interval = config_views.reload_interval(config_dict, default=0.1)
        wait_sec = max(reload_interval, CONST_KHAM_RELOAD_SETTLE)
        await asyncio_sleep_with_pause_check(wait_sec)
        return True

    else:
        # 刷新一次後依然無位：切換票區重新尋找其他位置
        KHAM_SEAT_RELOAD_TRACKER.pop(area_key, None)
        debug.log(f"[KHAM SEAT] 票區 {area_display} 刷新一次後仍無空位，返回票區列表重新找其他位置...")

        try:
            # The 切換票區 button calls showPic(), which only returns to the
            # previous page on some events; elsewhere it just opens the area
            # map over the same seat page, and the next round lands on the same
            # area again (seen 2026-09-24, 6樓包廂). Go back to the area list
            # (UTK0204) the way showPic does, so area selection picks again
            # under the user's area keywords. Without history, fall back to it.
            await tab.evaluate('''
                (() => {
                    if (window.history.length > 1) {
                        top.history.back();
                        return 'history_back';
                    }
                    if (typeof showPic === 'function') {
                        showPic();
                        return 'showPic';
                    }
                    return 'none';
                })()
            ''')
        except Exception as exc:
            debug.log(f"[KHAM SEAT] 點擊切換票區失敗: {exc}")

        await asyncio_sleep_with_pause_check(0.8)
        return True


async def _kham_seat_map_loaded(tab):
    """True once #TBL has been parsed and holds seat cells.

    A seat map that is still parsing reads as "no seats", the same as a full
    one. Only a parsed map that still yields no seats should reach
    nodriver_kham_handle_seat_unavailable.
    """
    try:
        result = await tab.evaluate('''
            (() => document.readyState !== 'loading' &&
                   document.querySelectorAll('table#TBL td').length > 0)()
        ''')
        return result is True
    except Exception:
        return False


async def nodriver_kham_seat_main(tab, config_dict, ocr, domain_name):
    """
    寬宏售票座位選擇主流程：票別選擇 -> 座位選擇 -> 驗證碼 -> 提交
    UTK0205 頁面處理
    """
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)
    ticket_number = config_dict["ticket_number"]

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

        debug.log(f"[KHAM SEAT] Already selected seats: {already_selected_count}")
    except Exception as exc:
        debug.log(f"[KHAM SEAT] Error checking selected seats: {exc}")

    # If already selected enough seats, skip seat selection and go to submit
    if already_selected_count >= ticket_number:
        debug.log(f"[KHAM SEAT] Already have {already_selected_count} seats (need {ticket_number}), skipping to submit")
        is_seat_type_assigned = True
        is_seat_assigned = True
        clear_kham_seat_reload_tracker(tab.target.url)
    else:
        # Step 1: Select seat type
        area_config = config_views.area_view(config_dict)
        area_keyword = area_config["keyword"].strip()
        is_seat_type_assigned = await nodriver_kham_seat_type_auto_select(
            tab, config_dict, area_keyword
        )

        # Step 2: Select seats
        is_seat_assigned = False
        if is_seat_type_assigned:
            is_seat_assigned = await nodriver_kham_seat_auto_select(
                tab, config_dict, rwd=is_utk_rwd_url(domain_name))

        if not is_seat_assigned:
            # Refresh-then-switch-area only for a loaded page whose area cannot
            # be bought; no seat type buttons or #TBL still parsing means
            # mid-load, so retry next pass. UDN is excluded: the handler's
            # selectors and history.back() fallback were only verified on kham.
            types_exhausted = _state.get("kham_seat_types_exhausted", False)
            page_ready = types_exhausted or (
                is_seat_type_assigned and await _kham_seat_map_loaded(tab))
            if 'udnfunlife' not in domain_name and page_ready:
                await nodriver_kham_handle_seat_unavailable(tab, config_dict)
            return False
        else:
            clear_kham_seat_reload_tracker(tab.target.url)

    # Step 3: Handle captcha (reuse KHAM OCR)
    is_captcha_sent = False
    if is_seat_assigned and config_dict["ocr_captcha"]["enable"]:
        try:
            # Find captcha input field
            captcha_input = await tab.query_selector('input#CHK')
            debug.log(f"[KHAM SEAT] Captcha input found: {captcha_input is not None}")
            if captcha_input:
                model_name = "UTK0205"
                is_captcha_sent = await nodriver_kham_captcha(
                    tab, config_dict, ocr, model_name
                )
                debug.log(f"[KHAM SEAT] is_captcha_sent: {is_captcha_sent}")
        except Exception as exc:
            debug.log(f"[ERROR] KHAM captcha processing error: {exc}")

    # Step 4: Submit order with improved dialog handling and URL tracking
    is_submit_success = False
    if is_seat_assigned and (not config_dict["ocr_captcha"]["enable"] or is_captcha_sent):
        try:
            # Baseline for the transition check below, taken before the click:
            # kham often redirects to UTK0206 while the reply dialog is still
            # being polled, and a baseline read after that never sees a change.
            pre_submit_url = tab.target.url

            # 4.1: Click submit button - UTK0205 uses addShoppingCart() function
            result = await tab.evaluate('''
                (function() {
                    // Method 1: Try calling addShoppingCart() directly (most reliable)
                    if (typeof addShoppingCart === 'function') {
                        addShoppingCart();
                        return true;
                    }
                    // Method 2: Find button inside the addcart anchor
                    const addcartBtn = document.querySelector('a#addcart button');
                    if (addcartBtn && !addcartBtn.disabled) {
                        addcartBtn.click();
                        return true;
                    }
                    // Method 3: Find button with onclick containing addShoppingCart
                    const btnWithOnclick = document.querySelector('button[onclick*="addShoppingCart"]');
                    if (btnWithOnclick && !btnWithOnclick.disabled) {
                        btnWithOnclick.click();
                        return true;
                    }
                    // Method 4: Legacy selector for other KHAM pages
                    const button = document.querySelector('button.sumitButton');
                    if (button && !button.disabled) {
                        button.click();
                        return true;
                    }
                    return false;
                })();
            ''')

            # Convert CDP format (boolean)
            if isinstance(result, list) and len(result) == 2:
                is_submit_success = result[1].get('value', False) if isinstance(result[1], dict) else result
            else:
                is_submit_success = result

            if is_submit_success:
                debug.log("[KHAM SUBMIT] Add-to-cart clicked, reading the reply dialog")

                # 4.2: Classify the reply. UTK0205 adds to cart over AJAX and kham
                # answers with JavaScript that either redirects to UTK0206 or opens
                # a dialog; same helper as the two submit paths in nodriver_kham_main.
                dialog_result = await _handle_post_submit_dialog(tab, config_dict)

                if dialog_result == "error":
                    # The helper cleared the captcha and kham issued a new image;
                    # end the round so the next pass re-runs OCR right away.
                    is_submit_success = False
                    debug.log("[KHAM SUBMIT] Submit refused; captcha cleared, "
                              "retrying on the next round")
                else:
                    # 4.3: Always check for page transition (fallback) - regardless of dialog detection
                    # This is more reliable than waiting for dialog to close
                    debug.log("[KHAM SUBMIT] Checking for page transition (fallback)...")

                    debug.log(f"[KHAM SUBMIT] URL before submit: {pre_submit_url}")

                    # Check if URL changed (maximum 15 seconds wait - more generous fallback)
                    url_changed = False
                    for i in range(30):  # 30 attempts * 0.5s = 15 seconds (extended from 10s)
                        new_url = tab.target.url
                        if new_url != pre_submit_url:
                            debug.log(f"[KHAM SUBMIT] Page transitioned successfully")
                            debug.log(f"[KHAM SUBMIT] New URL: {new_url}")
                            url_changed = True
                            break
                        await tab.sleep(0.5)

                    if not url_changed:
                        debug.log("[KHAM SUBMIT] URL did not change after submit")
                        debug.log("[KHAM SUBMIT] Note: KHAM may not auto-redirect - this may be normal")
                        debug.log("[KHAM SUBMIT] Proceeding anyway as submit button was clicked")

                    # 4.4: Play sound if enabled
                    if config_dict["advanced"]["play_sound"]["order"]:
                        play_sound_while_ordering(config_dict)

        except Exception as exc:
            debug.log(f"[ERROR] KHAM submit exception: {exc}")
            # Fallback: use JavaScript to force submit
            try:
                await tab.evaluate('addShoppingCart();')
                is_submit_success = True
                if config_dict["advanced"]["play_sound"]["order"]:
                    play_sound_while_ordering(config_dict)
                debug.log("[KHAM SUBMIT] Submitted via fallback method")
            except Exception as exc2:
                debug.log(f"[ERROR] KHAM fallback submit error: {exc2}")

    debug.log(f"[KHAM SEAT MAIN] Type:{is_seat_type_assigned} "
          f"Seat:{is_seat_assigned} Submit:{is_submit_success}")

    return is_submit_success
