#encoding=utf-8
# =============================================================================
# TixCraft Shared Layer
# Dependency-closed code shared by the tixcraft family modules (issue #48,
# ADR 0006): the single _state object, the captcha/OCR cluster, the reload
# gate and the soft-block helpers. Cluster modules import from here;
# nothing here imports back from them or from platforms.tixcraft.
# =============================================================================

import asyncio
import base64
import json
import random
import time
from zendriver import cdp
import util
import config_views
from nodriver_common import (
    CONST_MAXBOT_ANSWER_ONLINE_FILE,
    CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS,
    CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER,
    PageState,
    nodriver_check_checkbox_enhanced,
    nodriver_get_text_by_selector,
    write_question_to_file,
    ocr_classification,
)


# Module-level state (replaces global tixcraft_dict). Keys declare their
# lifecycle here, in one place (issue #7, ADR 0002): page keys are cleared
# by PageState when the main function moves to a different page; session
# keys survive page changes for the reason stated.
# Page ids for PageState: the main function classifies the current page
# into one of these per pass. Declarations and set_page share the names
# so a typo cannot silently disable a key's clearing.
_PAGE_DATE = "tixcraft_date"
_PAGE_AREA = "tixcraft_area"
_PAGE_TICKETMASTER_DATE = "ticketmaster_date"
_PAGE_TICKETMASTER_AREA = "ticketmaster_area"
_PAGE_TICKET = "ticket"

_state = PageState("tixcraft")
_state.declare_page("date_reload_cooldown_until", _PAGE_DATE)
_state.declare_page("area_reload_cooldown_until", _PAGE_AREA)
_state.declare_page("ticketmaster_date_reload_cooldown_until", _PAGE_TICKETMASTER_DATE)
_state.declare_page("ticketmaster_area_reload_cooldown_until", _PAGE_TICKETMASTER_AREA)
_state.declare_session("fail_list", "area failure memory; reset by the area-page exit branch")
_state.declare_session("fail_promo_list", "promo failure memory; reset by the area-page exit branch")
_state.declare_session("start_time", "purchase-flow timing for the whole run")
_state.declare_session("done_time", "purchase-flow timing for the whole run")
_state.declare_session("elapsed_time", "purchase-flow timing for the whole run")
_state.declare_session("is_popup_checkout", "checkout popup flag for the order flow")
_state.declare_session("area_retry_count", "reset by the area-page exit branch")
_state.declare_session("played_sound_ticket", "sound plays once per purchase flow")
_state.declare_session("played_sound_order", "sound plays once per purchase flow")
_state.declare_session("alert_handler_tab", "the tab object holding the global alert handler; the handler lives on that tab's CDP connection, so a different working tab re-registers (issue #94)")
_state.declare_session("captcha_alert_detected", "captcha retry guard shared by alert handler and ticket page")
_state.declare_session("captcha_submit_until", "captcha retry guard shared by alert handler and ticket page")
_state.declare_session("ocr_completed_url", "URL-keyed OCR dedup across pages")
_state.declare_session("last_homepage_redirect_time", "homepage redirect pacing across pages")
_state.declare_session("sold_out_cooldown_until", "wall-clock cooldown, cross-page by design (issue #188)")
_state.declare_session("printed_completed", "completion banner prints once per run")
_state.declare_session("ticketmaster_phase", "phase machine; reset by the area-page exit branch")
_state.declare_session("ticketmaster_captcha_processed_url", "URL-keyed captcha dedup across pages")
_state.declare_session("ip_block_until", "IP block window applies on every page")
_state.declare_session("queue_it_enter_time", "queue wait timing for the whole run")
_state.declare_session("cookie_accepted", "cookie banner is accepted once per run")
_state.declare_session("html_lang", "page language cached for the whole run")
_state.declare_session("reload_disabled_logged_tags", "log-once tags for the whole run")
_state.declare_session_prefix("ticket_assigned_", "per-ticket assignment markers keyed by URL and ticket count; kept for the run so a set count is not re-entered; capped at 128 entries, oldest evicted first (issue #92)")
_state.declare_session_prefix("ticket_unavailable_backs_", "per-ticket-URL count of returns to area selection after the target ticket count proved unavailable (issue #98); log visibility only, cleared on assignment success and by _forget_ticket_assigned; capped at 128 entries, oldest evicted first, like ticket_assigned_")


