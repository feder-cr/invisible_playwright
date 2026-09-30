"""Credential autofill: an element-bound write, not page-directed keystrokes."""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from invisible_playwright._origin import protect_fill_value, validate_expect_origin
from invisible_playwright._juggler.actions import Actions, ElementNotActionable
from invisible_playwright._juggler.connection import ProtocolError
from invisible_playwright._juggler.injected import EvaluationError, InjectedScript
from invisible_playwright._pw import async_api, sync_api

SECRET = "credential-'\\\n-do-not-print"
ORIGIN = "https://example.com"
KINDS = ["page", "frame", "locator", "handle"]


@pytest.mark.unit
@pytest.mark.parametrize("origin", [
    None, "http://localhost", ORIGIN, "https://example.com:8443",
    "http://127.0.0.1:0", "http://[::1]:8000", "https://xn--bcher-kva.example",
    "ftp://example.com", "ws://localhost:8080",
])
def test_serialized_origins(origin):
    validate_expect_origin(origin)


@pytest.mark.unit
@pytest.mark.parametrize("origin", [
    "", "null", "*", "example.com", "//example.com", "https://example.com/",
    "https://example.com/login", "https://example.com?", "https://example.com#",
    "https://user:pass@example.com", "https://example.com:99999",
    "https://example.com:", "https://example.com:0443", "https://example.com:443",
    "HTTPS://example.com", "https://EXAMPLE.com", " https://example.com",
    "https://exa\nmple.com", "https://example.com\\evil", "file://localhost",
    "data://example.com", "https://[broken]", "http://127.1",
    "https://b\u00fccher.example", 42, False,
])
def test_invalid_origins_do_not_echo_input(origin):
    with pytest.raises(ValueError, match="expect_origin must be a serialized origin") as failed:
        validate_expect_origin(origin)
    assert "expect_origin=" in str(failed.value)
    assert "nothing was written" in str(failed.value)


@pytest.mark.unit
@pytest.mark.parametrize("input_type", [
    None, "button", "checkbox", "color", "date", "datetime-local", "email",
    "file", "hidden", "image", "month", "number", "password", "radio", "range",
    "reset", "search", "submit", "tel", "text", "time", "url", "week", "PaSsWoRd",
])
def test_known_input_type_expectations(input_type):
    with protect_fill_value(SECRET, ORIGIN, expect_input_type=input_type):
        pass


@pytest.mark.unit
@pytest.mark.parametrize("input_type", [
    "", "textarea", "datetime", "unknown", " password", "password ", False, 42, SECRET,
])
def test_unknown_input_type_expectations_are_rejected_without_echoing_values(input_type):
    with pytest.raises(ValueError, match="expect_input_type must be a known HTML input type") as failed:
        with protect_fill_value(SECRET, ORIGIN, expect_input_type=input_type):
            pytest.fail("invalid expectations reached the operation")
    assert "expect_origin=" in str(failed.value)
    assert "nothing was written" in str(failed.value)
    assert SECRET not in str(failed.value)


@pytest.mark.unit
@pytest.mark.parametrize("api", [sync_api, async_api])
@pytest.mark.parametrize("name", ["Page", "Frame", "Locator", "ElementHandle"])
def test_every_public_fill_exposes_the_keyword(api, name):
    method = getattr(api, name).fill
    for keyword in ("expect_origin", "expect_input_type"):
        arg = inspect.signature(method).parameters[keyword]
        assert arg.kind == inspect.Parameter.KEYWORD_ONLY
        assert arg.default is None
        assert keyword in method.__doc__
    assert "own" in method.__doc__
    assert "trusted input and change" in method.__doc__
    assert "keydown/keypress/keyup" in method.__doc__


@pytest.mark.unit
@pytest.mark.parametrize("error_type", [sync_api.Error, sync_api.TimeoutError])
def test_driver_errors_and_their_metadata_are_redacted(error_type):
    original = error_type(f"{SECRET} {SECRET!r} {json.dumps(SECRET)}")
    original._stack = SECRET
    original._log = [SECRET]
    with pytest.raises(error_type) as failed:
        with protect_fill_value(SECRET, ORIGIN):
            raise original
    assert SECRET not in str(failed.value)
    assert repr(SECRET)[1:-1] not in str(failed.value)
    assert json.dumps(SECRET)[1:-1] not in str(failed.value)
    assert failed.value.stack is None
    assert failed.value._log is None
    assert failed.value.__suppress_context__


