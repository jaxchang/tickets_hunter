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
        self.query_calls = []
        self.click_calls = 0
        self.apply_calls = []
        self.send_keys_calls = []

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
        self.query_calls.append(selector)
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

    async def apply(self, script, *args):
        # Records the applied function; canned elements have no live
        # DOM state to read back, so this returns None.
        self.apply_calls.append(script)
        return None

    async def send_keys(self, text):
        self.send_keys_calls.append(text)


class FakeBrowser:
    """The tab's browser handle: just the tab list handlers inspect."""

    def __init__(self):
        self.tabs = []


class FakeTab:
    """A canned browser tab: selector/evaluate registries plus counters.

    Also keeps a CDP handler registry mirroring zendriver's
    Connection semantics (add appends without dedup; remove has the
    same three forms), so handler-lifecycle tests can assert what is
    still registered. Handlers are recorded, never invoked -- firing
    CDP events would be simulating, not stubbing.
    """

    # Mirrors zendriver's Tab, which defines __eq__ without __hash__:
    # tab objects are unhashable, so production code must key any
    # bookkeeping by id(tab), never by the tab itself (issue #96).
    __hash__ = None

    def __init__(self, url="about:blank"):
        self.url = url
        self.browser = FakeBrowser()
        self.get_calls = []
        self.reload_calls = 0
        self.sleep_calls = []
        self.evaluate_calls = []
        self.get_content_calls = 0
        self.close_calls = 0
        self.back_calls = 0
        self.back_error = None
        self.back_target_url = None
        self._content = ""
        self._elements = {}
        self._elements_all = {}
        self._elements_all_queue = {}
        self._evaluate_responses = []
        self._handlers = {}

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

    def queue_elements_all(self, selector, results):
        """Queue successive results for query_selector_all: each call
        returns the next list; the last one repeats. For polling
        scenarios where rows only appear after a few checks."""
        self._elements_all_queue[selector] = list(results)

    async def query_selector_all(self, selector):
        queue = self._elements_all_queue.get(selector)
        if queue:
            if len(queue) > 1:
                return queue.pop(0)
            return list(queue[0])
        return list(self._elements_all.get(selector, []))

    async def evaluate(self, script, await_promise=False):
        self.evaluate_calls.append(script)
        for match, result in self._evaluate_responses:
            if match in script:
                return result
        return []

    def add_handler(self, event_type, handler):
        """Mirror zendriver Connection.add_handler: append, no dedup."""
        self._handlers.setdefault(event_type, []).append(handler)

    def remove_handlers(self, event_type=None, handler=None):
        """Mirror zendriver Connection.remove_handlers' three forms."""
        if event_type is None:
            self._handlers.clear()
        elif handler is None:
            self._handlers.pop(event_type, None)
        else:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def handlers_for(self, event_type):
        """The handlers currently registered for ``event_type``."""
        return list(self._handlers.get(event_type, []))

    def set_content(self, html):
        """Set the canned page source returned by get_content()."""
        self._content = html

    async def get_content(self):
        self.get_content_calls += 1
        return self._content

    async def close(self):
        self.close_calls += 1

    async def get(self, url):
        self.get_calls.append(url)
        self.url = url

    async def reload(self):
        self.reload_calls += 1

    async def back(self):
        self.back_calls += 1
        if self.back_error is not None:
            raise self.back_error
        if self.back_target_url is not None:
            self.url = self.back_target_url

    async def sleep(self, seconds):
        # Recorded, not performed: tests pace themselves with the real
        # clock only where the behaviour under test is time-based.
        self.sleep_calls.append(seconds)
