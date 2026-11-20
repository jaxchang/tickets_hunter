"""Cloudflare Layer 3 verdict tests (issue #90).

detect_cloudflare_challenge is driven with a FakeTab: Layer 1's
tab.send is absent on the fake, so its except falls through, and
Layer 2's DOM query finds nothing, so every case here exercises
Layer 3 (the full-page keyword fallback).

Characterization (passes before and after): no keyword verdict, no
detection. New behaviour (fails on the old code): the Layer 3 scan
runs in-page and only a boolean crosses back, so get_content is
never called -- the positive pins live on the evaluate seam because
the verdict source is what this change moves.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import nodriver_common  # noqa: E402

from fake_tab import FakeTab  # noqa: E402


class CloudflareLayer3CharacterizationTest(unittest.IsolatedAsyncioTestCase):
    async def test_clean_page_not_detected(self):
        tab = FakeTab(url="https://example.com/")
        tab.set_content("<html><body>concert tickets</body></html>")
        result = await nodriver_common.detect_cloudflare_challenge(tab)
        self.assertFalse(result)

    async def test_empty_page_not_detected(self):
        tab = FakeTab(url="https://example.com/")
        tab.set_content("")
        result = await nodriver_common.detect_cloudflare_challenge(tab)
        self.assertFalse(result)


class CloudflareLayer3InPageTest(unittest.IsolatedAsyncioTestCase):
    async def test_keyword_verdict_detected_without_get_content(self):
        tab = FakeTab(url="https://example.com/")
        tab.set_content(
            "<html><body>Checking your browser before accessing</body></html>")
        tab.add_evaluate_response("outerHTML", True)
        result = await nodriver_common.detect_cloudflare_challenge(tab)
        self.assertTrue(result)
        self.assertEqual(tab.get_content_calls, 0)

    async def test_evaluate_false_wins_over_content(self):
        tab = FakeTab(url="https://example.com/")
        tab.set_content(
            "<html><body>Checking your browser before accessing</body></html>")
        tab.add_evaluate_response("outerHTML", False)
        result = await nodriver_common.detect_cloudflare_challenge(tab)
        self.assertFalse(result)
        self.assertEqual(tab.get_content_calls, 0)


if __name__ == "__main__":
    unittest.main()
