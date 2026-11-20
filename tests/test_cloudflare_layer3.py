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


class CloudflareInterstitialMarkerTest(unittest.IsolatedAsyncioTestCase):
    """Full-page interstitial markers (issue #102).

    The 2026-11-20 ibon incident: a full-page Cloudflare interstitial
    (title "請稍候...", body "驗證您是否是人類") has no Turnstile
    iframe for Layers 1-2 and none of Layer 3's English HTML keywords,
    so the main loop never saw it and ibon's local 15s guard cycled
    forever. The verdict still comes from the in-page evaluate, so
    the pins live on the marker constants and on the script the
    detector builds from them.
    """

    async def _layer3_script(self):
        tab = FakeTab(url="https://example.com/")
        tab.set_content("<html><body>concert tickets</body></html>")
        await nodriver_common.detect_cloudflare_challenge(tab)
        scripts = [s for s in tab.evaluate_calls if "outerHTML" in str(s)]
        self.assertEqual(len(scripts), 1)
        return scripts[0]

    def test_html_indicators_include_challenge_platform_path(self):
        self.assertIn("/cdn-cgi/challenge-platform/",
                      nodriver_common.CLOUDFLARE_HTML_INDICATORS)

    def test_title_and_text_marker_sets(self):
        self.assertEqual(nodriver_common.CLOUDFLARE_TITLE_MARKERS, ["請稍候"])
        self.assertEqual(nodriver_common.CLOUDFLARE_TEXT_MARKERS, ["驗證您是否是人類"])
        # The generic "正在驗證" alone is deliberately not a marker:
        # too broad for a detector whose false positive parks the bot.
        self.assertNotIn("正在驗證", nodriver_common.CLOUDFLARE_TEXT_MARKERS)

    async def test_script_scans_title_and_inner_text(self):
        script = await self._layer3_script()
        self.assertIn("document.title", script)
        self.assertIn("innerText", script)
        self.assertIn("請稍候", script)
        self.assertIn("驗證您是否是人類", script)
        self.assertIn("/cdn-cgi/challenge-platform/", script)


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
