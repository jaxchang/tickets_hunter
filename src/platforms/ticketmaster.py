#encoding=utf-8
# =============================================================================
# Ticketmaster Module
# The Ticketmaster family of the tixcraft platform (issue #48, ADR 0006),
# split out of platforms/tixcraft.py. Imports only the shared layer
# (platforms.tixcraft_shared) and the common layers; the family stays
# routed by nodriver_tixcraft_main as one registry entry.
# =============================================================================

import asyncio
import json
import os
import random
import re
import time
from zendriver import cdp
import util
import date_selection
import config_views
from nodriver_common import (
    CONST_MAXBOT_INT28_FILE,
    check_and_handle_pause,
    convert_remote_object,
    nodriver_check_checkbox,
    sleep_with_pause_check,
)
from platforms.tixcraft_shared import (
    _is_page_reload_enabled,
    _resolve_soft_block_wait_seconds,
    _should_leave_dialog_to_user,
    _state,
    nodriver_tixcraft_auto_ocr,
    nodriver_tixcraft_input_check_code,
    nodriver_tixcraft_keyin_captcha_code,
    nodriver_tixcraft_reload_captcha,
)


# Seconds the Ticketmaster check-captcha handler waits for the captcha input:
# the URL flips before the form renders, and nodriver_tixcraft_auto_ocr reads a
# missing input as "no captcha" and ends the OCR loop on the first pass.
CONST_TICKETMASTER_CAPTCHA_WAIT_SEC = 6.0


# T004: Parse zone_info JSON from #mapSelectArea
async def nodriver_ticketmaster_parse_zone_info(tab, config_dict):
    """
    Parse zone_info JavaScript variable from #mapSelectArea element.
    Returns: zone_info dict or None if parsing fails
    """
    debug = util.create_debug_logger(config_dict)

    zone_info = None

    # Try method 1: String extraction from innerHTML (get_attribute('innerHTML') returns None
    # because innerHTML is a DOM property, not an HTML attribute — use evaluate instead)
    try:
        mapSelectArea_html = await tab.evaluate(
            "document.getElementById('mapSelectArea')?.innerHTML || ''"
        )

        tag_start = "var zone ="
        tag_end = "fieldImageType"
        if mapSelectArea_html and tag_start in mapSelectArea_html and tag_end in mapSelectArea_html:
            zone_string = mapSelectArea_html.split(tag_start)[1]
            zone_string = zone_string.split(tag_end)[0]
            zone_string = zone_string.strip().rstrip('\n,')

            import json
            zone_info = json.loads(zone_string)
            if debug.enabled:
                debug.log(f"[TICKETMASTER ZONE] Parsed zone_info via string extraction ({len(zone_info)} zones)")
                if len(zone_info) > 0:
                    sample_id = list(zone_info.keys())[0]
                    sample = zone_info[sample_id]
                    if isinstance(sample, dict) and "groupName" in sample:
                        debug.log(f"[TICKETMASTER ZONE] Sample zone '{sample_id}' groupName: {sample['groupName']}")
            return zone_info

    except Exception as exc:
        debug.log(f"[TICKETMASTER ZONE] String extraction failed: {exc}")

    # Try method 2: Direct JavaScript evaluation (fallback)
    try:
        result = await tab.evaluate('''
            (function() {
                // Check if zone variable exists in global scope
                if (typeof zone !== 'undefined') {
                    // IMPORTANT: Use JSON.parse(JSON.stringify()) to serialize RemoteObject to plain JSON
                    // Without this, NoDriver returns CDP RemoteObject format:
                    // {"type": "object", "value": [["key", {"type": "string", "value": "..."}]]}
                    // Instead of standard JSON: {"key": "value"}
                    try {
                        return JSON.parse(JSON.stringify(zone));
                    } catch(e) {
                        console.error('Zone serialization failed:', e);
                        return zone;  // Fallback to RemoteObject if serialization fails
                    }
                }

                // Fallback: Extract from #mapSelectArea innerHTML
                const el = document.querySelector('#mapSelectArea');
                if (!el) return null;

                const html = el.innerHTML;
                const match = html.match(/var zone = ({[\\s\\S]*?});/);
                if (!match) return null;

                try {
                    return JSON.parse(match[1]);
                } catch(e) {
                    console.error('JSON parse failed:', e);
                    return null;
                }
            })();
        ''')

        if result:
            # Convert RemoteObject to standard Python types
            zone_info = convert_remote_object(result)

            if debug.enabled:
                zone_type = "dict" if isinstance(zone_info, dict) else "list"
                debug.log(f"[TICKETMASTER ZONE] Successfully parsed zone_info ({len(zone_info)} zones, type: {zone_type})")
                debug.log(f"[TICKETMASTER ZONE] RemoteObject converted to standard format")

                # Print detailed structure for debugging
                if len(zone_info) > 0:
                    try:
                        # Print sample zone keys (BEFORE json.dumps to avoid serialization issues)
                        if isinstance(zone_info, list):
                            sample = zone_info[0]
                            sample_id = "index_0"

                            # Diagnostic for List format
                            debug.log(f"[TICKETMASTER ZONE] List first item type: {type(sample)}")

                            if isinstance(sample, dict):
                                sample_keys = list(sample.keys())[:10]  # First 10 keys
                                debug.log(f"[TICKETMASTER ZONE] List item keys (first 10): {sample_keys}")

                                # Check for zone_id fields
                                zone_id_field = None
                                for field in ["sectionCode", "id", "zoneId", "areaNo"]:
                                    if field in sample:
                                        zone_id_field = field
                                        debug.log(f"[TICKETMASTER ZONE] Found zone_id field: '{field}' = '{sample.get(field)}'")
                                        break

                                if not zone_id_field:
                                    debug.log(f"[TICKETMASTER ZONE] WARNING: No zone_id field found (sectionCode, id, zoneId, areaNo)")

                            elif isinstance(sample, (list, tuple)):
                                debug.log(f"[TICKETMASTER ZONE] List item is tuple/list with {len(sample)} elements")
                                if len(sample) > 0:
                                    debug.log(f"[TICKETMASTER ZONE] First element type: {type(sample[0])}")
                                    if isinstance(sample[0], str):
                                        debug.log(f"[TICKETMASTER ZONE] First element (zone_id): {sample[0]}")
                                if len(sample) > 1:
                                    debug.log(f"[TICKETMASTER ZONE] Second element type: {type(sample[1])}")
                                    zone_data = sample[1]
                                    if isinstance(zone_data, dict):
                                        # Check if conversion successful
                                        if "groupName" in zone_data:
                                            debug.log(f"[TICKETMASTER ZONE] [OK] groupName found: {zone_data.get('groupName')}")
                                        elif "type" in zone_data and "value" in zone_data:
                                            debug.log(f"[TICKETMASTER ZONE] [FAIL] Still RemoteObject format (has 'type' and 'value' keys)")
                                            # Try to convert again
                                            zone_data = convert_remote_object(zone_data)
                                            # Update in the list
                                            sample[1] = zone_data
                                            zone_info[0] = sample
                                            if "groupName" in zone_data:
                                                debug.log(f"[TICKETMASTER ZONE] [OK] After re-conversion, groupName found: {zone_data.get('groupName')}")
                                        else:
                                            debug.log(f"[TICKETMASTER ZONE] zone_data keys: {list(zone_data.keys())[:10]}")
                            else:
                                debug.log(f"[TICKETMASTER ZONE] WARNING: Unknown list item format")
                        else:
                            # Dict format
                            sample_id = list(zone_info.keys())[0]
                            sample = zone_info[sample_id]
                            debug.log(f"[TICKETMASTER ZONE] Sample zone_id: {sample_id}")

                        sample_keys = list(sample.keys()) if isinstance(sample, dict) else []
                        if sample_keys:
                            debug.log(f"[TICKETMASTER ZONE] Sample structure keys: {sample_keys[:10]}")  # First 10 keys
                    except Exception as diag_exc:
                        debug.log(f"[TICKETMASTER ZONE] Diagnostic logging failed: {diag_exc}")

    except Exception as exc:
        debug.log(f"[TICKETMASTER ZONE] JavaScript evaluation failed: {exc}")

    return zone_info


