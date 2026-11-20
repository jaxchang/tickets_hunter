"""Tests for the keyword_exclude parse cache (issue #78).

util.is_row_match_keyword used to json.loads the same exclude string
on every call -- and it is called per row, per pass, from ~30 sites
plus the date-selection core. The parse is now cached by string.
These tests pin the predicate's behaviour (unchanged) and the
parse-once property.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import util  # noqa: E402


def clear_parse_cache():
    helper = getattr(util, "_parse_keyword_exclude_array", None)
    if helper is not None and hasattr(helper, "cache_clear"):
        helper.cache_clear()


class IsRowMatchKeywordBehaviourTest(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def test_empty_keyword_matches_all(self):
        self.assertTrue(util.is_row_match_keyword("", "任何文字"))

    def test_single_item_match_and_miss(self):
        keywords = '"VIP","搖滾區"'
        self.assertTrue(util.is_row_match_keyword(keywords, "搖滾區門票"))
        self.assertFalse(util.is_row_match_keyword(keywords, "一般區門票"))

    def test_multi_word_item_requires_all_words(self):
        keywords = '"搖滾 區"'
        self.assertTrue(util.is_row_match_keyword(keywords, "搖滾 區"))
        self.assertFalse(util.is_row_match_keyword(keywords, "搖滾"))

    def test_invalid_json_matches_nothing(self):
        self.assertFalse(util.is_row_match_keyword("not json", "任何文字"))

    def test_wrapper_reads_config_key(self):
        config = {"keyword_exclude": '"身障"'}
        self.assertTrue(
            util.reset_row_text_if_match_keyword_exclude(config, "身障席"))
        self.assertFalse(
            util.reset_row_text_if_match_keyword_exclude(config, "一般席"))
        self.assertFalse(
            util.reset_row_text_if_match_keyword_exclude(
                {"keyword_exclude": ""}, "身障席"))


class ParseOnceTest(unittest.TestCase):
    def setUp(self):
        clear_parse_cache()

    def test_same_string_parsed_once_across_calls(self):
        keywords = '"解析計數甲","解析計數乙"'
        real_loads = util.json.loads
        calls = []

        def counting_loads(text, *args, **kwargs):
            calls.append(text)
            return real_loads(text, *args, **kwargs)

        with mock.patch.object(util.json, "loads", counting_loads):
            for _ in range(5):
                util.is_row_match_keyword(keywords, "解析計數甲門票")
            util.is_row_match_keyword('"另一條字串"', "解析計數甲門票")

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
