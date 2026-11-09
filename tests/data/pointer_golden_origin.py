"""Golden generator: what the page's engine receives from the in-process client's
humanised pointer, computed with the CLIENT-SIDE code of commit 5d9ad70
(`invisible_playwright/_cursor.py` wrappers: `_approach`, `_wrap_mouse_move`,
`_wrap_mouse_click`, `_wrap_mouse_wheel`), through a real `invisible_core`
`Actions` object whose connection only records.

Run against the OLD tree only:
    PYTHONPATH=<worktree at 5d9ad70>/src python gen_golden.py > pointer_golden.json

REVISION 1 (feat/rt-dblmove): the `explicit_pointer` session, the only one with
a `Mouse.click`, no longer carries the duplicate the client's `Mouse._click`
made. That click moved to the point (`move`) and `click_at` then moved there
again as its own approach: two `mousemove` with the same coordinates at the same
instant before the press, which no mouse reports and a page can count. The
server now sends the stroke and lets `Actions.click_at` make the one move onto
the point (`Pointer.click_at`). Only that session changes (one `mousemove`
fewer; the per-event `latency` draws follow the event ordinal, so the ones
after it are the draws of the same ordinals); the other three sessions are
byte-for-byte what the client made at 5d9ad70. Regenerate that session with
the CURRENT tree, never by hand:
    PYTHONPATH=<this tree>/src python tests/data/pointer_golden_origin.py --revise-mouse-click
It refuses to write unless the new session is the old one minus exactly one
repeated mousemove on the click point.

REVISION 2 (feat/rt-dblmove): the same session had a second repeat, at the
START of the stroke of the `Mouse.click` that follows the explicit move and a
pause: the stroke's first waypoint is its start point, and the engine had
already been told the pointer stood on that pixel. `Pointer._travel` now does
not send a first waypoint that coincides with the position the engine knows.
Again one `mousemove` fewer in `explicit_pointer` only (t, x, y and type of
every other event unchanged; the latency draws follow the event ordinal); the
other three sessions are identical event for event, and the generator checks
that before it writes:
    PYTHONPATH=<this tree>/src python tests/data/pointer_golden_origin.py --revise-standstill
"""
import asyncio
import json
import sys
import time
from types import SimpleNamespace

from invisible_core.juggler import PageActs
from invisible_core.juggler import actions as core_actions
from invisible_core.juggler.actions import Actions

from invisible_playwright import _cursor

VIEWPORT = {"width": 1280, "height": 720}
BOXES = {
    "#b": {"x": 400.0, "y": 300.0, "width": 100.0, "height": 40.0},
    "#c": {"x": 880.5, "y": 120.25, "width": 160.0, "height": 36.0},
    "#tiny": {"x": 700.0, "y": 500.0, "width": 8.0, "height": 8.0},
}


HITS = "all"


def hit_ok(selector, x, y):
    """The element's shape as the engine's hit test sees it: a box ("all"), or
    a round control whose only sure point is the centre ("centre")."""
    if HITS == "all":
        return True
    b = BOXES[selector]
    return abs(x - (b["x"] + b["width"] / 2.0)) <= 1.0 and abs(y - (b["y"] + b["height"] / 2.0)) <= 1.0


class VT:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, s):
        self.t += max(0.0, s)

    def sync_sleep(self, s):
        self.t += max(0.0, s)


class Conn:
    def __init__(self, vt):
        self.vt = vt
        self.log = []

    def send(self, method, params=None, session=None, timeout=None, **kw):
        if method.startswith("Page.dispatch"):
            rec = {"t": round(self.vt.t, 9), "m": method}
            rec.update({k: v for k, v in (params or {}).items()
                        if k not in ("landsOn",)})
            self.log.append(rec)
        if method == "Page.getContentQuads":
            b = BOXES[self.current]
            return {"quads": [{"p1": {"x": b["x"], "y": b["y"]},
                               "p2": {"x": b["x"] + b["width"], "y": b["y"]},
                               "p3": {"x": b["x"] + b["width"], "y": b["y"] + b["height"]},
                               "p4": {"x": b["x"], "y": b["y"] + b["height"]}}]}
        if method == "Page.dispatchMouseEvent" and "landsOn" in (params or {}):
            return {"landing": {"landed": True, "on": "el"}}
        return {}