# T005: Get target area from zone_info (Pure function - no DOM access)
def get_ticketmaster_target_area(config_dict, area_keyword_item, zone_info):
    """
    Match areas from zone_info based on keyword.
    Returns: (is_need_refresh, matched_blocks)
    """
    debug = util.create_debug_logger(config_dict)

    area_config = config_views.area_view(config_dict)
    area_auto_select_mode = area_config["mode"]

    is_need_refresh = False
    matched_blocks = []

    if not zone_info or len(zone_info) == 0:
        return True, None

    # Normalize zone_info to uniform iteration format
    # Dict format: {"zone_id": {...}} → [("zone_id", {...}), ...]
    # List format (3 types):
    #   Type A: [{"sectionCode": "field_C1_B", ...}, {...}] → extract sectionCode as zone_id
    #   Type B: [["field_C1_B", {...}], ...] → unpack tuple/list
    #   Type C: [(zone_id, {...}), ...] → already in correct format

    if isinstance(zone_info, dict):
        # Dict format: standard case
        zone_items = list(zone_info.items())
    elif isinstance(zone_info, list):
        # List format: need to detect which type
        if len(zone_info) == 0:
            zone_items = []
        else:
            first_item = zone_info[0]

            if isinstance(first_item, dict):
                # Type A: List of dicts - extract zone_id from dict
                zone_items = []
                for z in zone_info:
                    if not isinstance(z, dict):
                        continue
                    zone_id = z.get("sectionCode") or z.get("id") or z.get("zoneId") or z.get("areaNo")
                    zone_items.append((zone_id, z))

            elif isinstance(first_item, (list, tuple)) and len(first_item) >= 2:
                # Type B: List of [id, data] pairs
                zone_items = []
                for item in zone_info:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        zone_id = item[0]
                        zone_data = item[1]
                        # Convert RemoteObject if needed
                        if isinstance(zone_data, dict) and "type" in zone_data and "value" in zone_data:
                            zone_data = convert_remote_object(zone_data)
                        zone_items.append((zone_id, zone_data))

            else:
                # Unknown format - fallback to old logic
                debug.log(f"[TICKETMASTER AREA] Unknown zone_info list format, first item type: {type(first_item)}")
                zone_items = [(None, z) for z in zone_info]
    else:
        # Unexpected type
        debug.log(f"[TICKETMASTER AREA] Unexpected zone_info type: {type(zone_info)}")
        zone_items = []

    for zone_id, zone_data in zone_items:
        # Validate zone_data is dict-like (has .get() method)
        if not hasattr(zone_data, 'get'):
            debug.log(f"[TICKETMASTER AREA] zone_data is not dict-like: {type(zone_data)}, skipping")
            continue

        # Fallback: extract zone_id if still None
        if zone_id is None:
            zone_id = zone_data.get("sectionCode") or zone_data.get("id") or zone_data.get("zoneId") or zone_data.get("areaNo")

        row_is_enabled = zone_data.get("areaStatus") != "UNAVAILABLE"

        if not row_is_enabled:
            continue

        # Build row text from zone info
        row_text = ""
        group_name = ""
        description = ""
        try:
            group_name = zone_data.get("groupName", "")
            description = zone_data.get("description", "")
            row_text = group_name + " " + description
            if "price" in zone_data and len(zone_data["price"]) > 0:
                row_text += " " + zone_data["price"][0].get("ticketPrice", "")
        except:
            pass

        if debug.enabled:
            # Show human-readable zone info instead of just zone_id
            display_name = f"{group_name} {description}".strip() if group_name or description else zone_id
            debug.log(f"[TICKETMASTER AREA] Processing zone: {zone_id} ({display_name})")

        if not row_text.strip():
            continue

        # Check exclude keywords
        if util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
            continue

        # Format and match keywords
        row_text = util.format_keyword_string(row_text)

        is_append_this_row = False
        if area_keyword_item:
            # Must match all keywords (AND logic)
            area_keyword_array = area_keyword_item.split(' ')

            # Word boundary matching function
            import re
            def word_boundary_match(keyword, text):
                """
                Match keyword with word boundary awareness.
                - Single char keywords (like 'I') require word boundaries
                - Multi-char keywords use substring match for flexibility
                """
                formatted_kw = util.format_keyword_string(keyword)
                if len(formatted_kw) <= 2:
                    # Short keywords need word boundary to avoid false positives
                    # e.g., 'I' should not match 'CIRCLE'
                    pattern = r'\b' + re.escape(formatted_kw) + r'\b'
                    return bool(re.search(pattern, text, re.IGNORECASE))
                else:
                    # Longer keywords use substring match
                    return formatted_kw in text

            # Detailed AND logic matching with PASS/FAIL logs
            if debug.enabled:
                keyword_results = []
                for kw in area_keyword_array:
                    match_result = word_boundary_match(kw, row_text)
                    status = "PASS" if match_result else "FAIL"
                    keyword_results.append(f"'{kw}':{status}")

                all_matched = all(
                    word_boundary_match(kw, row_text)
                    for kw in area_keyword_array
                )
                overall_status = "MATCHED" if all_matched else "REJECTED"
                debug.log(f"[TICKETMASTER AREA] AND Match: {zone_id} [{', '.join(keyword_results)}] -> {overall_status}")

            is_append_this_row = all(
                word_boundary_match(kw, row_text)
                for kw in area_keyword_array
            )
        else:
            # No keyword = match all
            is_append_this_row = True

        if is_append_this_row:
            matched_blocks.append(zone_id)

            if area_auto_select_mode == "from top to bottom":
                # Only need first match
                break

    if len(matched_blocks) == 0:
        matched_blocks = None
        is_need_refresh = True

    if matched_blocks:
        debug.log(f"[TICKETMASTER AREA] Matched {len(matched_blocks)} areas: {matched_blocks}")

    return is_need_refresh, matched_blocks


