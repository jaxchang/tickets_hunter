"""TixCraft ticket page: unavailable count returns to area selection (issue #98).

When the target ticket count cannot be set for the chosen ticket type
(e.g. 2 requested, 1 left, allow_less_tickets off), the failure path
used to reload the ticket page and retry the same select forever
(upstream T026). It now goes back to area selection so the flow can
pick another area and keep trying for the full count; the reload
remains as the fallback when going back fails or does not leave the
ticket page.

The leaf functions (assign / select_fill / agree / OCR) are stubbed at
the module seam: assign and select_fill return their real failure
outputs for the "target option missing" DOM state, which the JS in
select_fill produces deterministically. Everything composed in
nodriver_tixcraft_ticket_main itself is the real code.

Issue #106 adds the second failure kind: assign reports
"no_valid_types" when the area has no usable ticket type at all. That
path goes back with its own counter and its own log line instead of
borrowing the count's, and select_fill is never called (there is no
select to fill).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft  # noqa: E402

from fake_tab import FakeElement, FakeTab  # noqa: E402

TICKET_URL = "https://tixcraft.com/ticket/ticket/26_TEST/1/1"
AREA_URL = "https://tixcraft.com/ticket/area/26_TEST/1"
BACKS_KEY = f"ticket_unavailable_backs_{TICKET_URL}"
NOTYPE_KEY = f"ticket_no_type_backs_{TICKET_URL}"


def make_config():
    return {
        "ticket_number": 2,
        "tixcraft": {"allow_less_tickets": False},
        "advanced": {"verbose": False, "auto_reload_page_interval": 0},
    }


class TicketMainCountFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft._state.clear()
        self.tab = FakeTab(url=TICKET_URL)
        self.ocr_calls = 0
        self.fill_calls = 0

        async def fake_url(tab, config_dict=None):
            return tab.url, False

        async def fake_ocr(tab, config_dict, ocr, captcha_browser, domain_name):
            self.ocr_calls += 1

        self.patchers = [
            mock.patch.object(tixcraft, "check_and_handle_pause",
                              mock.AsyncMock(return_value=False)),
            mock.patch.object(tixcraft, "nodriver_current_url", fake_url),
            mock.patch.object(tixcraft, "nodriver_tixcraft_ticket_main_agree",
                              mock.AsyncMock()),
            mock.patch.object(tixcraft, "nodriver_tixcraft_ticket_main_ocr",
                              fake_ocr),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        tixcraft._state.clear()

    def stub_assignment(self, assigned, reason=None):
        async def fake_assign(tab, config_dict):
            if reason is not None:
                # A failed selection hands back no select at all.
                return assigned, None, None, reason
            return assigned, FakeElement(), "TicketForm_ticketPrice_1", None

        async def fake_fill(tab, select_obj, ticket_number, select_id=None,
                            allow_less_tickets=False):
            self.fill_calls += 1
            return assigned

        self.patchers.append(mock.patch.object(
            tixcraft, "nodriver_tixcraft_assign_ticket_number", fake_assign))
        self.patchers.append(mock.patch.object(
            tixcraft, "nodriver_ticket_number_select_fill", fake_fill))
        for patcher in self.patchers[-2:]:
            patcher.start()

    async def run_main(self):
        await tixcraft.nodriver_tixcraft_ticket_main(
            self.tab, make_config(), None, None, "tixcraft.com")

    async def test_unavailable_count_goes_back_instead_of_reloading(self):
        self.stub_assignment(False)
        self.tab.back_target_url = AREA_URL

        await self.run_main()

        self.assertEqual(self.tab.back_calls, 1)
        self.assertEqual(self.tab.reload_calls, 0)
        self.assertEqual(self.ocr_calls, 0)
        self.assertFalse(any(k.startswith("ticket_assigned_")
                             for k in tixcraft._state))
        self.assertEqual(tixcraft._state[BACKS_KEY], 1)

    async def test_back_error_falls_back_to_reload(self):
        self.stub_assignment(False)
        self.tab.back_error = RuntimeError("no history entry")

        await self.run_main()

        self.assertEqual(self.tab.back_calls, 1)
        self.assertEqual(self.tab.reload_calls, 1)

    async def test_still_on_ticket_page_after_back_reloads(self):
        self.stub_assignment(False)
        # back() succeeds but the browser stays on the ticket page
        # (back_target_url unset: FakeTab keeps its URL).

        await self.run_main()

        self.assertEqual(self.tab.back_calls, 1)
        self.assertEqual(self.tab.reload_calls, 1)

    async def test_counter_accumulates_then_success_clears_it(self):
        self.stub_assignment(False)
        self.tab.back_target_url = AREA_URL

        await self.run_main()
        self.tab.url = TICKET_URL  # re-entered the ticket page
        await self.run_main()
        self.assertEqual(tixcraft._state[BACKS_KEY], 2)

        self.stub_assignment(True)
        self.tab.url = TICKET_URL
        await self.run_main()

        self.assertNotIn(BACKS_KEY, tixcraft._state)
        self.assertTrue(any(k.startswith("ticket_assigned_")
                            for k in tixcraft._state))
        self.assertEqual(self.ocr_calls, 1)

    async def test_backs_counters_are_capped_oldest_first(self):
        cap = tixcraft.CONST_TIXCRAFT_TICKET_ASSIGNED_MAX
        for i in range(cap + 5):
            tixcraft._state[f"ticket_unavailable_backs_https://tixcraft.com/ticket/ticket/E{i}"] = 1

        tixcraft._cap_ticket_backs_counters()

        keys = [k for k in tixcraft._state
                if k.startswith("ticket_unavailable_backs_")]
        self.assertEqual(len(keys), cap)
        self.assertNotIn("ticket_unavailable_backs_https://tixcraft.com/ticket/ticket/E0", keys)
        self.assertIn(f"ticket_unavailable_backs_https://tixcraft.com/ticket/ticket/E{cap + 4}", keys)

    async def test_assign_pause_bail_out_ends_the_pass_quietly(self):
        async def fake_assign(tab, config_dict):
            return False, None, None, None

        async def fake_fill(tab, select_obj, ticket_number, select_id=None,
                            allow_less_tickets=False):
            self.fill_calls += 1
            return False

        self.patchers.append(mock.patch.object(
            tixcraft, "nodriver_tixcraft_assign_ticket_number", fake_assign))
        self.patchers.append(mock.patch.object(
            tixcraft, "nodriver_ticket_number_select_fill", fake_fill))
        for patcher in self.patchers[-2:]:
            patcher.start()

        result = await tixcraft.nodriver_tixcraft_ticket_main(
            self.tab, make_config(), None, None, "tixcraft.com")

        self.assertFalse(result)
        self.assertEqual(self.fill_calls, 0)
        self.assertEqual(self.tab.back_calls, 0)
        self.assertEqual(self.tab.reload_calls, 0)
        self.assertEqual(self.ocr_calls, 0)
        self.assertNotIn(BACKS_KEY, tixcraft._state)
        self.assertNotIn(NOTYPE_KEY, tixcraft._state)

    async def test_no_valid_types_goes_back_with_its_own_counter(self):
        self.stub_assignment(False, reason="no_valid_types")
        self.tab.back_target_url = AREA_URL

        await self.run_main()

        self.assertEqual(self.fill_calls, 0)
        self.assertEqual(self.tab.back_calls, 1)
        self.assertEqual(self.tab.reload_calls, 0)
        self.assertEqual(self.ocr_calls, 0)
        self.assertEqual(tixcraft._state[NOTYPE_KEY], 1)
        self.assertNotIn(BACKS_KEY, tixcraft._state)

    async def test_success_clears_both_back_counters(self):
        tixcraft._state[BACKS_KEY] = 3
        tixcraft._state[NOTYPE_KEY] = 1
        self.stub_assignment(True)

        await self.run_main()

        self.assertNotIn(BACKS_KEY, tixcraft._state)
        self.assertNotIn(NOTYPE_KEY, tixcraft._state)

    def test_forget_clears_both_back_counters(self):
        tixcraft._state[BACKS_KEY] = 3
        tixcraft._state[NOTYPE_KEY] = 1

        tixcraft._forget_ticket_assigned(TICKET_URL)

        self.assertNotIn(BACKS_KEY, tixcraft._state)
        self.assertNotIn(NOTYPE_KEY, tixcraft._state)

    async def test_notype_counters_are_capped_oldest_first(self):
        cap = tixcraft.CONST_TIXCRAFT_TICKET_ASSIGNED_MAX
        for i in range(cap + 5):
            tixcraft._state[f"ticket_no_type_backs_https://tixcraft.com/ticket/ticket/E{i}"] = 1

        tixcraft._cap_ticket_backs_counters()

        keys = [k for k in tixcraft._state
                if k.startswith("ticket_no_type_backs_")]
        self.assertEqual(len(keys), cap)
        self.assertNotIn("ticket_no_type_backs_https://tixcraft.com/ticket/ticket/E0", keys)

    async def test_success_first_try_never_goes_back(self):
        self.stub_assignment(True)

        await self.run_main()

        self.assertEqual(self.tab.back_calls, 0)
        self.assertEqual(self.tab.reload_calls, 0)
        self.assertEqual(self.ocr_calls, 1)
        self.assertNotIn(BACKS_KEY, tixcraft._state)


if __name__ == "__main__":
    unittest.main()
