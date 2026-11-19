"""Regression test for upstream #376 via the fake-tab seam (issue #20).

The tixcraft area handler runs on a sold-out page (the zone has no area
anchors). The #376 symptom: the refresh interval throttled the purchase
flow because the handler slept through the interval inside one pass.
The paced behaviour, asserted here with the real clock:

- every handler pass returns promptly (well under the interval);
- the first passes start/observe the cooldown instead of reloading;
- once the interval has elapsed, the next pass reloads exactly once.

This is the .scratch/issue3 feedback-loop harness, promoted to a
committed unittest on top of tests/fake_tab.py.
"""
import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft  # noqa: E402

from fake_tab import FakeTab  # noqa: E402

INTERVAL = 1.0  # seconds; short so the test stays fast, long vs the 50ms tick
BLOCK_BUDGET = 0.5  # a handler pass must finish well under the interval
AREA_URL = "https://tixcraft.com/ticket/area/00_test/1"


def make_config():
    return {
        "area_auto_select": {
            "enable": True,
            "area_keyword": '"A1"',
            "mode": "from top to bottom",
        },
        "area_auto_fallback": False,
        "ticket_number": 1,
        "keyword_exclude": "",
        "tixcraft": {"allow_less_tickets": False},
        "advanced": {
            "verbose": False,
            "auto_reload_page_interval": INTERVAL,
            "auto_reload_page_interval_range": False,
        },
    }


class TixcraftAreaCooldownTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft._state.clear()
        self.tab = FakeTab(url=AREA_URL)
        # Sold-out area page: the zone exists but holds no area anchors.
        self.tab.register_element(".zone")
        self.config = make_config()

    async def one_pass(self):
        start = time.monotonic()
        await tixcraft.nodriver_tixcraft_area_auto_select(
            self.tab, AREA_URL, self.config
        )
        return time.monotonic() - start

    async def test_reload_is_paced_without_blocking_the_pass(self):
        # Pass 1: starts the cooldown; must neither block nor reload.
        duration = await self.one_pass()
        self.assertLess(duration, BLOCK_BUDGET)
        self.assertEqual(self.tab.reload_calls, 0)

        # Pass 2, immediately after: still inside the interval.
        duration = await self.one_pass()
        self.assertLess(duration, BLOCK_BUDGET)
        self.assertEqual(self.tab.reload_calls, 0)

        # After the interval elapses, the next pass reloads exactly once.
        await asyncio.sleep(INTERVAL + 0.15)
        duration = await self.one_pass()
        self.assertLess(duration, BLOCK_BUDGET)
        self.assertEqual(self.tab.reload_calls, 1)


if __name__ == "__main__":
    unittest.main()
