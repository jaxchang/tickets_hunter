#encoding=utf-8
# =============================================================================
# KHAM UDN Seat Module
# The UDN (tickets.udnfunlife.com) seat selection flow (issue #44,
# ADR 0006), split out of platforms/kham.py. Self-contained: it imports
# only the common layers, not the facade or the shared layer.
# =============================================================================

import json
import traceback

import util
import config_views


async def nodriver_udn_seat_auto_select(tab, config_dict):
    """
    UDN - UTK0204 座位自動選擇

    UDN 的 UTK0204 頁面同時包含區域選擇和座位地圖。
    點擊區域後，座位地圖會在同一頁面顯示。

    座位元素結構（透過 MCP 測試驗證）：
    - 選擇器：td[title*="排"]
    - 格式：{區域}-{排號}排-{座號}號（如：特B區-10排-19號）
    - 可選座位：background-image 包含 icon_chair_empty_1.gif
    - 已售座位：background-image 包含 icon_chair_sale_1.gif
    """
    is_seat_assigned = False
    ticket_number = config_views.ticket_number(config_dict)
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    try:
        import json
        result = await tab.evaluate(f'''
            (function() {{
                const ticketNumber = {ticket_number};
                const showDebug = {json.dumps(show_debug)};

                // Step 1: Find all seat elements with title containing "排"
                const allSeats = document.querySelectorAll('td[title*="排"]');
                if (allSeats.length === 0) {{
                    if (showDebug) console.log('[UDN SEAT] No seat map found');
                    return {{ success: false, reason: 'no_seat_map', found: 0, selected: 0 }};
                }}

                // Step 2: Filter available seats by background image
                const availableSeats = [];
                allSeats.forEach(seat => {{
                    const style = seat.getAttribute('style') || '';
                    if (style.includes('icon_chair_empty_1.gif')) {{
                        const title = seat.getAttribute('title');
                        if (title && title.includes('排') && title.includes('號')) {{
                            // Parse seat info: {{區域}}-{{排號}}排-{{座號}}號
                            const parts = title.split('-');
                            if (parts.length >= 3) {{
                                const areaName = parts[0];
                                const rowMatch = parts[1].match(/(\d+)排/);
                                const seatMatch = parts[2].match(/(\d+)號/);
                                if (rowMatch && seatMatch) {{
                                    availableSeats.push({{
                                        element: seat,
                                        title: title,
                                        area: areaName,
                                        row: parseInt(rowMatch[1]),
                                        seat: parseInt(seatMatch[1])
                                    }});
                                }}
                            }}
                        }}
                    }}
                }});

                if (showDebug) {{
                    console.log('[UDN SEAT] Total seats: ' + allSeats.length);
                    console.log('[UDN SEAT] Available seats: ' + availableSeats.length);
                }}

                if (availableSeats.length === 0) {{
                    return {{ success: false, reason: 'no_available_seats', found: allSeats.length, selected: 0 }};
                }}

                if (availableSeats.length < ticketNumber) {{
                    return {{ success: false, reason: 'not_enough_seats', found: availableSeats.length, needed: ticketNumber, selected: 0 }};
                }}

                // Step 3: Sort by row (ascending) then by seat number (prefer middle)
                // Strategy: Front rows first, middle seats preferred
                availableSeats.sort((a, b) => {{
                    if (a.row !== b.row) return a.row - b.row;
                    // For same row, calculate distance from middle
                    // Assume middle seat is around 25 (based on typical venue layout)
                    const midSeat = 25;
                    const distA = Math.abs(a.seat - midSeat);
                    const distB = Math.abs(b.seat - midSeat);
                    return distA - distB;
                }});

                // Step 4: Select seats (up to ticketNumber)
                const selectedSeats = [];
                for (let i = 0; i < Math.min(ticketNumber, availableSeats.length); i++) {{
                    const seatInfo = availableSeats[i];
                    seatInfo.element.click();
                    selectedSeats.push(seatInfo.title);
                    if (showDebug) {{
                        console.log('[UDN SEAT] Clicked seat: ' + seatInfo.title);
                    }}
                }}

                return {{
                    success: true,
                    found: availableSeats.length,
                    selected: selectedSeats.length,
                    seats: selectedSeats
                }};
            }})();
        ''')

        if result and result.get('success'):
            is_seat_assigned = True
            debug.log(f"[UDN SEAT] Selected {result.get('selected')} seats: {result.get('seats')}")
        else:
            if debug.enabled:
                reason = result.get('reason', 'unknown') if result else 'no_result'
                debug.log(f"[UDN SEAT] Selection failed: {reason}")

    except Exception as exc:
        if debug.enabled:
            debug.log(f"[ERROR] UDN seat selection error: {exc}")
            import traceback
            traceback.print_exc()

    return is_seat_assigned