@pytest.mark.unit
def test_default_fill_preserves_the_original_error():
    original = sync_api.Error(SECRET)
    with pytest.raises(sync_api.Error) as failed:
        with protect_fill_value(SECRET, None):
            raise original
    assert failed.value is original


def _actions(result=None):
    inj = Mock()
    inj.contexts = {}
    inj.query_selector.return_value = "resolved"
    inj.element_states.return_value = {"ok": True}
    inj.call.side_effect = ["control", result or {"status": "done", "actualOrigin": ORIGIN}]
    actions = Actions(Mock(), "session", SimpleNamespace(main_frame="frame"), inj)
    actions._center_point = Mock(return_value=(10, 10))
    actions._in_viewport = Mock(return_value=True)
    actions._type = Mock()
    return actions, inj


@pytest.mark.unit
@pytest.mark.parametrize("input_type", [None, "password", "PaSsWoRd"])
def test_guarded_fill_keeps_actionability_and_dispatches_to_the_control(input_type):
    actions, inj = _actions()
    assert actions.fill(
        "#field", SECRET, expect_origin=ORIGIN, expect_input_type=input_type
    ) == "done"
    inj.element_states.assert_called_once_with(
        "frame", "resolved", ["visible", "stable", "enabled", "editable"])
    assert len(inj.call.call_args_list) == 2
    assert "fillWithOrigin" in inj.call.call_args_list[1].args[1]
    assert inj.call.call_args_list[1].args[-1] == input_type
    actions._type.assert_not_called()
    actions.c.send.assert_called_once_with(
        "Page.dispatchTrustedInputEvents",
        {"frameId": "frame", "objectId": "control", "types": ["input", "change"]},
        session="session", timeout=10)


@pytest.mark.unit
@pytest.mark.parametrize(("origin", "input_type"), [
    (None, "password"), (ORIGIN, "unknown"),
])
def test_invalid_type_expectations_are_rejected_before_resolution(origin, input_type):
    actions, inj = _actions()
    with pytest.raises(ValueError, match="expect_input_type") as failed:
        actions.fill("#field", SECRET, expect_origin=origin, expect_input_type=input_type)
    assert "expect_origin=" in str(failed.value)
    assert "nothing was written" in str(failed.value)
    assert SECRET not in str(failed.value)
    inj.query_selector.assert_not_called()
    inj.call.assert_not_called()
    actions._type.assert_not_called()


@pytest.mark.unit
def test_guard_refusal_is_not_retried_or_typed():
    actions, inj = _actions({"status": "error:origin: target detached/stale",
                             "actualOrigin": "https://other.example"})
    with pytest.raises(EvaluationError, match="nothing was written") as failed:
        actions.fill("#field", SECRET, expect_origin=ORIGIN)
    assert "expect_origin=" in str(failed.value)
    assert ORIGIN in str(failed.value)
    assert "https://other.example" in str(failed.value)
    assert SECRET not in str(failed.value)
    inj.query_selector.assert_called_once()
    actions._type.assert_not_called()
    actions.c.send.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("error_type", [EvaluationError, ProtocolError, ElementNotActionable])
def test_guarded_action_errors_cannot_echo_engine_arguments(error_type):
    actions, inj = _actions()
    inj.query_selector.side_effect = error_type(SECRET)
    with pytest.raises(error_type, match="nothing was written") as failed:
        actions.fill("#field", SECRET, expect_origin=ORIGIN)
    assert "expect_origin=" in str(failed.value)
    assert SECRET not in str(failed.value)
    actions._type.assert_not_called()


@pytest.mark.unit
def test_an_event_failure_does_not_claim_the_write_was_refused_or_retry():
    actions, inj = _actions()
    actions.c.send.side_effect = ProtocolError(SECRET)
    with pytest.raises(ProtocolError, match="value was written") as failed:
        actions.fill("#field", SECRET, expect_origin=ORIGIN)
    assert SECRET not in str(failed.value)
    assert "nothing was written" not in str(failed.value)
    inj.query_selector.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("message", [
    "Runtime.callFunction: no response in 30s",
    "Execution context was destroyed",
])
def test_an_unknown_write_outcome_never_claims_nothing_was_written(message):
    actions, inj = _actions()
    inj.call.side_effect = ["control", ProtocolError(message + SECRET)]
    with pytest.raises(ProtocolError, match="write outcome unknown") as failed:
        actions.fill("#field", SECRET, expect_origin=ORIGIN)
    assert "expect_origin=" in str(failed.value)
    assert "nothing was written" not in str(failed.value)
    assert SECRET not in str(failed.value)
    actions._type.assert_not_called()
    actions.c.send.assert_not_called()
    inj.query_selector.assert_called_once()


