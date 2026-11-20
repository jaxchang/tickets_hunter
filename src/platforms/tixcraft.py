#encoding=utf-8
# =============================================================================
# TixCraft + Ticketmaster Platform Module
# Extracted from nodriver_tixcraft.py during modularization (Phase 1)
# Contains: tixcraft.com, indievox.com, ticketmaster.* family
# =============================================================================

import asyncio
import base64
import json
import os
import random
import re
import time
import traceback
import webbrowser

try:
    import ddddocr
except Exception:
    pass

from zendriver import cdp

import util
import date_selection
import config_views
from nodriver_common import (
    PlatformEntry,
    PageState,
    check_and_handle_pause,
    sleep_with_pause_check,
    convert_remote_object,
    nodriver_check_checkbox,
    nodriver_check_checkbox_enhanced,
    nodriver_current_url,
    nodriver_get_text_by_selector,
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
    write_question_to_file,
    CONST_MAXBOT_ANSWER_ONLINE_FILE,
    CONST_MAXBOT_INT28_FILE,
    CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS,
    CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER,
)

from platforms.tixcraft_shared import (
    _PAGE_AREA,
    _PAGE_DATE,
    _PAGE_TICKET,
    _PAGE_TICKETMASTER_AREA,
    _PAGE_TICKETMASTER_DATE,
    _is_page_reload_enabled,
    _is_tixcraft_soft_block_scope,
    _should_leave_dialog_to_user,
    _state,
    nodriver_fill_verify_form,
    nodriver_tixcraft_auto_ocr,
    nodriver_tixcraft_get_ocr_answer,
    nodriver_tixcraft_input_check_code,
    nodriver_tixcraft_keyin_captcha_code,
    nodriver_tixcraft_reload_captcha,
    nodriver_tixcraft_toast,
)
from platforms.ticketmaster import (
    get_ticketmaster_target_area,
    nodriver_ticketmaster_area_auto_select,
    nodriver_ticketmaster_assign_ticket_number,
    nodriver_ticketmaster_captcha,
    nodriver_ticketmaster_check_ip_block,
    nodriver_ticketmaster_date_auto_select,
    nodriver_ticketmaster_get_ticketPriceList,
    nodriver_ticketmaster_parse_zone_info,
    nodriver_ticketmaster_promo,
)

__all__ = [
    "PLATFORM_ENTRIES",
    "nodriver_tixcraft_home_close_window",
    "nodriver_tixcraft_redirect",
    "nodriver_ticketmaster_parse_zone_info",
    "get_ticketmaster_target_area",
    "nodriver_ticketmaster_get_ticketPriceList",
    "nodriver_ticketmaster_date_auto_select",
    "nodriver_ticketmaster_area_auto_select",
    "nodriver_ticketmaster_assign_ticket_number",
    "nodriver_ticketmaster_captcha",
    "nodriver_ticketmaster_promo",
    "nodriver_tixcraft_verify",
    "nodriver_fill_verify_form",
    "nodriver_tixcraft_input_check_code",
    "nodriver_tixcraft_date_auto_select",
    "nodriver_tixcraft_area_auto_select",
    "nodriver_get_tixcraft_target_area",
    "nodriver_ticket_number_select_fill",
    "nodriver_tixcraft_assign_ticket_number",
    "nodriver_tixcraft_ticket_main_agree",
    "nodriver_tixcraft_ticket_main",
    "nodriver_tixcraft_keyin_captcha_code",
    "nodriver_tixcraft_toast",
    "nodriver_tixcraft_reload_captcha",
    "nodriver_tixcraft_get_ocr_answer",
    "nodriver_tixcraft_has_captcha",
    "nodriver_tixcraft_submit_without_captcha",
    "nodriver_tixcraft_auto_ocr",
    "nodriver_tixcraft_ticket_main_ocr",
    "nodriver_tixcraft_main",
    "nodriver_ticketmaster_check_ip_block",
]


def _process_queue_it_state(url, state, current_time):
    """Update queue-it tracking state and decide whether the main loop should pause.

    Returns:
        (should_pause: bool, elapsed_seconds: float | None)
        - should_pause=True means the caller must short-circuit the main loop
          (we're still inside *.queue-it.net).
        - elapsed_seconds is set to the wait duration when this call detects
          the queue has been passed; otherwise None.
    """
    url_lower = (url or "").lower()
    if 'queue-it.net' in url_lower:
        if state.get("queue_it_enter_time") is None:
            state["queue_it_enter_time"] = current_time
        return True, None
    if state.get("queue_it_enter_time") is not None:
        elapsed = current_time - state["queue_it_enter_time"]
        state["queue_it_enter_time"] = None
        return False, elapsed
    return False, None


async def nodriver_tixcraft_home_close_window(tab):
    if _state.get('cookie_accepted'):
        return
    try:
        accept_all_cookies_btn = await tab.query_selector('#onetrust-accept-btn-handler')
        if accept_all_cookies_btn:
            await accept_all_cookies_btn.click()
            _state['cookie_accepted'] = True
    except:
        pass

async def nodriver_tixcraft_redirect(tab, url):
    ret = False
    game_name = ""
    url_split = url.split("/")
    if len(url_split) >= 6:
        # Strip URL fragment/query: tixcraft detail pages carry a tab anchor
        # (e.g. .../activity/detail/26_fireball#intro). Without this the fragment
        # leaks into game_name -> the buy-link selector never matches -> the bot
        # never redirects to /activity/game/ and stalls on the detail page.
        game_name = url_split[5].split("#")[0].split("?")[0]
    if len(game_name) > 0:
        if "/activity/detail/%s" % (game_name,) in url:
            # Issue #278: detail pages are server-side rendered. Until the sale
            # opens there is no anchor to /activity/game/{id}. Redirecting
            # blindly drops the user on an empty gameList page and makes manual
            # F5 look broken. Only redirect once the buy link actually exists.
            selector = 'a[href*="/activity/game/%s"]' % game_name
            js = 'document.querySelector(%s) !== null' % json.dumps(selector)
            has_buy_link = False
            try:
                has_buy_link = await tab.evaluate(js)
            except Exception:
                has_buy_link = False
            if not has_buy_link:
                return ret
            entry_url = url.replace("/activity/detail/", "/activity/game/").split("#")[0]
            print("redirect to new url:", entry_url)
            try:
                await tab.get(entry_url)
                # 等待日期列表出現，確保頁面載入完成
                try:
                    await tab.wait_for('#gameList > table > tbody > tr', timeout=5)
                except:
                    pass  # timeout 沒關係，讓後續邏輯處理
                ret = True
            except Exception as exec1:
                pass
    return ret

# ============================================
# Ticketmaster.com NoDriver Platform Migration
# ============================================
# Foundation Functions (T004-T007)
#


# ============================================
# User Story 1: Date Auto Select (T009)
# ============================================


# ============================================
# User Story 2: Area Auto Select (T012)
# ============================================

            # Let next function (assign_ticket_number) handle it

# ============================================
# User Story 3: Ticket Number Assignment (T015-T016)
# ============================================


# ============================================
# User Story 4: Captcha Handling (T019)
# ============================================


async def nodriver_tixcraft_verify(tab, config_dict, fail_list):
    question_selector = '.zone-verify'
    return await nodriver_tixcraft_input_check_code(tab, config_dict, fail_list, question_selector)


