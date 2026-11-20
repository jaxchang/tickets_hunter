"""TicketPlus date handler: pause-aware wait + batch rows (issue #86).

The date-row wait used to be the only poll in ticketplus.py on raw
tab.sleep, so it ignored the pause key; it now goes through
sleep_with_pause_check like the module's other polls (same 3s cap),
and a pause aborts the pass. Row extraction used to pay one
get_html() round-trip per row; it now batch-fetches the rows'
outerHTML in one evaluate with a per-row fallback (the kham
precedent). The existing characterization suite covers the per-row
path, which is exactly the fallback path kept here.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import ticketplus  # noqa: E402

from fake_tab import FakeElement, FakeTab  # noqa: E402

ACTIVITY_URL = "https://ticketplus.com.tw/activity/EVT123"
ORDER_URL = "https://ticketplus.com.tw/order/EVT123/"
ROW_SELECTOR = "div#buyTicket div.sesstion-item"
ENABLED_ROW = "<div>場次列<button>>立即購</button></div>"
DISABLED_ROW = "<div>場次列（無購票鈕）</div>"


def make_config():
    return {
        "date_auto_select": {"enable": True, "mode": "from top to bottom",
                             "date_keyword": ""},
        "date_auto_fallback": True,
        "keyword_exclude": "",
        "tixcraft": {
            "pass_date_is_sold_out": False,
            "auto_reload_coming_soon_page": False,
        },
        "advanced": {"verbose": False},
    }


def vue_stub():
    return {"ready": True, "sessions": [
        {"sessionId": "s1", "date": "2026/01/02", "name": "場次一"},
    ]}


class TicketPlusDateWaitTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ticketplus._state.clear()

    async def test_wait_polls_until_rows_appear(self):
        # Characterization: rows rendering late (3rd poll) are still
        # picked up and the pass proceeds to the Vue navigation.
        tab = FakeTab(url=ACTIVITY_URL)
        tab.queue_elements_all(
            ROW_SELECTOR, [[], [], [FakeElement(html=ENABLED_ROW)]])
        tab.add_evaluate_response(".eventClass", vue_stub())

        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config())

        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s1"])

    async def test_pause_aborts_the_wait(self):
        tab = FakeTab(url=ACTIVITY_URL)
        tab.queue_elements_all(
            ROW_SELECTOR, [[], [], [FakeElement(html=ENABLED_ROW)]])
        tab.add_evaluate_response(".eventClass", vue_stub())
        pause_calls = []

        async def fake_pause_sleep(tab_, seconds, config_dict=None):
            pause_calls.append(seconds)
            return True  # paused

        with mock.patch.object(
                ticketplus, "sleep_with_pause_check", fake_pause_sleep):
            result = await ticketplus.nodriver_ticketplus_date_auto_select(
                tab, make_config())

        self.assertFalse(result)
        # Aborted at the first pause check: no navigation, and the
        # raw tab.sleep polling of the old code never ran.
        self.assertEqual(len(pause_calls), 1)
        self.assertEqual(tab.get_calls, [])
        self.assertEqual(tab.sleep_calls, [])


class TicketPlusDateBatchReadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ticketplus._state.clear()

    async def test_batch_evaluate_supplies_row_html(self):
        # The row elements carry no HTML of their own: extraction can
        # only succeed via the batch evaluate.
        tab = FakeTab(url=ACTIVITY_URL)
        tab.register_elements(
            ROW_SELECTOR, [FakeElement(html=""), FakeElement(html="")])
        tab.add_evaluate_response("outerHTML", [ENABLED_ROW, ENABLED_ROW])
        tab.add_evaluate_response(".eventClass", vue_stub())

        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config())

        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s1"])

    async def test_missing_batch_entry_falls_back_to_get_html(self):
        # Batch covers only the first row (which is not enabled); the
        # second row's HTML must come from its own get_html().
        tab = FakeTab(url=ACTIVITY_URL)
        tab.register_elements(ROW_SELECTOR, [
            FakeElement(html=""),
            FakeElement(html=ENABLED_ROW),
        ])
        tab.add_evaluate_response("outerHTML", [DISABLED_ROW])
        tab.add_evaluate_response(".eventClass", vue_stub())

        result = await ticketplus.nodriver_ticketplus_date_auto_select(
            tab, make_config())

        self.assertTrue(result)
        self.assertEqual(tab.get_calls, [ORDER_URL + "s1"])


if __name__ == "__main__":
    unittest.main()