@pytest.mark.unit
def test_default_fill_keeps_the_existing_call_and_keyboard_sequence():
    actions, inj = _actions()
    inj.call.side_effect = ["done", "needsinput"]
    assert actions.fill("#field", "ordinary", expect_origin=None) == "needsinput"
    assert [call.args[1] for call in inj.call.call_args_list] == [
        "(injected, el) => injected.focusNode(el, true)",
        "(injected, el, v) => injected.fill(el, v)",
    ]
    actions._type.assert_called_once_with("ordinary")
    actions.c.send.assert_not_called()


PAGE = """<!doctype html><meta charset=utf-8>
<label for=password id=label>Password</label><input id=password type=password>
<input id=text><textarea id=textarea></textarea><input id=number type=number>
<input id=date type=date><input id=checkbox type=checkbox>
<input id=disabled disabled><input id=readonly readonly>
<div id=editable contenteditable=true></div><input id=other>
<script>
window.events = [];
for (const type of ['input', 'change', 'keydown', 'keypress', 'keyup'])
  document.addEventListener(type, e => events.push([e.type, e.isTrusted, e.target.id]));
</script>
"""


@pytest.fixture(scope="module")
def origins():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            if self.path == "/sandbox":
                self.send_header("Content-Security-Policy", "sandbox allow-scripts")
            self.end_headers()
            self.wfile.write(PAGE.encode())

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
def browser(firefox_binary):
    from invisible_playwright import InvisiblePlaywright
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary, headless=True,
                             humanize=False) as browser:
        yield browser


@pytest.fixture
def page(browser, origins):
    page = browser.new_page()
    page.goto(origins[0])
    try:
        yield page
    finally:
        page.close()


def _fill(page, kind, value, origin, selector="#password", **options):
    if kind == "page":
        return page.fill(selector, value, expect_origin=origin, **options)
    if kind == "frame":
        return page.main_frame.fill(selector, value, expect_origin=origin, **options)
    if kind == "locator":
        return page.locator(selector).fill(value, expect_origin=origin, **options)
    handle = page.query_selector(selector)
    try:
        return handle.fill(value, expect_origin=origin, **options)
    finally:
        handle.dispose()


@pytest.mark.e2e
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("input_type", [None, "password", "PaSsWoRd"])
def test_right_origin_has_trusted_events_and_no_keys(page, origins, kind, input_type):
    _fill(page, kind, SECRET.replace("\n", ""), origins[0], expect_input_type=input_type)
    assert page.input_value("#password") == SECRET.replace("\n", "")
    assert page.evaluate("events") == [
        ["input", True, "password"], ["change", True, "password"]]
    _fill(page, kind, "", origins[0], expect_input_type=input_type)
    assert page.input_value("#password") == ""


@pytest.mark.e2e
@pytest.mark.parametrize("kind", KINDS)
def test_wrong_origin_writes_nothing_even_with_force(page, origins, kind):
    with pytest.raises(sync_api.Error, match="nothing was written") as failed:
        _fill(page, kind, SECRET, origins[1], force=True)
    assert origins[0] in str(failed.value)
    assert origins[1] in str(failed.value)
    assert "expect_origin=" in str(failed.value)
    assert SECRET not in str(failed.value)
    assert page.input_value("#password") == ""
    assert page.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize("kind", KINDS)
def test_public_invalid_origin_is_value_error(page, kind):
    with pytest.raises(ValueError, match="expect_origin=") as failed:
        _fill(page, kind, SECRET, "https://example.com/path")
    assert "nothing was written" in str(failed.value)
    assert page.input_value("#password") == ""


@pytest.mark.e2e
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(("origin", "input_type"), [
    (None, "password"), (ORIGIN, "unknown"),
])
def test_public_invalid_type_expectation_is_value_error(page, kind, origin, input_type):
    with pytest.raises(ValueError, match="expect_input_type") as failed:
        _fill(page, kind, SECRET, origin, expect_input_type=input_type)
    assert "expect_origin=" in str(failed.value)
    assert "nothing was written" in str(failed.value)
    assert SECRET not in str(failed.value)
    assert page.input_value("#password") == ""
    assert page.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("selector", ["#text", "#textarea"])
