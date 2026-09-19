"""A drag travels along a path, and the path is drawn by the one generator.

⛔ WHAT IT LOOKED LIKE BEFORE, measured 2026-09-16 against a real page whose
source element was `draggable`. The whole gesture was five events:

    mousemove(A) mousedown(A) mousemove(B) mousemove(B) mouseup(B)

Three things are wrong with that and only one of them is about stealth.

* The 480-pixel journey happened in ONE event. No hand does that, and no pointer
  device reports it.
* The two events carrying it were IDENTICAL and 7 ms apart. A move event is born
  from moving, so a pair like that describes nothing at all. It was deliberate -
  one jump alone did not reliably start the drag - which is the shape of a
  workaround standing in for the missing path.
* And on a `draggable` source it did not work: Gecko started a native drag
  session off that single jump, `dispatchDragEvent` then failed with
  NS_ERROR_FAILURE, and the `mouseup` never went out - so the button stayed
  DOWN, poisoning the `buttons` field of every event after it.

All three come from the same absence, which is why they are fixed by the same
change rather than by three.

⛔ AND THE PATH IS NOT DRAWN HERE. `_motion.CursorMotion` draws it and `_pacing`
delivers it, the same two things the client's cursor uses. A second generator on
this side would be two definitions of "how a pointer moves", agreeing until one
of them was edited.
"""
from __future__ import annotations

import pytest

from invisible_playwright import _pacing
from invisible_playwright._juggler.actions import Actions
from invisible_playwright._juggler.keyboard import BUTTON_MASK

pytestmark = pytest.mark.unit


class _Conn:
    def __init__(self):
        self.sent: list = []

    def send(self, method, params=None, **kw):
        p = params or {}
        self.sent.append((p.get("type"), p.get("x"), p.get("y"),
                          p.get("buttons")))
        return {}


class _Keyboard:
    def modifier_mask(self) -> int:
        return 0


@pytest.fixture()
def instant(monkeypatch):
    """A clock that moves only when something sleeps on it.

    Without this the pacer would judge a test machine hopelessly late and drop
    almost everything - which is correct behaviour and useless for asserting on
    a plan.
    """
    class _Clock:
        def __init__(self):
            self.t = 0.0

        def perf_counter(self):
            return self.t

        def sleep(self, seconds):
            self.t += seconds

    clock = _Clock()
    monkeypatch.setattr(_pacing, "time", clock)
    return clock


class _Injected:
    """Answers the one question `_viewport` asks, so the clamp is exercised.

    Without it `_viewport` falls into its own `except` and reports "unknown",
    under which nothing is clamped - so a test that leaves this out is green on
    a version with no clamp at all.
    """

    def __init__(self, w, h):
        self.size = None if w is None else {"w": w, "h": h}

    def evaluate(self, frame, expression):
        if self.size is None:
            raise RuntimeError("no viewport here")
        return self.size


class _Lifecycle:
    main_frame = "frame"


def _actions(seed=42, budget_s=None, viewport=(None, None)):
    a = Actions.__new__(Actions)
    a.c = _Conn()
    a.session = "session"
    a.keyboard = _Keyboard()
    a.position = (0.0, 0.0)
    a.motion = None
    a.motion_budget_s = budget_s
    a.inj = _Injected(*viewport)
    a.lifecycle = _Lifecycle()
    if seed is not None:
        from invisible_playwright._behaviour import _sub_seed
        from invisible_playwright._motion import CursorMotion
        a.motion = CursorMotion(_sub_seed(seed, "server:drag"))
    return a


def _moves(actions):
    return [(x, y) for kind, x, y, _ in actions.c.sent if kind == "mousemove"]


# ── the travel ──────────────────────────────────────────────────────────────

def test_a_glide_crosses_the_page_in_many_events_not_one(instant):
    a = _actions()
    a._glide((600.0, 400.0))
    moves = _moves(a)
    assert len(moves) > 5, "the pointer crossed the page in %d events" % len(moves)


