"""Full-page fetch verdict tests (issue #88).

Characterization (passes before and after): with no positive
verdict the hkticketing helpers stay put -- no redirect.

New behaviour (fails on the old code): the verdicts are computed
in-page and only a boolean crosses back, so get_content is never
called, and a positive verdict drives the same redirects as before;
travel_iframe's per-iframe scan also returns booleans. The ibon/kktix
verdict helpers are new seams for the checks that used to sit inline
in their mains. Cityline's dead fetch is gone: its main no longer
calls get_content on the event-host pages.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from nodriver_common import PlatformContext  # noqa: E402
from platforms import cityline, hkticketing, ibon, kktix  # noqa: E402

from fake_tab import FakeTab  # noqa: E402

CONFIG = {"advanced": {"verbose": False}, "keyword_exclude": "", "homepage": ""}
ENTRY_URL = "https://entry-hotshow.hkticketing.com/"
PREMIER_URL = "https://premier.hkticketing.com/"


class HkticketingCharacterizationTest(unittest.IsolatedAsyncioTestCase):
    """Pins that hold on both sides of the change: no verdict, no
    redirect. (The positive verdict -> redirect pins moved to the
    in-page seam in HkticketingInPageVerdictTest: the verdict source
    itself is what issue #88 changes, so a positive pin can only be
    written against one seam or the other.)"""

    def setUp(self):
        hkticketing._state.clear()

    async def test_url_redirect_clean_page(self):
        tab = FakeTab(url=ENTRY_URL)
        tab.set_content("<html><body>welcome</body></html>")
        result = await hkticketing.nodriver_hkticketing_url_redirect(
            tab, ENTRY_URL, CONFIG)
        self.assertFalse(result)
        self.assertEqual(tab.get_calls, [])

    async def test_content_refresh_clean_page(self):
        tab = FakeTab(url=PREMIER_URL)
        tab.set_content("<html><body>shows</body></html>")
        result = await hkticketing.nodriver_hkticketing_content_refresh(
            tab, PREMIER_URL, CONFIG)
        self.assertFalse(result)
        self.assertEqual(tab.get_calls, [])

    async def test_travel_iframe_all_clean(self):
        tab = FakeTab(url="https://www.hkticketing.com/queue")
        tab.add_evaluate_response("querySelectorAll('iframe').length", 2)
        tab.add_evaluate_response("iframe')[0]", False)
        tab.add_evaluate_response("iframe')[1]", False)
        result = await hkticketing.nodriver_hkticketing_travel_iframe(
            tab, CONFIG)
        self.assertFalse(result)
        self.assertEqual(tab.get_calls, [])


class HkticketingInPageVerdictTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hkticketing._state.clear()

    async def test_url_redirect_uses_evaluate_not_get_content(self):
        tab = FakeTab(url=ENTRY_URL)
        tab.set_content("<html><body>Access denied (403)</body></html>")
        tab.add_evaluate_response("outerHTML", True)
        result = await hkticketing.nodriver_hkticketing_url_redirect(
            tab, ENTRY_URL, CONFIG)
        self.assertTrue(result)
        self.assertEqual(tab.get_calls, ["https://hotshow.hkticketing.com/"])
        self.assertEqual(tab.get_content_calls, 0)

    async def test_url_redirect_evaluate_false_wins_over_content(self):
        tab = FakeTab(url=ENTRY_URL)
        tab.set_content("<html><body>Access denied (403)</body></html>")
        tab.add_evaluate_response("outerHTML", False)
        result = await hkticketing.nodriver_hkticketing_url_redirect(
            tab, ENTRY_URL, CONFIG)
        self.assertFalse(result)
        self.assertEqual(tab.get_content_calls, 0)

    async def test_content_refresh_uses_evaluate_not_get_content(self):
        tab = FakeTab(url=PREMIER_URL)
        tab.set_content("<html><body>Service Unavailable</body></html>")
        tab.add_evaluate_response("outerHTML", True)
        result = await hkticketing.nodriver_hkticketing_content_refresh(
            tab, PREMIER_URL, CONFIG)
        self.assertTrue(result)
        self.assertEqual(
            tab.get_calls, ["https://premier.hkticketing.com/default.aspx"])
        self.assertEqual(tab.get_content_calls, 0)

    async def test_travel_iframe_scans_in_page(self):
        tab = FakeTab(url="https://www.hkticketing.com/queue")
        tab.add_evaluate_response("querySelectorAll('iframe').length", 2)
        tab.add_evaluate_response("iframe')[0]", False)
        tab.add_evaluate_response("iframe')[1]", True)
        tab.add_evaluate_response(
            "window.location.href", "https://www.hkticketing.com/queue")
        result = await hkticketing.nodriver_hkticketing_travel_iframe(
            tab, CONFIG)
        self.assertTrue(result)
        self.assertEqual(
            tab.get_calls, ["https://www.hkticketing.com/default.aspx"])


class IbonKktixVerdictHelperTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ibon._state.clear()
        kktix._state.clear()

    async def test_ibon_name_based_verdict(self):
        tab = FakeTab(url="https://orders.ibon.com.tw/")
        tab.add_evaluate_response("outerHTML", True)
        self.assertTrue(await ibon._ibon_is_name_based_event_page(tab))
        self.assertEqual(tab.get_content_calls, 0)

    async def test_ibon_name_based_verdict_false(self):
        tab = FakeTab(url="https://orders.ibon.com.tw/")
        tab.add_evaluate_response("outerHTML", False)
        self.assertFalse(await ibon._ibon_is_name_based_event_page(tab))

    async def test_kktix_dom_ready_verdict(self):
        tab = FakeTab(url="https://kktix.com/registrations/new")
        tab.add_evaluate_response("outerHTML", True)
        self.assertTrue(await kktix._kktix_is_registrations_dom_ready(tab))
        self.assertEqual(tab.get_content_calls, 0)

    async def test_kktix_dom_ready_verdict_false(self):
        tab = FakeTab(url="https://kktix.com/registrations/new")
        tab.add_evaluate_response("outerHTML", False)
        self.assertFalse(await kktix._kktix_is_registrations_dom_ready(tab))


class CitylineDeadFetchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cityline._state.clear()

    async def test_event_host_pass_never_fetches_content(self):
        tab = FakeTab(url="https://msg.cityline.com/event/123")
        tab.set_content("<html>" + "x" * 20000 + "</html>")
        ctx = PlatformContext(
            tab=tab, url=tab.url, config_dict=dict(CONFIG),
            ocr=None, captcha_browser=None)
        await cityline.nodriver_cityline_main(ctx)
        self.assertEqual(tab.get_content_calls, 0)


if __name__ == "__main__":
    unittest.main()
