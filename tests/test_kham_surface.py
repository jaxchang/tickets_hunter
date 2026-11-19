"""Surface tests for the kham family split (issue #40, ADR 0006).

The split moved the dependency-closed shared layer into
``platforms/kham_shared.py`` and the UTK RWD cluster into
``platforms/kham_rwd.py``; ``platforms/kham.py`` stays the facade.
These tests pin the stability contract: the facade's ``__all__`` keeps
resolving, the ``_state`` object is one and the same object across all
three modules, the registry entry still points at the facade's main and
the shared matcher, and the sibling modules never import back into the
facade.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from platforms import (  # noqa: E402
    kham,
    kham_rwd,
    kham_seat,
    kham_shared,
    kham_ticket_seat,
    kham_udn_seat,
)


class KhamSurfaceTest(unittest.TestCase):
    def test_facade_all_resolves(self):
        missing = [name for name in kham.__all__ if not hasattr(kham, name)]
        self.assertEqual(missing, [])

    def test_state_is_one_object(self):
        self.assertIs(kham._state, kham_shared._state)
        self.assertIs(kham_rwd._state, kham_shared._state)

    def test_entry_points_at_facade_main_and_shared_matcher(self):
        entry = kham.PLATFORM_ENTRIES[0]
        self.assertIs(entry.handler, kham.nodriver_kham_main)
        self.assertIs(entry.match_fn, kham_shared.is_utk_rwd_url)

    def test_moved_names_are_the_same_objects(self):
        self.assertIs(kham.nodriver_kham_captcha, kham_shared.nodriver_kham_captcha)
        self.assertIs(kham.is_utk_rwd_url, kham_shared.is_utk_rwd_url)
        self.assertIs(
            kham.nodriver_utk_rwd_ensure_login, kham_rwd.nodriver_utk_rwd_ensure_login
        )
        self.assertIs(
            kham.nodriver_utk_rwd_assign_ticket_number,
            kham_rwd.nodriver_utk_rwd_assign_ticket_number,
        )

    def test_seat_cluster_lives_in_kham_seat(self):
        self.assertIs(kham.nodriver_kham_seat_main, kham_seat.nodriver_kham_seat_main)
        self.assertIs(
            kham.nodriver_kham_seat_auto_select, kham_seat.nodriver_kham_seat_auto_select
        )
        self.assertIs(kham_seat._state, kham_shared._state)
        self.assertIs(
            kham_seat.CONST_UTK_RWD_EMPTY_SEAT_IMG,
            kham_shared.CONST_UTK_RWD_EMPTY_SEAT_IMG,
        )

    def test_shared_constants_rehomed(self):
        # Issue #42: the reload settle constant is shared by core and the
        # seat flow; the empty-seat image marker is consumed by seat logic
        # only, so both live in the shared layer now.
        self.assertEqual(kham_shared.CONST_KHAM_RELOAD_SETTLE, 0.5)
        self.assertEqual(kham.CONST_KHAM_RELOAD_SETTLE, kham_shared.CONST_KHAM_RELOAD_SETTLE)
        self.assertFalse(hasattr(kham_rwd, "CONST_UTK_RWD_EMPTY_SEAT_IMG"))

    def test_udn_seat_cluster_lives_in_kham_udn_seat(self):
        self.assertIs(kham.nodriver_udn_seat_main, kham_udn_seat.nodriver_udn_seat_main)
        self.assertIs(
            kham.nodriver_udn_seat_auto_select, kham_udn_seat.nodriver_udn_seat_auto_select
        )

    def test_ticket_seat_cluster_lives_in_kham_ticket_seat(self):
        self.assertIs(
            kham.nodriver_ticket_seat_main, kham_ticket_seat.nodriver_ticket_seat_main
        )
        self.assertIs(kham.nodriver_ticket_login, kham_ticket_seat.nodriver_ticket_login)

    def test_siblings_do_not_import_the_facade(self):
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src", "platforms")
        for module in (
            "kham_shared",
            "kham_rwd",
            "kham_seat",
            "kham_udn_seat",
            "kham_ticket_seat",
        ):
            with open(os.path.join(src_dir, module + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("from platforms.kham import", text)
            self.assertNotIn("import platforms.kham\n", text)


if __name__ == "__main__":
    unittest.main()
