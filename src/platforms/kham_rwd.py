#encoding=utf-8
# =============================================================================
# KHAM UTK RWD Module
# The ASP.NET Core rewrite tenants of the kham UTK system (issue #40,
# ADR 0006), split out of platforms/kham.py. Imports only the shared
# layer (platforms.kham_shared) and the common layers.
# =============================================================================

import base64
import json
import re
import time
import urllib.parse
from zendriver import cdp
import util
import config_views
from nodriver_common import (
    CONST_FROM_TOP_TO_BOTTOM,
)
from platforms.kham_shared import (
    _state,
    _utk_rwd_wait_for,
    get_utk_rwd_tenant,
    is_utk_rwd_url,
)


# UTK RWD sites: 中職售票 (中信兄弟 / 味全龍 / 富邦悍將) and the basketball teams on the
# same system (中信特攻 under tix.ctbcsports.com/DEA, 臺北富邦勇士)
# The ASP.NET Core rewrite of the kham UTK system. Pages keep the kham ids
# (#CHK, setType, table#TBL, addShoppingCart, #dialog-message) but drop the
# /application/utkNN/ prefix and .aspx (/UTK0201_?PRODUCT_ID=), may sit under a
# virtual directory (/BROTHERS/UTK0201_), and log in through a popup that every
# page carries (#MASTER_ACCOUNT, #MASTER_PASSWORD, #MASTER_CHK, doMasterLogin()).
# ====================================================================================


CONST_UTK_RWD_PAGE_PATTERN = re.compile(r"UTK(\d{2})(\d{2})_(\w*)", re.IGNORECASE)


CONST_UTK_RWD_HOME_PAGE = "utk0101_"


# 味全龍's cart is /UTK0206_02, the other two /UTK0206_: both are the checkout.
CONST_UTK_RWD_CART_PAGE = "utk0206_"


# Pages that add to cart; a guest there only gets "【帳號】不為空值！" back.
CONST_UTK_RWD_ORDER_PAGES = ("/utk0205_.aspx", "/utk0202_.aspx", "/utk0201_001.aspx")


# The popup copy lists every performance; the inline table stops at four.
CONST_UTK_RWD_DATE_ROW_SELECTORS = (
    "#PerformanceListTable_popup table.ss tr",
    "#PerformanceListTable table.ss tr",
)


CONST_UTK_RWD_AREA_ROW_SELECTOR = "table.f1 > tbody > tr.saleTr"


# Wrong OCR reads are expected, but a wrong password must not lock the account.
CONST_UTK_RWD_LOGIN_MAX_TRIES = 5


CONST_UTK_RWD_LOGIN_RETRY_SEC = 3.0


def normalize_utk_rwd_url(url):
    """Rewrite an RWD URL into the kham .aspx layout, for page-type checks only.

    https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=X
      -> https://tix.ctbcsports.com/application/utk02/utk0201_.aspx?PRODUCT_ID=X
    so the kham checks ('utk0201_.aspx?product_id=', len(url.split('/')) == 6,
    url.split('/')[5]) read RWD pages as they are. Never navigate to the
    result: the RWD sites serve no such path. Other URLs come back untouched.
    """
    if not is_utk_rwd_url(url):
        return url
    parts = urllib.parse.urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    page = segments[-1] if segments else ""
    match = CONST_UTK_RWD_PAGE_PATTERN.fullmatch(page)
    if match:
        group = match.group(1)
        name = f"utk{group}{match.group(2)}_"
        if name != CONST_UTK_RWD_CART_PAGE:
            name += match.group(3).lower()
    elif not segments or (len(segments) == 1 and page.isalpha()):
        # Site root or a bare virtual directory (/BROTHERS): both serve the home page.
        group, name = "01", CONST_UTK_RWD_HOME_PAGE
    else:
        return url  # /PerformanceListControl, /Home/pic, legacy .aspx paths
    # A '/' in the query (STARTDATE=2026/10/17) would shift the split('/') indexes.
    query = parts.query.replace("/", "%2F")
    normalized = f"{parts.scheme}://{parts.netloc}/application/utk{group}/{name}.aspx"
    return normalized + (f"?{query}" if query else "")