def test_type_expectation_requires_an_input_of_the_requested_type(page, origins, kind, selector):
    with pytest.raises(sync_api.Error, match="nothing was written") as failed:
        _fill(page, kind, "must-stay-secret", origins[0], selector=selector,
              expect_input_type="password", force=True)
    assert "expect_origin=" in str(failed.value)
    assert "expect_input_type" in str(failed.value)
    assert "must-stay-secret" not in str(failed.value)
    assert page.input_value(selector) == ""
    assert page.evaluate("document.activeElement.id") != selector[1:]
    assert page.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize(("selector", "value", "target"), [
    ("#text", "ordinary", "text"), ("#textarea", SECRET, "textarea"),
    ("#label", "labelled", "password"), ("#date", "2026-09-30", "date"),
])
def test_native_setter_and_label_retargeting(page, origins, selector, value, target):
    page.evaluate("""() => {
      for (const proto of [HTMLInputElement.prototype, HTMLTextAreaElement.prototype])
        Object.defineProperty(proto, 'value', {
          get() { throw new Error('page getter must not run'); },
          set() { throw new Error('page setter must not run'); }
        });
    }""")
    input_type = {"#text": "text", "#label": "password", "#date": "date"}.get(selector)
    page.fill(selector, value, expect_origin=origins[0], expect_input_type=input_type)
    assert page.input_value("#" + target) == value
    assert page.evaluate("events") == [["input", True, target], ["change", True, target]]


@pytest.mark.e2e
def test_cross_origin_frame_uses_its_own_origin(page, origins):
    page.evaluate("""url => {
      const frame = document.createElement('iframe');
      frame.id = 'child'; frame.src = url; document.body.append(frame);
    }""", origins[1])
    child = page.frame_locator("#child").locator("#password")
    with pytest.raises(sync_api.Error, match="nothing was written"):
        child.fill(SECRET, expect_origin=origins[0])
    child.fill("child-secret", expect_origin=origins[1])
    frame = page.query_selector("#child").content_frame()
    assert frame.input_value("#password") == "child-secret"
    assert frame.evaluate("events") == [
        ["input", True, "password"], ["change", True, "password"]]
    assert page.input_value("#password") == ""


@pytest.mark.e2e
@pytest.mark.parametrize("sandbox", ["iframe", "csp"])
@pytest.mark.parametrize("spoof_origin", [False, True])
def test_an_opaque_security_origin_is_refused(page, origins, monkeypatch, sandbox, spoof_origin):
    if sandbox == "iframe":
        page.evaluate("""url => new Promise(resolve => {
          const frame = document.createElement('iframe');
          frame.id = 'sandboxed';
          frame.setAttribute('sandbox', 'allow-scripts');
          frame.addEventListener('load', () => resolve(), {once: true});
          frame.src = url;
          document.body.append(frame);
        })""", origins[0])
        target = page.query_selector("#sandboxed").content_frame()
    else:
        page.goto(origins[0] + "/sandbox")
        target = page.main_frame

    assert target.evaluate("location.origin") == origins[0]
    assert target.evaluate("self.origin") == "null"
    if spoof_origin:
        target.evaluate("""origin => {
          Object.defineProperty(window, 'origin', {value: origin});
        }""", origins[0])
        assert target.evaluate("self.origin") == origins[0]

    observed = []
    call = InjectedScript.call

    def observe_utility_origin(injected, frame_id, declaration, *args, **kwargs):
        if "fillWithOrigin" in declaration:
            observed.append(call(
                injected, frame_id,
                """(injected, el) => ({
                  url: el.ownerDocument.location.origin,
                  security: el.ownerDocument.defaultView.origin
                })""", args[0]))
        return call(injected, frame_id, declaration, *args, **kwargs)

    monkeypatch.setattr(InjectedScript, "call", observe_utility_origin)
    with pytest.raises(sync_api.Error, match="nothing was written") as failed:
        target.fill("#password", "opaque-origin-credential", expect_origin=origins[0])
    assert "expect_origin=" in str(failed.value)
    assert "actual origin='null'" in str(failed.value)
    assert "opaque-origin-credential" not in str(failed.value)
    assert observed == [{"url": origins[0], "security": "null"}]
    assert target.input_value("#password") == ""
    assert target.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("pin_type", [False, True])
