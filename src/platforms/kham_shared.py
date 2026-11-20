#encoding=utf-8
# =============================================================================
# KHAM Shared Layer
# Dependency-closed code shared by the kham family modules (issue #40,
# ADR 0006): the single _state object, the captcha cluster, the UTK tenant
# lookup and the post-submit dialog handler. Cluster modules import from
# here; nothing here imports back from them or from platforms.kham.
# =============================================================================

import re
import time
import traceback
import urllib.parse
import util
import config_views
from nodriver_common import (
    PageState,
    check_and_handle_pause,
    nodriver_get_captcha_image_from_dom_snapshot,
    ocr_classification,
)


# Module-level state (replaces global kham_dict)
# Module-level state. Keys declare their lifecycle here, in one place
# (issue #14, ADR 0002): page keys would be cleared by PageState when
# the main function moves to a different page; every current key is
# session state for the reason stated.


_state = PageState("kham")
_state.declare_session("is_popup_checkout", "checkout popup flag for the order flow")
_state.declare_session("played_sound_order", "sound plays once per purchase flow")
_state.declare_session("shown_checkout_message", "checkout message shows once per run")
_state.declare_session("udn_quick_buy_submitted", "UDN quick-buy submission flag")
_state.declare_session("kham_seat_types_exhausted", "seat-type exhaustion flag for the seat selection flow")
_state.declare_session("utk_rwd_cookie_hosts", "hosts that already received RWD cookies")
_state.declare_session("utk_rwd_login_tries", "RWD login attempt counts per host")
_state.declare_session("utk_rwd_notes", "RWD login notes already shown, remembered per run")


# The only reply meaning the order reached the cart; anything else (bad or empty
# captcha, no seat chosen) is a failure. An allowlist, since the exact
# wrong-captcha wording is unknown.
CONST_KHAM_CART_SUCCESS_KEYWORDS = ("加入購物車",)


def _is_cart_success_message(dialog_text):
    """True when a post-submit dialog says the order reached the cart.

    kham answers an add-to-cart over AJAX with JavaScript rather than
    navigating, so the dialog text is the only immediate signal.
    """
    if not dialog_text:
        return False
    return any(kw in dialog_text for kw in CONST_KHAM_CART_SUCCESS_KEYWORDS)


# Per-site differences, keyed by host. cookie_prefix picks the cookies taken from
# accounts.cpbl_cookie for that host, so one pasted string never hands one
# team's session cookies to another team's site.
CONST_UTK_RWD_TENANTS = {
    "tix.ctbcsports.com": {"cookie_prefix": "CTBC_"},       # 中信兄弟 (+ /DEA 中信特攻)
    "tix.wdragons.com": {"cookie_prefix": "WDRAGONS|"},     # 味全龍
    "guardians.fami.life": {"cookie_prefix": "FBGUARDIANS|"},  # 富邦悍將
    "tix.fubonbraves.com": {"cookie_prefix": "FUBONBRAVES|"},  # 臺北富邦勇士 (PLG)
}


def get_utk_rwd_tenant(url_or_host):
    """The CONST_UTK_RWD_TENANTS entry for a URL or a bare host, else None."""
    text = (url_or_host or "").strip().lower()
    host = urllib.parse.urlsplit(text).hostname if "//" in text else text.split(":")[0]
    return CONST_UTK_RWD_TENANTS.get(host or "")


def is_utk_rwd_url(url_or_host):
    """True for a page (or host) of one of the 中職 UTK RWD ticketing sites."""
    return get_utk_rwd_tenant(url_or_host) is not None


async def _utk_rwd_wait_for(tab, script, timeout):
    """Poll a page condition; the RWD lists and maps arrive by XHR after load."""
    for _ in range(max(1, int(timeout / 0.2))):
        try:
            if await tab.evaluate(script):
                return True
        except Exception:
            pass
        await tab.sleep(0.2)
    return False


async def _handle_post_submit_dialog(tab, config_dict):
    """
    After clicking submit, wait for dialog, classify as success/error/none.
    Returns: "success", "error", or "none"
    - success: dialog contained cart-added message, closed it
    - error: dialog contained error message, closed it and cleared captcha
    - none: no dialog appeared within timeout
    """
    debug = util.create_debug_logger(config_dict)

    for i in range(10):  # 10 * 0.5s = 5s
        await tab.sleep(0.5)
        try:
            dialog_text = await tab.evaluate('''
                (function() {
                    // Only a VISIBLE dialog counts. jQuery UI keeps the wrapper
                    // in the DOM after close, with the previous message still
                    // inside it, so an existence check reads the last round's
                    // text and classifies a fresh submit on a stale reply.
                    // Verified 2026-09-21 on kham UTK0205: after clicking Ok the
                    // wrapper stays with display:none and #dialog-message still
                    // holds the old message.
                    var wrap = document.querySelector('div.ui-dialog');
                    if (!wrap || window.getComputedStyle(wrap).display === 'none') {
                        return null;
                    }
                    var el = wrap.querySelector('div#dialog-message.ui-dialog-content');
                    return el ? el.textContent : null;
                })();
            ''')

            if dialog_text is not None:
                is_success = _is_cart_success_message(dialog_text)

                el_btn = await tab.query_selector('div.ui-dialog-buttonset > button.ui-button')
                if el_btn:
                    await el_btn.click()
                    await tab.sleep(0.5)

                if is_success:
                    debug.log("[SUBMIT] Success dialog closed")
                    return "success"
                else:
                    debug.log(f"[SUBMIT] Error dialog closed: {dialog_text}")
                    await nodriver_kham_keyin_captcha_code(tab, "")
                    return "error"
        except Exception as e:
            if i == 9:
                debug.log(f"[SUBMIT] Dialog detection failed: {e}")

    debug.log("[SUBMIT] No dialog appeared within 5 seconds")
    return "none"


