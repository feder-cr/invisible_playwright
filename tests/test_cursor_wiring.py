"""Offline tests for the cursor-motion wiring.

Nothing here launches a browser. Every property the wiring is responsible for
is checkable on plain objects: which prefs a session sends, which engine it
picks, that the session seed reaches the generator of the page's hand on the
server, that the same seed produces the same first waypoint, and that the
public constructor did not change shape while all of this was added.

The motion generator itself is stubbed. That is deliberate: these tests are
about the plumbing, and they must keep failing loudly for plumbing reasons
even while the generator is being tuned.

WHERE THE HAND IS. The walk of the pointer is drawn by the SERVER
(`_juggler/pointer.py`); the client keeps one wrapper, on an explicit
`Mouse.move`. What the server draws, against the golden of what the client used
to send, is in `test_pointer_server.py`.
"""
from __future__ import annotations

import ast
import inspect
import math
import pathlib
import random
import warnings

import pytest

from invisible_playwright import _cursor
from invisible_playwright._juggler import pointer as pointer_mod
from invisible_playwright.launcher import InvisiblePlaywright
from invisible_playwright.async_api import InvisiblePlaywright as AsyncInvisiblePlaywright

from _pointer_fakes import BOX, VirtualClock, make_pointer, use_clock


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


@pytest.fixture(autouse=True)
def virtual_clock(monkeypatch):
    """A clock that only advances when something sleeps.

    Every test in this file is about plumbing, not about timing, and plumbing
    assertions must not be at the mercy of what the machine running them was
    doing at the time. Delivered timing is measured separately, against a real
    clock, in test_cursor_dispatch.py."""
    clock = VirtualClock()
    use_clock(monkeypatch, clock)
    return clock


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


class _FakeBrowser:
    pass


def _landing_of(position):
    """Absolute point the action was told to act on: `position` is an offset
    from the box's top-left, or None for the centre."""
    if not position:
        return (BOX["x"] + BOX["width"] / 2.0, BOX["y"] + BOX["height"] / 2.0)
    return (BOX["x"] + position["x"], BOX["y"] + position["y"])


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
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=True)
    assert ip._cursor_engine == _cursor.ENGINE_PYTHON
    prefs = ip._build_prefs()
    assert prefs["stealthfox.humanize"] is False
    assert "stealthfox.humanize.maxTime" not in prefs


@pytest.mark.unit
def test_humanize_false_disables_everything(stub_motion):
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=False)
    assert ip._cursor_engine == _cursor.ENGINE_OFF
    prefs = ip._build_prefs()
    assert prefs["stealthfox.humanize"] is False
    assert "stealthfox.humanize.maxTime" not in prefs


@pytest.mark.unit
def test_humanize_float_still_means_a_cap_in_seconds(stub_motion):
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=2.5)
    assert ip._cursor_engine == _cursor.ENGINE_PYTHON
    # Was `ip._humanize_max_seconds()`, a wrapper with no caller in src/. The
    # live arming path reads the cap straight off this function.
    assert _cursor.max_seconds_for(ip._humanize) == 2.5


@pytest.mark.unit
def test_escape_hatch_restores_the_previous_behaviour(stub_motion, monkeypatch):
    """The way back: INVPW_CURSOR_ENGINE=binary is byte-identical to the old
    wiring - pref on, cap forwarded, and nothing armed on our side."""
    monkeypatch.setenv(_cursor.ENGINE_ENV, "binary")
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=3.0)
    assert ip._cursor_engine == _cursor.ENGINE_BINARY
    prefs = ip._build_prefs()
    assert prefs["stealthfox.humanize"] is True
    assert prefs["stealthfox.humanize.maxTime"] == "3.0"


@pytest.mark.unit
def test_escape_hatch_off_disables_motion_without_touching_the_constructor(stub_motion, monkeypatch):
    monkeypatch.setenv(_cursor.ENGINE_ENV, "off")
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=True)
    assert ip._cursor_engine == _cursor.ENGINE_OFF
    assert ip._build_prefs()["stealthfox.humanize"] is False


@pytest.mark.unit
def test_the_show_cursor_kwarg_reaches_the_prefs(stub_motion):
    """A kwarg that reaches nothing is inert, and this project has shipped
    seven inert levers by reading the code instead of the value.

    So the assertion is on what the browser will be HANDED, not on the
    attribute we stored: `_show_cursor` being True proves only that `__init__`
    ran.
    """
    on = InvisiblePlaywright(timezone="America/New_York", seed=42, show_cursor=True)._build_prefs()
    off = InvisiblePlaywright(timezone="America/New_York", seed=42, show_cursor=False)._build_prefs()
    silent = InvisiblePlaywright(timezone="America/New_York", seed=42)._build_prefs()
    assert on["stealthfox.showcursor"] is True
    assert off["stealthfox.showcursor"] is False
    # ⛔ SAYING NOTHING IS NOT SAYING FALSE. The overlay ships ON since
    # 2026-08-28, and the third arm is the one that catches a layer collapsing
    # None into False on the way down - which is exactly what `bool(...)` in
    # the constructor used to do.
    assert silent["stealthfox.showcursor"] is True