class Injected:
    def __init__(self, conn):
        self.conn = conn
        self.checked = False

    def query_selector(self, f, selector, strict=False):
        if selector in BOXES:
            self.conn.current = selector
            return "el"
        return None

    def element_states(self, f, el, states):
        return {"ok": True}

    def evaluate(self, f, expr, **kw):
        return {"w": VIEWPORT["width"], "h": VIEWPORT["height"]}

    def check_hit_target(self, f, el, point):
        return "done" if hit_ok(self.conn.current, point[0], point[1]) else "other"

    def element_state(self, f, el, state):
        # unchecked before the click, checked after it
        self.n = getattr(self, "n", 0) + 1
        return self.n > 1

    def dispose(self, f, el):
        pass

    def scroll_into_view(self, *a, **k):
        raise AssertionError("not expected")


class Life:
    main_frame = "main"


def build_actions(vt, seed, budget, number):
    conn = Conn(vt)
    inj = Injected(conn)
    act = Actions(conn, "sess", Life(), inj, acts=PageActs(number),
                  session_seed=seed, motion_budget_s=budget, layout="us")
    return act, conn, inj


# ── old client side ─────────────────────────────────────────────────────────

class OMouse:
    def __init__(self, page):
        self._channel = SimpleNamespace(_object=page)


class OPage:
    def __init__(self, ctx, number, actions):
        self._browser_context = ctx
        self._initializer = {"pageNumber": number}
        self._viewport_size = dict(VIEWPORT)
        self.mouse = OMouse(self)
        self.actions = actions


class OHandle:
    def __init__(self, selector, hits_fn):
        self.selector = selector
        self._channel = SimpleNamespace(send=self._send)

    async def _send(self, method, tc=None, params=None, **kw):
        # the real server answers with the ACTION'S OWN hit test (Actions.hit_target)
        assert method == "checkHitTarget"
        return hit_ok(self.selector, params["point"]["x"], params["point"]["y"])

    async def bounding_box(self):
        return dict(BOXES[self.selector])

    async def dispose(self):
        pass


class OFrame:
    def __init__(self, page, hits_fn):
        self._page = page
        self.hits_fn = hits_fn

    async def query_selector(self, selector):
        if selector not in BOXES:
            return None
        return OHandle(selector, self.hits_fn)


def scenario(spec):
    seed, number, budget = spec["seed"], spec["number"], spec["budget"]
    vt = VT()
    # one timer for the cursor and the sleeps of the actions
    _cursor._TIMER = vt
    core_actions.time = SimpleNamespace(sleep=vt.sync_sleep, monotonic=time.monotonic,
                                        perf_counter=time.perf_counter)
    act, conn, inj = build_actions(vt, seed, budget, number)
    class B: pass
    browser = B()
    assert _cursor._ensure_patched()
    assert _cursor.enable_for(browser, seed=seed, max_seconds=budget)
    class C: pass
    ctx = C()
    ctx._browser = browser
    page = OPage(ctx, number, act)

    async def fake_move(mouse, x, y, steps=None):
        act.move(x, y)
    _cursor._ORIGINAL_MOUSE_MOVE = fake_move

    async def fake_click(mouse, x, y, **kw):
        act.click_at(x, y, clicks=kw.get("clickCount") or 1)
    _cursor._ORIGINAL_MOUSE_CLICK = fake_click

    async def fake_wheel(mouse, dx, dy):
        act.wheel(dx, dy)
    _cursor._ORIGINAL_MOUSE_WHEEL = fake_wheel

    global HITS
    HITS = spec.get("hits", "all")
    frame = OFrame(page, None)

    async def run():
        for op in spec["ops"]:
            kind = op[0]
            if kind == "wait":
                vt.t += op[1]
                continue
            if kind in ("click", "hover", "dblclick", "tap", "check"):
                sel = op[1]
                kw = dict(op[2]) if len(op) > 2 else {}
                ov = await _cursor._approach(frame, (sel,), kw, timer=vt)
                kw = {**kw, **(ov or {})}
                pos = kw.get("position")
                timeout = (kw["timeout"] / 1000.0) if "timeout" in kw else 30.0
                fn = getattr(act, {"check": "check"}.get(kind, kind))
                fn(sel, timeout=timeout, position=pos)
            elif kind == "move":
                w = _cursor._wrap_mouse_move(fake_move)
                await w(page.mouse, op[1], op[2], None)
            elif kind == "mouse_click":
                # Mouse._click wrapper calls self.move (wrapped) then the original
                mv = _cursor._wrap_mouse_move(fake_move)

                class M:
                    _channel = SimpleNamespace(_object=page)

                    async def move(self, x, y, steps=None):
                        return await mv(self, x, y, steps)
                w = _cursor._wrap_mouse_click(fake_click)
                await w(M(), op[1], op[2], **(op[3] if len(op) > 3 else {}))
            elif kind == "wheel":
                w = _cursor._wrap_mouse_wheel(fake_wheel)

                class M2:
                    _channel = SimpleNamespace(_object=page)
                await w(M2(), op[1], op[2])
            else:
                raise ValueError(kind)
    asyncio.run(run())
    return conn.log


