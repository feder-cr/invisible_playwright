"""Acting through an ElementHandle, not through a selector.

`page.query_selector("#go").click()` is ordinary Playwright, and until 0.10.0 it
answered "ElementHandle has no method 'click'". The client offers fifty-five
methods on a handle and the driver served fourteen, every one of them a READ:
the whole acting half was missing, and the error said so honestly while being
useless. Nine actions are wired now, and the tests below are in two layers
because the defect that was found while writing them lives in only one of them.

The lower layer checks that `Actions._retry` treats a caller's element
differently from a selector it resolved itself - it must not look the node up
again, and it must not dispose it. No browser.

The upper layer drives a real page through the PUBLIC API, which is the only
layer that exercises the dispatcher and the wire names. That distinction is not
theoretical: while this was being written, `click` and `fill` accepted the new
parameter and quietly failed to forward it, so the driver went back to querying a
selector. Every lower-layer assertion would still have passed.

⛔ AND THE ACTIONS GO THROUGH THE SAME PATH AS THE SELECTOR VERSIONS. A handle
click is the humanised pointer - approach, hover, press, release, with the
hit-target interceptor - and not a shortcut. Two click paths would be two
fingerprints, which is the one thing this package exists not to have. The e2e
below asserts the hover arrives before the click, and that the click is trusted,
because those are what a shortcut would lose.
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

from invisible_playwright._juggler.actions import Actions

PAGE = b"""<!doctype html>
<html><head><title>handle actions</title></head><body>
  <input id="testo" type="text">
  <input id="daScrivere" type="text">
  <button id="bottone" type="button">press me</button>
  <input id="casella" type="checkbox">
  <select id="tendina">
    <option value="a">Alpha</option><option value="b">Beta</option>
  </select>
  <div id="passaggio">no</div>
<script>
  window.registro = {click: 0, ordine: [], change: [], tasti: []};
  const b = document.getElementById('bottone');
  b.addEventListener('click', e => {
    window.registro.click++; window.registro.fidato = e.isTrusted;
    window.registro.ordine.push('click');
  });
  b.addEventListener('mouseover', () => window.registro.ordine.push('hover'));
  for (const id of ['casella','tendina'])
    document.getElementById(id).addEventListener('change',
      () => window.registro.change.push(id));
  document.getElementById('daScrivere').addEventListener('keydown',
    e => window.registro.tasti.push(e.key));