# Keywords that identify serial-number / membership-code / promo-code style
# verify prompts -- i.e. prompts the discount_code setting can actually answer.
# Used as a guard for the discount_code fallback in nodriver_tixcraft_input_check_code
# to avoid wasting an attempt on unrelated questions (math, common knowledge, etc.).
# Match is case-insensitive substring on question_text; covers zh-TW/zh-CN/en/ja/ko.
_SERIAL_CODE_QUESTION_KEYWORDS = (
    "會員",        # zh-TW: member (會員)
    "会员",        # zh-CN: member (会员)
    "序號",        # zh-TW: serial (序號)
    "序号",        # zh-CN: serial (序号)
    "編號",        # zh-TW: ID/number (編號)
    "编号",        # zh-CN: ID/number (编号)
    "員編",        # zh-TW: member ID (員編)
    "会員番号",  # ja: member number (会員番号)
    "シリアル",  # ja: serial (シリアル)
    "회원",        # ko: member (회원)
    "멤버십",  # ko: membership (멤버십)
    "membership",
    "member id",
    "member no",
    "serial",
    "weverse",
    # Promo / discount code prompts (Ticketmaster #promoBox and TixCraft
    # discount gates). These read the same discount_code setting as the
    # member/serial prompts above, so they belong on the same allow list.
    "優惠碼",      # zh-TW: promo code
    "优惠码",      # zh-CN: promo code
    "折扣碼",      # zh-TW: discount code
    "折扣码",      # zh-CN: discount code
    "兌換碼",      # zh-TW: redemption code
    "兑换码",      # zh-CN: redemption code
    "promo",       # also covers "promotion" / "promotional code"
    "discount",
    "coupon",
)


_TIXCRAFT_SOFT_BLOCK_SCOPE_HOSTS = (
    "tixcraft.com",
    "teamear.com",
    "indievox.com",
)


def _is_serial_code_question(question_text):
    if not question_text:
        return False
    text_lower = question_text.lower()
    for kw in _SERIAL_CODE_QUESTION_KEYWORDS:
        if kw.lower() in text_lower:
            return True
    return False


def _should_leave_dialog_to_user(current_url):
    """True when a JS dialog on this page must NOT be answered automatically.

    Checkout dialogs are the user's decision, not the bot's. cdp.page
    .handle_java_script_dialog(accept=True) is the OK button of a confirm(),
    so auto-answering the checkout page's "Are you sure you want to cancel
    your order?" actually cancels the order (issue #378).
    """
    return '/ticket/checkout' in (current_url or '')


def _is_tixcraft_soft_block_scope(url):
    url_lower = (url or "").lower()
    return any(host in url_lower for host in _TIXCRAFT_SOFT_BLOCK_SCOPE_HOSTS)


def _parse_tixcraft_soft_block_delay(config_dict):
    advanced = (config_dict or {}).get("advanced", {})
    raw_value = advanced.get("tixcraft_soft_block_delay", "")

    if raw_value is None:
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    try:
        delay_seconds = int(text)
    except (TypeError, ValueError):
        return None

    if delay_seconds <= 0:
        return None

    return min(delay_seconds, 600)


def _resolve_soft_block_wait_seconds(config_dict, scope_url, default_wait_seconds=None):
    custom_delay = _parse_tixcraft_soft_block_delay(config_dict)
    if custom_delay is not None and _is_tixcraft_soft_block_scope(scope_url):
        return custom_delay, True

    if default_wait_seconds is None:
        default_wait_seconds = random.randint(240, 420)

    return default_wait_seconds, False