# T006: Get ticket price list (wait for page load)
async def nodriver_ticketmaster_get_ticketPriceList(tab, config_dict):
    """
    Wait for ticketPriceList to load and return the table element.
    Uses official NoDriver API (stable, recommended approach).
    Returns: table element or None

    References:
    - Fixed based on famiticket_nodriver_fixes.md (Phase 4: NoDriver Official API Migration)
    - Issue: tab.evaluate() returns None due to JavaScript Context failure
    - Solution: Use tab.wait_for() and tab.query_selector() instead
    """
    debug = util.create_debug_logger(config_dict)

    try:
        # Phase 1: Wait for mapContainer (basic page load)
        await tab.wait_for(selector='#mapContainer', timeout=5)

        # Ensure DOM references are synchronized (official recommendation)
        await tab

        debug.log("[TICKETMASTER TICKET] mapContainer found")

        # Phase 2: Wait for loading to finish (check if loadingmap disappears)
        max_wait = 10  # 10 seconds max
        for i in range(max_wait):
            loading = await tab.query_selector('#loadingmap')
            if not loading:
                if i > 0:
                    debug.log(f"[TICKETMASTER TICKET] Loading finished after {i}s")
                break
            await tab.sleep(1)
        else:
            # Timeout after 10 seconds
            debug.log("[TICKETMASTER TICKET] Loading timeout after 10s")

        # Phase 3: Try to find ticketPriceList
        table_element = await tab.query_selector('#ticketPriceList')

        if table_element:
            debug.log("[TICKETMASTER TICKET] Found ticketPriceList table")
            return table_element
        else:
            debug.log("[TICKETMASTER TICKET] ticketPriceList not found, will use zone_info")
            return None

    except asyncio.TimeoutError:
        debug.log("[TICKETMASTER TICKET] Timeout waiting for mapContainer")
        return None
    except Exception as e:
        debug.log(f"[TICKETMASTER TICKET] Error: {e}")
        return None