@pytest.mark.parametrize(("selector", "replacement"), [
    ("#password", "text"), ("#password", "number"), ("#text", "password"),
])
def test_focus_cannot_change_the_input_kind_before_a_credential_write(
    page, origins, kind, selector, replacement, pin_type
):
    original_type = page.locator(selector).evaluate("el => el.type")
    page.locator(selector).evaluate("""(el, type) => {
      el.addEventListener('focus', () => { el.type = type; });
    }""", replacement)
    with pytest.raises(sync_api.Error, match="nothing was written") as failed:
        _fill(page, kind, "type-change-credential", origins[0], selector=selector,
              expect_input_type=original_type if pin_type else None)
    assert "expect_origin=" in str(failed.value)
    assert "type-change-credential" not in str(failed.value)
    assert page.locator(selector).evaluate("el => el.type") == replacement
    assert page.input_value(selector) == ""
    assert page.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize("force", [False, True])
def test_type_expectation_refuses_a_handle_changed_after_resolution(page, origins, force):
    handle = page.query_selector("#password")
    try:
        assert handle.evaluate("el => el.type") == "password"
        handle.evaluate("""el => new Promise(resolve => {
          setTimeout(() => { el.type = 'text'; resolve(); }, 0);
        })""")
        with pytest.raises(sync_api.Error, match="nothing was written") as failed:
            handle.fill("must-stay-secret", expect_origin=origins[0],
                        expect_input_type="PASSWORD", force=force)
        assert "expect_origin=" in str(failed.value)
        assert "expect_input_type" in str(failed.value)
        assert "must-stay-secret" not in str(failed.value)
        assert handle.evaluate("el => el.type") == "text"
        assert handle.input_value() == ""
        assert page.evaluate("document.activeElement.id") != "password"
        assert page.evaluate("events") == []
    finally:
        handle.dispose()


@pytest.mark.e2e
def test_losing_the_reply_after_a_write_does_not_claim_a_no_write_refusal(page, origins, monkeypatch):
    call = InjectedScript.call

    def lose_reply(injected, frame_id, declaration, *args, **kwargs):
        result = call(injected, frame_id, declaration, *args, **kwargs)
        if "fillWithOrigin" in declaration:
            raise ProtocolError("Execution context was destroyed")
        return result

    monkeypatch.setattr(InjectedScript, "call", lose_reply)
    with pytest.raises(sync_api.Error, match="write outcome unknown") as failed:
        page.fill("#password", "write-without-reply", expect_origin=origins[0])
    assert "expect_origin=" in str(failed.value)
    assert "nothing was written" not in str(failed.value)
    assert "write-without-reply" not in str(failed.value)
    assert page.input_value("#password") == "write-without-reply"
    assert page.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize("stage", ["resolution", "write"])
def test_navigation_between_resolution_and_write_is_refused(page, origins, monkeypatch, stage):
    original = Actions._center_point
    navigated = []

    def navigate(actions, frame, element, position=None):
        point = original(actions, frame, element, position)
        if not navigated:
            if stage == "resolution":
                navigated.append(True)
                actions.lifecycle.goto(origins[1], until="load", timeout=10)
            else:
                call = actions.inj.call

                def before_write(frame_id, declaration, *args, **kwargs):
                    if "fillWithOrigin" in declaration and not navigated:
                        navigated.append(True)
                        actions.lifecycle.goto(origins[1], until="load", timeout=10)
                    return call(frame_id, declaration, *args, **kwargs)

                monkeypatch.setattr(actions.inj, "call", before_write)
        return point

    monkeypatch.setattr(Actions, "_center_point", navigate)
    with pytest.raises(sync_api.Error, match="nothing was written") as failed:
        page.locator("#password").fill(SECRET, expect_origin=origins[0])
    assert navigated
    assert SECRET not in str(failed.value)
    assert origins[1] in str(failed.value)
    assert page.url.startswith(origins[1])
    assert page.input_value("#password") == ""
    assert page.evaluate("events") == []


@pytest.mark.e2e
@pytest.mark.parametrize("mutation", [
    "el.remove()",
    "other.contentDocument.adoptNode(el); other.contentDocument.body.append(el)",
])
def test_focus_handler_cannot_move_the_write_to_a_stale_document(page, origins, mutation):
    page.evaluate("""mutation => {
      const other = document.createElement('iframe');
      document.body.append(other);
      const el = document.querySelector('#password');
      window.detached = el;
      el.addEventListener('focus', () => { eval(mutation); });
    }""", mutation)
    with pytest.raises(sync_api.Error, match="nothing was written"):
        page.fill("#password", SECRET, expect_origin=origins[0])
    assert page.evaluate("detached.value") == ""
    assert page.evaluate("events") == []


