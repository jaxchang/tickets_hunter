"""verify_cf_with_templates against zendriver's real signature (issue #104).

The helper was written for the old nodriver API, whose Tab.verify_cf
accepted template_image/flash; zendriver's verify_cf accepts neither,
so every call raised TypeError and the handle path's third fallback
never worked. The repaired helper makes one native verify_cf() call;
these tests pin the call shape with a stub tab.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import util  # noqa: E402


class _StubTab:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    async def verify_cf(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._error is not None:
            raise self._error


class VerifyCfWithTemplatesTest(unittest.IsolatedAsyncioTestCase):
    async def test_single_native_call_without_kwargs(self):
        tab = _StubTab()
        result = await util.verify_cf_with_templates(tab)
        self.assertTrue(result)
        self.assertEqual(tab.calls, [((), {})])

    async def test_templates_argument_accepted_and_ignored(self):
        tab = _StubTab()
        result = await util.verify_cf_with_templates(tab, templates=["cf_template_ibon.png"])
        self.assertTrue(result)
        self.assertEqual(tab.calls, [((), {})])

    async def test_failure_returns_false(self):
        tab = _StubTab(error=TypeError("unexpected keyword argument"))
        result = await util.verify_cf_with_templates(tab)
        self.assertFalse(result)
        self.assertEqual(len(tab.calls), 1)


if __name__ == "__main__":
    unittest.main()
