#encoding=utf-8
# =============================================================================
# KHAM Platform Module
# Extracted from nodriver_tixcraft.py during modularization (Phase 1)
# Contains: kham.com.tw, ticket.com.tw, udnfunlife.com (kham family)
# Also includes shared seat selection functions (nodriver_ticket_seat_*)
# =============================================================================

import asyncio
import base64
import json
import os
import random
import re
import time
import traceback
import urllib.parse
import webbrowser

from zendriver import cdp

import util
import date_selection
import config_views
from nodriver_common import (
    PlatformEntry,
    PageState,
    asyncio_sleep_with_pause_check,
    check_and_handle_pause,
    nodriver_check_checkbox,
    nodriver_force_check_checkbox,
    nodriver_check_modal_dialog_popup,
    nodriver_get_captcha_image_from_dom_snapshot,
    play_sound_while_ordering,
    send_discord_notification,
    send_telegram_notification,
    write_question_to_file,
    CONST_FROM_TOP_TO_BOTTOM,
    CONST_MAXBOT_ANSWER_ONLINE_FILE,
    CONST_MAXBOT_INT28_FILE,
)

from platforms.kham_shared import (
    CONST_KHAM_RELOAD_SETTLE,
    _handle_post_submit_dialog,
    _state,
    _utk_rwd_wait_for,
    is_utk_rwd_url,
    nodriver_kham_auto_ocr,
    nodriver_kham_captcha,
    nodriver_kham_keyin_captcha_code,
    nodriver_kham_refresh_captcha,
)
from platforms.kham_ticket_seat import (
    nodriver_ticket_allow_not_adjacent_seat,
    nodriver_ticket_check_seat_taken_dialog,
    nodriver_ticket_close_dialog_with_retry,
    nodriver_ticket_login,
    nodriver_ticket_seat_auto_select,
    nodriver_ticket_seat_main,
    nodriver_ticket_seat_type_auto_select,
    nodriver_ticket_switch_to_auto_seat,
)
from platforms.kham_udn_seat import (
    nodriver_udn_seat_auto_select,
    nodriver_udn_seat_main,
    nodriver_udn_seat_select_ticket_type,
)
from platforms.kham_seat import (
    nodriver_kham_allow_not_adjacent_seat,
    nodriver_kham_handle_seat_unavailable,
    nodriver_kham_seat_auto_select,
    nodriver_kham_seat_main,
    nodriver_kham_seat_type_auto_select,
    nodriver_kham_switch_to_auto_seat,
)
from platforms.kham_rwd import (
    CONST_UTK_RWD_AREA_ROW_SELECTOR,
    CONST_UTK_RWD_DATE_ROW_SELECTORS,
    nodriver_utk_rwd_assign_ticket_number,
    nodriver_utk_rwd_ensure_login,
    normalize_utk_rwd_url,
)

__all__ = [
    "PLATFORM_ENTRIES",
    "nodriver_kham_login",
    "nodriver_kham_go_buy_redirect",
    "nodriver_kham_check_realname_dialog",
    "nodriver_kham_allow_not_adjacent_seat",
    "nodriver_kham_switch_to_auto_seat",
    "nodriver_kham_check_captcha_text_error",
    "nodriver_kham_product",
    "nodriver_kham_date_auto_select",
    "nodriver_kham_keyin_captcha_code",
    "nodriver_kham_area_auto_select",
    "nodriver_kham_auto_ocr",
    "nodriver_kham_captcha",
    "nodriver_kham_performance",
    "nodriver_kham_main",
    "nodriver_ticket_login",
    "nodriver_kham_seat_type_auto_select",
    "nodriver_kham_seat_auto_select",
    "nodriver_kham_seat_main",
    "nodriver_kham_handle_seat_unavailable",
    "nodriver_udn_seat_auto_select",
    "nodriver_udn_seat_select_ticket_type",
    "nodriver_udn_seat_main",
    "nodriver_ticket_seat_type_auto_select",
    "nodriver_ticket_seat_auto_select",
    "nodriver_ticket_seat_main",
    "nodriver_ticket_check_seat_taken_dialog",
    "nodriver_ticket_close_dialog_with_retry",
    "nodriver_ticket_allow_not_adjacent_seat",
    "nodriver_ticket_switch_to_auto_seat",
    "is_utk_rwd_url",
]


def _parse_remaining_seats(row_text):
    """Remaining seats for an area row - the last column of the kham table.

    Reads the whole trailing token ("... 4,280 50" -> 50), not the last
    character. Returns None when that token is not a number (button label,
    empty cell, sold-out marker); callers treat None as "unknown, let it through".
    """
    if not row_text:
        return None
    tokens = row_text.split()
    if not tokens:
        return None
    candidate = tokens[-1].replace(',', '')
    return int(candidate) if candidate.isdigit() else None


# Page ids for PageState: the main function classifies the current page
# into one of these per pass. Declarations and set_page share the names
# so a typo cannot silently disable a key's clearing.
_PAGE_HOME = "home"
_PAGE_SEAT = "seat"
_PAGE_ACTIVITY_GROUP = "activity_group"
_PAGE_ACTIVITY_GROUP_ITEM = "activity_group_item"
_PAGE_UTK_TICKET = "utk_ticket"
_PAGE_UTK_DATE_AREA = "utk_date_area"


# ====================================================================================


# ====================================================================================
# Kham Platform (kham.com.tw / ticket.com.tw / udnfunlife.com)
# ====================================================================================

async def nodriver_kham_login(tab, account, password, ocr=None, config_dict=None):
    """
    Kham platform login with OCR captcha support
    """
    ret = False
    debug = util.create_debug_logger(config_dict)

    # Find email/account input
    el_email = None
    try:
        el_email = await tab.query_selector('#ACCOUNT')
    except Exception as exc:
        debug.log("Find #ACCOUNT fail:", exc)

    # Input account
    is_email_sent = False
    if el_email:
        try:
            inputed_text = await tab.evaluate('document.querySelector("#ACCOUNT").value')
            if not inputed_text or len(inputed_text) == 0:
                await el_email.send_keys(account)
                is_email_sent = True
            else:
                if inputed_text == account:
                    is_email_sent = True
        except Exception as exc:
            debug.log("Input account fail:", exc)

    # Find password input
    el_pass = None
    if is_email_sent:
        try:
            el_pass = await tab.query_selector('table.login > tbody > tr > td > input[type="password"]')
        except Exception as exc:
            debug.log("Find password input fail:", exc)

    # Input password
    is_password_sent = False
    if el_pass:
        try:
            inputed_text = await tab.evaluate('document.querySelector("table.login > tbody > tr > td > input[type=password]").value')
            if not inputed_text or len(inputed_text) == 0:
                await el_pass.click()
                if len(password) > 0:
                    await el_pass.send_keys(password)
                    is_password_sent = True
        except Exception as exc:
            debug.log("Input password fail:", exc)

    # Handle captcha with OCR
    is_captcha_sent = False
    if is_password_sent and ocr:
        try:
            debug.log("[KHAM LOGIN] Starting OCR captcha processing...")

            ocr_start_time = time.time()

            # Get captcha image using canvas
            img_base64 = None
            try:
                form_verifyCode_base64 = await tab.evaluate('''
                    (function() {
                        var canvas = document.createElement('canvas');
                        var context = canvas.getContext('2d');
                        var img = document.getElementById('chk_pic');
                        if (img != null) {
                            canvas.height = img.naturalHeight;
                            canvas.width = img.naturalWidth;
                            context.drawImage(img, 0, 0);
                            return canvas.toDataURL();
                        }
                        return null;
                    })();
                ''')

                if form_verifyCode_base64:
                    img_base64 = base64.b64decode(form_verifyCode_base64.split(',')[1])
            except Exception as exc:
                debug.log("[KHAM LOGIN] Canvas exception:", str(exc))

            # OCR recognition
            ocr_answer = None
            if img_base64:
                try:
                    ocr_answer = ocr.classification(img_base64)
                    ocr_done_time = time.time()
                    ocr_elapsed_time = ocr_done_time - ocr_start_time
                    debug.log(f"[KHAM LOGIN] OCR elapsed time: {ocr_elapsed_time:.3f}s")
                except Exception as exc:
                    debug.log("[KHAM LOGIN] OCR classification fail:", exc)

            # Input captcha answer
            if ocr_answer:
                ocr_answer = ocr_answer.strip()
                debug.log(f"[KHAM LOGIN] OCR answer: {ocr_answer}")

                if len(ocr_answer) == 4:
                    try:
                        # Find captcha input field
                        el_captcha = await tab.query_selector('#CHK')
                        if el_captcha:
                            await el_captcha.click()
                            await el_captcha.send_keys(ocr_answer)
                            is_captcha_sent = True
                            debug.log("[KHAM LOGIN] Captcha filled successfully")
                    except Exception as exc:
                        debug.log("[KHAM LOGIN] Fill captcha fail:", exc)
                else:
                    debug.log(f"[KHAM LOGIN] Invalid captcha length: {len(ocr_answer)}, expected 4")
                    # New image, or the next pass re-reads this one forever.
                    await nodriver_kham_refresh_captcha(tab, config_dict)
            else:
                debug.log("[KHAM LOGIN] OCR answer is None")

        except Exception as exc:
            debug.log("[KHAM LOGIN] Captcha processing exception:", exc)

    # Click login button
    if is_password_sent:
        try:
            # Wait a bit for captcha to be filled
            if is_captcha_sent:
                await tab.sleep(0.3)

            el_btn = await tab.query_selector('div.memberContent > p > a > button.red')
            if el_btn:
                await el_btn.click()
                ret = True
                debug.log("[KHAM LOGIN] Login button clicked")
        except Exception as exc:
            debug.log("Click login button fail:", exc)

    return ret

async def nodriver_kham_go_buy_redirect(tab, domain_name):
    """
    Click the "Go Buy" button on product page
    """
    is_button_clicked = False

    if 'kham.com' in domain_name:
        my_css_selector = 'div#content > p > a > button[onclick].red'
    elif 'ticket.com' in domain_name:
        my_css_selector = 'div.row > div > a.btn.btn-order.btn-block'
    elif 'udnfunlife.com' in domain_name:
        # UDN fast buy button uses nexturl attribute instead of onclick
        # Navigate directly to the nexturl for quick purchase (UTK0222_02)
        try:
            next_url = await tab.evaluate('''
                (() => {
                    const btn = document.querySelector('button[name="fastBuy"]');
                    if (btn) {
                        const nexturl = btn.getAttribute('nexturl');
                        if (nexturl) {
                            // Convert relative URL to absolute
                            if (nexturl.startsWith('../')) {
                                return window.location.origin + '/application/' + nexturl.replace('../', '');
                            } else if (nexturl.startsWith('/')) {
                                return window.location.origin + nexturl;
                            }
                            return nexturl;
                        }
                    }
                    return null;
                })()
            ''')

            if next_url:
                await tab.get(next_url)
                return True
        except:
            pass

        # Fallback to traditional buy button if fast buy not available
        my_css_selector = '#buttonBuy'
    else:
        return False

    try:
        el_btn = await tab.query_selector(my_css_selector)
        if el_btn:
            await el_btn.click()
            is_button_clicked = True
    except Exception as exc:
        pass

    return is_button_clicked

async def nodriver_kham_check_realname_dialog(tab, config_dict):
    """
    Check and handle realname notification dialog
    """
    debug = util.create_debug_logger(config_dict)
    is_realname_dialog_found = False

    try:
        el_message_text = await tab.evaluate('''
            (function() {
                const el = document.querySelector('div.ui-dialog > div#dialog-message.ui-dialog-content');
                return el ? el.textContent : null;
            })();
        ''')

        if el_message_text:
            debug.log("Dialog message:", el_message_text)

            # Check if it's realname notification
            if "個人實名制入場" in el_message_text or "實名制" in el_message_text:
                debug.log("Found realname dialog, clicking OK button...")

                # Click OK button using JavaScript for reliable jQuery UI event triggering
                click_result = await tab.evaluate('''
                    (function() {
                        const btn = document.querySelector('div.ui-dialog-buttonset > button.ui-button');
                        if (btn) {
                            btn.click();
                            return true;
                        }
                        return false;
                    })();
                ''')

                if click_result:
                    is_realname_dialog_found = True

                    # Wait for dialog to close (jQuery UI dialog animation)
                    await tab.sleep(0.5)

                    # Verify dialog is closed to prevent infinite loop
                    try:
                        for _ in range(10):
                            dialog_visible = await tab.evaluate('''
                                (function() {
                                    const dialog = document.querySelector('div.ui-dialog');
                                    if (!dialog) return false;
                                    const style = window.getComputedStyle(dialog);
                                    return style.display !== 'none';
                                })();
                            ''')
                            if not dialog_visible:
                                break
                            await tab.sleep(0.1)
                    except:
                        pass
    except Exception as exc:
        debug.log("Check realname dialog exception:", exc)

    return is_realname_dialog_found