SCENARIOS = {
    "session_a": {
        "seed": 4242, "number": 0, "budget": 1.5, "ops": [
            ["click", "#b"], ["wait", 0.1], ["hover", "#c"], ["wait", 3.0],
            ["dblclick", "#b"], ["wait", 2.0], ["tap", "#c"], ["wait", 0.05],
            ["click", "#c", {"position": {"x": 10.0, "y": 5.0}}], ["wait", 7.5],
            ["check", "#b"], ["click", "#tiny"],
        ]},
    "session_b_tight_cap": {
        "seed": 7, "number": 3, "budget": 0.4, "ops": [
            ["click", "#c"], ["wait", 1.0], ["hover", "#b"], ["wait", 12.0],
            ["click", "#b"],
        ]},
    "landing_misses": {
        "seed": 99, "number": 1, "budget": 1.5, "hits": "centre", "ops": [
            ["click", "#b"], ["wait", 2.0], ["hover", "#c"],
        ]},
    "explicit_pointer": {
        "seed": 31337, "number": 2, "budget": 1.5, "ops": [
            ["move", 800.0, 500.0], ["wait", 0.2], ["mouse_click", 120.0, 60.0],
            ["wheel", 0.0, 900.0], ["wheel", 0.0, 150.0], ["wheel", 30.0, -420.0],
            ["click", "#b"],
        ]},
}

# ── revision 1: the server's own run of the Mouse.click session ──────────────

def _line(obj):
    return json.dumps(obj, separators=(", ", ": "))


def dump_golden(g):
    """The file's layout: one line per event, so a diff shows events."""
    out = ["{", ' "_origin": ' + _line(g["_origin"]) + ",", ' "scenarios": {']
    names = list(g["scenarios"])
    for n, name in enumerate(names):
        sc = g["scenarios"][name]
        out.append("  %s: {" % json.dumps(name))
        out.append('   "spec": ' + _line(sc["spec"]) + ",")
        out.append('   "engine_events": [')
        evs = sc["engine_events"]
        for i, e in enumerate(evs):
            out.append("    " + _line(e) + ("," if i < len(evs) - 1 else ""))
        out.append("   ]")
        out.append("  }" + ("," if n < len(names) - 1 else ""))
    out += [" }", "}"]
    return "\r\n".join(out) + "\r\n"


def _server_session(spec, name):
    """What the engine receives from the CURRENT server for one session."""
    from invisible_playwright._juggler import pointer as pointer_mod
    import tests.test_pointer_server as T

    clock = T.VirtualClock()
    pointer_mod._CLOCK = clock
    core_actions.time = SimpleNamespace(
        sleep=clock.sleep, monotonic=time.monotonic,
        perf_counter=time.perf_counter)
    page = T.build_page(clock, T._boxes_of(name), seed=spec["seed"],
                        number=spec["number"], budget=spec["budget"],
                        hits=spec.get("hits", "all"))
    T.play(page, clock, spec["ops"])
    return json.loads(json.dumps(page.log))