def _biggest_step(points):
    return max((abs(q[0] - p[0]) + abs(q[1] - p[1])
                for p, q in zip(points, points[1:])), default=0.0)


def _travelled(points):
    return sum(abs(q[0] - p[0]) + abs(q[1] - p[1])
               for p, q in zip(points, points[1:]))


#: The biggest share of a journey one event may carry.
#:
#: ⛔ RELATIVE, NOT AN ABSOLUTE PIXEL COUNT, and the first version of these
#: tests got that wrong. An absolute floor of 100 px failed against a real
#: browser at 195 - and the CONTROL ARM said why: the client's own pointer, on
#: the same journey and the same page, produces a 124 px step too. Under load
#: the pacer drops overtaken points on purpose, so a big step is the discipline
#: working, not a teleport. Measured 2026-09-16 over 820 px of travel: server
#: max step 130 (16%), client max step 124 (15%).
#:
#: What the defect did was carry ALL of it in one event. Half is far above what
#: either arm produces and far below what a jump is.
MAX_SHARE_OF_A_JOURNEY = 0.5


def _no_event_carried_the_journey(points):
    biggest, travelled = _biggest_step(points), _travelled(points)
    assert travelled > 0
    assert biggest <= travelled * MAX_SHARE_OF_A_JOURNEY, (
        "one event moved the pointer %.0f px of a %.0f px journey"
        % (biggest, travelled))


def test_no_single_event_carries_the_whole_journey(instant):
    """⛔ THE KNOWN-BAD INPUT OF THIS FILE, and the exact shape of the defect.

    ⛔ AND NOT "no two events report the same point", which is what this
    asserted first and is WRONG: `_motion._collapse_duplicate_pixels` keeps
    short runs of repeated device pixels ON PURPOSE, because a real device
    reports them - "keeping a repeat sometimes is the point". An assertion
    against that would have been a gate demanding the product be less real.

    What no device does is cross 700 pixels in one report. To watch this fail,
    send the destination twice instead of calling `_glide`.
    """
    a = _actions()
    a._glide((600.0, 400.0))
    _no_event_carried_the_journey([(0.0, 0.0)] + _moves(a))


def test_the_path_ends_exactly_where_it_was_asked_to(instant):
    """A drag that stops one sample short drops on the wrong element."""
    a = _actions()
    a._glide((600.0, 400.0))
    assert _moves(a)[-1] == (600.0, 400.0)
    assert a.position == (600.0, 400.0)


def test_the_travel_carries_the_button_down(instant):
    """⛔ Gecko gives birth to a drag from movement with the button held. A path
    sent with `buttons=0` is a path that moves the cursor and drags nothing."""
    a = _actions()
    a._glide((600.0, 400.0), buttons=BUTTON_MASK[0])
    assert {b for kind, _, _, b in a.c.sent if kind == "mousemove"} == {BUTTON_MASK[0]}


def test_two_drags_in_a_session_do_not_draw_the_same_path(instant):
    """One stream per Actions. The same curve twice is a signature of its own."""
    a = _actions()
    a._glide((600.0, 400.0))
    first = _moves(a)
    a.c.sent.clear()
    a.position = (0.0, 0.0)
    a._glide((600.0, 400.0))
    assert first != _moves(a)


def test_the_path_is_the_seeds_and_not_a_constant(instant):
    def path_of(seed):
        a = _actions(seed=seed)
        a._glide((600.0, 400.0))
        return _moves(a)

    assert path_of(1) != path_of(2)


def test_without_a_seed_a_glide_is_one_event(instant):
    """Turning humanising off keeps meaning what it meant: no motion, not a
    default one."""
    a = _actions(seed=None)
    a._glide((600.0, 400.0))
    assert _moves(a) == [(600.0, 400.0)]


def test_the_callers_budget_is_honoured(instant):
    """⛔ THE DEFECT A MISSING BUDGET WOULD REINTRODUCE. The client caps every
    movement it makes at `humanize=<seconds>`; this movement is generated on the
    server, so without the cap arriving here a caller who asked for short
    movements would get them everywhere except in a drag."""
    tight = _actions(budget_s=0.05)
    tight._glide((600.0, 400.0))
    loose = _actions(budget_s=5.0)
    loose._glide((600.0, 400.0))
    assert instant.t >= 0.0
    assert len(_moves(tight)) < len(_moves(loose)), (
        "a 50 ms budget produced as many events as a 5 s one")


