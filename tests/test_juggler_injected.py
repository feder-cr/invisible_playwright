"""The injected script called from Python: selectors, actionability, and
the check that matters - that our client does not cross into the page
realm.
"""
from __future__ import annotations

import http.server
import importlib.util
import pathlib
import socketserver
import tempfile
import threading
import time

import pytest

from invisible_playwright._juggler import injected as ini

PAGE = b"""<!doctype html><html><head><title>actionable</title></head><body>
<h1 id=title>hello world</h1>
<button id=ok>press</button>
<button id=off disabled>off</button>
<div id=invisible style="display:none">you can't see me</div>
<input id=field placeholder=type>
<div data-testid=tagged>with testid</div>
<p class=three>a</p><p class=three>b</p><p class=three>c</p>
</body></html>"""


def _serve(body):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return H


def _open(binary, body):
    """Launches, navigates, and returns (connection, lifecycle, injected,
    frame, close)."""
    from invisible_core.launch import build_launch_plan
    from invisible_playwright._juggler import connection as conn
    from invisible_playwright._juggler.lifecycle import Lifecycle

    profile_dir = tempfile.mkdtemp(prefix="inj_test_")
    plan = build_launch_plan(5, profile_dir=profile_dir, binary_path=binary, timezone="UTC",
                             locale="en-US")
    srv = socketserver.TCPServer(("127.0.0.1", 0), _serve(body))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = conn.launch(binary, profile_dir, headless=True, env=plan.env)
    sessions: dict = {}
    c.add_listener(lambda m, p, s: (
        sessions.__setitem__(p["targetInfo"]["targetId"], p["sessionId"])
        if m == "Browser.attachedToTarget" else None))
    c.send("Browser.enable", {"attachToDefaultContext": True})
    ctx = c.send("Browser.createBrowserContext", {"removeOnDetach": True})
    page = c.send("Browser.newPage",
                  {"browserContextId": ctx["browserContextId"]})
    deadline = time.time() + 20
    while page["targetId"] not in sessions and time.time() < deadline:
        time.sleep(0.02)
    sess = sessions[page["targetId"]]
    lifecycle = Lifecycle(c, sess)
    inj = ini.InjectedScript(c, sess)
    inj.install()
    time.sleep(0.4)
    lifecycle.goto("http://127.0.0.1:%d/" % srv.server_address[1],
                   until="load", timeout=30)

    def close():
        c.close()
        srv.shutdown()

    return c, lifecycle, inj, lifecycle.main_frame, close


# ── without a browser ───────────────────────────────────────────────────────

def test_the_world_name_is_OUR_forks():
    """⛔ Upstream calls it something else: our fork renamed it because it
    traveled on the page's window. If this constant and the driver
    diverge, the hookup breaks SILENTLY - the context is born, but under
    a different name, and nobody recognizes it."""
    assert ini.UTILITY_WORLD == "__ctx_aux__"


def test_the_injected_script_is_OURS_not_upstreams():
    """A blob extracted from an upstream bundle would lose every stealth
    fix without anything raising an error."""
    js = pathlib.Path(ini.__file__).with_name("injected.js").read_text(
        encoding="utf-8", errors="replace")
    assert "MODIFIED by invisible_playwright" in js
    assert "InjectedScript" in js
    for engine in ("internal:role", "internal:testid", "internal:label"):
        assert engine in js, engine


def test_the_options_declare_the_utility_world_and_NOT_underTest():
    """Both options are still required, but for the reasons that survived
    measurement.

    ⛔ THIS DOCSTRING USED TO CLAIM THAT `isUtilityWorld` TRUE KEPT 13
    LISTENERS OFF THE PAGE. It did not. `this.window` in the utility world is
    the page's window seen through an Xray, so the constructor's calls landed
    on the page in either world; the flag only decided whether they happened
    once or once per InjectedScript. The listeners are gone now, installed by
    the action that needs them and removed when it stops, and
    `tests/test_injected_page_surface.py` measures that rather than asserting
    it here.

    What each option is still worth: `isUnderTest` false, or the constructor
    plants an ENUMERABLE `window.__injectedScript` on the page; and
    `isUtilityWorld` true, which is simply the truth about where this runs
    and which the bundle reads for its own behaviour.
    """
    source = pathlib.Path(ini.__file__).read_text(encoding="utf-8")
    assert '"isUtilityWorld": True' in source
    assert '"isUnderTest": False' in source