def _is_page_reload_enabled(config_dict, debug, log_tag):
    """True when auto_reload_page_interval allows a polling reload (issue #396).

    The settings help defines 0 as "disable auto reload", but the date and
    area selection sites, which reload to retry when nothing was selected,
    only skipped the wait before tab.reload(), so 0 still reloaded the page,
    on most of them once per 50ms main-loop pass. A value <= 0 now skips
    those polling reloads. Recovery reloads (TixCraft ticket page, issue
    #174; Ticketmaster after 30 retries, issue #378) do not use this check
    and keep their original behavior.
    The skip is logged once per call site. A site's mark is cleared the next
    time that same site runs with an interval above 0, so a change between
    two values <= 0 is not logged again. The mark is only set while verbose
    is on, so a site that skipped while verbose was off still logs the first
    time verbose is on.
    """
    interval = config_dict.get("advanced", {}).get("auto_reload_page_interval", 0)
    try:
        is_enabled = interval > 0
    except TypeError:
        # Non-numeric value (e.g. a hand-edited string): the Ticketmaster
        # date and area sites swallowed this TypeError inside try/except and
        # did not reload, while the TixCraft date and area sites raised it.
        # Treat it as reload disabled at every polling reload site.
        is_enabled = False
    logged_tags = _state.setdefault("reload_disabled_logged_tags", set())
    if is_enabled:
        logged_tags.discard(log_tag)
        return True
    if debug.enabled and log_tag not in logged_tags:
        debug.log(f"[{log_tag}] auto_reload_page_interval={interval!r}, page reload disabled")
        logged_tags.add(log_tag)
    return False


async def nodriver_fill_verify_form(tab, config_dict, inferred_answer_string, fail_list, input_text_css, next_step_button_css, submit_by_enter, check_input_interval):
    """
    NoDriver version of fill_common_verify_form for TixCraft verification.

    Fills verification form input and submits the answer.

    Args:
        tab: NoDriver tab object
        config_dict: Configuration dictionary
        inferred_answer_string: Answer to fill in
        fail_list: List of failed answers
        input_text_css: CSS selector for input field
        next_step_button_css: CSS selector for submit button (optional)
        submit_by_enter: Whether to submit by pressing Enter
        check_input_interval: Interval to wait when no answer

    Returns:
        tuple[bool, list]: (is_answer_sent, updated fail_list)
    """
    debug = util.create_debug_logger(config_dict)

    is_answer_sent = False

    try:
        # Check if input field exists and get current value
        input_info = await tab.evaluate(f'''
            (function() {{
                var input = document.querySelector("{input_text_css}");
                if (input) {{
                    return {{
                        exists: true,
                        value: input.value || ""
                    }};
                }}
                return {{ exists: false, value: "" }};
            }})()
        ''')
        input_info = util.parse_nodriver_result(input_info)

        if not input_info or not input_info.get('exists', False):
            debug.log("[VERIFY FORM] Input field not found:", input_text_css)
            return is_answer_sent, fail_list

        inputed_value = input_info.get('value', '')

        debug.log(f"[VERIFY FORM] Current input value: '{inputed_value}'")
        debug.log(f"[VERIFY FORM] Answer to fill: '{inferred_answer_string}'")

        if len(inferred_answer_string) > 0:
            # Fill the answer if different from current value
            if inputed_value != inferred_answer_string:
                # Clear and fill using JavaScript
                await tab.evaluate(f'''
                    (function() {{
                        var input = document.querySelector("{input_text_css}");
                        if (input) {{
                            input.value = "";
                            input.value = {json.dumps(inferred_answer_string)};
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        }}
                    }})()
                ''')

                debug.log(f"[VERIFY FORM] Filled answer: {inferred_answer_string}")

            # Submit the form
            is_button_clicked = False

            if submit_by_enter:
                # Submit by pressing Enter
                await tab.evaluate(f'''
                    (function() {{
                        var input = document.querySelector("{input_text_css}");
                        if (input) {{
                            var event = new KeyboardEvent('keydown', {{
                                key: 'Enter',
                                code: 'Enter',
                                keyCode: 13,
                                which: 13,
                                bubbles: true
                            }});
                            input.dispatchEvent(event);

                            // Also try form submit
                            var form = input.closest('form');
                            if (form) {{
                                form.submit();
                            }}
                        }}
                    }})()
                ''')
                is_button_clicked = True
                debug.log("[VERIFY FORM] Submitted by Enter key")
            elif len(next_step_button_css) > 0:
                # Click the submit button
                try:
                    btn = await tab.query_selector(next_step_button_css)
                    if btn:
                        await btn.click()
                        is_button_clicked = True
                        debug.log(f"[VERIFY FORM] Clicked submit button: {next_step_button_css}")
                except Exception as btn_exc:
                    debug.log(f"[VERIFY FORM] Failed to click button: {btn_exc}")

            if is_button_clicked:
                is_answer_sent = True
                fail_list.append(inferred_answer_string)
                debug.log(f"[VERIFY FORM] Answer sent, attempt #{len(fail_list)}")

                # Wait and check for alert
                await asyncio.sleep(0.3)
        else:
            # No answer to fill, just focus the input
            if len(inputed_value) == 0:
                await tab.evaluate(f'''
                    (function() {{
                        var input = document.querySelector("{input_text_css}");
                        if (input && document.activeElement !== input) {{
                            input.focus();
                        }}
                    }})()
                ''')
                await asyncio.sleep(check_input_interval)
                debug.log("[VERIFY FORM] No answer, focused input field")

    except Exception as exc:
        debug.log(f"[VERIFY FORM] Error: {exc}")

    return is_answer_sent, fail_list