#: A viewport big enough for the overshoot to matter. ⛔ The first version of
#: the test below used 200x150 and was green with no clamp at all, because the
#: overshoot scales with the DISTANCE and over 200 px there is nothing to clamp.
#: Measured 2026-09-16 over 240 generated paths that end near an edge of a
#: 1280x720 viewport: 76 of them leave it, by up to 31.8 px.
_VIEWPORT = (1280, 720)


def test_a_waypoint_never_leaves_the_viewport(instant):
    """⛔ A pointer event outside the viewport is not ignored: the browser parks
    the cursor at the ORIGIN, in the middle of the movement. A curved path near
    an edge goes outside on its own - the caller does not have to aim anywhere
    strange - so this is a real journey, not a hypothetical one.

    To watch it fail, drop the `clamp_to_viewport` call in `_glide`.
    """
    a = _actions(viewport=_VIEWPORT)
    a.position = (10.0, 10.0)
    a._glide((1270.0, 710.0))
    w, h = _VIEWPORT
    outside = [p for p in _moves(a)
               if not (0 <= p[0] <= w - 1 and 0 <= p[1] <= h - 1)]
    assert not outside, "%d waypoints left the viewport: %r" % (
        len(outside), outside[:3])


def test_the_destination_is_emitted_unclamped(instant):
    """It is where the movement has to end, and the caller chose it. Clamping it
    would move a drag onto a different element than the one it resolved."""
    a = _actions(viewport=(200, 150))
    a.position = (10.0, 10.0)
    a._glide((640.0, 480.0))
    assert _moves(a)[-1] == (640.0, 480.0)


def test_an_unreadable_viewport_clamps_nothing(instant):
    """Unknown is not "zero by zero". Guessing a viewport would move points that
    were fine, and the probe failing is not a reason to bend the path."""
    a = _actions(viewport=(None, None))
    a.position = (10.0, 10.0)
    a._glide((1270.0, 710.0))
    assert _moves(a)[-1] == (1270.0, 710.0)


def test_a_tight_budget_still_arrives(instant):
    """Capping the time must shorten the movement, never truncate it."""
    a = _actions(budget_s=0.02)
    a._glide((600.0, 400.0))
    assert _moves(a)[-1] == (600.0, 400.0)


# ── the whole gesture ───────────────────────────────────────────────────────
#
# ⛔ THESE EXIST BECAUSE THE TESTS ABOVE CALL `_glide` DIRECTLY, and a test that
# stops at the helper cannot see whether the gesture still calls it. Both halves
# were green while `drag_and_drop` teleported: a mutation that put the two
# identical events back survived every assertion above.

def _dragging(a, source=(100.0, 80.0), target=(580.0, 420.0)):
    """Run `drag_and_drop` with the actionability retry standing in.

    Only the retry is replaced - it is what needs a browser - so the beats, the
    order and the button state are the real ones.
    """
    points = {"#a": source, "#b": target}

    def retry(selector, fn, **kw):
        return fn(None, None, points[selector])

    a._retry = retry
    return a.drag_and_drop("#a", "#b")


def test_the_gesture_is_press_travel_release_in_that_order(instant):
    a = _actions()
    _dragging(a)
    kinds = [k for k, *_ in a.c.sent]
    assert kinds[0] == "mousemove"
    assert kinds.count("mousedown") == 1 and kinds.count("mouseup") == 1
    assert kinds.index("mousedown") < kinds.index("mouseup")
    assert kinds[-1] == "mouseup"


