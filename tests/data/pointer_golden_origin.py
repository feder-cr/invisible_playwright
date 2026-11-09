"""Golden generator: what the page's engine receives from the in-process client's
humanised pointer, computed with the CLIENT-SIDE code of commit 5d9ad70
(`invisible_playwright/_cursor.py` wrappers: `_approach`, `_wrap_mouse_move`,
`_wrap_mouse_click`, `_wrap_mouse_wheel`), through a real `invisible_core`
`Actions` object whose connection only records.

Run against the OLD tree only:
    PYTHONPATH=<worktree at 5d9ad70>/src python gen_golden.py > pointer_golden.json
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

if __name__ == "__main__":
    out = {name: {"spec": spec, "engine_events": scenario(spec)}
           for name, spec in SCENARIOS.items()}
    json.dump(out, sys.stdout, indent=1)
