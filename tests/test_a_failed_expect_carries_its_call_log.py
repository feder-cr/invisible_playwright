"""A failed expect says what it waited for, as Playwright's does ([B341]).

Playwright's server keeps a log for every call and sends it with a failure;
the client prints it under "Call log:", and its own suite reads lines of it
(`Expect "to_be_visible" with timeout 111ms`, `locator resolved to <input/>`).
This server sent none, so a failed expect said what was expected and what was
seen, and nothing of the wait in between: seven tests of Playwright's suite
were red on the message alone, with the verdict right.

The lines are Playwright 1.61's, from where its server writes them: the title
and the timeout (its dispatcher), what is waited for (`frames.ts` `expect`),
and each reading (`_expectInternal`, which builds them inside the page, so the
values are rendered as JavaScript renders them). No browser here: the reading's
JavaScript runs under Node with a stand-in for the injected script, and the
server's own functions run with a stand-in for the page.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from invisible_playwright._juggler import dispatcher, server, transport
from invisible_playwright._juggler.dispatcher import CallLog, ExpectNotMet

pytestmark = pytest.mark.unit


# --- the reading, in the page -------------------------------------------------

#: A stand-in for the injected script: what a reading of `_EXPECT` uses of it.
FAKE_INJECTED = """
globalThis.document = {};
function fake(elements, result) {
  return {
    parseSelector: (s) => s,
    querySelectorAll: () => elements,
    strictModeViolationError: () => new Error('strict mode violation'),
    previewNode: (e) => e.preview,
    expect: async () => result,
  };
}
"""

READINGS = [
    # (elements, the injected expect's answer, options) -> (log, unexpected)
    ([{"preview": "<input/>"}], {"matches": False, "received": {"value": "x"}},
     {"expression": "to.have.text", "isNot": False},
     ("  locator resolved to <input/>", '  unexpected value "x"')),
    ([], {"matches": False, "missingReceived": True},
     {"expression": "to.be.attached", "isNot": False},
     ("", None)),
    ([{"preview": "<li>"}, {"preview": "<li>"}], {"matches": False, "received": {"value": 2}},
     {"expression": "to.have.count", "isNot": False},
     ("  locator resolved to 2 elements", '  unexpected value "2"')),
    ([{"preview": "<li>"}], {"matches": True, "received": {"value": 1}},
     {"expression": "to.have.count", "isNot": False},
     ("  locator resolved to 1 element", None)),
    ([{"preview": "<b>"}], {"matches": False, "received": {"value": True}},
     {"expression": "to.be.checked", "isNot": False},
     ("  locator resolved to <b>", '  unexpected value "true"')),
    ([{"preview": "<b>"}], {"matches": False, "received": {"value": {"a": 1}}},
     {"expression": "to.have.property", "isNot": False},
     ("  locator resolved to <b>", '  unexpected value "[object Object]"')),
    ([{"preview": "<b>"}], {"matches": False, "received": {"value": ["a", "b"]}},
     {"expression": "to.have.text.array", "isNot": False},
     ("  locator resolved to 1 element", None)),
    ([{"preview": "<b>"}], {"matches": True, "received": {"value": "x"}},
     {"expression": "to.have.text", "isNot": True},
     ("  locator resolved to <b>", '  unexpected value "x"')),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_each_reading_logs_what_it_resolved_and_the_value_that_did_not_hold():
    """Under `isNot` a reading that MATCHES is the unexpected one; an array and
    a missing element have no value line; a number prints as `2`, a boolean as
    `true`, an object as `[object Object]`, as a template literal prints them."""
    script = FAKE_INJECTED + "const reading = %s;\n" % server.FrameDispatcher._EXPECT
    script += "(async () => { const out = [];\n"
    for elements, result, options, _ in READINGS:
        script += ("  out.push(await reading(fake(%s, %s), 'sel', %s));\n"
                   % (json.dumps(elements), json.dumps(result), json.dumps(options)))
    script += "  console.log(JSON.stringify(out)); })();\n"
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                    text=True, timeout=60, check=True).stdout)
    for (_, _, options, (log, unexpected)), reading in zip(READINGS, out):
        assert reading["log"] == log, (options, reading)
        assert reading.get("unexpected") == unexpected, (options, reading)


# --- the call: its lines, in order, on the failure ---------------------------

class _Injected:
    def call(self, frame_id, declaration, *arguments, **options):
        assert "asLocator" in declaration
        return 'locator("#a1")'


def _frame(readings):
    frame = server.FrameDispatcher.__new__(server.FrameDispatcher)
    frame.page = SimpleNamespace(injected=_Injected())
    frame.frame_id = "F"
    answers = list(readings)
    frame._expect_probe = lambda expression, selector, params: answers.pop(0) if len(answers) > 1 else answers[0]
    return frame


def test_the_failure_carries_the_title_the_wait_and_every_reading():
    token = dispatcher._CURRENT_CALL.set(CallLog('Expect "to_be_visible"'))
    try:
        reading = {"matches": False, "log": "  locator resolved to <div>",
                   "unexpected": '  unexpected value "hidden"', "received": "hidden"}
        with pytest.raises(ExpectNotMet) as failure:
            _frame([reading]).op_expect({"expression": "to.be.visible",
                                         "selector": "#a1", "timeout": 111})
    finally:
        dispatcher._CURRENT_CALL.reset(token)
    log = failure.value.log
    assert log[:4] == ['Expect "to_be_visible" with timeout 111ms',
                       'waiting for locator("#a1")',
                       "  locator resolved to <div>",
                       '  unexpected value "hidden"']
    assert len(log) > 4 and set(log[2:]) == {"  locator resolved to <div>",
                                             '  unexpected value "hidden"'}


def test_a_page_assertion_has_no_wait_line_and_no_timeout_says_none():
    token = dispatcher._CURRENT_CALL.set(CallLog('Expect "to_have_title"'))
    try:
        frame = _frame([{"matches": True, "log": ""}])
        assert frame.op_expect({"expression": "to.have.title"}) is None
        assert dispatcher.current_call().lines == ['Expect "to_have_title"']
    finally:
        dispatcher._CURRENT_CALL.reset(token)


def test_the_reply_carries_the_log_where_the_client_reads_it():
    sent = []
    t = transport.InProcessTransport.__new__(transport.InProcessTransport)
    t.emit_message = sent.append
    transport.InProcessTransport.reply_error(
        t, 7, ExpectNotMet("did not hold", {"timedOut": True}, ["one", "two"]))
    assert sent[0]["log"] == ["one", "two"]
    assert sent[0]["errorDetails"] == {"timedOut": True}
    sent.clear()
    transport.InProcessTransport.reply_error(t, 8, RuntimeError("other"))
    assert "log" not in sent[0]


def test_each_call_gets_its_own_log_titled_from_its_metadata():
    seen = []

    class _Obj:
        def call(self, method, params):
            seen.append((dispatcher.current_call().title, list(dispatcher.current_call().lines)))
            dispatcher.current_call().log("line")

    s = dispatcher.Server.__new__(dispatcher.Server)
    s.root_guid = "root"
    s._lock = __import__("threading").Lock()
    s._objects = {"g": _Obj()}
    s.handle({"guid": "g", "method": "expect", "params": {},
              "metadata": {"title": 'Expect "to_be_visible"'}})
    s.handle({"guid": "g", "method": "click", "params": {}, "metadata": {}})
    assert seen == [('Expect "to_be_visible"', []), ("click", [])]
    assert dispatcher.current_call().lines == []
