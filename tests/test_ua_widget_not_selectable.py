"""A page CSS query must never return the engine's own form widgets.

**What went wrong.** Firefox builds the controls inside `<input type=date>`
and `<input type=time>` as real elements, in a shadow root attached to the
input and marked closed. A stealth patch landed in firefox-21 that hands
closed shadow roots to the automation sandbox, which is what lets a locator
reach content a page deliberately hid. It checked two things, that the root is
closed and that the caller is the sandbox, and it did not check the third:
whether the root belongs to the PAGE or to the ENGINE.

The selector engine pierces shadow roots by design and is right to, so from
firefox-21 onward it collected engine internals as if the author had written
them. On a document with one button, `page.locator("button").count()` answered
2, and the extra match was invisible and therefore never clickable.

**Why it deserves its own file rather than a line in another one.** It failed
SILENTLY and disguised as the site defending itself. Reported from a real run:
a login form was filled, verified in the screenshot, the click on the submit
was reported as successful, and the request never left the browser, because
`.first` had resolved to the invisible calendar button. The HTTP log had zero
POSTs in 667 lines and the script concluded the credentials were rejected. For
a package whose job is measuring defences, that is the worst failure available:
it does not look like a bug, it looks like data.

**The fix is in the engine, not here.** `Element::GetShadowRootForBindings`
gained the guard its neighbour `GetOpenOrClosedShadowRoot` has carried since
upstream bug 2035665: a UA widget is refused to anything that is not the
system principal. These tests therefore FAIL on firefox-21 and firefox-22 and
that is correct, they are the regression sentinels for a binary that does not
have the guard yet.

**The last two tests are the ones that stop a fix in the wrong direction.**
Refusing UA widgets must not also refuse the AUTHOR closed shadow roots the
patch exists for, and it must not be achieved by opening anything to the page.
A test file that only checked the count would be satisfied by a patch that
reverted the feature entirely, or by one that made closed roots readable by
content, which would be a fingerprinting tell.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


# The engine widgets live inside these two inputs. Both are here rather than
# just the one from the report, because the guard is about the CLASS of root
# and a test naming a single input would not notice a fix that special-cased
# it.
_WIDGET_PAGE = b"""<!doctype html><html><head><title>ua-widget</title></head>
<body>
  <form id="f" action="/posted" method="post">
    <input type="time" id="when">
    <input type="date" id="day">
    <button id="author_button">Submit order</button>
    <input type="submit" id="author_submit" value="Send">
  </form>
</body></html>"""

# A closed shadow root the PAGE created. This is what the stealth patch is for
# and it must keep working.
_AUTHOR_PAGE = b"""<!doctype html><html><head><title>author-closed</title></head>
<body>
  <div id="host"></div>
  <input type="time" id="when">
  <script>
    const root = document.getElementById('host').attachShadow({mode: 'closed'});
    root.innerHTML = '<button id="hidden_button">inside a closed root</button>';
    // What the PAGE can see of its own roots. Both must stay null: the first
    // is the spec answer for a closed root, the second is the engine's own
    // widget, which content has never been able to reach.
    window.__page_sees_own_closed = document.getElementById('host').shadowRoot;
    window.__page_sees_ua_widget = document.getElementById('when').shadowRoot;
  </script>
</body></html>"""


class _SilentHandler(BaseHTTPRequestHandler):
    #: A silent socket no longer pins a thread: after five seconds it drops.
    timeout = 5
    """Serve the two pages by path, and say nothing while doing it."""

    def log_message(self, *_a):
        pass

    def do_GET(self):
        body = _AUTHOR_PAGE if self.path.startswith("/author") else _WIDGET_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def widget_harness():
    """Serve both pages from a kernel-chosen port.

    Port 0 is bound here and read back off the listening socket rather than
    picked in advance and bound later: `run_e2e.py` runs four workers at once,
    and a port number that has been released is a number two workers can be
    given at the same time.
    """
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _SilentHandler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % port
    finally:
        srv.shutdown()


def _ids(loc) -> list:
    """The id of every match, in the order the locator returns them."""
    return [loc.nth(i).get_attribute("id") for i in range(loc.count())]


@pytest.mark.e2e
def test_a_simple_selector_returns_only_author_elements(firefox_binary, widget_harness):
    """`button` on a page with one button must answer 1.

    Measured before the guard: 2, the second being the calendar button of the
    datetime widget, with an id of `calendar-button` and no author anywhere
    near it.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        page = browser.new_page()
        page.goto(widget_harness + "/", wait_until="load", timeout=30_000)
        page.wait_for_selector("#author_button", timeout=10_000)

        found = _ids(page.locator("button"))
        assert found == ["author_button"], (
            "a CSS query returned engine internals: expected the one button "
            "the document contains, got %r" % (found,)
        )