async def nodriver_tixcraft_date_auto_select(tab, url, config_dict, domain_name):
    debug = util.create_debug_logger(config_dict)

    # Issue #188: Check sold out cooldown before proceeding.
    # Non-blocking (issue #80): while the cooldown is active this pass
    # simply does nothing and the main loop's next pass re-checks the
    # timestamp -- the #376 pattern. The timestamp is the single
    # source of truth: it expires on its own and a new sold-out alert
    # re-arms it, so it is no longer reset by hand after waiting.
    if _state and _state.get("sold_out_cooldown_until", 0) > time.time():
        remaining = _state["sold_out_cooldown_until"] - time.time()
        debug.log(f"[DATE SELECT] Sold out cooldown active, skipping this pass ({remaining:.1f}s left)")
        return False

    # Read config via the date view (issue #30, ADR 0004)
    config_view = config_views.date_view(config_dict)

    # T003: Check main switch (defensive programming)
    if not config_view["enable"]:
        debug.log("[DATE SELECT] Main switch is disabled, skipping date selection")
        return False

    # read config
    auto_select_mode = config_view["mode"]
    date_keyword = config_view["keyword"]
    date_auto_fallback = config_view["fallback"]
    pass_date_is_sold_out_enable = config_dict["tixcraft"]["pass_date_is_sold_out"]
    auto_reload_coming_soon_page_enable = config_dict["tixcraft"]["auto_reload_coming_soon_page"]

    sold_out_text_list = ["選購一空","已售完","No tickets available","Sold out","空席なし","完売した"]
    find_ticket_text_list = ['立即訂購','Find tickets', 'Start ordering','お申込みへ進む']

    game_name = ""
    if "/activity/game/" in url:
        url_split = url.split("/")
        if len(url_split) >= 6:
            game_name = url_split[5]

    check_game_detail = "/activity/game/%s" % (game_name,) in url

    area_list = None
    if check_game_detail:
        # 智慧等待：等待日期列表出現
        # 注意：從 /activity/detail/ redirect 過來時，redirect 函數已經等待過了
        # 這裡再等待一次是為了處理直接進入 /activity/game/ 頁面的情況
        try:
            await tab.wait_for('#gameList > table > tbody > tr', timeout=3)
        except:
            pass  # timeout 沒關係，繼續嘗試讀取

        try:
            area_list = await tab.query_selector_all('#gameList > table > tbody > tr')
        except:
            pass

    # Language detection for coming soon
    is_coming_soon = False
    coming_soon_conditions = {
        'en-US': [' day(s)', ' hrs.',' min',' sec',' till sale starts!','0',':','/'],
        'zh-TW': ['開賣','剩餘',' 天',' 小時',' 分鐘',' 秒','0',':','/','20'],
        'ja': ['発売開始', ' 日', ' 時間',' 分',' 秒','0',':','/','20']
    }

    if 'html_lang' not in _state:
        try:
            _state['html_lang'] = await tab.evaluate('document.documentElement.lang') or 'en-US'
        except:
            _state['html_lang'] = 'en-US'
    html_lang = _state['html_lang']

    coming_soon_condictions_list = coming_soon_conditions.get(html_lang, coming_soon_conditions['en-US'])

    rows = []

    if area_list and len(area_list) > 0:
        # Batch fetch all row HTML in one CDP round-trip (~9-16x faster than sequential get_html())
        all_row_htmls = None
        try:
            all_row_htmls = await tab.evaluate(
                "Array.from(document.querySelectorAll('#gameList > table > tbody > tr')).map(r => r.outerHTML)"
            )
        except:
            pass
        for i, row in enumerate(area_list):
            try:
                if all_row_htmls and i < len(all_row_htmls):
                    row_html = all_row_htmls[i]
                else:
                    row_html = await row.get_html()
                row_text = util.remove_html_tags(row_html)
            except:
                break

            # Rows for the shared date-selection core (issue #24,
            # ADR 0003): availability is computed here (buy text
            # present, sold-out tail check); keyword priority matching
            # lives in the core. The exclude predicate still runs here
            # for the coming-soon gate, exactly where it used to run;
            # the core applies it again when picking candidates.
            row_is_enabled = False
            if row_text and not util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
                # Check coming soon
                if all(cond in row_text for cond in coming_soon_condictions_list):
                    is_coming_soon = True
                    debug.log(f"[DATE SELECT] Detected coming soon countdown")
                    if auto_reload_coming_soon_page_enable:
                        debug.log(f"[DATE SELECT] auto_reload_coming_soon_page=true, will reload and retry")
                        break
                    else:
                        # Skip this row (don't add it to rows)
                        continue

                # Check if row has ticket text
                row_is_enabled = any(text in row_text for text in find_ticket_text_list)

                # Check sold out
                if row_is_enabled and pass_date_is_sold_out_enable:
                    for sold_out_item in sold_out_text_list:
                        if sold_out_item in row_text[-(len(sold_out_item)+5):]:
                            row_is_enabled = False
                            # 移除：售完訊息過度詳細
                            break

            if row_text:
                rows.append({
                    "text": row_text,
                    "available": row_is_enabled,
                    "payload": row,
                })

    chosen_index = date_selection.select_date(rows, config_view)
    target_area = rows[chosen_index]["payload"] if chosen_index is not None else None

    if debug.enabled:
        if target_area:
            debug.log(f"[DATE SELECT] Auto-select mode: {auto_select_mode}")
            debug.log(f"[DATE SELECT] Target selected from {len(rows)} dates")
        else:
            debug.log(f"[DATE SELECT] No target selected")

    is_date_clicked = False

    # 移除：內部選擇細節過度詳細

    if target_area:
        # Priority: button with data-href (tixcraft/indievox) > regular link > regular button
        # IMPORTANT: Search within target_area, not the whole page
        click_method_used = None
        try:
            debug.log("[DATE SELECT] Trying button[data-href] method within target_area...")

            # Method 1: button[data-href] within target_area (tixcraft/indievox specific)
            # 使用 NoDriver Element API 取得 data-href
            button_with_href = await target_area.query_selector('button[data-href]')
            data_href = None
            if button_with_href:
                # 更新元素以確保屬性載入
                await button_with_href.update()
                button_attrs = button_with_href.attrs or {}
                data_href = button_attrs.get('data-href')

                if debug.enabled:
                    if data_href:
                        debug.log(f"[DATE SELECT] button[data-href] found in target_area: {data_href}")
                    else:
                        debug.log("[DATE SELECT] button[data-href] found but no href value")

                if data_href:
                    debug.log("[DATE SELECT] Navigating via button[data-href]...")
                    await tab.get(data_href)
                    is_date_clicked = True
                    click_method_used = "button[data-href]"
                    debug.log("[DATE SELECT] Successfully navigated via button[data-href]")
            else:
                debug.log("[DATE SELECT] No button[data-href] in target_area, will try fallback methods")
        except Exception as e:
            debug.log(f"[DATE SELECT] button[data-href] method failed: {e}")

        # Method 2: regular link or button click
        if not is_date_clicked:
            try:
                debug.log("[DATE SELECT] Trying link <a[href]> method within target_area...")

                # Try link first (ticketmaster, etc)
                link = await target_area.query_selector('a[href]')
                if link:
                    debug.log("[DATE SELECT] Link found in target_area, clicking...")
                    await link.click()
                    is_date_clicked = True
                    click_method_used = "link <a[href]>"
                    debug.log("[DATE SELECT] Successfully clicked via link")
                else:
                    debug.log("[DATE SELECT] No link found, trying regular button within target_area...")

                    # Try regular button
                    button = await target_area.query_selector('button')
                    if button:
                        debug.log("[DATE SELECT] Regular button found in target_area, clicking...")
                        await button.click()
                        is_date_clicked = True
                        click_method_used = "regular button"
                        debug.log("[DATE SELECT] Successfully clicked via regular button")
                    else:
                        debug.log("[DATE SELECT] No clickable element found in target_area")
            except Exception as e:
                debug.log(f"[DATE SELECT] Click error: {e}")

        # Final summary
        if debug.enabled:
            if is_date_clicked and click_method_used:
                debug.log(f"[DATE SELECT] ========================================")
                debug.log(f"[DATE SELECT] Date selection completed successfully")
                debug.log(f"[DATE SELECT] Method used: {click_method_used}")
                debug.log(f"[DATE SELECT] ========================================")
            elif not is_date_clicked:
                debug.log(f"[DATE SELECT] ========================================")
                debug.log("[DATE SELECT] All click methods failed")
                debug.log(f"[DATE SELECT] ========================================")

    # Auto refresh if no date was selected (for strict mode or sold out scenarios)
    if not is_date_clicked and _is_page_reload_enabled(config_dict, debug, "DATE SELECT"):
        # Non-blocking cooldown (issue #3): record the deadline and return,
        # so the main loop keeps re-checking the page while waiting.
        interval = config_views.reload_interval(config_dict)
        previous_until = _state.get("date_reload_cooldown_until", 0)
        should_reload, cooldown_until = util.advance_reload_cooldown(
            time.time(), previous_until, interval)
        _state["date_reload_cooldown_until"] = cooldown_until
        if should_reload:
            debug.log(f"[DATE SELECT] No date selected, reloading page...")
            try:
                await tab.reload()
            except Exception:
                pass
        elif not previous_until:
            debug.log(f"[DATE SELECT] Waiting {interval}s before reload...")

    return is_date_clicked

