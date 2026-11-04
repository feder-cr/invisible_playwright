"""The host gate's comparison (scripts/ci_host_gate.py), on synthetic dumps.

Each property is shown failing on the input it exists for: a value that moves
with the host, a value that does not depend on the identity but is not
retail's, an exception that hides nothing any more. And a value that depends on
the identity is held to host-independence only, which is what lets the gate run
without a list of keys.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci_host_gate as gate  # noqa: E402

LEGS = ("linux-x86_64", "win-x86_64")

# A maximized window on a 1920x1080 screen with a 40 px taskbar, as retail
# Firefox for Windows has it: the frame hangs 8 px off every edge, the viewport
# starts 85 px below the screen's top.
SCREEN = {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040}
NORMAL = {"innerWidth": 1920, "innerHeight": 955, "outerWidth": 1936, "outerHeight": 1048,
          "screenX": -8, "screenY": -8, "mozInnerScreenX": 0, "mozInnerScreenY": 85, "screen": dict(SCREEN),
          "display_mode_fullscreen": False, "fullscreen_element": False, "window_fullScreen": False,
          "events": {"mousemove": [[0, 85]], "mousedown": [[0, 85]], "mouseup": [[0, 85]], "click": [[0, 85]]}}
ENTERED = {"how": "fullscreenchange", "request": "resolved", "settled": True}
FULLSCREEN = {"innerWidth": 1920, "innerHeight": 1080, "outerWidth": 1920, "outerHeight": 1080,
              "screenX": 0, "screenY": 0, "mozInnerScreenX": 0, "mozInnerScreenY": 0, "screen": dict(SCREEN),
              "display_mode_fullscreen": True, "fullscreen_element": True, "window_fullScreen": True,
              "events": {"mousemove": [[0, 0]], "mousedown": [[0, 0]], "mouseup": [[0, 0]], "click": [[0, 0]]},
              "transition": dict(ENTERED)}
# The defect that escaped: in fullscreen the engine kept declaring the maximized window.
BUG_FULLSCREEN = dict(FULLSCREEN, outerWidth=1936, outerHeight=1048, screenX=-8, screenY=-8, mozInnerScreenY=85,
                      events={"mousemove": [[0, 85]], "click": [[0, 85]]})
# Linux under Xvfb without a window manager: the DOM goes fullscreen, the window does not.
XVFB_FULLSCREEN = dict(FULLSCREEN, innerHeight=1040, outerHeight=1040, display_mode_fullscreen=False)


def retail_states(**override):
    """The three states as retail has them, with some replaced: {name: values}."""
    states = {"normal": NORMAL, "fullscreen": FULLSCREEN, "after_fullscreen": dict(NORMAL, transition=dict(ENTERED))}
    states.update(override)
    return {gate.STATE + name: json.loads(json.dumps(values)) for name, values in states.items()}


def broken(**override):
    """The names of the invariants the states break, the others as retail's."""
    return {inv.name for inv, state, message in gate.check_invariants(gate.visit_states(retail_states(**override)))}


def write(tmp_path, items_by_leg_seed, retail):
    dumps = tmp_path / "dumps"
    ref = tmp_path / "ref"
    dumps.mkdir(parents=True)
    ref.mkdir(parents=True)
    for dpr in gate.DPRS:
        (ref / ("dpr%s.json" % dpr)).write_text(json.dumps({"items": retail}), encoding="utf-8")
        for leg in LEGS:
            for seed in gate.SEEDS:
                items = dict(retail_states(), **items_by_leg_seed(leg, seed))
                (dumps / ("%s_dpr%s_seed%d.json" % (leg, dpr, seed))).write_text(json.dumps(
                    {"leg": leg, "dpr": dpr, "seed": seed,
                     "result": {"format": gate.FORMAT, "items": items, "noise": {}}}), encoding="utf-8")
    return dumps, ref


def run(tmp_path, items, retail, exceptions=None, notes=None):
    dumps, ref = write(tmp_path, items, retail)
    return gate.compare(dumps, ref, exceptions or {}, notes)