def parse_cookie_string(raw):
    """Split a pasted cookie string ("a=1; b=2") into (name, value) pairs.

    Takes a leading "Cookie:" and newlines between pairs; a value keeps any '='
    it contains; a repeated name keeps its last value.
    """
    text = (raw or "").strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    pairs = {}
    for chunk in re.split(r"[;\r\n]+", text):
        name, sep, value = chunk.partition("=")
        name = name.strip()
        if sep and name:
            pairs[name] = value.strip()
    return list(pairs.items())


def filter_utk_rwd_cookies(pairs, url):
    """Keep the pairs named with the current site's cookie prefix only."""
    tenant = get_utk_rwd_tenant(url)
    if tenant is None:
        return []
    prefix = tenant["cookie_prefix"]
    return [(name, value) for name, value in pairs if name.startswith(prefix)]


def _pick_utk_rwd_amount_row(rows, config_dict, match_area_keyword=True):
    """The UTK0202 ticket row to buy, or None when none fits.

    rows: [{"key", "area", "type", "left"}] for the rows still on sale. Rows hit
    by keyword_exclude, or with fewer seats left than ticket_number, drop out.
    With match_area_keyword the area keywords are tried in order (space = AND)
    and a miss falls back to area_auto_select.mode only if area_auto_fallback.
    """
    ticket_number = int(config_views.ticket_number(config_dict, default=1))
    candidates = []
    for row in rows or []:
        text = f"{row.get('area', '')} {row.get('type', '')}".strip()
        if not row.get("key") or util.reset_row_text_if_match_keyword_exclude(config_dict, text):
            continue
        left = str(row.get("left", "")).strip()
        if left.isdigit() and int(left) < ticket_number:
            continue
        candidates.append((row, util.format_keyword_string(text)))
    if not candidates:
        return None
    area_config = config_views.area_view(config_dict)
    keyword = area_config["keyword"].strip() if match_area_keyword else ""
    if keyword:
        for item in util.parse_keyword_string_to_array(keyword):
            words = [util.format_keyword_string(w) for w in item.split(" ") if w]
            for row, text in candidates:
                if words and all(w in text for w in words):
                    return row
        if not area_config["fallback"]:
            return None
    mode = area_config["mode"]
    return util.get_target_item_from_matched_list([row for row, _ in candidates], mode)


CONST_UTK_RWD_LOGIN_STATE_JS = '''
    (() => (typeof _ul === 'undefined') ? null : String(_ul))()
'''


CONST_UTK_RWD_CLOSE_DIALOG_JS = '''
    (() => {
        const wraps = Array.from(document.querySelectorAll('div.ui-dialog'));
        const open = wraps.find((w) => window.getComputedStyle(w).display !== 'none');
        const btn = open ? open.querySelector('div.ui-dialog-buttonset > button.ui-button') : null;
        if (btn) btn.click();
        return !!btn;
    })()
'''


CONST_UTK_RWD_LOGIN_OPEN_JS = '''
    (() => {
        const btn = document.querySelector('#userbutton');
        if (!btn || !document.querySelector('#MASTER_ACCOUNT')) return false;
        // The site's own "please log in" path: opens #popupuser and loads #master_chk_pic.
        btn.click();
        const img = document.querySelector('#master_chk_pic');
        const refresh = document.querySelector('#master_chang_pic');
        if (img && !img.getAttribute('src') && refresh) refresh.click();
        return true;
    })()
'''


CONST_UTK_RWD_LOGIN_REFRESH_CAPTCHA_JS = '''
    (() => {
        const refresh = document.querySelector('#master_chang_pic');
        if (refresh) refresh.click();
        return !!refresh;
    })()
'''


CONST_UTK_RWD_LOGIN_CAPTCHA_JS = '''
    (() => {
        const img = document.querySelector('#master_chk_pic');
        if (!img || !img.getAttribute('src') || !img.complete || !img.naturalWidth) return null;
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        canvas.getContext('2d').drawImage(img, 0, 0);
        return canvas.toDataURL();
    })()
'''


CONST_UTK_RWD_AMOUNT_ROWS_JS = '''
    (() => Array.from(document.querySelectorAll('input.numbox[type="number"][id="AMOUNT"]')).map((input) => {
        const key = input.getAttribute('key') || '';
        const read = (suffix) => {
            const el = document.getElementById(key + suffix);
            return el ? el.value : '';
        };
        return { key: key, area: read('_PRICE_AREA_NAME'), type: read('_NAME'), left: read('_LAST_AMOUNT') };
    }))()
'''