async def nodriver_tixcraft_area_auto_select(tab, url, config_dict):
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)

    area_config = config_views.area_view(config_dict)
    # T010: Check main switch (defensive programming)
    if not area_config["enable"]:
        debug.log("[AREA SELECT] Main switch is disabled, skipping area selection")
        return False

    import json

    area_keyword = area_config["keyword"].strip()
    auto_select_mode = area_config["mode"]
    area_auto_fallback = area_config["fallback"]

    try:
        el = await tab.query_selector('.zone')
    except:
        return

    if not el:
        return

    # Batch pre-fetch: one JS call to get all area text and font data
    area_list_cache = None
    area_text_cache = None
    try:
        area_list_cache = await el.query_selector_all('a')
        area_text_cache = await tab.evaluate("""
            Array.from(document.querySelectorAll('.zone a')).map(a => ({
                text: a.innerText.trim(),
                fontText: a.querySelector('font')?.textContent?.trim() ?? ''
            }))
        """)
        if area_list_cache and area_text_cache and len(area_list_cache) != len(area_text_cache):
            area_text_cache = None
        if area_text_cache:
            debug.log(f"[AREA KEYWORD] Batch pre-fetch: {len(area_text_cache)} areas cached")
    except:
        area_list_cache = None
        area_text_cache = None

    is_need_refresh = False
    matched_blocks = None

    if area_keyword:
        # Parse keywords using JSON to avoid splitting keywords containing commas (e.g., "5,600")
        # Format: "\"keyword1\",\"keyword2\"" → ['keyword1', 'keyword2']
        # Supports OR logic - iterates through keywords until match found
        area_keyword_array = util.parse_keyword_string_to_array(area_keyword)
        area_keyword_array = list(dict.fromkeys(area_keyword_array))

        # T012: Start checking keywords log
        debug.log(f"[AREA KEYWORD] Start checking keywords in order: {area_keyword_array}")
        debug.log(f"[AREA KEYWORD] Total keyword groups: {len(area_keyword_array)}")

        # T011: Early return pattern - iterate keywords in priority order
        keyword_matched = False
        for keyword_index, area_keyword_item in enumerate(area_keyword_array):
            debug.log(f"[AREA KEYWORD] Checking keyword #{keyword_index + 1}: {area_keyword_item}")

            is_need_refresh, matched_blocks = await nodriver_get_tixcraft_target_area(
                el, config_dict, area_keyword_item,
                area_list_cache=area_list_cache, area_text_cache=area_text_cache)

            if not is_need_refresh:
                # T013: Keyword matched log
                keyword_matched = True
                debug.log(f"[AREA KEYWORD] Keyword #{keyword_index + 1} matched: '{area_keyword_item}'")
                break

        # T014: All keywords failed log
        if not keyword_matched:
            debug.log(f"[AREA KEYWORD] All keywords failed to match")

        # T022-T024: NEW - Conditional fallback based on area_auto_fallback switch
        is_fallback_selection = False  # Track selection type for logging
        if is_need_refresh and matched_blocks is None:
            if area_auto_fallback:
                # T022: Fallback enabled - use auto_select_mode without keyword
                debug.log(f"[AREA FALLBACK] area_auto_fallback=true, triggering auto fallback")
                debug.log(f"[AREA FALLBACK] Selecting available area based on area_select_order='{auto_select_mode}'")
                is_need_refresh, matched_blocks = await nodriver_get_tixcraft_target_area(
                    el, config_dict, "",
                    area_list_cache=area_list_cache, area_text_cache=area_text_cache)
                is_fallback_selection = True  # Mark as fallback selection
            else:
                # T023: Fallback disabled - strict mode (no selection, but still reload)
                debug.log(f"[AREA FALLBACK] area_auto_fallback=false, fallback is disabled")
                debug.log(f"[AREA SELECT] No area selected, will reload page and retry")
                # Don't return - let reload logic execute below
                # matched_blocks remains None (no selection will be made)
                # is_need_refresh remains True (will trigger reload)
    else:
        is_need_refresh, matched_blocks = await nodriver_get_tixcraft_target_area(
            el, config_dict, "",
            area_list_cache=area_list_cache, area_text_cache=area_text_cache)
        # No keyword specified, treat as mode-based selection (similar to fallback)
        if not area_keyword:
            is_fallback_selection = True

    # T024: Handle case when matched_blocks is empty or None (all options excluded or sold out)
    if matched_blocks is None or len(matched_blocks) == 0:
        debug.log(f"[AREA FALLBACK] No available options after exclusion")
        debug.log(f"[AREA SELECT] Will reload page and retry")
        # Don't return - let reload logic execute below
        is_need_refresh = True  # Ensure reload will happen
        target_area = None  # Skip selection when no options available
    else:
        target_area = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)
    if target_area:
        # T013: Log selected area with selection type
        if debug.enabled:
            try:
                # Element.text is a property and there is no inner_text attribute;
                # awaiting either raised and was swallowed, so this line never logged.
                area_html = await target_area.get_html()
                area_text = util.remove_html_tags(area_html)
                area_text = area_text[:80] if area_text else "Unknown"
                selection_type = "fallback" if is_fallback_selection else "keyword match"
                debug.log(f"[AREA SELECT] Selected area: {area_text} ({selection_type})")
            except:
                pass  # If text extraction fails, skip logging

        try:
            await target_area.click()
        except:
            try:
                # Element.apply, not Element.evaluate: zendriver defines
                # evaluate on Tab only and Element.__getattr__ returns None for
                # unknown names, so .evaluate(...) is a TypeError.
                await target_area.apply('(el) => el.click()')
            except:
                pass

    # Auto refresh if needed (non-blocking cooldown, issue #3: record the
    # deadline and return, so the main loop keeps re-checking the page
    # while waiting instead of sleeping through the interval)
    if is_need_refresh and _is_page_reload_enabled(config_dict, debug, "AREA SELECT"):
        interval = config_views.reload_interval(config_dict)
        previous_until = _state.get("area_reload_cooldown_until", 0)
        should_reload, cooldown_until = util.advance_reload_cooldown(
            time.time(), previous_until, interval)
        _state["area_reload_cooldown_until"] = cooldown_until
        if should_reload:
            debug.log(f"[AREA SELECT] Page reloading...")
            try:
                await tab.reload()
            except Exception:
                pass
        elif not previous_until:
            debug.log(f"[AREA SELECT] Waiting {interval}s before reload...")

async def nodriver_get_tixcraft_target_area(el, config_dict, area_keyword_item,
                                            area_list_cache=None, area_text_cache=None):
    area_config = config_views.area_view(config_dict)
    area_auto_select_mode = area_config["mode"]
    debug = util.create_debug_logger(config_dict)
    is_need_refresh = False
    matched_blocks = None

    # Display keyword information
    if debug.enabled:
        debug.log(f"[AREA KEYWORD] ========================================")
        if area_keyword_item:
            keyword_parts = area_keyword_item.split(' ')
            debug.log(f"[AREA KEYWORD] Raw input: '{area_keyword_item}'")
            debug.log(f"[AREA KEYWORD] Parsed (AND logic): {keyword_parts}")
            debug.log(f"[AREA KEYWORD] Total sub-keywords: {len(keyword_parts)}")
            debug.log(f"[AREA KEYWORD] Auto-select mode: {area_auto_select_mode}")
        else:
            debug.log(f"[AREA KEYWORD] No keyword specified, matching all areas")
            debug.log(f"[AREA KEYWORD] Auto-select mode: {area_auto_select_mode}")

    if not el:
        debug.log(f"[AREA KEYWORD] Element is None, cannot select area")
        return True, None

    if area_list_cache is not None:
        area_list = area_list_cache
    else:
        try:
            area_list = await el.query_selector_all('a')
        except:
            debug.log(f"[AREA KEYWORD] Failed to query area list")
            return True, None

    if not area_list or len(area_list) == 0:
        debug.log(f"[AREA KEYWORD] No areas found")
        return True, None

    debug.log(f"[AREA KEYWORD] Found {len(area_list)} area(s) to check")
    debug.log(f"[AREA KEYWORD] ========================================")

    matched_blocks = []
    area_index = 0
    for row in area_list:
        area_index += 1

        if area_text_cache is not None:
            row_text = area_text_cache[area_index - 1].get('text', '')
        else:
            try:
                row_html = await row.get_html()
                row_text = util.remove_html_tags(row_html)
            except:
                debug.log(f"[AREA KEYWORD] [{area_index}] Failed to get row content")
                break

        if not row_text or util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
            debug.log(f"[AREA KEYWORD] [{area_index}] Excluded by keyword_exclude")
            continue

        debug.log(f"[AREA KEYWORD] [{area_index}/{len(area_list)}] Checking: {row_text[:80]}...")

        row_text = util.format_keyword_string(row_text)

        # Check keyword match
        if area_keyword_item:
            keyword_parts = area_keyword_item.split(' ')

            debug.log(f"[AREA KEYWORD]   Matching AND keywords: {keyword_parts}")

            # Check each keyword individually for detailed feedback
            match_results = {}
            for kw in keyword_parts:
                formatted_kw = util.format_keyword_string(kw)
                kw_match = formatted_kw in row_text
                match_results[kw] = kw_match

                if debug.enabled:
                    status = "PASS" if kw_match else "FAIL"
                    debug.log(f"[AREA KEYWORD]     {status} '{kw}': {kw_match}")

            is_match = all(match_results.values())

            if debug.enabled:
                if is_match:
                    debug.log(f"[AREA KEYWORD]   All AND keywords matched")
                else:
                    debug.log(f"[AREA KEYWORD]   AND logic failed")

            if not is_match:
                continue
        else:
            debug.log(f"[AREA KEYWORD]   No keyword filter, accepting this area")

        # Check seat availability for multiple tickets
        allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
        if config_views.ticket_number(config_dict) > 1 and not allow_less_tickets:
            try:
                if area_text_cache is not None:
                    font_text = area_text_cache[area_index - 1].get('fontText', '')
                else:
                    font_text = ''
                    font_el = await row.query_selector('font')
                    if font_el:
                        # Element.apply, not Element.evaluate (None on an
                        # Element; see nodriver_tixcraft_area_auto_select).
                        font_text = await font_el.apply('(el) => el.textContent') or ''
                if font_text:
                    font_text = font_text.strip()

                    debug.log(f"[AREA KEYWORD]   Checking seats: {font_text}")

                    # The count is embedded in localized text, never a bare
                    # number: "剩餘 1" (zh) / "1 seat(s) remaining" (en). The old
                    # "@1@".."@9@" substring test therefore never matched and
                    # this guard silently passed every area through (#358).
                    # A status word instead of a count (熱賣中 / Available) means
                    # the page is not telling us how many are left, so the area
                    # is kept - the ticket-number page is the next line of
                    # defence.
                    seats_match = re.search(r'\d+', font_text)
                    if seats_match:
                        remaining = int(seats_match.group())
                        if remaining < config_views.ticket_number(config_dict):
                            debug.log(f"[AREA KEYWORD]   Insufficient seats (need {config_views.ticket_number(config_dict)}, only {remaining} available)")
                            continue
                        debug.log(f"[AREA KEYWORD]   Sufficient seats available ({remaining})")
                    else:
                        debug.log(f"[AREA KEYWORD]   No seat count in '{font_text}', keeping this area")
            except:
                pass

        matched_blocks.append(row)

        debug.log(f"[AREA KEYWORD]   → Area added to matched list (total: {len(matched_blocks)})")

        if area_auto_select_mode == util.CONST_FROM_TOP_TO_BOTTOM:
            debug.log(f"[AREA KEYWORD]   Mode is '{area_auto_select_mode}', stopping at first match")
            break

    if not matched_blocks:
        is_need_refresh = True
        matched_blocks = None

    return is_need_refresh, matched_blocks