async def nodriver_ticketmaster_date_auto_select(tab, config_dict):
    """
    Automatically select event date on Ticketmaster artist page.
    Returns: True if date was clicked, False otherwise
    """
    debug = util.create_debug_logger(config_dict)

    # Read config (the selection bundle is built by the date view
    # at the selection site below, issue #30 / ADR 0004)
    pass_date_is_sold_out_enable = config_dict.get("tixcraft", {}).get("pass_date_is_sold_out", False)
    # The key only exists under "tixcraft" (settings.py get_default_config); every
    # other platform reads it from there too. Reading "kktix" made this always
    # False, so a Ticketmaster page never auto-reloaded while waiting for the
    # sale to open (issue #378).
    auto_reload_coming_soon_page_enable = config_dict.get("tixcraft", {}).get("auto_reload_coming_soon_page", False)

    sold_out_text_list = ["Sold out", "No tickets available"]
    find_ticket_text_list = ['Find tickets', 'See Tickets']

    # Query date list
    # Ticketmaster.sg uses a table structure: #gameList tbody tr
    # Wait for dynamic content to load (max 5 seconds)
    area_list = None
    max_attempts = 10
    for attempt in range(max_attempts):
        try:
            area_list = await tab.query_selector_all('#gameList tbody tr')
            if area_list and len(area_list) > 0:
                debug.log(f"[TICKETMASTER DATE] Found date list after {attempt * 0.5}s")
                break
            await asyncio.sleep(0.5)
        except Exception as exc:
            if attempt == 0:
                debug.log(f"[TICKETMASTER DATE] Waiting for date list to load... ({exc})")
            await asyncio.sleep(0.5)

    if not area_list:
        debug.log(f"[TICKETMASTER DATE] Failed to find date list after {max_attempts * 0.5}s")
        return False

    # Rows for the shared date-selection core (issue #26, ADR 0003).
    # Two passes, mirroring the original extraction: the first pass
    # decides availability (ticket text present, sold-out check), the
    # second re-reads the text of available rows for keyword matching
    # (a failed re-read leaves the text empty). This handler has never
    # applied keyword_exclude, so the config view passes an empty
    # exclude string on purpose.
    rows = []
    for row in area_list:
        try:
            row_html = await row.get_html()
            row_text = util.remove_html_tags(row_html)
        except:
            break

        if not row_text:
            continue

        row_is_enabled = False

        # Must contain "See Tickets"
        for text_item in find_ticket_text_list:
            if text_item in row_text:
                row_is_enabled = True
                break

        # Check sold out
        if row_is_enabled and pass_date_is_sold_out_enable:
            for sold_out_item in sold_out_text_list:
                if sold_out_item in row_text:
                    row_is_enabled = False
                    debug.log(f"[TICKETMASTER DATE] Skipping sold out event: {row_text[:60]}...")
                    break

        rows.append({
            "text": row_text,
            "available": row_is_enabled,
            "payload": row,
        })

    for row in rows:
        if not row["available"]:
            continue
        try:
            row_html = await row["payload"].get_html()
            row["text"] = util.remove_html_tags(row_html)
        except:
            row["text"] = ""

    available_date_count = sum(1 for row in rows if row["available"])
    debug.log(f"[TICKETMASTER DATE] {available_date_count} available dates after filtering")

    # The bundle comes from the date view (issue #30, ADR 0004); this
    # handler has never applied keyword_exclude, so the view's
    # pass-through value is overridden with an empty string (issue #26).
    config_view = config_views.date_view(config_dict)
    config_view["keyword_exclude"] = ""
    chosen_index = date_selection.select_date(rows, config_view)
    target_area = rows[chosen_index]["payload"] if chosen_index is not None else None

    is_date_clicked = False
    if target_area:
        try:
            # Click "See Tickets" link
            link_element = await target_area.query_selector('a')
            if link_element:
                # Snapshot the tabs that already exist, so only a tab spawned by
                # THIS click is closed afterwards. Closing tabs[1:] wholesale
                # also killed tabs the user had opened themselves to queue other
                # dates (issue #378).
                known_target_ids = set()
                try:
                    for known_tab in tab.browser.tabs:
                        known_id = getattr(getattr(known_tab, "target", None), "target_id", None)
                        if known_id:
                            known_target_ids.add(known_id)
                except Exception:
                    pass

                await link_element.click()
                is_date_clicked = True
                debug.log("[TICKETMASTER DATE] Clicked 'See Tickets' link")

                # Handle new tab (close only what this click opened)
                await tab.sleep(0.3)
                for extra_tab in list(tab.browser.tabs):
                    extra_id = getattr(getattr(extra_tab, "target", None), "target_id", None)
                    if not extra_id or extra_id in known_target_ids:
                        continue
                    try:
                        await extra_tab.close()
                        debug.log("[TICKETMASTER DATE] Closed the tab opened by the click")
                    except Exception:
                        pass
                    await tab.sleep(0.2)

        except Exception as exc:
            debug.log(f"[TICKETMASTER DATE] Failed to click link: {exc}")

    # Auto reload if no match
    if auto_reload_coming_soon_page_enable and not is_date_clicked and available_date_count == 0 and _is_page_reload_enabled(config_dict, debug, "TICKETMASTER DATE"):
        try:
            # Pace reloads with a non-blocking cooldown instead of sleeping
            # for the refresh interval (issue #3). The main loop spins every
            # 50ms, so reloading without pacing here means 2-3 page loads per
            # second while waiting for a sale to open -- exactly what trips
            # Ticketmaster's EPS block (issue #378) -- while sleeping for the
            # interval froze every other check for its whole duration.
            reload_interval = config_views.reload_interval(config_dict)
            previous_until = _state.get("ticketmaster_date_reload_cooldown_until", 0)
            should_reload, cooldown_until = util.advance_reload_cooldown(
                time.time(), previous_until, reload_interval)
            _state["ticketmaster_date_reload_cooldown_until"] = cooldown_until
            if should_reload:
                debug.log("[TICKETMASTER DATE] No dates available, reloading page...")
                await tab.reload()
                await tab.sleep(0.3)
            elif not previous_until:
                debug.log(f"[TICKETMASTER DATE] Waiting {reload_interval}s before reload...")
        except:
            pass

    return is_date_clicked