def test_identical_hosts_and_retail_pass(tmp_path):
    assert run(tmp_path, lambda leg, seed: {"surface.gpu.limits": 8}, {"surface.gpu.limits": 8}) == []


def test_a_value_that_moves_with_the_host_fails(tmp_path):
    items = lambda leg, seed: {"surface.gpu.isFallbackAdapter": leg.startswith("linux")}
    failures = run(tmp_path, items, {"surface.gpu.isFallbackAdapter": False})
    assert any(f.startswith("host  surface.gpu.isFallbackAdapter") for f in failures)


def test_an_identity_value_needs_only_host_independence(tmp_path):
    # the screen width changes with the seed; retail has its own, and that is fine
    items = lambda leg, seed: {"surface.screen.width": 1000 + seed}
    assert run(tmp_path, items, {"surface.screen.width": 2560}) == []


def test_a_value_that_does_not_depend_on_the_identity_must_be_retails(tmp_path):
    items = lambda leg, seed: {"surface.media.webrtc.vp9": [True, True, False]}
    failures = run(tmp_path, items, {"surface.media.webrtc.vp9": [True, True, True]})
    assert len([f for f in failures if f.startswith("retail surface.media.webrtc.vp9")]) == len(LEGS) * len(gate.DPRS)


def test_noise_and_network_are_excused_known_defects_must_still_be_seen(tmp_path):
    items = lambda leg, seed: {"surface.timing.load": seed * 7 + len(leg), "surface.intl.zone": "UTC",
                               "text.canvas.H": "linux" if leg.startswith("linux") else "win"}
    exceptions = {
        "noise": [{"keys": ["surface.timing.*"], "why": "varies between two retail launches"}],
        "network": [{"keys": ["surface.intl.zone"], "why": "from the egress IP"}],
        "known_defects": [
            {"keys": ["text.*"], "legs": ["linux-x86_64"], "why": "phase 4"},
            {"keys": ["surface.gone.*"], "why": "fixed already"},
        ],
    }
    failures = run(tmp_path, items, {"surface.intl.zone": "Asia/Bangkok", "text.canvas.H": "win"}, exceptions)
    assert failures == ["known defect no longer seen, remove it from exceptions.json: "
                        "['surface.gone.*'] (fixed already)"]


def test_a_known_defect_is_scoped_to_its_legs(tmp_path):
    items = lambda leg, seed: {"text.canvas.H": "x" if leg == "win-x86_64" else "y"}
    exceptions = {"known_defects": [{"keys": ["text.*"], "legs": ["linux-arm64"], "why": "phase 4"}]}
    failures = run(tmp_path, items, {"text.canvas.H": "x"}, exceptions)
    assert any(f.startswith("host  text.canvas.H") for f in failures)


def test_an_image_difference_is_told_in_bytes():
    img = lambda data: {"w": 2, "h": 1, "enc": "alpha8", "data": base64.b64encode(bytes(data)).decode()}
    assert gate.describe(img([0, 10]), img([0, 13])) == "image: 1 of 2 bytes differ, max delta 3"


