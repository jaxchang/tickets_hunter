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


def make_reload_config(interval=5, range_on=False, upper=0):
    return {
        "advanced": {
            "auto_reload_page_interval": interval,
            "auto_reload_page_interval_range": range_on,
            "auto_reload_page_interval_max": upper,
        }
    }


class ReloadIntervalViewTest(unittest.TestCase):
    def test_interval_returned_as_is_without_range(self):
        view = config_views.reload_interval(make_reload_config(interval=7))
        self.assertEqual(view, 7)

    def test_zero_means_reload_disabled(self):
        view = config_views.reload_interval(make_reload_config(interval=0))
        self.assertEqual(view, 0)

    def test_missing_key_uses_default(self):
        self.assertEqual(config_views.reload_interval({"advanced": {}}), 0)
        self.assertEqual(
            config_views.reload_interval({"advanced": {}}, default=5), 5
        )
        self.assertEqual(config_views.reload_interval({}), 0)

    def test_valid_range_draws_within_bounds(self):
        config = make_reload_config(interval=2, range_on=True, upper=6)
        for _ in range(50):
            view = config_views.reload_interval(config)
            self.assertGreaterEqual(view, 2)
            self.assertLessEqual(view, 6)
            self.assertEqual(view, round(view, 2))

    def test_range_with_interval_not_below_upper_returns_interval(self):
        config = make_reload_config(interval=6, range_on=True, upper=6)
        self.assertEqual(config_views.reload_interval(config), 6)
        config = make_reload_config(interval=0, range_on=True, upper=6)
        self.assertEqual(config_views.reload_interval(config), 0)

    def test_range_with_non_numeric_values_returns_interval(self):
        config = make_reload_config(interval="5", range_on=True, upper=6)
        self.assertEqual(config_views.reload_interval(config), "5")
        config = make_reload_config(interval=2, range_on=True, upper="6")
        self.assertEqual(config_views.reload_interval(config), 2)

    def test_range_with_bool_values_returns_interval(self):
        config = make_reload_config(interval=True, range_on=True, upper=6)
        self.assertIs(config_views.reload_interval(config), True)

    def test_view_reflects_in_place_updates(self):
        config = make_reload_config(interval=5)
        self.assertEqual(config_views.reload_interval(config), 5)
        config["advanced"]["auto_reload_page_interval"] = 9
        self.assertEqual(config_views.reload_interval(config), 9)


if __name__ == "__main__":
    unittest.main()
