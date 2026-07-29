"""Offline tests for the cursor-motion wiring.

Nothing here launches a browser. Every property the wiring is responsible for
is checkable on plain objects: which prefs a session sends, which engine it
picks, that the session seed reaches the generator, that the same seed
produces the same first waypoint, and that the public constructor did not
change shape while all of this was added.

The motion generator itself is stubbed. That is deliberate: these tests are
about the plumbing, and they must keep failing loudly for plumbing reasons
even while the generator is being tuned.
"""
from __future__ import annotations

import asyncio
import ast
import inspect
import math
import pathlib
import random
import warnings

import pytest

from invisible_playwright import _cursor
from invisible_playwright.launcher import InvisiblePlaywright
from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright


# ── a stub generator standing in for _motion ────────────────────────────────

class _StubProfile:
    """Deterministic, seed-driven, and records what it was constructed with."""

    def __init__(self, seed: int, max_seconds: float) -> None:
        self.seed = seed
        self.max_seconds = max_seconds
        self._rng = random.Random(seed)

    def path(self, fx, fy, tx, ty):
        # Four waypoints wobbled by the seeded stream: enough for "the same
        # seed gives the same first waypoint" to mean something.
        out = []
        for i in range(1, 5):
            t = i / 5.0
            out.append((
                fx + (tx - fx) * t + self._rng.uniform(-5, 5),
                fy + (ty - fy) * t + self._rng.uniform(-5, 5),
                0.0,
            ))
        return out


class _StubMotionModule:
    MotionProfile = _StubProfile


class _VirtualTimer:
    """A clock that only advances when something sleeps.

    Every test in this file is about plumbing, not about timing, and plumbing
    assertions must not be at the mercy of what the machine running them was
    doing at the time. The dispatcher drops waypoints it cannot deliver on
    time; against a clock that never falls behind, nothing is dropped and the
    behaviour is exactly reproducible. Delivered timing is measured separately,
    against a real clock, in test_cursor_dispatch.py.
    """

    def __init__(self) -> None:
        self.t = 0.0

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += max(0.0, seconds)


@pytest.fixture(autouse=True)
def virtual_clock(monkeypatch):
    timer = _VirtualTimer()
    monkeypatch.setattr(_cursor, "_TIMER", timer)
    return timer


@pytest.fixture(autouse=True)
def quiet_warnings():
    """Warnings are per-process latches; a test must not depend on order."""
    _cursor._reset_warnings()
    yield
    _cursor._reset_warnings()


@pytest.fixture
def stub_motion(monkeypatch):
    """Make a generator available."""
    monkeypatch.setattr(_cursor, "_motion_mod", _StubMotionModule)
    monkeypatch.delenv(_cursor.ENGINE_ENV, raising=False)
    monkeypatch.delenv(_cursor.LANDING_ENV, raising=False)
    return _StubMotionModule


@pytest.fixture
def no_motion(monkeypatch):
    monkeypatch.setattr(_cursor, "_motion_mod", None)
    monkeypatch.delenv(_cursor.ENGINE_ENV, raising=False)


# ── fake Playwright implementation objects ──────────────────────────────────

class _FakeMouse:
    def __init__(self, page):
        self._page = page

    async def move(self, x, y, steps=None):
        self._page.moves.append((round(x, 4), round(y, 4)))

    async def wheel(self, delta_x, delta_y):
        self._page.wheels.append((delta_x, delta_y))


class _FakeContext:
    def __init__(self, browser=None):
        self._browser = browser


class _FakePage:
    def __init__(self, context, width=1280, height=720):
        self._browser_context = context
        self._viewport_size = {"width": width, "height": height}
        self.moves = []
        self.wheels = []
        self.mouse = _FakeMouse(self)


class _FakeBrowser:
    pass


# A 100x40 control at (400, 300): centre exactly (450, 320).
_BOX = {"x": 400.0, "y": 300.0, "width": 100.0, "height": 40.0}


class _FakeHandle:
    async def bounding_box(self):
        return dict(_BOX)

    async def evaluate(self, expression, arg=None):
        # The real one asks the page whether the point hits this element.
        return True

    async def dispose(self):
        pass


async def _fake_move(mouse, x, y):
    await mouse.move(x, y)


def _landing_of(kwargs):
    """Absolute point the action was told to act on."""
    pos = kwargs.get("position")
    if not pos:
        return (_BOX["x"] + _BOX["width"] / 2.0, _BOX["y"] + _BOX["height"] / 2.0)
    return (_BOX["x"] + pos["x"], _BOX["y"] + pos["y"])


# ═══════════════════════════════════════════════════════════════════════════
# 1. The prefs a session sends, for every value of humanize=
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_python_engine_turns_the_browser_side_expansion_off(stub_motion):
    """The single most important assertion in this file.

    If the browser's own expansion stayed on while the wrapper also draws a
    path, every waypoint we send would be expanded into a whole path of its
    own: a hundred-fold event storm, and a shape neither generator intended.
    """
    ip = InvisiblePlaywright(seed=42, humanize=True)
    assert ip._cursor_engine == _cursor.ENGINE_PYTHON
    prefs = ip._build_prefs()
    assert prefs["stealthfox.humanize"] is False
    assert "stealthfox.humanize.maxTime" not in prefs


