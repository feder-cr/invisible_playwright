"""The retry cycle and the actions, against a real browser.

⛔ This is the piece that fails silently: if a condition is checked ONCE
and then acted on, the page can change between the check and the action.
It does not break - it breaks one time in twenty.

The client is `invisible_core.juggler` and its tests without a browser are
the core's (`tests/test_juggler_actions.py` there). These need the engine,
which is where this package's e2e job is, and every one runs TWICE: with the
pointer approach drawn by the client (this package's mode, where `_cursor`
walks the cursor first) and by the engine (`engine_approach=True`, the mode
invisible-selenium and invisible-puppeteer drive the same client in). Those
two packages had a copy of these tests each; one place now covers both modes.
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import tempfile
import threading
import time

import pytest

from invisible_core.juggler._behaviour import PageActs
from invisible_core.juggler.actions import Actions, ElementNotActionable

#: Who draws the pointer approach in `_open`; set per test by the fixture below.
_ENGINE_APPROACH = False


@pytest.fixture(autouse=True, params=[False, True],
                ids=["client-approach", "engine-approach"])
def _approach_mode(request, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_ENGINE_APPROACH", request.param)


PAGE = b"""<!doctype html><html><head><title>actions</title></head><body>
<button id=ok onclick="this.dataset.clicks=(+(this.dataset.clicks||0)+1)">press</button>
<input id=field>
<input id=date type=date>
<div id=events data-count="0" data-trusted=""></div>
<input id=checkbox type=checkbox>
<input id=already type=checkbox checked>
<select id=choice><option value=a>A</option><option value=b>B</option></select>
<div id=keys data-log=""></div>
<div id=dbl data-n="0" ondblclick="this.dataset.n=(+this.dataset.n+1)">double</div>
<div id=source draggable=true style="width:60px;height:30px">drag</div>
<div id=target data-drop="0" style="width:60px;height:30px">here</div>
<!-- LAST in the flow, on purpose. It appears 1.2 s after load, and while it
     stood above the checkboxes its appearance pushed them down by one line.
     A `check("#checkbox")` that straddled that instant verified the point on
     the checkbox and pressed on #slow, which is what the engine now reports
     (ActionMissed: mousedown landed on button#slow) and what the CI saw as
     "clicked but the box stayed unchecked" one run in twelve since
     2026-09-05. Nothing below it is acted on by another test. [B217] -->
<div id=late style="display:none"><button id=slow>delayed</button></div>
<script>
  const log = document.getElementById('keys');
  document.addEventListener('keydown', ev => {
    log.dataset.log += ev.key + '|' + ev.code + '|' + ev.keyCode + '|'
      + (ev.shiftKey ? 'S' : '-') + (ev.ctrlKey ? 'C' : '-')
      + '|' + (ev.isTrusted ? 'T' : 'F') + ';';
  });
  const b = document.getElementById('target');
  b.addEventListener('mouseup', () => { b.dataset.drop = '1'; });
</script>
<script>
  const c = document.getElementById('field');
  const e = document.getElementById('events');
  let n = 0;
  const d = document.getElementById('date');
  for (const el of [c, d])
    for (const t of ['input','change']) el.addEventListener(t, ev => {
      n++; e.dataset.count = n;
      e.dataset.trusted = (e.dataset.trusted || '') + (ev.isTrusted ? 'T' : 'F');
    });
  setTimeout(() => { document.getElementById('late').style.display = 'block'; }, 1200);
</script>
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
    from invisible_core.launch import build_launch_plan
    from invisible_core.juggler import connection as conn
    from invisible_core.juggler.injected import InjectedScript
    from invisible_core.juggler.lifecycle import Lifecycle

    profile_dir = tempfile.mkdtemp(prefix="act_test_")
    plan = build_launch_plan(9, profile_dir=profile_dir, binary_path=binary, timezone="UTC",
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
    inj = InjectedScript(c, sess)
    inj.install()
    time.sleep(0.4)
    lifecycle.goto("http://127.0.0.1:%d/" % srv.server_address[1],
                   until="load", timeout=30)
    actions = Actions(c, sess, lifecycle, inj, acts=PageActs(1),
                      engine_approach=_ENGINE_APPROACH)

    def close():
        c.close()
        srv.shutdown()

    return actions, inj, lifecycle.main_frame, close


def _dataset(inj, f, sel, attr):
    oid = inj.query_selector(f, sel)
    v = inj.call(f, "(injected, el, a) => el.dataset[a] || ''",
                 {"objectId": oid}, attr)
    inj.dispose(f, oid)
    return v


@pytest.mark.e2e
def test_the_click_reaches_the_page(firefox_binary):
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        actions.click("#ok")
        assert _dataset(inj, f, "#ok", "clicks") == "1"
        actions.click("#ok")
        assert _dataset(inj, f, "#ok", "clicks") == "2"
    finally:
        close()


@pytest.mark.e2e
def test_the_fill_events_are_TRUSTED(firefox_binary):
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE.

    Dispatching events from the injected script produces `isTrusted: false`,
    and the mix of trusted and untrusted events on the same form is a tell
    cheaper than any single signal: no enumeration API, a single
    `addEventListener`. This is [B175], already paid for once.

    ⛔ AND IT NEEDS TWO FIELDS, not one, because the two paths are different
    and the first draft of this test only exercised one. On a TEXT input
    `injected.fill` returns `needsinput` and the text gets TYPED: those
    events are trusted because they come from key presses, and a mutation to
    the command that fired them **survived** because that line was never
    executed. The path [B175] lives on is the other one: inputs whose value
    gets SET - `date`, `color`, `range`, `time` - where `injected.fill`
    answers `{"setUserInput": value}` and the engine commits it through
    Firefox's own user path (`Page.setUserInput`), which fires the events.
    """
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        # path A: the text gets TYPED
        actions.fill("#field", "hello")
        oid = inj.query_selector(f, "#field")
        assert inj.call(f, "(injected, el) => el.value",
                        {"objectId": oid}) == "hello"
        inj.dispose(f, oid)

        # path B: the value gets SET, and this is the [B175] one
        actions.fill("#date", "2026-08-27")
        oid = inj.query_selector(f, "#date")
        assert inj.call(f, "(injected, el) => el.value",
                        {"objectId": oid}) == "2026-08-27"
        inj.dispose(f, oid)

        trusted = _dataset(inj, f, "#events", "trusted")
        assert trusted, "the page did not receive any event"
        assert len(trusted) >= 4, (
            "too few events (%r): one of the two paths did not fire"
            % trusted)
        assert "F" not in trusted, (
            "untrusted events among the ones received: %r" % trusted)
    finally:
        close()


@pytest.mark.e2e
def test_the_lifecycle_WAITS_for_an_element_that_appears_later(firefox_binary):
    """The reason the lifecycle exists: without it, this would be a failure
    instead of a wait. The element appears after 1.2 seconds."""
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        actions.click("#slow", timeout=15)
    finally:
        close()


@pytest.mark.e2e
def test_a_timeout_SAYS_the_reason_for_the_last_attempt(firefox_binary):
    """⛔ A bare `TimeoutError` on a retry cycle is the least useful thing you
    can print: without the reason for the last attempt, the reader doesn't
    know whether the selector found nothing, whether a state was missing, or
    whether the element had no quad."""
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        with pytest.raises(ElementNotActionable) as e:
            actions.click("#doesnotexist", timeout=2)
        text = str(e.value)
        assert "#doesnotexist" in text
        assert "attempts" in text
        assert "the selector finds nothing" in text, text
    finally:
        close()


@pytest.mark.e2e
def test_an_element_that_is_NEVER_actionable_says_WHICH_state_is_missing(
        firefox_binary):
    """A disabled button is not "not found": it is found and not
    actionable, and the message must distinguish the two cases."""
    body = (b"<!doctype html><html><body>"
            b"<button id=disabled disabled>no</button></body></html>")
    actions, inj, f, close = _open(firefox_binary, body)
    try:
        with pytest.raises(ElementNotActionable) as e:
            actions.click("#disabled", timeout=2)
        assert "missing enabled" in str(e.value), str(e.value)
    finally:
        close()


@pytest.mark.e2e
def test_the_keys_arrive_with_REAL_code_and_keycode(firefox_binary):
    """⛔ THE CASE THAT COUNTS: the page reads what actually arrived.

    The browser-less assertions above prove the TABLE; this one proves that
    what Juggler delivers to the page carries the same values. These are two
    different questions, and this project has already paid for testing only
    one of them: seven levers the bench believed were set and that never
    arrived.
    """
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        actions.focus("#field")
        actions.keyboard.press("a")
        actions.keyboard.press("Shift+KeyB")
        actions.keyboard.press("Enter")
        log = _dataset(inj, f, "#keys", "log")
        entries = [v for v in log.split(";") if v]
        assert entries, "no keydown reached the page"
        by_code = {v.split("|")[1]: v.split("|") for v in entries}
        assert by_code["KeyA"][0] == "a"
        assert by_code["KeyA"][2] == "65", (
            "wrong keyCode: %r" % by_code["KeyA"])
        assert by_code["KeyB"][0] == "B" and "S" in by_code["KeyB"][3]
        assert by_code["Enter"][2] == "13"
        assert all(v.split("|")[4] == "T" for v in entries), (
            "a key arrived NOT trusted: %r" % log)
    finally:
        close()


@pytest.mark.e2e
def test_check_and_uncheck_CHECK_instead_of_toggling(firefox_binary):
    """⛔ Clicking without checking flips a checkbox that is already correct.
    And the RE-CHECK afterward is what matters: an element that intercepts
    the click, or a handler that resets the value, make the action succeed
    while leaving the wrong state."""
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        def is_checked(sel):
            oid = inj.query_selector(f, sel)
            v = inj.element_state(f, oid, "checked")
            inj.dispose(f, oid)
            return v

        assert not is_checked("#checkbox")
        actions.check("#checkbox")
        assert is_checked("#checkbox")
        # ⛔ The second time must NOT click: if it clicked, it would flip it.
        actions.check("#checkbox")
        assert is_checked("#checkbox"), "the second check flipped it"
        assert is_checked("#already")
        actions.uncheck("#already")
        assert not is_checked("#already")
    finally:
        close()


@pytest.mark.e2e
def test_the_double_click_sends_clickCount_2(firefox_binary):
    """⛔ Two clicks with `clickCount: 1` produce two `click` events and NO
    `dblclick`: the action succeeds and the site's handler never fires."""
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        actions.dblclick("#dbl")
        assert _dataset(inj, f, "#dbl", "n") == "1", (
            "the page did not see any dblclick")
    finally:
        close()


@pytest.mark.e2e
def test_choosing_an_option_by_value_or_label(firefox_binary):
    """The real case of the known-bad input above: the string must choose
    the RIGHT option, not the first one."""
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        def value():
            oid = inj.query_selector(f, "#choice")
            v = inj.call(f, "(injected, el) => el.value", {"objectId": oid})
            inj.dispose(f, oid)
            return v

        assert value() == "a"
        actions.select_option("#choice", ["b"])
        assert value() == "b", "the bare string chose the first option"
        actions.select_option("#choice", [{"index": 0}])
        assert value() == "a"
        # And by LABEL, which is the other half of `valueOrLabel`.
        actions.select_option("#choice", ["B"])
        assert value() == "b"
    finally:
        close()


@pytest.mark.e2e
def test_typing_ADDS_where_filling_REPLACES(firefox_binary):
    """Swapping them is the easiest way to write the same text twice into a
    field."""
    actions, inj, f, close = _open(firefox_binary, PAGE)
    try:
        def value():
            oid = inj.query_selector(f, "#field")
            v = inj.call(f, "(injected, el) => el.value", {"objectId": oid})
            inj.dispose(f, oid)
            return v

        actions.fill("#field", "abc")
        actions.type_text("#field", "de")
        assert value() == "abcde"
        actions.fill("#field", "z")
        assert value() == "z", "fill did not replace"
    finally:
        close()