async def nodriver_udn_seat_select_ticket_type(tab, config_dict):
    """
    UDN - 選擇票種並加入購物車

    點擊座位後，頁面會顯示：
    1. 已選座位資訊（如：特B區-10排-19號）
    2. 票種選擇 combobox
    3. 「加入購物車 Add to Cart」按鈕
    """
    is_success = False
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)

    try:
        import json
        result = await tab.evaluate(f'''
            (function() {{
                const showDebug = {json.dumps(show_debug)};

                // Step 1: Find ticket type combobox
                // UDN uses <select> element for ticket type selection
                const comboboxes = document.querySelectorAll('select');
                let ticketTypeSelect = null;

                for (const select of comboboxes) {{
                    const options = select.querySelectorAll('option');
                    for (const opt of options) {{
                        // Look for price pattern like "全票-NT$1,880"
                        if (opt.textContent.includes('NT$') || opt.textContent.includes('票')) {{
                            ticketTypeSelect = select;
                            break;
                        }}
                    }}
                    if (ticketTypeSelect) break;
                }}

                if (!ticketTypeSelect) {{
                    if (showDebug) console.log('[UDN TICKET] No ticket type select found');
                    return {{ success: false, reason: 'no_ticket_select' }};
                }}

                // Step 2: Select first valid ticket type (skip "請選擇" placeholder)
                const options = ticketTypeSelect.querySelectorAll('option');
                let selectedOption = null;
                for (const opt of options) {{
                    if (opt.textContent.includes('NT$') && !opt.textContent.includes('請選擇')) {{
                        opt.selected = true;
                        ticketTypeSelect.value = opt.value;
                        // Trigger change event
                        ticketTypeSelect.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        selectedOption = opt.textContent;
                        break;
                    }}
                }}

                if (!selectedOption) {{
                    if (showDebug) console.log('[UDN TICKET] No valid ticket type option found');
                    return {{ success: false, reason: 'no_valid_option' }};
                }}

                if (showDebug) {{
                    console.log('[UDN TICKET] Selected ticket type: ' + selectedOption);
                }}

                // Step 3: Find and click "加入購物車" button
                const buttons = document.querySelectorAll('button, input[type="button"], input[type="submit"]');
                let addToCartBtn = null;
                for (const btn of buttons) {{
                    const text = btn.textContent || btn.value || '';
                    if (text.includes('加入購物車') || text.includes('Add to Cart')) {{
                        addToCartBtn = btn;
                        break;
                    }}
                }}

                if (!addToCartBtn) {{
                    if (showDebug) console.log('[UDN TICKET] No add to cart button found');
                    return {{ success: false, reason: 'no_cart_button', ticketType: selectedOption }};
                }}

                // Click the button
                addToCartBtn.click();
                if (showDebug) {{
                    console.log('[UDN TICKET] Clicked add to cart button');
                }}

                return {{
                    success: true,
                    ticketType: selectedOption,
                    buttonClicked: true
                }};
            }})();
        ''')

        if result and result.get('success'):
            is_success = True
            debug.log(f"[UDN TICKET] Added to cart: {result.get('ticketType')}")

            # Wait for dialog and dismiss it
            await tab.sleep(0.5)
            try:
                dialog_result = await tab.evaluate('''
                    (function() {
                        // Find dialog with "完成加入購物車" message
                        const dialogs = document.querySelectorAll('[role="dialog"], .ui-dialog');
                        for (const dialog of dialogs) {
                            const text = dialog.textContent || '';
                            if (text.includes('完成加入購物車') || text.includes('購物車')) {
                                // Find OK button
                                const buttons = dialog.querySelectorAll('button');
                                for (const btn of buttons) {
                                    const btnText = btn.textContent || '';
                                    if (btnText.includes('Ok') || btnText.includes('確定') || btnText === 'Ok') {
                                        btn.click();
                                        return { dismissed: true };
                                    }
                                }
                            }
                        }
                        return { dismissed: false };
                    })();
                ''')
                if dialog_result:
                    debug.log(f"[UDN TICKET] Dialog dismissed: {dialog_result.get('dismissed')}")
            except Exception as dialog_exc:
                debug.log(f"[UDN TICKET] Dialog dismiss error (may be normal): {dialog_exc}")

        else:
            if debug.enabled:
                reason = result.get('reason', 'unknown') if result else 'no_result'
                debug.log(f"[UDN TICKET] Failed: {reason}")

    except Exception as exc:
        if debug.enabled:
            debug.log(f"[ERROR] UDN ticket type selection error: {exc}")
            import traceback
            traceback.print_exc()

    return is_success