# ── with a browser ──────────────────────────────────────────────────────────

@pytest.mark.e2e
def test_the_selector_engines_respond(firefox_binary):
    c, lifecycle, inj, f, close = _open(firefox_binary, PAGE)
    try:
        for sel in ("#title", "css=#ok", "text=hello world",
                    "internal:testid=[data-testid='tagged']",
                    "xpath=//button[@id='ok']"):
            oid = inj.query_selector(f, sel)
            assert oid, "selector %r found nothing" % sel
            inj.dispose(f, oid)
        assert inj.query_selector(f, "#doesnotexist") is None
        assert inj.count(f, ".three") == 3
    finally:
        close()


@pytest.mark.e2e
def test_actionability_DISTINGUISHES_instead_of_always_saying_yes(
        firefox_binary):
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE.

    `checkElementStates` is ASYNCHRONOUS. If the function calling it did
    not await it, it would return a Promise, the Promise is a truthy
    object, and EVERY element would come out actionable - including a
    disabled button and a div with `display:none`. An actionability
    check that always says yes is not a check, and the failure would be
    silent.

    Here we demand that it DISCRIMINATES, and that it says WHICH state
    is missing.
    """
    c, lifecycle, inj, f, close = _open(firefox_binary, PAGE)
    try:
        full_states = ["visible", "stable", "enabled"]

        ok = inj.element_states(f, inj.query_selector(f, "#ok"), full_states)
        assert ok == {"ok": True}, ok

        off = inj.element_states(f, inj.query_selector(f, "#off"), full_states)
        assert off["ok"] is False and off["missing"] == "enabled", off

        invisible = inj.element_states(
            f, inj.query_selector(f, "#invisible"), ["visible"])
        assert invisible["ok"] is False and \
            invisible["missing"] == "visible", invisible

        field = inj.element_states(
            f, inj.query_selector(f, "#field"),
            ["visible", "stable", "enabled", "editable"])
        assert field == {"ok": True}, field
    finally:
        close()


@pytest.mark.e2e
def test_the_text_reads(firefox_binary):
    c, lifecycle, inj, f, close = _open(firefox_binary, PAGE)
    try:
        oid = inj.query_selector(f, "#title")
        assert inj.text_content(f, oid) == "hello world"
    finally:
        close()


@pytest.mark.e2e
def test_a_javascript_that_THROWS_arrives_as_an_error_not_as_None(
        firefox_binary):
    """⛔ A page exception does NOT come back as a protocol error: it
    comes back as `exceptionDetails` inside a SUCCESSFUL response.
    Whoever looks only at the return code reads `None` and proceeds with
    a value that does not exist."""
    c, lifecycle, inj, f, close = _open(firefox_binary, PAGE)
    try:
        with pytest.raises(ini.EvaluationError) as e:
            inj.evaluate(
                f, "(() => { throw new Error('broken on purpose'); })()")
        assert "broken on purpose" in str(e.value)
    finally:
        close()


READING = b"""<!doctype html><html><head><title>reading</title></head><body>
<div id=t>hello <b>world</b></div>
<input id=field value=foo>
<input id=ticked type=checkbox checked>
<button id=off disabled>no</button>
<div id=hidden style=display:none>x</div>
<a id=link href="/here">go</a>
</body></html>"""


@pytest.mark.e2e
def test_the_DOM_READING_group(firefox_binary):
    """The operations from item 6, group "DOM reading" (§6.5)."""
    c, lifecycle, inj, f, close = _open(firefox_binary, READING)
    try:
        assert inj.title(f) == "reading"
        assert inj.content(f).startswith("<!DOCTYPE html>")

        t = inj.query_selector(f, "#t")
        assert inj.inner_text(f, t) == "hello world"
        assert inj.inner_html(f, t) == "hello <b>world</b>"
        box = inj.bounding_box(f, t)
        assert box and box["width"] > 0 and box["height"] > 0, box

        assert inj.input_value(f, inj.query_selector(f, "#field")) == "foo"

        link = inj.query_selector(f, "#link")
        assert inj.get_attribute(f, link, "href") == "/here"
        # ⛔ A MISSING attribute returns None, not the empty string: they
        # are two different things and whoever reads it must be able to
        # tell them apart.
        assert inj.get_attribute(f, link, "doesnotexist") is None

        # a hidden element has no quad: None, not a zero-sized box
        assert inj.bounding_box(f, inj.query_selector(f, "#hidden")) is None
    finally:
        close()


@pytest.mark.e2e
def test_states_DISCRIMINATE_instead_of_always_saying_true(firefox_binary):
    """⛔ THE SECOND KNOWN-BAD INPUT OF THIS FILE.

    `injected.elementState` does NOT return a boolean: it returns
    `{matches, received}`. Reading it as a boolean would give `True`
    always, because a non-empty dict is truthy - and every element
    would come out visible, enabled and checked. A check that always
    says yes is not a check.
    """
    c, lifecycle, inj, f, close = _open(firefox_binary, READING)
    try:
        assert inj.element_state(
            f, inj.query_selector(f, "#t"), "visible") is True
        assert inj.element_state(
            f, inj.query_selector(f, "#hidden"), "visible") is False
        assert inj.element_state(
            f, inj.query_selector(f, "#hidden"), "hidden") is True
        assert inj.element_state(
            f, inj.query_selector(f, "#off"), "disabled") is True
        assert inj.element_state(
            f, inj.query_selector(f, "#off"), "enabled") is False
        assert inj.element_state(
            f, inj.query_selector(f, "#ticked"), "checked") is True
        assert inj.element_state(
            f, inj.query_selector(f, "#field"), "editable") is True
    finally:
        close()


@pytest.mark.e2e
def test_reads_that_make_no_sense_are_REJECTED(firefox_binary):
    """An `input_value` on a div and a made-up state would come back as
    `undefined` silently. A value that means nothing is worse than an
    error, because it lets execution continue."""
    c, lifecycle, inj, f, close = _open(firefox_binary, READING)
    try:
        with pytest.raises(ini.EvaluationError) as e:
            inj.input_value(f, inj.query_selector(f, "#t"))
        assert "input" in str(e.value)

        with pytest.raises(ValueError) as e2:
            inj.element_state(
                f, inj.query_selector(f, "#t"), "wheneverIFeelLikeIt")
        assert "unknown" in str(e2.value)
    finally:
        close()


@pytest.mark.e2e
def test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm(firefox_binary):
    """The check that matters more than any other.

    Reuses the trap page from the crossings gate - twenty traps armed in
    the FIRST script - but drives it with `_juggler` instead of
    Playwright. If the driver was clean and our client was not, the
    defect is ours, and this test is the only place that would say so.
    """
    spec = importlib.util.spec_from_file_location(
        "oc", str(pathlib.Path(__file__).resolve().parents[3]
                  / "tests" / "gates" / "observable_crossings.py"))
    if spec is None or not pathlib.Path(spec.origin).is_file():
        pytest.skip("the trap page lives in the workbench, not here")
    oc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oc)

    c, lifecycle, inj, f, close = _open(firefox_binary, oc.PAGINA)
    try:
        alive = inj.call(f, "(injected) => document.getElementById('spia')"
                            ".getAttribute('data-viva')")
        assert alive == "1", (
            "traps NOT hooked (%s): a zero would be worth nothing" % alive)

        def read():
            time.sleep(0.15)
            g = inj.call(f, "(injected) => document.getElementById('spia')"
                            ".getAttribute('data-conta') || ''") or ""
            counts = {}
            for x in g.split():
                k, _, v = x.partition("=")
                try:
                    counts[k] = int(v)
                except ValueError:
                    pass
            return counts

        before = read()
        assert before, "the page did not publish the counters"

        for action in (lambda: inj.query_selector(f, "#bersaglio"),
                       lambda: inj.count(f, "div"),
                       lambda: inj.query_selector(f, "text=cliccami"),
                       lambda: inj.element_states(
                           f, inj.query_selector(f, "#bersaglio"),
                           ["visible", "stable", "enabled"]),
                       lambda: inj.text_content(
                           f, inj.query_selector(f, "#bersaglio")),
                       lambda: inj.evaluate(f, "({a: 1, b: [1,2,3]})")):
            action()
        after = read()

        moved = {k: after[k] - before.get(k, 0)
                 for k in after if after[k] - before.get(k, 0)}
        assert not moved, "our client crossed over: %s" % moved
    finally:
        close()
