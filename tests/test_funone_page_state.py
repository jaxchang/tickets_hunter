"""PageState declaration tests for funone (issue #52).

The main function used to reset ten flags by hand whenever the page
type changed. An evidence pass showed nine of them live entirely
inside one page type, so they are now declared with declare_page and
cleared by set_page; the tenth (max_retry_logged) was dead and is
removed. These tests pin the declarations and the clearing behaviour
against funone's real _state object.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from platforms import funone  # noqa: E402

PAGE_KEYS = {
    "waiting_page_logged": "waiting",
    "next_button_clicked": "activity_detail",
    "refresh_retry_count": "ticket_flow",
    "last_sold_out_logged": "ticket_flow",
    "qty_sold_out_refreshing": "ticket_flow",
    "ocr_retry_count": "ticket_flow",
    "ocr_exhausted": "ticket_flow",
    "waiting_captcha_printed": "ticket_flow",
    "captcha_filled_printed": "ticket_flow",
}


class FunonePageStateTest(unittest.TestCase):
    def setUp(self):
        self._snapshot = dict(funone._state)
        self._page = funone._state.current_page

    def tearDown(self):
        funone._state.clear()
        funone._state.update(self._snapshot)
        funone._state.set_page(self._page)

    def test_keys_declared_for_their_pages(self):
        for key, page in PAGE_KEYS.items():
            self.assertEqual(funone._state._page_of_key.get(key), page, key)

    def test_dead_key_is_gone(self):
        self.assertNotIn("max_retry_logged", funone._state._page_of_key)
        self.assertNotIn("max_retry_logged", funone._state._session_reasons)

    def test_page_change_clears_other_pages_keys(self):
        state = funone._state
        state.set_page("ticket_flow")
        for key in PAGE_KEYS:
            state[key] = True
        state.set_page("waiting")
        self.assertTrue(state.get("waiting_page_logged"))
        for key in PAGE_KEYS:
            if key != "waiting_page_logged":
                self.assertNotIn(key, state, key)
        state.set_page(None)
        self.assertNotIn("waiting_page_logged", state)

    def test_same_page_keeps_keys(self):
        state = funone._state
        state.set_page("ticket_flow")
        state["ocr_retry_count"] = 3
        state.set_page("ticket_flow")
        self.assertEqual(state.get("ocr_retry_count"), 3)


if __name__ == "__main__":
    unittest.main()