@pytest.mark.unit
def test_humanize_false_disables_everything(stub_motion):
    ip = InvisiblePlaywright(seed=42, humanize=False)
    assert ip._cursor_engine == _cursor.ENGINE_OFF
    prefs = ip._build_prefs()
    assert prefs["stealthfox.humanize"] is False
    assert "stealthfox.humanize.maxTime" not in prefs


@pytest.mark.unit
def test_humanize_float_still_means_a_cap_in_seconds(stub_motion):
    ip = InvisiblePlaywright(seed=42, humanize=2.5)
    assert ip._cursor_engine == _cursor.ENGINE_PYTHON
    # Was `ip._humanize_max_seconds()`, a wrapper with no caller in src/. The
    # live arming path reads the cap straight off this function.
    assert _cursor.max_seconds_for(ip._humanize) == 2.5


@pytest.mark.unit
def test_escape_hatch_restores_the_previous_behaviour(stub_motion, monkeypatch):
    """The way back: INVPW_CURSOR_ENGINE=binary is byte-identical to the old
    wiring - pref on, cap forwarded, and nothing armed on our side."""
    monkeypatch.setenv(_cursor.ENGINE_ENV, "binary")
    ip = InvisiblePlaywright(seed=42, humanize=3.0)
    assert ip._cursor_engine == _cursor.ENGINE_BINARY
    prefs = ip._build_prefs()
    assert prefs["stealthfox.humanize"] is True
    assert prefs["stealthfox.humanize.maxTime"] == "3.0"


@pytest.mark.unit
def test_escape_hatch_off_disables_motion_without_touching_the_constructor(stub_motion, monkeypatch):
    monkeypatch.setenv(_cursor.ENGINE_ENV, "off")
    ip = InvisiblePlaywright(seed=42, humanize=True)
    assert ip._cursor_engine == _cursor.ENGINE_OFF
    assert ip._build_prefs()["stealthfox.humanize"] is False


@pytest.mark.unit
def test_missing_generator_falls_back_to_the_browser(no_motion):
    """A session must always be able to move. With no generator installed we
    hand the job back to the browser rather than shipping a teleporting
    cursor."""
    ip = InvisiblePlaywright(seed=42, humanize=True)
    assert ip._cursor_engine == _cursor.ENGINE_BINARY
    assert ip._build_prefs()["stealthfox.humanize"] is True


@pytest.mark.unit
def test_sync_and_async_agree_on_the_prefs(stub_motion):
    """Both launchers must derive the prefs from the same helper - a divergence
    here is a silent double expansion in exactly one of the two APIs."""
    for humanize in (True, False, 2.0):
        sync = InvisiblePlaywright(seed=42, humanize=humanize)
        asy = AsyncInvisiblePlaywright(seed=42, humanize=humanize)
        assert sync._cursor_engine == asy._cursor_engine
        assert (
            _cursor.humanize_prefs(sync._cursor_engine, humanize)
            == _cursor.humanize_prefs(asy._cursor_engine, humanize)
        )


@pytest.mark.unit
def test_humanize_prefs_never_sets_maxtime_without_the_toggle():
    for engine in (_cursor.ENGINE_PYTHON, _cursor.ENGINE_OFF):
        prefs = _cursor.humanize_prefs(engine, True)
        assert prefs["stealthfox.humanize"] is False
        assert "stealthfox.humanize.maxTime" not in prefs


# ═══════════════════════════════════════════════════════════════════════════
# 2. The session seed reaches the generator
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_session_seed_reaches_the_generator(stub_motion):
    browser = _FakeBrowser()
    assert _cursor.enable_for(browser, seed=12345, max_seconds=1.25) is True
    page = _FakePage(_FakeContext(browser))
    cursor = _cursor._cursor_for_page(page)
    assert cursor is not None
    assert cursor._profile.seed == _cursor.page_motion_seed(12345, 0)
    assert cursor._profile.max_seconds == 1.25


@pytest.mark.unit
def test_the_launcher_hands_over_its_own_seed(stub_motion, monkeypatch):
    """The seed that drives the fingerprint is the seed that drives the cursor."""
    seen = {}

    def spy(owner, *, seed, max_seconds):
        seen["seed"] = seed
        seen["max_seconds"] = max_seconds
        return True

    monkeypatch.setattr("invisible_playwright.launcher._enable_cursor_engine", spy)
    ip = InvisiblePlaywright(seed=777, humanize=2.0)
    ip._arm_cursor_engine(_FakeBrowser())
    assert seen == {"seed": 777, "max_seconds": 2.0}
    assert ip.seed == 777


@pytest.mark.unit
def test_binary_engine_arms_nothing(stub_motion, monkeypatch):
    """Under the escape hatch our side must stay completely out of the way."""
    called = []
    monkeypatch.setattr(
        "invisible_playwright.launcher._enable_cursor_engine",
        lambda *a, **k: called.append(a),
    )
    monkeypatch.setenv(_cursor.ENGINE_ENV, "binary")
    InvisiblePlaywright(seed=1, humanize=True)._arm_cursor_engine(_FakeBrowser())
    assert called == []