async def nodriver_tixcraft_input_check_code(tab, config_dict, fail_list, question_selector):
    debug = util.create_debug_logger(config_dict)

    answer_list = []

    question_text = await nodriver_get_text_by_selector(tab, question_selector, 'innerText')
    if len(question_text) > 0:
        write_question_to_file(question_text)

        answer_config = config_views.answer_view(config_dict)
        answer_list = util.get_answer_list_from_user_guess_string(config_dict, CONST_MAXBOT_ANSWER_ONLINE_FILE)
        if len(answer_list)==0:
            if answer_config["auto_guess_options"]:
                # Note: guess_tixcraft_question() doesn't use the driver parameter
                answer_list = util.guess_tixcraft_question(None, question_text, config_dict)

        # Fallback: use discount_code as final answer when user_guess_string is empty
        # and auto_guess_options yielded no result. Covers serial-number style prompts
        # (e.g. Weverse Presale MY MEMBERSHIP) and promo-code gates (Ticketmaster
        # #promoBox) where users naturally fill the discount code field rather than
        # the answer dictionary.
        # Guard: only trigger for member/serial/promo style prompts so the
        # discount_code is not wasted on unrelated questions (math, trivia, etc.).
        if len(answer_list)==0:
            discount_code_fallback = (answer_config["discount_code"] or "").strip()
            if discount_code_fallback and _is_serial_code_question(question_text):
                debug.log("[VERIFY] Using discount_code as serial-code fallback answer")
                answer_list = [discount_code_fallback]

        inferred_answer_string = ""
        for answer_item in answer_list:
            if not answer_item in fail_list:
                inferred_answer_string = answer_item
                break

        debug.log("inferred_answer_string:", inferred_answer_string)
        debug.log("answer_list:", answer_list)

        # PS: auto-focus() when empty inferred_answer_string with empty inputed text value.
        input_text_css = "input[name='checkCode']"
        next_step_button_css = "button.btn.btn-primary"
        submit_by_enter = False
        check_input_interval = 0.2
        is_answer_sent, fail_list = await nodriver_fill_verify_form(tab, config_dict, inferred_answer_string, fail_list, input_text_css, next_step_button_css, submit_by_enter, check_input_interval)

    return fail_list