async def nodriver_ticket_number_select_fill(tab, select_obj, ticket_number, select_id=None, allow_less_tickets=False):
    """簡化版本：參考 Chrome 邏輯設定票券數量，並檢查 option 是否可用

    Args:
        tab: NoDriver tab object
        select_obj: The select element (for compatibility)
        ticket_number: Target ticket count to select
        select_id: The specific select element ID to use (fixes Issue #200/#201)
        allow_less_tickets: Allow selecting the largest available count below ticket_number
    """
    is_ticket_number_assigned = False

    if select_obj is None and select_id is None:
        return is_ticket_number_assigned

    # Build JavaScript selector - prefer specific ID over querySelector
    if select_id:
        js_selector = f"document.getElementById('{select_id}')"
    else:
        js_selector = "document.querySelector('.mobile-select') || document.querySelector('select[id*=\"TicketForm_ticketPrice_\"]')"

    try:
        # 嘗試透過 JavaScript 設定選擇器的值，並檢查 option 是否 disabled
        result = await tab.evaluate(f'''
            (function() {{
                const select = {js_selector};
                if (!select) return {{success: false, error: "Select not found"}};

                // 售完關鍵字列表
                const soldOutKeywords = ["選購一空", "已售完", "Sold out", "No tickets available", "空席なし", "完売した"];

                // 先嘗試設定目標數量（檢查是否 disabled 或售完）
                const targetOption = Array.from(select.options).find(opt =>
                    opt.value === "{ticket_number}" &&
                    !opt.disabled &&
                    !soldOutKeywords.includes(opt.value)
                );

                if (targetOption) {{
                    select.value = "{ticket_number}";
                    select.selectedIndex = targetOption.index;
                    select.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{success: true, selected: "{ticket_number}"}};
                }}

                if (!{str(bool(allow_less_tickets)).lower()}) {{
                    return {{success: false, error: "Target ticket count unavailable"}};
                }}

                // Fallback: select max available option below target ticket count.
                const validOptions = Array.from(select.options).filter(opt =>
                    !opt.disabled &&
                    !soldOutKeywords.includes(opt.value) &&
                    parseInt(opt.value) > 0 &&
                    parseInt(opt.value) < parseInt("{ticket_number}") &&
                    !isNaN(parseInt(opt.value))
                );

                if (validOptions.length > 0) {{
                    const maxOption = validOptions.reduce((max, opt) =>
                        parseInt(opt.value) > parseInt(max.value) ? opt : max
                    );
                    select.value = maxOption.value;
                    select.selectedIndex = maxOption.index;
                    select.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return {{success: true, selected: maxOption.value, fallback: true}};
                }}

                return {{success: false, error: "No valid options (all disabled or sold out)"}};
            }})();
        ''')

        # 解析結果
        result = util.parse_nodriver_result(result)
        if isinstance(result, dict):
            is_ticket_number_assigned = result.get('success', False)

    except Exception as exc:
        pass

    return is_ticket_number_assigned

