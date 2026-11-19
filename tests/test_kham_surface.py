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

from platforms import kham, kham_rwd, kham_shared  # noqa: E402


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

    def test_siblings_do_not_import_the_facade(self):
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src", "platforms")
        for module in ("kham_shared", "kham_rwd"):
            with open(os.path.join(src_dir, module + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("from platforms.kham import", text)
            self.assertNotIn("import platforms.kham\n", text)


if __name__ == "__main__":
    unittest.main()