async def _utk_rwd_login_state(tab):
    """'in' or 'out' from the page's _ul flag ('0' is a guest: the test the
    site's own scripts use); None while the page has not defined it."""
    try:
        flag = await tab.evaluate(CONST_UTK_RWD_LOGIN_STATE_JS)
    except Exception:
        return None
    if flag is None or not isinstance(flag, (str, int)):
        return None
    return "out" if str(flag) == "0" else "in"


def _utk_rwd_note_once(debug, key, message):
    """Log a waiting-state message once instead of on every main-loop pass."""
    notes = _state.setdefault("utk_rwd_notes", set())
    if key not in notes:
        notes.add(key)
        debug.log(message)


async def nodriver_utk_rwd_cookie_login(tab, config_dict, url):
    """Issue #437: log in with accounts.cpbl_cookie on the current RWD site.

    Only cookies named with this site's prefix are set, host-only on this host,
    after deleting same-name cookies; the page reloads once and _ul tells whether
    the session took. Logs carry cookie counts, never cookie values.
    """
    debug = util.create_debug_logger(config_dict)
    raw = config_dict.get("accounts", {}).get("cpbl_cookie", "")
    pairs = filter_utk_rwd_cookies(parse_cookie_string(raw), url)
    host = urllib.parse.urlsplit(url).hostname
    if not pairs:
        debug.log(f"[CPBL COOKIE] 中職售票 Cookie has no cookie for {host}")
        return False
    site_url = f"https://{host}/"
    try:
        for name, value in pairs:
            await tab.send(cdp.network.delete_cookies(name=name, url=site_url))
            await tab.send(cdp.network.set_cookie(
                name=name, value=value, url=site_url, path="/", secure=True,
                http_only=True, same_site=cdp.network.CookieSameSite.LAX))
        debug.log(f"[CPBL COOKIE] Set {len(pairs)} cookie(s) on {host}, reloading")
        await tab.reload()
    except Exception as exc:
        debug.log(f"[CPBL COOKIE] Cookie injection failed: {type(exc).__name__}")
        return False
    # A read right after reload() can still see the old document.
    await tab.sleep(1.0)
    await _utk_rwd_wait_for(tab, CONST_UTK_RWD_LOGIN_STATE_JS, 4.0)
    is_logged_in = await _utk_rwd_login_state(tab) == "in"
    debug.log(f"[CPBL COOKIE] Logged in after cookie: {is_logged_in}")
    return is_logged_in


async def _utk_rwd_read_login_captcha(tab):
    """The popup captcha as PNG bytes, once #master_chk_pic has loaded."""
    for _ in range(10):
        data_url = await tab.evaluate(CONST_UTK_RWD_LOGIN_CAPTCHA_JS)
        if isinstance(data_url, str) and "," in data_url:
            return base64.b64decode(data_url.split(",", 1)[1])
        await tab.sleep(0.2)
    return None


async def nodriver_utk_rwd_login(tab, config_dict, ocr, account, password):
    """Log in through the RWD popup: open it, OCR #master_chk_pic, then call
    doMasterLogin(). Without OCR the popup is left filled for the user.
    Returns True once the login was submitted."""
    debug = util.create_debug_logger(config_dict)
    await tab.evaluate(CONST_UTK_RWD_CLOSE_DIALOG_JS)  # a previous wrong-captcha reply
    if not await tab.evaluate(CONST_UTK_RWD_LOGIN_OPEN_JS):
        debug.log("[CPBL LOGIN] Login popup not found on this page")
        return False
    answer = ""
    if ocr:
        img_bytes = await _utk_rwd_read_login_captcha(tab)
        try:
            answer = (ocr.classification(img_bytes) or "").strip() if img_bytes else ""
        except Exception as exc:
            debug.log(f"[CPBL LOGIN] OCR failed: {exc}")
        if len(answer) != 4:
            debug.log(f"[CPBL LOGIN] Unusable captcha read '{answer}', retrying with a new image")
            # LOGIN_OPEN_JS only loads an image while the popup has none, so ask
            # for the next one here or the retry reads this same image again.
            await tab.evaluate(CONST_UTK_RWD_LOGIN_REFRESH_CAPTCHA_JS)
            return False
    values = {"MASTER_ACCOUNT": account, "MASTER_PASSWORD": password}
    if answer:
        values["MASTER_CHK"] = answer
    submitted = await tab.evaluate(f'''
        (() => {{
            const values = {json.dumps(values)};
            for (const id of Object.keys(values)) {{
                const el = document.getElementById(id);
                if (!el) return false;
                el.value = values[id];
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
            if (!{json.dumps(bool(answer))} || typeof doMasterLogin !== 'function') return false;
            doMasterLogin();
            return true;
        }})()
    ''')
    # The account is a national ID or passport number: never log any part of it.
    debug.log("[CPBL LOGIN] " + (
        "submitted" if submitted is True else "filled; enter the captcha and log in by hand"))
    return submitted is True


