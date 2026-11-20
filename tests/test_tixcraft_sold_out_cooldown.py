"""Behaviour tests for the tixcraft sold-out cooldown gate (issue #80).

The global alert handler arms _state["sold_out_cooldown_until"] when
it dismisses a sold-out dialog. The date handler used to convert
that timestamp back into a blocking asyncio.sleep inside the
handler, stalling the main loop for up to the full reload interval.
The gate is now non-blocking (the #376 pattern): while the cooldown
is active the handler returns immediately and the main loop's next
pass re-checks the timestamp.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft, tixcraft_shared  # noqa: E402

from fake_tab import FakeTab  # noqa: E402

URL = "https://tixcraft.com/activity/detail/123"


def make_config():
    return {
        "date_auto_select": {
            "enable": True,
            "mode": "random",
            "date_keyword": "",
            "keyword": "",
            "auto_select_fallback": True,
            "fallback": True,
        },
        "tixcraft": {
            "pass_date_is_sold_out": False,
            "auto_reload_coming_soon_page": False,
        },
        "keyword_exclude": "",
        "advanced": {"verbose": False},
    }


class SoldOutCooldownGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft_shared._state.clear()

    async def call_handler(self, tab):
        return await tixcraft.nodriver_tixcraft_date_auto_select(
            tab, URL, make_config(), "tixcraft.com")

    async def test_active_cooldown_returns_immediately(self):
        tab = FakeTab(url=URL)
        until = time.time() + 3
        tixcraft_shared._state["sold_out_cooldown_until"] = until

        start = time.time()
        result = await self.call_handler(tab)
        elapsed = time.time() - start

        self.assertFalse(result)
        # The old code slept for the remaining ~3s inside the handler.
        self.assertLess(elapsed, 1.5)
        # The gate stopped the handler before any page work...
        self.assertNotIn("html_lang", tixcraft_shared._state)
        # ...and the timestamp is left as the source of truth.
        self.assertEqual(
            tixcraft_shared._state["sold_out_cooldown_until"], until)

    async def test_expired_cooldown_proceeds(self):
        tab = FakeTab(url=URL)
        tab.add_evaluate_response("documentElement.lang", "zh-TW")
        tixcraft_shared._state["sold_out_cooldown_until"] = time.time() - 10

        start = time.time()
        result = await self.call_handler(tab)
        elapsed = time.time() - start

        self.assertFalse(result)  # no rows on the fake page
        self.assertLess(elapsed, 1.5)
        # Proof the handler ran past the gate: it classified the page
        # language, which only happens after the cooldown check.
        self.assertEqual(tixcraft_shared._state.get("html_lang"), "zh-TW")

    async def test_no_cooldown_proceeds(self):
        tab = FakeTab(url=URL)
        tab.add_evaluate_response("documentElement.lang", "zh-TW")

        result = await self.call_handler(tab)

        self.assertFalse(result)
        self.assertEqual(tixcraft_shared._state.get("html_lang"), "zh-TW")


if __name__ == "__main__":
    unittest.main()