async def nodriver_ticketmaster_area_auto_select(tab, config_dict, zone_info):
    """
    Automatically select seat area on Ticketmaster ticket page.
    """
    debug = util.create_debug_logger(config_dict)

    area_config = config_views.area_view(config_dict)
    area_keyword = area_config["keyword"].strip()

    debug.log(f"[TICKETMASTER AREA] area_keyword: {area_keyword}")

    is_need_refresh = False
    matched_blocks = None

    # Get area_auto_fallback setting (default: False = strict mode)
    area_auto_fallback = area_config["fallback"]

    if area_keyword:
        area_keyword_array = util.parse_keyword_string_to_array(area_keyword)

        debug.log(f"[TICKETMASTER AREA] Parsed keyword groups: {area_keyword_array}")

        # Early Return Pattern: Try each keyword group with priority
        for idx, area_keyword_item in enumerate(area_keyword_array):
            debug.log(f"[TICKETMASTER AREA] Trying keyword group {idx + 1}/{len(area_keyword_array)}: '{area_keyword_item}'")

            is_need_refresh, matched_blocks = get_ticketmaster_target_area(config_dict, area_keyword_item, zone_info)

            if not is_need_refresh and matched_blocks:
                # Found match - Early Return
                debug.log(f"[TICKETMASTER AREA] Early Return: keyword group {idx + 1} matched {len(matched_blocks)} area(s)")
                break
            else:
                debug.log(f"[TICKETMASTER AREA] Keyword group {idx + 1} had no matches, trying next...")

        # Conditional fallback: only match all if area_auto_fallback is enabled
        if is_need_refresh:
            if area_auto_fallback:
                debug.log("[TICKETMASTER AREA] Fallback enabled: selecting from all available areas")
                is_need_refresh, matched_blocks = get_ticketmaster_target_area(config_dict, "", zone_info)
            else:
                debug.log("[TICKETMASTER AREA] Strict mode: no keyword match, will refresh page")
                # Keep is_need_refresh = True, matched_blocks = None
    else:
        # Empty keyword = match all
        is_need_refresh, matched_blocks = get_ticketmaster_target_area(config_dict, "", zone_info)

    # Select target
    auto_select_mode = area_config["mode"]
    target_area = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)

    if target_area:
        try:
            # Execute JavaScript to select area
            click_area_javascript = f'areaTicket("{target_area}", "map");'
            debug.log(f"[TICKETMASTER AREA] Executing: {click_area_javascript}")

            await tab.evaluate(click_area_javascript)

            # Wait for AJAX to load ticketPriceList (areaTicket executes AJAX request)
            max_wait = 5  # 5 seconds max
            for i in range(max_wait):
                await tab.sleep(1)

                # Check if ticketPriceList has loaded
                price_list = await tab.query_selector('#ticketPriceList')
                if price_list:
                    debug.log(f"[TICKETMASTER AREA] ticketPriceList loaded after {i+1}s")
                    break
            else:
                debug.log("[TICKETMASTER AREA] Timeout waiting for ticketPriceList (5s)")

            debug.log(f"[TICKETMASTER AREA] Selected zone: {target_area}")

        except Exception as exc:
            debug.log(f"[TICKETMASTER AREA] Failed to execute JavaScript: {exc}")

    # Auto refresh if needed (only when keyword is specified but no match)
    if is_need_refresh:
        # Check if area_keyword is empty (empty = should match all areas)
        area_keyword = area_config["keyword"].strip()

        if area_keyword and _is_page_reload_enabled(config_dict, debug, "TICKETMASTER AREA"):
            # Keyword specified but no match → might need to wait for availability
            # Non-blocking cooldown paces the reload (issue #3): passes inside
            # the interval return at once instead of sleeping through it.
            try:
                reload_interval = config_views.reload_interval(config_dict)
                previous_until = _state.get("ticketmaster_area_reload_cooldown_until", 0)
                should_reload, cooldown_until = util.advance_reload_cooldown(
                    time.time(), previous_until, reload_interval)
                _state["ticketmaster_area_reload_cooldown_until"] = cooldown_until
                if should_reload:
                    debug.log("[TICKETMASTER AREA] No areas matched keyword, reloading page...")
                    await tab.reload()
                elif not previous_until:
                    debug.log(f"[TICKETMASTER AREA] Waiting {reload_interval}s before reload...")
            except:
                pass
        elif not area_keyword:
            # No keyword but no areas → likely a data parsing issue, don't reload
            debug.log("[TICKETMASTER AREA] No areas available (possible zone_info parsing issue)")