def test_the_gesture_travels_to_the_target_instead_of_jumping(instant):
    """⛔ THE KNOWN-BAD INPUT FOR THE JUNCTION. To watch it fail, replace the
    `_glide` in `run` with the two identical `_mouse_event` calls it used to
    send."""
    a = _actions()
    _dragging(a)
    down = [k for k, *_ in a.c.sent].index("mousedown")
    travel = [(x, y) for k, x, y, _ in a.c.sent[down:] if k == "mousemove"]
    assert len(travel) > 5, (
        "the button-down journey took %d events" % len(travel))


def test_no_event_of_the_whole_gesture_carries_a_jump(instant):
    """⛔ ASSERTED OVER THE WHOLE SEQUENCE, not over one leg of it, because the
    join between the approach and the travel belongs to neither leg and is
    where a defect can hide from both."""
    a = _actions()
    a.position = (700.0, 500.0)
    _dragging(a)
    _no_event_carried_the_journey([(700.0, 500.0)] + _moves(a))


def test_the_travel_does_not_re_report_where_the_press_happened(instant):
    """⛔ A path INCLUDES where it starts, and after the `mousedown` the pointer
    is already there. Sending that waypoint would report a move to the point the
    previous event just reported - an event carrying no movement at all, which
    is different from the device-level pixel repeats `_motion` keeps on purpose.
    The client's walk drops it for the same reason (`path[1:]`).
    """
    a = _actions()
    a.position = (700.0, 500.0)
    _dragging(a, source=(100.0, 80.0))
    kinds = [k for k, *_ in a.c.sent]
    down = kinds.index("mousedown")
    first_travel = next((x, y) for k, x, y, _ in a.c.sent[down + 1:]
                        if k == "mousemove")
    assert first_travel != (100.0, 80.0)


def test_the_gesture_approaches_the_source_instead_of_jumping(instant):
    """⛔ The cursor was somewhere before this call. Arriving at the source in
    one event is the same tell as crossing the page in one. To watch it fail,
    replace the first `_glide` with a single `_mouse_event`."""
    a = _actions()
    a.position = (700.0, 500.0)
    _dragging(a)
    down = [k for k, *_ in a.c.sent].index("mousedown")
    approach = [(x, y) for k, x, y, _ in a.c.sent[:down] if k == "mousemove"]
    assert len(approach) > 5, (
        "the cursor reached the source in %d events" % len(approach))


def test_the_whole_travel_happens_with_the_button_down(instant):
    """A journey sent with `buttons=0` moves the cursor and drags nothing."""
    a = _actions()
    _dragging(a)
    down = [k for k, *_ in a.c.sent].index("mousedown")
    up = [k for k, *_ in a.c.sent].index("mouseup")
    travel = [b for k, _, _, b in a.c.sent[down + 1:up] if k == "mousemove"]
    assert travel and set(travel) == {BUTTON_MASK[0]}


def test_a_failure_after_the_press_still_lets_the_button_go(instant):
    """⛔ A button left down poisons EVERY subsequent action: the `buttons`
    field of every event after it would say "pressed". Measured 2026-09-16, this
    is not hypothetical - the old drag raised on a `draggable` source and left
    it down."""
    a = _actions()
    points = {"#a": (100.0, 80.0)}

    def retry(selector, fn, **kw):
        if selector == "#b":
            raise RuntimeError("the target went away")
        return fn(None, None, points[selector])

    a._retry = retry
    with pytest.raises(RuntimeError):
        a.drag_and_drop("#a", "#b")
    kinds = [k for k, *_ in a.c.sent]
    assert kinds[-1] == "mouseup"
    assert [b for k, _, _, b in a.c.sent if k == "mouseup"] == [0]


# ── with a browser ──────────────────────────────────────────────────────────