@pytest.mark.unit
def test_the_dot_and_the_motion_are_two_switches(stub_motion):
    """Watching a TELEPORTING cursor is a legitimate thing to want, and it is
    the combination a derivation would quietly forbid."""
    prefs = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=False,
                                show_cursor=True)._build_prefs()
    assert prefs["stealthfox.humanize"] is False
    assert prefs["stealthfox.showcursor"] is True


@pytest.mark.unit
def test_missing_generator_falls_back_to_the_browser(no_motion):
    """A session must always be able to move. With no generator installed we
    hand the job back to the browser rather than shipping a teleporting
    cursor."""
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=True)
    assert ip._cursor_engine == _cursor.ENGINE_BINARY
    assert ip._build_prefs()["stealthfox.humanize"] is True


@pytest.mark.unit
def test_sync_and_async_agree_on_the_prefs(stub_motion):
    """Both launchers must derive the prefs from the same helper - a divergence
    here is a silent double expansion in exactly one of the two APIs."""
    for humanize in (True, False, 2.0):
        sync = InvisiblePlaywright(timezone="America/New_York", seed=42, humanize=humanize)
        asy = AsyncInvisiblePlaywright(timezone="America/New_York", seed=42, humanize=humanize)
        assert sync._cursor_engine == asy._cursor_engine
        assert _humanize_subset(sync) == _humanize_subset(asy)


def _humanize_subset(session):
    """The `stealthfox.humanize*` prefs a session would actually launch with.

    Through `build_prefs`, which is the function both front doors call and the
    only thing that reaches the browser. These two assertions used to run
    against `_cursor.humanize_prefs`, a second implementation of this contract
    that no code in src/ called: it was missing `stealthfox.humanize.stepMs`,
    which the live path emits, so the tests were green about a pref set the
    product never produced. The function is gone; the properties move here.
    """
    from invisible_playwright._session import build_prefs
    from invisible_core import generate_profile
    prefs = build_prefs(
        profile=generate_profile(seed=42), locale="en-US", timezone="UTC",
        extra_prefs=None, virtual_display=False,
        cursor_engine=session._cursor_engine, humanize=session._humanize,
        show_cursor=None)
    return {k: v for k, v in prefs.items() if k.startswith("stealthfox.humanize")}


@pytest.mark.unit
def test_maxtime_is_never_set_without_the_toggle():
    """A cap with the generator off would be a value nothing reads, and the
    browser deciding it had a budget for a path the driver is already drawing."""
    from invisible_playwright._session import build_prefs
    from invisible_core import generate_profile
    for engine in (_cursor.ENGINE_PYTHON, _cursor.ENGINE_OFF):
        prefs = build_prefs(
            profile=generate_profile(seed=42), locale="en-US", timezone="UTC",
            extra_prefs=None, virtual_display=False,
            cursor_engine=engine, humanize=True, show_cursor=None)
        assert prefs["stealthfox.humanize"] is False
        assert "stealthfox.humanize.maxTime" not in prefs


# ═══════════════════════════════════════════════════════════════════════════
# 2. The session seed reaches the generator
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_session_seed_reaches_the_generator(stub_motion):
    """The seed and the cap the server was given, and the page's number, reach
    the generator of that page's hand."""
    _, pointer = make_pointer(seed=12345, max_seconds=1.25, number=0)
    assert pointer.active
    assert pointer.hand._profile.seed == _cursor.page_motion_seed(12345, 0)
    assert pointer.hand._profile.max_seconds == 1.25


@pytest.mark.unit
def test_the_launcher_hands_over_its_own_seed(stub_motion, monkeypatch):
    """The seed that drives the fingerprint is the seed that drives the cursor."""
    seen = {}

    def spy(owner, *, seed, max_seconds):
        seen["seed"] = seed
        seen["max_seconds"] = max_seconds
        return True

    monkeypatch.setattr("invisible_playwright._session._enable_cursor_engine", spy)
    ip = InvisiblePlaywright(timezone="America/New_York", seed=777, humanize=2.0)
    ip._arm_cursor_engine(_FakeBrowser())
    assert seen == {"seed": 777, "max_seconds": 2.0}
    assert ip.seed == 777


