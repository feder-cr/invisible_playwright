"""The injected bundle must not touch the page's window. Ever, in any phase.

⛔ WHY THIS IS A CLASS AND NOT A LIST OF NAMES. Four tells were found in this
bundle one at a time, each fixed on its own: two CustomEvents dispatched before
every action (removed), an enumerable `window.builtins` (turned off), a
`__playwright_mark_target__` rename, and a `__ctx_ping__` the constructor
dispatched on the page's window to learn whether its listeners had been wiped.
The fourth survived all three earlier passes, because each fix knew about its
own symbol and nothing knew about the shape they shared.

So this gate never looks for a name. It RUNS the bundle against a window and a
document that record every access, and asserts ONE property:

    constructing the script, checking a hit target, and having the page rewrite
    itself all touch the page's window zero times.

⛔ THIS USED TO BE THREE PROPERTIES, and the collapse is the news. While the
hit-target check was an INTERCEPTOR it had to put capture listeners on the
page's window for the duration of each action, so the best that could be asked
was that it removed exactly what it added and added no more than it needed. The
check is a pure DOM read now, so there is nothing to balance: the number is
zero everywhere, which is a statement two symmetry assertions could never make.

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

PHASES = ("construction", "check_hit_target", "page_replaces_documentElement")


def run_gate(bundle: pathlib.Path) -> dict:
    assert NODE, ("node is not on PATH, and this gate cannot run without it. "
                  "It is not optional: see the note in this module.")
    done = subprocess.run([NODE, str(GATE), str(bundle)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, (
        "the gate did not run: %s\n%s" % (done.returncode, done.stderr[-3000:]))
    return {p["name"]: p for p in json.loads(done.stdout)["phases"]}


@pytest.fixture(scope="module")
def shipped() -> dict:
    return run_gate(BUNDLE)


def test_the_harness_exercised_every_phase_it_claims_to(shipped):
    """⛔ A phase that raised measures nothing, and its empty touch list reads
    exactly like a clean one. This has to fail loudly, or the assertions below
    become true for the wrong reason.

    It earned its place: enriching the fake DOM for the hit-test phase was
    needed precisely because this caught the phase raising instead of letting
    it report a spotless zero.
    """
    assert set(shipped) == set(PHASES), (
        "the gate reported %s, expected %s" % (sorted(shipped), sorted(PHASES)))
    broken = {n: p["error"] for n, p in shipped.items() if p["error"]}
    assert not broken, "phases raised inside the harness: %s" % broken


@pytest.mark.parametrize("phase", PHASES)
def test_the_bundle_does_not_touch_the_page(shipped, phase):
    """The property, for every phase there is."""
    seen = shipped[phase]
    assert seen["mutating"] == 0, (
        "%s touched the page %d time(s): %s"
        % (phase, seen["mutating"], seen["counts"]))
    assert seen["added"] == [] and seen["removed"] == [], (
        "%s left listeners on the page: added=%s removed=%s"
        % (phase, seen["added"], seen["removed"]))
    assert seen["dispatched"] == [], (
        "%s dispatched %s on the page" % (phase, seen["dispatched"]))


def test_the_hit_target_check_still_answers(shipped):
    """⛔ Zero touches is trivially true for a function that does nothing.

    The check has to keep working, or this gate would go green precisely when
    the hit test was broken - which is the failure mode of every assertion that
    only counts absences. The harness raises if the verdict is not a string, and
    the phase above proves it did not raise.
    """
    assert shipped["check_hit_target"]["error"] is None


# ── the known-bad inputs ────────────────────────────────────────────────────
#
# ⛔ Each mutation is SPLICED OUT OF THE FILE'S OWN BYTES, never retyped. A
# hand-written multi-line target silently fails to match: the mutation is then
# never applied, the gate passes, and the report says "this gate does not see
# this defect" - the worst thing a gate can say about itself, for a reason that
# lives in the bench rather than in the code it judges.


def bundle_eol(data: bytes) -> bytes:
    """The bundle's own line ending, because it is not the same everywhere.

    ⛔ THIS WAS HARDCODED TO CRLF AND THE LINUX RUNNERS CAUGHT IT on the first
    push. Windows checks the bundle out with CRLF under core.autocrlf and Linux
    with LF, so splitting on the wrong one yields a single giant line and every
    anchor matches nothing. The comment above was right about the class and
    wrong about the constant: a line ending belongs to the CHECKOUT, not to the
    file, so it gets read rather than assumed.

    It failed loudly only because `at()` asserts its anchor is unique. A plain
    replace would have applied nothing and left four mutation tests reporting
    that the gate is blind.
    """
    return b"\r\n" if data.count(b"\r\n") else b"\n"


def mutate(tmp_path: pathlib.Path, edit) -> pathlib.Path:
    data = BUNDLE.read_bytes()
    eol = bundle_eol(data)
    lines = data.split(eol)
    assert len(lines) > 100, (
        "the bundle split into %d line(s): the line ending is wrong" % len(lines))
    edit(lines)
    out = tmp_path / "injected.js"
    out.write_bytes(eol.join(lines))
    assert out.read_bytes() != data, "the mutation changed nothing"
    return out


def at(lines: list, needle: bytes) -> int:
    hits = [i for i, line in enumerate(lines) if line == needle]
    assert len(hits) == 1, "anchor %r matched %d lines" % (needle, len(hits))
    return hits[0]


CONSTRUCTOR_ANCHOR = b"    this._isUtilityWorld = !!options.isUtilityWorld;"
CHECK_ANCHOR = b"  checkHitTarget(node, hitPoint) {"


def test_a_mutation_lands_whatever_the_checkout_did_to_the_line_endings():
    """⛔ THE KNOWN-BAD INPUT OF THE BENCH ITSELF, and it comes from a real red.

    The mutations were split on a hardcoded CRLF, which is what Windows checks
    out and not what Linux does. Every anchor then matched nothing, and four
    mutation tests failed on the Linux runners only while Windows stayed green -
    a bench that accuses a healthy gate on half the machines.

    Both spellings are exercised here rather than whichever one this machine
    happens to produce, because a bench that only ever sees its own platform is
    how this got shipped in the first place.
    """
    flat = BUNDLE.read_bytes().replace(b"\r\n", b"\n")
    for eol in (b"\r\n", b"\n"):
        body = eol.join(flat.split(b"\n"))
        assert bundle_eol(body) == eol, (
            "the line ending of a %r checkout was read as %r"
            % (eol, bundle_eol(body)))
        lines = body.split(bundle_eol(body))
        assert len(lines) > 100
        at(lines, CONSTRUCTOR_ANCHOR)
        at(lines, CHECK_ANCHOR)


def test_the_gate_catches_a_listener_planted_in_the_constructor(tmp_path):
    def edit(lines):
        i = at(lines, CONSTRUCTOR_ANCHOR)
        lines.insert(i + 1, b'    this.window.addEventListener("pagehide", () => {});')

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["construction"]["added"] == ["pagehide"]
    assert phases["construction"]["mutating"] == 1


def test_the_gate_catches_a_listener_planted_in_the_hit_target_check(tmp_path):
    """⛔ THE KNOWN-BAD INPUT OF THE NEW PROPERTY. The check is where the
    interceptor used to live, so it is the place a future change would most
    naturally put a listener back."""
    def edit(lines):
        i = at(lines, CHECK_ANCHOR)
        lines.insert(i + 1, b'    this.window.addEventListener("mousedown", () => {},'
                            b' { capture: true });')

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["check_hit_target"]["added"] == ["mousedown"]
    assert phases["construction"]["added"] == [], (
        "the mutation leaked into the wrong phase, so it proves nothing")


def test_the_gate_catches_a_probe_dispatched_on_the_page(tmp_path):
    """The removed detector, put back under a different name. The gate must not
    care what it is called."""
    def edit(lines):
        i = at(lines, CONSTRUCTOR_ANCHOR)
        lines.insert(i + 1, b'    new MutationObserver(() => { this.window'
                            b'.dispatchEvent(new CustomEvent("__whatever__")); })'
                            b'.observe(this.document, { childList: true });')

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["page_replaces_documentElement"]["dispatched"] == ["__whatever__"]


def test_the_gate_catches_a_hit_test_that_stopped_answering(tmp_path):
    """The other half of the previous test: a check that returns nothing would
    make every count zero for the wrong reason."""
    def edit(lines):
        i = at(lines, CHECK_ANCHOR)
        lines.insert(i + 1, b"    return undefined;")

    phases = run_gate(mutate(tmp_path, edit))
    assert phases["check_hit_target"]["error"], (
        "a check that answers nothing went unnoticed")
