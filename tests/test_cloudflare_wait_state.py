"""Cloudflare wait state: gate decisions for the main loop (issue #100).

When a Cloudflare challenge resists the automatic handler's per-URL
budget, the main loop must stop dispatching platform handlers onto
the challenge page and wait for a human to clear it (upstream issue
#433: continuing to operate through the challenge risks the site's
rate limits). The gate state machine lives in nodriver_common so it
is testable without a browser; the main loop only wires its actions
(poll detect, skip dispatch, resume). Cadences are in main-loop
passes (~50ms each): poll ~3s, reminder ~60s, periodic re-check
~10s.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import nodriver_common as nc  # noqa: E402

URL = "https://ticket.ibon.com.tw/Event/B86P2Q9Z"


class PeriodicCheckTests(unittest.TestCase):
    def test_not_due_before_interval(self):
        state = nc.new_cf_wait_state()
        self.assertFalse(nc.cf_periodic_check_due(state, nc.CF_PERIODIC_CHECK_PASSES - 1))

    def test_due_at_interval(self):
        state = nc.new_cf_wait_state()
        self.assertTrue(nc.cf_periodic_check_due(state, nc.CF_PERIODIC_CHECK_PASSES))

    def test_never_due_while_waiting(self):
        state = nc.new_cf_wait_state()
        nc.cf_enter_wait(state, URL, 0)
        self.assertFalse(nc.cf_periodic_check_due(state, 10 * nc.CF_PERIODIC_CHECK_PASSES))

    def test_never_due_on_excluded_pages(self):
        # Login pages (cityline/kktix): the form Turnstile belongs to
        # the sign-in flow; the periodic check must not resurrect the
        # generic detection the exclusion suppresses.
        state = nc.new_cf_wait_state()
        self.assertFalse(nc.cf_periodic_check_due(
            state, 10 * nc.CF_PERIODIC_CHECK_PASSES, excluded=True))

    def test_mark_periodic_restarts_the_interval(self):
        state = nc.new_cf_wait_state()
        state["last_periodic_pass"] = 500
        self.assertFalse(nc.cf_periodic_check_due(state, 500 + nc.CF_PERIODIC_CHECK_PASSES - 1))
        self.assertTrue(nc.cf_periodic_check_due(state, 500 + nc.CF_PERIODIC_CHECK_PASSES))


class WaitStepTests(unittest.TestCase):
    def test_not_waiting_is_noop(self):
        state = nc.new_cf_wait_state()
        self.assertEqual(nc.cf_wait_step(state, URL, 100), (None, False))

    def test_url_change_exits(self):
        state = nc.new_cf_wait_state()
        nc.cf_enter_wait(state, URL, 100)
        action, _ = nc.cf_wait_step(state, "https://ticket.ibon.com.tw/", 101)
        self.assertEqual(action, "exit_url")

    def test_waits_without_polling_before_poll_pass(self):
        state = nc.new_cf_wait_state()
        nc.cf_enter_wait(state, URL, 100)
        action, reminder = nc.cf_wait_step(state, URL, 100 + nc.CF_WAIT_POLL_PASSES - 1)
        self.assertEqual(action, "wait")
        self.assertFalse(reminder)

    def test_poll_at_poll_pass_and_cadence_advances(self):
        state = nc.new_cf_wait_state()
        nc.cf_enter_wait(state, URL, 100)
        first_poll = 100 + nc.CF_WAIT_POLL_PASSES
        action, _ = nc.cf_wait_step(state, URL, first_poll)
        self.assertEqual(action, "poll")
        # Next poll is a full interval later, not on the very next pass.
        action, _ = nc.cf_wait_step(state, URL, first_poll + 1)
        self.assertEqual(action, "wait")
        action, _ = nc.cf_wait_step(state, URL, first_poll + nc.CF_WAIT_POLL_PASSES)
        self.assertEqual(action, "poll")

    def test_reminder_due_only_at_reminder_pass(self):
        state = nc.new_cf_wait_state()
        nc.cf_enter_wait(state, URL, 0)
        # Polls before the reminder pass never carry a reminder.
        for step in range(1, nc.CF_WAIT_REMINDER_PASSES // nc.CF_WAIT_POLL_PASSES):
            _, reminder = nc.cf_wait_step(state, URL, step * nc.CF_WAIT_POLL_PASSES)
            self.assertFalse(reminder)
        _, reminder = nc.cf_wait_step(state, URL, nc.CF_WAIT_REMINDER_PASSES)
        self.assertTrue(reminder)
        # And the following poll is quiet again.
        _, reminder = nc.cf_wait_step(
            state, URL, nc.CF_WAIT_REMINDER_PASSES + nc.CF_WAIT_POLL_PASSES)
        self.assertFalse(reminder)

    def test_exit_wait_clears_state(self):
        state = nc.new_cf_wait_state()
        nc.cf_enter_wait(state, URL, 100)
        nc.cf_exit_wait(state)
        self.assertFalse(state["waiting"])
        self.assertEqual(nc.cf_wait_step(state, URL, 200), (None, False))
        # Periodic checks resume after the wait.
        state["last_periodic_pass"] = 0
        self.assertTrue(nc.cf_periodic_check_due(state, nc.CF_PERIODIC_CHECK_PASSES))


class ReappearanceTests(unittest.TestCase):
    """Fake-success escalation (issue #104): the handler can report a
    solve whose proof (the response field vanishing) does not stick --
    the widget resets and the challenge is detected again on the same
    URL. Reappearances after reported solves are counted; at the
    limit the caller stops handling and enters the wait for a human,
    whose clicks do pass."""

    def test_detection_without_prior_solve_not_counted(self):
        state = nc.new_cf_wait_state()
        self.assertFalse(nc.cf_note_detected(state, URL))
        self.assertEqual(state["reappearances"], 0)

    def test_first_reappearance_below_limit(self):
        state = nc.new_cf_wait_state()
        nc.cf_note_solved(state, URL)
        self.assertFalse(nc.cf_note_detected(state, URL))
        self.assertEqual(state["reappearances"], 1)

    def test_limit_reached_signals_wait(self):
        state = nc.new_cf_wait_state()
        nc.cf_note_solved(state, URL)
        for _ in range(nc.CF_REAPPEARANCE_LIMIT - 1):
            self.assertFalse(nc.cf_note_detected(state, URL))
            # A same-URL solve between detections must not reset the count.
            nc.cf_note_solved(state, URL)
        self.assertTrue(nc.cf_note_detected(state, URL))

    def test_detection_on_other_url_not_counted(self):
        state = nc.new_cf_wait_state()
        nc.cf_note_solved(state, URL)
        self.assertFalse(nc.cf_note_detected(state, "https://example.com/other"))
        self.assertEqual(state["reappearances"], 0)

    def test_clean_detection_resets_tracking(self):
        state = nc.new_cf_wait_state()
        nc.cf_note_solved(state, URL)
        nc.cf_note_detected(state, URL)
        nc.cf_note_clean(state)
        self.assertEqual(state["reappearances"], 0)
        nc.cf_note_solved(state, URL)
        self.assertFalse(nc.cf_note_detected(state, URL))
        self.assertEqual(state["reappearances"], 1)

    def test_enter_wait_clears_tracking(self):
        state = nc.new_cf_wait_state()
        nc.cf_note_solved(state, URL)
        nc.cf_note_detected(state, URL)
        nc.cf_enter_wait(state, URL, 0)
        self.assertEqual(state["reappearances"], 0)
        self.assertEqual(state["solved_url"], "")


if __name__ == "__main__":
    unittest.main()
