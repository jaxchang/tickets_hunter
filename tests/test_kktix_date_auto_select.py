"""Characterization tests for the kktix date-selection handler (issue #22).

These tests pin the CURRENT behaviour of
``kktix.nodriver_kktix_date_auto_select`` through the fake-tab seam
(``fake_tab.py``): keyword group priority, AND list groups, keyword
exclude, rows without a buy button, mode picks, strict vs fallback,
and whitespace collapsing. They were written against the pre-refactor
handler and must pass unchanged after the handler is rewired onto the
shared date-selection core (``src/date_selection.py``) -- that is what
makes the rewiring a behaviour-preserving change.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import kktix  # noqa: E402
from fake_tab import FakeElement, FakeTab  # noqa: E402

SESSIONS_SELECTOR = "div.event-list ul.clearfix > li"
DATE_SELECTOR = "span.timezoneSuffix"
BUTTON_SELECTOR = "div.content > a.btn-point"


def make_config(keyword="", mode="from top to bottom", fallback=False, exclude=""):
    return {
        "date_auto_select": {
            "enable": True,
            "mode": mode,
            "date_keyword": keyword,
        },
        "date_auto_fallback": fallback,
        "keyword_exclude": exclude,
        "advanced": {"verbose": False},
    }


def make_session(text, has_button=True):
    """One session row: a date label and (optionally) a buy button."""
    item = FakeElement()
    item.register_child(DATE_SELECTOR, FakeElement(html=text))
    button = None
    if has_button:
        button = item.register_child(BUTTON_SELECTOR, FakeElement())
    return item, button


def make_tab(texts, button_flags=None):
    tab = FakeTab()
    buttons = []
    items = []
    for i, text in enumerate(texts):
        has_button = True if button_flags is None else button_flags[i]
        item, button = make_session(text, has_button=has_button)
        items.append(item)
        buttons.append(button)
    tab.register_elements(SESSIONS_SELECTOR, items)
    return tab, buttons


class KktixDateAutoSelectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        kktix._state.clear()

    async def test_keyword_group_priority_beats_row_order(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/03", "2026/01/02"])
        config = make_config(keyword='"2026/01/03","2026/01/01"')
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1, 0])

    async def test_and_list_group_requires_all_substrings(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(keyword='["2026","01/02"]')
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_excluded_row_cannot_be_selected(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/03"])
        config = make_config(keyword='"2026/01/03"', exclude='"01/03"')
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertFalse(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0])

    async def test_row_without_button_is_skipped(self):
        tab, buttons = make_tab(
            ["2026/01/01", "2026/01/02"], button_flags=[False, True]
        )
        config = make_config(keyword="")
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual(buttons[1].click_calls, 1)

    async def test_no_keyword_bottom_mode_picks_last(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02", "2026/01/03"])
        config = make_config(keyword="", mode="from bottom to top")
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0, 1])

    async def test_no_keyword_center_mode_picks_middle(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02", "2026/01/03"])
        config = make_config(keyword="", mode="center")
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1, 0])

    async def test_keyword_miss_strict_returns_false(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(keyword='"2027/12/31"', fallback=False)
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertFalse(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0])

    async def test_keyword_miss_fallback_uses_mode(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(
            keyword='"2027/12/31"', fallback=True, mode="from bottom to top"
        )
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 1])

    async def test_whitespace_is_collapsed_before_matching(self):
        tab, buttons = make_tab(["2026/01/02  (六)"])
        config = make_config(keyword='"2026/01/02 (六)"')
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual(buttons[0].click_calls, 1)

    async def test_keyword_hit_ignores_mode_first_match_wins(self):
        # The winning group matches both rows; the handler takes the
        # FIRST match -- the mode pick must not be applied to keyword
        # hits (bottom mode would otherwise click the last row).
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(keyword='"2026"', mode="from bottom to top")
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertTrue(result)
        self.assertEqual([b.click_calls for b in buttons], [1, 0])

    async def test_unparsable_keyword_is_a_miss(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(keyword="not-a-keyword", fallback=False)
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertFalse(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0])

    async def test_missing_keyword_exclude_key_selects_nothing(self):
        # The old handler ran the exclude predicate inside its per-row
        # try, so a config without the key skipped every row.
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(keyword="")
        del config["keyword_exclude"]
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertFalse(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0])

    async def test_none_keyword_exclude_selects_nothing(self):
        tab, buttons = make_tab(["2026/01/01", "2026/01/02"])
        config = make_config(keyword="")
        config["keyword_exclude"] = None
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertFalse(result)
        self.assertEqual([b.click_calls for b in buttons], [0, 0])

    async def test_no_sessions_returns_false(self):
        tab = FakeTab()
        config = make_config(keyword="")
        result = await kktix.nodriver_kktix_date_auto_select(tab, config)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