async def nodriver_ticketmaster_assign_ticket_number(tab, config_dict):
    """
    Automatically set ticket number on Ticketmaster ticket page.
    Caller is responsible for area selection before calling this function.
    Returns True if ticket number was set, False otherwise.
    """
    debug = util.create_debug_logger(config_dict)

    # Get ticket price list (area must already be selected by caller)
    table_select = await nodriver_ticketmaster_get_ticketPriceList(tab, config_dict)
    if not table_select:
        return False

    # Find select element
    select_element = None
    try:
        select_element = await table_select.query_selector('select')
    except Exception as exc:
        debug.log(f"[TICKETMASTER TICKET] Failed to find select: {exc}")
        return False

    if not select_element:
        debug.log("[TICKETMASTER TICKET] No select element found")
        return False

    # Update element to sync attributes
    try:
        await select_element.update()
    except:
        pass

    # Check if element is enabled
    try:
        select_attrs = select_element.attrs or {}
        is_disabled = 'disabled' in select_attrs
        if is_disabled:
            debug.log("[TICKETMASTER TICKET] Select element is disabled")
            return False
    except Exception as exc:
        debug.log(f"[TICKETMASTER TICKET] Failed to check disabled status: {exc}")
        pass

    # Ticketmaster shares the TixCraft backend but has its own DOM, so it needs
    # its own allow_less_tickets handling (the TixCraft path lives in
    # nodriver_ticket_number_select_fill).
    ticket_number = str(config_dict.get("ticket_number", 1))
    allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)

    # Check current value (zendriver evaluate returns Python values directly)
    select_attrs = select_element.attrs or {}
    selector_id = select_attrs.get('id')
    current_value = None
    if selector_id:
        try:
            current_value = await tab.evaluate(f'''
                (function() {{
                    const selectEl = document.getElementById('{selector_id}');
                    if (selectEl && selectEl.selectedIndex >= 0) {{
                        return selectEl.options[selectEl.selectedIndex].text;
                    }}
                    return null;
                }})();
            ''')
        except:
            pass

    if current_value and current_value != "0" and current_value.isnumeric():
        # A leftover value from an earlier round (or from the site itself) must
        # still satisfy the target, otherwise the order is submitted with the
        # wrong count. Only accept fewer tickets when the user opted in.
        current_count = int(current_value)
        target_count = int(ticket_number)
        is_acceptable = (current_count == target_count) or (
            allow_less_tickets and 0 < current_count < target_count
        )
        if is_acceptable:
            debug.log(f"[TICKETMASTER TICKET] Ticket number already set to: {current_value}")
            try:
                auto_mode_button = await tab.query_selector('#autoMode')
                if auto_mode_button:
                    await auto_mode_button.click()
                    debug.log("[TICKETMASTER TICKET] Clicked #autoMode button")
            except:
                pass
            return True
        debug.log(f"[TICKETMASTER TICKET] Existing ticket number {current_value} "
                  f"does not satisfy target {ticket_number}, re-selecting")

    try:
        # Get select element ID for JavaScript manipulation
        select_attrs = select_element.attrs or {}
        selector_id = select_attrs.get('id')
        if not selector_id:
            debug.log("[TICKETMASTER TICKET] Select element has no id attribute")
            return False

        # Dump all option texts for debugging
        option_texts = await tab.evaluate(f'''
            (function(elementId) {{
                const selectEl = document.getElementById(elementId);
                if (!selectEl) return [];
                return Array.from(selectEl.options).map(o => o.text + '|' + o.value);
            }})('{selector_id}');
        ''')
        debug.log(f"[TICKETMASTER TICKET] Available options: {option_texts}")

        # Use JavaScript to set select value. Without allow_less_tickets the exact
        # count is mandatory; with it, fall back to the largest count BELOW the
        # target (never above it -- that would buy more than the user asked for).
        result = await tab.evaluate(f'''
            (function(elementId, targetText, targetCount, allowLess) {{
                const selectEl = document.getElementById(elementId);
                if (!selectEl) {{
                    return {{ success: false, error: "Element not found" }};
                }}
                const options = selectEl.options;
                // Try exact text match first
                for (let i = 0; i < options.length; i++) {{
                    if (options[i].text === targetText) {{
                        selectEl.selectedIndex = i;
                        selectEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return {{ success: true, value: options[i].value, selected: options[i].text }};
                    }}
                }}
                if (!allowLess) {{
                    return {{ success: false, error: "Exact ticket count unavailable" }};
                }}
                // Fallback: largest available count below the target
                let maxIdx = -1;
                let maxVal = 0;
                for (let i = 0; i < options.length; i++) {{
                    const v = parseInt(options[i].value);
                    if (!isNaN(v) && v > 0 && v < targetCount && v > maxVal) {{
                        maxVal = v;
                        maxIdx = i;
                    }}
                }}
                if (maxIdx >= 0) {{
                    selectEl.selectedIndex = maxIdx;
                    selectEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    return {{ success: true, value: options[maxIdx].value, selected: options[maxIdx].text, fallback: true }};
                }}
                return {{ success: false, error: "Option not found" }};
            }})({json.dumps(selector_id)}, {json.dumps(ticket_number)}, {json.dumps(int(ticket_number))}, {json.dumps(bool(allow_less_tickets))});
        ''')

        if result and result.get('success'):
            selected = result.get('selected', ticket_number)
            if result.get('fallback'):
                debug.log(f"[TICKETMASTER TICKET] Exact '{ticket_number}' not found, "
                          f"allow_less_tickets enabled, selected: {selected}")
            else:
                debug.log(f"[TICKETMASTER TICKET] Set ticket number to: {selected}")

            # Click autoMode button
            await tab.sleep(0.1)
            try:
                auto_mode_button = await tab.query_selector('#autoMode')
                if auto_mode_button:
                    await auto_mode_button.click()
                    debug.log("[TICKETMASTER TICKET] Clicked #autoMode button")
            except:
                pass
            return True
        else:
            debug.log(f"[TICKETMASTER TICKET] Failed to set ticket number: {result.get('error') if result else 'no result'}")
            return False

    except Exception as exc:
        debug.log(f"[TICKETMASTER TICKET] Exception setting ticket number: {exc}")
        return False


async def _wait_for_captcha_input(tab):
    """True once #TicketForm_verifyCode exists, within the wait limit.

    Polls on wall-clock time, so pressing pause mid-wait neither shortens it
    nor gives up early; the OCR loop handles pause. Each poll catches its own
    error: a CDP error while the form is still rendering means "not yet".
    tab.wait_for re-raises those and would give up on the first one.
    """
    deadline = time.monotonic() + CONST_TICKETMASTER_CAPTCHA_WAIT_SEC
    while True:
        try:
            if await tab.query_selector('#TicketForm_verifyCode'):
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        await tab.sleep(0.5)


