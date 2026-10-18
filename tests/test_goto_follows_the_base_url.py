"""`goto` resolves its address against the context's `baseURL` ([B342]).

Until this, `baseURL` was read nowhere in the server, and
`page.goto("/empty.html")` in a context created with a base failed with
"Invalid url /empty.html", where Playwright navigates to the base's
`/empty.html`. Playwright's server resolves it in `goto` with
`new URL(given, base).toString()`, keeping the given text when that throws;
this server resolves it with the same expression, run by the engine in the
utility world, so the parser that resolves the address is the parser that
navigates to it.

The wiring is checked here with the dispatcher's own two methods and nothing
else real. The expression is checked by running it next to Playwright's own
function under Node's WHATWG URL, on the cases where URL resolution has edges:
the two must agree on every one. No browser is involved.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from invisible_playwright._juggler.server import FrameDispatcher


class _Injected:
    def __init__(self):
        self.calls = []

    def call(self, frame_id, declaration, *arguments, **options):
        self.calls.append((frame_id, declaration, arguments))
        return "http://resolved.test/"


class _Page:
    def __init__(self, options):
        self.context = type("Context", (), {"options": options})()
        self.injected = _Injected()

    def navigation_response(self, navigation_id):
        return None


class _Lifecycle:
    def __init__(self):
        self.urls = []

    def goto(self, url, **options):
        self.urls.append(url)
        return {"navigationId": "n"}


class _Frame:
    """What `_navigation_url` and `op_goto` read, around the real methods."""

    _navigation_url = FrameDispatcher._navigation_url
    op_goto = FrameDispatcher.op_goto

    def __init__(self, options):
        self.page = _Page(options)
        self.frame_id = "frame"
        self.lifecycle = _Lifecycle()

    @property
    def injected(self):
        return self.page.injected

    def _timeout(self, params):
        return 30.0


def test_without_a_base_the_address_is_left_alone():
    frame = _Frame({})
    frame.op_goto({"url": "/empty.html"})
    assert frame.lifecycle.urls == ["/empty.html"]
    assert frame.page.injected.calls == []


def test_with_a_base_goto_navigates_to_the_resolved_address():
    frame = _Frame({"baseURL": "http://localhost:8000/sub/"})
    frame.op_goto({"url": "/empty.html"})
    assert frame.lifecycle.urls == ["http://resolved.test/"]
    [(frame_id, _, arguments)] = frame.page.injected.calls
    assert frame_id == "frame"
    assert arguments == ("/empty.html", "http://localhost:8000/sub/")


#: Playwright 1.61.0's own resolution (packages/isomorphic/urlMatch.ts,
#: `resolveBaseURL` as `constructURLBasedOnBaseURL` uses it), verbatim.
PLAYWRIGHT = """
function resolveBaseURL(baseURL, givenURL) {
  try {
    const url2 = new URL(givenURL, baseURL);
    const resolved = url2.toString();
    const caseInsensitivePrefix = url2.origin;
    return { resolved, caseInsensitivePart: caseInsensitivePrefix };
  } catch (e) {
    return { resolved: givenURL };
  }
}
"""

CASES = [
    ("/empty.html", "http://localhost:8000/sub/page"),
    ("empty.html", "http://localhost:8000/sub/page"),
    ("./a/../b", "http://localhost:8000/sub/"),
    ("../../../x", "http://localhost:8000/a/b"),
    ("?q=1", "http://localhost:8000/p?old=2"),
    ("#frag", "http://localhost:8000/p"),
    ("//other.test/p", "https://localhost:8000/"),
    ("https://absolute.test/y", "http://localhost:8000/"),
    ("/p%20q", "http://LOCALHOST:8000"),
    ("/x", "not a url"),
    ("data:text/html,hi", "http://localhost:8000/"),
    ("", "http://localhost:8000/dir/file?x#y"),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_expression_resolves_exactly_as_playwright_does():
    frame = _Frame({"baseURL": "http://localhost:8000/"})
    frame._navigation_url("/x")
    [(_, declaration, _)] = frame.page.injected.calls
    script = PLAYWRIGHT + (
        "const ours = %s;\n"
        "const cases = %s;\n"
        "console.log(JSON.stringify(cases.map(([given, base]) =>"
        " [ours(null, given, base), resolveBaseURL(base, given).resolved])));\n"
        % (declaration, json.dumps(CASES)))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         timeout=60, check=True).stdout
    results = json.loads(out)
    assert len(results) == len(CASES)
    for (given, base), (ours, theirs) in zip(CASES, results):
        assert ours == theirs, (given, base, ours, theirs)
