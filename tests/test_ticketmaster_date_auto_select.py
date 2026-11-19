"""Characterization tests for the ticketmaster date handler (issue #26).

These tests pin the CURRENT behaviour of
``tixcraft.nodriver_ticketmaster_date_auto_select`` through the
fake-tab seam (``fake_tab.py``): keyword group priority, AND list
groups, the sold-out check gated by ``pass_date_is_sold_out``, rows
without a ticket text, mode picks, strict vs fallback -- and two
traits that set this handler apart: it has NEVER applied
``keyword_exclude``, and its cooldown reload only runs when no date
is available at all (a strict keyword miss with rows present does
not reload). They were written against the pre-refactor handler and
must pass unchanged after the handler is rewired onto the shared
date-selection core (``src/date_selection.py``, ADR 0003).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft  # noqa: E402
from fake_tab import FakeElement, FakeTab  # noqa: E402

ROWS_SELECTOR = "#gameList tbody tr"


def make_config(keyword="", mode="from top to bottom", fallback=False,
                exclude="", pass_sold_out=True, coming_soon_reload=True):
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


def make_row(text):
    row = FakeElement(html=text)
    link = row.register_child("a", FakeElement())
    return row, link


def make_tab(texts):
    tab = FakeTab(url="https://www.ticketmaster.sg/activity/detail/test")
    links = []
    rows = []
    for text in texts:
        row, link = make_row(text)
        rows.append(row)
        links.append(link)
    tab.register_elements(ROWS_SELECTOR, rows)
    return tab, links


class TicketmasterDateAutoSelectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft._state.clear()

    async def select(self, tab, config):
        return await tixcraft.nodriver_ticketmaster_date_auto_select(tab, config)

    async def test_keyword_group_priority_beats_row_order(self):
        tab, links = make_tab([
            "Fri Jan 02 2026 Find tickets",
            "Sun Jan 04 2026 Find tickets",
            "Sat Jan 03 2026 Find tickets",
        ])
        config = make_config(keyword='"Jan 04","Jan 02"')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1, 0])

    async def test_and_list_group_requires_all_substrings(self):
        tab, links = make_tab([
            "Fri Jan 02 2026 Find tickets",
            "Sat Jan 03 2026 Find tickets",
        ])
        config = make_config(keyword='["Jan","03"]')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_keyword_exclude_is_not_applied(self):
        # This handler has never honoured keyword_exclude; the
        # migration preserves that (see issue #26).
        tab, links = make_tab([
            "Fri Jan 02 2026 Find tickets",
            "Sun Jan 04 2026 Find tickets",
        ])
        config = make_config(keyword='"Jan 04"', exclude='"Jan 04"')
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_sold_out_skipped_when_pass_enabled(self):
        tab, links = make_tab([
            "Sat Jan 03 2026 Sold out Find tickets",
        ])
        config = make_config(keyword='"Jan 03"', pass_sold_out=True)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(links[0].click_calls, 0)

    async def test_sold_out_ignored_when_pass_disabled(self):
        tab, links = make_tab([
            "Sat Jan 03 2026 Sold out Find tickets",
        ])
        config = make_config(keyword='"Jan 03"', pass_sold_out=False)
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual(links[0].click_calls, 1)

    async def test_row_without_ticket_text_is_skipped(self):
        tab, links = make_tab([
            "Fri Jan 02 2026",
            "Sat Jan 03 2026 See Tickets",
        ])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_no_keyword_bottom_mode_picks_last(self):
        tab, links = make_tab([
            "Fri Jan 02 2026 Find tickets",
            "Sat Jan 03 2026 Find tickets",
            "Sun Jan 04 2026 Find tickets",
        ])
        config = make_config(keyword="", mode="from bottom to top")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 0, 1])

    async def test_keyword_miss_fallback_uses_mode(self):
        tab, links = make_tab([
            "Fri Jan 02 2026 Find tickets",
            "Sat Jan 03 2026 Find tickets",
        ])
        config = make_config(
            keyword='"Dec 31"', fallback=True, mode="from bottom to top"
        )
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual([l.click_calls for l in links], [0, 1])

    async def test_keyword_miss_strict_with_rows_does_not_reload(self):
        # Unlike the tixcraft date handler, a strict miss with rows
        # present returns without touching the reload cooldown.
        tab, links = make_tab([
            "Fri Jan 02 2026 Find tickets",
            "Sat Jan 03 2026 Find tickets",
        ])
        config = make_config(keyword='"Dec 31"', fallback=False)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual([l.click_calls for l in links], [0, 0])
        self.assertEqual(tab.reload_calls, 0)
        self.assertNotIn("ticketmaster_date_reload_cooldown_until", tixcraft._state)

    async def test_row_with_failed_text_reread_stays_selectable(self):
        # Extraction reads each available row's text twice; when the
        # re-read fails the row's text is empty, but the row itself
        # remains selectable by a mode pick.
        class FlakyElement(FakeElement):
            def __init__(self, html):
                super().__init__(html=html)
                self.calls = 0

            async def get_html(self):
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("re-rendered")
                return self._html

        tab = FakeTab(url="https://www.ticketmaster.sg/activity/detail/test")
        row = FlakyElement("Fri Jan 02 2026 Find tickets")
        link = row.register_child("a", FakeElement())
        tab.register_elements(ROWS_SELECTOR, [row])
        config = make_config(keyword="")
        result = await self.select(tab, config)
        self.assertTrue(result)
        self.assertEqual(link.click_calls, 1)

    async def test_no_available_dates_reloads_via_cooldown(self):
        import time

        tab, links = make_tab([
            "Fri Jan 02 2026 Sold out Find tickets",
            "Sat Jan 03 2026 Sold out Find tickets",
        ])
        config = make_config(keyword="", pass_sold_out=True)
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(tab.reload_calls, 0)
        self.assertIn("ticketmaster_date_reload_cooldown_until", tixcraft._state)

        tixcraft._state["ticketmaster_date_reload_cooldown_until"] = time.time() - 1
        result = await self.select(tab, config)
        self.assertFalse(result)
        self.assertEqual(tab.reload_calls, 1)


if __name__ == "__main__":
    unittest.main()