@pytest.mark.unit
def test_each_page_gets_its_own_stream(stub_motion):
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=999, max_seconds=1.0)
    ctx = _FakeContext(browser)
    a = _cursor._cursor_for_page(_FakePage(ctx))
    b = _cursor._cursor_for_page(_FakePage(ctx))
    assert a.seed != b.seed, "two tabs sharing one stream would couple their paths"
    assert a.seed == _cursor.page_motion_seed(999, 0)
    assert b.seed == _cursor.page_motion_seed(999, 1)


@pytest.mark.unit
def test_page_motion_seed_is_stable_and_in_range():
    assert _cursor.page_motion_seed(42, 0) == _cursor.page_motion_seed(42, 0)
    assert _cursor.page_motion_seed(42, 0) != _cursor.page_motion_seed(43, 0)
    for seed in (0, 1, 42, 2**31 - 1):
        for ordinal in range(4):
            assert 0 <= _cursor.page_motion_seed(seed, ordinal) < 2**31


@pytest.mark.unit
def test_an_unregistered_page_is_left_alone(stub_motion):
    """A plain Playwright browser in the same process must find nothing: the
    wrappers are installed on the class, so this is what keeps them inert."""
    page = _FakePage(_FakeContext(_FakeBrowser()))
    assert _cursor._cursor_for_page(page) is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. The same seed produces the same first waypoint
# ═══════════════════════════════════════════════════════════════════════════

def _drive(page, cursor, to_x, to_y):
    async def raw(x, y):
        await page.mouse.move(x, y)

    asyncio.run(_cursor._travel(cursor, page, to_x, to_y, raw))
    return page.moves


def _fresh_session_moves(seed):
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=seed, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))
    cursor = _cursor._cursor_for_page(page)
    return _drive(page, cursor, 900.0, 500.0)


@pytest.mark.unit
def test_same_seed_same_first_waypoint(stub_motion):
    first = _fresh_session_moves(4242)
    second = _fresh_session_moves(4242)
    assert first, "the stub generator must have produced waypoints"
    assert first[0] == second[0], "a replayed seed must replay the first waypoint"
    assert first == second, "a replayed seed must replay the whole path"


@pytest.mark.unit
def test_a_different_seed_moves_differently(stub_motion):
    assert _fresh_session_moves(4242) != _fresh_session_moves(4243)


@pytest.mark.unit
def test_the_start_point_is_seeded_and_is_not_the_origin(stub_motion):
    """A cursor sitting at the top-left corner at the start of every session
    of every install is exactly the kind of shared constant this change is
    removing."""
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=31337, max_seconds=1.0)
    cursor = _cursor._cursor_for_page(_FakePage(_FakeContext(browser)))
    start = cursor.start_point(1280, 720)
    assert start != (0.0, 0.0)
    assert 0 < start[0] < 1280 and 0 < start[1] < 720
    assert start == cursor.start_point(1280, 720)


@pytest.mark.unit
def test_a_page_with_no_viewport_still_does_not_start_at_the_origin(stub_motion):
    """The silent reinstatement of the very invariant this work removes.

    ``start_point`` used to return exactly (0, 0) whenever the page would not
    report a size, so every session of every install departed from the same
    corner - and said nothing about it.
    """
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=31337, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))
    page._viewport_size = None
    cursor = _cursor._cursor_for_page(page)
    with pytest.warns(RuntimeWarning, match="no viewport size"):
        start = cursor.start_point(None, None)
    assert start != (0.0, 0.0)
    assert 0 < start[0] < 1280 and 0 < start[1] < 720
    # ...seeded, so two installs still do not agree
    other = _FakeBrowser()
    _cursor.enable_for(other, seed=4242, max_seconds=1.0)
    assert _cursor._cursor_for_page(
        _FakePage(_FakeContext(other))).start_point(None, None) != start


@pytest.mark.unit
def test_a_generator_whose_start_point_is_broken_says_so_once(stub_motion, monkeypatch):
    class _BadStart:
        def __init__(self, seed, max_seconds):
            pass

        def start_point(self, w, h):
            raise ValueError("nope")

        def path(self, fx, fy, tx, ty):
            return []

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _BadStart, raising=False)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=5, max_seconds=1.0)
    cursor = _cursor._cursor_for_page(_FakePage(_FakeContext(browser)))
    with pytest.warns(RuntimeWarning, match="start_point"):
        first = cursor.start_point(1280, 720)
    assert first != (0.0, 0.0)
    # ...and exactly once: a warning per click would be its own breakage.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cursor.start_point(1280, 720)
    assert caught == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. Waypoint hygiene the wiring owns
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_waypoints_stay_inside_the_viewport(stub_motion, monkeypatch):
    """A waypoint outside the viewport is not simply dropped by the browser:
    it makes the browser park the pointer at the origin, which is worse than
    not moving at all. So the wiring clamps."""
    class _Wild:
        def __init__(self, seed, max_seconds):
            pass

        def path(self, fx, fy, tx, ty):
            return [(-500.0, -400.0, 0.0), (99999.0, 88888.0, 0.0), (tx, ty, 0.0)]

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _Wild, raising=False)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=5, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser), width=800, height=600)
    cursor = _cursor._cursor_for_page(page)
    moves = _drive(page, cursor, 400.0, 300.0)
    assert moves, "clamping must not swallow the whole path"
    for x, y in moves:
        assert 0 <= x <= 799 and 0 <= y <= 599, f"{(x, y)} escaped the viewport"