@pytest.mark.unit
def test_the_seed_and_who_draws_travel_to_the_server_in_the_prefs(stub_motion):
    """The server draws the walk only when the launcher chose the Python
    generator; with the engine's own expansion (`binary`) or none (`off`) the
    same seed and budget arrive and the server must stay out of the way."""
    from invisible_playwright._juggler.server import (
        MOTION_BUDGET_PREF, POINTER_PREF, SESSION_SEED_PREF,
        session_draws_pointer)
    from invisible_playwright._session import build_prefs
    from invisible_core import generate_profile

    for engine, draws in ((_cursor.ENGINE_PYTHON, True),
                          (_cursor.ENGINE_BINARY, False),
                          (_cursor.ENGINE_OFF, False)):
        prefs = build_prefs(
            profile=generate_profile(seed=42), locale="en-US", timezone="UTC",
            extra_prefs=None, virtual_display=False, cursor_engine=engine,
            humanize=2.0, show_cursor=None, session_seed=42)
        assert prefs[SESSION_SEED_PREF] == 42
        assert prefs[MOTION_BUDGET_PREF] == 2000
        assert session_draws_pointer(prefs) is draws, engine
    # humanize off: nothing travels at all
    prefs = build_prefs(
        profile=generate_profile(seed=42), locale="en-US", timezone="UTC",
        extra_prefs=None, virtual_display=False,
        cursor_engine=_cursor.ENGINE_OFF, humanize=False, show_cursor=None,
        session_seed=42)
    assert SESSION_SEED_PREF not in prefs and POINTER_PREF not in prefs


@pytest.mark.unit
def test_binary_engine_arms_nothing(stub_motion, monkeypatch):
    """Under the escape hatch our side must stay completely out of the way."""
    called = []
    monkeypatch.setattr(
        "invisible_playwright._session._enable_cursor_engine",
        lambda *a, **k: called.append(a),
    )
    monkeypatch.setenv(_cursor.ENGINE_ENV, "binary")
    InvisiblePlaywright(timezone="America/New_York", seed=1, humanize=True)._arm_cursor_engine(_FakeBrowser())
    assert called == []


@pytest.mark.unit
def test_each_page_gets_its_own_stream(stub_motion):
    _, a = make_pointer(seed=999, max_seconds=1.0, number=0)
    _, b = make_pointer(seed=999, max_seconds=1.0, number=1)
    assert a.hand.seed != b.hand.seed, "two tabs sharing one stream would couple their paths"
    assert a.hand.seed == _cursor.page_motion_seed(999, 0)
    assert b.hand.seed == _cursor.page_motion_seed(999, 1)


@pytest.mark.unit
def test_a_page_moved_first_keeps_the_number_the_server_gave_it(stub_motion):
    """[B237]. Known-bad, before: the cursor counted pages at their FIRST
    MOVEMENT, so a script that opened p0 and p1 and moved on p1 first gave p1
    ordinal 0 there while the server, numbering at creation, gave it 1 - one
    page, two numbers, two layers drawing apart. The hand is born with the
    page and takes its number (`PageDispatcher.number`), so the order of the
    movements cannot matter: there is nothing to count."""
    p1 = make_pointer(seed=999, max_seconds=1.0, number=1)[1]
    p0 = make_pointer(seed=999, max_seconds=1.0, number=0)[1]
    assert p1.hand.seed == _cursor.page_motion_seed(999, 1)
    assert p0.hand.seed == _cursor.page_motion_seed(999, 0)


@pytest.mark.unit
def test_the_motion_seed_reads_every_bit_of_the_page_number():
    """A page the site opened is numbered above bit 62
    (`_behaviour.popup_number`); with the old 16-bit mask two such numbers
    that agreed on their low 16 bits drew the same paths. The pages the
    script asks for keep the seeds they had: the values below are the old
    formula's."""
    from invisible_core.juggler._behaviour import popup_number

    def old(seed, ordinal):
        h = (seed & 0xFFFFFFFF) * 0x9E3779B1
        h = (h ^ ((ordinal & 0xFFFF) * 0x85EBCA6B)) & 0xFFFFFFFF
        h ^= h >> 16
        return h & 0x7FFFFFFF

    for seed in (0, 1, 106, 999, 2**31 - 1):
        for ordinal in (0, 1, 2, 7, 65535):
            assert _cursor.page_motion_seed(seed, ordinal) == old(seed, ordinal)
    a = (1 << 62) | 0x1234
    b = (1 << 62) | (1 << 40) | 0x1234
    assert _cursor.page_motion_seed(106, a) != _cursor.page_motion_seed(106, b)
    popups = {_cursor.page_motion_seed(106, popup_number(o, k))
              for o in (None, 0, 1) for k in range(1, 6)}
    assert len(popups) == 15
    assert all(0 <= s < 2**31 for s in popups)


@pytest.mark.unit
def test_page_motion_seed_is_stable_and_in_range():
    assert _cursor.page_motion_seed(42, 0) == _cursor.page_motion_seed(42, 0)
    assert _cursor.page_motion_seed(42, 0) != _cursor.page_motion_seed(43, 0)
    for seed in (0, 1, 42, 2**31 - 1):
        for ordinal in range(4):
            assert 0 <= _cursor.page_motion_seed(seed, ordinal) < 2**31