async def nodriver_tixcraft_keyin_captcha_code(tab, answer="", auto_submit=False, config_dict=None):
    """輸入驗證碼到表單"""
    debug = util.create_debug_logger(config_dict) if config_dict else util.create_debug_logger(enabled=False)
    is_verifyCode_editing = False
    is_form_submitted = False

    # 找到驗證碼輸入框
    form_verifyCode = await tab.query_selector('#TicketForm_verifyCode')

    if form_verifyCode:
        is_visible = False
        try:
            # 檢查元素是否可見和可用
            is_visible = await tab.evaluate('''
                (function() {
                    const element = document.querySelector('#TicketForm_verifyCode');
                    return element && !element.disabled && element.offsetParent !== null;
                })();
            ''')
        except Exception as exc:
            pass

        if is_visible:
            # 取得當前輸入值
            inputed_value = ""
            try:
                inputed_value = await form_verifyCode.apply('function (element) { return element.value; }') or ""
            except Exception as exc:
                pass

            is_text_clicked = False

            if not inputed_value and not answer:
                # 聚焦到輸入框等待手動輸入
                try:
                    await form_verifyCode.click()
                    is_text_clicked = True
                    is_verifyCode_editing = True
                except Exception as exc:
                    debug.log("[TIXCRAFT CAPTCHA] Failed to click captcha input, trying JavaScript")
                    try:
                        await tab.evaluate('''
                            document.getElementById("TicketForm_verifyCode").focus();
                        ''')
                        is_verifyCode_editing = True
                    except Exception as exc:
                        pass

            if answer:
                debug.log("[TIXCRAFT CAPTCHA] Starting to fill in captcha...")
                try:
                    if not is_text_clicked:
                        await form_verifyCode.click()

                    # 清空並輸入答案
                    await form_verifyCode.apply('function (element) { element.value = ""; }')
                    await form_verifyCode.send_keys(answer)

                    if auto_submit:
                        # 提交前確認票券數量是否已設定
                        ticket_number = str(config_views.ticket_number(config_dict)) if config_dict else "2"
                        allow_less_tickets = config_dict.get("tixcraft", {}).get("allow_less_tickets", False) if config_dict else False
                        # Issue #200/#201: some tixcraft events render multiple ticket
                        # quantity selects; inspect every visible select, not just the first.
                        ticket_number_ok = await tab.evaluate(f'''
                            (function() {{
                                if (window.location.href.includes('ticketmaster')) return true;
                                const target = parseInt("{ticket_number}");
                                const allowLess = {str(bool(allow_less_tickets)).lower()};
                                const selects = Array.from(document.querySelectorAll(
                                    '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                                )).filter(s => s && !s.disabled);
                                return selects.some(s => {{
                                    if (s.value === "0" || s.value === "") return false;
                                    const current = parseInt(s.value);
                                    if (isNaN(current) || isNaN(target)) return false;
                                    return allowLess ? (current > 0 && current <= target) : (current === target);
                                }});
                            }})();
                        ''')
                        ticket_number_ok = util.parse_nodriver_result(ticket_number_ok)

                        if not ticket_number_ok and config_dict:
                            debug.log("[TIXCRAFT CAPTCHA] Warning: Ticket number not set, resetting...")
                            # Reset ticket number across all selects. Honor allow_less_tickets:
                            # when disabled only set the exact target option; otherwise fall back
                            # to the largest option within (0, target] (leave untouched if none,
                            # so Issue #174 reload retry can take over).
                            ticket_number_js = json.dumps(ticket_number)
                            await tab.evaluate(f'''
                                (function() {{
                                    const target = parseInt("{ticket_number}");
                                    const allowLess = {str(bool(allow_less_tickets)).lower()};
                                    const selects = Array.from(document.querySelectorAll(
                                        '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                                    )).filter(s => s && !s.disabled);
                                    const pick = (s) => {{
                                        let opt = Array.from(s.options).find(o =>
                                            o.value === {ticket_number_js} && !o.disabled);
                                        if (!opt && allowLess) {{
                                            const cands = Array.from(s.options).filter(o =>
                                                !o.disabled && parseInt(o.value) > 0 && parseInt(o.value) <= target);
                                            opt = cands.sort((a, b) => parseInt(b.value) - parseInt(a.value))[0];
                                        }}
                                        return opt;
                                    }};
                                    for (const s of selects) {{
                                        const opt = pick(s);
                                        if (opt) {{
                                            s.value = opt.value;
                                            s.dispatchEvent(new Event('change', {{bubbles: true}}));
                                            break;
                                        }}
                                    }}
                                }})();
                            ''')

                        # 勾選同意條款
                        await nodriver_check_checkbox_enhanced(tab, '#TicketForm_agree')

                        # 最終確認所有欄位都已填寫
                        form_ready = await tab.evaluate(f'''
                            (function() {{
                                const verify = document.querySelector('#TicketForm_verifyCode');
                                const agree = document.querySelector('#TicketForm_agree');

                                // Ticketmaster check-captcha page has no ticket selector
                                // Ticket number is already set on previous page
                                const isTicketmaster = window.location.href.includes('ticketmaster');
                                const target = parseInt("{ticket_number}");
                                const allowLess = {str(bool(allow_less_tickets)).lower()};
                                const selects = Array.from(document.querySelectorAll(
                                    '.mobile-select, select[id*="TicketForm_ticketPrice_"]'
                                )).filter(s => s && !s.disabled);
                                const matched = selects.find(s => {{
                                    if (s.value === "0" || s.value === "") return false;
                                    const current = parseInt(s.value);
                                    if (isNaN(current) || isNaN(target)) return false;
                                    return allowLess ? (current > 0 && current <= target) : (current === target);
                                }});
                                const ticketOk = isTicketmaster ? true : !!matched;

                                return {{
                                    ticket: ticketOk,
                                    ticket_select: matched ? (matched.id || matched.name || matched.className || "") : "",
                                    verify: verify && verify.value.length === 4,
                                    agree: agree && agree.checked,
                                    ready: ticketOk &&
                                           (verify && verify.value.length === 4) &&
                                           (agree && agree.checked)
                                }};
                            }})();
                        ''')
                        form_ready = util.parse_nodriver_result(form_ready)

                        if form_ready.get('ready', False):
                            # 提交表單 (按 Enter) - 使用完整的鍵盤事件
                            await tab.send(cdp.input_.dispatch_key_event("keyDown", code="Enter", key="Enter", text="\r", windows_virtual_key_code=13))
                            await tab.send(cdp.input_.dispatch_key_event("keyUp", code="Enter", key="Enter", text="\r", windows_virtual_key_code=13))
                            is_verifyCode_editing = False
                            is_form_submitted = True
                            # Short submit-in-progress guard: stop the main loop from
                            # re-clicking the captcha input while navigation is pending.
                            _state["captcha_submit_until"] = time.time() + 1.5
                        else:
                            debug.log(f"[TIXCRAFT CAPTCHA] Form not ready - Ticket:{form_ready.get('ticket')} Select:{form_ready.get('ticket_select')} Captcha:{form_ready.get('verify')} Agreement:{form_ready.get('agree')}")
                    else:
                        # 選取輸入框內容並顯示提示
                        await tab.evaluate('''
                            document.getElementById("TicketForm_verifyCode").select();
                        ''')
                        # 顯示提示訊息
                        await nodriver_tixcraft_toast(tab, f"※ 按 Enter 如果答案是: {answer}")

                except Exception as exc:
                    debug.log(f"[TIXCRAFT CAPTCHA] Failed to input captcha: {exc}")

    return is_verifyCode_editing, is_form_submitted