async def nodriver_utk_rwd_ensure_login(tab, url, config_dict, ocr):
    """Keep the RWD session logged in. True means: skip this page this pass.

    Order (issue #437): accounts.cpbl_cookie once per site, then the popup with
    accounts.cpbl_account, at most CONST_UTK_RWD_LOGIN_MAX_TRIES submissions
    CONST_UTK_RWD_LOGIN_RETRY_SEC apart. A guest with nothing left to try may
    still browse, but order pages wait for a manual login.
    """
    if await _utk_rwd_login_state(tab) != "out":
        return False
    debug = util.create_debug_logger(config_dict)
    host = urllib.parse.urlsplit(url).hostname
    accounts = config_dict.get("accounts", {})
    cookie_hosts = _state.setdefault("utk_rwd_cookie_hosts", set())
    if accounts.get("cpbl_cookie", "").strip() and host not in cookie_hosts:
        cookie_hosts.add(host)
        await nodriver_utk_rwd_cookie_login(tab, config_dict, url)
        return True
    account = accounts.get("cpbl_account", "").strip()
    tries = _state.setdefault("utk_rwd_login_tries", {})
    count, last = tries.get(host, (0, 0.0))
    if account and count < CONST_UTK_RWD_LOGIN_MAX_TRIES:
        if time.time() - last >= CONST_UTK_RWD_LOGIN_RETRY_SEC:
            submitted = await nodriver_utk_rwd_login(
                tab, config_dict, ocr, account, accounts.get("cpbl_password", "").strip())
            # Without OCR the popup is the user's now: no refills over their typing.
            count = count + 1 if submitted else (count if ocr else CONST_UTK_RWD_LOGIN_MAX_TRIES)
            tries[host] = (count, time.time())
        return True
    if account:
        _utk_rwd_note_once(debug, f"gave_up:{host}", "[CPBL LOGIN] Automatic login stopped; please log in by hand")
    if any(page in url.lower() for page in CONST_UTK_RWD_ORDER_PAGES):
        _utk_rwd_note_once(debug, f"guest:{host}", "[CPBL LOGIN] Order page needs a logged-in member; waiting for login")
        return True
    return False


async def nodriver_utk_rwd_assign_ticket_number(tab, url, config_dict):
    """RWD UTK0202: pick the ticket row and raise its count to ticket_number.

    中信's date list links straight to a UTK0202 that lists every area of the
    performance, each with its own #AMOUNT, so the area keyword is matched here
    (area name + ticket type). A UTK0202 reached from an area click has its area
    fixed already: there only keyword_exclude applies. Sold-out areas render no
    #AMOUNT input, so they never become candidates.
    """
    debug = util.create_debug_logger(config_dict)
    rows = await tab.evaluate(CONST_UTK_RWD_AMOUNT_ROWS_JS)
    rows = rows if isinstance(rows, list) else []
    match_area = "performance_price_area_id=" not in url.lower()
    row = _pick_utk_rwd_amount_row(rows, config_dict, match_area)
    if row is None:
        debug.log(f"[CPBL AMOUNT] No ticket row fits ({len(rows)} on sale)")
        return False
    ticket_number_value = config_views.ticket_number(config_dict)
    value = await tab.evaluate(f'''
        (() => {{
            const key = {json.dumps(row["key"])};
            const target = {int(ticket_number_value)};
            const input = Array.from(document.querySelectorAll('input.numbox[id="AMOUNT"]'))
                .find((el) => el.getAttribute('key') === key);
            if (!input) return null;
            for (let n = parseInt(input.value) || 0; n < target; n++) {{
                if (typeof opera1 === 'function') {{
                    opera1(key, true);
                }} else {{
                    input.value = String(n + 1);
                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}
            return input.value;
        }})()
    ''')
    debug.log(f"[CPBL AMOUNT] {row.get('area', '')} {row.get('type', '')} -> {value}")
    return str(value or "0").isdigit() and int(value or 0) > 0
