"""Surface tests for the tixcraft family split (issue #48, ADR 0006).

The split moved the dependency-closed shared layer into
``platforms/tixcraft_shared.py`` and the Ticketmaster family into
``platforms/ticketmaster.py``; ``platforms/tixcraft.py`` stays the
facade and keeps routing both families from its single registry entry.
These tests pin the stability contract: the facade's ``__all__`` keeps
resolving, the ``_state`` object is one and the same object across all
three modules, the moved names are the same objects, and the sibling
modules never import back into the facade.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from platforms import ticketmaster, tixcraft, tixcraft_shared  # noqa: E402


class TixcraftSurfaceTest(unittest.TestCase):
    def test_facade_all_resolves(self):
        missing = [name for name in tixcraft.__all__ if not hasattr(tixcraft, name)]
        self.assertEqual(missing, [])

    def test_state_is_one_object(self):
        self.assertIs(tixcraft._state, tixcraft_shared._state)
        self.assertIs(ticketmaster._state, tixcraft_shared._state)

    def test_entry_points_at_facade_main(self):
        for entry in tixcraft.PLATFORM_ENTRIES:
            self.assertIs(entry.handler, tixcraft.nodriver_tixcraft_main)

    def test_moved_names_are_the_same_objects(self):
        self.assertIs(
            tixcraft.nodriver_ticketmaster_date_auto_select,
            ticketmaster.nodriver_ticketmaster_date_auto_select,
        )
        self.assertIs(
            tixcraft.nodriver_ticketmaster_captcha,
            ticketmaster.nodriver_ticketmaster_captcha,
        )
        self.assertIs(
            tixcraft.nodriver_tixcraft_auto_ocr, tixcraft_shared.nodriver_tixcraft_auto_ocr
        )
        self.assertIs(
            tixcraft.nodriver_tixcraft_input_check_code,
            tixcraft_shared.nodriver_tixcraft_input_check_code,
        )

    def test_siblings_do_not_import_the_facade(self):
        src_dir = os.path.join(os.path.dirname(__file__), "..", "src", "platforms")
        for module in ("tixcraft_shared", "ticketmaster"):
            with open(os.path.join(src_dir, module + ".py"), encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("from platforms.tixcraft import", text)
            self.assertNotIn("import platforms.tixcraft\n", text)


if __name__ == "__main__":
    unittest.main()