async def nodriver_tixcraft_toast(tab, message):
    """顯示提示訊息"""
    try:
        await tab.evaluate(f'''
            (function() {{
                const toast = document.querySelector('p.remark-word');
                if (toast) {{
                    toast.innerHTML = '{message}';
                }}
            }})();
        ''')
    except Exception as exc:
        pass


async def nodriver_get_yii_captcha_hash(tab):
    """Read Yii2 captcha hash1 from body data (stored after refresh).
    Returns hash1 (int) or 0 if not yet available (first page load)."""
    try:
        result = await tab.evaluate('''
            (function() {
                if (typeof jQuery === "undefined") return 0;
                var data = jQuery("body").data("yiiCaptcha/ticket/captcha");
                return (data && data[0]) ? data[0] : 0;
            })()
        ''')
        return int(result) if result else 0
    except Exception:
        return 0


async def nodriver_tixcraft_reload_captcha(tab, domain_name, config_dict=None):
    """重新載入驗證碼（Yii2 jQuery refresh）並等待圖片更新。
    Yii2 refresh 後自動將 hash 存入 body data，供 nodriver_get_yii_captcha_hash 讀取。"""
    try:
        result = await tab.evaluate('''
            (async function() {
                if (typeof jQuery === "undefined") return false;
                var $img = jQuery("#TicketForm_verifyCode-image");
                if (!$img.length || typeof $img.yiiCaptcha !== "function") return false;
                var oldSrc = $img.attr("src") || "";
                $img.yiiCaptcha("refresh");
                // Wait up to 2s for src to change
                for (var i = 0; i < 20; i++) {
                    await new Promise(function(r) { setTimeout(r, 100); });
                    if (($img.attr("src") || "") !== oldSrc) break;
                }
                return true;
            })()
        ''', await_promise=True)
        return bool(result)
    except Exception as exc:
        debug = util.create_debug_logger(config_dict)
        debug.log(f"[TIXCRAFT OCR] reload_captcha failed: {exc}")
    return False