async def nodriver_tixcraft_assign_ticket_number(tab, config_dict):
    """
    Enhanced ticket type selection with keyword matching support
    支援票種關鍵字選擇（indievox 類型 B 頁面：直接跳到 /ticket/ticket/）
    """
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False

    debug = util.create_debug_logger(config_dict)
    is_ticket_number_assigned = False

    # 等待票券選擇器出現（智慧等待，取代固定 0.5 秒延遲）
    try:
        await tab.wait_for('.mobile-select, select[id*="TicketForm_ticketPrice_"]', timeout=2)
    except:
        pass  # Continue even if timeout, will try to find selectors below

    # 查找票券選擇器
    form_select_list = []
    try:
        form_select_list = await tab.query_selector_all('.mobile-select')
    except Exception as exc:
        debug.log("Failed to find .mobile-select")

    # 如果沒找到 .mobile-select，嘗試其他選擇器
    if len(form_select_list) == 0:
        try:
            form_select_list = await tab.query_selector_all('select[id*="TicketForm_ticketPrice_"]')
        except Exception as exc:
            debug.log("Failed to find ticket selector")

    form_select_count = len(form_select_list)

    if form_select_count > 0:
        debug.log(f"[TICKET SELECT] Found {form_select_count} select element(s)")

    # Get area keyword configuration
    import json
    area_config = config_views.area_view(config_dict)
    area_keyword = area_config["keyword"].strip()
    area_auto_fallback = area_config["fallback"]
    auto_select_mode = area_config["mode"]

    # Parse keywords using JSON
    area_keyword_array = util.parse_keyword_string_to_array(area_keyword)
    if area_keyword_array:
        debug.log(f"[TICKET SELECT] Area keywords: {area_keyword_array}")

    # 過濾並收集票種資訊（包含票種名稱）
    valid_ticket_types = []
    sold_out_keywords = ["選購一空", "已售完", "Sold out", "No tickets available", "空席なし", "完売した"]

    # 使用 NoDriver Element API 檢查每個 select 元素
    for idx, select_element in enumerate(form_select_list):
        try:
            # 更新元素以確保屬性載入
            await select_element.update()

            # 檢查 select 是否 disabled
            select_attrs = select_element.attrs or {}
            select_id = select_attrs.get('id', f'select_{idx}')
            is_select_disabled = 'disabled' in select_attrs

            if is_select_disabled:
                debug.log(f"[TICKET SELECT] Skipping disabled select: {select_id}")
                continue

            # 檢查 option 元素
            option_elements = await select_element.query_selector_all('option')
            has_valid_option = False
            option_values = []

            for option_element in option_elements:
                try:
                    await option_element.update()
                    option_attrs = option_element.attrs or {}
                    option_value = option_attrs.get('value', '')
                    option_text = option_element.text or ''
                    option_disabled = 'disabled' in option_attrs

                    option_values.append(option_value)

                    # 檢查是否為有效選項
                    if (option_value != "0" and
                        not option_disabled and
                        option_value not in sold_out_keywords and
                        option_text not in sold_out_keywords):
                        has_valid_option = True

                except Exception as opt_exc:
                    debug.log(f"[TICKET SELECT] Error checking option: {opt_exc}")
                    continue

            if not has_valid_option:
                debug.log(f"[TICKET SELECT] Skipping select (all options sold out or disabled): {select_id}")
                continue

            # 嘗試獲取票種名稱（從父元素 <tr> 中的 <h4> 或 <td> 提取）
            ticket_type_name = ""
            try:
                # 查找父元素 <tr>
                parent_row = select_element
                for _ in range(5):  # 最多向上查找 5 層
                    parent_row = parent_row.parent
                    if parent_row and parent_row.tag.lower() == 'tr':
                        break

                if parent_row and parent_row.tag.lower() == 'tr':
                    # Read the whole cell, not Element.text: that property returns
                    # only the first text node, so a name split across nested tags
                    # (e.g. <a>full</a>+<a>voucher</a>) would lose everything after
                    # the first fragment. Stay scoped to the name cell -- widening
                    # this to the <tr> would pull in the remark column, whose text
                    # can match keyword_exclude and wipe out every row.
                    name_element = await parent_row.query_selector('h4')
                    if not name_element:
                        name_element = await parent_row.query_selector('td.fcBlue')

                    if name_element:
                        name_html = await name_element.get_html()
                        ticket_type_name = util.remove_html_tags(name_html)

            except Exception as name_exc:
                debug.log(f"[TICKET SELECT] Failed to extract ticket type name: {name_exc}")

            # Apply keyword_exclude here so keyword matching and fallback share one
            # clean candidate list -- an excluded ticket type can never be picked
            # by the fallback branch below (same invariant as cityline's
            # _cityline_collect_available_areas).
            #
            # Guard the empty name: is_row_match_keyword reports a match for
            # empty text, so a ticket type whose name could not be read would
            # otherwise count as excluded and drop out of the fallback too.
            if ticket_type_name and util.reset_row_text_if_match_keyword_exclude(config_dict, ticket_type_name):
                debug.log(f"[TICKET SELECT] Excluded by keyword_exclude: '{ticket_type_name}'")
                continue

            # 加入 valid_ticket_types
            valid_ticket_types.append({
                'select': select_element,
                'id': select_id,
                'name': ticket_type_name,
                'index': idx
            })

            debug.log(f"[TICKET SELECT] Valid ticket type: {select_id} - '{ticket_type_name}'")

        except Exception as exc:
            debug.log(f"[TICKET SELECT] Error checking select element: {exc}")

    debug.log(f"[TICKET SELECT] Valid ticket types: {len(valid_ticket_types)}/{form_select_count}")

    if len(valid_ticket_types) == 0:
        debug.log("[TICKET SELECT] Warning: All ticket types are sold out or disabled")
        return False, None, None

    # Keyword matching logic (similar to area selection)
    matched_ticket = None
    is_keyword_matched = False

    if area_keyword_array:
        debug.log(f"[TICKET SELECT] Starting keyword matching with {len(area_keyword_array)} keyword(s)")

        for keyword_index, keyword_item in enumerate(area_keyword_array):
            debug.log(f"[TICKET SELECT] Checking keyword #{keyword_index + 1}: '{keyword_item}'")

            # Check each valid ticket type
            for ticket_info in valid_ticket_types:
                ticket_name = ticket_info['name']

                # Keyword matching (support space-separated AND logic)
                keyword_parts = keyword_item.split(' ')
                row_text = util.format_keyword_string(ticket_name)
                is_match = True

                for kw in keyword_parts:
                    formatted_kw = util.format_keyword_string(kw)
                    if formatted_kw not in row_text:
                        is_match = False
                        break

                if is_match:
                    matched_ticket = ticket_info
                    is_keyword_matched = True
                    debug.log(f"[TICKET SELECT]   [OK] Keyword matched: '{ticket_name}'")
                    break

            if matched_ticket:
                break  # Early return: first match wins

        if not matched_ticket:
            debug.log(f"[TICKET SELECT] All keywords failed to match")

    # Single option auto-select: when only one valid ticket type exists, select it
    # directly. keyword_exclude was already applied while collecting candidates.
    if not matched_ticket and len(valid_ticket_types) == 1:
        matched_ticket = valid_ticket_types[0]
        debug.log(f"[TICKET SELECT] Single option auto-select: '{matched_ticket['name']}'")

    # Fallback logic (similar to area selection)
    if not matched_ticket:
        if area_keyword_array and not area_auto_fallback:
            # Strict mode: no keyword match and fallback disabled
            debug.log(f"[TICKET SELECT] area_auto_fallback=false, fallback is disabled")
            debug.log(f"[TICKET SELECT] No ticket type selected")
            return False, None, None
        else:
            # Fallback enabled or no keyword specified
            if area_keyword_array:
                debug.log(f"[TICKET SELECT] area_auto_fallback=true, using fallback selection")

            # Select based on auto_select_mode
            matched_ticket = util.get_target_item_from_matched_list(
                [t['select'] for t in valid_ticket_types],
                auto_select_mode
            )
            # Find the ticket_info for the matched select
            for ticket_info in valid_ticket_types:
                if ticket_info['select'] == matched_ticket:
                    matched_ticket = ticket_info
                    break

            if matched_ticket:
                selection_type = "fallback" if area_keyword_array else "mode-based"
                debug.log(f"[TICKET SELECT] Selected ticket type ({selection_type}): '{matched_ticket['name']}'")

    # Use the matched ticket select
    select_obj = matched_ticket['select'] if matched_ticket else None
    form_select_count = len(valid_ticket_types)

    # Get select ID for JavaScript operations
    select_id = matched_ticket['id'] if matched_ticket else None

    # 檢查是否已經選擇了符合設定的票券數量
    if select_id:
        try:
            # 使用 JavaScript 取得當前選中的值（使用正確的 select ID）
            current_value = await tab.evaluate(f'''
                (function() {{
                    const select = document.getElementById('{select_id}');
                    return select ? select.value : "0";
                }})();
            ''')

            # 解析結果
            current_value = util.parse_nodriver_result(current_value)

            if current_value and current_value != "0" and str(current_value).isnumeric():
                target_ticket_number = int(config_views.ticket_number(config_dict, default=1))
                current_ticket_number = int(current_value)
                allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
                is_expected_count = current_ticket_number == target_ticket_number
                is_allowed_less_count = allow_less_tickets and 0 < current_ticket_number < target_ticket_number
                if is_expected_count or is_allowed_less_count:
                    is_ticket_number_assigned = True
                    debug.log(f"Ticket number already set to: {current_value}")
        except Exception as exc:
            debug.log(f"Failed to check current selected value: {exc}")

    # 回傳結果：select_obj 和 select_id 用於後續操作
    return is_ticket_number_assigned, select_obj, select_id

async def nodriver_tixcraft_ticket_main_agree(tab, config_dict):
    debug = util.create_debug_logger(config_dict)

    debug.log("Starting to check agreement checkbox")

    for i in range(3):
        is_finish_checkbox_click = await nodriver_check_checkbox_enhanced(tab, '#TicketForm_agree', config_dict)
        if is_finish_checkbox_click:
            debug.log("Agreement checkbox checked successfully")
            break
        else:
            debug.log(f"Failed to check agreement, retry {i+1}/3")
            # The page is often still rendering on the first pass; space the
            # retries so they do not all read the same unfinished DOM.
            await tab.sleep(0.3)

    if not is_finish_checkbox_click:
        debug.log("Warning: Failed to check agreement checkbox")