@pytest.mark.unit
def test_a_session_without_a_seed_or_that_does_not_draw_has_no_hand(stub_motion):
    """A plain page of a session that turned humanising off, or whose pointer
    is the engine's own, finds no hand: the action is the caller's."""
    assert not make_pointer(seed=None)[1].active
    assert not make_pointer(seed=5, draws=False)[1].active
    assert not make_pointer(seed=5, max_seconds=None)[1].active


@pytest.mark.unit
def test_an_unarmed_page_of_the_client_sends_the_plain_move(stub_motion):
    """The one wrapper the client still has marks an explicit `mouse.move`
    `humanise` - only for a page whose session armed the engine. A plain
    Playwright browser in the same process, or a page of a session that did
    not, sends the move as the caller wrote it."""
    import asyncio

    sent = []

    class _Channel:
        _object = None

        async def send(self, method, timeout_calculator, params):
            sent.append((method, params))

    async def original(self, x, y, steps=None):
        sent.append(("original", (x, y, steps)))

    channel = _Channel()
    mouse = type("M", (), {"_channel": channel})()
    wrapped = _cursor._wrap_mouse_move(original)

    class _Ctx:
        _browser = None

    page = type("P", (), {})()
    page._browser_context = _Ctx()
    channel._object = page

    asyncio.run(wrapped(mouse, 5.0, 6.0))
    assert sent == [("original", (5.0, 6.0, None))]

    browser = _FakeBrowser()
    page._browser_context._browser = browser
    assert _cursor.enable_for(browser, seed=3, max_seconds=1.0)
    sent.clear()
    asyncio.run(wrapped(mouse, 5.0, 6.0))
    assert sent == [("mouseMove", {"x": 5.0, "y": 6.0, "humanise": True})]
    sent.clear()
    asyncio.run(wrapped(mouse, 5.0, 6.0, steps=7))   # the caller's own steps
    assert sent == [("original", (5.0, 6.0, 7))]


# ═══════════════════════════════════════════════════════════════════════════
# 3. The same seed produces the same first waypoint
# ═══════════════════════════════════════════════════════════════════════════

def _drive(page, pointer, to_x, to_y):
    pointer._travel(to_x, to_y)
    return page.moves


def _fresh_session_moves(seed):
    page, pointer = make_pointer(seed=seed, max_seconds=1.0)
    return _drive(page, pointer, 900.0, 500.0)


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
    _, pointer = make_pointer(seed=31337, max_seconds=1.0)
    start = pointer.hand.start_point(1280, 720)
    assert start != (0.0, 0.0)
    assert 0 < start[0] < 1280 and 0 < start[1] < 720
    assert start == pointer.hand.start_point(1280, 720)


@pytest.mark.unit
def test_a_page_with_no_viewport_still_does_not_start_at_the_origin(stub_motion):
    """The silent reinstatement of the very invariant this work removes.

    ``start_point`` used to return exactly (0, 0) whenever the page would not
    report a size, so every session of every install departed from the same
    corner - and said nothing about it.
    """
    page, pointer = make_pointer(seed=31337, max_seconds=1.0)
    page.viewport_size = None
    with pytest.warns(RuntimeWarning, match="no viewport size"):
        start = pointer.hand.start_point(None, None)
    assert start != (0.0, 0.0)
    assert 0 < start[0] < 1280 and 0 < start[1] < 720
    # ...seeded, so two installs still do not agree
    assert make_pointer(seed=4242, max_seconds=1.0)[1].hand.start_point(
        None, None) != start


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
    _, pointer = make_pointer(seed=5, max_seconds=1.0)
    with pytest.warns(RuntimeWarning, match="start_point"):
        first = pointer.hand.start_point(1280, 720)
    assert first != (0.0, 0.0)
    # ...and exactly once: a warning per click would be its own breakage.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pointer.hand.start_point(1280, 720)
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
    page, pointer = make_pointer(seed=5, max_seconds=1.0, width=800, height=600)
    moves = _drive(page, pointer, 400.0, 300.0)
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
    page, pointer = make_pointer(seed=6, max_seconds=1.0)
    moves = _drive(page, pointer, 640.0, 360.0)
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
    page, pointer = make_pointer(seed=61, max_seconds=1.0)
    moves = _drive(page, pointer, 640.0, 360.0)
    assert moves == [(302.0, 202.0)], moves


@pytest.mark.unit
def test_a_broken_generator_never_breaks_an_action(stub_motion, monkeypatch):
    class _Broken:
        def __init__(self, seed, max_seconds):
            pass

        def path(self, fx, fy, tx, ty):
            raise RuntimeError("generator exploded")

    monkeypatch.setattr(_StubMotionModule, "MotionProfile", _Broken, raising=False)
    page, pointer = make_pointer(seed=7, max_seconds=1.0)
    with pytest.warns(RuntimeWarning, match="generator raised"):
        assert _drive(page, pointer, 100.0, 100.0) == []
    assert (pointer.hand.x, pointer.hand.y) == (100.0, 100.0)