async def nodriver_ticketmaster_captcha(tab, config_dict, ocr, captcha_browser):
    """
    Handle captcha on Ticketmaster check-captcha page.
    Returns: True if captcha was handled, False otherwise
    """
    debug = util.create_debug_logger(config_dict)

    # Check agree checkbox
    for _ in range(2):
        is_checked = await nodriver_check_checkbox(tab, '#TicketForm_agree')
        if is_checked:
            debug.log("[TICKETMASTER CAPTCHA] Checked TicketForm_agree")
            break

    # Alert state tracked by event handler
    alert_state = {"detected": False, "message": ""}

    async def on_captcha_alert(event: cdp.page.JavascriptDialogOpening):
        alert_state["detected"] = True
        alert_state["message"] = event.message
        debug.log(f"[TICKETMASTER CAPTCHA] Alert event: '{event.message[:60]}'")

        # This handler is registered on the check-captcha page but never removed
        # (zendriver's tab.remove_handlers drops EVERY handler of the type,
        # including the global one), so it stays alive for the rest of the
        # session. Without the guards below it answered OK to the checkout
        # page's "cancel your order?" confirm and cancelled the order (#378).
        if os.path.exists(util.get_instance_state_path(CONST_MAXBOT_INT28_FILE)):
            return
        # tab.target.url is the CDP-cached value; js_dumps would hang here
        # because an open dialog blocks JavaScript execution.
        current_url = tab.target.url if hasattr(tab, 'target') and tab.target else ""
        if _should_leave_dialog_to_user(current_url):
            debug.log("[TICKETMASTER CAPTCHA] Alert on checkout page, NOT auto-dismissing")
            return

        # Dismiss the alert immediately to prevent blocking
        try:
            await tab.send(cdp.page.handle_java_script_dialog(accept=True))
            debug.log("[TICKETMASTER CAPTCHA] Alert auto-dismissed by handler")
        except:
            pass

    # Register handler for this captcha session
    tab.add_handler(cdp.page.JavascriptDialogOpening, on_captcha_alert)

    # Handle captcha
    if not config_dict.get("ocr_captcha", {}).get("enable", False):
        # OCR disabled - manual input
        await nodriver_tixcraft_keyin_captcha_code(tab, answer="", auto_submit=False, config_dict=config_dict)
        return False
    else:
        # OCR enabled - auto recognition
        previous_answer = None
        current_url = tab.target.url
        fail_count = 0
        total_fail_count = 0

        # The caller marks this URL done once we return, so an input that has
        # not rendered yet must be waited for here, not skipped.
        if not await _wait_for_captcha_input(tab):
            print("[TICKETMASTER CAPTCHA] Captcha input did not appear. "
                  "Please enter the captcha manually.")
            return False

        # Once the input is there, give the captcha image a moment to draw.
        await asyncio.sleep(random.uniform(0.5, 1.0))

        for redo_ocr in range(99):
            try:
                alert_state["detected"] = False  # Reset before each attempt

                away_from_keyboard_enable = config_dict.get("ocr_captcha", {}).get("force_submit", False)
                ocr_captcha_image_source = config_dict.get("ocr_captcha", {}).get("image_source", "canvas")
                domain_name = tab.target.url.split('/')[2]

                # Call tixcraft_auto_ocr for captcha recognition
                is_need_redo_ocr, previous_answer, is_form_submitted = await nodriver_tixcraft_auto_ocr(
                    tab, config_dict, ocr, away_from_keyboard_enable, previous_answer,
                    captcha_browser, ocr_captcha_image_source, domain_name
                )

                if is_form_submitted:
                    debug.log("[TICKETMASTER CAPTCHA] Form submitted")

                    # Poll for alert event (max 2 seconds)
                    for wait_i in range(10):
                        await asyncio.sleep(0.2)
                        if alert_state["detected"]:
                            break

                    debug.log(f"[TICKETMASTER CAPTCHA] alert_state={alert_state}")

                    error_detected = alert_state["detected"]

                    # If alert was detected (already dismissed by handler), retry OCR
                    if error_detected:
                        debug.log("[TICKETMASTER CAPTCHA] Captcha error detected, retrying...")

                        await asyncio.sleep(0.3)
                        await nodriver_tixcraft_reload_captcha(tab, domain_name)
                        previous_answer = None
                        fail_count = 0
                        total_fail_count += 1

                        if total_fail_count >= 15:
                            print("[TICKETMASTER CAPTCHA] Failed 15 times. Manual input required.")
                            await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                            break

                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        continue

                    # Check for Ticketmaster custom error modal (not native alert)
                    # The modal shows "The verification code that you entered is incorrect"
                    try:
                        # Check for modal overlay or dialog
                        modal_result = await tab.evaluate('''
                            (function() {
                                // Check for visible modal or alert dialog
                                const modals = document.querySelectorAll('.modal, .alert, [role="dialog"], [role="alertdialog"]');
                                for (const modal of modals) {
                                    if (modal.offsetParent !== null || getComputedStyle(modal).display !== 'none') {
                                        return {
                                            found: true,
                                            text: modal.innerText || modal.textContent
                                        };
                                    }
                                }
                                // Also check for any visible buttons that might be confirm/OK
                                const buttons = document.querySelectorAll('button');
                                for (const btn of buttons) {
                                    const text = btn.innerText || btn.textContent;
                                    if ((text.includes('確定') || text.includes('OK') || text.includes('Try again')) &&
                                        btn.offsetParent !== null) {
                                        return {
                                            found: true,
                                            buttonSelector: 'button'
                                        };
                                    }
                                }
                                return { found: false };
                            })();
                        ''')

                        # Handle CDP RemoteObject format (may return as list or dict)
                        modal_content = None
                        if modal_result:
                            if isinstance(modal_result, dict):
                                modal_content = modal_result
                            elif isinstance(modal_result, list) and len(modal_result) > 0:
                                # CDP sometimes returns [{'type': 'object', 'value': {...}}]
                                first_item = modal_result[0]
                                if isinstance(first_item, dict):
                                    if 'value' in first_item:
                                        modal_content = first_item.get('value', {})
                                    else:
                                        modal_content = first_item

                        if modal_content and isinstance(modal_content, dict) and modal_content.get('found'):
                            error_detected = True
                            debug.log(f"[TICKETMASTER CAPTCHA] Error modal detected")

                            # Try to click confirm/OK button to dismiss modal
                            dismiss_result = await tab.evaluate('''
                                (function() {
                                    // Find and click confirm button
                                    const buttons = document.querySelectorAll('button');
                                    for (const btn of buttons) {
                                        const text = btn.innerText || btn.textContent;
                                        if (text.includes('確定') || text.includes('OK') || text.includes('Try again')) {
                                            btn.click();
                                            return true;
                                        }
                                    }
                                    // Try to find any primary button
                                    const primaryBtn = document.querySelector('.btn-primary, [type="button"]');
                                    if (primaryBtn) {
                                        primaryBtn.click();
                                        return true;
                                    }
                                    return false;
                                })();
                            ''')

                            # Handle CDP RemoteObject format
                            dismiss_success = False
                            if dismiss_result is True:
                                dismiss_success = True
                            elif isinstance(dismiss_result, list) and len(dismiss_result) > 0:
                                dismiss_success = dismiss_result[0] is True or dismiss_result[0] == True

                            if debug.enabled:
                                if dismiss_success:
                                    debug.log("[TICKETMASTER CAPTCHA] Error modal dismissed, will retry OCR")
                                else:
                                    debug.log("[TICKETMASTER CAPTCHA] Could not dismiss modal")

                            # Reset state for retry
                            await asyncio.sleep(0.3)

                            # Reload captcha for new image
                            await nodriver_tixcraft_reload_captcha(tab, domain_name)
                            previous_answer = None
                            fail_count = 0
                            total_fail_count += 1

                            # Check retry limit
                            if total_fail_count >= 15:
                                print("[TICKETMASTER CAPTCHA] OCR failed 15 times after error modal. Please enter captcha manually.")
                                await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                                break

                            await asyncio.sleep(random.uniform(0.5, 1.0))
                            continue  # Retry OCR

                    except Exception as modal_exc:
                        debug.log(f"[TICKETMASTER CAPTCHA] Error checking modal: {modal_exc}")

                    # No error modal detected, form submitted successfully
                    if not error_detected:
                        break

                if not away_from_keyboard_enable:
                    break

                if not is_need_redo_ocr:
                    break

                # Track failures and handle retry limits
                fail_count += 1
                total_fail_count += 1

                debug.log(f"[TICKETMASTER CAPTCHA] Fail count: {fail_count}, Total fails: {total_fail_count}")

                # Check if total failures reached 15, switch to manual input mode
                if total_fail_count >= 15:
                    print("[TICKETMASTER CAPTCHA] OCR failed 15 times. Please enter captcha manually.")
                    await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                    break

                # Refresh captcha after 3 consecutive failures with same answer
                if fail_count >= 3:
                    debug.log("[TICKETMASTER CAPTCHA] 3 consecutive failures, reloading captcha...")
                    await nodriver_tixcraft_reload_captcha(tab, domain_name)
                    fail_count = 0
                    previous_answer = None  # Reset to allow fresh OCR
                    await asyncio.sleep(random.uniform(0.8, 1.2))  # Wait for new captcha to load
                else:
                    # Wait between retries to allow canvas to fully load
                    await asyncio.sleep(random.uniform(0.3, 0.5))

                # Check if URL changed
                new_url = tab.target.url
                if new_url != current_url:
                    debug.log("[TICKETMASTER CAPTCHA] URL changed, stopping OCR loop")
                    break

            except Exception as exc:
                debug.log(f"[TICKETMASTER CAPTCHA] OCR error: {exc}")
                break

        return True