async def nodriver_tixcraft_ticket_main(tab, config_dict, ocr, Captcha_Browser, domain_name):
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False
    debug = util.create_debug_logger(config_dict)

    # 檢查是否已經設定過票券數量（方案 B：狀態標記）
    current_url, _ = await nodriver_current_url(tab)
    ticket_number = str(config_views.ticket_number(config_dict))
    allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False)
    ticket_state_key = f"ticket_assigned_{current_url}_{ticket_number}_{int(allow_less_tickets)}"

    # Skip this iteration while a captcha submit is in flight (awaiting navigation).
    if _state.get("captcha_submit_until", 0) > time.time():
        debug.log("[TIXCRAFT OCR] Submit in progress, waiting for navigation")
        return

    if ticket_state_key in _state and _state[ticket_state_key]:
        debug.log(f"Ticket number already set ({ticket_number}), skipping")

        # Ensure agreement checkbox is checked (even if ticket number already set)
        await nodriver_tixcraft_ticket_main_agree(tab, config_dict)

        # Reset OCR state if captcha alert detected (wrong answer submitted)
        if _state.get("captcha_alert_detected", False):
            _state["ocr_completed_url"] = ""
            _state["captcha_alert_detected"] = False

        # Skip OCR if already completed on this URL (non-force_submit mode only)
        captcha_config = config_views.captcha_view(config_dict)
        is_force_submit = captcha_config["force_submit"]
        if is_force_submit or _state.get("ocr_completed_url", "") != current_url:
            await nodriver_tixcraft_ticket_main_ocr(tab, config_dict, ocr, Captcha_Browser, domain_name)
        return

    # Always check agreement checkbox in NoDriver mode
    await nodriver_tixcraft_ticket_main_agree(tab, config_dict)

    is_ticket_number_assigned = False

    # PS: some events on tixcraft have multi <select>.
    # Fix Issue #200/#201: Now returns select_id for correct element targeting
    is_ticket_number_assigned, select_obj, select_id = await nodriver_tixcraft_assign_ticket_number(tab, config_dict)

    if not is_ticket_number_assigned:
        debug.log(f"Setting ticket number: {ticket_number}")
        is_ticket_number_assigned = await nodriver_ticket_number_select_fill(
            tab,
            select_obj,
            ticket_number,
            select_id,
            allow_less_tickets=allow_less_tickets,
        )

    # Record state after successful setting
    if is_ticket_number_assigned:
        _state[ticket_state_key] = True
        _cap_ticket_assigned_markers()
        debug.log("Ticket number set successfully, starting OCR captcha processing")
        await nodriver_tixcraft_ticket_main_ocr(tab, config_dict, ocr, Captcha_Browser, domain_name)
    else:
        # T026: Fix Issue #174 - reload page when ticket number cannot be set
        # This prevents infinite loop when desired ticket count is unavailable
        debug.log("[TICKET SELECT] Ticket count unavailable, reloading page to retry...")
        try:
            # Wait based on auto_reload_page_interval setting
            interval = config_views.reload_interval(config_dict)
            if interval > 0:
                await asyncio.sleep(interval)
            await tab.reload()
        except Exception as reload_exc:
            debug.log(f"[TICKET SELECT] Reload failed: {reload_exc}")


async def nodriver_tixcraft_has_captcha(tab):
    """True when this ticket page actually shows an image captcha.

    Some events use invisible reCAPTCHA and have no #TicketForm_verifyCode,
    which every captcha path here assumes exists. A detection failure answers
    True: guessing False on a captcha page would submit an empty answer.
    """
    try:
        result = await tab.evaluate('''
            (function() {
                return !!(document.querySelector('#TicketForm_verifyCode') ||
                          document.querySelector('#TicketForm_verifyCode-image'));
            })();
        ''')
        return result is not False
    except Exception:
        return True


def _forget_ticket_assigned(current_url):
    """Drop the "ticket count already set" marks nodriver_tixcraft_ticket_main
    keeps for this URL, so the next pass assigns the count again."""
    prefix = f"ticket_assigned_{current_url}_"
    for key in [k for k in _state if k.startswith(prefix)]:
        del _state[key]


# The ticket_assigned_* markers are kept for the run (see the
# declaration in tixcraft_shared), but the key embeds the full URL,
# so a long run across many events would grow the family without
# bound. Cap it, evicting the oldest markers first: an evicted URL
# degrades to the first-visit path and simply re-runs the idempotent
# ticket-count assignment (issue #92).
CONST_TIXCRAFT_TICKET_ASSIGNED_MAX = 128


def _cap_ticket_assigned_markers():
    """Evict the oldest ticket_assigned_* markers beyond the cap."""
    keys = [k for k in _state if k.startswith("ticket_assigned_")]
    excess = len(keys) - CONST_TIXCRAFT_TICKET_ASSIGNED_MAX
    if excess <= 0:
        return
    for key in keys[:excess]:
        del _state[key]


async def nodriver_tixcraft_submit_without_captcha(tab, config_dict):
    """Agree, confirm the ticket count, and submit a ticket page with no captcha.

    The captcha flow cannot submit here: it is gated on the input existing and
    its readiness check requires verify.value.length === 4.
    """
    debug = util.create_debug_logger(config_dict)

    await nodriver_check_checkbox_enhanced(tab, '#TicketForm_agree', config_dict)

    # nodriver_tixcraft_ticket_main has already assigned the count, so only
    # confirm it here rather than duplicate keyin_captcha_code's reset.
    ticket_number_js = json.dumps(str(config_views.ticket_number(config_dict)))
    allow_less_js = json.dumps(bool(
        config_dict.get("tixcraft", {}).get("allow_less_tickets", False)))
    ready = await tab.evaluate(f'''
        (function() {{
            const target = parseInt({ticket_number_js});
            const allowLess = {allow_less_js};
            const agree = document.querySelector('#TicketForm_agree');
            const selects = Array.from(document.querySelectorAll(
                '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
            )).filter(s => s && !s.disabled);
            const matched = selects.find(s => {{
                if (s.value === "0" || s.value === "") return false;
                const current = parseInt(s.value);
                if (isNaN(current) || isNaN(target)) return false;
                return allowLess ? (current > 0 && current <= target) : (current === target);
            }});
            return {{
                ticket: !!matched,
                agree: !!(agree && agree.checked),
                ready: !!matched && !!(agree && agree.checked)
            }};
        }})();
    ''')

    if not isinstance(ready, dict) or not ready.get('ready'):
        got = ready if isinstance(ready, dict) else {}
        debug.log("[TIXCRAFT] No captcha on this page, but the form is not ready "
                  f"- Ticket:{got.get('ticket')} Agreement:{got.get('agree')}")
        if got.get('ticket') is False:
            # The count went back to 0 (refused submit or back navigation on
            # the same URL); nodriver_tixcraft_ticket_main skips assignment
            # while the URL is marked done, so clear the mark.
            current_url, _ = await nodriver_current_url(tab)
            _forget_ticket_assigned(current_url)
            debug.log("[TIXCRAFT] Ticket count was reset, re-assigning next round")
        return False

    captcha_config = config_views.captcha_view(config_dict)
    if not captcha_config["force_submit"]:
        debug.log("[TIXCRAFT] No captcha on this page; agreement and ticket count "
                  "are set. Auto-submit is off, so the order is left for you.")
        return False

    # Click the button: Enter needs focus in the (absent) captcha input, and
    # form.submit() skips the page's handler that enforces agreement and quota.
    clicked = await tab.evaluate('''
        (function() {
            const form = document.querySelector('#form-ticket-ticket');
            if (!form) return false;
            const btn = form.querySelector('button[type="submit"], input[type="submit"]');
            if (!btn || btn.disabled) return false;
            btn.click();
            return true;
        })();
    ''')

    if clicked:
        debug.log("[TIXCRAFT] Submitted a page with no captcha")
        # Same guard the captcha path sets: keep the main loop from re-entering
        # this page while the POST is still navigating.
        _state["captcha_submit_until"] = time.time() + 1.5
    else:
        debug.log("[TIXCRAFT] No captcha on this page and no submit button found")
    return bool(clicked)


async def nodriver_tixcraft_ticket_main_ocr(tab, config_dict, ocr, Captcha_Browser, domain_name):
    """票券頁面 OCR 處理主函數"""
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False, "", False

    debug = util.create_debug_logger(config_dict)

    # Handle the no-captcha case before the ocr_captcha.enable split: with OCR
    # off the manual branch below only focuses the captcha input, so an event
    # without one reaches the same dead end by a different route.
    if not await nodriver_tixcraft_has_captcha(tab):
        await nodriver_tixcraft_submit_without_captcha(tab, config_dict)
        return

    captcha_config = config_views.captcha_view(config_dict)
    away_from_keyboard_enable = captcha_config["force_submit"]
    if not captcha_config["enable"]:
        away_from_keyboard_enable = False
    ocr_captcha_image_source = captcha_config["image_source"]

    if not captcha_config["enable"]:
        # 手動模式
        await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
    else:
        # 自動 OCR 模式
        previous_answer = None
        current_url, _ = await nodriver_current_url(tab)
        fail_count = 0  # Track consecutive failures
        total_fail_count = 0  # Track total failures
        is_form_submitted = False

        for redo_ocr in range(5):
            is_need_redo_ocr, previous_answer, is_form_submitted = await nodriver_tixcraft_auto_ocr(
                tab, config_dict, ocr, away_from_keyboard_enable,
                previous_answer, Captcha_Browser, ocr_captcha_image_source, domain_name
            )

            if is_form_submitted:
                debug.log("[TIXCRAFT OCR] Form submitted")
                break

            if not away_from_keyboard_enable:
                break

            if not is_need_redo_ocr:
                break

            # Track failures; after 3 consecutive failures dismiss any alert and wait
            # (the captcha is not reloaded here)
            if is_need_redo_ocr:
                fail_count += 1
                total_fail_count += 1
                debug.log(f"[TIXCRAFT OCR] Fail count: {fail_count}, Total fails: {total_fail_count}")

                # Check if total failures reached 5, switch to manual input mode
                if total_fail_count >= 5:
                    print("[TIXCRAFT OCR] OCR failed 5 times. Please enter captcha manually.")
                    away_from_keyboard_enable = False
                    await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                    break

                if fail_count >= 3:
                    debug.log("[TIXCRAFT OCR] 3 consecutive failures reached")

                    # Try to dismiss any existing alert before continuing
                    try:
                        await tab.send(cdp.page.handle_java_script_dialog(accept=True))
                        debug.log("[TIXCRAFT OCR] Dismissed existing alert")
                    except:
                        pass

                    # Wait for potential auto-refresh
                    await asyncio.sleep(2.5)
                    fail_count = 0  # Reset consecutive counter after handling

            # 檢查是否還在同一頁面
            new_url, _ = await nodriver_current_url(tab)
            if new_url != current_url:
                break

            debug.log(f"[TIXCRAFT OCR] Retry {redo_ocr + 1}/5")

        # Mark OCR completed for this URL only when form was actually submitted
        if is_form_submitted:
            _state["ocr_completed_url"] = current_url