async def nodriver_kham_check_captcha_text_error(tab, config_dict):
    """
    Check captcha error message dialog
    """
    debug = util.create_debug_logger(config_dict)
    is_reset_password_text = False

    try:
        el_message_text = await tab.evaluate('''
            (function() {
                const el = document.querySelector('div.ui-dialog > div#dialog-message.ui-dialog-content');
                return el ? el.textContent : null;
            })();
        ''')

        if el_message_text:
            debug.log("Dialog message:", el_message_text)

            if "驗證碼輸入錯誤" in el_message_text or "【驗證碼】輸入錯誤" in el_message_text:
                # Click OK button to dismiss dialog
                el_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
                if el_btn:
                    await el_btn.click()
                    is_reset_password_text = True

                # Clear captcha input and wait for re-input
                await nodriver_kham_keyin_captcha_code(tab, "")
    except Exception as exc:
        debug.log("Check captcha error exception:", exc)

    return is_reset_password_text

async def nodriver_kham_product(tab, domain_name, config_dict):
    """
    Product page processing - call date auto select
    """
    debug = util.create_debug_logger(config_dict)

    is_date_assign_by_bot = await nodriver_kham_date_auto_select(tab, domain_name, config_dict)

    if not is_date_assign_by_bot:
        # Click "not on sale now" dialog button if exists
        try:
            el_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
            if el_btn:
                await el_btn.click()
        except:
            pass

    return is_date_assign_by_bot

async def nodriver_kham_date_auto_select(tab, domain_name, config_dict):
    """
    Date auto selection with keyword matching and mode fallback
    Supports: kham.com.tw, ticket.com.tw, udnfunlife.com
    """
    debug = util.create_debug_logger(config_dict)

    # Wait for page to load
    await tab.sleep(0.6)

    # Read config via the date view (issue #30, ADR 0004)
    config_view = config_views.date_view(config_dict)
    auto_select_mode = config_view["mode"]
    date_keyword = config_view["keyword"]
    auto_reload_coming_soon_page_enable = config_dict["tixcraft"]["auto_reload_coming_soon_page"]
    date_auto_fallback = config_view["fallback"]

    debug.log("date_keyword:", date_keyword)
    debug.log("auto_reload_coming_soon_page_enable:", auto_reload_coming_soon_page_enable)

    # Get all date rows using query_selector_all (similar to TixCraft)
    selector = "table.eventTABLE > tbody > tr"
    if 'ticket.com' in domain_name:
        selector = "div.description > table.table.table-striped.itable > tbody > tr"
    elif 'udnfunlife.com' in domain_name:
        selector = "div.yd_session-block"
    is_rwd = is_utk_rwd_url(domain_name)
    selectors = CONST_UTK_RWD_DATE_ROW_SELECTORS if is_rwd else (selector,)

    area_list = None
    try:
        for each_selector in selectors:
            area_list = await tab.query_selector_all(each_selector)
            if area_list:
                break
    except Exception as exc:
        debug.log(f"query_selector_all error: {exc}")

    # Rows for the shared date-selection core (issue #28, ADR 0003):
    # availability is decided here by the HTML filters below; keyword
    # exclude and priority matching live in the core.
    rows = []

    if area_list and len(area_list) > 0:
        for row in area_list:
            try:
                row_html = await row.get_html()
                row_text = util.remove_html_tags(row_html)
                row_text = row_text.strip()
            except Exception as exc:
                debug.log(f"get_html error: {exc}")
                break

            if not row_text:
                continue

            if util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
                rows.append({"text": row_text, "available": False, "payload": row})
                continue

            row_is_enabled = True

            # Filter: exclude disabled buttons
            # Issue #188: Allow "尚未開賣" (coming soon) buttons even with CSS disabled class
            if ' disabled">' in row_html and '尚未開賣' not in row_html:
                row_is_enabled = False

            # RWD rows: 購買 opens the area page via location.href; the other
            # button kind, VipSellCheck(), is a member pre-sale (out of scope).
            if row_is_enabled and is_rwd:
                row_is_enabled = 'location.href' in row_html

            # Filter: check if button exists (for kham/ticket)
            if row_is_enabled and not is_rwd and 'udnfunlife' not in domain_name:
                if '<button' in row_html:
                    # Issue #188: Support "尚未開賣" (coming soon) button for ERA TICKET
                    valid_button_texts = ['立即訂購', '點此購票', '尚未開賣']
                    if not any(text in row_html for text in valid_button_texts):
                        row_is_enabled = False
                else:
                    row_is_enabled = False  # No button, skip

            # Filter: check if tickets available (udn)
            if row_is_enabled and not is_rwd and 'udnfunlife' in domain_name:
                if '前往購票' not in row_html:
                    row_is_enabled = False

            # Filter: check price availability (for kham/ticket)
            if row_is_enabled and not is_rwd and 'udnfunlife' not in domain_name:
                if '<td' in row_html:
                    disabled_marker = '<del>' if 'ticket.com' in domain_name else '"lightblue"'
                    # Split by <td to get each column
                    td_array = row_html.split('<td')
                    if len(td_array) > 3:
                        # 4th td is the price column (date, location, price, order)
                        td_target = td_array[3]
                        # Split prices by Chinese comma
                        price_array = td_target.split('、')
                        is_all_price_disabled = True
                        for each_price in price_array:
                            if disabled_marker not in each_price:
                                is_all_price_disabled = False
                                break
                        if is_all_price_disabled:
                            debug.log(f"Skipping row: all prices are disabled")
                            row_is_enabled = False

            rows.append({
                "text": row_text,
                "available": row_is_enabled,
                "payload": row,
            })

    available_rows = [row for row in rows if row["available"]]

    if debug.enabled:
        debug.log(f"Valid date rows count: {len(available_rows)}")
        if available_rows:
            # Clean up whitespace for display
            import re
            display_text = re.sub(r'\s+', ' ', available_rows[0]["text"]).strip()
            debug.log(f"First row text sample: {display_text[:80]}")

    # Handle case when no rows are available (all options excluded):
    # the original returned here, before any reload handling.
    if len(available_rows) == 0:
        debug.log(f"[KHAM DATE FALLBACK] No available options after exclusion")
        return False

    # Kham's keyword parse-failure semantics differ from the other
    # platforms': the old matching loop's except set the matched list
    # to every row, so a parse/evaluation failure behaves like no
    # keyword (parse_error_all). The core evaluates groups lazily,
    # like the old loop, so an earlier matching group still wins over
    # a later malformed one.
    config_view["parse_error_all"] = True
    chosen_index = date_selection.select_date(rows, config_view)
    target_row = rows[chosen_index]["payload"] if chosen_index is not None else None

    if debug.enabled:
        if target_row:
            debug.log(f"Target row selected (mode: {auto_select_mode})")
        else:
            debug.log("No target row selected")

    is_date_assign_by_bot = False
    is_coming_soon = False

    if target_row:
        # Issue #188: Check if target is "coming soon" button before clicking
        try:
            target_row_html = await target_row.get_html()
            if '尚未開賣' in target_row_html:
                is_coming_soon = True
                debug.log("[TICKET.COM] Coming soon button detected, skip clicking")
        except:
            pass

        if not is_coming_soon:
            # Click the button in target row (similar to TixCraft)
            try:
                button_selector = 'button'
                if 'udnfunlife.com' in domain_name:
                    button_selector = 'div.goNext'

                btn = await target_row.query_selector(button_selector)
                if btn and is_rwd:
                    # The full RWD list sits in a hidden popup, where
                    # Element.click() finds no position; the onclick runs anyway.
                    await btn.apply('(el) => el.click()')
                    is_date_assign_by_bot = True
                    debug.log("Date buy button clicked successfully")
                elif btn:
                    await btn.click()
                    is_date_assign_by_bot = True
                    debug.log("Date buy button clicked successfully")
            except Exception as exc:
                debug.log(f"Click button error: {exc}")

    # Auto reload if: no target found OR target is coming soon button
    if not is_date_assign_by_bot and auto_reload_coming_soon_page_enable:
        if is_coming_soon or len(available_rows) == 0:
            try:
                if debug.enabled:
                    if is_coming_soon:
                        debug.log("[TICKET.COM] Waiting for sale time, will reload after delay...")
                    else:
                        debug.log("Date list empty, will auto reload after delay...")

                # Wait before reload (use config interval)
                reload_interval = config_views.reload_interval(config_dict)
                if reload_interval > 0:
                    await tab.sleep(reload_interval)
                else:
                    await tab.sleep(1.0)  # Default 1 second delay

                await tab.reload()
                await tab.sleep(0.5)  # Wait for page to start loading

                debug.log("Page reloaded, waiting for content...")
            except Exception as exc:
                debug.log("Auto reload exception:", exc)

    return is_date_assign_by_bot


