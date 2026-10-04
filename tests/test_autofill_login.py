"""One engine write, with explicit outcomes and no credentials in errors."""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from invisible_playwright._behaviour import PageActs
from invisible_playwright._juggler.actions import Actions, ElementNotActionable
from invisible_playwright._juggler.connection import ProtocolError, TargetClosedError
from invisible_playwright._juggler.injected import EvaluationError
from invisible_playwright._juggler.server import FrameDispatcher
from invisible_playwright._pw import async_api, sync_api
from invisible_playwright._pw._impl._frame import Frame
from invisible_playwright._pw._impl._page import Page

ORIGIN = "https://example.com"
USERNAME = "username-'\\\n-\u00e9-private"
PASSWORD = "password-\"\\\n-\u00e9-private"
OPTIONS = dict(origin=ORIGIN, username=USERNAME, username_selector="#username",
               username_type="email", password=PASSWORD, password_selector="#password")


def _assert_redacted(error):
    message = str(error)
    for value in (USERNAME, PASSWORD):
        for spelling in (value, repr(value)[1:-1], json.dumps(value)[1:-1],
                         json.dumps(value, ensure_ascii=False)[1:-1]):
            assert spelling not in message


def _actions(result=None):
    injected = Mock()
    injected.query_selector.side_effect = lambda f, s, **kw: s[1:] + "-object"
    injected.element_states.return_value = {"ok": True}
    connection = Mock()
    connection.send.return_value = (
        {"username": "filled", "password": "filled"} if result is None else result)
    actions = Actions(connection, "session", SimpleNamespace(main_frame="main"),
                      injected, acts=PageActs())
    return actions, injected


@pytest.mark.parametrize("origin", [
    ORIGIN, "http://localhost", "https://example.com:8443", "http://127.0.0.1:0",
    "http://[::1]:8000", "https://xn--bcher-kva.example", "ftp://example.com",
    "ws://localhost:8080",
])
def test_serialized_origins(origin):
    from invisible_playwright._origin import validate_expect_origin
    validate_expect_origin(origin)


@pytest.mark.parametrize("origin", [
    None, "", "null", "*", "example.com", "//example.com", "https://example.com/",
    "https://example.com/login", "https://example.com?", "https://example.com#",
    "https://user:secret@example.com", "https://example.com:99999",
    "https://example.com:", "https://example.com:0443", "https://example.com:443",
    "HTTPS://example.com", "https://EXAMPLE.com", " https://example.com",
    "https://exa\nmple.com", "https://example.com\\evil", "file://localhost",
    "data://example.com", "https://[broken]", "http://127.1",
    "https://b\u00fccher.example", 42, False, USERNAME, PASSWORD,
])
def test_invalid_origins_write_nothing_and_do_not_echo_input(origin):
    actions, injected = _actions()
    with pytest.raises(ValueError, match="serialized origin.*nothing was written") as failed:
        actions.autofill_login(**dict(OPTIONS, origin=origin))
    _assert_redacted(failed.value)
    injected.query_selector.assert_not_called()
    actions.c.send.assert_not_called()


def test_one_engine_call_has_both_resolved_fields_without_pointer_or_focus():
    actions, injected = _actions()
    assert actions.autofill_login(**OPTIONS, frame_id="child") is None
    actions.c.send.assert_called_once()
    args, kwargs = actions.c.send.call_args
    assert args == ("Page.autofillLogin", {
        "frameId": "child", "origin": ORIGIN,
        "username": {"objectId": "username-object", "value": USERNAME, "type": "email"},
        "password": {"objectId": "password-object", "value": PASSWORD, "type": "password"},
    })
    assert kwargs["session"] == "session"
    assert 0 < kwargs["timeout"] <= 30
    assert injected.element_states.call_args_list == [
        call("child", field + "-object", ["visible", "enabled", "editable"])
        for field in ("username", "password")
    ]
    assert injected.query_selector.call_args_list == [
        call("child", "#" + field, strict=True) for field in ("username", "password")
    ]
    assert injected.dispose.call_args_list == [
        call("child", field + "-object") for field in ("password", "username")
    ]
    injected.call.assert_not_called()
    injected.scroll_into_view.assert_not_called()


