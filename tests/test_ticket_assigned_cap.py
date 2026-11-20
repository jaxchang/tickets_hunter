"""ticket_assigned_* cap tests (issue #92).

The markers are kept for the run so a set ticket count is not
re-entered, but the key embeds the full URL, so the family grew
without bound on long runs. The cap evicts the oldest markers
first; an evicted URL degrades to the first-visit path and re-runs
the idempotent count assignment.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import tixcraft  # noqa: E402


def seed_markers(count, start=0):
    for i in range(start, start + count):
        tixcraft._state[f"ticket_assigned_https://example/{i}_2_0"] = True


def marker_keys():
    return [k for k in tixcraft._state if k.startswith("ticket_assigned_")]


class TicketAssignedCapTest(unittest.TestCase):
    def setUp(self):
        tixcraft._state.clear()

    def test_cap_evicts_oldest_first(self):
        seed_markers(130)
        tixcraft._cap_ticket_assigned_markers()
        keys = marker_keys()
        self.assertEqual(len(keys), 128)
        self.assertNotIn("ticket_assigned_https://example/0_2_0", keys)
        self.assertNotIn("ticket_assigned_https://example/1_2_0", keys)
        self.assertIn("ticket_assigned_https://example/2_2_0", keys)
        self.assertIn("ticket_assigned_https://example/129_2_0", keys)

    def test_under_cap_is_noop(self):
        seed_markers(3)
        tixcraft._cap_ticket_assigned_markers()
        self.assertEqual(len(marker_keys()), 3)

    def test_exactly_at_cap_is_noop(self):
        seed_markers(128)
        tixcraft._cap_ticket_assigned_markers()
        self.assertEqual(len(marker_keys()), 128)
        self.assertIn("ticket_assigned_https://example/0_2_0", marker_keys())

    def test_other_keys_untouched(self):
        tixcraft._state["html_lang"] = "en"
        seed_markers(130)
        tixcraft._cap_ticket_assigned_markers()
        self.assertEqual(tixcraft._state["html_lang"], "en")
        self.assertEqual(len(marker_keys()), 128)

    def test_new_write_survives_cap(self):
        seed_markers(128)
        tixcraft._state["ticket_assigned_https://example/new_2_0"] = True
        tixcraft._cap_ticket_assigned_markers()
        keys = marker_keys()
        self.assertEqual(len(keys), 128)
        self.assertIn("ticket_assigned_https://example/new_2_0", keys)
        self.assertNotIn("ticket_assigned_https://example/0_2_0", keys)


if __name__ == "__main__":
    unittest.main()