@pytest.mark.unit
def test_two_tuple_waypoints_are_accepted_and_paced(stub_motion):
    out = pointer_mod._normalise_waypoints([(1, 2), (3, 4)], 1.0)
    assert out == [(1.0, 2.0, 0.5), (3.0, 4.0, 0.5)]
    out = pointer_mod._normalise_waypoints([(1, 2, 0.01)], 1.0)
    assert out == [(1.0, 2.0, 0.01)]


# ═══════════════════════════════════════════════════════════════════════════
# 5. The two halves fit: the wiring driving the real generator
# ═══════════════════════════════════════════════════════════════════════════

def _real_session_moves(seed, max_seconds=1.5, width=1280, height=720):
    page, pointer = make_pointer(seed=seed, max_seconds=max_seconds,
                                 width=width, height=height)
    assert pointer.active
    return _drive(page, pointer, 900.0, 500.0)


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
    from invisible_core.juggler import _motion

    motion = _motion.CursorMotion(4242)
    raw = motion.path(10.0, 10.0, 1200.0, 680.0)
    for cap in (0.3, 1.5):
        paced = pointer_mod._normalise_waypoints(raw, cap)
        assert sum(d for _, _, d in paced) <= cap + 1e-9
    # ...and a generous cap must not stretch a short movement to fill it.
    short = pointer_mod._normalise_waypoints(motion.path(10.0, 10.0, 40.0, 30.0), 10.0)
    assert sum(d for _, _, d in short) < 10.0


# ═══════════════════════════════════════════════════════════════════════════
# 6. The mechanism itself: where the server's hand is reached from
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_the_client_wraps_only_the_explicit_move():
    """The client does not walk any more: `Frame` and `Mouse.click/wheel` are the
    plain protocol calls, and the server draws the rest. A wrapper that came
    back on one of them would humanise twice."""
    from invisible_playwright._pw._impl._frame import Frame
    from invisible_playwright._pw._impl._input import Mouse

    assert _cursor._ensure_patched() is True
    assert getattr(Mouse.move, _cursor._MARKER, False)
    for name in ("_click", "dblclick", "hover", "tap", "check", "uncheck"):
        assert not getattr(getattr(Frame, name), _cursor._MARKER, False), name
    for name in ("_click", "wheel", "click", "dblclick", "down", "up"):
        assert not getattr(getattr(Mouse, name), _cursor._MARKER, False), name


@pytest.mark.unit
def test_the_wrapper_accepts_the_arguments_the_bindings_actually_pass():
    """Both generated API layers call the implementation entirely by keyword -
    ``move(x=, y=, steps=)``. A wrapper that renames one of those parameters
    does not shadow the method, it raises TypeError on every call to it, and no
    amount of testing the wrapper positionally would show it."""
    from invisible_playwright._pw._impl._input import Mouse

    assert _cursor._ensure_patched() is True
    original = inspect.signature(
        Mouse.move.__wrapped__ if hasattr(Mouse.move, "__wrapped__")
        else Mouse.move).parameters
    assert list(original) == ["self", "x", "y", "steps"]


@pytest.mark.unit
def test_the_funnel_the_server_reads_still_exists():
    """The explicit move is the one thing the client marks; if the bindings
    rename it the mark lands on nothing and every session's own moves teleport."""
    from invisible_playwright._pw._impl._input import Mouse

    assert callable(getattr(Mouse, "move", None))
    assert '"mouseMove"' in inspect.getsource(_cursor._wrap_mouse_move(Mouse.move))


@pytest.mark.unit
def test_the_wrapper_is_installed_exactly_once():
    from invisible_playwright._pw._impl._input import Mouse

    assert _cursor._ensure_patched() is True
    first = Mouse.move
    assert _cursor._ensure_patched() is True
    assert Mouse.move is first, "re-arming must not stack a second wrapper"
    assert getattr(Mouse.move, _cursor._MARKER, False) is True


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
    page, pointer = make_pointer(seed=99, max_seconds=1.0)
    observed = {}

    def run(position, timeout):
        observed["moves_before_action"] = list(page.moves)
        observed["position"] = position
        observed["timeout"] = timeout

    pointer.act("main", "#buy", None, 30.0, run)

    moves = observed["moves_before_action"]
    assert len(moves) > 1, "the pointer must actually travel, not jump"
    # The pointer is standing ON the element when the action starts, and the
    # last hop onto the click point is the action's own move - the single event
    # the hit-target check gets to see.
    landing = _landing_of(observed["position"])
    assert 400.0 <= moves[-1][0] <= 500.0 and 300.0 <= moves[-1][1] <= 340.0
    assert 0 < math.hypot(moves[-1][0] - landing[0],
                          moves[-1][1] - landing[1]) < 25.0
    # And no waypoint is left pending to fire inside the action's own window.
    assert page.moves == moves