@pytest.mark.parametrize("field", ["username", "password"])
@pytest.mark.parametrize("value", ["", "single-field"])
@pytest.mark.parametrize("status", ["filled", "unchanged"])
def test_single_field_calls(field, value, status):
    actions, injected = _actions({field: status})
    assert actions.autofill_login(
        origin=ORIGIN, **{field: value, field + "_selector": "#field"}) is None
    sent = actions.c.send.call_args.args[1]
    assert set(sent) == {"frameId", "origin", field}
    assert sent[field]["value"] == value
    injected.query_selector.assert_called_once()


@pytest.mark.parametrize("options", [
    {}, {"password": PASSWORD}, {"password_selector": "#password"},
    {"username": USERNAME}, {"username_selector": "#username"},
    {"username_type": "email"},
    {"password": 123, "password_selector": "#password"},
    {"username": USERNAME, "username_selector": ""},
    dict(OPTIONS, username_type=PASSWORD),
    dict(OPTIONS, username_type="password"),
])
def test_invalid_field_pairs_never_reach_resolution(options):
    actions, injected = _actions()
    with pytest.raises(ValueError, match="nothing was written") as failed:
        actions.autofill_login(**dict(options, origin=ORIGIN))
    _assert_redacted(failed.value)
    injected.query_selector.assert_not_called()
    actions.c.send.assert_not_called()


def test_actionability_failure_is_no_write_and_redacted():
    actions, injected = _actions()
    injected.element_states.return_value = {"ok": False, "missing": "editable"}
    with pytest.raises(ElementNotActionable, match="nothing was written") as failed:
        actions.autofill_login(**dict(OPTIONS, username_selector=USERNAME), timeout=0.001)
    _assert_redacted(failed.value)
    actions.c.send.assert_not_called()


@pytest.mark.parametrize("message", [
    "autofillLogin refused: wrong origin",
    "Page.autofillLogin: autofillLogin refused: password notconnected",
    'Page.autofillLogin: error in channel "content::9/10/2": exception while running '
    'method "autofillLogin" in namespace "page": autofillLogin refused: username: origin mismatch ',
])
def test_engine_refusals_say_nothing_was_written_and_are_not_retried(message):
    actions, injected = _actions()
    actions.c.send.side_effect = ProtocolError(message)
    with pytest.raises(RuntimeError, match="nothing was written"):
        actions.autofill_login(**OPTIONS)
    actions.c.send.assert_called_once()
    assert injected.query_selector.call_count == 2


def test_an_engine_without_the_command_wrote_nothing():
    """The dispatcher refuses an unknown method before any page code runs."""
    actions, _ = _actions()
    actions.c.send.side_effect = ProtocolError(
        "Page.autofillLogin: ERROR: method 'Page.autofillLogin' is not supported")
    with pytest.raises(RuntimeError, match="has no Page.autofillLogin; nothing was written"):
        actions.autofill_login(**OPTIONS)
    actions.c.send.assert_called_once()


@pytest.mark.parametrize("status", ["skipped", "cleared", "uncleared", "altered"])
def test_nonfilled_status_names_every_field(status):
    actions, _ = _actions({"username": "filled", "password": status, "reason": "type changed"})
    with pytest.raises(RuntimeError) as failed:
        actions.autofill_login(**OPTIONS)
    message = str(failed.value)
    assert "username=filled" in message
    assert "password=" + status in message
    assert "type changed" in message
    assert "nothing was written" not in message
    actions.c.send.assert_called_once()


@pytest.mark.parametrize("result", [{}, {"username": "filled"}, {"password": "mystery"}, []])
def test_incomplete_or_invalid_reply_is_not_success(result):
    actions, _ = _actions(result)
    with pytest.raises(RuntimeError, match="write outcome unknown"):
        actions.autofill_login(**OPTIONS)
    actions.c.send.assert_called_once()