async def nodriver_tixcraft_get_ocr_answer(tab, ocr, ocr_captcha_image_source, Captcha_Browser, domain_name):
    """取得驗證碼圖片並進行 OCR 識別"""
    debug = util.create_debug_logger(enabled=False)  # OCR: intentionally silent

    ocr_answer = None
    if not ocr is None:
        img_base64 = None

        if ocr_captcha_image_source == CONST_OCR_CAPTCH_IMAGE_SOURCE_NON_BROWSER:
            if not Captcha_Browser is None:
                img_base64 = base64.b64decode(Captcha_Browser.request_captcha())

        if ocr_captcha_image_source == CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS:
            image_id = 'TicketForm_verifyCode-image'
            if 'indievox.com' in domain_name:
                image_id = 'TicketForm_verifyCode-image'

            try:
                # Stage 7: get captcha image via canvas
                # async IIFE waits for image load to avoid reading stale image after reload
                form_verifyCode_base64 = await tab.evaluate(f'''
                    (async function() {{
                        var img = document.getElementById('{image_id}');
                        if(!img || !img.src) return null;

                        if(img.naturalWidth === 0 || !img.complete) {{
                            await new Promise(function(resolve) {{
                                var timer = setTimeout(resolve, 3000);
                                img.onload = function() {{ clearTimeout(timer); resolve(); }};
                                img.onerror = function() {{ clearTimeout(timer); resolve(); }};
                            }});
                        }}

                        if(img.naturalWidth === 0 || img.naturalHeight === 0) return null;

                        var canvas = document.createElement('canvas');
                        var context = canvas.getContext('2d');
                        canvas.height = img.naturalHeight;
                        canvas.width = img.naturalWidth;
                        context.drawImage(img, 0, 0);
                        return canvas.toDataURL();
                    }})();
                ''', await_promise=True)

                if form_verifyCode_base64:
                    img_base64 = base64.b64decode(form_verifyCode_base64.split(',')[1])

                if img_base64 is None:
                    if not Captcha_Browser is None:
                        debug.log("[TIXCRAFT OCR] Failed to get image from canvas, using fallback: NonBrowser")
                        img_base64 = base64.b64decode(Captcha_Browser.request_captcha())

            except Exception as exc:
                debug.log("[TIXCRAFT OCR] Canvas error:", str(exc))

        # OCR 識別
        if not img_base64 is None:
            try:
                ocr_answer = await ocr_classification(ocr, img_base64)
            except Exception as exc:
                debug.log("[TIXCRAFT OCR] Classification error:", str(exc))

    return ocr_answer