async def nodriver_kham_area_auto_select(tab, domain_name, config_dict, area_keyword_item,
                                         allow_fallback=True):
    """
    Area/ticket type auto selection with table and dropdown support

    allow_fallback=False keeps a missed keyword from falling back to any row,
    so a caller trying several keywords can defer the fallback to the last one.
    """
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False, False, False

    debug = util.create_debug_logger(config_dict)
    area_config = config_views.area_view(config_dict)
    auto_select_mode = area_config["mode"]

    # Feature 003: Safe access for conditional fallback switch
    area_auto_fallback = area_config["fallback"] and allow_fallback
    # Fallback is on in settings but held for a later keyword: a miss here just
    # moves on to the next keyword, with no reload.
    fallback_deferred = area_config["fallback"] and not allow_fallback

    # NOTE: area_keyword_item is already a SINGLE keyword string: the caller
    # nodriver_kham_performance splits area_keyword with util.parse_keyword_string_to_array
    # and passes each keyword individually to this function.
    # Therefore, we should NOT split by comma again here - just clean the quotes
    if area_keyword_item and len(area_keyword_item) > 0:
        try:
            area_keyword_clean = area_keyword_item.strip()
            if area_keyword_clean.startswith('"') and area_keyword_clean.endswith('"'):
                area_keyword_clean = area_keyword_clean[1:-1]

            # Use the cleaned keyword directly (no comma split)
            area_keyword_item = area_keyword_clean
        except Exception as e:
            debug.log(f"[KHAM AREA] Keyword parse error: {e}")

    is_price_assign_by_bot = False
    is_need_refresh = False
    is_keyword_matched = False  # Track whether keyword actually matched (vs fallback)

    # Try dropdown mode first using CDP DOM operations
    # Supports both ibon (id="PRICE") and ticket.com.tw (id="ctl00_ContentPlaceHolder1_PRICE")
    price_select = None
    try:
        # Try standard id="PRICE" first (ibon)
        price_select = await tab.query_selector('select#PRICE')
        if not price_select:
            # Try ticket.com.tw selector
            selects = await tab.query_selector_all('select[id$="_PRICE"]')
            if selects and len(selects) > 0:
                price_select = selects[0]
    except Exception as exc:
        debug.log(f"Error finding PRICE select: {exc}")

    # Handle dropdown mode using CDP
    if price_select:
        try:
            # Get all option elements using CDP
            option_elements = await price_select.query_selector_all('option:not([value="-1"])')

            debug.log(f"Found dropdown with {len(option_elements)} options")

            # Extract option data using CDP
            options_data = []
            for i, opt_elem in enumerate(option_elements):
                try:
                    # Get text content
                    opt_text = await opt_elem.get_html()
                    opt_text = util.remove_html_tags(opt_text).strip()

                    # Get value attribute using JavaScript (more reliable)
                    opt_value = await opt_elem.apply('function(el) { return el.value; }')

                    # Check if disabled
                    is_disabled = await opt_elem.apply('function(el) { return el.disabled; }')

                    if opt_text and not is_disabled:
                        options_data.append({
                            'index': i,
                            'text': opt_text,
                            'value': opt_value,
                            'element': opt_elem
                        })
                except Exception as exc:
                    debug.log(f"Error processing option {i}: {exc}")

            # Feature 003: Filter by keyword with early return pattern
            matched_options = []
            available_options = []  # Track all non-excluded options for fallback

            for opt in options_data:
                option_text = opt['text']

                # Apply exclude keyword filter first
                if util.reset_row_text_if_match_keyword_exclude(config_dict, option_text):
                    debug.log(f"[KHAM AREA] Option excluded: '{option_text}'")
                    continue

                # Track available options for fallback
                available_options.append(opt)

                # Apply positive keyword matching with early return
                if len(area_keyword_item) > 0:
                    area_keyword_array = area_keyword_item.split(' ')
                    row_text = util.format_keyword_string(option_text)
                    is_match = True
                    for keyword in area_keyword_array:
                        formatted_keyword = util.format_keyword_string(keyword)
                        if formatted_keyword not in row_text:
                            is_match = False
                            break
                    if is_match:
                        # EARLY RETURN: First match found
                        matched_options.append(opt)
                        is_keyword_matched = True  # True keyword match (not fallback)
                        debug.log(f"[KHAM AREA KEYWORD] Keyword matched (dropdown): '{option_text}'")
                        break  # Stop checking further options
                else:
                    # No positive keyword - match all (except excluded)
                    matched_options.append(opt)
                    debug.log(f"[KHAM AREA SELECT] No keyword filter (dropdown): '{option_text}'")

            # Feature 003: Conditional fallback logic
            if len(matched_options) == 0 and len(area_keyword_item) > 0:
                if len(available_options) > 0:
                    if area_auto_fallback:
                        # Fallback enabled - use auto_select_mode
                        debug.log(f"[KHAM AREA FALLBACK] area_auto_fallback=true, triggering auto fallback (dropdown)")
                        debug.log(f"[KHAM AREA FALLBACK] Selecting from {len(available_options)} available options using mode='{auto_select_mode}'")
                        matched_options = available_options
                    elif fallback_deferred:
                        debug.log(f"[KHAM AREA FALLBACK] No match for '{area_keyword_item}', fallback deferred to the last keyword (dropdown)")
                        return False, False, False
                    else:
                        # Fallback disabled - strict mode (no selection, will reload)
                        debug.log(f"[KHAM AREA FALLBACK] area_auto_fallback=false, fallback is disabled (dropdown)")
                        debug.log(f"[KHAM AREA SELECT] No area selected, will reload page and retry")
                        return False, False, False  # Return to trigger reload logic
                else:
                    # No available options (all excluded)
                    debug.log(f"[KHAM AREA FALLBACK] No available options after exclusion (dropdown)")
                    return False, False, False

            # Select target option by simulating user interaction
            if matched_options:
                # Honour area_auto_select.mode, as the table branch does.
                target_option = util.get_target_item_from_matched_list(matched_options, auto_select_mode)
                target_value = target_option['value']
                target_text = target_option['text']

                debug.log(f"Selecting option: {target_text} (value: {target_value})")

                # Step 1: Click Bootstrap Select button using CDP + JavaScript
                try:
                    # First, check if Bootstrap Select button exists
                    bs_button = await tab.query_selector('button.dropdown-toggle[data-id$="_PRICE"]')

                    if bs_button:
                        debug.log("Found Bootstrap Select button, clicking to open dropdown...")

                        # Use JavaScript to click the button (avoid CDP click error)
                        await tab.evaluate('''
                            (function() {
                                const button = document.querySelector('button.dropdown-toggle[data-id$="_PRICE"]');
                                if (button) {
                                    button.click();
                                }
                            })();
                        ''')

                        # Wait for dropdown to open
                        await tab.sleep(0.5)

                        debug.log(f"Dropdown opened, looking for option: {target_text}")

                        # Step 2: Use CDP to find all <a> elements in the dropdown
                        menu_items = await tab.query_selector_all('ul.dropdown-menu.inner li[data-original-index] a')

                        debug.log(f"Found {len(menu_items)} menu items via CDP")

                        # Check each menu item
                        click_success = False
                        for link in menu_items:
                            try:
                                # Get the text from span.text using CDP
                                text_span = await link.query_selector('span.text')
                                if text_span:
                                    # Get text content
                                    span_html = await text_span.get_html()
                                    option_text = util.remove_html_tags(span_html).strip()

                                    debug.log(f"  Checking option: '{option_text}'")

                                    if option_text == target_text:
                                        debug.log(f"  Match found! Clicking...")

                                        # Use JavaScript to click the link (avoid CDP click error)
                                        await link.apply('function(el) { el.click(); }')
                                        click_success = True
                                        break
                            except Exception as exc:
                                debug.log(f"  Error checking menu item: {exc}")

                        if click_success:
                            is_price_assign_by_bot = True
                            debug.log(f"Successfully clicked Bootstrap Select option: {target_text}")
                        else:
                            debug.log(f"Failed to find/click option: {target_text}")

                    else:
                        # No Bootstrap Select button found, try direct select value setting
                        debug.log("Bootstrap Select button not found, using direct value setting...")

                        # Use CDP to directly set select value (avoid parameter serialization)
                        select_result = False
                        try:
                            select_elem = await tab.query_selector('select#PRICE, select[id$="_PRICE"]')
                            if select_elem:
                                # Set value directly using CDP
                                await select_elem.apply(f'function(el) {{ el.value = "{target_value}"; }}')
                                # Trigger change event
                                await tab.evaluate('''
                                    (function() {
                                        const select = document.querySelector('select#PRICE, select[id$="_PRICE"]');
                                        if (select) {
                                            select.dispatchEvent(new Event('change', { bubbles: true }));
                                        }
                                    })();
                                ''')
                                select_result = True
                        except Exception as fallback_exc:
                            debug.log(f"Direct select value setting error: {fallback_exc}")

                        is_price_assign_by_bot = select_result

                except Exception as exc:
                    debug.log(f"Bootstrap Select interaction error: {exc}")

        except Exception as exc:
            debug.log(f"Dropdown processing error: {exc}")

    else:
        # Handle table mode - use DOM element operations (similar to date_auto_select)

        # Determine selector
        selector = "table#salesTable > tbody > tr[class='status_tr']"
        if 'ticket.com.tw' in domain_name:
            selector = "li.main"
        elif 'udnfunlife' in domain_name:
            # UDN UTK0204: table.status > tr.status_tr (verified via MCP)
            # Soldout items have class="status_tr Soldout"
            selector = "table.status > tbody > tr.status_tr"
        elif is_utk_rwd_url(domain_name):
            # Columns by data-title (票區/票價/空位); a sold-out row reads 售完
            selector = CONST_UTK_RWD_AREA_ROW_SELECTOR

        # Get all area rows using query_selector_all
        area_list = None
        try:
            area_list = await tab.query_selector_all(selector)
        except Exception as exc:
            debug.log(f"query_selector_all error: {exc}")

        # Format area list with filtering
        formated_area_list = []
        formated_area_list_text = []

        if area_list and len(area_list) > 0:
            for row in area_list:
                try:
                    row_html = await row.get_html()
                    row_text = util.remove_html_tags(row_html)
                    row_text = row_text.strip()
                except Exception as exc:
                    debug.log(f"get_html error: {exc}")
                    break

                if not row_text:
                    continue

                # Filter: check if sold out
                if '售完' in row_text or ' Soldout' in row_html:
                    continue

                # Filter: udn specific check
                if 'udnfunlife' in domain_name:
                    if 'style="color:gray;border:solid 1px gray;cursor:default"' in row_html:
                        continue

                formated_area_list.append(row)
                formated_area_list_text.append(row_text)

        if debug.enabled:
            debug.log(f"Valid area rows count: {len(formated_area_list)}")
            if formated_area_list and len(formated_area_list) > 0:
                # Clean up whitespace for display
                import re
                display_text = re.sub(r'\s+', ' ', formated_area_list_text[0]).strip()
                debug.log(f"First row text sample: {display_text[:60]}")

        # Apply keyword matching
        matched_blocks = None
        if formated_area_list and len(formated_area_list) > 0:
            # Apply exclude keywords first
            filtered_rows = []
            filtered_rows_text = []
            for i, row_text in enumerate(formated_area_list_text):
                if not util.reset_row_text_if_match_keyword_exclude(config_dict, row_text):
                    filtered_rows.append(formated_area_list[i])
                    filtered_rows_text.append(row_text)

            # Check ticket number availability
            final_rows = []
            final_rows_text = []
            ticket_number = config_views.ticket_number(config_dict)
            for i, row_text in enumerate(filtered_rows_text):
                if 'udnfunlife' not in domain_name:
                    if ticket_number > 1:
                        remaining = _parse_remaining_seats(row_text)
                        if remaining is not None and remaining < ticket_number:
                            continue
                final_rows.append(filtered_rows[i])
                final_rows_text.append(row_text)

            # Feature 003: Match by keyword with early return pattern
            matched_blocks = []
            if len(area_keyword_item) > 0:
                # Use keyword matching on text, but keep DOM elements
                area_keyword_array = area_keyword_item.split(' ')
                area_found = False

                for i, row_text in enumerate(final_rows_text):
                    formatted_row_text = util.format_keyword_string(row_text)
                    is_match = True
                    for keyword in area_keyword_array:
                        formatted_keyword = util.format_keyword_string(keyword)
                        if formatted_keyword not in formatted_row_text:
                            is_match = False
                            break
                    if is_match:
                        # EARLY RETURN: First match found
                        matched_blocks.append(final_rows[i])
                        area_found = True
                        is_keyword_matched = True  # True keyword match (not fallback)
                        if debug.enabled:
                            # Clean up whitespace for display
                            import re
                            display_row_text = re.sub(r'\s+', ' ', row_text).strip()
                            debug.log(f"[KHAM AREA KEYWORD] Keyword matched (table): {display_row_text[:60]}")
                        break  # Stop checking further rows

                # All keywords failed log
                if not area_found:
                    debug.log(f"[KHAM AREA KEYWORD] All keywords failed to match (table)")
            else:
                # No keyword filter - use all available rows
                matched_blocks = final_rows
                debug.log(f"[KHAM AREA SELECT] No keyword filter (table): using {len(final_rows)} available rows")

            if matched_blocks:
                debug.log("Matched area blocks:", len(matched_blocks))

            # Feature 003: Conditional fallback logic (Table Mode)
            if len(matched_blocks) == 0 and len(area_keyword_item) > 0:
                if len(final_rows) > 0:
                    if area_auto_fallback:
                        # Fallback enabled - use auto_select_mode
                        debug.log(f"[KHAM AREA FALLBACK] area_auto_fallback=true, triggering auto fallback (table)")
                        debug.log(f"[KHAM AREA FALLBACK] Selecting from {len(final_rows)} available rows using mode='{auto_select_mode}'")
                        matched_blocks = final_rows
                    elif fallback_deferred:
                        debug.log(f"[KHAM AREA FALLBACK] No match for '{area_keyword_item}', fallback deferred to the last keyword (table)")
                        return False, False, False
                    else:
                        # Fallback disabled - strict mode (no selection, will reload)
                        debug.log(f"[KHAM AREA FALLBACK] area_auto_fallback=false, fallback is disabled (table)")
                        debug.log(f"[KHAM AREA SELECT] No area selected, will reload page and retry")
                        return False, False, False  # Return to trigger reload logic
                else:
                    # No available rows (all filtered out or sold out)
                    debug.log(f"[KHAM AREA FALLBACK] No available rows after filtering (table)")
                    return False, False, False

        # Get target and click
        target_row = util.get_target_item_from_matched_list(matched_blocks, auto_select_mode)

        if debug.enabled:
            if target_row:
                debug.log(f"Target area row selected (mode: {auto_select_mode})")
            else:
                debug.log(f"No target area row selected from {len(matched_blocks) if matched_blocks else 0} matched blocks")

        if target_row:
            # Click target row directly
            # For Kham, the entire row is clickable
            try:
                debug.log(f"Clicking target area row...")
                await target_row.click()
                is_price_assign_by_bot = True
                debug.log("Area row clicked successfully")
            except Exception as exc:
                debug.log(f"Click area row error: {exc}")
        else:
            is_need_refresh = True

    return is_need_refresh, is_price_assign_by_bot, is_keyword_matched