@pytest.mark.parametrize("error_type", [
    ProtocolError, TimeoutError, TargetClosedError, OSError, EvaluationError,
])
def test_failures_after_send_have_unknown_outcome_and_never_retry(error_type):
    actions, _ = _actions()
    actions.c.send.side_effect = error_type("notconnected " + PASSWORD)
    with pytest.raises(RuntimeError, match="write outcome unknown") as failed:
        actions.autofill_login(**OPTIONS)
    assert "nothing was written" not in str(failed.value)
    _assert_redacted(failed.value)
    actions.c.send.assert_called_once()


@pytest.mark.parametrize("stage", ["resolve", "refused", "transport", "status"])
def test_all_error_paths_redact_literal_repr_and_json_credentials(stage):
    echo = " ".join(spelling for value in (USERNAME, PASSWORD) for spelling in
                    (value, repr(value), json.dumps(value), json.dumps(value, ensure_ascii=False)))
    actions, injected = _actions()
    if stage == "resolve":
        injected.query_selector.side_effect = EvaluationError(echo)
    elif stage == "status":
        actions.c.send.return_value = {"username": "cleared", "password": "skipped", "reason": echo}
    else:
        prefix = "Page.autofillLogin: autofillLogin refused: " if stage == "refused" else ""
        actions.c.send.side_effect = ProtocolError(prefix + echo)
    with pytest.raises(Exception) as failed:
        actions.autofill_login(**OPTIONS)
    _assert_redacted(failed.value)
    assert failed.value.__suppress_context__


def _dispatcher():
    frame = object.__new__(FrameDispatcher)
    frame.frame_id = "main"
    frame.page = SimpleNamespace(actions=Mock())
    frame.enter_frames = Mock(side_effect=lambda s: ("main", s))
    return frame


def test_dispatcher_carries_options_and_converts_milliseconds():
    frame = _dispatcher()
    assert frame.call("autofillLogin", dict(OPTIONS, timeout=2500)) is None
    frame.actions.autofill_login.assert_called_once_with(
        **OPTIONS, frame_id="main", timeout=2.5)


def test_both_selectors_must_resolve_in_the_same_frame():
    frame = _dispatcher()
    frame.enter_frames.side_effect = [("main", "#username"), ("child", "#password")]
    with pytest.raises(Exception, match="same frame.*nothing was written"):
        frame.call("autofillLogin", OPTIONS)
    frame.actions.autofill_login.assert_not_called()


def test_dispatcher_resolution_errors_are_redacted_before_the_client():
    frame = _dispatcher()
    frame.enter_frames.side_effect = EvaluationError(USERNAME + PASSWORD)
    with pytest.raises(Exception, match="nothing was written") as failed:
        frame.call("autofillLogin", OPTIONS)
    _assert_redacted(failed.value)
    frame.actions.autofill_login.assert_not_called()


@pytest.mark.parametrize("api", [sync_api, async_api])
@pytest.mark.parametrize("name", ["Page", "Frame"])
def test_public_api_signature_and_forwarding(api, name):
    method = getattr(getattr(api, name), "autofill_login")
    signature = inspect.signature(method)
    for keyword in (*OPTIONS, "timeout"):
        assert signature.parameters[keyword].kind == inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["timeout"].default == 30000
    assert "Page.autofillLogin" in method.__doc__
    impl = SimpleNamespace(autofill_login=AsyncMock())
    target = SimpleNamespace(_impl_obj=impl, _sync=asyncio.run)
    if api is sync_api:
        assert method(target, **OPTIONS, timeout=2500) is None
    else:
        assert asyncio.run(method(target, **OPTIONS, timeout=2500)) is None
    impl.autofill_login.assert_awaited_once_with(**OPTIONS, timeout=2500)


