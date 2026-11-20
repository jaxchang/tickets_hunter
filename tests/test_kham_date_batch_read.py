"""Kham date handler tests (issue #82).

Characterization (passes before and after the change): the handler
extracts rows from per-row HTML, applies kham's availability filters
(disabled marker, button text, keyword exclude), lets the shared
date-selection core pick, and clicks the chosen row's button; a
coming-soon row is never clicked.

New behaviour (fails on the old handler): row HTML comes from one
batch evaluate with a per-row get_html fallback, and the fixed
tab.sleep(0.6) is replaced by a smart poll (same 0.6s cap) that
stops as soon as rows appear.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import kham, kham_shared  # noqa: E402

from fake_tab import FakeElement, FakeTab  # noqa: E402

SELECTOR = "table.eventTABLE > tbody > tr"
DOMAIN = "kham.com.tw"


def row_html(date_text, button_text="立即訂購", disabled=False):
    disabled_attr = ' disabled"' if disabled else '"'
    return (
        f"<tr><td>{date_text}</td><td>台北</td><td>800</td>"
        f"<td><button class=\"buy{disabled_attr}>{button_text}</button></td></tr>"
    )


def make_row(html):
    row = FakeElement(html=html)
    button = FakeElement()
    row.register_child("button", button)
    return row, button


def make_config(**overrides):
    config = {
        "date_auto_select": {
            "enable": True,
            "mode": "random",
            "date_keyword": "",
            "auto_select_fallback": False,
        },
        "tixcraft": {"auto_reload_coming_soon_page": False},
        "keyword_exclude": "",
        "advanced": {"verbose": False},
    }
    config.update(overrides)
    return config


class KhamDateCharacterizationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        kham_shared._state.clear()

    async def call_handler(self, tab, config):
        return await kham.nodriver_kham_date_auto_select(tab, DOMAIN, config)

    async def test_clicks_keyword_matched_available_row(self):
        disabled_row, disabled_btn = make_row(
            row_html("2026/01/01", disabled=True))
        target_row, target_btn = make_row(row_html("2026/01/02"))
        tab = FakeTab(url="https://kham.com.tw/event")
        tab.register_elements(SELECTOR, [disabled_row, target_row])
        config = make_config()
        config["date_auto_select"]["date_keyword"] = '"2026/01/02"'

        result = await self.call_handler(tab, config)

        self.assertTrue(result)
        self.assertEqual(target_btn.click_calls, 1)
        self.assertEqual(disabled_btn.click_calls, 0)

    async def test_coming_soon_row_is_not_clicked(self):
        row, btn = make_row(row_html("2026/01/02", button_text="尚未開賣"))
        tab = FakeTab(url="https://kham.com.tw/event")
        tab.register_elements(SELECTOR, [row])

        result = await self.call_handler(tab, make_config())

        self.assertFalse(result)
        self.assertEqual(btn.click_calls, 0)

    async def test_excluded_row_is_skipped(self):
        excluded_row, excluded_btn = make_row(row_html("2026/01/01"))
        target_row, target_btn = make_row(row_html("2026/01/02"))
        tab = FakeTab(url="https://kham.com.tw/event")
        tab.register_elements(SELECTOR, [excluded_row, target_row])
        config = make_config(keyword_exclude='"2026/01/01"')

        result = await self.call_handler(tab, config)

        self.assertTrue(result)
        self.assertEqual(target_btn.click_calls, 1)
        self.assertEqual(excluded_btn.click_calls, 0)


class KhamDateBatchReadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        kham_shared._state.clear()

    async def test_batch_evaluate_supplies_row_html(self):
        # The row elements carry no HTML of their own: selection can
        # only work if the handler uses the batch evaluate result.
        html1 = row_html("2026/01/01", disabled=True)
        html2 = row_html("2026/01/02")
        row1, btn1 = make_row("")
        row2, btn2 = make_row("")
        tab = FakeTab(url="https://kham.com.tw/event")
        tab.register_elements(SELECTOR, [row1, row2])
        tab.add_evaluate_response("outerHTML", [html1, html2])
        config = make_config()
        config["date_auto_select"]["date_keyword"] = '"2026/01/02"'

        result = await kham.nodriver_kham_date_auto_select(
            tab, DOMAIN, config)

        self.assertTrue(result)
        self.assertEqual(btn2.click_calls, 1)
        self.assertEqual(btn1.click_calls, 0)

    async def test_poll_waits_for_rows_without_fixed_sleep(self):
        row, btn = make_row(row_html("2026/01/02"))
        tab = FakeTab(url="https://kham.com.tw/event")
        tab.queue_elements_all(SELECTOR, [[], [], [row]])
        tab.add_evaluate_response("outerHTML", [row_html("2026/01/02")])

        result = await kham.nodriver_kham_date_auto_select(
            tab, DOMAIN, make_config())

        self.assertTrue(result)
        self.assertEqual(btn.click_calls, 1)
        # The old handler unconditionally recorded a 0.6s tab.sleep.
        self.assertNotIn(0.6, tab.sleep_calls)


if __name__ == "__main__":
    unittest.main()