@pytest.mark.unit
def test_the_endpoint_is_never_pre_empted(stub_motion, monkeypatch):
    """The endpoint belongs to the caller, and it is sent exactly once.

    Note what is NOT asserted here any more: that no two events ever share a
    pixel. The dispatcher used to strip every repeat, which made "the duplicate
    fraction is exactly zero" true of this package no matter what the generator
    produced - and a quantity that is exactly zero in every install of every
    session is an invariant just as much as one that is exactly 0.5. Whether a
    path repeats a pixel is a property of its shape, so it is the generator's
    to decide and the dispatcher's to deliver faithfully.
    """
    class _Stuttering:
        def __init__(self, seed, max_seconds):
            pass

        def path(self, fx, fy, tx, ty):
            return [(300.0, 200.0, 0.02)] * 5 + [(tx, ty, 0.02)] * 4

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _Stuttering, raising=False)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=6, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))
    cursor = _cursor._cursor_for_page(page)
    moves = _drive(page, cursor, 640.0, 360.0)
    assert (640.0, 360.0) not in moves, "the endpoint is the caller's to emit, once"
    assert moves == [(300.0, 200.0)] * 5


@pytest.mark.unit
def test_waypoints_that_share_one_instant_become_one_event(stub_motion, monkeypatch):
    """Merging what cannot be separated in time is the dispatcher's job; it is
    a statement about the wire, not about the shape of the path."""
    class _AllAtOnce:
        def __init__(self, seed, max_seconds):
            pass

        def path(self, fx, fy, tx, ty):
            # Same instant, three different pixels: no device reports that.
            return [(300.0, 200.0, 0.05), (301.0, 201.0, 0.0),
                    (302.0, 202.0, 0.0), (tx, ty, 0.05)]

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _AllAtOnce, raising=False)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=61, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))
    cursor = _cursor._cursor_for_page(page)
    moves = _drive(page, cursor, 640.0, 360.0)
    assert moves == [(302.0, 202.0)], moves


@pytest.mark.unit
def test_a_broken_generator_never_breaks_an_action(stub_motion, monkeypatch):
    class _Broken:
        def __init__(self, seed, max_seconds):
            pass

        def path(self, fx, fy, tx, ty):
            raise RuntimeError("generator exploded")

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _Broken, raising=False)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=7, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))
    cursor = _cursor._cursor_for_page(page)
    assert _drive(page, cursor, 100.0, 100.0) == []
    assert (cursor.x, cursor.y) == (100.0, 100.0)


@pytest.mark.unit
def test_two_tuple_waypoints_are_accepted_and_paced(stub_motion):
    out = _cursor._normalise_waypoints([(1, 2), (3, 4)], 1.0)
    assert out == [(1.0, 2.0, 0.5), (3.0, 4.0, 0.5)]
    out = _cursor._normalise_waypoints([(1, 2, 0.01)], 1.0)
    assert out == [(1.0, 2.0, 0.01)]


# ═══════════════════════════════════════════════════════════════════════════
# 5. The two halves fit: the wiring driving the real generator
# ═══════════════════════════════════════════════════════════════════════════

def _real_session_moves(seed, max_seconds=1.5, width=1280, height=720):
    browser = _FakeBrowser()
    assert _cursor.enable_for(browser, seed=seed, max_seconds=max_seconds) is True
    page = _FakePage(_FakeContext(browser), width=width, height=height)
    cursor = _cursor._cursor_for_page(page)
    assert cursor is not None
    return _drive(page, cursor, 900.0, 500.0)


@pytest.mark.unit
def test_the_real_generator_drives_the_wiring():
    """No stub: the shipped generator, through the shipped plumbing."""
    moves = _real_session_moves(20260726)
    assert len(moves) > 4, f"the path collapsed to {len(moves)} events"
    for x, y in moves:
        assert 0 <= x <= 1279 and 0 <= y <= 719
    assert len(moves) == len(set(moves)), "duplicate positions reached the wire"
    assert (900.0, 500.0) not in moves, "the endpoint is the caller's to emit"


@pytest.mark.unit
def test_the_real_generator_replays_from_the_seed():
    assert _real_session_moves(20260726) == _real_session_moves(20260726)


@pytest.mark.unit
def test_two_seeds_do_not_move_the_same_way():
    """The whole point: the shape is a property of the session, not of the
    package. Two installs must not draw the same curve."""
    assert _real_session_moves(11111) != _real_session_moves(22222)