async def nodriver_kham_performance(tab, config_dict, ocr, domain_name, model_name):
    """
    Performance page processing - integrate area selection, captcha, and ticket number
    """
    debug = util.create_debug_logger(config_dict)

    is_price_assign_by_bot = False
    is_captcha_sent = False

    area_config = config_views.area_view(config_dict)
    area_keyword = area_config["keyword"].strip()

    debug.log("area_keyword:", area_keyword)

    is_need_refresh = False

    # Record the URL before area selection. Clicking a kham/ticket area row fires
    # top.location.href to the next page, and we use this baseline below to detect
    # that navigation.
    pre_select_url = tab.target.url

    if len(area_keyword) > 0:
        # Parse JSON array keyword
        area_keyword_array = util.parse_keyword_string_to_array(area_keyword)

        # Feature 003: Enhanced fallback logic with early return
        for keyword_index, area_keyword_item in enumerate(area_keyword_array):
            is_last_keyword = (keyword_index == len(area_keyword_array) - 1)

            # Fallback only on the last keyword: a fallback clicks a row and
            # the loop stops (Case 3), so allowing it earlier would pick any
            # area before the later keywords were ever tried.
            is_need_refresh, is_price_assign_by_bot, is_keyword_matched = await nodriver_kham_area_auto_select(
                tab, domain_name, config_dict, area_keyword_item,
                allow_fallback=is_last_keyword
            )

            # Case 1: True keyword match - stop trying
            if is_keyword_matched:
                debug.log(f"[KHAM PERFORMANCE] Keyword matched: '{area_keyword_item}'")
                break

            # Case 2: Strict mode (area_auto_fallback=false) - only stop if last keyword
            # is_need_refresh=False, is_price_assign_by_bot=False, is_keyword_matched=False
            if not is_need_refresh and not is_price_assign_by_bot:
                if is_last_keyword:
                    # Last keyword failed in strict mode - trigger reload and retry
                    is_need_refresh = True
                    debug.log(f"[KHAM PERFORMANCE] All keywords exhausted, strict mode stops")
                    debug.log(f"[KHAM PERFORMANCE] Will reload page and retry")
                    break
                else:
                    # Not last keyword - continue trying
                    debug.log(f"[KHAM PERFORMANCE] Keyword #{keyword_index + 1} not matched, trying next...")
                    continue

            # Case 3: Fallback selection - the row is clicked and the page is
            # navigating, so stop: later keywords would read the torn-down DOM
            # as zero rows and trigger a reload of a page already left.
            # is_price_assign_by_bot=True, is_keyword_matched=False
            if is_price_assign_by_bot and not is_keyword_matched:
                debug.log(f"[KHAM PERFORMANCE] Fallback selection for "
                          f"'{area_keyword_item}', stopping")
                break

            # Case 4: Refresh needed - continue trying next keyword
            # is_need_refresh=True (other scenarios)
            if is_need_refresh:
                debug.log(f"[KHAM PERFORMANCE] Need refresh for keyword: {area_keyword_item}")
                # Continue to next keyword
    else:
        # Empty keyword - match all
        is_need_refresh, is_price_assign_by_bot, is_keyword_matched = await nodriver_kham_area_auto_select(
            tab, domain_name, config_dict, ""
        )

    if is_need_refresh:
        debug.log("is_need_refresh:", is_need_refresh)
        # Stage 5: area sold out / keyword not matched -> reload and retry.
        try:
            await tab.reload()
        except Exception as exc:
            debug.log("Area reload exception:", exc)
        # Wait AFTER the reload: a DOM read before the new document parses sees
        # zero area rows and reloads forever. The same wait throttles the refresh
        # rate (#322); the settle floor applies even when the interval is 0.
        reload_interval = config_views.reload_interval(config_dict)
        await tab.sleep(max(reload_interval, CONST_KHAM_RELOAD_SETTLE))
        # The page is reloading and the current DOM is stale, so skip the
        # captcha/submit attempt this round. Returning is_captcha_sent=False
        # makes the caller skip the add-to-cart submit (avoids the wasted
        # OCR + "Add shopping cart button not found" churn on a sold-out page).
        return is_price_assign_by_bot, False

    # Stage 5: for kham/ticket, clicking an area row fires top.location.href to
    # UTK0205 (seat map) or UTK0202 (ticket-number page). When that navigation
    # happens, hand off to the main loop's dedicated handler instead of running
    # captcha + add-to-cart here -- submitting before seat selection raises a
    # "please choose a seat" error and wastes an OCR + submit + retry round.
    # UDN keeps its seat map on the same UTK0204 page (no navigation) and skips
    # captcha entirely, so it is excluded and its flow stays unchanged.
    if is_price_assign_by_bot and 'udnfunlife' not in domain_name:
        for _ in range(6):  # up to ~3s; exits as soon as the navigation is seen
            if tab.target.url != pre_select_url:
                debug.log("[KHAM PERFORMANCE] Area click navigated to next page; handing off to main loop")
                return is_price_assign_by_bot, False
            await tab.sleep(0.5)

    # udn uses reCaptcha, skip for now. An RWD area page has no order captcha
    # (it sits on the seat/amount page); its #MASTER_CHK belongs to the login popup.
    if 'udnfunlife' not in domain_name and not is_utk_rwd_url(domain_name):
        is_captcha_sent = await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

    return is_price_assign_by_bot, is_captcha_sent