def test_an_exception_without_a_reason_is_refused(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"noise": [{"keys": ["a.*"]}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        gate.load_exceptions(path)


def test_an_object_is_compared_field_by_field(tmp_path):
    # hardwareConcurrency is the identity's, appName is Firefox's: one wrong
    # field fails alone, and the identity's field does not drag the object down
    items = lambda leg, seed: {"surface.navigator": {"hardwareConcurrency": 4 + seed, "appName": "Gecko"}}
    failures = run(tmp_path, items, {"surface.navigator": {"hardwareConcurrency": 24, "appName": "Netscape"}})
    assert [f.split()[1] for f in failures] == ["surface.navigator.appName"] * (len(LEGS) * len(gate.DPRS))


def test_a_declared_identity_value_that_happens_to_repeat_is_not_held_to_retail(tmp_path):
    # 16 cores for every seed visited, 24 on the retail machine
    items = lambda leg, seed: {"surface.worker": {"hc": 16}}
    assert run(tmp_path / "plain", items, {"surface.worker": {"hc": 24}}) != []
    exceptions = {"identity": [{"keys": ["surface.worker.hc"], "why": "the declared core count"}]}
    assert run(tmp_path / "declared", items, {"surface.worker": {"hc": 24}}, exceptions) == []


def test_a_declared_identity_value_is_still_held_to_host_independence(tmp_path):
    items = lambda leg, seed: {"surface.worker": {"hc": 16 if leg.startswith("win") else 8}}
    exceptions = {"identity": [{"keys": ["surface.worker.hc"], "why": "the declared core count"}]}
    failures = run(tmp_path, items, {"surface.worker": {"hc": 16}}, exceptions)
    assert any(f.startswith("host  surface.worker.hc") for f in failures)


def test_an_unknown_kind_is_refused(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"whatever": [{"keys": ["a"], "why": "b"}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        gate.load_exceptions(path)


def test_diag_values_are_information_never_compared(tmp_path):
    # diag.* differs between the legs and between the seeds, and retail does not
    # record it: none of that is a failure
    items = lambda leg, seed: {
        "surface.gpu.limits": 8,
        "diag.webgl": {"webgl2": {"is_null": leg.startswith("win"),
                                  "creation_errors": ["AllowWebgl2:false"] if leg.startswith("win") else []},
                       "webgl1": {"SAMPLES": 0 if leg.startswith("win") else 4, "seed": seed}},
    }
    assert run(tmp_path, items, {"surface.gpu.limits": 8}) == []


def test_a_diag_value_is_not_the_cause_of_a_known_defect_being_seen(tmp_path):
    # an exception that only a diag. key would match is still reported as no longer seen
    items = lambda leg, seed: {"diag.webgl": {"webgl2": {"is_null": leg.startswith("win")}}}
    exceptions = {"known_defects": [{"keys": ["diag.*"], "why": "should never be needed"}]}
    failures = run(tmp_path, items, {}, exceptions)
    assert failures == ["known defect no longer seen, remove it from exceptions.json: "
                        "['diag.*'] (should never be needed)"]


def test_only_the_diag_prefix_is_skipped(tmp_path):
    items = lambda leg, seed: {"diagnostics.webgl": leg}
    failures = run(tmp_path, items, {"diagnostics.webgl": "win-x86_64"})
    assert any(f.startswith("host  diagnostics.webgl") for f in failures)


def test_a_value_missing_on_one_leg_still_fails(tmp_path):
    items = lambda leg, seed: ({"surface.gpu.limits": 8, "diag.webgl": 1} if leg.startswith("win")
                               else {"diag.webgl": 2})
    failures = run(tmp_path, items, {"surface.gpu.limits": 8})
    host = [f for f in failures if f.startswith("host  surface.gpu.limits")]
    assert len(host) == len(gate.SEEDS) * len(gate.DPRS)
    assert all('"<missing>" vs 8' in f for f in host)
    assert any(f.startswith("retail surface.gpu.limits") and "on linux-x86_64" in f for f in failures)


def test_a_visit_pins_its_dpr_in_the_persona_and_never_forces_the_pref():
    # Run 41325657085: the pref forced over whatever persona the seed drew made
    # a window smaller than its own viewport. The DPR is the persona's.
    for dpr in gate.DPRS:
        opts = gate.visit_options("firefox", 12, dpr, "UTC")
        assert opts["pin"] == {"screen.dpr": dpr}
        assert "layout.css.devPixelsPerPx" not in (opts.get("extra_prefs") or {})
        assert opts["seed"] == 12 and opts["binary_path"] == "firefox"


def test_the_dump_prints_the_diag_values_of_the_leg():
    first = {"surface.gpu.limits": 8,
             "diag.webgl": {"webgl2": {"is_null": True, "creation_errors": ["AllowWebgl2:false"]},
                            "webgl1": {"SAMPLES": 0}}}
    lines = gate.diag_report("win-x86_64 dpr 1.0 seed 11", first, None)
    assert lines == ["diag win-x86_64 dpr 1.0 seed 11:",
                     '  diag.webgl.webgl2.is_null = true',
                     '  diag.webgl.webgl2.creation_errors = ["AllowWebgl2:false"]',
                     '  diag.webgl.webgl1.SAMPLES = 0']
    seen = {k: v for k, v in gate.leaves(first).items() if gate.is_diag(k)}
    assert gate.diag_report("win-x86_64 dpr 1.0 seed 12", first, seen) == [
        "diag win-x86_64 dpr 1.0 seed 12: as the first visit"]
    later = {"diag.webgl": {"webgl2": {"is_null": True, "creation_errors": ["AllowWebgl2:false"]},
                            "webgl1": {}}}
    assert gate.diag_report("win-x86_64 dpr 1.5 seed 11", later, seen) == [
        "diag win-x86_64 dpr 1.5 seed 11: changed from the first visit:",
        "  diag.webgl.webgl1 = {}",
        "  diag.webgl.webgl1.SAMPLES = <missing>"]
    assert gate.diag_report("linux-x86_64 dpr 1.0 seed 11", {"surface.gpu.limits": 8}, None) == [
        "diag linux-x86_64 dpr 1.0 seed 11:", "  (the page recorded no diag. keys)"]


# -
# States and their invariants. Each invariant is shown failing on the defect it
# exists for and passing on retail's values.

def test_retail_states_break_no_invariant():
    assert broken() == set()


def test_the_invariant_table_is_whole():
    names = [inv.name for inv in gate.INVARIANTS]
    assert len(names) == len(set(names)) and names[0] == "measured"
    for inv in gate.INVARIANTS:
        assert inv.why.strip() and inv.states and set(inv.states) <= set(gate.STATES)


@pytest.mark.parametrize("invariant, override", [
    # the escaped defect: 1936x1048 outer under a 1920x1080 viewport, -8/-8, 85
    ("outer_holds_inner", {"fullscreen": BUG_FULLSCREEN}),
    ("fullscreen_is_the_screen", {"fullscreen": BUG_FULLSCREEN}),
    ("fullscreen_at_origin", {"fullscreen": BUG_FULLSCREEN}),
    ("fullscreen_at_origin", {"fullscreen": dict(FULLSCREEN, mozInnerScreenY=85, events={"click": [[0, 85]]})}),
    # every mouse event at 85 below the viewport the page is told sits at 0
    ("events_at_inner_screen", {"fullscreen": dict(FULLSCREEN, events={"mousemove": [[0, 85]], "click": [[0, 85]]})}),
    ("events_at_inner_screen", {"normal": dict(NORMAL, events={"click": [[0, 85]], "mousemove": [[0, 0]]})}),
    ("events_at_inner_screen", {"fullscreen": dict(FULLSCREEN, events={})}),
    # Xvfb without a window manager: display-mode false, the viewport stops at availHeight
    ("fullscreen_declared", {"fullscreen": XVFB_FULLSCREEN}),
    ("fullscreen_is_the_screen", {"fullscreen": XVFB_FULLSCREEN}),
    ("fullscreen_declared", {"fullscreen": dict(FULLSCREEN, fullscreen_element=False)}),
    # the return keeps the fullscreen geometry, or the defect's
    ("back_to_normal", {"after_fullscreen": dict(FULLSCREEN, display_mode_fullscreen=False, fullscreen_element=False)}),
    ("back_to_normal", {"after_fullscreen": dict(NORMAL, innerHeight=1080, transition=ENTERED)}),
    ("outer_holds_inner", {"after_fullscreen": dict(NORMAL, innerHeight=1080, transition=ENTERED)}),
    ("not_fullscreen", {"after_fullscreen": dict(FULLSCREEN)}),
    ("not_fullscreen", {"normal": dict(NORMAL, display_mode_fullscreen=True)}),
    ("screen_stays", {"fullscreen": dict(FULLSCREEN, screen=dict(SCREEN, height=1048))}),
    ("transition", {"fullscreen": dict(FULLSCREEN, transition={"how": "timeout", "request": "pending", "settled": True})}),
    ("transition", {"fullscreen": dict(FULLSCREEN, transition={"how": "fullscreenerror", "request": "ERR TypeError: x",
                                                               "settled": True})}),
    ("transition", {"after_fullscreen": dict(NORMAL, transition=dict(ENTERED, settled=False))}),
    ("transition", {"fullscreen": {k: v for k, v in FULLSCREEN.items() if k != "transition"}}),
    ("measured", {"fullscreen": {"error": "TimeoutError: page.mouse.click"}}),
    ("measured", {"fullscreen": {k: v for k, v in FULLSCREEN.items() if k != "mozInnerScreenY"}}),
    ("measured", {"normal": dict(NORMAL, mozInnerScreenX=None)}),
])
def test_an_invariant_fails_on_its_defect(invariant, override):
    assert invariant in broken(**override)


def test_the_escaped_defect_breaks_every_relation_it_breaks():
    assert broken(fullscreen=BUG_FULLSCREEN) == {"outer_holds_inner", "fullscreen_is_the_screen", "fullscreen_at_origin"}


def test_a_state_the_dump_does_not_have_fails():
    states = gate.visit_states(retail_states())
    del states["after_fullscreen"]
    failed = [(inv.name, state) for inv, state, message in gate.check_invariants(states)]
    assert failed == [("measured", "after_fullscreen")]


@pytest.mark.parametrize("inner_screen, offset, ok", [
    (85, 85, True), (85, 86, False), (85, 84, False),          # whole: nothing to round, exact
    (85.33, 85, True), (85.33, 86, True), (85.33, 87, False),  # fractional: within 1 px
    (0, 0, True), (0, 85, False),
])
def test_an_event_offset_is_compared_after_the_rounding_the_page_sees(inner_screen, offset, ok):
    normal = dict(NORMAL, mozInnerScreenY=inner_screen, events={"click": [[0, offset]]})
    assert ("events_at_inner_screen" not in broken(normal=normal, after_fullscreen=dict(normal, transition=ENTERED))) is ok


def test_the_xvfb_leg_fails_its_invariants_in_compare(tmp_path):
    items = lambda leg, seed: retail_states(fullscreen=XVFB_FULLSCREEN) if leg.startswith("linux") else {}
    failures = run(tmp_path, items, {})
    inv = [f for f in failures if f.startswith("invariant ")]
    assert inv and all(" linux-x86_64 " in f for f in inv)
    for name in ("fullscreen_declared", "fullscreen_is_the_screen"):
        hits = [f for f in inv if f.startswith("invariant %s " % name)]
        assert len(hits) == len(gate.DPRS) * len(gate.SEEDS)
        assert all("state.fullscreen" in f and "(why: " in f for f in hits)


def test_the_invariants_run_without_a_reference_and_no_exception_hides_them(tmp_path):
    items = lambda leg, seed: retail_states(fullscreen=BUG_FULLSCREEN)
    exceptions = {"identity": [{"keys": ["state.*"], "why": "x"}], "noise": [{"keys": ["state.*"], "why": "x"}],
                  "known_defects": [{"keys": ["state.*"], "why": "x"}]}
    failures = run(tmp_path, items, {}, exceptions)
    assert any(f.startswith("invariant fullscreen_at_origin win-x86_64 dpr 1.0 seed 11 state.fullscreen: "
                            "screenX -8, screenY -8, mozInnerScreenY 85") for f in failures)
    assert len([f for f in failures if f.startswith("invariant ")]) == 3 * len(LEGS) * len(gate.DPRS) * len(gate.SEEDS)


def test_a_reference_without_states_does_not_fail_them(tmp_path):
    # nothing the states answer is compared with a retail that recorded none; a note says so
    items = lambda leg, seed: {"surface.gpu.limits": 8}
    notes = []
    assert run(tmp_path, items, {"surface.gpu.limits": 8}, notes=notes) == []
    assert len(notes) == len(gate.DPRS) and all("no state.*" in n for n in notes)


def test_a_reference_with_states_holds_them_to_retail(tmp_path):
    exceptions = {"identity": gate.load_exceptions()["identity"]}  # the declarations the gate runs with
    retail = dict(retail_states(normal=dict(NORMAL, innerWidth=1280, outerWidth=1304, screenX=4)),
                  **{"surface.gpu.limits": 8})
    items = lambda leg, seed: dict(retail_states(fullscreen=dict(FULLSCREEN, window_fullScreen=False)),
                                   **{"surface.gpu.limits": 8})
    notes = []
    failures = run(tmp_path, items, retail, exceptions, notes)
    assert notes == []
    # the identity's window geometry is not retail's and passes; Firefox's own answer is held to retail
    assert sorted({f.split()[1] for f in failures}) == ["state.fullscreen.window_fullScreen"]


def test_a_state_value_is_held_to_host_independence(tmp_path):
    items = lambda leg, seed: retail_states(after_fullscreen=dict(NORMAL, transition=ENTERED,
                                                                  window_fullScreen=leg.startswith("linux")))
    failures = run(tmp_path, items, {})
    assert any(f.startswith("host  state.after_fullscreen.window_fullScreen") for f in failures)


class FakePage:
    """The page's __hostGateStates as drive_states drives it: the window goes
    fullscreen only when the button is clicked after arm()."""

    def __init__(self, raise_on=None):
        self.raise_on = raise_on
        self.calls, self.full, self.armed = [], False, False

    def geometry(self):
        state = FULLSCREEN if self.full else NORMAL
        return json.loads(json.dumps({k: v for k, v in state.items() if k != "transition"}))

    def evaluate(self, expr):
        self.calls.append(expr)
        if self.raise_on and self.raise_on in expr:
            raise RuntimeError("Target closed")
        if expr == "__hostGateStates.prepare()":
            return {"x": 100, "y": 40}
        if expr == "[innerWidth, innerHeight]":
            g = self.geometry()
            return [g["innerWidth"], g["innerHeight"]]
        if expr.startswith("__hostGateStates.arm("):
            self.armed = True
        elif expr.startswith("__hostGateStates.leave("):
            self.full = False
        elif expr == "__hostGateStates.wait()":
            return dict(ENTERED)
        elif expr == "__hostGateStates.measure()":
            return self.geometry()
        else:
            assert expr == "__hostGateStates.clear()", expr

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def click(self, x, y):
        self.calls.append(("click", x, y))
        if (x, y) == (100, 40) and self.armed:
            self.full = True


def test_drive_states_reaches_the_three_states_with_real_input():
    page = FakePage()
    items = gate.drive_states(page.evaluate, page.move, page.click)
    assert list(items) == ["state.normal", "state.fullscreen", "state.after_fullscreen"]
    assert gate.check_invariants(gate.visit_states(items)) == []
    # the fullscreen click is on the button, after arm()
    assert page.calls.index(("click", 100, 40)) > page.calls.index("__hostGateStates.arm(%d)" % gate.STATE_TIMEOUT_MS)
    # each state is probed by real moves and a click in the middle of its own viewport
    assert page.calls.count(("click", 960, 955 // 2 + 20)) == 2      # normal, and after
    assert page.calls.count(("click", 960, 1080 // 2 + 20)) == 1     # fullscreen
    assert ("move", 940, 1080 // 2) in page.calls


def test_drive_states_records_a_failure_instead_of_raising():
    page = FakePage(raise_on="wait()")
    items = gate.drive_states(page.evaluate, page.move, page.click)
    assert items["state.fullscreen"] == items["state.after_fullscreen"] == {"error": "RuntimeError: Target closed"}
    failed = {(inv.name, state) for inv, state, message in gate.check_invariants(gate.visit_states(items))}
    assert failed == {("measured", "fullscreen"), ("measured", "after_fullscreen")}


def test_every_page_call_the_driver_makes_exists_in_the_page():
    source = Path(gate.__file__).read_text(encoding="utf-8")
    api = gate.PAGE.read_text(encoding="utf-8").split("window.__hostGateStates = ")[1]
    used = set(re.findall(r"__hostGateStates\.(\w+)\(", source))
    assert used == {"prepare", "arm", "wait", "leave", "clear", "measure"}
    for name in used:
        assert re.search(r"\b%s\b\s*[(,]" % name, api), name
