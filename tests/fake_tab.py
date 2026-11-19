"""Fake tab/element test doubles for handler-level tests (issue #20).

Handlers under ``src/platforms/`` take a zendriver tab, which normally
needs a real browser. These doubles are the seam that lets tests drive a
handler directly: the fake tab records what the handler did (reloads,
sleeps, evaluated scripts) and plays back canned page state that the
test registered up front.

Rules of the seam:

- Stub, not simulator. No HTML parsing, no JavaScript execution. A test
  registers exactly the elements and evaluate() replies its scenario
  needs; anything unregistered answers with the empty default a
  sold-out page would give.
- Grow from real scenarios only. The surface below is exactly what the
  existing scenarios use. When a new test needs a tab or element method
  that is not here, add the smallest recording/playback version of it
  in that change; do not fake CDP (``tab.send``) or browser internals
  speculatively.
- State isolation: platform modules keep a module-level ``_state``.
  Every handler test must clear it in ``setUp`` (for example
  ``tixcraft._state.clear()``) so tests cannot leak state into each
  other.

See ``test_tixcraft_area_cooldown.py`` for a worked example.
"""


class FakeElement:
    """A canned page element: children registered per selector."""

    def __init__(self, html="", attrs=None):
        self._html = html
        self.attrs = dict(attrs) if attrs else {}
        self._children = {}
        self._children_all = {}
        self.click_calls = 0

    def register_child(self, selector, element=None):
        """Make ``query_selector(selector)`` return ``element``.

        Creates and returns a fresh FakeElement when none is given.
        """
        if element is None:
            element = FakeElement()
        self._children[selector] = element
        return element

    def register_children(self, selector, elements):
        """Make ``query_selector_all(selector)`` return ``elements``."""
        self._children_all[selector] = list(elements)
        return elements

    async def query_selector(self, selector):
        return self._children.get(selector)

    async def query_selector_all(self, selector):
        return list(self._children_all.get(selector, []))

    async def get_html(self):
        return self._html

    async def update(self):
        # zendriver refreshes the element's attributes; the fake's
        # canned attrs are already final.
        pass

    async def click(self):
        self.click_calls += 1


class FakeTab:
    """A canned browser tab: selector/evaluate registries plus counters."""

    def __init__(self, url="about:blank"):
        self.url = url
        self.get_calls = []
        self.reload_calls = 0
        self.sleep_calls = []
        self.evaluate_calls = []
        self._elements = {}
        self._elements_all = {}
        self._evaluate_responses = []

    def register_element(self, selector, element=None):
        """Make ``query_selector(selector)`` return ``element``.

        Creates and returns a fresh FakeElement when none is given, so
        tests can chain: ``zone = tab.register_element('.zone')``.
        """
        if element is None:
            element = FakeElement()
        self._elements[selector] = element
        return element

    def add_evaluate_response(self, match, result):
        """Reply ``result`` to evaluate() scripts containing ``match``.

        Responses are tried in registration order; the first substring
        match wins. Unmatched scripts return ``[]``.
        """
        self._evaluate_responses.append((match, result))

    def register_elements(self, selector, elements):
        """Make ``query_selector_all(selector)`` return ``elements``."""
        self._elements_all[selector] = list(elements)
        return elements

    async def query_selector(self, selector):
        return self._elements.get(selector)

    async def query_selector_all(self, selector):
        return list(self._elements_all.get(selector, []))

    async def evaluate(self, script, await_promise=False):
        self.evaluate_calls.append(script)
        for match, result in self._evaluate_responses:
            if match in script:
                return result
        return []

    async def get(self, url):
        self.get_calls.append(url)
        self.url = url

    async def reload(self):
        self.reload_calls += 1

    async def sleep(self, seconds):
        # Recorded, not performed: tests pace themselves with the real
        # clock only where the behaviour under test is time-based.
        self.sleep_calls.append(seconds)