</script>
</body></html>"""


# ── the lower layer: no browser ─────────────────────────────────────────────

class _Inj:
    """Records whether the loop looked a node up, and whether it disposed one."""

    def __init__(self):
        self.queried = []
        self.disposed = []

    def query_selector(self, f, selector):
        self.queried.append(selector)
        return "resolved-by-the-loop"

    def dispose(self, f, element):
        self.disposed.append(element)

    def element_states(self, f, element, states):
        return {"ok": True}

    def scroll_into_view(self, f, element):
        return False


class _Lifecycle:
    main_frame = "frame-1"


def _actions():
    inj = _Inj()
    a = Actions(None, "S", _Lifecycle(), inj)
    # The point is computed from the quad; here it is always usable, so the loop
    # reaches `run` on the first turn and the test is about what it did to get
    # there rather than about geometry.
    a._center_point = lambda f, element, position=None: (10.0, 10.0)
    a._in_viewport = lambda point: True
    return a, inj


def test_a_handle_is_not_looked_up_again():
    """A handle names ONE node. Re-querying the selector would find a different
    element with the same description, which is not what `handle.click()` means.

    Known-bad, and it is what shipped for an afternoon: an action that accepts
    `element_id` and forwards nothing, after which the loop resolves the string
    that was only ever meant for the timeout message.
    """
    a, inj = _actions()
    seen = {}
    a._retry("<element handle>", lambda f, el, pt: seen.setdefault("el", el),
             element_id="the-caller's-node")

    assert seen["el"] == "the-caller's-node"
    assert inj.queried == [], f"the loop resolved a selector anyway: {inj.queried}"


def test_the_caller_s_handle_is_not_disposed():
    """`handle.click()` must leave the handle usable.

    Known-bad: reusing the loop as-is. It disposes what it resolved, which is
    right for a selector and destroys the caller's handle here - and the failure
    lands on the NEXT call, with an error naming neither the cause nor the call
    that caused it.
    """
    a, inj = _actions()
    a._retry("<element handle>", lambda f, el, pt: None, element_id="keep-me")
    assert inj.disposed == [], f"the caller's node was disposed: {inj.disposed}"


def test_a_selector_is_still_resolved_and_still_disposed():
    """The other half, so the two tests above cannot pass by disabling the loop."""
    a, inj = _actions()
    a._retry("#go", lambda f, el, pt: None)
    assert inj.queried == ["#go"]
    assert inj.disposed == ["resolved-by-the-loop"]


def test_every_action_that_takes_an_element_forwards_it():
    """The defect found while writing this file, as a check rather than a memory.

    `click` and `fill` grew the parameter and did not pass it on, because their
    call to `_retry` is formatted differently from the others and a
    pattern-based edit missed them. Nothing failed at import; the driver simply
    went back to querying a selector.
    """
    import ast
    import inspect

    src = inspect.getsource(Actions)
    tree = ast.parse("class X:\n" + "\n".join(
        "    " + line for line in src.split("\n")[1:]))
    cls = tree.body[0]

    manca = []
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name == "_retry":
            continue
        names = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        if "element_id" not in names:
            continue
        body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
        if "element_id" not in body.replace("arg='element_id'", ""):
            manca.append(fn.name)
    assert not manca, f"these accept element_id and never pass it on: {manca}"


# ── the upper layer: a real browser, through the public API ──────────────────

def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % port


@pytest.mark.e2e
def test_the_nine_actions_reach_the_page_through_a_handle(firefox_binary):
    """One browser, every action, and the PAGE is asked what happened.

    Never the method's own silence: a driver that accepts `click` and does
    nothing returns None exactly as happily as one that clicks.
    """
    from invisible_playwright import InvisiblePlaywright

    srv, url = _serve()
    try:
        with InvisiblePlaywright(seed=4242, binary_path=firefox_binary,
                                 headless=True) as browser:
            page = browser.new_context().new_page()
            page.goto(url, wait_until="load", timeout=30_000)
            log = lambda: page.evaluate("() => window.registro")

            button = page.query_selector("#bottone")
            button.click()
            r = log()
            assert r["click"] == 1
            assert r["fidato"] is True, "the click was not a trusted event"
            assert r["ordine"][:2] == ["hover", "click"], (
                "the pointer did not approach before pressing, so this is not "
                "the humanised path: %r" % r["ordine"])

            # the same handle, again: the action must not have disposed it
            button.click()
            assert log()["click"] == 2

            page.query_selector("#testo").fill("Federico")
            assert page.eval_on_selector("#testo", "e => e.value") == "Federico"

            typed = page.query_selector("#daScrivere")
            typed.fill("bar")
            typed.type("baz")
            assert page.eval_on_selector("#daScrivere", "e => e.value") == "barbaz", (
                "type must APPEND; replacing is what fill does")
            typed.press("Enter")
            assert "Enter" in log()["tasti"]

            box = page.query_selector("#casella")
            box.check()
            assert page.eval_on_selector("#casella", "e => e.checked") is True
            box.uncheck()
            assert page.eval_on_selector("#casella", "e => e.checked") is False
            assert log()["change"].count("casella") == 2

            assert page.query_selector("#tendina").select_option("b") == ["b"]
            assert page.eval_on_selector("#tendina", "e => e.value") == "b"

            page.evaluate("() => { window.registro.ordine = [] }")
            page.query_selector("#bottone").hover()
            assert "hover" in log()["ordine"]

            page.query_selector("#testo").focus()
            assert page.evaluate("() => document.activeElement.id") == "testo"
    finally:
        srv.shutdown()
