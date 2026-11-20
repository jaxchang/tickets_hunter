"""Characterization tests for the TicketPlus date handler (issue #68).

The handler has two selection paths (ADR 0007): a Vue data-layer path
(Python-side matching over ``__vue__.$data.sessions``) and a DOM/JS
path (matching inside an evaluated script). These tests pin the
current behaviour of the Vue path and of the DOM path's Python-side
candidate computation (``allowed_indexes``), using the fake-tab seam:

- The front segment reads session rows via query_selector_all; a row
  counts as enabled when its HTML carries the buy text.
- The Vue path is driven by stubbing the ``.eventClass`` evaluate.
- The DOM path's collect evaluate is stubbed the same way; the click
  script is generated in Python, so the ``allowedIndexes`` it embeds
  is asserted from the recorded evaluate calls, and a canned click
  result stands in for the JS-side matching (not unit-tested, as with
  other JS-heavy handlers).

Two known defects are deliberately NOT pinned here (ADR 0007): the
Vue path ignores ``date_auto_select.mode``, and a honeypot session
blocked in the Vue path can still be reached by the DOM path. The
honeypot test below pins only the Vue-level guarantee (never navigate
to a fake session id), which holds before and after the fix.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import ticketplus  # noqa: E402

from fake_tab import FakeElement, FakeTab  # noqa: E402

ACTIVITY_URL = "https://ticketplus.com.tw/activity/EVT123"
ORDER_URL = "https://ticketplus.com.tw/order/EVT123/"
FAKE_SESSION_ID = "c18900a1d5f295218fe60b982d7ece96"
ROW_SELECTOR = "div#buyTicket div.sesstion-item"
ENABLED_ROW = "<div>場次列<button>>立即購</button></div>"


def make_config(keyword="", mode="from top to bottom", fallback=False,
                exclude="", pass_sold_out=False):
    return {
        "date_auto_select": {"enable": True, "mode": mode, "date_keyword": keyword},
        "date_auto_fallback": fallback,
        "keyword_exclude": exclude,
        "tixcraft": {
            "pass_date_is_sold_out": pass_sold_out,
            "auto_reload_coming_soon_page": False,
        },
        "advanced": {"verbose": False},
    }


def session(session_id, date, name):
    return {"sessionId": session_id, "date": date, "name": name}


def make_tab(rows=(ENABLED_ROW,), vue=None, collected=None, click=None):
    tab = FakeTab(url=ACTIVITY_URL)
    tab.register_elements(ROW_SELECTOR, [FakeElement(html=h) for h in rows])
    if vue is not None:
        tab.add_evaluate_response(".eventClass", vue)
    if collected is not None:
        tab.add_evaluate_response("sessionContainers.map", collected)
    if click is not None:
        tab.add_evaluate_response("Starting date selection", click)
    return tab


def click_scripts(tab):
    return [s for s in tab.evaluate_calls if "allowedIndexes" in s]


class TicketPlusVuePathTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ticketplus._state.clear()

    async def test_session_major_first_match_wins(self):
        # Keyword groups are ["Beta場", "Alpha場"], but matching is
        # session-major: the first session matching ANY group wins,
        # even though its group comes second.
        tab = make_tab(vue={"ready": True, "sessions": [
            session("sA", "2026/01/02", "Alpha場"),
            session("sB", "2026/01/03", "Beta場"),
        ]})
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"Beta場","Alpha場"'))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "sA"])

    async def test_space_split_and_within_group(self):
        # One group "01/03 晚場" is an AND: a session must contain
        # both tokens in its "date name" text.
        tab = make_tab(vue={"ready": True, "sessions": [
            session("s1", "2026/01/03", "午場"),
            session("s2", "2026/01/03", "晚場"),
        ]})
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"01/03 晚場"'))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s2"])

    async def test_unquoted_keyword_falls_back_to_single_group(self):
        # A keyword that is not valid JSON-list syntax is treated as
        # one whole group instead of failing.
        tab = make_tab(vue={"ready": True, "sessions": [
            session("s1", "2026/01/02", "午場"),
            session("s2", "2026/01/03", "晚場"),
        ]})
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword="01/03"))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s2"])

    async def test_exclude_applies_before_fallback(self):
        # Empty keyword + fallback picks the first session -- but the
        # excluded session is removed first, so fallback lands on s2.
        tab = make_tab(vue={"ready": True, "sessions": [
            session("s1", "2026/01/02", "午場"),
            session("s2", "2026/01/03", "晚場"),
        ]})
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword="", fallback=True, exclude='"午場"'))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s2"])

    async def test_honeypot_session_is_never_navigated(self):
        # The only session is the known fake id; even as the fallback
        # pick, the Vue path must not navigate to it.
        tab = make_tab(vue={"ready": True, "sessions": [
            session(FAKE_SESSION_ID, "2026/01/02", "午場"),
        ]})
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword="", fallback=True))
        self.assertFalse(result)
        self.assertEqual(tab.get_calls, [])

    async def test_honeypot_does_not_block_a_real_match(self):
        tab = make_tab(vue={"ready": True, "sessions": [
            session(FAKE_SESSION_ID, "2026/01/02", "午場"),
            session("s2", "2026/01/03", "晚場"),
        ]})
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"晚場"'))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s2"])

    async def test_vue_candidates_come_from_vue_data_not_row_availability(self):
        # Per-system divergence pinned by ADR 0007: the Vue path's
        # candidate set is the Vue session list; the front segment's
        # per-row sold-out filtering only gates whether the paths run
        # at all (one enabled row is enough), it does not remove Vue
        # sessions from consideration.
        sold_out_row = "<div>2026/01/03 晚場 銷售一空<button>>立即購</button></div>"
        tab = make_tab(
            rows=(ENABLED_ROW, sold_out_row),
            vue={"ready": True, "sessions": [
                session("s2", "2026/01/03", "晚場"),
            ]},
        )
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"晚場"', pass_sold_out=True))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s2"])


class TicketPlusFrontSegmentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ticketplus._state.clear()

    async def test_loading_placeholder_marks_vue_not_ready(self):
        row = ('<div><div class="v-progress-circular__info"></div>'
               "<button>>立即購</button></div>")
        tab = make_tab(rows=(row,))
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"晚場"'))
        self.assertFalse(result)
        self.assertEqual(tab.evaluate_calls, [])

    async def test_all_rows_sold_out_gates_both_paths(self):
        row = "<div>2026/01/02 午場 銷售一空<button>>立即購</button></div>"
        tab = make_tab(rows=(row,))
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"午場"', pass_sold_out=True))
        self.assertFalse(result)
        self.assertEqual(tab.evaluate_calls, [])


class TicketPlusDomPathTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ticketplus._state.clear()

    async def test_no_vue_match_falls_through_to_dom_click(self):
        tab = make_tab(
            vue={"ready": True, "sessions": [
                session("s1", "2026/01/02", "午場"),
            ]},
            collected=[
                {"index": 0, "text": "2026/01/02 午場"},
                {"index": 1, "text": "2026/01/03 晚場"},
            ],
            click={"success": True, "action": "button_clicked", "targetText": "x"},
        )
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"不存在"'))
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [])
        scripts = click_scripts(tab)
        self.assertEqual(len(scripts), 1)
        self.assertIn("allowedIndexes = [0, 1]", scripts[0])

    async def test_dom_allowed_indexes_reflect_exclude(self):
        tab = make_tab(
            vue={"ready": False},
            collected=[
                {"index": 0, "text": "2026/01/02 午場"},
                {"index": 1, "text": "2026/01/03 晚場"},
                {"index": 2, "text": "2026/01/04 午場"},
            ],
            click={"success": True, "action": "button_clicked", "targetText": "x"},
        )
        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config(keyword='"午場"', exclude='"晚場"'))
        self.assertTrue(result)
        scripts = click_scripts(tab)
        self.assertEqual(len(scripts), 1)
        self.assertIn("allowedIndexes = [0, 2]", scripts[0])


if __name__ == "__main__":
    unittest.main()
