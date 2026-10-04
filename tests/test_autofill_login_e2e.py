"""Firefox's login-manager event order, highlight and origin boundary."""
from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from invisible_playwright import InvisiblePlaywright
from invisible_playwright._pw.sync_api import Error

USERNAME = "autofill@example.test"
PASSWORD = "autofill-test-password"
PAGE = b"""<!doctype html><meta charset=utf-8>
<form><input id=username type=email><input id=password type=password></form>
<input id=other>
<script>
window.audit = [];
window.originalActive = document.activeElement;
const username = document.querySelector('#username');
const password = document.querySelector('#password');
for (const type of ['beforeinput', 'input', 'change', 'focus', 'focusin',
                    'blur', 'focusout', 'keydown', 'keypress', 'keyup',
                    'pointerover', 'pointerenter', 'pointermove', 'pointerdown',
                    'pointerup', 'pointerout', 'pointerleave', 'mousedown',
                    'mousemove', 'mouseup', 'click'])
  document.addEventListener(type, event => audit.push({
    field: event.target.id, type: event.type, cls: event.constructor.name,
    inputType: event.inputType ?? null, trusted: event.isTrusted,
    ownAutofill: event.target.matches(':autofill'),
    usernameAutofill: username.matches(':autofill')
  }), true);
username.addEventListener('input', () => {
  queueMicrotask(() => audit.push({type: 'microtask'}));
  setTimeout(() => audit.push({type: 'task-boundary'}), 0);
});
window.inputHighlights = [];
for (const el of [username, password])
  el.addEventListener('input', () => inputHighlights.push({
    field: el.id, own: el.matches(':autofill'),
    username: username.matches(':autofill')
  }));
</script>"""


@pytest.fixture(scope="module")
def origins():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Handler) for _ in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    try:
        yield tuple(f"http://127.0.0.1:{s.server_port}" for s in servers)
    finally:
        for server, thread in zip(servers, threads):
            server.shutdown()
            server.server_close()
            thread.join()


