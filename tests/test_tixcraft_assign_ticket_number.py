"""TixCraft assign: the ticket-type keyword is a preference, not a gate (issue #106).

nodriver_tixcraft_assign_ticket_number matches ticket types with the
AREA keyword (tixcraft has no ticket-type keyword of its own). The
match used to be a hard gate: with area_auto_fallback off, a keyword
that matched no ticket type name selected nothing, and the caller
reported it as an unavailable ticket count -- on the 2026-11-21 ARTMS
page the area is named VIP區 but the types are 全票..., so the run
looped forever buying nothing.

Now the keyword only ranks: the first matching type wins, and when
nothing matches the pick falls through to the auto-select mode over
every usable type. keyword_exclude, applied while collecting
candidates, stays the only hard filter. The function also returns a
failure reason ("no_valid_types") so the caller can tell "this area
has no usable ticket type" apart from an unavailable count.

The page is a FakeTab whose .mobile-select elements are FakeElements
shaped like the real ticket rows: a select inside a <tr> whose <h4>
carries the ticket type name.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft  # noqa: E402

from fake_tab import FakeElement, FakeTab  # noqa: E402

TICKET_URL = "https://tixcraft.com/ticket/ticket/27_artms/24577/1/67"


def make_config(keyword='"VIP"', fallback=False, keyword_exclude=""):
    return {
        "ticket_number": 2,
        "keyword_exclude": keyword_exclude,
        "area_auto_fallback": fallback,
        "area_auto_select": {
            "enable": True,
            "mode": "random",
            "area_keyword": keyword,
        },
        "tixcraft": {"allow_less_tickets": False},
        "advanced": {"verbose": False},
    }


def make_select(select_id, name, disabled=False):
    """A ticket row's select: options 0/1/2, the type name in the row's <h4>."""
    select = FakeElement(attrs={"id": select_id})
    if disabled:
        select.attrs["disabled"] = "disabled"
    options = []
    for value in ("0", "1", "2"):
        option = FakeElement(attrs={"value": value})
        option.text = value
        options.append(option)
    select.register_children("option", options)
    row = FakeElement()
    row.tag = "tr"
    row.register_child("h4", FakeElement(html=name))
    select.parent = row
    return select


def artms_tab():
    """The incident page's two usable types (four more rows are disabled)."""
    selects = [
        make_select("TicketForm_ticketPrice_01", "全票+福利兌換券 4,980"),
        make_select("TicketForm_ticketPrice_05", "全票 4,900"),
    ]
    tab = FakeTab(url=TICKET_URL)
    tab.register_elements(".mobile-select", selects)
    return tab, selects


class AssignTicketNumberTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft._state.clear()

    def tearDown(self):
        tixcraft._state.clear()

    async def test_pause_bail_out_returns_the_empty_tuple(self):
        tab, _ = artms_tab()

        with mock.patch.object(tixcraft, "check_and_handle_pause",
                               mock.AsyncMock(return_value=True)):
            result = await tixcraft.nodriver_tixcraft_assign_ticket_number(
                tab, make_config())

        self.assertEqual(result, (False, None, None, None))

    async def test_keyword_miss_picks_a_type_by_mode(self):
        # The incident configuration: area keyword VIP, fallback off.
        # Neither usable type is named VIP, yet a type must be picked.
        tab, selects = artms_tab()

        assigned, select_obj, select_id, reason = (
            await tixcraft.nodriver_tixcraft_assign_ticket_number(
                tab, make_config(keyword='"VIP"', fallback=False)))

        self.assertFalse(assigned)  # count not set yet; the caller fills it
        self.assertIn(select_obj, selects)
        self.assertIn(select_id, [s.attrs["id"] for s in selects])
        self.assertIsNone(reason)

    async def test_keyword_match_is_preferred(self):
        tab, _ = artms_tab()

        assigned, select_obj, select_id, reason = (
            await tixcraft.nodriver_tixcraft_assign_ticket_number(
                tab, make_config(keyword='"福利"')))

        self.assertEqual(select_id, "TicketForm_ticketPrice_01")
        self.assertIsNone(reason)

    async def test_keyword_exclude_is_the_hard_filter(self):
        # VIP misses both names; 福利 excludes the voucher type at
        # collection time, so the plain 全票 is the only candidate.
        tab, _ = artms_tab()

        assigned, select_obj, select_id, reason = (
            await tixcraft.nodriver_tixcraft_assign_ticket_number(
                tab, make_config(keyword='"VIP"', keyword_exclude='"福利"')))

        self.assertEqual(select_id, "TicketForm_ticketPrice_05")
        self.assertIsNone(reason)

    async def test_exclude_can_empty_the_candidates(self):
        tab, _ = artms_tab()

        assigned, select_obj, select_id, reason = (
            await tixcraft.nodriver_tixcraft_assign_ticket_number(
                tab, make_config(keyword_exclude='"全票"')))

        self.assertFalse(assigned)
        self.assertIsNone(select_obj)
        self.assertIsNone(select_id)
        self.assertEqual(reason, "no_valid_types")

    async def test_all_types_disabled_reports_no_valid_types(self):
        tab = FakeTab(url=TICKET_URL)
        tab.register_elements(".mobile-select", [
            make_select("TicketForm_ticketPrice_19", "身障票+福利兌換券 2,530",
                        disabled=True),
            make_select("TicketForm_ticketPrice_20", "陪同票+福利兌換券 2,530",
                        disabled=True),
        ])

        assigned, select_obj, select_id, reason = (
            await tixcraft.nodriver_tixcraft_assign_ticket_number(
                tab, make_config()))

        self.assertFalse(assigned)
        self.assertIsNone(select_obj)
        self.assertIsNone(select_id)
        self.assertEqual(reason, "no_valid_types")


if __name__ == "__main__":
    unittest.main()