async def nodriver_kham_main(ctx):
    """
    Main control flow for Kham platform with URL routing
    """
    tab = ctx.tab
    url = ctx.url
    config_dict = ctx.config_dict
    ocr = ctx.ocr

    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        # Platform interface (issue #36): this used to return False and
        # the dispatcher assigned it to tab unconditionally, clobbering
        # the tab for later passes. Returning the tab is the fix.
        return tab
    if not _state:
        _state.update({
            "is_popup_checkout": False,
            "played_sound_order": False,
            "shown_checkout_message": False,
            "udn_quick_buy_submitted": False,
        })

    # 中職 RWD sites: every page-type check below reads the kham-layout URL;
    # raw_url stays the address to show or open.
    raw_url = url
    is_rwd = is_utk_rwd_url(url)
    url = normalize_utk_rwd_url(url)
    domain_name = url.split('/')[2]
    debug = util.create_debug_logger(config_dict)
    area_config = config_views.area_view(config_dict)
    ticket_number_value = config_views.ticket_number(config_dict)
    captcha_config = config_views.captcha_view(config_dict)

    if is_rwd and await nodriver_utk_rwd_ensure_login(tab, url, config_dict, ocr):
        return tab

    # Home page handling
    home_url_list = [
        'https://kham.com.tw/',
        'https://kham.com.tw/application/utk01/utk0101_.aspx',
        'https://kham.com.tw/application/utk01/utk0101_03.aspx',
        'https://ticket.com.tw/application/utk01/utk0101_.aspx',
        'https://tickets.udnfunlife.com/application/utk01/utk0101_.aspx'
    ]
    if is_rwd:
        home_url_list.append(normalize_utk_rwd_url(f"https://{domain_name}/").lower())

    # Page state lifecycle (issue #14): classify the page once per pass
    # from the normalized URL, mirroring the dispatch branches below.
    if url.lower() in home_url_list:
        _page_id = _PAGE_HOME
    elif 'utk0205' in url.lower():
        _page_id = _PAGE_SEAT
    elif 'utk0201_040.aspx?agid=' in url.lower():
        _page_id = _PAGE_ACTIVITY_GROUP
    elif 'utk0201_041.aspx?agid=' in url.lower():
        _page_id = _PAGE_ACTIVITY_GROUP_ITEM
    elif 'utk0202' in url.lower() or 'utk0203' in url.lower():
        _page_id = _PAGE_UTK_TICKET
    elif 'utk0201' in url.lower():
        _page_id = _PAGE_UTK_DATE_AREA
    else:
        _page_id = None
    _state.set_page(_page_id)

    for each_url in home_url_list:
        if each_url == url.lower():
            # Clean popup banners
            try:
                await tab.evaluate('''
                    (function() {
                        const popup = document.querySelector('.popoutBG');
                        if (popup) popup.remove();
                    })();
                ''')
            except:
                pass

            # For UDN login page: execute login first, then redirect after login completes
            # This prevents the infinite redirect loop between login page and event page
            if 'udnfunlife.com' in url.lower():
                udn_account = config_dict["accounts"]["udn_account"]
                udn_password = config_dict["accounts"]["udn_password"].strip()

                if len(udn_account) > 4:
                    # Check if already logged in by looking for logout button or user menu
                    is_logged_in = False
                    try:
                        login_state_raw = await tab.evaluate('''
                            (() => {
                                // Method 1: Check member area login item visibility
                                const memberArea = document.querySelector('.yd_mainNav-member');
                                if (memberArea) {
                                    const subList = memberArea.querySelector('.yd_mainNav-subList');
                                    if (subList) {
                                        const listItems = subList.querySelectorAll('li');
                                        const loginItem = listItems[0];
                                        if (loginItem && window.getComputedStyle(loginItem).display === 'none') {
                                            return { isLoggedIn: true };
                                        }
                                    }
                                }
                                // Method 2: Check for welcome message or user name display
                                const welcomeText = document.body.innerText;
                                if (welcomeText.includes('您好') || welcomeText.includes('登出')) {
                                    return { isLoggedIn: true };
                                }
                                return { isLoggedIn: false };
                            })()
                        ''')
                        login_state = util.parse_nodriver_result(login_state_raw)
                        if isinstance(login_state, dict):
                            is_logged_in = login_state.get('isLoggedIn', False)
                    except Exception as exc:
                        debug.log(f"[UDN LOGIN] Login state check error: {exc}")

                    if is_logged_in:
                        debug.log("[UDN LOGIN] Already logged in, proceeding to redirect...")
                    else:
                        # Not logged in yet - execute login and DON'T redirect
                        # Let the login process complete first
                        debug.log(f"[UDN LOGIN] Not logged in, executing login with account: {udn_account[:3]}***")

                        # Trigger login dialog
                        await tab.evaluate('if(typeof doLoginRWD === "function") doLoginRWD();')
                        await tab.sleep(0.5)

                        # Fill account
                        try:
                            await tab.evaluate(f'''
                                (() => {{
                                    const emailInput = document.getElementById('ID');
                                    if (emailInput && !emailInput.value) {{
                                        emailInput.value = "{udn_account}";
                                        emailInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    }}
                                }})()
                            ''')
                        except Exception as exc:
                            debug.log(f"[UDN LOGIN] Fill account error: {exc}")

                        # Fill password
                        try:
                            await tab.evaluate(f'''
                                (() => {{
                                    const passInput = document.getElementById('password');
                                    if (passInput && !passInput.value) {{
                                        passInput.value = "{udn_password}";
                                        passInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    }}
                                }})()
                            ''')
                        except Exception as exc:
                            debug.log(f"[UDN LOGIN] Fill password error: {exc}")

                        # Click reCAPTCHA checkbox
                        try:
                            recaptcha_clicked = False
                            try:
                                checkboxes = await tab.select_all('.recaptcha-checkbox-border', include_frames=True)
                                if checkboxes and len(checkboxes) > 0:
                                    await checkboxes[0].click()
                                    recaptcha_clicked = True
                                    debug.log("[UDN LOGIN] reCAPTCHA clicked via include_frames")
                            except Exception as e1:
                                debug.log(f"[UDN LOGIN] include_frames method failed: {e1}")

                            if not recaptcha_clicked:
                                try:
                                    recaptcha_pos = await tab.evaluate('''
                                        (() => {
                                            const frame = document.querySelector('iframe[title*="reCAPTCHA"]');
                                            if (frame) {
                                                frame.scrollIntoView({ block: 'center' });
                                                const rect = frame.getBoundingClientRect();
                                                return { x: rect.left + 27, y: rect.top + 30, found: true };
                                            }
                                            return { found: false };
                                        })()
                                    ''')
                                    if isinstance(recaptcha_pos, dict) and recaptcha_pos.get('found'):
                                        x = recaptcha_pos['x']
                                        y = recaptcha_pos['y']
                                        await tab.mouse_click(x, y)
                                        recaptcha_clicked = True
                                        debug.log(f"[UDN LOGIN] reCAPTCHA clicked via mouse_click at ({x}, {y})")
                                except Exception as e2:
                                    debug.log(f"[UDN LOGIN] mouse_click method failed: {e2}")
                        except Exception as exc:
                            debug.log(f"[UDN LOGIN] reCAPTCHA click error: {exc}")

                        debug.log("[UDN LOGIN] Credentials filled, waiting for reCAPTCHA verification...")

                        # DON'T redirect yet - return and let user complete login
                        # Next iteration will check if logged in and redirect then
                        return tab

            # Redirect to target page after login
            config_homepage = config_dict["homepage"]

            # Check if config_homepage is also a home page URL (skip redirect to avoid loop).
            # An RWD homepage is compared in the kham layout, as url is.
            config_homepage_layout = normalize_utk_rwd_url(config_homepage) if config_homepage else ""
            config_homepage_normalized = config_homepage_layout.lower().rstrip('/')
            is_config_homepage_also_home = any(
                config_homepage_normalized == each.rstrip('/')
                for each in home_url_list
            ) or config_homepage_normalized in [
                'https://kham.com.tw',
                'https://ticket.com.tw',
                'https://tickets.udnfunlife.com'
            ]

            # Redirect only if homepage is different AND not a home page URL
            if config_homepage and not is_config_homepage_also_home and config_homepage_layout.lower() != url.lower():
                debug.log(f"[KHAM LOGIN] Redirecting to target: {config_homepage}")
                try:
                    await tab.get(config_homepage)
                    return tab
                except Exception as e:
                    debug.log(f"[KHAM LOGIN] Redirect failed: {e}")
            break

    # Check realname dialog
    await nodriver_kham_check_realname_dialog(tab, config_dict)

    # KHAM UTK0205 seat selection page (graphical seat map with ticket type buttons)
    if ("kham.com.tw" in url or is_rwd) and 'utk0205' in url.lower():
        debug.log("Detected KHAM UTK0205 seat selection page")

        is_seat_selection_success = await nodriver_kham_seat_main(tab, config_dict, ocr, domain_name)

        debug.log(f"KHAM seat selection result: {is_seat_selection_success}")

        # Return to avoid double processing by UTK0202/UTK0205 logic below
        return tab

    # UDN UTK0205 seat selection page (Feature 010: UDN seat auto select)
    # UDN shares the same UTK backend system with KHAM, so we reuse KHAM seat selection logic
    # Reference: research.md - DOM structure and selectors are identical
    if "udnfunlife.com" in url and 'utk0205' in url.lower():
        debug.log("[UDN SEAT] Detected UDN UTK0205 seat selection page")

        is_seat_selection_success = await nodriver_kham_seat_main(tab, config_dict, ocr, domain_name)

        if debug.enabled:
            debug.log(f"[UDN SEAT] Seat selection result: {is_seat_selection_success}")
            if is_seat_selection_success:
                debug.log("[SUCCESS] UDN seat selection completed")

        # Return to avoid double processing
        return tab

    # Activity Group page (UTK0201_040.aspx?AGID=)
    # This is a special page format for activity groups with realname requirements
    if 'utk0201_040.aspx?agid=' in url.lower():
        debug.log("Detected KHAM Activity Group page (UTK0201_040)")

        # Check realname dialog
        await nodriver_kham_check_realname_dialog(tab, config_dict)

        # Click buy button
        await nodriver_kham_go_buy_redirect(tab, domain_name)

    # Activity Group Item page (UTK0201_041.aspx?AGID=)
    # This page is a standard table.eventTABLE listing multiple performances /
    # ticket types on the SAME date (e.g. physical ticket vs welfare voucher),
    # each row carrying its own "立即訂購" button.
    # Issue #357: the buttons redirect to DIFFERENT pages
    # (physical -> UTK0201_000, voucher -> UTK0202), so the old
    # 'button.red[onclick*="UTK0202"]' selector only ever matched the voucher row
    # and clicked buttons[0], ignoring both selection mode and keyword_exclude.
    # Reuse date_auto_select, which filters eventTABLE rows (keyword_exclude),
    # applies the selection mode, and clicks the button inside the chosen row.
    if 'utk0201_041.aspx?agid=' in url.lower():
        debug.log("Detected KHAM Activity Group Item page (UTK0201_041)")

        # Check realname dialog first
        await nodriver_kham_check_realname_dialog(tab, config_dict)

        # Stage 4: select the correct performance row (keyword_exclude + mode aware)
        await nodriver_kham_date_auto_select(tab, domain_name, config_dict)

    # RWD product page: the performance list itself, filled in by XHR after load.
    # It has no buy button or captcha of its own, so the kham branch below is skipped.
    if is_rwd and 'utk0201_.aspx?product_id=' in url.lower():
        list_ready = await _utk_rwd_wait_for(
            tab, "(() => !!document.querySelector('#PerformanceListTable table.ss'))()", 3.0)
        if list_ready and config_dict["date_auto_select"]["enable"]:
            await nodriver_kham_product(tab, domain_name, config_dict)

    # Product page (UTK0201_.aspx?product_id=)
    if 'utk0201_.aspx?product_id=' in url.lower() and not is_rwd:
        is_event_page = len(url.split('/')) == 6

        if is_event_page:
            # Check realname dialog
            await nodriver_kham_check_realname_dialog(tab, config_dict)

            # Click buy button
            await nodriver_kham_go_buy_redirect(tab, domain_name)
            await tab.sleep(1.0)

            # Check if page changed after clicking buy button
            current_url = tab.target.url
            if 'utk0201_.aspx?product_id=' in current_url.lower():
                # Still on product page - check realname dialog and handle captcha
                await nodriver_kham_check_realname_dialog(tab, config_dict)

                # Handle captcha if enabled
                is_captcha_sent = False
                if captcha_config["enable"]:
                    debug.log("Starting captcha processing for purchase page...")

                    model_name = url.split('/')[5] if len(url.split('/')) > 5 else "UTK0201"
                    if len(model_name) > 7:
                        model_name = model_name[:7]

                    is_captcha_sent = await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

                if is_captcha_sent:
                    # Set ticket number
                    ticket_number = str(config_views.ticket_number(config_dict))
                    try:
                        await tab.evaluate(f'''
                            (function() {{
                                const amountInput = document.querySelector('#AMOUNT');
                                if (amountInput && (amountInput.value === '' || amountInput.value === '0')) {{
                                    amountInput.value = '{ticket_number}';
                                    amountInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                }}
                            }})();
                        ''')
                    except:
                        pass

                    # Click add to cart
                    try:
                        btn_selector = 'button[onclick="addShoppingCart();return false;"]'
                        el_btn = await tab.query_selector(btn_selector)
                        if el_btn:
                            await el_btn.click()
                        else:
                            # Try alternative selector
                            el_btn = await tab.query_selector('#addcart button.red')
                            if el_btn:
                                await el_btn.click()
                    except:
                        pass

    # Date selection page (UTK0201_00.aspx?product_id=)
    if 'utk0201_00.aspx?product_id=' in url.lower():
        is_event_page = len(url.split('/')) == 6

        if is_event_page and config_dict["date_auto_select"]["enable"]:
            await nodriver_kham_product(tab, domain_name, config_dict)

    # UDN specific handling
    if 'udnfunlife' in domain_name:
        # UDN homepage login (popup dialog with reCAPTCHA)
        # UDN uses a popup login dialog on homepage, not UTK1306 page
        if 'utk01/utk0101_.aspx' in url.lower():
            udn_account = config_dict["accounts"]["udn_account"]
            udn_password = config_dict["accounts"]["udn_password"].strip()
            if len(udn_account) > 4:
                # Check if already logged in
                # Detection method: Check if "登入/註冊" menu item is hidden
                # When logged in: "登入/註冊" is display:none, "登出" is visible
                # When not logged in: "登入/註冊" is visible, "登出" is display:none
                is_logged_in = False
                try:
                    login_state_raw = await tab.evaluate('''
                        (() => {
                            const memberArea = document.querySelector('.yd_mainNav-member');
                            if (!memberArea) return { loginItemHidden: false };
                            const subList = memberArea.querySelector('.yd_mainNav-subList');
                            if (!subList) return { loginItemHidden: false };
                            const listItems = subList.querySelectorAll('li');
                            // First item is "登入/註冊", check if it's hidden
                            const loginItem = listItems[0];
                            const loginItemHidden = loginItem && window.getComputedStyle(loginItem).display === 'none';
                            return { loginItemHidden: loginItemHidden };
                        })()
                    ''')
                    # Use util.parse_nodriver_result to handle nodriver's special return format
                    login_state = util.parse_nodriver_result(login_state_raw)
                    if isinstance(login_state, dict):
                        # User is logged in if "登入/註冊" item is hidden
                        is_logged_in = login_state.get('loginItemHidden', False)
                except Exception as exc:
                    debug.log(f"[UDN LOGIN] Login state check error: {exc}")

                if not is_logged_in:
                    debug.log(f"[UDN LOGIN] Starting login with account: {udn_account[:3]}***")

                    # Trigger login dialog
                    await tab.evaluate('if(typeof doLoginRWD === "function") doLoginRWD();')
                    await tab.sleep(0.5)

                    # Fill account
                    try:
                        await tab.evaluate(f'''
                            (() => {{
                                const emailInput = document.getElementById('ID');
                                if (emailInput && !emailInput.value) {{
                                    emailInput.value = "{udn_account}";
                                    emailInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                }}
                            }})()
                        ''')
                    except Exception as exc:
                        debug.log(f"[UDN LOGIN] Fill account error: {exc}")

                    # Fill password
                    try:
                        await tab.evaluate(f'''
                            (() => {{
                                const passInput = document.getElementById('password');
                                if (passInput && !passInput.value) {{
                                    passInput.value = "{udn_password}";
                                    passInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                }}
                            }})()
                        ''')
                    except Exception as exc:
                        debug.log(f"[UDN LOGIN] Fill password error: {exc}")

                    # Click reCAPTCHA checkbox
                    try:
                        recaptcha_clicked = False

                        # Method 1: Use nodriver's include_frames to find checkbox inside iframe
                        try:
                            checkboxes = await tab.select_all('.recaptcha-checkbox-border', include_frames=True)
                            if checkboxes and len(checkboxes) > 0:
                                await checkboxes[0].click()
                                recaptcha_clicked = True
                                debug.log("[UDN LOGIN] reCAPTCHA clicked via include_frames")
                        except Exception as e1:
                            debug.log(f"[UDN LOGIN] include_frames method failed: {e1}")

                        # Method 2: Fallback to CDP mouse event
                        if not recaptcha_clicked:
                            try:
                                recaptcha_pos = await tab.evaluate('''
                                    (() => {
                                        const frame = document.querySelector('iframe[title*="reCAPTCHA"]');
                                        if (frame) {
                                            frame.scrollIntoView({ block: 'center' });
                                            const rect = frame.getBoundingClientRect();
                                            return { x: rect.left + 27, y: rect.top + 30, found: true };
                                        }
                                        return { found: false };
                                    })()
                                ''')

                                if isinstance(recaptcha_pos, dict) and recaptcha_pos.get('found'):
                                    x = recaptcha_pos['x']
                                    y = recaptcha_pos['y']
                                    await tab.mouse_click(x, y)
                                    recaptcha_clicked = True
                                    debug.log(f"[UDN LOGIN] reCAPTCHA clicked via mouse_click at ({x}, {y})")
                            except Exception as e2:
                                debug.log(f"[UDN LOGIN] mouse_click method failed: {e2}")

                        if not recaptcha_clicked:
                            debug.log("[UDN LOGIN] reCAPTCHA checkbox not found")
                    except Exception as exc:
                        debug.log(f"[UDN LOGIN] reCAPTCHA click error: {exc}")

                    # After filling credentials and clicking reCAPTCHA, stop here
                    # User needs to complete reCAPTCHA verification and click login manually
                    # The bot will check login status on next iteration
                    debug.log("[UDN LOGIN] Credentials filled, waiting for user to complete reCAPTCHA and login...")

        # UDN ticket selection (UTK0203 date/session selection)
        if 'utk0203_.aspx?product_id=' in url.lower():
            # Try layout format 1
            try:
                ticket_input = await tab.query_selector('input.yd_counterNum')
                if ticket_input:
                    await tab.evaluate(f'''
                        (function() {{
                            const input = document.querySelector('input.yd_counterNum');
                            if (input && input.value === '0') {{
                                input.value = '{ticket_number_value}';
                            }}
                        }})();
                    ''')
                    btn_next = await tab.query_selector('#buttonNext')
                    if btn_next:
                        await btn_next.click()
                else:
                    # Layout format 2 - use date selection
                    if config_dict["date_auto_select"]["enable"]:
                        await nodriver_kham_product(tab, domain_name, config_dict)
            except:
                pass

        # UDN UTK0204 area selection page (Feature 010: UDN area auto select)
        # URL pattern: .aspx?PERFORMANCE_ID=xxx&PRODUCT_ID=xxx (without PERFORMANCE_PRICE_AREA_ID)
        # This page shows available ticket areas for selection
        # Note: UTK0204 combines area selection and seat map on the same page
        if '.aspx?performance_id=' in url.lower() and 'product_id=' in url.lower():
            # Exclude seat selection page (UTK0205) which has PERFORMANCE_PRICE_AREA_ID
            if 'performance_price_area_id=' not in url.lower():
                debug.log("[UDN AREA] Detected UDN UTK0204 area selection page")

                if area_config["enable"]:
                    # UDN uses nodriver_kham_performance for area selection
                    # UDN selector: table.status > tbody > tr.status_tr (verified via MCP)
                    model_name = "UTK0204"

                    is_price_assign_by_bot, is_captcha_sent = await nodriver_kham_performance(
                        tab, config_dict, ocr, domain_name, model_name
                    )

                    debug.log(f"[UDN AREA] Area selection result: is_price_assign_by_bot={is_price_assign_by_bot}")

                    # Feature 010: UDN seat auto select
                    # After area selection, seat map may appear on the same page
                    # Check for seat map and perform seat selection if available
                    if is_price_assign_by_bot:
                        await tab.sleep(0.5)  # Wait for seat map to load
                        is_seat_success = await nodriver_udn_seat_main(tab, config_dict)
                        if debug.enabled:
                            debug.log(f"[UDN SEAT] Seat selection result: {is_seat_success}")
                            if is_seat_success:
                                debug.log("[SUCCESS] UDN seat selection and add to cart completed")

        # UDN UTK0222_02 fast purchase page (Feature 010: UDN quick buy)
        # URL pattern: UTK0222_02.aspx?PRODUCT_ID=xxx
        # This page shows: 1) Date selection 2) Performance selection 3) Area selection
        if 'utk0222_02.aspx' in url.lower():
            # Skip if quick buy was already submitted (waiting for navigation)
            if _state.get("udn_quick_buy_submitted", False):
                return tab

            debug.log("[UDN QUICK BUY] Detected UTK0222_02 fast purchase page")

            try:
                # Step 1: Date selection (li.yd_datedBtn)
                if config_dict["date_auto_select"]["enable"]:
                    date_keyword = config_dict["date_auto_select"]["date_keyword"].strip()

                    date_result_raw = await tab.evaluate('''
                        (() => {
                            const dateBtns = document.querySelectorAll('li.yd_datedBtn');
                            const dates = [];
                            dateBtns.forEach((btn, idx) => {
                                dates.push({
                                    index: idx,
                                    text: btn.textContent.trim()
                                });
                            });
                            return { dates: dates, count: dates.length };
                        })()
                    ''')
                    date_result = util.parse_nodriver_result(date_result_raw)

                    if isinstance(date_result, dict) and date_result.get('count', 0) > 0:
                        dates = date_result.get('dates', [])
                        target_date_idx = None  # None means no match yet
                        keyword_matched = False

                        # Match date keyword (use JSON parsing like other platforms)
                        if date_keyword:
                            keywords = util.parse_keyword_string_to_array(date_keyword)

                            debug.log(f"[UDN QUICK BUY] Date keywords parsed: {keywords}")

                            for i, date_item in enumerate(dates):
                                date_text = date_item.get('text', '')
                                for kw in keywords:
                                    # Support AND logic (space-separated keywords)
                                    kw_parts = kw.split(' ') if ' ' in kw else [kw]
                                    all_match = all(part in date_text for part in kw_parts)
                                    if all_match:
                                        target_date_idx = i
                                        keyword_matched = True
                                        debug.log(f"[UDN QUICK BUY] Date matched: {date_text} with keyword: {kw}")
                                        break
                                if keyword_matched:
                                    break  # Early return when matched

                        # Fallback logic based on date_auto_fallback and mode
                        if not keyword_matched:
                            date_auto_fallback = config_dict.get("date_auto_fallback", False)
                            date_mode = config_dict["date_auto_select"].get("mode", "from top to bottom")

                            if date_auto_fallback:
                                debug.log(f"[UDN QUICK BUY] Date keyword not matched, fallback with mode: {date_mode}")

                                target_date_idx = util.get_target_index_by_mode(len(dates), date_mode)

                                if debug.enabled:
                                    selected_date = dates[target_date_idx].get('text', '') if target_date_idx is not None and target_date_idx < len(dates) else ''
                                    debug.log(f"[UDN QUICK BUY] Fallback selected date: {selected_date} (index: {target_date_idx})")
                            else:
                                # Strict mode: no fallback, use first date as default
                                target_date_idx = 0
                                debug.log(f"[UDN QUICK BUY] Fallback disabled, using first date")

                        # Click the date button
                        await tab.evaluate(f'''
                            (() => {{
                                const dateBtns = document.querySelectorAll('li.yd_datedBtn');
                                if (dateBtns[{target_date_idx}]) {{
                                    dateBtns[{target_date_idx}].click();
                                }}
                            }})()
                        ''')
                        await tab.sleep(0.3)

                # Step 2: Performance/Session selection (div.sd-btn.bg--gray)
                if config_dict["date_auto_select"]["enable"]:
                    perf_result_raw = await tab.evaluate('''
                        (() => {
                            const perfBtns = document.querySelectorAll('div.sd-btn.bg--gray');
                            const perfs = [];
                            perfBtns.forEach((btn, idx) => {
                                perfs.push({
                                    index: idx,
                                    text: btn.textContent.trim().replace(/\\s+/g, ' '),
                                    isActive: btn.classList.contains('active')
                                });
                            });
                            return { perfs: perfs, count: perfs.length };
                        })()
                    ''')
                    perf_result = util.parse_nodriver_result(perf_result_raw)

                    if isinstance(perf_result, dict) and perf_result.get('count', 0) > 0:
                        perfs = perf_result.get('perfs', [])
                        target_perf_idx = None  # None means no match yet
                        keyword_matched = False

                        # Check if any is already active
                        has_active = any(p.get('isActive') for p in perfs)

                        # Match performance keyword (use date_keyword for time/venue matching)
                        if date_keyword and not has_active:
                            keywords = util.parse_keyword_string_to_array(date_keyword)

                            for i, perf_item in enumerate(perfs):
                                perf_text = perf_item.get('text', '')
                                for kw in keywords:
                                    # Support AND logic (space-separated keywords)
                                    kw_parts = kw.split(' ') if ' ' in kw else [kw]
                                    all_match = all(part in perf_text for part in kw_parts)
                                    if all_match:
                                        target_perf_idx = i
                                        keyword_matched = True
                                        debug.log(f"[UDN QUICK BUY] Performance matched: {perf_text} with keyword: {kw}")
                                        break
                                if keyword_matched:
                                    break  # Early return when matched

                        # Fallback logic based on date_auto_fallback and mode
                        if not keyword_matched and not has_active:
                            date_auto_fallback = config_dict.get("date_auto_fallback", False)
                            date_mode = config_dict["date_auto_select"].get("mode", "from top to bottom")

                            if date_auto_fallback:
                                debug.log(f"[UDN QUICK BUY] Performance keyword not matched, fallback with mode: {date_mode}")

                                target_perf_idx = util.get_target_index_by_mode(len(perfs), date_mode)

                                if debug.enabled:
                                    selected_perf = perfs[target_perf_idx].get('text', '') if target_perf_idx is not None and target_perf_idx < len(perfs) else ''
                                    debug.log(f"[UDN QUICK BUY] Fallback selected performance: {selected_perf}")
                            else:
                                # Strict mode: default to first
                                target_perf_idx = 0

                        # Click the performance button if not already active
                        if not has_active and target_perf_idx is not None:
                            await tab.evaluate(f'''
                                (() => {{
                                    const perfBtns = document.querySelectorAll('div.sd-btn.bg--gray');
                                    if (perfBtns[{target_perf_idx}]) {{
                                        perfBtns[{target_perf_idx}].click();
                                    }}
                                }})()
                            ''')
                            await tab.sleep(0.3)

                # Step 3: Area selection
                # Get area keywords from config
                area_keyword = area_config["keyword"].strip() if area_config["enable"] else ""

                # Find all ticket rows from VISIBLE tables only
                # Each performance has its own table, controlled by parent .sd-target display
                ticket_info_raw = await tab.evaluate('''
                    (() => {
                        const tables = document.querySelectorAll('table.yd_ticketsTable');
                        const tickets = [];
                        tables.forEach((table) => {
                            // Check if this table's parent container is visible
                            let parent = table.parentElement;
                            let isVisible = true;
                            for (let i = 0; i < 5 && parent; i++) {
                                if (window.getComputedStyle(parent).display === 'none') {
                                    isVisible = false;
                                    break;
                                }
                                parent = parent.parentElement;
                            }
                            if (!isVisible) return;

                            // Get ticket rows from visible table
                            const rows = table.querySelectorAll('tr.main');
                            rows.forEach((row, idx) => {
                                const cells = row.querySelectorAll('td');
                                if (cells.length >= 5) {
                                    const areaName = cells[1] ? cells[1].textContent.trim() : '';
                                    const ticketType = cells[2] ? cells[2].textContent.trim() : '';
                                    const price = cells[3] ? cells[3].textContent.trim() : '';
                                    const buyBtn = cells[4] ? cells[4].querySelector('.yd_btn--link') : null;
                                    const fastcode = buyBtn ? buyBtn.getAttribute('fastcode') : null;
                                    const isDisabled = buyBtn ? buyBtn.style.cursor === 'default' : true;
                                    tickets.push({
                                        index: idx,
                                        areaName: areaName,
                                        ticketType: ticketType,
                                        price: price,
                                        fastcode: fastcode,
                                        isDisabled: isDisabled
                                    });
                                }
                            });
                        });
                        return { tickets: tickets };
                    })()
                ''')
                ticket_info = util.parse_nodriver_result(ticket_info_raw)

                if isinstance(ticket_info, dict) and 'tickets' in ticket_info:
                    tickets = ticket_info['tickets']
                    debug.log(f"[UDN QUICK BUY] Found {len(tickets)} ticket areas")

                    # Find matching area based on keyword (use JSON parsing like other platforms)
                    target_ticket = None
                    if area_keyword:
                        keywords = util.parse_keyword_string_to_array(area_keyword)

                        debug.log(f"[UDN QUICK BUY] Area keywords parsed: {keywords}")

                        # Get keyword_exclude for filtering
                        keyword_exclude = area_config["keyword_exclude"]

                        for kw in keywords:
                            if target_ticket:
                                break  # Early return when matched

                            # Support AND logic (space-separated keywords)
                            kw_parts = kw.split(' ') if ' ' in kw else [kw]

                            for ticket in tickets:
                                if ticket.get('isDisabled'):
                                    continue
                                area_name = ticket.get('areaName', '')

                                # Apply keyword_exclude filter
                                if keyword_exclude and util.reset_row_text_if_match_keyword_exclude(config_dict, area_name):
                                    debug.log(f"[UDN QUICK BUY] Excluded by keyword_exclude: {area_name}")
                                    continue

                                # Check AND logic - all parts must match
                                all_match = all(part in area_name for part in kw_parts)
                                if all_match:
                                    target_ticket = ticket
                                    debug.log(f"[UDN QUICK BUY] Matched area: {area_name} with keyword: {kw}")
                                    break

                    # If no keyword match, apply fallback logic based on area_auto_fallback and mode
                    if not target_ticket:
                        area_auto_fallback = area_config["fallback"]
                        area_mode = area_config["mode"]

                        if area_auto_fallback:
                            # Filter available tickets (not disabled, has fastcode, respecting keyword_exclude)
                            available_tickets = []
                            keyword_exclude = area_config["keyword_exclude"]
                            for ticket in tickets:
                                if ticket.get('isDisabled') or not ticket.get('fastcode'):
                                    continue
                                area_name = ticket.get('areaName', '')
                                if keyword_exclude and util.reset_row_text_if_match_keyword_exclude(config_dict, area_name):
                                    continue
                                available_tickets.append(ticket)

                            if available_tickets:
                                debug.log(f"[UDN QUICK BUY] No keyword match, fallback with mode: {area_mode}")

                                target_ticket = util.get_target_item_from_matched_list(available_tickets, area_mode)

                                if target_ticket:
                                    debug.log(f"[UDN QUICK BUY] Fallback selected area: {target_ticket.get('areaName')}")
                        else:
                            # Strict mode: no fallback, don't select anything
                            debug.log(f"[UDN QUICK BUY] No keyword match, fallback disabled, waiting for manual selection")

                    # Click the buy button
                    if target_ticket and target_ticket.get('fastcode'):
                        fastcode = target_ticket['fastcode']
                        debug.log(f"[UDN QUICK BUY] Clicking buy button for area: {target_ticket.get('areaName')}, fastcode: {fastcode}")

                        await tab.evaluate(f'''
                            (() => {{
                                const btn = document.querySelector('.yd_btn--link[fastcode="{fastcode}"]');
                                if (btn && btn.style.cursor !== 'default') {{
                                    btn.click();
                                }}
                            }})()
                        ''')

                        # Step 4: Handle quantity selection dialog
                        # After clicking "立即購票", a lightbox appears with quantity input
                        # Wait for lightbox with retry
                        ticket_number = config_views.ticket_number(config_dict)
                        lightbox_found = False

                        for retry in range(5):  # Retry up to 5 times (total 2.5s max)
                            await tab.sleep(0.5)

                            qty_set_raw = await tab.evaluate(f'''
                                (() => {{
                                    const activeLightbox = document.querySelector('.yd_lightbox.active');
                                    if (!activeLightbox) return {{ success: false, reason: 'no_lightbox' }};

                                    const qtyInput = activeLightbox.querySelector('#QRY2, .yd_counterNum');
                                    if (!qtyInput) return {{ success: false, reason: 'no_qty_input' }};

                                    // Get max limit
                                    const maxLimit = parseInt(qtyInput.getAttribute('perflimit') || '4');
                                    const targetQty = Math.min({ticket_number}, maxLimit);

                                    // Set quantity
                                    qtyInput.value = targetQty;
                                    qtyInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    qtyInput.dispatchEvent(new Event('change', {{ bubbles: true }}));

                                    return {{ success: true, qty: targetQty, max: maxLimit }};
                                }})()
                            ''')
                            qty_set = util.parse_nodriver_result(qty_set_raw)

                            if isinstance(qty_set, dict) and qty_set.get('success'):
                                lightbox_found = True
                                debug.log(f"[UDN QUICK BUY] Set quantity to {qty_set.get('qty')} (max: {qty_set.get('max')})")

                                await tab.sleep(0.3)

                                # Click the "快速訂購" button
                                await tab.evaluate('''
                                    (() => {
                                        const activeLightbox = document.querySelector('.yd_lightbox.active');
                                        if (!activeLightbox) return;
                                        const submitBtn = activeLightbox.querySelector('#f_btn, input[value="快速訂購"], button.yd_btn--primary');
                                        if (submitBtn) {
                                            submitBtn.click();
                                        }
                                    })()
                                ''')

                                # Mark as submitted to prevent duplicate processing
                                _state["udn_quick_buy_submitted"] = True

                                debug.log("[UDN QUICK BUY] Clicked submit button, waiting for navigation...")

                                # Wait for navigation to checkout page
                                for nav_wait in range(10):  # Wait up to 5 seconds for navigation
                                    await tab.sleep(0.5)
                                    current_url = str(tab.target.url).lower()
                                    if 'utk0206' in current_url:
                                        debug.log("[UDN QUICK BUY] Successfully navigated to checkout page")
                                        break
                                break  # Exit retry loop after successful submit
                            else:
                                if retry == 4:  # Only print on last retry
                                    debug.log(f"[UDN QUICK BUY] Failed to set quantity after retries: {qty_set}")
                    else:
                        debug.log("[UDN QUICK BUY] No available ticket area found")

            except Exception as exc:
                debug.log(f"[UDN QUICK BUY] Error: {exc}")

    else:
        # Kham / Ticket.com.tw handling
        # Performance / area page (.aspx?performance_id= & product_id=)
        # Issue #357: activity-group physical tickets land on UTK0201_000.aspx,
        # which carries the SAME query string (performance_id + product_id +
        # activity_group_id) as the UTK0202 voucher page below. Distinguish by
        # FILE NAME, not activity_group_id: anything that is not UTK0202 is an
        # area page (salesTable) and must run the area + sold-out handler.
        if '.aspx?performance_id=' in url.lower() and 'product_id=' in url.lower() and 'utk0202' not in url.lower():
            model_name = url.split('/')[5] if len(url.split('/')) > 5 else "UTK0204"
            if len(model_name) > 7:
                model_name = model_name[:7]

            # Check realname dialog
            await nodriver_kham_check_realname_dialog(tab, config_dict)

            # Check captcha error
            if captcha_config["enable"]:
                is_reset = await nodriver_kham_check_captcha_text_error(tab, config_dict)
                if is_reset:
                    await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

            # Close dialog buttons (ticket.com.tw uses retry mechanism)
            if "ticket.com.tw" in url:
                await nodriver_ticket_close_dialog_with_retry(tab, config_dict)
            else:
                try:
                    el_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
                    if el_btn:
                        await el_btn.click()
                except:
                    pass

            if area_config["enable"]:
                # Switch to auto seat
                if "ticket.com.tw" in url:
                    # Ticket.com.tw uses different selector
                    await nodriver_ticket_switch_to_auto_seat(tab)
                else:
                    await nodriver_kham_switch_to_auto_seat(tab)

                # Clean sold out rows (kham specific). Only strip the rows here;
                # do NOT location.reload() when all rows are sold out. A bare
                # location.reload() bypasses auto_reload_page_interval and reloads
                # the sold-out page in place every loop (~1x/sec), which Doc 13
                # (Simple Wait) and issue-322 both rule out. When every row is
                # sold out, nodriver_kham_performance() below sees zero area rows,
                # sets is_need_refresh, and reloads via the throttled Simple Wait
                # path instead.
                if "kham.com.tw" in url:
                    try:
                        await tab.evaluate('''
                            (function() {
                                const soldoutRows = document.querySelectorAll('tr.Soldout');
                                soldoutRows.forEach(row => row.remove());
                            })();
                        ''')
                    except:
                        pass

                # An RWD area row only forwards its click to the seat-map <area>,
                # which arrives by XHR; a click before that goes nowhere.
                if is_rwd:
                    await _utk_rwd_wait_for(
                        tab, "(() => document.querySelectorAll('#mapdata area').length > 0)()", 2.0)

                # Area selection and captcha
                is_price_assign_by_bot, is_captcha_sent = await nodriver_kham_performance(
                    tab, config_dict, ocr, domain_name, model_name
                )

                # Set ticket number
                if "ticket.com.tw" in url:
                    select_query = 'div.qty-select input[type="text"]'
                else:
                    select_query = '#AMOUNT'

                try:
                    await tab.evaluate(f'''
                        (function() {{
                            const input = document.querySelector('{select_query}');
                            if (input && (input.value === '' || input.value === '0')) {{
                                input.value = '{ticket_number_value}';
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }})();
                    ''')
                except:
                    pass

                # Check adjacent seat checkbox
                if config_dict["advanced"]["disable_adjacent_seat"]:
                    if "ticket.com.tw" in url:
                        await nodriver_ticket_allow_not_adjacent_seat(tab, config_dict)
                    if "kham.com.tw" in url:
                        await nodriver_kham_allow_not_adjacent_seat(tab, config_dict)

                # Submit if captcha sent
                if is_captcha_sent:
                    try:
                        if "ticket.com.tw" in url:
                            # ticket.com.tw uses <input type="submit"> with id ending in AddShopingCart
                            debug.log("[SUBMIT] Searching for ticket.com.tw submit button...")
                            el_btn = await tab.query_selector('input[id$="AddShopingCart"]')
                            if not el_btn:
                                # Fallback to <a> tag (for other possible layouts)
                                el_btn = await tab.query_selector('a[onclick="return chkCart();"]')
                        elif "orders.ibon.com.tw" in url:
                            # ibon uses <a> tag with id containing AddShopingCart
                            debug.log("[SUBMIT] Searching for ibon submit button...")
                            el_btn = await tab.query_selector('a[id*="AddShopingCart"]')
                            if not el_btn:
                                # Fallback to generic button
                                el_btn = await tab.query_selector('a.btn.btn-primary.btn-block')
                        else:
                            # Kham
                            debug.log("[SUBMIT] Searching for Kham submit button...")
                            el_btn = await tab.query_selector('button[onclick="addShoppingCart();return false;"]')

                        if el_btn:
                            debug.log("[SUBMIT] Submit button found, scrolling into view...")
                            # Scroll button into view first (important for CDP click)
                            try:
                                await el_btn.scroll_into_view()
                                await tab.sleep(0.3)
                            except:
                                pass

                            debug.log("[SUBMIT] Clicking using CDP native click...")
                            # Use NoDriver CDP native click
                            await el_btn.click()
                            debug.log("[SUBMIT] Add shopping cart button clicked successfully!")

                            dialog_result = await _handle_post_submit_dialog(tab, config_dict)

                            if dialog_result == "success":
                                debug.log("[SUBMIT] Waiting for page transition after success...")
                                await tab.sleep(1.0)

                                current_url = tab.target.url
                                url_changed = False
                                for i in range(60):  # 60 * 0.5s = 30s
                                    await tab.sleep(0.5)
                                    new_url = tab.target.url
                                    if new_url != current_url:
                                        debug.log(f"[SUBMIT] Page transitioned to {new_url}")
                                        url_changed = True
                                        break

                                if not url_changed:
                                    debug.log("[SUBMIT] CRITICAL: Success dialog but URL never changed after 30s")
                                    await tab.sleep(5.0)

                            elif dialog_result == "error":
                                debug.log("[SUBMIT] Will retry with new captcha")
                                await tab.sleep(2.0)

                            else:
                                current_url = tab.target.url
                                for i in range(10):  # 10 * 0.5s = 5s
                                    await tab.sleep(0.5)
                                    new_url = tab.target.url
                                    if new_url != current_url:
                                        debug.log(f"[SUBMIT] Page transitioned to {new_url}")
                                        break
                        else:
                            debug.log("[SUBMIT] Add shopping cart button not found")
                    except Exception as exc:
                        debug.log(f"[SUBMIT] Click chkCart/addShoppingCart button fail: {exc}")

        # Ticket.com.tw UTK0205 seat selection page (graphical seat map)
        if "ticket.com.tw" in url and 'utk0205' in url.lower():
            debug.log("Detected ticket.com.tw UTK0205 seat selection page")

            is_seat_selection_success = await nodriver_ticket_seat_main(tab, config_dict, ocr, domain_name)

            debug.log(f"Seat selection result: {is_seat_selection_success}")

        # UTK0202 page - Activity Group voucher ticket selection (new format)
        # URL: UTK0202_.aspx?PERFORMANCE_ID=xxx&PRODUCT_ID=xxx&ACTIVITY_GROUP_ID=xxx&ACTIVITY_GROUP_ITEM_ID=xxx
        # Issue #357: must match the UTK0202 file name explicitly, otherwise the
        # physical-ticket UTK0201_000 area page (identical query string) is
        # wrongly treated as a captcha page and spins forever on empty OCR.
        if 'utk0202' in url.lower() and '.aspx?performance_id=' in url.lower() and 'activity_group_id=' in url.lower():
            model_name = url.split('/')[5] if len(url.split('/')) > 5 else "UTK0202"
            if len(model_name) > 7:
                model_name = model_name[:7]

            debug.log(f"Detected UTK0202 Activity Group ticket page, model: {model_name}")

            # Check realname dialog
            await nodriver_kham_check_realname_dialog(tab, config_dict)

            # Handle captcha if enabled
            is_captcha_sent = False
            if captcha_config["enable"]:
                is_captcha_sent = await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

            if is_captcha_sent:
                # Set ticket number by clicking + button
                ticket_number = int(config_views.ticket_number(config_dict))
                try:
                    # Click + button N times to set ticket number
                    set_result = await tab.evaluate(f'''
                        (function() {{
                            // Try multiple selectors for + button
                            let plusBtn = document.querySelector('button.plus');
                            if (!plusBtn) {{
                                plusBtn = document.querySelector('button[onclick*="opera1"][onclick*="true"]');
                            }}
                            if (!plusBtn) {{
                                // Try by text content
                                const buttons = document.querySelectorAll('button');
                                for (let btn of buttons) {{
                                    if (btn.textContent.trim() === '+') {{
                                        plusBtn = btn;
                                        break;
                                    }}
                                }}
                            }}

                            if (plusBtn) {{
                                for (let i = 0; i < {ticket_number}; i++) {{
                                    plusBtn.click();
                                }}
                                const amountInput = document.querySelector('#AMOUNT');
                                return amountInput ? amountInput.value : '{ticket_number}';
                            }}

                            // Fallback: directly set input value
                            const amountInput = document.querySelector('#AMOUNT');
                            if (amountInput) {{
                                amountInput.value = '{ticket_number}';
                                amountInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                amountInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                if (typeof checkNum === 'function') {{
                                    checkNum(amountInput);
                                }}
                                return amountInput.value + ' (fallback)';
                            }}
                            return null;
                        }})();
                    ''')
                    debug.log(f"Ticket number set to: {set_result}")
                except Exception as exc:
                    debug.log(f"Set ticket number error: {exc}")

                # Click add to cart
                try:
                    btn_selector = 'button[onclick="addShoppingCart();return false;"]'
                    el_btn = await tab.query_selector(btn_selector)
                    if el_btn:
                        await el_btn.click()
                        debug.log("Clicked add to cart button")
                    else:
                        # Try alternative selector
                        el_btn = await tab.query_selector('#addcart button.red')
                        if el_btn:
                            await el_btn.click()
                            debug.log("Clicked add to cart button (alt)")
                except:
                    pass

        # UTK0202/UTK0205 page - Ticket number selection page
        # URL: UTK0202_.aspx?PERFORMANCE_ID=xxx&PERFORMANCE_PRICE_AREA_ID=xxx
        # RWD UTK0202 also comes without PERFORMANCE_PRICE_AREA_ID: 中信's date
        # list opens it with every area of the performance on one page.
        is_rwd_amount_page = is_rwd and '/utk0202_.aspx?performance_id=' in url.lower()
        if ('.aspx?performance_id=' in url.lower() and 'performance_price_area_id=' in url.lower()) or is_rwd_amount_page:
            model_name = url.split('/')[5] if len(url.split('/')) > 5 else "UTK0202"
            if len(model_name) > 7:
                model_name = model_name[:7]

            debug.log(f"Detected UTK0202/UTK0205 ticket number selection page, model: {model_name}")

            # RWD: pick the row before any OCR. Nothing to buy (sold out, no
            # keyword match) reloads at the area-page pace instead of submitting.
            if is_rwd_amount_page and not await nodriver_utk_rwd_assign_ticket_number(tab, url, config_dict):
                try:
                    await tab.reload()
                except Exception as exc:
                    debug.log(f"[CPBL AMOUNT] Reload exception: {exc}")
                reload_interval = config_views.reload_interval(config_dict)
                await tab.sleep(max(reload_interval, CONST_KHAM_RELOAD_SETTLE))
                return tab

            is_captcha_sent = False

            # Check captcha error dialog FIRST (before checking filled state).
            # An error dialog means the server has refreshed the captcha image,
            # so the old value in the input field is stale and must be cleared.
            if captcha_config["enable"]:
                is_reset = await nodriver_kham_check_captcha_text_error(tab, config_dict)
                if is_reset:
                    is_captcha_sent = await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

            # Then check if captcha is already filled (from previous page or above OCR)
            if captcha_config["enable"] and not is_captcha_sent:
                try:
                    captcha_value = await tab.evaluate('''
                        (function() {
                            const input = document.querySelector('input[value="驗證碼"]') ||
                                        document.querySelector('input[placeholder="驗證碼"]') ||
                                        document.querySelector('input[placeholder="請輸入圖片上符號"]') ||
                                        document.querySelector('input[type="text"][maxlength="4"]');
                            return input ? input.value : null;
                        })();
                    ''')
                    if captcha_value and len(captcha_value) == 4 and captcha_value != "驗證碼":
                        is_captcha_sent = True
                        debug.log(f"[CAPTCHA] Already filled: {captcha_value}")
                except:
                    pass

            # Check adjacent seat checkbox
            if config_dict["advanced"]["disable_adjacent_seat"]:
                if "ticket.com.tw" in url:
                    await nodriver_ticket_allow_not_adjacent_seat(tab, config_dict)
                else:
                    await nodriver_kham_allow_not_adjacent_seat(tab, config_dict)

            # Close dialog buttons (ticket.com.tw uses retry mechanism)
            if "ticket.com.tw" in url:
                await nodriver_ticket_close_dialog_with_retry(tab, config_dict)
            else:
                try:
                    el_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
                    if el_btn:
                        await el_btn.click()
                except:
                    pass

            # Handle captcha only if not already sent
            if captcha_config["enable"] and not is_captcha_sent:
                is_captcha_sent = await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

            # Set ticket number
            # For Kham UTK0202 page, there may be multiple ticket types (原價, 身心障礙票, etc.)
            # We need to select the correct input based on ticket type name
            if is_rwd_amount_page:
                pass  # assigned at the top of this branch
            elif "ticket.com.tw" in url:
                select_query = 'div.qty-select input[type="text"]'
                try:
                    await tab.evaluate(f'''
                        (function() {{
                            const input = document.querySelector('{select_query}');
                            if (input && (input.value === '' || input.value === '0')) {{
                                input.value = '{ticket_number_value}';
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }})();
                    ''')
                    debug.log(f"Ticket number set to: {ticket_number_value}")
                except Exception as exc:
                    debug.log(f"Set ticket number error: {exc}")
            elif "orders.ibon.com.tw" in url:
                # ibon - uses SELECT dropdown for ticket number
                select_query = 'select[id*="AMOUNT_DDL"]'
                try:
                    await tab.evaluate(f'''
                        (function() {{
                            const select = document.querySelector('{select_query}');
                            if (select) {{
                                const targetValue = '{ticket_number_value}';
                                // Check if option exists
                                const option = Array.from(select.options).find(opt => opt.value === targetValue);
                                if (option) {{
                                    select.value = targetValue;
                                    select.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                    console.log('[IBON TICKET] Set to: ' + targetValue);
                                    return true;
                                }} else {{
                                    console.log('[IBON TICKET] Target value not available: ' + targetValue);
                                    return false;
                                }}
                            }}
                            console.log('[IBON TICKET] SELECT not found');
                            return false;
                        }})();
                    ''')
                    debug.log(f"[IBON TICKET] Ticket number set to: {ticket_number_value}")
                except Exception as exc:
                    debug.log(f"[IBON TICKET] Set ticket number error: {exc}")
            else:
                # Kham - find the correct ticket type input using pure JavaScript
                try:
                    # Build exclude keywords list for JavaScript
                    exclude_keywords = []
                    if "keyword_exclude" in config_dict:
                        keyword_exclude_str = area_config["keyword_exclude"].strip()
                        try:
                            # Try JSON format first (standard storage format)
                            # Example: "\"輪椅\",\"身障\"" → ["輪椅", "身障"]
                            exclude_keywords = json.loads("[" + keyword_exclude_str + "]")
                        except:
                            # Fallback: semicolon-separated format (Issue #23)
                            if util.CONST_KEYWORD_DELIMITER in keyword_exclude_str:
                                exclude_keywords = [k.strip() for k in keyword_exclude_str.split(util.CONST_KEYWORD_DELIMITER) if k.strip()]
                            else:
                                # Single keyword
                                exclude_keywords = [keyword_exclude_str] if keyword_exclude_str else []

                    exclude_keywords_json = json.dumps(exclude_keywords)

                    # Execute all logic in JavaScript to avoid DOM element passing issues
                    result = await tab.evaluate(f'''
                        (function() {{
                            const ticketNumber = '{ticket_number_value}';
                            const excludeKeywords = {exclude_keywords_json};

                            // Get all ticket type inputs
                            const inputs = document.querySelectorAll('input.numbox[type="number"][id="AMOUNT"]');
                            console.log('[TICKET] Found ' + inputs.length + ' ticket type inputs');

                            if (inputs.length === 0) {{
                                return {{ success: false, message: 'No ticket inputs found' }};
                            }}

                            // Find the first non-excluded ticket type
                            for (let i = 0; i < inputs.length; i++) {{
                                const input = inputs[i];
                                const key = input.getAttribute('key');

                                    if (!key) continue;

                                    // Get ticket type name
                                const nameInput = document.getElementById(key + '_NAME');
                                const typeName = nameInput ? nameInput.value : '';

                                    console.log('[TICKET] Type: ' + typeName + ' (key: ' + key + ')');

                                    // Check exclude keywords
                                let excluded = false;
                                if (typeName && excludeKeywords.length > 0) {{
                                        const lowerTypeName = typeName.toLowerCase();
                                    for (let j = 0; j < excludeKeywords.length; j++) {{
                                        const keyword = excludeKeywords[j].toLowerCase();
                                        if (keyword && lowerTypeName.includes(keyword)) {{
                                                console.log('[TICKET] Excluded: ' + typeName);
                                                excluded = true;
                                                break;
                                            }}
                                        }}
                                    }}

                                if (excluded) continue;

                                // Found valid ticket type - click + button via opera1
                                console.log('[TICKET] Selected: ' + typeName);
                                const currentValue = parseInt(input.value) || 0;
                                const targetValue = parseInt(ticketNumber);

                                if (currentValue < targetValue) {{
                                    // Try to find and call opera1 function
                                    if (typeof opera1 === 'function') {{
                                        for (let clicks = currentValue; clicks < targetValue; clicks++) {{
                                            opera1(key, true);  // true = increase
                                            console.log('[TICKET] Called opera1(' + key + ', true) - click ' + (clicks + 1));
                                        }}
                                    }} else {{
                                        // Fallback: directly set value
                                        input.value = ticketNumber;
                                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                        console.log('[TICKET] Set to: ' + ticketNumber + ' (fallback)');
                                    }}
                                }}

                                return {{
                                    success: true,
                                    typeName: typeName,
                                    key: key,
                                    value: ticketNumber
                                }};
                                }}

                            return {{ success: false, message: 'All ticket types excluded' }};
                            }})();
                    ''')

                    if debug.enabled:
                        # NoDriver returns a complex format, just confirm execution
                        debug.log(f"[TICKET] Ticket selection JavaScript executed")

                except Exception as exc:
                    debug.log(f"Set ticket number error: {exc}")

            # Submit if captcha sent and ticket number assigned
            if is_captcha_sent:
                # Check if login is required (Kham only)
                need_login = False
                if "kham.com.tw" in url:
                    try:
                            login_id = await tab.query_selector('#LOGIN_ID')
                            login_pwd = await tab.query_selector('#LOGIN_PWD')

                            if login_id and login_pwd:
                                # Check if login fields are visible
                                is_visible = await tab.evaluate('''
                                    (function() {
                                        const block1 = document.getElementById('LOGIN_BLOCK1');
                                        const block2 = document.getElementById('LOGIN_BLOCK2');
                                        if (block1 && block2) {
                                            const style1 = window.getComputedStyle(block1);
                                            const style2 = window.getComputedStyle(block2);
                                            return style1.display !== 'none' && style2.display !== 'none';
                                        }
                                        return false;
                                    })();
                                ''')

                                if is_visible:
                                    need_login = True
                                    debug.log("[LOGIN REQUIRED] Login fields detected - triggering idle mechanism")
                    except Exception as e:
                        debug.log(f"Login detection error: {e}")

                # If login required, skip submit and let the next loop retry.
                # Idle triggering belongs to the settings/Web UI layer; with
                # auto-login configured, nodriver_kham_login handles this case.
                if need_login:
                    debug.log("[LOGIN REQUIRED] Skip submit - waiting for login (auto-login or manual)")
                else:
                    # Normal submit flow
                    try:
                        if "ticket.com.tw" in url:
                            # ticket.com.tw uses <input type="submit"> with id ending in AddShopingCart
                            debug.log("[SUBMIT] Searching for ticket.com.tw submit button...")
                            el_btn = await tab.query_selector('input[id$="AddShopingCart"]')
                            if not el_btn:
                                # Fallback to <a> tag
                                el_btn = await tab.query_selector('a[onclick="return chkCart();"]')
                        elif "orders.ibon.com.tw" in url:
                            # ibon uses <a> tag with id containing AddShopingCart
                            debug.log("[SUBMIT] Searching for ibon submit button...")
                            el_btn = await tab.query_selector('a[id*="AddShopingCart"]')
                            if not el_btn:
                                # Fallback to generic button
                                el_btn = await tab.query_selector('a.btn.btn-primary.btn-block')
                        else:
                            # Kham
                            debug.log("[SUBMIT] Searching for Kham submit button...")
                            el_btn = await tab.query_selector('button[onclick="addShoppingCart();return false;"]')

                        if el_btn:
                            debug.log("[SUBMIT] Submit button found, scrolling into view...")
                            # Scroll button into view first (important for CDP click)
                            try:
                                await el_btn.scroll_into_view()
                                await tab.sleep(0.3)
                            except:
                                pass

                            debug.log("[SUBMIT] Clicking using CDP native click...")
                            # Use NoDriver CDP native click
                            await el_btn.click()
                            debug.log("[SUBMIT] Add shopping cart button clicked successfully!")

                            dialog_result = await _handle_post_submit_dialog(tab, config_dict)

                            if dialog_result == "success":
                                debug.log("[SUBMIT] Waiting for page transition after success...")
                                await tab.sleep(1.0)

                                current_url = tab.target.url
                                url_changed = False
                                for i in range(60):  # 60 * 0.5s = 30s
                                    await tab.sleep(0.5)
                                    new_url = tab.target.url
                                    if new_url != current_url:
                                        debug.log(f"[SUBMIT] Page transitioned to {new_url}")
                                        url_changed = True
                                        break

                                if not url_changed:
                                    debug.log("[SUBMIT] CRITICAL: Success dialog but URL never changed after 30s")
                                    await tab.sleep(5.0)

                            elif dialog_result == "error":
                                debug.log("[SUBMIT] Will retry with new captcha")
                                await tab.sleep(2.0)

                            else:
                                current_url = tab.target.url
                                for i in range(10):  # 10 * 0.5s = 5s
                                    await tab.sleep(0.5)
                                    new_url = tab.target.url
                                    if new_url != current_url:
                                        debug.log(f"[SUBMIT] Page transitioned to {new_url}")
                                        break
                        else:
                            debug.log("[SUBMIT] Add shopping cart button not found")
                    except Exception as exc:
                        if debug.enabled:
                            debug.log(f"[SUBMIT] Click chkCart/addShoppingCart button fail: {exc}")
                            import traceback
                            traceback.print_exc()

        # Login page handling (UTK1306)
        if '/utk13/utk1306_.aspx' in url.lower():
            # Close dialog buttons
            try:
                el_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
                if el_btn:
                    await el_btn.click()
            except:
                pass

            if captcha_config["enable"]:
                model_name = url.split('/')[5] if len(url.split('/')) > 5 else "UTK1306"
                if len(model_name) > 7:
                    model_name = model_name[:7]

                # Handle captcha
                await nodriver_kham_captcha(tab, config_dict, ocr, model_name)

                # UDN login (Feature 010: uses same UTK backend as KHAM)
                if 'udnfunlife' in domain_name:
                    udn_account = config_dict["accounts"]["udn_account"]
                    udn_password = config_dict["accounts"]["udn_password"].strip()
                    if len(udn_account) > 4:
                        debug.log(f"[UDN LOGIN] Attempting login with account: {udn_account[:3]}***")
                        await nodriver_kham_login(tab, udn_account, udn_password, ocr, config_dict=config_dict)

                # Each account is filled only on its own site: the RWD team sites
                # are normalized onto this same login URL, and must never receive
                # KHAM or ticket.com.tw credentials.
                # Kham login
                kham_account = config_dict["accounts"]["kham_account"]
                kham_password = config_dict["accounts"]["kham_password"].strip()
                if len(kham_account) > 4 and domain_name.endswith('kham.com.tw'):
                    await nodriver_kham_login(tab, kham_account, kham_password, ocr, config_dict=config_dict)

                # Ticket.com.tw login
                ticket_account = config_dict["accounts"]["ticket_account"]
                ticket_password = config_dict["accounts"]["ticket_password"].strip()
                if len(ticket_account) > 4 and domain_name.endswith('ticket.com.tw'):
                    # Use dedicated ticket login function (different selectors)
                    await nodriver_ticket_login(tab, ticket_account, ticket_password, config_dict)

    # Check if reached checkout page (ticket purchase successful)
    if '/utk02/utk0206_.aspx' in url.lower():
        # Reset quick buy flag since we've reached checkout
        _state["udn_quick_buy_submitted"] = False

        # Show success message (only once)
        if debug.enabled:
            if not _state["shown_checkout_message"]:
                debug.log("[SUCCESS] Reached checkout page - ticket purchase successful!")
        _state["shown_checkout_message"] = True

        # Play sound notification (only once)
        if not _state["played_sound_order"]:
            if config_dict["advanced"]["play_sound"]["order"]:
                play_sound_while_ordering(config_dict)
            send_discord_notification(config_dict, "order", "KHAM")
            send_telegram_notification(config_dict, "order", "KHAM")
        _state["played_sound_order"] = True

        # If headless mode, open browser to show checkout page (only once)
        if config_dict["advanced"]["headless"]:
            if not _state["is_popup_checkout"]:
                import webbrowser
                checkout_url = raw_url
                print(f"搶票成功，請前往該帳號訂單查看: {checkout_url}")
                webbrowser.open_new(checkout_url)
                _state["is_popup_checkout"] = True

    return tab

# ====================================================================================
# Ticket Platform (ticket.com.tw / 年代售票)
# ====================================================================================


# ====================================================================================
# UDN Platform Seat Selection (Feature 010: UDN seat auto select)
# UTK0204 page combines area selection and seat map on the same page
# ====================================================================================


def _kham_match_url(url):
    """Dispatch matcher for the kham entry (issue #50).

    "ticket.com.tw" is matched by host, not by substring: as a substring
    it also matches famiticket.com.tw, which made the kham main run on
    FamiTicket pages too.
    """
    if is_utk_rwd_url(url):
        return True
    host = urllib.parse.urlparse(url).hostname or ""
    return host == "ticket.com.tw" or host.endswith(".ticket.com.tw")


# Platform interface (issue #34, ADR 0005): this module's dispatch entries.
PLATFORM_ENTRIES = [
    PlatformEntry(
        "kham",
        nodriver_kham_main,
        patterns=("kham.com.tw", "tickets.udnfunlife.com"),
        match_fn=_kham_match_url,
    ),
]