@pytest.mark.e2e
def test_a_comma_selector_returns_only_author_elements(firefox_binary, widget_harness):
    """The comma form is a separate assertion because it took a separate path.

    Reported, and the reason `.first` was landing on an invisible element:
    with `button` alone the widget came back SECOND, while with
    `button, input[type='submit']` it came back FIRST. Same document, same
    session, two orders. Whatever the merge does with the alternatives, it must
    not be handed engine internals to merge in the first place.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        page = browser.new_page()
        page.goto(widget_harness + "/", wait_until="load", timeout=30_000)
        page.wait_for_selector("#author_button", timeout=10_000)

        loc = page.locator("button, input[type='submit']")
        found = _ids(loc)
        assert found == ["author_button", "author_submit"], (
            "the comma form returned %r" % (found,)
        )
        # Belt and braces, and it is the assertion that speaks to the symptom:
        # every match a user can be handed has to be one they can act on.
        invisible = [i for i in range(loc.count()) if not loc.nth(i).is_visible()]
        assert not invisible, (
            "matches %r are invisible, so a click on them can only time out"
            % (invisible,)
        )


@pytest.mark.e2e
def test_the_order_is_author_document_order_in_both_forms(firefox_binary, widget_harness):
    """Both selector forms must agree, and agree with the document.

    The two forms disagreeing is what turned a wrong match set into a wrong
    FIRST match, and `.first` is what most callers use. Asserting the count
    alone would pass on a build that returns the right two elements in the
    wrong order.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        page = browser.new_page()
        page.goto(widget_harness + "/", wait_until="load", timeout=30_000)
        page.wait_for_selector("#author_button", timeout=10_000)

        # The document's own order, straight from the DOM, so the expectation
        # is not a second copy of the same belief written by hand.
        expected = page.evaluate(
            "() => [...document.querySelectorAll(\"button, input[type='submit']\")]"
            ".map(e => e.id)"
        )
        assert expected == ["author_button", "author_submit"], (
            "the harness page is not what this test thinks it is: %r" % (expected,)
        )

        assert _ids(page.locator("button, input[type='submit']")) == expected
        assert _ids(page.locator("button")) == [i for i in expected if i == "author_button"]


@pytest.mark.e2e
def test_an_author_closed_shadow_root_is_still_reachable(firefox_binary, widget_harness):
    """The feature the guard must not take away.

    Refusing UA widgets is one condition away from refusing every closed root,
    which would revert the patch and silently remove the ability to drive a
    site that hides its controls that way. This test fails on stock Firefox,
    which is exactly what makes it worth having.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        page = browser.new_page()
        page.goto(widget_harness + "/author", wait_until="load", timeout=30_000)
        page.wait_for_selector("#host", timeout=10_000)

        found = _ids(page.locator("button"))
        # `in`, not equality, and the difference is the whole point of the test.
        # This page also carries an <input type="time">, because the test below
        # reads the page's own view of BOTH kinds of root off it. On a binary
        # without the guard the locator therefore answers
        # ['hidden_button', 'calendar-button'], and an equality assertion would
        # fail here with the message "the author's closed shadow root is no
        # longer reachable" while the root was in fact perfectly reachable.
        # That is a test reporting the opposite of what it measured. Whether the
        # widget is in that list is asserted by the three tests above, which is
        # where that claim belongs.
        assert "hidden_button" in found, (
            "the author's closed shadow root is no longer reachable: %r" % (found,)
        )


@pytest.mark.e2e
def test_the_page_itself_still_sees_null_on_both_kinds_of_closed_root(
    firefox_binary, widget_harness
):
    """The guard must not be implemented by opening anything to content.

    A page reading `element.shadowRoot` on a closed root gets null in every
    browser on earth. If ours answered anything else, the whole patch would be
    a fingerprinting tell rather than a capability, and the count assertions
    above would still pass.
    """
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False) as browser:
        page = browser.new_page()
        page.goto(widget_harness + "/author", wait_until="load", timeout=30_000)
        page.wait_for_selector("#host", timeout=10_000)

        seen = page.evaluate(
            "() => ({own: window.__page_sees_own_closed,"
            " ua: window.__page_sees_ua_widget,"
            " live_own: !!document.getElementById('host').shadowRoot,"
            " live_ua: !!document.getElementById('when').shadowRoot})"
        )
        assert seen["own"] is None, "the page can read its own closed shadow root"
        assert seen["ua"] is None, "the page can read the engine's widget root"
        assert seen["live_own"] is False
        assert seen["live_ua"] is False