@pytest.mark.unit
def test_the_humanize_cap_is_honoured_end_to_end():
    """``humanize=0.3`` must actually bound a movement, whether or not the
    generator knows about the cap."""
    from invisible_playwright import _motion

    motion = _motion.CursorMotion(4242)
    raw = motion.path(10.0, 10.0, 1200.0, 680.0)
    for cap in (0.3, 1.5):
        paced = _cursor._normalise_waypoints(raw, cap)
        assert sum(d for _, _, d in paced) <= cap + 1e-9
    # ...and a generous cap must not stretch a short movement to fill it.
    short = _cursor._normalise_waypoints(motion.path(10.0, 10.0, 40.0, 30.0), 10.0)
    assert sum(d for _, _, d in short) < 10.0


# ═══════════════════════════════════════════════════════════════════════════
# 6. The mechanism itself: the funnel we hook must still be the funnel
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_the_wrappers_accept_the_arguments_the_bindings_actually_pass():
    """Both generated API layers call the implementation entirely by keyword -
    ``move(x=, y=, steps=)``, ``wheel(deltaX=, deltaY=)``, ``_click(selector=,
    ...)``. A wrapper that renames one of those parameters does not shadow the
    method, it raises TypeError on every call to it, and no amount of testing
    the wrapper positionally would show it."""
    from playwright._impl._input import Mouse

    assert _cursor._ensure_patched() is True
    for name in ("move", "wheel"):
        bound = getattr(Mouse, name)
        assert getattr(bound, _cursor._MARKER, False), f"Mouse.{name} not wrapped"
        original = inspect.signature(
            _cursor._ORIGINAL_MOUSE_MOVE if name == "move"
            else _cursor._ORIGINAL_MOUSE_WHEEL
        ).parameters
        assert list(inspect.signature(bound).parameters) == list(original), name


@pytest.mark.unit
def test_the_hooked_funnel_still_exists():
    """If the bindings rename these, the wrappers land on nothing and every
    session silently teleports again. Cheap to check, expensive to miss."""
    from playwright._impl._frame import Frame
    from playwright._impl._input import Mouse

    for name in _cursor._FRAME_ACTIONS:
        assert callable(getattr(Frame, name, None)), f"Frame.{name} is gone"
    assert callable(getattr(Mouse, "move", None))


@pytest.mark.unit
def test_page_frame_and_locator_all_funnel_through_the_hook():
    """The whole no-new-API claim rests on this: page.click, frame.click and
    locator.click must all reach Frame._click, or wrapping one of them would
    leave the other two teleporting."""
    from playwright._impl._locator import Locator
    from playwright._impl._page import Page

    assert _cursor._ensure_patched() is True
    assert "_main_frame._click(" in inspect.getsource(Page.click)
    assert "self._frame._click(" in inspect.getsource(Locator.click)
    assert "_main_frame.hover(" in inspect.getsource(Page.hover)
    assert "self._frame.hover(" in inspect.getsource(Locator.hover)
    # And the hooked coroutine really is the one that talks to the browser.
    assert 'send("click"' in inspect.getsource(_cursor._ORIGINAL_FRAME_ACTIONS["_click"])
    assert 'send("hover"' in inspect.getsource(_cursor._ORIGINAL_FRAME_ACTIONS["hover"])


@pytest.mark.unit
def test_wrappers_are_installed_exactly_once():
    from playwright._impl._frame import Frame

    assert _cursor._ensure_patched() is True
    first = Frame._click
    assert _cursor._ensure_patched() is True
    assert Frame._click is first, "re-arming must not stack a second wrapper"
    assert getattr(Frame._click, _cursor._MARKER, False) is True


@pytest.mark.unit
def test_the_whole_approach_runs_before_the_action(stub_motion, monkeypatch):
    """This is the hover fix, stated as an assertion.

    The failure it replaces was an ordering failure: the path used to be drawn
    from inside the action, so the check that confirms the pointer is over the
    right element saw a waypoint from the far end of the curve and concluded
    the element was covered. Here the path is finished, and the pointer is
    already on the target, before the action is even entered - so the only
    event inside the checked window is the action's own move.
    """
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=99, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    async def fake_move(mouse, x, y):
        await mouse.move(x, y)

    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", fake_move)

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _FakeHandle()

    observed = {}

    async def original(self, *args, **kwargs):
        observed["moves_before_action"] = list(page.moves)
        observed["kwargs"] = kwargs

    wrapped = _cursor._wrap_frame_action(original)
    asyncio.run(wrapped(_FakeFrame(), "#buy"))

    moves = observed["moves_before_action"]
    assert len(moves) > 1, "the pointer must actually travel, not jump"
    # The pointer is standing ON the element when the action starts, and the
    # last hop onto the click point is the action's own move - the single event
    # the hit-target check gets to see.
    landing = _landing_of(observed["kwargs"])
    assert 400.0 <= moves[-1][0] <= 500.0 and 300.0 <= moves[-1][1] <= 340.0
    assert 0 < math.hypot(moves[-1][0] - landing[0],
                          moves[-1][1] - landing[1]) < 25.0
    # And no waypoint is left pending to fire inside the action's own window.
    assert page.moves == moves