@pytest.mark.e2e
def test_focus_redirect_still_writes_only_the_bound_element(page, origins):
    page.evaluate("""() => {
      password.addEventListener('focus', () => other.focus());
    }""")
    page.fill("#password", "bound-secret", expect_origin=origins[0])
    assert page.input_value("#password") == "bound-secret"
    assert page.input_value("#other") == ""
    assert page.evaluate("events") == [
        ["input", True, "password"], ["change", True, "password"]]


@pytest.mark.e2e
def test_a_detached_handle_and_a_stale_handle_write_nothing(page, origins):
    handle = page.query_selector("#password")
    handle.evaluate("el => el.remove()")
    with pytest.raises(sync_api.Error, match="nothing was written") as failed:
        handle.fill(SECRET, expect_origin=origins[0], timeout=100)
    assert SECRET not in str(failed.value)
    assert handle.evaluate("el => el.value") == ""
    page.goto(origins[1])
    with pytest.raises(sync_api.Error, match="nothing was written"):
        handle.fill(SECRET, expect_origin=origins[0], timeout=100)
    assert page.input_value("#password") == ""
    handle.dispose()


@pytest.mark.e2e
@pytest.mark.parametrize("selector", [
    "#number", "#date", "#checkbox", "#disabled", "#readonly", "#editable",
    "#missing", "input[", "input",
])
def test_failure_paths_never_expose_the_value(page, origins, selector):
    with pytest.raises(sync_api.Error) as failed:
        page.locator(selector).fill(SECRET, expect_origin=origins[0], timeout=100)
    assert SECRET not in str(failed.value)
    assert repr(SECRET)[1:-1] not in str(failed.value)
    assert json.dumps(SECRET)[1:-1] not in str(failed.value)
    assert "nothing was written" in str(failed.value)
    assert "expect_origin=" in str(failed.value)


@pytest.mark.e2e
def test_default_fill_still_types_keys(page):
    page.fill("#password", "abc")
    assert page.input_value("#password") == "abc"
    assert any(event[0] == "keydown" for event in page.evaluate("events"))


@pytest.mark.e2e
def test_async_public_surfaces(firefox_binary, origins):
    from invisible_playwright.async_api import InvisiblePlaywright

    async def exercise():
        async with InvisiblePlaywright(
            seed=42, binary_path=firefox_binary, headless=True, humanize=False
        ) as browser:
            page = await browser.new_page()
            for kind in KINDS:
                await page.goto(origins[0])
                if kind == "page":
                    fill = lambda value, **kw: page.fill("#password", value, **kw)
                elif kind == "frame":
                    fill = lambda value, **kw: page.main_frame.fill("#password", value, **kw)
                elif kind == "locator":
                    fill = page.locator("#password").fill
                else:
                    handle = await page.query_selector("#password")
                    fill = handle.fill
                with pytest.raises(ValueError):
                    await fill(SECRET, expect_origin="not-an-origin")
                for origin, input_type in [(None, "password"), (origins[0], "unknown")]:
                    with pytest.raises(ValueError, match="expect_input_type") as failed:
                        await fill(SECRET, expect_origin=origin, expect_input_type=input_type)
                    assert "expect_origin=" in str(failed.value)
                    assert "nothing was written" in str(failed.value)
                with pytest.raises(async_api.Error, match="nothing was written") as failed:
                    await fill(SECRET, expect_origin=origins[0], expect_input_type="email")
                assert "expect_input_type" in str(failed.value)
                assert SECRET not in str(failed.value)
                with pytest.raises(async_api.Error, match="nothing was written") as failed:
                    await fill(SECRET, expect_origin=origins[1])
                assert SECRET not in str(failed.value)
                assert await page.input_value("#password") == ""
                await fill("async-secret", expect_origin=origins[0], expect_input_type="PaSsWoRd")
                assert await page.input_value("#password") == "async-secret"
                assert await page.evaluate("events") == [
                    ["input", True, "password"], ["change", True, "password"]]
                if kind == "handle":
                    await handle.dispose()

    asyncio.run(exercise())
