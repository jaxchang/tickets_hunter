# Unit tests for nodriver_common.PageState (issue #7, ADR 0002).
# Run from the repo root: python -m unittest discover -s tests
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nodriver_common import PageState  # noqa: E402


class PageStateTest(unittest.TestCase):
    def make_state(self):
        state = PageState("test")
        state.declare_page("area_reload_cooldown_until", "tixcraft_area")
        state.declare_page("date_reload_cooldown_until", "tixcraft_date")
        state.declare_session("played_sound_ticket", "sound plays once per run")
        return state

    def test_page_key_survives_on_its_own_page(self):
        state = self.make_state()
        state.set_page("tixcraft_area")
        state["area_reload_cooldown_until"] = 123.0
        state.set_page("tixcraft_area")
        self.assertEqual(state["area_reload_cooldown_until"], 123.0)

    def test_page_key_cleared_when_page_changes(self):
        state = self.make_state()
        state.set_page("tixcraft_area")
        state["area_reload_cooldown_until"] = 123.0
        cleared = state.set_page("ticket")
        self.assertEqual(cleared, ["area_reload_cooldown_until"])
        self.assertNotIn("area_reload_cooldown_until", state)

    def test_key_of_another_page_cleared_on_arrival(self):
        state = self.make_state()
        state.set_page("tixcraft_date")
        state["date_reload_cooldown_until"] = 50.0
        state.set_page("tixcraft_area")
        self.assertNotIn("date_reload_cooldown_until", state)

    def test_none_page_clears_all_page_keys(self):
        state = self.make_state()
        state.set_page("tixcraft_area")
        state["area_reload_cooldown_until"] = 1.0
        state.set_page(None)
        self.assertNotIn("area_reload_cooldown_until", state)

    def test_session_key_survives_page_changes(self):
        state = self.make_state()
        state["played_sound_ticket"] = True
        state.set_page("tixcraft_area")
        state.set_page("ticket")
        state.set_page(None)
        self.assertTrue(state["played_sound_ticket"])

    def test_undeclared_key_behaves_as_session_and_is_recorded(self):
        state = self.make_state()
        state["surprise_key"] = 1
        state.set_page("ticket")
        self.assertEqual(state["surprise_key"], 1)
        self.assertEqual(state.undeclared_keys(), ["surprise_key"])

    def test_dict_compatibility(self):
        state = self.make_state()
        state.update({"played_sound_ticket": False})
        self.assertFalse(state["played_sound_ticket"])
        self.assertEqual(state.get("missing", 7), 7)
        self.assertEqual(state.setdefault("played_sound_ticket", True), False)
        self.assertEqual(state.pop("played_sound_ticket"), False)
        self.assertNotIn("played_sound_ticket", state)

    def test_update_and_setdefault_writes_are_tracked(self):
        state = self.make_state()
        state.update({"via_update": 1})
        state.setdefault("via_setdefault", 2)
        self.assertEqual(state.undeclared_keys(), ["via_setdefault", "via_update"])
        state.update({"played_sound_ticket": True})
        self.assertNotIn("played_sound_ticket", state.undeclared_keys())

    def test_session_prefix_family_is_declared_and_survives(self):
        state = self.make_state()
        state.declare_session_prefix("ticket_assigned_", "per-ticket markers")
        state["ticket_assigned_https://example_2_0"] = True
        state.set_page("ticket")
        state.set_page(None)
        self.assertTrue(state["ticket_assigned_https://example_2_0"])
        self.assertEqual(state.undeclared_keys(), [])

    def test_regression_cooldown_deadline_does_not_leak_across_pages(self):
        # Issue #3's review finding, replayed at the state level: a reload
        # cooldown written on the area page must not exist on the ticket page.
        state = self.make_state()
        state.set_page("tixcraft_area")
        state["area_reload_cooldown_until"] = 9999999999.0
        state.set_page("ticket")
        self.assertNotIn("area_reload_cooldown_until", state)


if __name__ == "__main__":
    unittest.main()