@pytest.fixture
def page(firefox_binary, origins):
    with InvisiblePlaywright(seed=4243, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_page()
        page.goto(origins[0])
        page.evaluate("() => { audit = []; originalActive = document.activeElement; }")
        yield page


def _fill(target, origin):
    return target.autofill_login(
        origin=origin, username=USERNAME, username_selector="#username",
        username_type="email", password=PASSWORD, password_selector="#password")


@pytest.mark.e2e
@pytest.mark.parametrize("surface", ["page", "frame"])
def test_native_events_highlight_order_and_later_edit(page, origins, surface):
    target = page if surface == "page" else page.main_frame
    assert _fill(target, origins[0]) is None
    assert page.input_value("#username") == USERNAME
    assert page.input_value("#password") == PASSWORD
    assert page.evaluate("document.activeElement === originalActive")
    page.wait_for_function("audit.some(e => e.type === 'task-boundary')")
    audit = page.evaluate("audit")
    events = [e for e in audit if e["type"] not in ("microtask", "task-boundary")]
    assert [(e["field"], e["type"], e["cls"], e["inputType"], e["trusted"])
            for e in events] == [
        (field, kind, cls, input_type, True)
        for field in ("username", "password")
        for kind, cls, input_type in [
            ("beforeinput", "InputEvent", "insertReplacementText"),
            ("input", "InputEvent", "insertReplacementText"),
            ("change", "Event", None),
        ]
    ]
    assert page.evaluate("inputHighlights") == [
        {"field": "username", "own": False, "username": False},
        {"field": "password", "own": False, "username": True},
    ]
    boundary = next(i for i, e in enumerate(audit) if e["type"] == "task-boundary")
    assert all(i < boundary for i, e in enumerate(audit) if e.get("field") == "password")
    assert page.evaluate("""[username, password].map(el =>
      [el.matches(':autofill'), el.matches(':-webkit-autofill')])""") == [[True, True]] * 2

    page.evaluate("() => { audit = []; inputHighlights = []; }")
    _fill(target, origins[0])
    assert page.evaluate("audit") == []
    page.focus("#password")
    page.keyboard.press("End")
    page.keyboard.type("x")
    assert page.input_value("#password") == PASSWORD + "x"
    assert page.evaluate("""[username, password].map(el =>
      [el.matches(':autofill'), el.matches(':-webkit-autofill')])""") == [
          [True, True], [False, False]]


@pytest.mark.e2e
def test_wrong_origin_writes_nothing(page, origins):
    with pytest.raises(Error, match="nothing was written") as failed:
        _fill(page, origins[1])
    assert USERNAME not in str(failed.value)
    assert PASSWORD not in str(failed.value)
    assert page.input_value("#username") == ""
    assert page.input_value("#password") == ""
    assert page.evaluate("audit") == []


@pytest.mark.e2e
def test_beforeinput_password_type_change_is_reported_and_cleared(page, origins):
    page.evaluate("""password.addEventListener('beforeinput', () => {
      password.type = 'text';
    }, {once: true})""")
    with pytest.raises(Error, match="password=cleared") as failed:
        _fill(page, origins[0])
    assert "username=filled" in str(failed.value)
    assert PASSWORD not in str(failed.value)
    assert "nothing was written" not in str(failed.value)
    assert page.input_value("#password") == ""
    assert page.evaluate("password.matches(':autofill')") is False
    assert page.evaluate("username.matches(':autofill')") is True


@pytest.mark.e2e
def test_page_altered_username_is_reported_without_clearing_it(page, origins):
    page.evaluate("""username.addEventListener('input', () => {
      username.value = 'changed@example.test';
    }, {once: true})""")
    with pytest.raises(Error, match="username=altered") as failed:
        _fill(page, origins[0])
    assert "password=filled" in str(failed.value)
    assert USERNAME not in str(failed.value)
    assert PASSWORD not in str(failed.value)
    assert page.input_value("#username") == "changed@example.test"
    assert page.input_value("#password") == PASSWORD
    assert page.evaluate("username.matches(':autofill') && password.matches(':autofill')")


@pytest.mark.e2e
def test_another_origin_iframe_is_refused(page, origins):
    page.evaluate("""url => new Promise(resolve => {
      const child = document.createElement('iframe');
      child.id = 'child';
      child.onload = resolve;
      child.src = url;
      document.body.append(child);
    })""", origins[1])
    child = page.query_selector("#child").content_frame()
    with pytest.raises(Error, match="nothing was written"):
        _fill(child, origins[0])
    assert child.input_value("#username") == ""
    assert child.input_value("#password") == ""
    assert child.evaluate("audit") == []
    assert page.evaluate("audit") == []


@pytest.mark.e2e
@pytest.mark.parametrize("field", ["username", "password"])
def test_single_field_native_autofill(page, origins, field):
    value = USERNAME if field == "username" else PASSWORD
    page.autofill_login(origin=origins[0], **{field: value, field + "_selector": "#" + field})
    assert page.input_value("#" + field) == value
    other = "password" if field == "username" else "username"
    assert page.input_value("#" + other) == ""
    assert page.locator("#" + field).evaluate("el => el.matches(':autofill')")
    assert not page.locator("#" + other).evaluate("el => el.matches(':autofill')")


@pytest.mark.e2e
def test_async_page_and_frame(firefox_binary, origins):
    from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright

    async def exercise():
        async with AsyncInvisiblePlaywright(
            seed=4243, binary_path=firefox_binary, headless=True
        ) as browser:
            page = await browser.new_page()
            for surface in ("page", "frame"):
                await page.goto(origins[0])
                target = page if surface == "page" else page.main_frame
                await _fill(target, origins[0])
                assert await page.input_value("#username") == USERNAME
                assert await page.input_value("#password") == PASSWORD
                assert await page.evaluate(
                    "username.matches(':autofill') && password.matches(':autofill')")

    asyncio.run(exercise())
