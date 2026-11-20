"""Behaviour tests for the ibon livemap poll helper (issue #74).

The fast-path used to call the synchronous CDN fetch inline in the
async main, freezing the event loop for one HTTP round-trip on every
pass. _ibon_livemap_poll replaces it with a single-flight background
fetch (asyncio.to_thread, ADR 0008) plus a per-performance cache.
These tests inject fake fetch functions and pin the contract:

- the first poll for a performance returns None and starts a fetch;
- while a fetch is in flight no second fetch starts, and polls keep
  returning the previous result;
- once a fetch lands, polls return its areas (and a refresh starts);
- a performance change never returns the old performance's areas,
  and clears the livemap retry memory;
- a failed fetch neither raises nor poisons the cache.
"""

import asyncio
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from platforms import ibon  # noqa: E402

AREAS_A = [{"area_id": "a1", "area_name": "A區", "remaining": 10}]
AREAS_B = [{"area_id": "b1", "area_name": "B區", "remaining": 5}]


async def settle(rounds=50):
    """Yield to the loop until background threads have had time to land."""
    for _ in range(rounds):
        await asyncio.sleep(0.01)


class IbonLivemapPollTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ibon._state.clear()
        ibon._ensure_state()
        self._orig_fetch = ibon.ibon_fetch_and_parse_livemap
        self.calls = []

    def tearDown(self):
        ibon.ibon_fetch_and_parse_livemap = self._orig_fetch

    def install_fetch(self, fn):
        def recording(perf_id, debug=None):
            self.calls.append(perf_id)
            return fn(perf_id)
        ibon.ibon_fetch_and_parse_livemap = recording

    async def test_first_poll_starts_fetch_then_serves_cache(self):
        self.install_fetch(lambda perf_id: list(AREAS_A))

        self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
        await settle()
        self.assertEqual(self.calls, ["perfA"])

        self.assertEqual(ibon._ibon_livemap_poll("perfA"), AREAS_A)
        # Serving the cache also kicked off a refresh fetch.
        await settle()
        self.assertEqual(self.calls, ["perfA", "perfA"])

    async def test_single_flight_while_fetch_in_flight(self):
        gate = threading.Event()

        def blocking(perf_id):
            gate.wait(timeout=5)
            return list(AREAS_A)

        self.install_fetch(blocking)
        try:
            self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
            await settle(rounds=5)  # let the fetch start and block
            self.assertEqual(self.calls, ["perfA"])
            self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
            self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
            self.assertEqual(self.calls, ["perfA"])
        finally:
            gate.set()

        await settle()
        self.assertEqual(ibon._ibon_livemap_poll("perfA"), AREAS_A)

    async def test_perf_change_isolates_cache_and_clears_retry_memory(self):
        self.install_fetch(
            lambda perf_id: list(AREAS_A) if perf_id == "perfA" else list(AREAS_B))

        self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
        await settle()
        self.assertEqual(ibon._ibon_livemap_poll("perfA"), AREAS_A)

        ibon._state["livemap_failed_areas"] = {"perfA": {"a1"}}
        ibon._state["livemap_last_attempt"] = {"perf_id": "perfA", "area_id": "a1"}

        # The new performance must not see perfA's areas or retry memory.
        self.assertIsNone(ibon._ibon_livemap_poll("perfB"))
        self.assertEqual(ibon._state["livemap_failed_areas"], {})
        self.assertIsNone(ibon._state["livemap_last_attempt"])

        await settle()
        self.assertEqual(ibon._ibon_livemap_poll("perfB"), AREAS_B)

    async def test_failed_fetch_returns_none_and_retries_next_poll(self):
        def boom(perf_id):
            raise RuntimeError("network down")

        self.install_fetch(boom)
        self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
        await settle()
        # No exception, no cached result, and a new fetch was started.
        self.assertIsNone(ibon._ibon_livemap_poll("perfA"))
        await settle()
        self.assertGreaterEqual(len(self.calls), 2)
        self.assertIsNone(ibon._state.get("livemap_cache"))


if __name__ == "__main__":
    unittest.main()
