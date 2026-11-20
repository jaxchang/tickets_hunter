"""Alert handler tab identity + stale tab sweep tests (issue #94).

Registration identity: a platform's global JavascriptDialogOpening
handler lives on its tab's CDP connection, so registration is keyed
by the tab holding it. Registering twice on the same tab must not
duplicate the handler (zendriver appends without dedup), and a
different tab must get its own registration -- the old process-wide
boolean flag stayed True after a tab replacement and silently left
the new connection without alert handling.

Sweep: the main loop closes non-working tabs first seen more than
STALE_TAB_MAX_AGE_PASSES passes ago; the working tab is exempt and
tabs that disappeared are forgotten.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from zendriver import cdp  # noqa: E402

import nodriver_common  # noqa: E402
from platforms import ibon, kktix, tixcraft  # noqa: E402

from fake_tab import FakeTab  # noqa: E402

CONFIG = {"advanced": {"verbose": False}}
EVENT = cdp.page.JavascriptDialogOpening


class AlertHandlerIdentityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft._state.clear()
        ibon._state.clear()
        kktix._state.clear()

    async def test_ibon_registers_per_tab_not_per_process(self):
        tab_a, tab_b = FakeTab(), FakeTab()
        await ibon.register_ibon_alert_handler(tab_a, CONFIG)
        await ibon.register_ibon_alert_handler(tab_a, CONFIG)
        self.assertEqual(len(tab_a.handlers_for(EVENT)), 1)

        await ibon.register_ibon_alert_handler(tab_b, CONFIG)
        self.assertEqual(len(tab_b.handlers_for(EVENT)), 1)
        self.assertIs(ibon._state["alert_handler_tab"], tab_b)

    async def test_tixcraft_registers_per_tab_not_per_process(self):
        tab_a, tab_b = FakeTab(), FakeTab()
        await tixcraft.register_tixcraft_alert_handler(tab_a, CONFIG)
        await tixcraft.register_tixcraft_alert_handler(tab_a, CONFIG)
        self.assertEqual(len(tab_a.handlers_for(EVENT)), 1)

        await tixcraft.register_tixcraft_alert_handler(tab_b, CONFIG)
        self.assertEqual(len(tab_b.handlers_for(EVENT)), 1)
        self.assertIs(tixcraft._state["alert_handler_tab"], tab_b)

    async def test_kktix_registers_per_tab_not_per_process(self):
        tab_a, tab_b = FakeTab(), FakeTab()
        await kktix.register_kktix_alert_handler(tab_a, CONFIG)
        await kktix.register_kktix_alert_handler(tab_a, CONFIG)
        self.assertEqual(len(tab_a.handlers_for(EVENT)), 1)

        await kktix.register_kktix_alert_handler(tab_b, CONFIG)
        self.assertEqual(len(tab_b.handlers_for(EVENT)), 1)
        self.assertIs(kktix._state["alert_handler_tab"], tab_b)


class StaleTabSweepTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_tab_closed_working_tab_exempt(self):
        working, stale = FakeTab(), FakeTab()
        first_seen = {id(working): 0, id(stale): 0}
        closed = await nodriver_common.close_stale_tabs(
            [working, stale], working, first_seen, current_pass=1000)
        self.assertEqual(closed, [stale])
        self.assertEqual(stale.close_calls, 1)
        self.assertEqual(working.close_calls, 0)
        self.assertNotIn(id(working), first_seen)

    async def test_young_tab_kept_and_recorded(self):
        working, young = FakeTab(), FakeTab()
        first_seen = {}
        closed = await nodriver_common.close_stale_tabs(
            [working, young], working, first_seen, current_pass=10)
        self.assertEqual(closed, [])
        self.assertEqual(first_seen[id(young)], 10)

        closed = await nodriver_common.close_stale_tabs(
            [working, young], working, first_seen, current_pass=10 + 600)
        self.assertEqual(closed, [])
        self.assertEqual(young.close_calls, 0)

    async def test_boundary_age_closes_only_past_max(self):
        working, tab = FakeTab(), FakeTab()
        first_seen = {id(tab): 100}
        closed = await nodriver_common.close_stale_tabs(
            [working, tab], working, first_seen, current_pass=100 + 601)
        self.assertEqual(closed, [tab])

    async def test_disappeared_tab_forgotten(self):
        working, ghost = FakeTab(), FakeTab()
        first_seen = {id(ghost): 0}
        closed = await nodriver_common.close_stale_tabs(
            [working], working, first_seen, current_pass=50)
        self.assertEqual(closed, [])
        self.assertNotIn(id(ghost), first_seen)


if __name__ == "__main__":
    unittest.main()