async def nodriver_tixcraft_auto_ocr(tab, config_dict, ocr, away_from_keyboard_enable,
                                     previous_answer, Captcha_Browser,
                                     ocr_captcha_image_source, domain_name):
    """OCR 自動識別主邏輯"""
    debug = util.create_debug_logger(config_dict)

    is_need_redo_ocr = False
    is_form_submitted = False

    is_input_box_exist = False
    if not ocr is None:
        try:
            # query_selector returns None when absent rather than raising, so
            # the flag must test the result: events with no image captcha
            # would otherwise burn the whole retry budget.
            form_verifyCode = await tab.query_selector('#TicketForm_verifyCode')
            is_input_box_exist = form_verifyCode is not None
        except Exception as exc:
            pass
    else:
        debug.log("[TIXCRAFT OCR] ddddocr component unavailable, you may be running on ARM")

    if is_input_box_exist:
        debug.log("[TIXCRAFT OCR] away_from_keyboard_enable:", away_from_keyboard_enable)
        debug.log("[TIXCRAFT OCR] previous_answer:", previous_answer)
        debug.log("[TIXCRAFT OCR] ocr_captcha_image_source:", ocr_captcha_image_source)

        ocr_start_time = time.time()
        ocr_answer = await nodriver_tixcraft_get_ocr_answer(tab, ocr, ocr_captcha_image_source, Captcha_Browser, domain_name)
        ocr_done_time = time.time()
        ocr_elapsed_time = ocr_done_time - ocr_start_time
        debug.log("[TIXCRAFT OCR] Processing time:", "{:.3f}".format(ocr_elapsed_time))

        if ocr_answer is None:
            if away_from_keyboard_enable:
                # 頁面尚未準備好，重試
                # PS: 通常發生在非同步腳本取得驗證碼圖片時
                is_need_redo_ocr = True
                await asyncio.sleep(0.1)
            else:
                await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
        else:
            ocr_answer = ocr_answer.strip()
            debug.log("[TIXCRAFT OCR] Result:", ocr_answer)
            if len(ocr_answer) == 4:
                # Yii2 hash pre-validation (hash available after first reload, 0 on first load)
                if away_from_keyboard_enable:
                    hash1 = await nodriver_get_yii_captcha_hash(tab)
                    if hash1 > 0:
                        if not util.yii_captcha_verify(ocr_answer, hash1):
                            candidates = util.yii_captcha_edit1(ocr_answer, hash1)
                            if candidates:
                                ocr_answer = candidates[0]
                                debug.log(f"[TIXCRAFT OCR] Hash edit1 corrected to: {ocr_answer}")
                            else:
                                debug.log(f"[TIXCRAFT OCR] Hash mismatch, no edit1 solution, reloading")
                                is_need_redo_ocr = True
                                await nodriver_tixcraft_reload_captcha(tab, domain_name)
                                ocr_answer = None
                        else:
                            debug.log(f"[TIXCRAFT OCR] Hash verified ok: {ocr_answer}")
                if ocr_answer is not None:
                    who_care_var, is_form_submitted = await nodriver_tixcraft_keyin_captcha_code(tab, answer=ocr_answer, auto_submit=away_from_keyboard_enable, config_dict=config_dict)
            else:
                if not away_from_keyboard_enable:
                    await nodriver_tixcraft_keyin_captcha_code(tab, config_dict=config_dict)
                else:
                    is_need_redo_ocr = True
                    if previous_answer != ocr_answer:
                        previous_answer = ocr_answer
                        debug.log("[TIXCRAFT OCR] Reloading captcha")

                        await nodriver_tixcraft_reload_captcha(tab, domain_name)

                        if ocr_captcha_image_source == CONST_OCR_CAPTCH_IMAGE_SOURCE_CANVAS:
                            await asyncio.sleep(0.3)
    else:
        debug.log("[TIXCRAFT OCR] Input box not found, exiting OCR...")

    return is_need_redo_ocr, previous_answer, is_form_submitted
