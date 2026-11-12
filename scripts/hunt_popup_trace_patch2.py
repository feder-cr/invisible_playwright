"""Hunt scaffolding (never merged), second layer over hunt_popup_trace_patch.py: what the wrapper SENDS to the client for a frame
(its creation with the url of its initializer, every `navigated` and `loadstate`), with the page's session, and, in the tests'
conftest, the start and end of every test with its node id and the worker's pid. Run from the root of a checkout that already has
the first layer. Hooks by anchor, so it applies to main and to the fix branch alike."""
import io

p = "src/invisible_playwright/_juggler/server.py"
raw = io.open(p, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in raw else "\n"
s = raw.replace("\r\n", "\n")
assert "_hunt_trace(" in s, "the first layer is missing"

# 1. the frame's creation, with the url of its initializer
anchor = '''                          "loadStates": load_states or ["commit"]})
'''
assert s.count(anchor) == 1, "frame initializer anchor"
s = s.replace(anchor, anchor + '''        try:
            _hunt_trace(session=getattr(page, "session", None), method="FRAME_CREATED", frame=frame_id, url=url,
                        name=",".join(load_states or ["commit"]), main=parent_frame is None)
        except Exception:
            pass
''', 1)

# 2. every navigated / loadstate the frame sends the client
sig = "    def emit(self, method: str, params: Optional[Dict] = None) -> None:\n"
cls = s.index("class FrameDispatcher(Dispatcher):")
at = s.index(sig, cls) + len(sig)
s = s[:at] + '''        if _HUNT_TRACE_PATH and method in ("navigated", "loadstate"):
            try:
                _hunt_trace(session=getattr(self.page, "session", None), method="EMIT " + method, frame=self.frame_id,
                            url=(params or {}).get("url"), name=json.dumps(params or {})[:120],
                            announced=getattr(self.page, "_announced", None))
            except Exception:
                pass
''' + s[at:]
io.open(p, "w", encoding="utf-8", newline="").write(s.replace("\n", nl))

# 3. the start and end of every test
c = "tests/conftest.py"
raw = io.open(c, encoding="utf-8", newline="").read()
cnl = "\r\n" if "\r\n" in raw else "\n"
s = raw.replace("\r\n", "\n")
s += '''


# --- hunt scaffolding (never merged): every test's start and end in the popup trace, to tie each popup to its test --------
import json as _hunt_json
import os as _hunt_os
import time as _hunt_time


@pytest.fixture(autouse=True)
def _hunt_mark_the_test(request):
    path = _hunt_os.environ.get("INVPW_HUNT_POPUP_TRACE")

    def mark(what, **extra):
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_hunt_json.dumps(dict({"t": round(_hunt_time.time(), 4), "pid": _hunt_os.getpid(), "method": what,
                                                "test": request.node.nodeid}, **extra)) + "\\n")

    mark("TEST_START")
    yield
    rep = getattr(request.node, "_hunt_call_report", None)
    mark("TEST_END", outcome=getattr(rep, "outcome", "unknown"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item._hunt_call_report = rep
'''
io.open(c, "w", encoding="utf-8", newline="").write(s.replace("\n", cnl))
print("patched trace layer 2")
