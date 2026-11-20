"""Dialog-handler lifecycle tests (issue #72).

zendriver's Connection.add_handler appends without dedup and handlers
live on the tab connection for its whole lifetime, so a per-call
registration that is never removed accumulates over a long run -- and
an event-wide remove_handlers deletes other features' handlers too.
These tests pin the lifecycle contract for the two per-call dialog
handlers, using the fake tab's handler registry:

- ibon's captcha-submit handler removes only itself; the process-wide
  alert handler registered before it must still be there afterwards.
- ticketmaster's captcha handler is removed when the captcha call
  returns, however often the call repeats.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from zendriver import cdp  # noqa: E402

from platforms import ibon, ticketmaster, tixcraft_shared  # noqa: E402

from fake_tab import FakeTab  # noqa: E402

DIALOG = cdp.page.JavascriptDialogOpening


async def _global_alert_handler(event):
    pass


def quiet_config(**extra):
    config = {"advanced": {"verbose": False}}
    config.update(extra)
    return config


class IbonKeyinDialogHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ibon._state.clear()

    async def test_submit_removes_own_handler_and_keeps_global(self):
        tab = FakeTab(url="https://ticket.ibon.com.tw/EventBuy")
        tab.add_handler(DIALOG, _global_alert_handler)
        tab.register_element('input[placeholder*="驗證碼"]')
        tab.add_evaluate_response("offsetParent", True)        # input visible
        tab.add_evaluate_response("rwdtable", True)            # ticket selected
        tab.add_evaluate_response("ImageCode_Verify2", True)   # submit clicked

        editing, submitted = await ibon.nodriver_ibon_keyin_captcha_code(
            tab, answer="1234", auto_submit=True, config_dict=quiet_config())

        self.assertTrue(submitted)
        self.assertEqual(tab.handlers_for(DIALOG), [_global_alert_handler])

    async def test_repeated_submits_do_not_accumulate(self):
        tab = FakeTab(url="https://ticket.ibon.com.tw/EventBuy")
        tab.add_handler(DIALOG, _global_alert_handler)
        tab.register_element('input[placeholder*="驗證碼"]')
        tab.add_evaluate_response("offsetParent", True)
        tab.add_evaluate_response("rwdtable", True)
        tab.add_evaluate_response("ImageCode_Verify2", True)

        for _ in range(3):
            await ibon.nodriver_ibon_keyin_captcha_code(
                tab, answer="1234", auto_submit=True, config_dict=quiet_config())

        self.assertEqual(tab.handlers_for(DIALOG), [_global_alert_handler])


class TicketmasterCaptchaDialogHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tixcraft_shared._state.clear()

    async def test_captcha_calls_do_not_accumulate_handlers(self):
        tab = FakeTab(url="https://www.ticketmaster.com/ticket/check-captcha/x")
        tab.add_handler(DIALOG, _global_alert_handler)
        # OCR disabled: the function registers its dialog handler, takes
        # the manual-input branch and returns -- the handler must be
        # gone again afterwards, on every call.
        config = quiet_config(ocr_captcha={"enable": False})

        for _ in range(3):
            result = await ticketmaster.nodriver_ticketmaster_captcha(
                tab, config, ocr=None, captcha_browser=None)
            self.assertFalse(result)

        self.assertEqual(tab.handlers_for(DIALOG), [_global_alert_handler])


if __name__ == "__main__":
    unittest.main()