def _shape(evs):  # the latency is a draw per event ordinal, not an event
    return [{k: v for k, v in e.items() if k != "latency"} for e in evs]


def _revise(path, is_dup, revision):
    """Rewrite `explicit_pointer` from the current server, refusing unless it
    is the file's session minus EXACTLY the one event `is_dup` names (same
    t, x, y and type everywhere else), and every other session is what the
    file already holds, event for event, latency included."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))

    gp = pathlib.Path(path)
    with open(gp, newline="", encoding="utf-8") as fh:
        text = fh.read()
    g = json.loads(text)
    assert dump_golden(g) == text, \
        "the writer does not reproduce the file: refusing to rewrite it"
    name = "explicit_pointer"
    old = g["scenarios"][name]["engine_events"]
    dup = [i for i in range(1, len(old)) if is_dup(g["scenarios"][name]["spec"], old, i)]
    assert len(dup) == 1, "expected exactly one such event, found %r" % dup
    for other, sc in g["scenarios"].items():
        if other != name:
            assert _server_session(sc["spec"], other) == sc["engine_events"], \
                "session %s is not what the file holds: refusing" % other
    new = _server_session(g["scenarios"][name]["spec"], name)
    assert _shape(new) == _shape(old[:dup[0]] + old[dup[0] + 1:]), \
        "the session differs from the old one by more than that event"
    g["scenarios"][name]["engine_events"] = new
    g["_origin"].setdefault("revisions", []).append(revision)
    with open(gp, "w", newline="", encoding="utf-8") as fh:
        fh.write(dump_golden(g))


def _same_point_as_before(old, i, point=None):
    a, b = old[i - 1], old[i]
    return (a["m"] == b["m"] == "Page.dispatchMouseEvent"
            and a["type"] == b["type"] == "mousemove"
            and (a["x"], a["y"]) == (b["x"], b["y"])
            and (point is None or (b["x"], b["y"]) == point))


def revise_mouse_click(path):
    _revise(path,
            lambda spec, old, i: _same_point_as_before(
                old, i, tuple(spec["ops"][2][1:3])),
            {
        "what": "explicit_pointer: the Mouse.click no longer repeats its mousemove on the click point",
        "why": "Pointer.click_at sent the endpoint as a move and Actions.click_at moved onto it again before the press; the server now lets click_at make the one move",
        "change": "one mousemove fewer (same x, y, t as its twin); the other sessions are untouched",
        "generator": "tests/data/pointer_golden_origin.py --revise-mouse-click, run against feat/rt-dblmove",
    })


def revise_standstill(path):
    _revise(path,
            lambda spec, old, i: _same_point_as_before(
                old, i, tuple(spec["ops"][0][1:3])),
            {
        "what": "explicit_pointer: a stroke from standstill no longer opens with a mousemove on the point it stands on",
        "why": "the first waypoint of a stroke is its start; Pointer._travel sent it although the engine had already been told the pointer was on that pixel (a zero-displacement move no mouse reports)",
        "change": "one mousemove fewer (the repeat of (800, 500) after the wait); t, x, y and type of every other event are unchanged, the latency draws follow the event ordinal; the other three sessions are identical event for event",
        "generator": "tests/data/pointer_golden_origin.py --revise-standstill, run against feat/rt-dblmove",
    })


if __name__ == "__main__":
    import pathlib
    for flag, fn in (("--revise-mouse-click", revise_mouse_click),
                     ("--revise-standstill", revise_standstill)):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            fn(sys.argv[i + 1] if len(sys.argv) > i + 1 else
               pathlib.Path(__file__).with_name("pointer_golden.json"))
            sys.exit(0)
    out = {name: {"spec": spec, "engine_events": scenario(spec)}
           for name, spec in SCENARIOS.items()}
    json.dump(out, sys.stdout, indent=1)