@pytest.mark.unit
def test_the_action_is_told_to_click_off_the_geometric_centre(stub_motion, monkeypatch):
    """B3: ``box.x + width/2, box.y + height/2`` is an exact number that every
    install produces for every element-targeted action, and it is readable from
    a single event. The action itself has to be aimed somewhere else."""
    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", _fake_move)
    seen = []
    for seed in range(24):
        browser = _FakeBrowser()
        _cursor.enable_for(browser, seed=seed, max_seconds=1.0)
        page = _FakePage(_FakeContext(browser))

        class _FakeFrame:
            _page = page

            async def query_selector(self, selector):
                return _FakeHandle()

        captured = {}

        async def original(self, *args, **kwargs):
            captured.update(kwargs)

        asyncio.run(_cursor._wrap_frame_action(original)(_FakeFrame(), "#buy"))
        assert "position" in captured, "the action still aims at the centre"
        seen.append(_landing_of(captured))

    centre = (450.0, 320.0)
    assert centre not in seen
    for x, y in seen:
        assert 400.0 < x < 500.0 and 300.0 < y < 340.0
    assert len(set(seen)) > 20, "the landing point must vary per seed"


@pytest.mark.unit
def test_two_actions_of_one_session_do_not_land_on_the_same_pixel(stub_motion, monkeypatch):
    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", _fake_move)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=7, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _FakeHandle()

    seen = []
    for _ in range(8):
        captured = {}

        async def original(self, *args, **kwargs):
            captured.update(kwargs)

        asyncio.run(_cursor._wrap_frame_action(original)(_FakeFrame(), "#buy"))
        seen.append(_landing_of(captured))
    assert len(set(seen)) >= 7, seen


@pytest.mark.unit
def test_a_caller_supplied_position_is_never_overridden(stub_motion, monkeypatch):
    """The caller asked to click a specific pixel. That is not ours to move."""
    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", _fake_move)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=11, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _FakeHandle()

    captured = {}

    async def original(self, *args, **kwargs):
        captured.update(kwargs)

    asyncio.run(_cursor._wrap_frame_action(original)(
        _FakeFrame(), "#buy", position={"x": 3.0, "y": 4.0}))
    assert captured["position"] == {"x": 3.0, "y": 4.0}
    assert "timeout" not in captured
    # ...and the pointer still walked there.
    assert page.moves and page.moves[-1] != (450.0, 320.0)


@pytest.mark.unit
def test_a_point_that_does_not_hit_the_element_is_not_used(stub_motion, monkeypatch):
    """A bounding box is not the element: an inline link wrapping two lines has
    points inside its box that belong to the page behind it. Handing one of
    those to the action would turn a working click into a hit-target failure,
    so a point we chose is checked before it is used."""
    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", _fake_move)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=13, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _Missing(_FakeHandle):
        async def evaluate(self, expression, arg=None):
            return False

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _Missing()

    captured = {}

    async def original(self, *args, **kwargs):
        captured.update(kwargs)

    asyncio.run(_cursor._wrap_frame_action(original)(_FakeFrame(), "#buy"))
    assert "position" not in captured, "an unverified point reached the action"


@pytest.mark.unit
def test_a_landing_that_is_not_actionable_retries_the_caller_s_own_call(
        stub_motion, monkeypatch):
    """The invariant that outranks every stealth property in this module:
    aiming must never turn a call that would have succeeded into one that
    fails."""
    from playwright._impl._errors import TimeoutError as PWTimeout

    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", _fake_move)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=17, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _FakeHandle()

    calls = []

    async def original(self, *args, **kwargs):
        calls.append(kwargs)
        if "position" in kwargs:
            raise PWTimeout("hit target check failed")
        return "clicked"

    with pytest.warns(RuntimeWarning, match="off-centre"):
        result = asyncio.run(
            _cursor._wrap_frame_action(original)(_FakeFrame(), "#buy"))
    assert result == "clicked"
    assert len(calls) == 2
    assert "position" not in calls[1] and "timeout" not in calls[1]


@pytest.mark.unit
def test_landing_can_be_switched_off(stub_motion, monkeypatch):
    monkeypatch.setenv(_cursor.LANDING_ENV, "off")
    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", _fake_move)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=19, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _FakeHandle()

    captured = {}

    async def original(self, *args, **kwargs):
        captured.update(kwargs)

    asyncio.run(_cursor._wrap_frame_action(original)(_FakeFrame(), "#buy"))
    assert captured == {}
    assert page.moves, "the pointer still travels; only the aim is left alone"


@pytest.mark.unit
def test_an_offscreen_target_is_left_to_the_action(stub_motion, monkeypatch):
    """The action is about to scroll; aiming at where the element used to be
    would send waypoints out of the viewport, which parks the pointer at the
    origin. Skipping is strictly better than that."""
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=100, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser), width=800, height=600)

    async def fake_move(mouse, x, y):
        await mouse.move(x, y)

    monkeypatch.setattr(_cursor, "_ORIGINAL_MOUSE_MOVE", fake_move)

    class _Offscreen(_FakeHandle):
        async def bounding_box(self):
            return {"x": 100.0, "y": 2400.0, "width": 80.0, "height": 30.0}

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return _Offscreen()

    assert asyncio.run(_cursor._approach(_FakeFrame(), ("#deep",), {})) is None
    assert page.moves == []


