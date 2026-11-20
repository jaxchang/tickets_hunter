"""Behaviour tests for the OCR offload (issue #76, ADR 0008).

ddddocr inference used to run synchronously on the event loop at six
call sites, and ibon rebuilt its OCR object (reloading the ONNX
model) in four separate flows. These tests pin the replacements:

- nodriver_common.ocr_classification runs the inference on a worker
  thread: the event loop stays responsive while it is blocked, and
  the result (or exception) passes through unchanged.
- ibon._ibon_get_ocr builds the shared instance once and reuses it,
  for both the universal-model path and the builtin fallback.
"""

import asyncio
import os
import sys
import threading
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import nodriver_common  # noqa: E402
from platforms import ibon  # noqa: E402


class BlockingOcr:
    def __init__(self):
        self.gate = threading.Event()
        self.thread_id = None
        self.calls = 0

    def classification(self, img):
        self.calls += 1
        self.thread_id = threading.get_ident()
        self.gate.wait(timeout=5)
        return "AB12"


class OcrClassificationOffloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_inference_runs_off_loop_and_returns_result(self):
        ocr = BlockingOcr()
        task = asyncio.ensure_future(nodriver_common.ocr_classification(ocr, b"img"))
        # The event loop must stay responsive while inference is blocked.
        await asyncio.sleep(0.1)
        self.assertFalse(task.done())
        self.assertEqual(ocr.calls, 1)
        self.assertNotEqual(ocr.thread_id, threading.get_ident())

        ocr.gate.set()
        self.assertEqual(await task, "AB12")

    async def test_exception_propagates(self):
        class BoomOcr:
            def classification(self, img):
                raise ValueError("bad image")

        with self.assertRaises(ValueError):
            await nodriver_common.ocr_classification(BoomOcr(), b"img")


class FakeDdddOcr:
    instances = []

    def __init__(self, show_ad=None, beta=None, **kwargs):
        self.show_ad = show_ad
        self.beta = beta
        self.ranges = None
        FakeDdddOcr.instances.append(self)

    def set_ranges(self, ranges):
        self.ranges = ranges


class IbonGetOcrTest(unittest.TestCase):
    def setUp(self):
        ibon._state.clear()
        ibon._ensure_state()
        self._orig_factory = ibon.create_universal_ocr
        self._orig_ddddocr = sys.modules.get("ddddocr")
        FakeDdddOcr.instances = []
        fake_module = types.SimpleNamespace(DdddOcr=FakeDdddOcr)
        sys.modules["ddddocr"] = fake_module

    def tearDown(self):
        ibon.create_universal_ocr = self._orig_factory
        if self._orig_ddddocr is None:
            sys.modules.pop("ddddocr", None)
        else:
            sys.modules["ddddocr"] = self._orig_ddddocr

    def test_universal_instance_created_once(self):
        sentinel = object()
        calls = []

        def factory(config_dict):
            calls.append(config_dict)
            return sentinel

        ibon.create_universal_ocr = factory
        config = {"ocr_captcha": {"enable": True}}

        self.assertIs(ibon._ibon_get_ocr(config), sentinel)
        self.assertIs(ibon._ibon_get_ocr(config), sentinel)
        self.assertEqual(len(calls), 1)
        self.assertEqual(FakeDdddOcr.instances, [])

    def test_fallback_instance_created_once_with_ranges(self):
        ibon.create_universal_ocr = lambda config_dict: None
        config = {"ocr_captcha": {"enable": True, "beta": False}}

        first = ibon._ibon_get_ocr(config)
        second = ibon._ibon_get_ocr(config)

        self.assertIs(first, second)
        self.assertEqual(len(FakeDdddOcr.instances), 1)
        self.assertEqual(first.beta, False)
        self.assertEqual(first.ranges, 0)


if __name__ == "__main__":
    unittest.main()