async def nodriver_ticketmaster_promo(tab, config_dict, fail_list):
    question_selector = '#promoBox'
    return await nodriver_tixcraft_input_check_code(tab, config_dict, fail_list, question_selector)


async def nodriver_ticketmaster_check_ip_block(tab, config_dict, current_url=""):
    """Detect PerimeterX EPS block page on tixcraft/ticketmaster domains.

    When blocked, waits 4-7 minutes (random) then navigates back to original URL.
    Returns True if blocked (caller should skip normal processing), False otherwise.
    """
    debug = util.create_debug_logger(config_dict)

    # Still within previous block wait period
    block_until = _state.get("ip_block_until", 0)
    if block_until > 0 and time.time() < block_until:
        remaining = int(block_until - time.time())
        debug.log(f"[EPS BLOCK] Still waiting for block to expire, {remaining}s remaining")
        await sleep_with_pause_check(tab, 5, config_dict)
        return True

    try:
        result_json = await tab.evaluate('''
            (function() {
                try {
                    if (typeof action !== "undefined" && action === "block" && typeof rr !== "undefined") {
                        return JSON.stringify({
                            blocked: true,
                            rr: rr || "",
                            client_ip: typeof client_ip !== "undefined" ? client_ip : ""
                        });
                    }
                } catch(e) {}
                return JSON.stringify({blocked: false, rr: "", client_ip: ""});
            })()
        ''')

        if not result_json:
            return False

        result = json.loads(result_json)
        if not result.get("blocked", False):
            return False

        original_url = result.get("rr", "")
        client_ip = result.get("client_ip", "unknown")
        scope_url = original_url or current_url
        wait_seconds, is_custom_delay = _resolve_soft_block_wait_seconds(config_dict, scope_url)

        if is_custom_delay:
            debug.log(
                f"[EPS BLOCK] IP blocked (IP: {client_ip}), using custom TixCraft soft-block delay: {wait_seconds}s"
            )
        else:
            debug.log(
                f"[EPS BLOCK] IP blocked (IP: {client_ip}), using default delay: {wait_seconds}s before retry"
            )
        _state["ip_block_until"] = time.time() + wait_seconds

        waited = 0
        while waited < wait_seconds:
            if await check_and_handle_pause(config_dict):
                return True
            chunk = min(10, wait_seconds - waited)
            await asyncio.sleep(chunk)
            waited += chunk
            remaining = wait_seconds - waited
            if remaining > 0 and waited % 60 == 0:
                debug.log(f"[EPS BLOCK] Waiting for block to expire, {remaining}s remaining")

        _state["ip_block_until"] = 0

        if original_url:
            debug.log(f"[EPS BLOCK] Block expired, navigating back to: {original_url}")
            try:
                await tab.get(original_url)
            except Exception:
                pass

        return True

    except Exception as exc:
        debug.log(f"[EPS BLOCK] Error checking block status: {exc}")
        return False
