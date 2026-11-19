"""Unit tests for the platform interface (issue #34, ADR 0005).

The registry itself is assembled in nodriver_tixcraft.py, which the
test environment cannot import (it pulls in the full runtime), so
these tests assemble the same registry from the platform modules'
PLATFORM_ENTRIES in the documented order and pin:

- the matcher characterization table: which entries a URL dispatches
  to, in order (family groupings, the Facebook prefix, Kham's UTK RWD
  host parse, and the raw-substring / case-sensitive semantics the
  dispatcher's if-chain had);
- the registry's completeness and order;
- PlatformEntry / PlatformContext behaviour;
- the dispatcher's own assembly staying in the same order (a source
  structure check, so the two cannot drift apart silently).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import nodriver_common  # noqa: E402
from platforms import (  # noqa: E402
    cityline,
    facebook,
    famiticket,
    fansigo,
    funone,
    hkticketing,
    ibon,
    kham,
    kktix,
    ticketplus,
    tixcraft,
)

# The dispatcher's documented assembly order (nodriver_tixcraft.py).
MODULE_ORDER = [
    kktix,
    tixcraft,
    famiticket,
    ibon,
    kham,
    ticketplus,
    cityline,
    hkticketing,
    funone,
    fansigo,
    facebook,
]

EXPECTED_ENTRY_NAMES = [
    "kktix",
    "tixcraft",
    "famiticket",
    "ibon",
    "kham",
    "ticketplus",
    "cityline",
    "hkticketing",
    "funone",
    "fansigo",
    "fansigo_signin",
    "facebook",
]


def assemble_registry():
    entries = []
    for module in MODULE_ORDER:
        entries.extend(module.PLATFORM_ENTRIES)
    return entries


def dispatched_names(url):
    return [entry.name for entry in assemble_registry() if entry.matches(url)]


class RegistryShapeTest(unittest.TestCase):
    def test_entry_names_in_dispatch_order(self):
        self.assertEqual(
            [entry.name for entry in assemble_registry()], EXPECTED_ENTRY_NAMES
        )

    def test_entry_names_are_unique(self):
        names = [entry.name for entry in assemble_registry()]
        self.assertEqual(len(names), len(set(names)))

    def test_every_handler_is_callable(self):
        for entry in assemble_registry():
            self.assertTrue(callable(entry.handler), entry.name)


class MatcherTableTest(unittest.TestCase):
    def assertDispatches(self, url, expected):
        self.assertEqual(dispatched_names(url), expected, url)

    def test_kktix_domains(self):
        self.assertDispatches("https://kktix.com/events/123", ["kktix"])
        self.assertDispatches("https://kktix.cc/events/123", ["kktix"])

    def test_tixcraft_family(self):
        self.assertDispatches("https://tixcraft.com/activity/detail/1", ["tixcraft"])
        self.assertDispatches("https://www.indievox.com/activity/1", ["tixcraft"])
        self.assertDispatches("https://www.ticketmaster.com/event/1", ["tixcraft"])
        self.assertDispatches("https://ticketmaster.sg/event/1", ["tixcraft"])

    def test_famiticket_url_matches_only_famiticket(self):
        # Issue #50: Kham's "ticket.com.tw" used to be a substring
        # pattern, and "famiticket.com.tw" contains it, so Kham's main
        # also ran on FamiTicket pages. The kham entry now matches
        # ticket.com.tw by host, so FamiTicket pages dispatch only to
        # famiticket.
        self.assertDispatches("https://www.famiticket.com.tw/Home/", ["famiticket"])

    def test_single_pattern_platforms(self):
        self.assertDispatches("https://ticket.ibon.com.tw/Activity/1", ["ibon"])
        self.assertDispatches("https://ticketplus.com.tw/order/1", ["ticketplus"])
        self.assertDispatches("https://www.cityline.com/Events.html", ["cityline"])
        self.assertDispatches("https://tickets.funone.io/events/1", ["funone"])
        self.assertDispatches("https://go.fansi.me/events/1", ["fansigo"])

    def test_kham_family(self):
        self.assertDispatches("https://kham.com.tw/application/UTK02/", ["kham"])
        self.assertDispatches("https://www.ticket.com.tw/application/", ["kham"])
        self.assertDispatches("https://ticket.com.tw/application/", ["kham"])
        self.assertDispatches("https://tickets.udnfunlife.com/application/", ["kham"])

    def test_kham_utk_rwd_tenant_host(self):
        self.assertDispatches("https://tix.ctbcsports.com/", ["kham"])

    def test_softix_family(self):
        self.assertDispatches("https://premier.hkticketing.com/Secure/", ["hkticketing"])
        self.assertDispatches("https://www.galaxymacau.com/tickets/", ["hkticketing"])
        self.assertDispatches("https://premier.ticketek.com.au/shows/", ["hkticketing"])

    def test_fansigo_signin_domain(self):
        url = "https://%s/login" % fansigo.FANSIGO_COGNITO_DOMAIN
        self.assertDispatches(url, ["fansigo_signin"])

    def test_facebook_login_prefix(self):
        self.assertDispatches(
            "https://www.facebook.com/login.php?next=/", ["facebook"]
        )
        self.assertDispatches("https://www.facebook.com/", [])

    def test_no_match(self):
        self.assertDispatches("https://example.com/", [])
        self.assertDispatches("https://www.google.com/search?q=tickets", [])

    def test_matching_is_case_sensitive_on_the_raw_url(self):
        # The if-chain tested the raw URL case-sensitively; pinned as-is.
        self.assertDispatches("https://TICKETPLUS.com.tw/order/1", [])

    def test_substring_semantics_are_preserved(self):
        # A pattern anywhere in the raw URL matches, even in a query
        # string -- the if-chain behaved exactly this way.
        self.assertDispatches(
            "https://example.com/redirect?to=ticketplus.com.tw", ["ticketplus"]
        )

    def test_run_all_matches_in_registry_order(self):
        self.assertDispatches(
            "https://kktix.com/?u=ticketplus.com", ["kktix", "ticketplus"]
        )


class PlatformEntryTest(unittest.TestCase):
    def test_patterns_are_substring_or(self):
        entry = nodriver_common.PlatformEntry(
            "x", handler=None, patterns=("a.com", "b.com")
        )
        self.assertTrue(entry.matches("https://a.com/"))
        self.assertTrue(entry.matches("https://b.com/"))
        self.assertFalse(entry.matches("https://c.com/"))

    def test_match_fn_extends_patterns(self):
        entry = nodriver_common.PlatformEntry(
            "x",
            handler=None,
            patterns=("a.com",),
            match_fn=lambda url: url.startswith("https://login."),
        )
        self.assertTrue(entry.matches("https://login.example/"))
        self.assertFalse(entry.matches("https://example/"))

    def test_no_patterns_no_match_fn_never_matches(self):
        entry = nodriver_common.PlatformEntry("x", handler=None)
        self.assertFalse(entry.matches("https://a.com/"))


class PlatformContextTest(unittest.TestCase):
    def test_fields_and_defaults(self):
        tab = object()
        config = {"a": 1}
        ctx = nodriver_common.PlatformContext(tab, "https://x/", config)
        self.assertIs(ctx.tab, tab)
        self.assertEqual(ctx.url, "https://x/")
        self.assertIs(ctx.config_dict, config)
        self.assertIsNone(ctx.ocr)
        self.assertIsNone(ctx.captcha_browser)

    def test_ocr_and_captcha_browser_are_carried(self):
        ocr = object()
        browser = object()
        ctx = nodriver_common.PlatformContext(
            object(), "https://x/", {}, ocr=ocr, captcha_browser=browser
        )
        self.assertIs(ctx.ocr, ocr)
        self.assertIs(ctx.captcha_browser, browser)


class DispatcherAssemblySourceTest(unittest.TestCase):
    """nodriver_tixcraft.py must assemble and use the same registry."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(
            os.path.dirname(__file__), "..", "src", "nodriver_tixcraft.py"
        )
        with open(path, encoding="utf-8") as fh:
            cls.source = fh.read()

    def test_registry_alias_imports_in_order(self):
        aliases = [
            "_KKTIX_ENTRIES",
            "_TIXCRAFT_ENTRIES",
            "_FAMITICKET_ENTRIES",
            "_IBON_ENTRIES",
            "_KHAM_ENTRIES",
            "_TICKETPLUS_ENTRIES",
            "_CITYLINE_ENTRIES",
            "_HKTICKETING_ENTRIES",
            "_FUNONE_ENTRIES",
            "_FANSIGO_ENTRIES",
            "_FACEBOOK_ENTRIES",
        ]
        positions = [self.source.index(alias) for alias in aliases]
        self.assertEqual(positions, sorted(positions))

    def test_dispatch_iterates_the_registry(self):
        self.assertIn("for entry in PLATFORM_REGISTRY:", self.source)
        self.assertIn("entry.matches(url)", self.source)
        self.assertIn("await entry.handler(ctx)", self.source)

    def test_old_direct_dispatch_calls_are_gone(self):
        self.assertNotIn("await nodriver_kktix_main(", self.source)
        self.assertNotIn("await nodriver_tixcraft_main(", self.source)
        self.assertNotIn("await nodriver_ticketplus_main(", self.source)
        self.assertNotIn("ticketplus_purchase_done", self.source)


if __name__ == "__main__":
    unittest.main()
