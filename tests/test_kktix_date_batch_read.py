"""KKTIX date handler tests (issue #84).

Characterization (passes before and after the change): the handler
extracts each session li's date text (span.timezoneSuffix first,
.event-info > a > p as fallback) and buy-button presence, lets the
shared date-selection core pick, and clicks the chosen session's
button; a session without a button is unavailable. A misaligned
batch result falls back to exactly this per-row path.

New behaviour (fails on the old handler): the rows come from one
batch evaluate returning {dateText, hasButton} per li, the payload
is the li element, and the buy button is resolved once -- only for
the chosen li, after selection.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import kktix  # noqa: E402

from fake_tab import FakeElement, FakeTab  # noqa: E402

LIST_SELECTOR = "div.event-list ul.clearfix > li"
SPAN_SELECTOR = "span.timezoneSuffix"
FALLBACK_SELECTOR = ".event-info > a > p"
BUTTON_SELECTOR = "div.content > a.btn-point"


def make_config(date_keyword=""):
    return {
        "date_auto_select": {
            "enable": True,
            "mode": "random",
            "date_keyword": date_keyword,
            "auto_select_fallback": False,
        },
        "keyword_exclude": "",
        "advanced": {"verbose": False},
    }


def make_session(date_text=None, fallback_text=None, with_button=True):
    li = FakeElement()
    if date_text is not None:
        li.register_child(SPAN_SELECTOR, FakeElement(html=date_text))
    if fallback_text is not None:
        li.register_child(FALLBACK_SELECTOR, FakeElement(html=fallback_text))
    button = None
    if with_button:
        button = FakeElement()
        li.register_child(BUTTON_SELECTOR, button)
    return li, button


class KktixDateCharacterizationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        kktix._state.clear()

    async def call_handler(self, tab, config):
        return await kktix.nodriver_kktix_date_auto_select(tab, config)

    async def test_clicks_keyword_matched_session(self):
        li1, btn1 = make_session(date_text="2026/01/01")
        li2, btn2 = make_session(date_text="2026/01/02")
        tab = FakeTab(url="https://kktix.com/events/x")
        tab.register_elements(LIST_SELECTOR, [li1, li2])

        result = await self.call_handler(tab, make_config('"2026/01/02"'))

        self.assertTrue(result)
        self.assertEqual(btn2.click_calls, 1)
        self.assertEqual(btn1.click_calls, 0)

    async def test_session_without_button_is_unavailable(self):
        li1, _ = make_session(date_text="2026/01/01", with_button=False)
        li2, btn2 = make_session(date_text="2026/01/02")
        tab = FakeTab(url="https://kktix.com/events/x")
        tab.register_elements(LIST_SELECTOR, [li1, li2])

        result = await self.call_handler(tab, make_config())

        self.assertTrue(result)
        self.assertEqual(btn2.click_calls, 1)

    async def test_fallback_date_source(self):
        li, btn = make_session(fallback_text="2026/01/03")
        tab = FakeTab(url="https://kktix.com/events/x")
        tab.register_elements(LIST_SELECTOR, [li])

        result = await self.call_handler(tab, make_config('"2026/01/03"'))

        self.assertTrue(result)
        self.assertEqual(btn.click_calls, 1)

    async def test_misaligned_batch_falls_back_to_per_row(self):
        li1, btn1 = make_session(date_text="2026/01/01")
        li2, btn2 = make_session(date_text="2026/01/02")
        tab = FakeTab(url="https://kktix.com/events/x")
        tab.register_elements(LIST_SELECTOR, [li1, li2])
        # One batch row for two sessions: misaligned, must be ignored.
        tab.add_evaluate_response(
            "timezoneSuffix", [{"dateText": "2026/01/01", "hasButton": True}])

        result = await self.call_handler(tab, make_config('"2026/01/02"'))

        self.assertTrue(result)
        self.assertEqual(btn2.click_calls, 1)
        self.assertEqual(btn1.click_calls, 0)


class KktixDateBatchReadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        kktix._state.clear()

    async def test_batch_rows_and_button_resolved_once(self):
        # The lis carry no date-text children: their texts can only
        # come from the batch evaluate. Buttons are registered so the
        # chosen li can resolve its own after selection.
        lis_and_btns = [make_session() for _ in range(3)]
        lis = [item[0] for item in lis_and_btns]
        btns = [item[1] for item in lis_and_btns]
        tab = FakeTab(url="https://kktix.com/events/x")
        tab.register_elements(LIST_SELECTOR, lis)
        tab.add_evaluate_response("timezoneSuffix", [
            {"dateText": "2026/01/01", "hasButton": True},
            {"dateText": "2026/01/02", "hasButton": True},
            {"dateText": "2026/01/03", "hasButton": False},
        ])

        result = await kktix.nodriver_kktix_date_auto_select(
            tab, make_config('"2026/01/02"'))

        self.assertTrue(result)
        self.assertEqual(btns[1].click_calls, 1)
        self.assertEqual(btns[0].click_calls, 0)
        self.assertEqual(btns[2].click_calls, 0)
        # No per-li text extraction happened...
        for li in lis:
            self.assertNotIn(SPAN_SELECTOR, li.query_calls)
            self.assertNotIn(FALLBACK_SELECTOR, li.query_calls)
        # ...and the button was resolved only on the chosen li, once.
        self.assertEqual(lis[1].query_calls.count(BUTTON_SELECTOR), 1)
        self.assertNotIn(BUTTON_SELECTOR, lis[0].query_calls)
        self.assertNotIn(BUTTON_SELECTOR, lis[2].query_calls)


if __name__ == "__main__":
    unittest.main()