@pytest.mark.unit
def test_the_action_is_told_to_click_off_the_geometric_centre(stub_motion):
    """B3: ``box.x + width/2, box.y + height/2`` is an exact number that every
    install produces for every element-targeted action, and it is readable from
    a single event. The action itself has to be aimed somewhere else."""
    seen = []
    for seed in range(24):
        page, pointer = make_pointer(seed=seed, max_seconds=1.0)
        captured = {}
        pointer.act("main", "#buy", None, 30.0,
                    lambda position, timeout: captured.update(
                        position=position, timeout=timeout))
        assert captured["position"] is not None, "the action still aims at the centre"
        # the first attempt is bounded, so the caller's own call is reachable
        assert captured["timeout"] == 5.0
        seen.append(_landing_of(captured["position"]))

    centre = (450.0, 320.0)
    assert centre not in seen
    for x, y in seen:
        assert 400.0 < x < 500.0 and 300.0 < y < 340.0
    assert len(set(seen)) > 20, "the landing point must vary per seed"


@pytest.mark.unit
def test_two_actions_of_one_session_do_not_land_on_the_same_pixel(stub_motion):
    page, pointer = make_pointer(seed=7, max_seconds=1.0)
    seen = []
    for _ in range(8):
        captured = {}
        pointer.act("main", "#buy", None, 30.0,
                    lambda position, timeout: captured.update(position=position))
        seen.append(_landing_of(captured["position"]))
    assert len(set(seen)) >= 7, seen


@pytest.mark.unit
def test_a_caller_supplied_position_is_never_overridden(stub_motion):
    """The caller asked to click a specific pixel. That is not ours to move."""
    page, pointer = make_pointer(seed=11, max_seconds=1.0)
    captured = {}
    pointer.act("main", "#buy", {"x": 3.0, "y": 4.0}, 30.0,
                lambda position, timeout: captured.update(
                    position=position, timeout=timeout))
    assert captured["position"] == {"x": 3.0, "y": 4.0}
    assert captured["timeout"] == 30.0
    # ...and the pointer still walked there.
    assert page.moves and page.moves[-1] != (450.0, 320.0)


@pytest.mark.unit
def test_a_point_that_does_not_hit_the_element_is_not_used(stub_motion):
    """A bounding box is not the element: an inline link wrapping two lines has
    points inside its box that belong to the page behind it. Handing one of
    those to the action would turn a working click into a hit-target failure,
    so a point we chose is checked before it is used."""
    page, pointer = make_pointer(seed=13, max_seconds=1.0, hits=False)
    captured = {}
    pointer.act("main", "#buy", None, 30.0,
                lambda position, timeout: captured.update(position=position))
    assert captured["position"] is None, "an unverified point reached the action"


@pytest.mark.unit
def test_the_landing_is_checked_by_the_actions_own_hit_test_in_the_boxs_space(
        stub_motion, monkeypatch):
    """The off-centre point goes to `Actions.hit_target` as it is, in the
    main-frame space the box was read in, with the frame the element is in: it
    shifts it into that frame with the same arithmetic its own pre-commit check
    uses. The cursor used to ask `document.elementFromPoint` itself, at those
    main-frame coordinates, inside the element's document - wrong in every
    nested frame and in every shadow root, so the landing always fell back to
    the exact centre."""
    page, pointer = make_pointer(seed=13, max_seconds=1.0)
    monkeypatch.setattr(pointer_mod, "landing_point",
                        lambda *args, **kwargs: (410.0, 312.0))
    aim = pointer._choose_landing("frame-7", "#buy", None)
    assert page.hit_asked == [("frame-7", "el", (410.0, 312.0))]
    assert aim.override == {"x": 10.0, "y": 12.0}
    assert page.disposed == ["el"], "the probe's handle must be released"


@pytest.mark.unit
def test_a_landing_that_is_not_actionable_retries_the_caller_s_own_call(stub_motion):
    """The invariant that outranks every stealth property in this module:
    aiming must never turn a call that would have succeeded into one that
    fails."""
    page, pointer = make_pointer(seed=17, max_seconds=1.0)
    calls = []

    def run(position, timeout):
        calls.append((position, timeout))
        if position is not None:
            raise TimeoutError("hit target check failed")
        return "clicked"

    with pytest.warns(RuntimeWarning, match="off-centre"):
        result = pointer.act("main", "#buy", None, 30.0, run)
    assert result == "clicked"
    assert len(calls) == 2
    assert calls[0][0] is not None and calls[0][1] == 5.0
    # the caller's own call, with what is left of the caller's time
    assert calls[1][0] is None and 24.0 < calls[1][1] <= 30.0


