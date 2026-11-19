"""Unit tests for the config views (issue #30, ADR 0004).

Views are pure functions of the raw config_dict, so these tests need
no browser seam: they pin each field's source key, its default, the
keyword_exclude pass-through (missing stays None), and the two
properties the main loop relies on -- a view is computed fresh from
the current dict on every call, and it never mutates its input.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import config_views  # noqa: E402


def make_config():
    return {
        "date_auto_select": {
            "enable": True,
            "mode": "from bottom to top",
            "date_keyword": "  2026/01/02  ",
        },
        "keyword_exclude": "01/03",
        "date_auto_fallback": True,
    }


class DateViewTest(unittest.TestCase):
    def test_full_config_maps_each_field(self):
        view = config_views.date_view(make_config())
        self.assertEqual(view, {
            "keyword": "2026/01/02",
            "mode": "from bottom to top",
            "keyword_exclude": "01/03",
            "fallback": True,
        })

    def test_missing_section_yields_defaults(self):
        view = config_views.date_view({})
        self.assertEqual(view, {
            "keyword": "",
            "mode": "from top to bottom",
            "keyword_exclude": None,
            "fallback": False,
        })

    def test_missing_keyword_exclude_stays_none(self):
        config = make_config()
        del config["keyword_exclude"]
        view = config_views.date_view(config)
        self.assertIn("keyword_exclude", view)
        self.assertIsNone(view["keyword_exclude"])

    def test_keyword_exclude_passes_through_untouched(self):
        config = make_config()
        config["keyword_exclude"] = 123  # malformed on purpose
        view = config_views.date_view(config)
        self.assertEqual(view["keyword_exclude"], 123)

    def test_none_keyword_becomes_empty_string(self):
        config = make_config()
        config["date_auto_select"]["date_keyword"] = None
        view = config_views.date_view(config)
        self.assertEqual(view["keyword"], "")

    def test_empty_mode_falls_back_to_top_to_bottom(self):
        config = make_config()
        config["date_auto_select"]["mode"] = ""
        view = config_views.date_view(config)
        self.assertEqual(view["mode"], "from top to bottom")

    def test_fallback_defaults_to_false(self):
        config = make_config()
        del config["date_auto_fallback"]
        view = config_views.date_view(config)
        self.assertIs(view["fallback"], False)

    def test_view_is_computed_fresh_from_current_dict(self):
        # reload_config() mutates the dict in place between passes;
        # a later call must see the new values (no snapshot/cache).
        config = make_config()
        first = config_views.date_view(config)
        config["date_auto_select"]["date_keyword"] = "2027/12/31"
        second = config_views.date_view(config)
        self.assertEqual(first["keyword"], "2026/01/02")
        self.assertEqual(second["keyword"], "2027/12/31")

    def test_view_does_not_mutate_input(self):
        config = make_config()
        snapshot = copy.deepcopy(config)
        config_views.date_view(config)
        self.assertEqual(config, snapshot)


if __name__ == "__main__":
    unittest.main()
