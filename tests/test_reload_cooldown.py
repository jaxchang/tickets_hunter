# Unit tests for util.advance_reload_cooldown (issue #3).
# Run from the repo root: python -m unittest discover -s tests
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import util  # noqa: E402


class AdvanceReloadCooldownTest(unittest.TestCase):
    def test_no_cooldown_recorded_starts_one_without_reloading(self):
        self.assertEqual(util.advance_reload_cooldown(100.0, 0, 10), (False, 110.0))
        self.assertEqual(util.advance_reload_cooldown(100.0, None, 10), (False, 110.0))

    def test_cooldown_still_running_keeps_waiting(self):
        self.assertEqual(util.advance_reload_cooldown(105.0, 110.0, 10), (False, 110.0))

    def test_cooldown_expired_reloads_and_starts_the_next_one(self):
        self.assertEqual(util.advance_reload_cooldown(110.0, 110.0, 10), (True, 120.0))
        self.assertEqual(util.advance_reload_cooldown(115.0, 110.0, 10), (True, 125.0))

    def test_zero_or_negative_interval_disables_pacing(self):
        self.assertEqual(util.advance_reload_cooldown(100.0, 0, 0), (True, 0.0))
        self.assertEqual(util.advance_reload_cooldown(100.0, 110.0, 0), (True, 0.0))
        self.assertEqual(util.advance_reload_cooldown(100.0, 0, -5), (True, 0.0))

    def test_fractional_interval_from_range_mode(self):
        self.assertEqual(util.advance_reload_cooldown(100.0, 0, 2.5), (False, 102.5))


if __name__ == "__main__":
    unittest.main()
