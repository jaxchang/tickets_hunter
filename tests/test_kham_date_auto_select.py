"""Characterization tests for the kham date-selection handler (issue #28).

These tests pin the CURRENT behaviour of
``kham.nodriver_kham_date_auto_select`` (kham.com.tw layout) through
the fake-tab seam (``fake_tab.py``): keyword group priority, AND list
groups, keyword exclude, the HTML-side availability filters (disabled
button, all-prices-disabled column, missing button), mode picks,
fallback -- and this handler's signature divergence: an unparsable
keyword string behaves like NO keyword (mode pick over all rows)
instead of a miss, even with fallback off. A selected coming-soon
(尚未開賣) row is not clicked and triggers the reload tail instead.
They were written against the pre-refactor handler and must pass
unchanged after the handler is rewired onto the shared
date-selection core (``src/date_selection.py``, ADR 0003).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import kham  # noqa: E402
from fake_tab import FakeElement, FakeTab  # noqa: E402

ROWS_SELECTOR = "table.eventTABLE > tbody > tr"
DOMAIN = "kham.com.tw"


def make_config(keyword="", mode="from top to bottom", fallback=False,
                exclude="", coming_soon_reload=True):
    return {
        "date_auto_select": {
            "enable": True,
            "mode": mode,
            "date_keyword": keyword,
        },
        "date_auto_fallback": fallback,
        "keyword_exclude": exclude,
        "tixcraft": {"auto_reload_coming_soon_page": coming_soon_reload},
        "advanced": {"verbose": False, "auto_reload_page_interval": 1},
    }


def row_html(date_text, price="1000", button="立即訂購", disabled=False):
    disabled_attr = ' disabled"' if disabled else ""
    return (
        f"<tr><td>{date_text}</td><td>台北</td><td>{price}</td>"
        f"<td><button{disabled_attr}>{button}</button></td></tr>"
    )


def make_row(html):
    row = FakeElement(html=html)
    button = row.register_child("button", FakeElement())
    return row, button


def make_tab(htmls):
    tab = FakeTab()
    buttons = []
    rows = []
    for html in htmls:
        row, button = make_row(html)
        rows.append(row)
        buttons.append(button)
    tab.register_elements(ROWS_SELECTOR, rows)
    return tab, buttons


class KhamDateAutoSelectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        kham._state.clear()

    async def select(self, tab, config):
        return await kham.nodriver_kham_date_auto_select(tab, DOMAIN, config)

    async def test_keyword_group_priority_beats_row_order(self):
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/03"),
            row_html("2026/01/02"),
        ])
        config = make_config(keyword='"2026/01/03","2026/01/01"')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1, 0])

    async def test_and_list_group_requires_all_substrings(self):
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/02"),
        ])
        config = make_config(keyword='["2026","01/02"]')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_excluded_row_cannot_be_selected(self):
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/03"),
        ])
        config = make_config(keyword='"2026/01/03"', exclude='"01/03"')
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0])

    async def test_disabled_button_row_is_skipped(self):
        tab, buttons = make_tab([
            row_html("2026/01/01", disabled=True),
            row_html("2026/01/02"),
        ])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_all_prices_disabled_row_is_skipped(self):
        disabled_prices = (
            '<span class="lightblue">1000</span>、'
            '<span class="lightblue">800</span>'
        )
        tab, buttons = make_tab([
            row_html("2026/01/01", price=disabled_prices),
            row_html("2026/01/02"),
        ])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_row_without_valid_button_is_skipped(self):
        no_button = "<tr><td>2026/01/01</td><td>台北</td><td>1000</td><td></td></tr>"
        tab, buttons = make_tab([
            no_button,
            row_html("2026/01/02"),
        ])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_no_keyword_bottom_mode_picks_last(self):
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/02"),
            row_html("2026/01/03"),
        ])
        config = make_config(keyword="", mode="from bottom to top")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0, 1])

    async def test_keyword_miss_fallback_uses_mode(self):
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/02"),
        ])
        config = make_config(
            keyword='"2027/12/31"', fallback=True, mode="from bottom to top"
        )
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_unparsable_keyword_behaves_like_no_keyword(self):
        # Kham's signature divergence: a keyword that fails to parse
        # selects by mode over all rows, even with fallback off.
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/02"),
        ])
        config = make_config(
            keyword="not-a-keyword", fallback=False, mode="from bottom to top"
        )
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_matching_group_wins_over_later_malformed_group(self):
        # Group evaluation is lazy: the first group matches, so the
        # malformed second group is never evaluated and cannot turn
        # the selection into a mode pick over all rows.
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/02"),
        ])
        config = make_config(keyword='"2026/01/02",[123]', fallback=False)
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_malformed_first_group_behaves_like_no_keyword(self):
        # The malformed group is reached first, so the failure turns
        # the selection into a mode pick over all rows.
        tab, buttons = make_tab([
            row_html("2026/01/01"),
            row_html("2026/01/02"),
        ])
        config = make_config(keyword='[123],"2026/01/02"', fallback=False)
        result = await self.select(tab, config)
        self.assertTrue(result)
        # Top mode over ALL rows (failure semantics), not the second
        # group's row-2 match.
        self.assertEqual([b.click_calls for b in buttons], [1, 0])

    async def test_coming_soon_target_is_not_clicked_and_reloads(self):
        tab, buttons = make_tab([
            row_html("2026/01/05", button="尚未開賣"),
        ])
        config = make_config(keyword="", coming_soon_reload=True)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(buttons[0].click_calls, 0)
        self.assertEqual(tab.reload_calls, 1)


if __name__ == "__main__":
    unittest.main()
