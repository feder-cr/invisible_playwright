"""Worker policy is an engine command, sent before the context is handed out."""
from __future__ import annotations

import pytest

from invisible_playwright._juggler.connection import EventListeners, ProtocolError
from invisible_playwright._juggler.protocol import COMMANDS
from invisible_playwright._juggler.server import (
    BrowserDispatcher,
    BrowserTypeDispatcher,
    JugglerServer,
)

pytestmark = pytest.mark.unit
_COMMAND = "Browser.setServiceWorkersBlocked"


class _Connection(EventListeners):
    def __init__(self, failure=None):
        super().__init__()
        self.sent = []
        self.failure = failure
        self.closed = False

    def send(self, method, params=None, session=None, timeout=30):
        self.sent.append((method, params))
        if method == _COMMAND and self.failure:
            raise self.failure
        return {"browserContextId": "CTX"}

    def close(self):
        self.closed = True


def _browser(failure=None):
    conn = _Connection(failure)
    browser = BrowserDispatcher(JugglerServer(), None, conn, "151.0")
    conn.sent.clear()
    return browser, conn


@pytest.mark.parametrize("persistent", [False, True])
def test_block_command_precedes_context_exposure_and_other_options(persistent):
    browser, conn = _browser()
    method = browser.op_default_context if persistent else browser.op_new_context
    method({"serviceWorkers": "block", "locale": "en-US"})
    if not persistent:
        assert conn.sent.pop(0)[0] == "Browser.createBrowserContext"
    expected = {"blocked": True}
    if not persistent:
        expected["browserContextId"] = "CTX"
    assert conn.sent[0] == (_COMMAND, expected)
    assert conn.sent[1][0] == "Browser.setLocaleOverride"
    assert len(browser.contexts) == 1
    assert not browser.contexts[0].pages
    assert all(name != "Browser.setInitScripts" for name, _ in conn.sent)


@pytest.mark.parametrize("policy", [None, "allow"])
def test_unblocked_contexts_do_not_send_a_worker_command(policy):
    browser, conn = _browser()
    browser._apply_context_options("CTX", {"serviceWorkers": policy})
    assert conn.sent == []


@pytest.mark.parametrize("message", [
    f"{_COMMAND}: Failed to unregister service worker",
    f"{_COMMAND}: no response in 30s",
    f"{_COMMAND}: ERROR: method '{_COMMAND}' is not supported",
])
def test_an_engine_failure_reaches_the_caller_and_the_context_goes(message):
    """No probe and no rewording: the pinned engine has the command, so any
    failure is the engine's answer, passed through as it came. The context
    the caller never received is removed rather than leaked."""
    failure = ProtocolError(message)
    browser, conn = _browser(failure)
    with pytest.raises(ProtocolError) as raised:
        browser.op_new_context({"serviceWorkers": "block"})
    assert raised.value is failure
    assert conn.sent[-1] == (
        "Browser.removeBrowserContext", {"browserContextId": "CTX"})
    assert not browser.contexts


def test_failed_persistent_launch_closes_the_browser(monkeypatch):
    browser, conn = _browser(ProtocolError(f"{_COMMAND}: no response in 30s"))
    browser_type = BrowserTypeDispatcher(browser.server)
    monkeypatch.setattr(browser_type, "op_launch",
                        lambda _params: {"browser": browser.channel})
    with pytest.raises(ProtocolError):
        browser_type.op_launch_persistent(
            {"userDataDir": "/profile", "serviceWorkers": "block"})
    assert conn.closed
    assert (_COMMAND, {"blocked": True}) in conn.sent
    assert not browser.contexts


def test_worker_protocol_addresses_the_default_context_without_a_null_id():
    assert COMMANDS[_COMMAND] == {
        "params": {"k": "Object", "fields": {
            "browserContextId": {"k": "Optional", "of": {"k": "String"}},
            "blocked": {"k": "Boolean"},
        }},
        "returns": None,
    }