async def nodriver_tixcraft_main(ctx):
    tab = ctx.tab
    url = ctx.url
    config_dict = ctx.config_dict
    ocr = ctx.ocr
    Captcha_Browser = ctx.captcha_browser

    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return None

    debug = util.create_debug_logger(config_dict)

    # Global alert handler - auto-dismiss all alerts (sold out, errors, etc.)
    # Handles alerts that appear after page navigation (e.g., area selection redirects)
    # Reference: KHAM platform implementation (platforms/kham.py)
    async def handle_global_alert(event):
        # Skip alert handling when bot is paused (let user handle manually)
        if os.path.exists(util.get_instance_state_path(CONST_MAXBOT_INT28_FILE)):
            return
        # IMPORTANT: Use tab.target.url (cached) instead of nodriver_current_url (js_dumps)
        # When alert dialog is open, JavaScript execution is blocked, causing js_dumps to hang
        current_url = tab.target.url if hasattr(tab, 'target') and tab.target else ""

        if _should_leave_dialog_to_user(current_url):
            debug.log(f"[GLOBAL ALERT] Alert on checkout page, NOT auto-dismissing: '{event.message}'")
            return

        debug.log(f"[GLOBAL ALERT] Alert detected: '{event.message}'")

        # Track captcha error alerts for retry logic
        is_captcha_error = False
        captcha_error_keywords = [
            'verification code',
            'incorrect',
            'try again',
            'captcha',
            'wrong code'
        ]
        alert_message_lower = event.message.lower()
        for keyword in captcha_error_keywords:
            if keyword in alert_message_lower:
                is_captcha_error = True
                break

        if is_captcha_error:
            _state["captcha_alert_detected"] = True
            # Wrong answer submitted: clear the submit guard so retry is immediate.
            _state["captcha_submit_until"] = 0
            debug.log(f"[GLOBAL ALERT] Captcha error detected, flagging for retry")

        # Issue #188: Detect sold out alerts to add cooldown delay
        sold_out_keywords = ['售完', '已售完', '選購一空', 'sold out', 'no tickets']
        is_sold_out_alert = any(kw in alert_message_lower for kw in sold_out_keywords)

        # Dismiss the alert - try multiple times with small delays
        dismiss_success = False
        for attempt in range(3):
            try:
                await tab.send(cdp.page.handle_java_script_dialog(accept=True))
                dismiss_success = True
                debug.log(f"[GLOBAL ALERT] Alert dismissed (attempt {attempt + 1})")
                break
            except Exception as dismiss_exc:
                error_msg = str(dismiss_exc)
                # CDP -32602 means no dialog is showing (already dismissed by another handler or user)
                if "No dialog is showing" in error_msg or "-32602" in error_msg:
                    dismiss_success = True  # Consider it handled
                    debug.log("[GLOBAL ALERT] Dialog already dismissed")
                    break  # No need to retry
                if attempt < 2:
                    await asyncio.sleep(0.1)  # Small delay before retry
                else:
                    debug.log(f"[GLOBAL ALERT] Failed to dismiss alert: {dismiss_exc}")

        # Issue #188: Set cooldown timestamp instead of async sleep (event handler doesn't block main loop)
        if is_sold_out_alert and dismiss_success:
            interval = config_views.reload_interval(config_dict)
            if interval > 0:
                cooldown_until = time.time() + interval
                _state["sold_out_cooldown_until"] = cooldown_until
                debug.log(f"[GLOBAL ALERT] Sold out detected, setting cooldown for {interval}s")

    if not _state:
        _state.update({
            "fail_list": [],
            "fail_promo_list": [],
            "start_time": None,
            "done_time": None,
            "elapsed_time": None,
            "is_popup_checkout": False,
            "area_retry_count": 0,
            "played_sound_ticket": False,
            "played_sound_order": False,
            "alert_handler_registered": False,
            "captcha_alert_detected": False,
            "captcha_submit_until": 0,
            "ocr_completed_url": "",
            "last_homepage_redirect_time": 0,
            "sold_out_cooldown_until": 0,
            "printed_completed": False,
            "ticketmaster_phase": "area_select",
            "ticketmaster_captcha_processed_url": "",
            "ip_block_until": 0,
            "queue_it_enter_time": None,
        })

    # T010 page classification, computed once per pass and shared by the
    # page-state lifecycle below and the Ticketmaster date selection
    # further down. Both URL formats are supported:
    # - /artist/{artist_id} (artist listing page)
    # - /activity/game/{event_id} (event date listing page from /activity/detail redirect)
    is_ticketmaster_date_page = (
        'ticketmaster' in url and
        (('/artist/' in url and len(url.split('/')) == 6) or
         ('/activity/game/' in url) or
         ('/activity/detail/' in url))
    )

    # Page state lifecycle (issue #7): classify the page once per pass;
    # PageState clears the page keys owned by any other page, so a stale
    # value can neither fire nor postpone work on a different page.
    if is_ticketmaster_date_page:
        _page_id = _PAGE_TICKETMASTER_DATE
    elif "/activity/game/" in url:
        _page_id = _PAGE_DATE
    elif '/ticket/area/' in url:
        _page_id = _PAGE_TICKETMASTER_AREA if 'ticketmaster' in url else _PAGE_AREA
    elif '/ticket/' in url:
        _page_id = _PAGE_TICKET
    else:
        _page_id = None
    _state.set_page(_page_id)

    date_config = config_views.date_view(config_dict)
    sound_config = config_views.play_sound_view(config_dict)

    # Register global alert handler (remains active throughout session)
    # Only register once to prevent infinite loop
    if not _state.get("alert_handler_registered", False):
        try:
            tab.add_handler(cdp.page.JavascriptDialogOpening, handle_global_alert)
            _state["alert_handler_registered"] = True
            debug.log(f"[GLOBAL ALERT] Global alert handler registered")
        except Exception as handler_exc:
            debug.log(f"[GLOBAL ALERT] Failed to register alert handler: {handler_exc}")

    # Queue-it virtual waiting room detection (TixCraft + Ticketmaster SG)
    # Pattern: ported from platforms/ibon.py — URL-based, customerId-agnostic.
    # Both family platforms route high-traffic users to *.queue-it.net before EPS evaluates.
    was_in_queue = _state.get("queue_it_enter_time") is not None
    should_pause, elapsed = _process_queue_it_state(url, _state, time.time())
    if should_pause:
        if not was_in_queue:
            debug.log("[TIXCRAFT] Queue-IT entered, waiting...")
        return None
    if elapsed is not None:
        debug.log(f"[TIXCRAFT] Queue-IT passed (waited {elapsed:.1f}s)")

    await nodriver_tixcraft_home_close_window(tab)

    # EPS block detection for TixCraft family and Ticketmaster domains (Issue #289)
    if _is_tixcraft_soft_block_scope(url) or 'ticketmaster' in url:
        if await nodriver_ticketmaster_check_ip_block(tab, config_dict, current_url=url):
            return None

    # special case for same event re-open, redirect to user's homepage.
    # Add cooldown to prevent infinite redirect loop when area page is unavailable
    # Match homepage URLs: tixcraft.com, tixcraft.com/, tixcraft.com/activity
    is_tixcraft_home = url in ['https://tixcraft.com', 'https://tixcraft.com/', 'https://tixcraft.com/activity']
    if is_tixcraft_home:
        homepage = config_dict["homepage"]
        # Only redirect if homepage is not the platform root itself (avoid infinite loop)
        homepage_is_root = homepage.rstrip('/') in ['https://tixcraft.com', 'https://tixcraft.com/activity']
        if not homepage_is_root:
            current_time = time.time()
            last_redirect_time = _state.get("last_homepage_redirect_time", 0)
            # Use auto_reload_page_interval from settings, default to 3 seconds
            redirect_interval = config_dict["advanced"].get("auto_reload_page_interval", 3)
            if redirect_interval <= 0:
                redirect_interval = 3  # Minimum 3 seconds to prevent rapid loop

            if current_time - last_redirect_time > redirect_interval:
                try:
                    _state["last_homepage_redirect_time"] = current_time
                    await tab.get(homepage)
                except Exception:
                    pass

    # Ticketmaster embeds the date list (#gameList) directly in the detail page
    # and has no /activity/game/ URL at all, so the TixCraft detail->game
    # redirect can never fire there. The Ticketmaster date handler below claims
    # the detail page instead (issue #378).
    if "/activity/detail/" in url and 'ticketmaster' not in url:
        _state["start_time"] = time.time()
        is_redirected = await nodriver_tixcraft_redirect(tab, url)

    is_date_selected = False
    # Check if this is a Ticketmaster page before using TixCraft logic
    if "/activity/game/" in url and 'ticketmaster' not in url:
        _state["start_time"] = time.time()
        if date_config["enable"]:
            domain_name = url.split('/')[2]
            is_date_selected = await nodriver_tixcraft_date_auto_select(tab, url, config_dict, domain_name)

    # T010: Ticketmaster date selection integration (User Story 1)
    # is_ticketmaster_date_page is computed once per pass, next to the
    # page-state classification near the top of this function.
    if is_ticketmaster_date_page:
        _state["start_time"] = time.time()
        if date_config["enable"]:
            debug.log("[TICKETMASTER] Detected Ticketmaster date page, calling date auto select")
            domain_name = url.split('/')[2]
            # Call Ticketmaster date auto select
            is_date_selected = await nodriver_ticketmaster_date_auto_select(tab, config_dict)
            if debug.enabled:
                if is_date_selected:
                    debug.log("[TICKETMASTER] Date selection completed")
                else:
                    debug.log("[TICKETMASTER] Date selection failed or no match")

    # choose area
    if '/ticket/area/' in url:
        domain_name = url.split('/')[2]
        area_config = config_views.area_view(config_dict)
        if area_config["enable"]:
            if not 'ticketmaster' in domain_name:
                # for tixcraft
                await nodriver_tixcraft_area_auto_select(tab, url, config_dict)

                _state["area_retry_count"]+=1
                #print("count:", _state["area_retry_count"])
                if _state["area_retry_count"] >= (60 * 15):
                    # Cool-down
                    _state["area_retry_count"] = 0
                    await asyncio.sleep(5)
            else:
                # T013+T017: Ticketmaster area + ticket number (phase-based state machine)
                # Ticketmaster uses AJAX on same URL: area selection -> ticketPriceList loads -> set ticket number
                # Phase: area_select -> wait_ticket -> done (reset when URL leaves /ticket/area/)
                phase = _state.get("ticketmaster_phase", "area_select")

                if phase == "area_select":
                    zone_info = await nodriver_ticketmaster_parse_zone_info(tab, config_dict)
                    if zone_info:
                        await nodriver_ticketmaster_area_auto_select(tab, config_dict, zone_info)
                        _state["ticketmaster_phase"] = "wait_ticket"
                        _state["area_retry_count"] = 0
                    else:
                        _state["area_retry_count"] += 1

                elif phase == "wait_ticket":
                    is_assigned = await nodriver_ticketmaster_assign_ticket_number(tab, config_dict)
                    if is_assigned:
                        _state["fail_promo_list"] = await nodriver_ticketmaster_promo(tab, config_dict, _state["fail_promo_list"])
                        _state["ticketmaster_phase"] = "done"
                        _state["area_retry_count"] = 0
                    else:
                        _state["area_retry_count"] += 1
                        if _state["area_retry_count"] >= 30:
                            # Either ticketPriceList never loaded, or the wanted
                            # ticket count is unavailable and allow_less_tickets
                            # is off. Re-selecting the area alone is not enough:
                            # with an empty area_keyword the area step matches
                            # everything and never reloads, so the phase machine
                            # would spin on a stale page forever and miss any
                            # returned ticket. Reload (throttled) like the
                            # TixCraft ticket page does (issue #378 / #174).
                            debug.log("[TICKETMASTER] Ticket assignment failed after 30 retries, reloading page")
                            _state["ticketmaster_phase"] = "area_select"
                            _state["area_retry_count"] = 0
                            try:
                                reload_interval = config_views.reload_interval(config_dict)
                                if reload_interval > 0:
                                    await sleep_with_pause_check(tab, reload_interval, config_dict)
                                await tab.reload()
                            except Exception:
                                pass

                # phase == "done": no-op, wait for POST navigation to /ticket/ticket/
    else:
        _state["fail_promo_list"] = []
        _state["area_retry_count"] = 0
        _state["ticketmaster_phase"] = "area_select"

    # T020: Ticketmaster captcha integration (User Story 4)
    # https://ticketmaster.sg/ticket/check-captcha/23_blackpink/954/5/75
    if '/ticket/check-captcha/' in url:
        domain_name = url.split('/')[2]
        if 'ticketmaster' in domain_name:
            # Check if we already processed this captcha page (avoid repeated execution)
            ticketmaster_captcha_processed = _state.get("ticketmaster_captcha_processed_url", "")
            if ticketmaster_captcha_processed != url:
                # Call Ticketmaster captcha handler
                await nodriver_ticketmaster_captcha(tab, config_dict, ocr, Captcha_Browser)
                # Mark this URL as processed
                _state["ticketmaster_captcha_processed_url"] = url
    else:
        # Reset captcha processed state when leaving captcha page
        _state["ticketmaster_captcha_processed_url"] = ""

    if '/ticket/verify/' in url:
        # Tixcraft verify handler (already implemented)
        _state["fail_list"] = await nodriver_tixcraft_verify(tab, config_dict, _state["fail_list"])
    else:
        _state["fail_list"] = []

    # main app, to select ticket number.
    if '/ticket/ticket/' in url:
        domain_name = url.split('/')[2]
        await nodriver_tixcraft_ticket_main(tab, config_dict, ocr, Captcha_Browser, domain_name)
        _state["done_time"] = time.time()

        if not _state["played_sound_ticket"]:
            if sound_config["ticket"]:
                play_sound_while_ordering(config_dict)
        _state["played_sound_ticket"] = True
    else:
        _state["played_sound_ticket"] = False

    if '/ticket/order' in url:
        _state["done_time"] = time.time()

    is_quit_bot = False
    if '/ticket/checkout' in url:
        if not _state["start_time"] is None:
            if not _state["done_time"] is None:
                bot_elapsed_time = _state["done_time"] - _state["start_time"]
                if _state["elapsed_time"] != bot_elapsed_time:
                    print("bot elapsed time:", "{:.3f}".format(bot_elapsed_time))
                _state["elapsed_time"] = bot_elapsed_time

        # Always set is_quit_bot when checkout page is detected (not just in headless mode)
        if not _state["is_popup_checkout"]:
            is_quit_bot = True
            _state["is_popup_checkout"] = True

            # Issue #193: Move inside the block to execute only once on first checkout detection
            # Headless-specific behavior: open checkout URL in new browser window
            if config_dict["advanced"]["headless"]:
                domain_name = url.split('/')[2]
                checkout_url = "https://%s/ticket/checkout" % (domain_name)
                print("Ticket purchase successful, please check order at: %s" % (checkout_url))
                webbrowser.open_new(checkout_url)

        if not _state["played_sound_order"]:
            if sound_config["order"]:
                play_sound_while_ordering(config_dict)
            send_discord_notification(config_dict, "order", "TixCraft")
            send_telegram_notification(config_dict, "order", "TixCraft")
        _state["played_sound_order"] = True
    else:
        _state["is_popup_checkout"] = False
        _state["played_sound_order"] = False
        _state["printed_completed"] = False

    # Approach B: handle printed_completed internally
    if is_quit_bot:
        if not _state.get("printed_completed", False):
            print("TixCraft ticket purchase completed")
            _state["printed_completed"] = True

    # Platform interface (issue #34): the former is_quit_bot return was
    # neutralized by the dispatcher on every pass, so the main now
    # returns None (keep the current tab) like every other entry point.
    return None


# Platform interface (issue #34, ADR 0005): this module's dispatch entries.
PLATFORM_ENTRIES = [
    PlatformEntry(
        "tixcraft",
        nodriver_tixcraft_main,
        patterns=("tixcraft.com", "indievox.com", "ticketmaster."),
    ),
]

