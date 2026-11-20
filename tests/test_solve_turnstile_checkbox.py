"""solve_turnstile_checkbox layout guard (issue #104).

A Turnstile iframe that has not laid out yet reports a 0x0 box; the
2026-11-20 ibon run clicked those blind coordinates and burned a
full token wait on a click that could not land. Widgets below the
layout threshold must not be clicked -- the attempt's other methods
or the handler's retry get a laid-out widget shortly after.
"""

import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import nodriver_common as nc  # noqa: E402


def _tab_with_box(quad):
    box = types.SimpleNamespace(content=quad)
    tab = mock.Mock()
    # send() sequence: get_document, scroll_into_view_if_needed, get_box_model
    tab.send = mock.AsyncMock(side_effect=[object(), None, box])
    return tab


class SolveTurnstileCheckboxSizeTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_size_widget_not_clicked(self):
        tab = _tab_with_box([0, 0, 0, 0, 0, 0, 0, 0])
        with mock.patch.object(nc, "_find_cf_iframe_in_dom", return_value=(33, "src")), \
                mock.patch.object(nc, "cdp_click_at", new=mock.AsyncMock()) as click:
            result = await nc.solve_turnstile_checkbox(tab)
        self.assertFalse(result)
        click.assert_not_called()

    async def test_normal_widget_clicked(self):
        # 300x65 widget at (100, 400)
        tab = _tab_with_box([100, 400, 400, 400, 400, 465, 100, 465])
        with mock.patch.object(nc, "_find_cf_iframe_in_dom", return_value=(33, "src")), \
                mock.patch.object(nc, "cdp_click_at", new=mock.AsyncMock()) as click:
            result = await nc.solve_turnstile_checkbox(tab)
        self.assertTrue(result)
        click.assert_called_once()


if __name__ == "__main__":
    unittest.main()
