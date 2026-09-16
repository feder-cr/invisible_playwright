"""The injected bundle must not live on the page's window.

⛔ WHY THIS IS A CLASS AND NOT A LIST OF NAMES. Three separate tells were found
in this bundle one at a time, each fixed on its own: two CustomEvents dispatched
before every action (removed), an enumerable `window.builtins` (turned off), and
a `__playwright_mark_target__` rename. A fourth survived all three passes - a
CustomEvent the constructor dispatched on the page's window to find out whether
its listeners had been wiped - because each fix knew about its own symbol and
nothing knew about the shape they shared.

So this gate never looks for a name. It RUNS the bundle against a window and a
document that record every access, and asserts three properties:

1. constructing the InjectedScript writes nothing to the page's window;
2. whatever an action adds, the same action's stop removes;
3. no single action registers every interceptor event, only its own.

A rename cannot get past any of them, and neither can a new symbol nobody has
read yet.

The measurement lives in `tests/gates/injected_page_surface.js`, which also
documents what the harness can and cannot model.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

GATE = pathlib.Path(__file__).parent / "gates" / "injected_page_surface.js"
BUNDLE = (pathlib.Path(__file__).parent.parent / "src" / "invisible_playwright"
          / "_juggler" / "injected.js")

#: ⛔ Node is REQUIRED, not optional. Skipping when it is absent would turn the
#: one gate that can see this class into a line of output nobody reads: the
#: bundle is JavaScript, so a scanner would have to imitate a parser, and this
#: project already measured what that costs. Every GitHub runner ships node.
NODE = shutil.which("node")


def run_gate(bundle: pathlib.Path) -> dict:
    assert NODE, ("node is not on PATH, and this gate cannot run without it. "
                  "It is not optional: see the note in this module.")
    done = subprocess.run([NODE, str(GATE), str(bundle)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "the gate did not run: %s\n%s" % (done.returncode, done.stderr[-3000:]))
    report = json.loads(done.stdout)
    return {p["name"]: p for p in report["phases"]}


@pytest.fixture(scope="module")
def shipped() -> dict:
    return run_gate(BUNDLE)


def test_every_phase_of_the_harness_actually_ran(shipped):
    """⛔ A phase that raised measures nothing, and its empty touch list would
    read exactly like a clean one. This has to fail loudly, or the three
    assertions below become true for the wrong reason."""
    broken = {n: p["error"] for n, p in shipped.items() if p["error"]}
    assert not broken, "phases raised inside the harness: %s" % broken


def test_constructing_the_injected_script_writes_nothing_to_the_page(shipped):
    """Property 1. It used to register THIRTEEN capture listeners here and
    observe the document, for the whole life of the page."""
    phase = shipped["construction"]
    assert phase["mutating"] == 0, (
        "constructing the InjectedScript touched the page %d time(s): %s"
        % (phase["mutating"], phase["counts"]))


def test_the_page_is_not_touched_when_it_replaces_its_own_documentElement(shipped):
    """Property 1, at the moment that used to expose it.

    Replacing `documentElement` was the condition the removed detector waited
    for, and the only moment its CustomEvent actually fired. A site could arm
    that in three lines."""
    phase = shipped["page_replaces_documentElement"]
    assert phase["dispatched"] == [], (
        "the bundle dispatched %s on the page when it rewrote itself"
        % phase["dispatched"])
    assert phase["mutating"] == 0, (
        "the bundle touched the page %d time(s) when it rewrote itself: %s"
        % (phase["mutating"], phase["counts"]))


@pytest.mark.parametrize("kind", ["hover", "tap", "mouse"])
def test_an_action_removes_exactly_the_listeners_it_added(shipped, kind):
    """Property 2. The interceptor owns its listeners, so the page carries
    them only while the action is in flight."""
    armed = shipped["arm:" + kind]
    stopped = shipped["stop:" + kind]
    assert armed["added"], (
        "arming %r registered nothing: the interceptor would be inert, and "
        "the symmetry below would hold for the wrong reason" % kind)
    assert armed["added"] == stopped["removed"], (
        "%r added %s and removed %s" % (kind, armed["added"], stopped["removed"]))
    assert armed["removed"] == [] and stopped["added"] == [], (
        "%r added and removed in the wrong phases" % kind)


def test_no_action_registers_every_interceptor_event(shipped):
    """Property 3, written so it duplicates nothing.

    ⛔ The three event sets live in the bundle. Repeating them here would make
    this file a second source for the same fact, and the two would drift. The
    property that does not need them: the union of what the three actions
    register must be larger than what any ONE of them registers. That is false
    the moment somebody goes back to registering all of them every time.
    """
    added = {k: set(shipped["arm:" + k]["added"]) for k in ("hover", "tap", "mouse")}
    union = set().union(*added.values())
    for kind, events in added.items():
        assert events != union, (
            "%r registers the whole union of interceptor events (%d types); "
            "an action must register only the ones that can interrupt it"
            % (kind, len(union)))


# ── the known-bad inputs ────────────────────────────────────────────────────
#
# ⛔ Each mutation is SPLICED OUT OF THE FILE'S OWN BYTES, never retyped. The
# bundle is entirely CRLF in the working tree, and a hand-written multi-line
# target silently fails to match: the mutation is then never applied, the gate
# passes, and the report says "this gate does not see this defect" - which is
# the worst thing a gate can say about itself, for a reason that lives in the
# bench.

EOL = b"\r\n"


def mutate(tmp_path: pathlib.Path, edit) -> pathlib.Path:
    lines = BUNDLE.read_bytes().split(EOL)
    edit(lines)
    out = tmp_path / "injected.js"
    out.write_bytes(EOL.join(lines))
    assert out.read_bytes() != BUNDLE.read_bytes(), "the mutation changed nothing"
    return out


def at(lines: list, needle: bytes) -> int:
    hits = [i for i, l in enumerate(lines) if l == needle]
    assert len(hits) == 1, "anchor %r matched %d lines" % (needle, len(hits))
    return hits[0]


CONSTRUCTOR_ANCHOR = b"    this._isUtilityWorld = !!options.isUtilityWorld;"


def test_the_gate_catches_a_listener_planted_back_in_the_constructor(tmp_path):
    def edit(lines):
        i = at(lines, CONSTRUCTOR_ANCHOR)
        lines.insert(i + 1, b'    this.window.addEventListener("pagehide", () => {});')

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["construction"]["mutating"] == 1
    assert phases["construction"]["added"] == ["pagehide"]


def test_the_gate_catches_a_probe_dispatched_on_the_page(tmp_path):
    """The removed mechanism, put back under a different name. The gate must
    not care what it is called."""
    def edit(lines):
        i = at(lines, CONSTRUCTOR_ANCHOR)
        lines.insert(i + 1, b'    new MutationObserver(() => { this.window'
                            b'.dispatchEvent(new CustomEvent("__whatever__")); })'
                            b'.observe(this.document, { childList: true });')

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["page_replaces_documentElement"]["dispatched"] == ["__whatever__"]


def test_the_gate_catches_a_stop_that_leaves_the_listeners_behind(tmp_path):
    def edit(lines):
        i = at(lines, b"        for (const event of events)")
        assert lines[i + 1] == (b"          this.window.removeEventListener"
                                b"(event, listener, { capture: true });")
        lines[i:i + 2] = [b"        // mutation: the listeners stay"]

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["stop:mouse"]["removed"] == []


def test_the_gate_catches_an_action_that_registers_every_event(tmp_path):
    """⛔ This mutation keeps the symmetry INTACT on purpose: it adds the union
    and removes the union. Only the minimality property can see it, which is
    what makes it the known-bad input for that property rather than a second
    test of the previous one."""
    union = (b"[...this._hoverHitTargetInterceptorEvents, "
             b"...this._tapHitTargetInterceptorEvents, "
             b"...this._mouseHitTargetInterceptorEvents]")

    def edit(lines):
        add = at(lines, b"    for (const event of events)")
        lines[add] = b"    for (const event of " + union + b")"
        rm = at(lines, b"        for (const event of events)")
        lines[rm] = b"        for (const event of " + union + b")"

    phases = run_gate(mutate(tmp_path, edit))
    added = {k: set(phases["arm:" + k]["added"]) for k in ("hover", "tap", "mouse")}
    union_seen = set().union(*added.values())
    assert added["hover"] == union_seen, (
        "the mutation did not reach the code: the arms still differ")
    # and the symmetry it deliberately preserves is still green, which is the
    # whole reason this known-bad input exists
    for kind in ("hover", "tap", "mouse"):
        assert phases["arm:" + kind]["added"] == phases["stop:" + kind]["removed"]