async def nodriver_kham_keyin_captcha_code(tab, answer="", auto_submit=False):
    """
    Input captcha code manually or auto-submit
    """
    is_verifyCode_editing = False

    # Find captcha input with multiple selectors
    form_verifyCode = None
    selectors = [
        'input#CHK',
        '#ctl00_ContentPlaceHolder1_CHK',
        'input[value="驗證碼"]',
        'input[placeholder="驗證碼"]',
        'input[placeholder="請輸入圖片上符號"]',
        'input[type="text"][maxlength="4"]'
    ]

    for selector in selectors:
        try:
            form_verifyCode = await tab.query_selector(selector)
            if form_verifyCode:
                break
        except:
            continue

    is_start_to_input_answer = False
    if form_verifyCode:
        if len(answer) > 0:
            # Check current input value
            try:
                inputed_value = await tab.evaluate(f'''
                    (function() {{
                        const input = document.querySelector('{selectors[0]}') ||
                                    document.querySelector('{selectors[1]}') ||
                                    document.querySelector('{selectors[2]}') ||
                                    document.querySelector('{selectors[3]}') ||
                                    document.querySelector('{selectors[4]}') ||
                                    document.querySelector('{selectors[5]}');
                        return input ? input.value : null;
                    }})();
                ''')

                if inputed_value is None:
                    inputed_value = ""

                # Clear if placeholder text
                if inputed_value == "驗證碼":
                    try:
                        await form_verifyCode.apply('function(el) { el.value = ""; }')
                    except:
                        pass
                else:
                    if len(inputed_value) > 0:
                        print("Captcha text already inputed:", inputed_value, "target answer:", answer)
                        is_verifyCode_editing = True
                    else:
                        is_start_to_input_answer = True
            except Exception as exc:
                print("Check verify code value fail:", exc)
        else:
            # Clear input
            try:
                await form_verifyCode.apply('function(el) { el.value = ""; }')
            except:
                pass

    if is_start_to_input_answer:
        try:
            await form_verifyCode.click()
            await form_verifyCode.apply('function(el) { el.value = ""; }')
            await form_verifyCode.send_keys(answer)
        except Exception as exc:
            print("Send keys OCR answer fail:", answer, exc)

    # Auto submit if enabled (for away_from_keyboard mode)
    if auto_submit:
        try:
            # Find and click submit button using NoDriver CDP
            # Year Ticket (ticket.com.tw): AddShopingCart button
            print("[AUTO SUBMIT] Searching for submit button...")
            submit_button = await tab.query_selector('input[id$="AddShopingCart"]')

            if submit_button:
                print("[AUTO SUBMIT] Submit button found, checking if enabled...")
                # Check if button is enabled
                is_enabled = await submit_button.apply('function(el) { return !el.disabled; }')

                if is_enabled:
                    print("[AUTO SUBMIT] Button enabled, scrolling into view...")
                    # Scroll button into view first (important for CDP click)
                    try:
                        await submit_button.scroll_into_view()
                        await tab.sleep(0.3)
                    except:
                        pass

                    print("[AUTO SUBMIT] Clicking submit button using CDP native click...")
                    # Use NoDriver CDP native click
                    await submit_button.click()
                    print("[AUTO SUBMIT] Submit button clicked successfully!")
                else:
                    print("[AUTO SUBMIT] Submit button is disabled")
            else:
                print("[AUTO SUBMIT] Submit button not found (selector: input[id$=\"AddShopingCart\"])")
        except Exception as exc:
            print(f"[AUTO SUBMIT] Error: {exc}")
            import traceback
            traceback.print_exc()

    return is_verifyCode_editing




CONST_KHAM_CAPTCHA_REFRESH_JS = '''
    (() => {
        const img = document.querySelector('#chk_pic');
        if (!img) return 'no_image';
        // RWD pages ship a captcha widget with its own refresh (and loading mask).
        if (typeof $ === 'function' && typeof $(img).captchaInstance === 'function') {
            try { $(img).captchaInstance().refresh(); return 'widget'; } catch (e) {}
        }
        // Rebuild from the page's own URL: the TYPE must stay exactly as served
        // (e.g. UTK0201_001), which a truncated model name would not.
        const base = img.getAttribute('data-captcha-url') || img.src.split('&ts=')[0];
        img.src = base + (base.indexOf('?') >= 0 ? '&' : '?') + 'ts=' + Date.now();
        return 'src';
    })()
'''