def test_impl_page_delegates_to_its_main_frame():
    target = SimpleNamespace(_main_frame=SimpleNamespace(autofill_login=AsyncMock()))
    assert asyncio.run(Page.autofill_login(target, **OPTIONS)) is None
    target._main_frame.autofill_login.assert_awaited_once_with(**OPTIONS, timeout=30000)


def test_impl_frame_sends_one_channel_call():
    target = SimpleNamespace(_channel=SimpleNamespace(send=AsyncMock()), _timeout=Mock())
    assert asyncio.run(Frame.autofill_login(target, **OPTIONS)) is None
    target._channel.send.assert_awaited_once_with(
        "autofillLogin", target._timeout, dict(OPTIONS, timeout=30000))


@pytest.mark.parametrize("error_type", [sync_api.Error, sync_api.TimeoutError])
def test_client_transport_errors_are_unknown_and_drop_driver_metadata(error_type):
    original = error_type(" ".join((USERNAME, PASSWORD, repr(PASSWORD), json.dumps(USERNAME))))
    original._stack = PASSWORD
    original._log = [USERNAME]
    target = SimpleNamespace(
        _channel=SimpleNamespace(send=AsyncMock(side_effect=original)), _timeout=Mock())
    with pytest.raises(error_type, match="write outcome unknown") as failed:
        asyncio.run(Frame.autofill_login(target, **OPTIONS))
    _assert_redacted(failed.value)
    assert failed.value.stack is None
    assert failed.value._log is None
    assert failed.value.__suppress_context__


def test_impl_validation_precedes_the_channel():
    target = SimpleNamespace(_channel=SimpleNamespace(send=AsyncMock()), _timeout=Mock())
    with pytest.raises(ValueError, match="nothing was written"):
        asyncio.run(Frame.autofill_login(target, **dict(OPTIONS, origin=None)))
    target._channel.send.assert_not_called()


@pytest.mark.parametrize("error_type", [OSError, TimeoutError, TargetClosedError])
def test_client_failures_outside_playwright_error_are_unknown(error_type):
    target = SimpleNamespace(
        _channel=SimpleNamespace(send=AsyncMock(side_effect=error_type(PASSWORD))),
        _timeout=Mock())
    with pytest.raises(error_type, match="write outcome unknown") as failed:
        asyncio.run(Frame.autofill_login(target, **OPTIONS))
    _assert_redacted(failed.value)


def test_resolution_retries_only_before_send():
    actions, injected = _actions()
    injected.element_states.side_effect = [
        {"ok": False, "missing": "error:notconnected"}, {"ok": True}, {"ok": True}]
    actions.autofill_login(**OPTIONS)
    assert injected.query_selector.call_count == 3
    actions.c.send.assert_called_once()


def test_username_type_is_case_insensitive():
    actions, _ = _actions()
    actions.autofill_login(**dict(OPTIONS, username_type="EMAIL"))
    assert actions.c.send.call_args.args[1]["username"]["type"] == "email"


def test_timeout_is_shared_by_resolution_and_send(monkeypatch):
    actions, _ = _actions()
    clock = iter([0, 0, 0, 2, 2, 5])
    monkeypatch.setattr("invisible_playwright._juggler.actions.time.monotonic", lambda: next(clock))
    actions.autofill_login(**OPTIONS, timeout=30)
    assert actions.c.send.call_args.kwargs["timeout"] == 25


def test_expired_budget_never_sends(monkeypatch):
    actions, _ = _actions()
    clock = iter([0, 0, 0, 2, 2, 31])
    monkeypatch.setattr("invisible_playwright._juggler.actions.time.monotonic", lambda: next(clock))
    with pytest.raises(ElementNotActionable, match="nothing was written"):
        actions.autofill_login(**OPTIONS, timeout=30)
    actions.c.send.assert_not_called()


def test_zero_timeout_has_no_engine_reply_deadline():
    actions, _ = _actions()
    actions.autofill_login(**OPTIONS, timeout=0)
    assert actions.c.send.call_args.kwargs["timeout"] is None
