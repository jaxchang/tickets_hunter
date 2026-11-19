"""Characterization tests for the tixcraft date-selection handler (issue #24).

These tests pin the CURRENT behaviour of
``tixcraft.nodriver_tixcraft_date_auto_select`` through the fake-tab
seam (``fake_tab.py``): keyword group priority, AND list groups,
keyword exclude, the sold-out tail check gated by
``pass_date_is_sold_out``, rows without a buy text, coming-soon rows,
mode picks, strict-mode cooldown reload, and the click cascade
(``button[data-href]`` navigation beats the row's link). They were
written against the pre-refactor handler and must pass unchanged
after the handler is rewired onto the shared date-selection core
(``src/date_selection.py``, ADR 0003).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft  # noqa: E402
from fake_tab import FakeElement, FakeTab  # noqa: E402

ROWS_SELECTOR = "#gameList > table > tbody > tr"
GAME_URL = "https://tixcraft.com/activity/game/00_test"
DOMAIN = "tixcraft.com"


def make_config(keyword="", mode="from top to bottom", fallback=False,
                exclude="", pass_sold_out=True, coming_soon_reload=False):
    return {
        "date_auto_select": {
            "enable": True,
            "mode": mode,
            "date_keyword": keyword,
        },
        "date_auto_fallback": fallback,
        "keyword_exclude": exclude,
        "tixcraft": {
            "pass_date_is_sold_out": pass_sold_out,
            "auto_reload_coming_soon_page": coming_soon_reload,
        },
        "advanced": {"verbose": False, "auto_reload_page_interval": 1},
    }


def make_row(text, with_link=True, data_href=None):
    """One game-list row: canned HTML text plus its clickable children."""
    row = FakeElement(html=text)
    link = None
    if with_link:
        link = row.register_child("a[href]", FakeElement())
    if data_href is not None:
        row.register_child(
            "button[data-href]", FakeElement(attrs={"data-href": data_href})
        )
    return row, link


def make_tab(texts):
    tab = FakeTab(url=GAME_URL)
    links = []
    rows = []
    for text in texts:
        row, link = make_row(text)
        rows.append(row)
        links.append(link)
    tab.register_elements(ROWS_SELECTOR, rows)
    return tab, links


class TixcraftDateAutoSelectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft._state.clear()

    async def select(self, tab, config, url=GAME_URL):
        return await tixcraft.nodriver_tixcraft_date_auto_select(
            tab, url, config, DOMAIN
        )

    async def test_keyword_group_priority_beats_row_order(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/03 (六) 19:30 立即訂購",
            "2026/01/02 (五) 19:30 立即訂購",
        ])
        config = make_config(keyword='"2026/01/03","2026/01/01"')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1, 0])

    async def test_and_list_group_requires_all_substrings(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/02 (五) 19:30 立即訂購",
        ])
        config = make_config(keyword='["2026","01/02"]')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_excluded_row_cannot_be_selected(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/03 (六) 19:30 立即訂購",
        ])
        config = make_config(keyword='"2026/01/03"', exclude='"01/03"')
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual([l.click_calls for l in links], [0, 0])

    async def test_sold_out_tail_skipped_when_pass_enabled(self):
        tab, links = make_tab([
            "2026/01/02 (五) 19:30 立即訂購 已售完",
        ])
        config = make_config(keyword='"2026/01/02"', pass_sold_out=True)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(links[0].click_calls, 0)

    async def test_sold_out_tail_ignored_when_pass_disabled(self):
        tab, links = make_tab([
            "2026/01/02 (五) 19:30 立即訂購 已售完",
        ])
        config = make_config(keyword='"2026/01/02"', pass_sold_out=False)
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual(links[0].click_calls, 1)

    async def test_row_without_ticket_text_is_skipped(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 台北小巨蛋",
            "2026/01/02 (五) 19:30 立即訂購",
        ])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_no_keyword_bottom_mode_picks_last(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/02 (五) 19:30 立即訂購",
            "2026/01/03 (六) 19:30 立即訂購",
        ])
        config = make_config(keyword="", mode="from bottom to top")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 0, 1])

    async def test_keyword_miss_fallback_uses_mode(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/02 (五) 19:30 立即訂購",
        ])
        config = make_config(
            keyword='"2027/12/31"', fallback=True, mode="from bottom to top"
        )
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_keyword_miss_strict_reloads_via_cooldown(self):
        # Non-blocking cooldown (issue #3): the first miss only sets
        # the deadline; the reload lands on a later pass once the
        # interval has elapsed.
        import time

        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/02 (五) 19:30 立即訂購",
        ])
        config = make_config(keyword='"2027/12/31"', fallback=False)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual([l.click_calls for l in links], [0, 0])
        self.assertEqual(tab.reload_calls, 0)
        self.assertIn("date_reload_cooldown_until", tixcraft._state)

        tixcraft._state["date_reload_cooldown_until"] = time.time() - 1
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(tab.reload_calls, 1)

    async def test_data_href_navigation_beats_link_click(self):
        tab = FakeTab(url=GAME_URL)
        row, link = make_row(
            "2026/01/01 (四) 19:30 立即訂購",
            data_href="https://tixcraft.com/ticket/area/00_test/1",
        )
        tab.register_elements(ROWS_SELECTOR, [row])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual(
            tab.get_calls, ["https://tixcraft.com/ticket/area/00_test/1"]
        )
        self.assertEqual(link.click_calls, 0)

    async def test_coming_soon_row_is_skipped(self):
        tab, links = make_tab([
            "2026/01/01 12:00 0 day(s) 0 hrs. 0 min 0 sec till sale starts!",
        ])
        config = make_config(keyword="", coming_soon_reload=False)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(links[0].click_calls, 0)

    async def test_sold_out_text_early_in_row_is_not_a_tail_match(self):
        # The sold-out check only inspects the tail of the row text;
        # a sold-out string at the start must not disable the row.
        tab, links = make_tab([
            "已售完 2026/01/02 (五) 19:30 立即訂購",
        ])
        config = make_config(keyword='"2026/01/02"', pass_sold_out=True)
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual(links[0].click_calls, 1)

    async def test_coming_soon_break_keeps_earlier_rows_selectable(self):
        # With auto_reload_coming_soon_page on, a coming-soon row
        # stops the extraction, but rows collected before it are
        # still selectable in the same pass.
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
            "2026/01/02 12:00 0 day(s) 0 hrs. 0 min 0 sec till sale starts!",
        ])
        config = make_config(keyword="", coming_soon_reload=True)
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [1, 0])

    async def test_html_fetch_failure_keeps_earlier_rows(self):
        class BrokenElement(FakeElement):
            async def get_html(self):
                raise RuntimeError("cdp gone")

        tab = FakeTab(url=GAME_URL)
        row1, link1 = make_row("2026/01/01 (四) 19:30 立即訂購")
        row3, link3 = make_row("2026/01/03 (六) 19:30 立即訂購")
        tab.register_elements(ROWS_SELECTOR, [row1, BrokenElement(), row3])
        config = make_config(keyword="", mode="from bottom to top")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual(link1.click_calls, 1)
        self.assertEqual(link3.click_calls, 0)

    async def test_non_game_url_selects_nothing(self):
        tab, links = make_tab([
            "2026/01/01 (四) 19:30 立即訂購",
        ])
        config = make_config(keyword="")
        result = await self.select(
            tab, config, url="https://tixcraft.com/activity/detail/00_test"
        )
        self.assertFalse(result)
        self.assertEqual(links[0].click_calls, 0)


if __name__ == "__main__":
    unittest.main()