async def nodriver_kham_refresh_captcha(tab, config_dict):
    """Ask the page for a new captcha image; returns how it was refreshed."""
    debug = util.create_debug_logger(config_dict)
    try:
        method = await tab.evaluate(CONST_KHAM_CAPTCHA_REFRESH_JS)
        debug.log(f"[KHAM OCR] Captcha refreshed via {method}")
        await tab.sleep(0.3)
        return method
    except Exception as exc:
        debug.log(f"[KHAM OCR] Captcha refresh failed: {exc}")
        return None


async def nodriver_kham_auto_ocr(tab, config_dict, ocr, away_from_keyboard_enable, previous_answer, model_name):
    """
    Auto OCR captcha recognition
    """
    debug = util.create_debug_logger(config_dict)

    debug.log("Starting Kham OCR processing...")
    debug.log("away_from_keyboard_enable:", away_from_keyboard_enable)
    debug.log("previous_answer:", previous_answer)

    is_need_redo_ocr = False
    is_form_submitted = False

    ocr_answer = None
    if ocr:
        import time
        ocr_start_time = time.time()

        # Get captcha image using DOMSnapshot (shared with ibon)
        img_base64 = await nodriver_get_captcha_image_from_dom_snapshot(tab, config_dict)

        if img_base64:
            try:
                ocr_answer = await ocr_classification(ocr, img_base64)
            except Exception as exc:
                debug.log("OCR classification error:", exc)

        ocr_done_time = time.time()
        ocr_elapsed_time = ocr_done_time - ocr_start_time
        debug.log(f"OCR elapsed time: {ocr_elapsed_time:.3f}s")
    else:
        debug.log("[KHAM OCR] OCR engine is None")

    if ocr_answer:
        ocr_answer = ocr_answer.strip()
        debug.log(f"[KHAM OCR] OCR answer: {ocr_answer}")

        if len(ocr_answer) == 4:
            # Valid 4-character answer
            previous_answer = ocr_answer  # Update previous_answer to mark as sent
            who_care_var = await nodriver_kham_keyin_captcha_code(tab, answer=ocr_answer, auto_submit=away_from_keyboard_enable)
        else:
            # Invalid length: ask for a new image either way. Without it the
            # next pass re-reads the same image and gets the same wrong answer
            # forever (seen on UTK0201_001, a 5-char read of a 4-char captcha).
            await nodriver_kham_refresh_captcha(tab, config_dict)
            if not away_from_keyboard_enable:
                await nodriver_kham_keyin_captcha_code(tab, "")
            else:
                is_need_redo_ocr = True
                if previous_answer != ocr_answer:
                    previous_answer = ocr_answer
    else:
        debug.log(f"[KHAM OCR] OCR answer is None, previous_answer: {previous_answer}")
        if previous_answer is None:
            await nodriver_kham_keyin_captcha_code(tab, "")
        else:
            is_need_redo_ocr = True

    return is_need_redo_ocr, previous_answer, is_form_submitted


async def nodriver_kham_captcha(tab, config_dict, ocr, model_name):
    """
    Captcha main control with retry logic
    """
    # 函數開始時檢查暫停
    if await check_and_handle_pause(config_dict):
        return False

    captcha_config = config_views.captcha_view(config_dict)
    away_from_keyboard_enable = captcha_config["force_submit"]
    if not captcha_config["enable"]:
        away_from_keyboard_enable = False

    # PS: need 'auto assign seat' feature to enable away_from_keyboard
    away_from_keyboard_enable = False

    is_captcha_sent = False
    previous_answer = None
    last_url = tab.target.url

    for redo_ocr in range(999):
        is_need_redo_ocr, previous_answer, is_form_submitted = await nodriver_kham_auto_ocr(
            tab, config_dict, ocr, away_from_keyboard_enable, previous_answer, model_name
        )

        # If captcha found and processed, set flag to True
        if previous_answer is not None:
            is_captcha_sent = True

        if is_form_submitted:
            break

        if not away_from_keyboard_enable:
            break

        if not is_need_redo_ocr:
            break

        current_url = tab.target.url
        if current_url != last_url:
            break

    return is_captcha_sent


# Seconds to let a reloaded page settle before reading its DOM: tab.reload()
# returns once CDP sends Page.reload, before the new document parses (~200ms on
# UTK0204). Same 0.5s nodriver_kham_date_auto_select waits after its reload.
CONST_KHAM_RELOAD_SETTLE = 0.5


# A free RWD seat has no class: it is a #TBL cell drawn with seat-empty_N.png
# (N = facing) and titled with the seat name; taken seats get no title.
CONST_UTK_RWD_EMPTY_SEAT_IMG = "seat-empty_"
