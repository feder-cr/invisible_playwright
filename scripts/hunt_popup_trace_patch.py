"""Hunt scaffolding (never merged): every engine event of a page's birth, per session, written as JSON lines to $INVPW_HUNT_POPUP_TRACE,
and the moment each page is announced with the url the client is told. Applied to src/invisible_playwright/_juggler/server.py."""
import io

p = "src/invisible_playwright/_juggler/server.py"
raw = io.open(p, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in raw else "\n"
s = raw.replace("\r\n", "\n")

helper = '''

# --- hunt scaffolding (never merged): the birth of every page, per session -----------------------------------------
_HUNT_TRACE_PATH = os.environ.get("INVPW_HUNT_POPUP_TRACE") or ""
_HUNT_TRACE_LOCK = threading.Lock()


def _hunt_trace(**row) -> None:
    if not _HUNT_TRACE_PATH:
        return
    row["t"] = round(time.time(), 4)
    row["pid"] = os.getpid()
    try:
        with _HUNT_TRACE_LOCK, open(_HUNT_TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\\n")
    except Exception:
        pass


class _Adoption:'''
assert s.count("\nclass _Adoption:") == 1
s = s.replace("\nclass _Adoption:", helper, 1)

old = '''    def _route_browser_event(self, method: str, params: Dict, session) -> None:
        if session and method in _Adoption.TRACED:'''
new = '''    def _route_browser_event(self, method: str, params: Dict, session) -> None:
        if _HUNT_TRACE_PATH and (method in _Adoption.TRACED or method in ("Page.eventFired", "Browser.attachedToTarget", "Page.linkClicked")):
            info = params.get("targetInfo") or {}
            _hunt_trace(session=session or params.get("sessionId"), method=method, frame=params.get("frameId"),
                        url=params.get("url") or info.get("url"), nav=params.get("navigationId"), name=params.get("name"),
                        target=info.get("targetId"), opener=info.get("openerId"), error=params.get("errorText"))
        if session and method in _Adoption.TRACED:'''
assert old in s
s = s.replace(old, new, 1)

old = '''        page.announce(context, replay=not adoption.requested)
        with self._targets_lock:
            adoption.state = "announced"'''
new = '''        page.announce(context, replay=not adoption.requested)
        try:
            _hunt_trace(session=adoption.session, method="ANNOUNCED", target=adoption.target_id,
                        url=getattr(page, "url", None) if not callable(getattr(page, "url", None)) else None,
                        requested=adoption.requested, trace=adoption.trace)
        except Exception:
            pass
        with self._targets_lock:
            adoption.state = "announced"'''
assert old in s
s = s.replace(old, new, 1)
for mod in ("import json\n", "import os\n", "import threading\n", "import time\n"):
    assert mod in s, mod
io.open(p, "w", encoding="utf-8", newline="").write(s.replace("\n", nl))
print("patched trace")