@pytest.mark.unit
def test_a_missing_element_never_adds_a_wait_or_an_error(stub_motion):
    """Aiming is a hint. If the element is not there yet we give up silently and
    let the action do its own waiting and raise its own errors."""
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=101, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _FakeFrame:
        _page = page

        async def query_selector(self, selector):
            return None

    asyncio.run(_cursor._approach(_FakeFrame(), ("#later",), {}))
    assert page.moves == []


@pytest.mark.unit
def test_a_skipped_waypoint_does_not_shorten_the_movement(stub_motion, monkeypatch,
                                                          virtual_clock):
    """Dropping a waypoint must not shorten the movement.

    Under the previous dispatcher this was arithmetic on an owed pause handed
    back to the caller. It is now a property of the schedule: every waypoint
    carries an ABSOLUTE time, so a point that is skipped changes what is sent
    and never when the rest of it is sent - including the endpoint, whose
    deadline is waited out even though the caller is the one who emits it.
    """
    class _Stuttering:
        def __init__(self, seed, max_seconds):
            pass

        def path(self, fx, fy, tx, ty):
            return [(300.0, 200.0, 0.05)] * 4 + [(tx, ty, 0.05)]

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _Stuttering, raising=False)
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=102, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))
    cursor = _cursor._cursor_for_page(page)

    async def raw(x, y):
        await page.mouse.move(x, y)

    t0 = virtual_clock.now()
    asyncio.run(_cursor._travel(cursor, page, 640.0, 360.0, raw))
    # 5 waypoints x 50 ms = 250 ms of schedule. The endpoint belongs to the
    # caller and is not sent here, but its deadline is still waited out, so the
    # movement takes the whole 250 ms rather than 200.
    assert page.moves == [(300.0, 200.0)] * 4
    assert virtual_clock.now() - t0 == pytest.approx(0.25)


@pytest.mark.unit
def test_a_trial_run_does_not_move_the_pointer(stub_motion):
    """trial=True asks for the actionability checks without the action, so a
    real pointer would not move either."""
    browser = _FakeBrowser()
    _cursor.enable_for(browser, seed=8, max_seconds=1.0)
    page = _FakePage(_FakeContext(browser))

    class _FakeFrame:
        _page = page

    asyncio.run(_cursor._approach(_FakeFrame(), ("#b",), {"trial": True}))
    assert page.moves == []


# ═══════════════════════════════════════════════════════════════════════════
# 7. The public constructor did not change shape
# ═══════════════════════════════════════════════════════════════════════════

_EXPECTED_SIGNATURE = [
    ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("seed", inspect.Parameter.POSITIONAL_OR_KEYWORD),
    ("pin", inspect.Parameter.KEYWORD_ONLY),
    ("headless", inspect.Parameter.KEYWORD_ONLY),
    ("proxy", inspect.Parameter.KEYWORD_ONLY),
    ("extra_args", inspect.Parameter.KEYWORD_ONLY),
    ("humanize", inspect.Parameter.KEYWORD_ONLY),
    ("locale", inspect.Parameter.KEYWORD_ONLY),
    ("timezone", inspect.Parameter.KEYWORD_ONLY),
    ("extra_prefs", inspect.Parameter.KEYWORD_ONLY),
    ("binary_path", inspect.Parameter.KEYWORD_ONLY),
    ("profile_dir", inspect.Parameter.KEYWORD_ONLY),
    ("prep_recaptcha", inspect.Parameter.KEYWORD_ONLY),
]


@pytest.mark.unit
@pytest.mark.parametrize("cls", [InvisiblePlaywright, AsyncInvisiblePlaywright])
def test_public_constructor_signature_unchanged(cls):
    """No new argument. The motion arrives through the methods people already
    call, so there is nothing to add here and nothing to remember."""
    params = list(inspect.signature(cls.__init__).parameters.values())
    assert [(p.name, p.kind) for p in params] == _EXPECTED_SIGNATURE


@pytest.mark.unit
@pytest.mark.parametrize("cls", [InvisiblePlaywright, AsyncInvisiblePlaywright])
def test_humanize_default_is_still_on(cls):
    assert inspect.signature(cls.__init__).parameters["humanize"].default is True


# ═══════════════════════════════════════════════════════════════════════════
# 8. One law for deriving a sub-stream seed
# ═══════════════════════════════════════════════════════════════════════════
#
# Three modules derive independent PRNG streams from the one session seed with
# their own copy of the same FNV-1a mix, and two of the three docstrings claim
# to be "the same convention used elsewhere in the package". They are not
# automatically the same thing just because they say so: the copies differ in
# how they mask the seed, how they encode the tag, and what they return. Today
# those differences happen to cancel; nothing was checking that, and a fourth
# copy could be added tomorrow with no test noticing.
#
# The authoritative definition is ``_recaptcha_seed._sub_seed``. Not because it
# is the nicest - because it is the one that already shipped. Its outputs are
# baked into the seed -> fingerprint reproducibility this package documents, so
# it is the copy that cannot be changed; every other copy has to agree with it.
#
# The tests below pin two things: how many copies exist, and that they agree.