@pytest.mark.unit
def test_landing_can_be_switched_off(stub_motion, monkeypatch):
    monkeypatch.setenv(_cursor.LANDING_ENV, "off")
    page, pointer = make_pointer(seed=19, max_seconds=1.0)
    captured = {}
    pointer.act("main", "#buy", None, 30.0,
                lambda position, timeout: captured.update(
                    position=position, timeout=timeout))
    assert captured == {"position": None, "timeout": 30.0}
    assert page.moves, "the pointer still travels; only the aim is left alone"


@pytest.mark.unit
def test_an_offscreen_target_is_left_to_the_action(stub_motion):
    """The action is about to scroll; aiming at where the element used to be
    would send waypoints out of the viewport, which parks the pointer at the
    origin. Skipping is strictly better than that."""
    page, pointer = make_pointer(
        seed=100, max_seconds=1.0, width=800, height=600,
        box={"x": 100.0, "y": 2400.0, "width": 80.0, "height": 30.0})
    assert pointer.approach("main", "#deep", None) is None
    assert page.moves == []


@pytest.mark.unit
def test_a_missing_element_never_adds_a_wait_or_an_error(stub_motion):
    """Aiming is a hint. If the element is not there yet we give up silently and
    let the action do its own waiting and raise its own errors."""
    page, pointer = make_pointer(seed=101, max_seconds=1.0, box=None)
    assert pointer.approach("main", "#later", None) is None
    assert page.moves == []
    assert page.queried == [("main", "#later")]


@pytest.mark.unit
def test_a_probe_the_page_cannot_answer_is_a_missing_element(stub_motion):
    """The frame navigated or the selector is not one: not an error of ours."""
    from invisible_core.juggler.injected import ContextDestroyed

    page, pointer = make_pointer(seed=101, max_seconds=1.0)

    def gone(*a, **k):
        raise ContextDestroyed("navigated")
    page.injected.query_selector = gone
    assert pointer.approach("main", "#x", None) is None
    assert page.moves == []


@pytest.mark.unit
def test_a_skipped_waypoint_does_not_shorten_the_movement(stub_motion, monkeypatch):
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
    page, pointer = make_pointer(seed=102, max_seconds=1.0)

    t0 = pointer_mod._CLOCK.now()
    pointer._travel(640.0, 360.0)
    # 5 waypoints x 50 ms = 250 ms of schedule. The endpoint belongs to the
    # caller and is not sent here, but its deadline is still waited out, so the
    # movement takes the whole 250 ms rather than 200.
    assert page.moves == [(300.0, 200.0)] * 4
    assert pointer_mod._CLOCK.now() - t0 == pytest.approx(0.25)


@pytest.mark.unit
def test_a_trial_run_does_not_move_the_pointer(stub_motion):
    """trial=True asks for the actionability checks without the action, so a
    real pointer would not move either."""
    page, pointer = make_pointer(seed=8, max_seconds=1.0)
    assert pointer.approach("main", "#b", None, trial=True) is None
    assert page.moves == []


@pytest.mark.unit
def test_the_hand_stands_where_the_pointer_was_left(stub_motion):
    """Whatever else moved the pointer - an explicit `mouse.move`, the click an
    action ends with - is where the next walk starts, because the server's own
    record of it (`Actions.position`) is what the hand reads."""
    page, pointer = make_pointer(seed=8, max_seconds=1.0)
    assert pointer._here() == pointer.hand.start_point(1280, 720)  # never moved
    page.actions.position = (640.0, 360.0)   # another writer moved it
    assert pointer._here() == (640.0, 360.0)


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
    # Added 2026-08-28, and it is the FIRST entry on this list that the motion
    # work did not already justify. `humanize` needed no new argument because
    # the motion arrives through methods people already call; the visible dot
    # has nothing to arrive through - it is a property of the session, decided
    # before the first window is built, so it has to be said at construction.
    ("show_cursor", inspect.Parameter.KEYWORD_ONLY),
]


@pytest.mark.unit
@pytest.mark.parametrize("cls", [InvisiblePlaywright, AsyncInvisiblePlaywright])
def test_public_constructor_signature_unchanged(cls):
    """The two constructors have the SAME shape, and it changes only on purpose.

    ⛔ What this guards is not "never add an argument" - it is that adding one
    is a decision somebody wrote down, and that both entry points get it. The
    sync and async classes have drifted before: a knob honoured in one and not
    the other is a documented feature that works depending on which import the
    caller happened to use.
    """
    params = list(inspect.signature(cls.__init__).parameters.values())
    assert [(p.name, p.kind) for p in params] == _EXPECTED_SIGNATURE