@pytest.mark.e2e
def test_a_real_page_sees_a_drag_that_travelled(firefox_binary):
    """The proof, and the only arm that exercises the whole chain.

    ⛔ It also covers the FUNCTIONAL half: before the fix this call raised
    `NS_ERROR_FAILURE` on a `draggable` source and left the button down. A test
    that only counted events would have passed on a version that still threw,
    because it would never have got as far as counting.
    """
    import urllib.parse

    from invisible_playwright import InvisiblePlaywright

    html = (
        "<style>body{margin:0}"
        "#a{position:absolute;left:40px;top:40px;width:120px;height:80px}"
        "#b{position:absolute;left:520px;top:380px;width:120px;height:80px}"
        "</style>"
        "<div id='a' draggable='true'>source</div><div id='b'>target</div>"
        "<script>window.__e=[];window.__d=[];"
        "for (const t of ['mousemove','mousedown','mouseup'])"
        " document.addEventListener(t,"
        "  e => window.__e.push([t, e.clientX, e.clientY, e.buttons]), true);"
        "for (const t of ['dragstart','dragover','drop','dragend'])"
        " document.addEventListener(t, e => {"
        "  window.__d.push(t);"
        "  if (t === 'dragover')"
        "   window.__e.push([t, e.clientX, e.clientY, e.buttons]);"
        "  if (t !== 'dragstart') e.preventDefault(); },"
        "  true);"
        "</script>")
    with InvisiblePlaywright(seed=42, binary_path=firefox_binary) as browser:
        page = browser.new_page()
        page.goto("data:text/html," + urllib.parse.quote(html))
        page.mouse.move(300, 300)
        page.evaluate("() => { window.__e = []; }")
        page.drag_and_drop("#a", "#b")
        events = page.evaluate("window.__e")
        dragged = page.evaluate("window.__d")
        # Move again, on its own record, to see what the pointer reports AFTER
        # the gesture. This is the release check, and it is deliberately not
        # folded into `events`: a jump from the target to here is not part of
        # the journey and would be measured as if it were.
        page.evaluate("() => { window.__e = []; }")
        page.mouse.move(700, 120)
        afterwards = page.evaluate("window.__e")

    # ⛔ A COMPLETED DRAG DELIVERS NO `mouseup` TO THE PAGE, and this used to
    # assert that it did. That was not wrong when it was written: the drop never
    # arrived, so the release reached the page as a plain `mouseup`, and
    # counting it was a cheap proxy for the thing that actually matters - that
    # the button is not left down. [B213] made the drag complete, so the release
    # now arrives as `drop` + `dragend`, the way it does in a real browser, and
    # the proxy became false while the intent it stood for stayed true.
    #
    # So the intent is asserted DIRECTLY - what does the pointer report once the
    # gesture is over - and the drag is required to have COMPLETED, which the
    # proxy never checked. Without that second half, "no mouseup" would also be
    # satisfied by a gesture that broke in some new way.
    assert "dragend" in dragged, (
        "the gesture did not complete as a drag, it only did %s - so the "
        "absence of a mouseup says nothing" % (dragged or "nothing"))
    assert afterwards and all(e[3] == 0 for e in afterwards), (
        "the button is still down after the drag, so every later event lies: "
        "%s" % afterwards[:3])

    # ⛔ AND THE JOURNEY IS CARRIED BY TWO KINDS OF EVENT, not one. Once the
    # drag engages, the browser stops sending `mousemove` and sends `dragover`
    # instead - which is what a real one does, and what ours does now that the
    # drag is actually adopted. Counting only the mouse half measures HOW LATE
    # the drag engaged, not how far the pointer went: on an engine that adopts
    # it immediately, three `mousemove` and forty `dragover` is a full journey,
    # and the old count read it as a journey of three.
    kinds = [e[0] for e in events]
    assert "mousedown" in kinds, "the press never reached the page"
    press = kinds.index("mousedown")
    journey = [e for e in events[press + 1:] if e[0] in ("mousemove", "dragover")]

    assert len(journey) > 5, (
        "the whole journey took %d events: %s" % (len(journey), kinds))
    _no_event_carried_the_journey([(e[1], e[2]) for e in journey])

    # And every movement that still arrives as a `mousemove` after the press has
    # to report the button down - one that does not means the press was lost,
    # which is the failure the old count was really guarding against.
    after_press = [e for e in events[press + 1:] if e[0] == "mousemove"]
    assert after_press and all(e[3] == 1 for e in after_press), (
        "a movement after the press reports the button up, so the press was "
        "lost: %s" % after_press[:3])