_PKG_DIR = pathlib.Path(_cursor.__file__).resolve().parent

# The FNV-1a 64-bit offset basis and prime. Written as numbers rather than
# matched as text so reformatting or a change of hex case cannot hide a copy.
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3

# module stem -> function name. Adding a copy means adding a line here, which
# is the point: it is a decision, not an accident.
_EXPECTED_MIXERS = {
    ("_recaptcha_seed", "_sub_seed"),   # authoritative - already shipped
    ("_behaviour", "_sub_seed"),        # byte-identical copy
    ("_motion", "_mix"),                # same mix, reduced to int31
}


def _fnv_functions() -> set:
    """Every function in the package that implements the FNV-1a mix."""
    found = set()
    for path in sorted(_PKG_DIR.rglob("*.py")):
        if "__pycache__" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            consts = {
                sub.value
                for sub in ast.walk(node)
                if isinstance(sub, ast.Constant) and isinstance(sub.value, int)
            }
            if _FNV_OFFSET in consts or _FNV_PRIME in consts:
                found.add((path.stem, node.name))
    return found


# A corpus that reaches every case the three copies could disagree on: zero,
# negative, above 2**32 (where the seed masks differ), and the int31 ceiling
# the wiring hands to _motion.
_SEED_CORPUS = [0, 1, -1, -7, 42, 2**31 - 1, 2**31, 2**32 - 1, 2**32,
                2**40, 10**18, -(2**31), -(2**40)]
_SEED_CORPUS += [random.Random(917).randrange(-(2**48), 2**48) for _ in range(300)]

# Every tag any of the three is called with today, plus shapes they could grow.
_TAG_CORPUS = ["motion:style", "motion:move:0", "motion:move:137",
               "pointer-persona", "idle:0", "scroll:7", "approach:12",
               "aimless:3", "session-gate", "pointer-origin", "google",
               "dom:example", "", "x" * 64]


@pytest.mark.unit
def test_exactly_three_copies_of_the_sub_stream_mix_exist():
    """A fourth copy must be a decision, not a surprise.

    This is the test that fails when someone adds a mixer - including one
    added by copy-paste into a module that has nothing to do with motion.
    """
    assert _fnv_functions() == _EXPECTED_MIXERS


@pytest.mark.unit
def test_every_copy_of_the_mix_agrees_with_the_authoritative_one():
    """Same seed, same tag, same stream - or the copies are not copies.

    ``_motion._mix`` reduces to int31 and the others do not, so the comparison
    is made after that documented reduction. Everything else about them has to
    be identical, for every seed in the corpus.
    """
    from invisible_playwright._behaviour import _sub_seed as behaviour_mix
    from invisible_playwright._motion import _mix as motion_mix
    from invisible_playwright._recaptcha_seed import _sub_seed as authoritative

    for seed in _SEED_CORPUS:
        for tag in _TAG_CORPUS:
            ref = authoritative(seed, tag)
            assert behaviour_mix(seed, tag) == ref, (seed, tag)
            assert motion_mix(seed, tag) == ref & 0x7FFFFFFF, (seed, tag)


@pytest.mark.unit
def test_the_reductions_each_copy_applies_are_the_documented_ones():
    """The one place the copies are allowed to differ, stated explicitly."""
    from invisible_playwright._behaviour import _sub_seed as behaviour_mix
    from invisible_playwright._motion import _mix as motion_mix
    from invisible_playwright._recaptcha_seed import _sub_seed as authoritative

    for seed in _SEED_CORPUS[:60]:
        for tag in _TAG_CORPUS:
            assert 0 <= motion_mix(seed, tag) < 2**31
            for fn in (authoritative, behaviour_mix):
                value = fn(seed, tag)
                assert 0 < value < 2**64
    # A stream seed of exactly zero would collapse two tags onto one stream in
    # the 64-bit copies, which is why they carry the `or 0xdeadbeef` fallback.
    assert authoritative(0, "") != 0
    assert behaviour_mix(0, "") != 0


@pytest.mark.unit
def test_the_page_seed_is_a_different_derivation_and_stays_one():
    """``page_motion_seed`` is seed x tab ordinal, not seed x tag.

    It is deliberately not the FNV mix - it answers a different question - and
    the discovery test above is what keeps it that way: turning it into a
    fourth FNV copy would show up there as an unexpected mixer.
    """
    assert ("_cursor", "page_motion_seed") not in _fnv_functions()
    from invisible_playwright._recaptcha_seed import _sub_seed as authoritative

    for seed in _SEED_CORPUS[:40]:
        assert 0 <= _cursor.page_motion_seed(seed, 0) < 2**31
        # ...and it is genuinely its own function, not the mix under a name.
        assert _cursor.page_motion_seed(seed, 0) != authoritative(seed, "0") & 0x7FFFFFFF