@pytest.mark.unit
@pytest.mark.parametrize("cls", [InvisiblePlaywright, AsyncInvisiblePlaywright])
def test_neither_entry_point_decides_the_cursor_default(cls):
    """⛔ THE ASSERTION IS None, NOT True, and that is the whole point.

    The default used to be written into six signatures plus the engine's own
    fallback, so flipping it meant finding all seven and a missed one would
    give the sync API a different answer from the async one with every test
    still green. Now the signatures say None - "the caller did not say" - and
    `invisible_core.prefs.DEFAULT_SHOW_CURSOR` is the only thing that decides.
    A True here would be the old defect written the other way round.

    What the value IS gets asserted where it lives, in the core's own suite,
    and end to end by `test_the_show_cursor_kwarg_reaches_the_prefs`.

    (Owner decision 2026-08-28: on. Camoufox ships it on too. The argument for
    off has not gone away and is recorded next to the constant: a pointer
    gliding across a window with nobody touching the mouse reads as "this is a
    bot" to any person glancing at the monitor.)
    """
    assert inspect.signature(cls.__init__).parameters["show_cursor"].default is None


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
# The authoritative definition is ``invisible_core._cookies._sub_seed``, the
# persona cookie mixer. Not because it is the nicest - because it is the one
# that already shipped (in this package's ``_recaptcha_seed`` until the cookie
# data moved to the core). Its outputs are baked into the seed -> cookie
# reproducibility this package documents, so it is the copy that cannot be
# changed; every copy left in this package has to agree with it.
#
# The tests below pin two things: how many copies exist, and that they agree.

_PKG_DIR = pathlib.Path(_cursor.__file__).resolve().parent

# The FNV-1a 64-bit offset basis and prime. Written as numbers rather than
# matched as text so reformatting or a change of hex case cannot hide a copy.
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3

# module stem -> function name. Adding a copy means adding a line here, which
# is the point: it is a decision, not an accident.
# NONE since 0.30.0: the two copies (`_behaviour._sub_seed`, byte-identical,
# and `_motion._mix`, reduced to int31) moved with the Juggler client into
# invisible_core.juggler, beside the authoritative one in invisible_core._cookies,
# and the core's own census counts them there. A mixer appearing in THIS package
# again is a fourth copy.
_EXPECTED_MIXERS: set = set()


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
def test_no_copy_of_the_sub_stream_mix_lives_in_this_package():
    """The mix is `invisible_core.seedmix`, ONE copy since 0.30.0.

    This is the test that fails when someone adds a mixer here - including one
    added by copy-paste into a module that has nothing to do with motion. The
    two tests that checked the three copies agreed moved to the core
    (`tests/test_one_seed_mixer.py`), where the copies became one function,
    with a digest of what they answered before the merge.
    """
    assert _fnv_functions() == _EXPECTED_MIXERS


@pytest.mark.unit
def test_the_page_seed_is_a_different_derivation_and_stays_one():
    """``page_motion_seed`` is seed x tab ordinal, not seed x tag.

    It is deliberately not the FNV mix - it answers a different question - and
    the discovery test above is what keeps it that way: turning it into a
    fourth FNV copy would show up there as an unexpected mixer.
    """
    assert ("_cursor", "page_motion_seed") not in _fnv_functions()
    from invisible_core.seedmix import sub_seed as authoritative

    for seed in _SEED_CORPUS[:40]:
        assert 0 <= _cursor.page_motion_seed(seed, 0) < 2**31
        # ...and it is genuinely its own function, not the mix under a name.
        assert _cursor.page_motion_seed(seed, 0) != authoritative(seed, "0") & 0x7FFFFFFF


@pytest.mark.unit
def test_the_core_we_resolve_against_decides_the_cursor_default():
    """⛔ A BEHAVIOUR THIS PACKAGE DEPENDS ON, MADE INTO A NAME IT IMPORTS.

    The launcher forwards `show_cursor=None` and lets `invisible_core` resolve
    what "the caller did not say" means. Every core released before
    2026-08-28 reads None as "do not emit the pref at all", so the engine falls
    back to its compiled default and the cursor is OFF - no exception, no
    missing attribute, nothing for a CI runner to go red on, and a released
    wrapper whose documented default silently does not happen.

    The import floor cannot see that class: nothing fails to import. So the
    dependency is turned into one it CAN see. This test goes red on any
    environment resolving a core older than the one that owns
    `DEFAULT_SHOW_CURSOR`, which is exactly the ordering the release runbook
    requires - publish the core, then move the pin, then release the wrapper.
    """
    from invisible_core.prefs import DEFAULT_SHOW_CURSOR

    assert isinstance(DEFAULT_SHOW_CURSOR, bool)
    ip = InvisiblePlaywright(timezone="America/New_York", seed=42)
    assert ip._build_prefs()["stealthfox.showcursor"] is DEFAULT_SHOW_CURSOR, (
        "the wrapper's silent default disagrees with the core's declaration; "
        "one of the two has grown a second opinion")