async def nodriver_udn_seat_main(tab, config_dict):
    """
    UDN UTK0204 座位選擇主流程

    流程（透過 MCP 測試驗證）：
    1. 偵測座位地圖是否存在
    2. 選擇可用座位
    3. 選擇票種
    4. 加入購物車
    5. 處理確認對話框

    Returns:
        bool: True if successfully added to cart
    """
    show_debug = config_dict["advanced"].get("verbose", False)
    debug = util.create_debug_logger(enabled=show_debug)
    is_success = False

    # Check if seat map is present
    try:
        seat_map_check_raw = await tab.evaluate('''
            (function() {
                const seats = document.querySelectorAll('td[title*="排"]');
                const availableSeats = Array.from(seats).filter(s => {
                    const style = s.getAttribute('style') || '';
                    return style.includes('icon_chair_empty_1.gif');
                });
                return {
                    hasSeatMap: seats.length > 0,
                    totalSeats: seats.length,
                    availableSeats: availableSeats.length
                };
            })();
        ''')
        seat_map_check = util.parse_nodriver_result(seat_map_check_raw)

        if not seat_map_check or not seat_map_check.get('hasSeatMap'):
            debug.log("[UDN SEAT MAIN] No seat map detected on this page")
            return False

        debug.log(f"[UDN SEAT MAIN] Seat map found: {seat_map_check.get('totalSeats')} total, "
              f"{seat_map_check.get('availableSeats')} available")

        if seat_map_check.get('availableSeats', 0) == 0:
            debug.log("[UDN SEAT MAIN] No available seats")
            return False

    except Exception as exc:
        debug.log(f"[UDN SEAT MAIN] Error checking seat map: {exc}")
        return False

    # Step 1: Select seats
    is_seat_selected = await nodriver_udn_seat_auto_select(tab, config_dict)

    if not is_seat_selected:
        debug.log("[UDN SEAT MAIN] Seat selection failed")
        return False

    # Wait for UI to update after seat selection
    await tab.sleep(0.3)

    # Step 2: Select ticket type and add to cart
    is_success = await nodriver_udn_seat_select_ticket_type(tab, config_dict)

    debug.log(f"[UDN SEAT MAIN] Result: seat_selected={is_seat_selected}, added_to_cart={is_success}")

    return is_success
