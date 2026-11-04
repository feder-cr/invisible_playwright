#!/usr/bin/env python3
"""CI host gate: what a page reads is a function of the declared identity and of
Firefox's code, never of the machine the browser runs on.

Two properties follow, and they are checked for every value the page dumps
(whole APIs, not a hand-picked list), so the gate needs no knowledge of where
the next leak will be:

  1. HOST-INDEPENDENCE. The same identity on different hosts (the Windows and
     Linux x86_64 legs) gives the same bytes.
  2. RETAIL EQUALITY. A value that does not depend on the identity equals what
     retail Firefox for Windows answers on the same page, at the same device
     pixel ratio.

Values are compared leaf by leaf: an object the page reads (navigator, screen)
mixes fields the identity decides with fields it does not, and each is held to
its own property.

Which values depend on the identity is measured AND declared. Each leg visits
the page with several seeds at a fixed DPR; a value that changes with the seed
depends on the identity and is held to property 1 only. A value the identity
decides can still come out the same for every seed (16 cores, a 1920x1080
screen), so those are declared as well.

The only exceptions live in host_gate/exceptions.json, each with its reason:
  - identity: values the declared identity decides; exempt from property 2,
    never from property 1;
  - noise: values that differ between two launches of retail itself;
  - network: values the identity takes from the network (language and zone
    from the egress IP), which CI without a proxy cannot reproduce;
  - measurement: values that record how the reference was taken rather than
    what Firefox answers (focus, on the hidden desktop retail ran on);
  - known_defects: open divergences with the work that closes them. This list
    only shrinks: an entry that no longer matches a difference fails the gate,
    so it is removed when its fix lands.

Values under the diag. prefix are not held to either property. They are
information the page records about the host (diag.webgl: whether WebGL2 and
multisampling exist there and, when not, the message Firefox gives): the legs
legitimately differ on them and the retail reference does not record them.
compare() skips them by name; dump() prints them into the job log, so a run
tells why without its artifacts being downloaded.

Values under the state. prefix are the window in the states a page can drive
it to: state.normal, state.fullscreen (entered through a real click, a user
gesture) and state.after_fullscreen. The dump measures them after the page is
done (drive_states). Besides the two properties they are held to INVARIANTS:
relations between values that hold on every host, for every identity and
whatever the reference says, because a window that breaks them cannot exist
(an outer window smaller than its viewport, a fullscreen window off the
screen's origin). Each value can look plausible on its own; only the relation
shows the defect, so these are checked even where nothing else is.

This replaces ci_font_gate.py, which checked one surface (the font list) of
the same rule.

    python ci_host_gate.py dump <firefox> --leg linux-x86_64 --out dumps/
    python ci_host_gate.py compare dumps/          (reference: host_gate/reference/firefox-159)
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import gzip
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, NamedTuple

HERE = Path(__file__).resolve().parent / "host_gate"
PAGE = HERE / "host_gate_page.html"
EXCEPTIONS = HERE / "exceptions.json"
REFERENCE = HERE / "reference" / "firefox-159"
FORMAT = "host-gate/1"

#: DPRs the reference is recorded at, and the seeds visited at each. Three seeds
#: so that a value that depends on the identity is very unlikely to look
#: constant by chance.
DPRS = (1.0, 1.5)
SEEDS = (11, 12, 13)

#: The leg every other leg is compared with: the platform the identity claims.
ANCHOR = "win-x86_64"

#: Keys under this prefix are information, never compared (see the module docstring).
DIAG = "diag."


def is_diag(key: str) -> bool:
    return key.startswith(DIAG)


def diag_lines(items: dict, before: dict | None = None) -> list:
    """The diag. leaves of one visit as 'key = value' lines; with before (an
    earlier visit's diag leaves), only the keys whose value changed."""
    now = {k: v for k, v in leaves(items).items() if is_diag(k)}
    lines = ["  %s = %s" % (k, json.dumps(v, sort_keys=True)) for k, v in now.items()
             if before is None or before.get(k, "<missing>") != v]
    return lines + ["  %s = <missing>" % k for k in (before or {}) if k not in now]


def diag_report(visit: str, items: dict, first: dict | None) -> list:
    """What dump() prints for one visit: every diag. value on the leg's first
    visit (first is None), then only what changed from it."""
    if first is None:
        return ["diag %s:" % visit] + (diag_lines(items) or ["  (the page recorded no diag. keys)"])
    changed = diag_lines(items, first)
    return ["diag %s: %s" % (visit, "changed from the first visit:" if changed else "as the first visit")] + changed


# -
# States: the window driven to fullscreen and back with real input, and the
# relations its values must keep in every state.

STATE = "state."

#: The states drive_states measures, in the order it reaches them.
STATES = ("normal", "fullscreen", "after_fullscreen")

#: How long the page waits for fullscreenchange before recording 'timeout'.
STATE_TIMEOUT_MS = 5000

#: Retail equality for these namespaces waits for the reference to record them.
#: The firefox-159 reference was taken before the dump drove any state, so it has
#: no state.* key; until it has, a state.* value is held to host-independence and
#: to INVARIANTS only, and compare() notes it. The session that produces the
#: reference adds, to the items of each dpr<x>.json.gz, the three items
#: state.normal, state.fullscreen and state.after_fullscreen, shaped exactly as
#: drive_states returns them, by running drive_states on retail Firefox for
#: Windows on this page with evaluate = run the expression in the page, and move
#: and click = OS-level input at those viewport (CSS px) points, e.g. SendInput:
#: a click a script dispatches is not a user gesture and requestFullscreen
#: refuses it. From the first reference that has state.*, these values are
#: compared with retail like every other; the window geometry is declared
#: identity in exceptions.json, the rest (display-mode, fullscreenElement,
#: window.fullScreen, how the transition went) is held to retail.
REFERENCE_PENDING = (STATE,)


def drive_states(evaluate: Callable, move: Callable, click: Callable,
                 timeout_ms: int = STATE_TIMEOUT_MS) -> dict:
    """The page's window in each of STATES, as items {state.<name>: values}.

    evaluate runs an expression in the page and returns its (awaited) value;
    move and click are real input at a viewport point. In each state the mouse
    moves twice and clicks on the middle of the viewport, so every value of the
    state comes with the screen - client offsets of real mouse events.
    Fullscreen is entered by clicking the page's button (its handler calls
    requestFullscreen) and left with document.exitFullscreen(); each transition
    records how it ended (fullscreenchange, fullscreenerror, timeout). Whatever
    raises leaves {"error": ...} in the states not measured yet, which the
    'measured' invariant reports.
    """
    states = {}

    def measure(name, transition=None):
        evaluate("__hostGateStates.clear()")
        w, h = evaluate("[innerWidth, innerHeight]")
        x, y = w // 2, h // 2
        move(x - 20, y)
        move(x, y + 20)
        click(x, y + 20)
        values = evaluate("__hostGateStates.measure()")
        if transition is not None:
            values["transition"] = transition
        states[name] = values

    try:
        button = evaluate("__hostGateStates.prepare()")
        measure("normal")
        evaluate("__hostGateStates.arm(%d)" % timeout_ms)
        click(button["x"], button["y"])
        measure("fullscreen", evaluate("__hostGateStates.wait()"))
        evaluate("__hostGateStates.leave(%d)" % timeout_ms)
        measure("after_fullscreen", evaluate("__hostGateStates.wait()"))
    except Exception as e:  # noqa: BLE001 - recorded, and failed by the 'measured' invariant
        for name in STATES:
            states.setdefault(name, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])})
    return {STATE + name: states[name] for name in STATES}


#: What a state is made of; the screen is {width, height, availWidth, availHeight}.
POSITION = ("innerWidth", "innerHeight", "outerWidth", "outerHeight", "screenX", "screenY",
            "mozInnerScreenX", "mozInnerScreenY", "screen")
FIELDS = POSITION + ("display_mode_fullscreen", "fullscreen_element", "events")


def _measured(s, states):
    if "error" in s:
        return "not measured: %s" % s["error"]
    missing = [f for f in FIELDS if f not in s]
    missing += ["%s as a number" % f for f in POSITION[:-1]
                if f in s and (isinstance(s[f], bool) or not isinstance(s[f], (int, float)))]
    if not isinstance(s.get("screen"), dict) or not {"width", "height", "availWidth", "availHeight"} <= set(s["screen"]):
        missing.append("screen.width/height/availWidth/availHeight")
    if not isinstance(s.get("events"), dict):
        missing.append("events as {type: [[dx, dy]]}")
    return missing and "not measured: no %s" % ", ".join(missing)


def _transition(s, states):
    t = s.get("transition")
    if not isinstance(t, dict) or t.get("how") != "fullscreenchange" or not t.get("settled"):
        return "the transition did not complete: %s" % json.dumps(t, sort_keys=True)


def _outer_holds_inner(s, states):
    bad = ["outer%s %s < inner%s %s" % (d, s["outer" + d], d, s["inner" + d])
           for d in ("Width", "Height") if s["outer" + d] < s["inner" + d]]
    return "; ".join(bad)


def _is_the_screen(s, states):
    w, h = s["screen"]["width"], s["screen"]["height"]
    bad = ["%s %s" % (k, s[k]) for k, want in (("innerWidth", w), ("outerWidth", w),
                                               ("innerHeight", h), ("outerHeight", h)) if s[k] != want]
    return bad and "%s, screen %sx%s (avail %sx%s)" % (", ".join(bad), w, h,
                                                      s["screen"]["availWidth"], s["screen"]["availHeight"])


def _at_origin(s, states):
    bad = ["%s %s" % (k, s[k]) for k in ("screenX", "screenY", "mozInnerScreenX", "mozInnerScreenY") if s[k] != 0]
    return ", ".join(bad)


def _declares(fullscreen):
    def check(s, states):
        bad = ["%s %s" % (k, json.dumps(s[k])) for k in ("display_mode_fullscreen", "fullscreen_element")
               if s[k] is not fullscreen]
        return ", ".join(bad)
    return check


def _offset_matches(offset, inner_screen):
    # MouseEvent.screenX and clientX are each rounded to a whole CSS px, while
    # mozInnerScreenX is the viewport's exact offset, fractional when the DPR
    # does not divide the window's device position (85.33 at 1.5): the
    # difference of the two rounded values is then within 1 px of it. A whole
    # offset has nothing to round and must match exactly.
    if float(inner_screen).is_integer():
        return offset == inner_screen
    return abs(offset - inner_screen) <= 1


def _events_at_inner_screen(s, states):
    if not any(s["events"].values()):
        return "no mouse event was recorded"
    inner = (s["mozInnerScreenX"], s["mozInnerScreenY"])
    bad = ["%s (%s, %s)" % (typ, dx, dy) for typ, pairs in sorted(s["events"].items()) for dx, dy in pairs
           if not (_offset_matches(dx, inner[0]) and _offset_matches(dy, inner[1]))]
    return bad and "screen - client of %s, mozInnerScreen (%s, %s)" % (", ".join(bad), inner[0], inner[1])


def _screen_stays(s, states):
    normal = states.get("normal")
    if not isinstance(normal, dict) or not isinstance(normal.get("screen"), dict):
        return None  # the normal state's own 'measured' failure says why
    bad = ["screen.%s %s (normal %s)" % (k, s["screen"][k], normal["screen"].get(k))
           for k in ("width", "height") if s["screen"][k] != normal["screen"].get(k)]
    return ", ".join(bad)


def _back_to_normal(s, states):
    normal = states.get("normal")
    if not isinstance(normal, dict) or _measured(normal, states):
        return None  # the normal state's own 'measured' failure says why
    bad = ["%s %s (normal %s)" % (k, json.dumps(s[k]), json.dumps(normal[k])) for k in POSITION if s[k] != normal[k]]
    return ", ".join(bad)


class Invariant(NamedTuple):
    name: str
    states: tuple
    check: Callable   # (state values, every state's values) -> failure message, or a false value
    why: str


#: Every relation the states are held to, the only place they are declared.
#: 'measured' runs first: the others assume the state's fields are there.
INVARIANTS = (
    Invariant("measured", STATES, _measured,
              "a state the dump could not reach or read is a failure, never a silent pass"),
    Invariant("transition", ("fullscreen", "after_fullscreen"), _transition,
              "a real click is a user gesture, so retail enters and leaves fullscreen and the window then holds still; "
              "a timeout or fullscreenerror means the window did not do what a user's does"),
    Invariant("outer_holds_inner", STATES, _outer_holds_inner,
              "the outer window contains the viewport: a smaller outer window cannot exist "
              "(the defect it was written for: 1936x1048 outer under a 1920x1080 viewport in fullscreen)"),
    Invariant("fullscreen_is_the_screen", ("fullscreen",), _is_the_screen,
              "in fullscreen retail has no frame, toolbar or taskbar: viewport, window and screen are one rectangle "
              "(a viewport at availHeight is a window that never really went fullscreen)"),
    Invariant("fullscreen_at_origin", ("fullscreen",), _at_origin,
              "a fullscreen window sits at the screen's origin and its viewport at the window's: "
              "screenX/Y and mozInnerScreenX/Y are 0 (not a maximized window's -8/-8 and 85)"),
    Invariant("fullscreen_declared", ("fullscreen",), _declares(True),
              "the page entered fullscreen, so CSS (display-mode) and the DOM (fullscreenElement) say so"),
    Invariant("not_fullscreen", ("normal", "after_fullscreen"), _declares(False),
              "outside fullscreen neither CSS nor the DOM may claim it; it also keeps 'back_to_normal' "
              "from passing with both states stuck in fullscreen"),
    Invariant("events_at_inner_screen", STATES, _events_at_inner_screen,
              "an event's screen point is its client point plus where the viewport sits on the screen, "
              "which is mozInnerScreenX/Y: screen - client of every mouse event equals it"),
    Invariant("screen_stays", ("fullscreen", "after_fullscreen"), _screen_stays,
              "fullscreen changes the window, not the monitor: screen.width/height are the normal state's"),
    Invariant("back_to_normal", ("after_fullscreen",), _back_to_normal,
              "leaving fullscreen puts the window back as it was: every size and position is the normal state's"),
)


def check_invariants(states: dict) -> list:
    """Every broken invariant of one visit as (invariant, state, message).
    states maps each of STATES to its values (None when the dump has none)."""
    broken = []
    for state in STATES:
        s = states.get(state)
        if not isinstance(s, dict):
            s = {"error": "the dump has no %s%s" % (STATE, state)}
        for inv in INVARIANTS:
            if state not in inv.states:
                continue
            message = inv.check(s, states)
            if message:
                broken.append((inv, state, message))
                if inv.name == "measured":
                    break
    return broken


def visit_states(items: dict) -> dict:
    return {name: items.get(STATE + name) for name in STATES}


def invariant_lines(visit: str, items: dict) -> list:
    return ["invariant %s %s %s%s: %s (why: %s)" % (inv.name, visit, STATE, state, message, inv.why)
            for inv, state, message in check_invariants(visit_states(items))]


# -
# Dump: one leg, every (dpr, seed) visit, the way the reference was measured:
# the page over http://127.0.0.1 (a secure context), en-US, the zone the retail
# reference reported (retail on Windows ignores TZ and answers the machine's),
# the device pixel ratio pinned in the persona, no drawn cursor.

class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # the page also posts its result; the dump reads it from the page
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def reference_items(reference: Path, dpr: float) -> dict | None:
    """Retail's items at one DPR, from dpr<x>.json.gz (or a plain dpr<x>.json)."""
    for name in ("dpr%s.json.gz" % dpr, "dpr%s.json" % dpr):
        path = reference / name
        if path.is_file():
            raw = path.read_bytes()
            return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)["items"]
    return None


def reference_zone(reference: Path) -> str:
    return reference_items(reference, 1.0)["surface.intl"]["DateTimeFormat"]["timeZone"]


def visit_options(firefox: str, seed: int, dpr: float, zone: str) -> dict:
    """The launch of one (dpr, seed) visit. The DPR is PINNED in the persona
    (screen.dpr), so the core declares a screen, a work area and a window made
    at that scale. It used to be forced through
    extra_prefs={"layout.css.devPixelsPerPx": ...} on whatever persona the seed
    drew: a 100% persona at 1.5 read innerWidth 1920 inside an outerWidth of
    1291 (run 41325657085), an identity no machine has, which the engine now
    refuses to start with."""
    return {"seed": seed, "binary_path": firefox, "headless": True, "show_cursor": False,
            "locale": "en-US", "timezone": zone, "pin": {"screen.dpr": dpr}}


def dump(firefox: str, leg: str, out: Path, reference: Path, timeout_ms: int = 180_000) -> int:
    from invisible_playwright import InvisiblePlaywright

    out.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    zone = reference_zone(reference)
    first_diag = None
    try:
        for dpr in DPRS:
            for seed in SEEDS:
                with InvisiblePlaywright(**visit_options(firefox, seed, dpr, zone)) as browser:
                    page = browser.new_context().new_page()
                    page.goto("http://127.0.0.1:%d/?dpr%s" % (server.server_address[1], dpr))
                    page.wait_for_function("document.title === 'done'", timeout=timeout_ms)
                    result = page.evaluate("window.__hostGate")
                    if result.get("format") == FORMAT:
                        result["items"].update(drive_states(page.evaluate, page.mouse.move, page.mouse.click))
                if result.get("format") != FORMAT:
                    print("the page answered format %r, not %r" % (result.get("format"), FORMAT))
                    return 2
                path = out / ("%s_dpr%s_seed%d.json" % (leg, dpr, seed))
                path.write_text(json.dumps({"leg": leg, "dpr": dpr, "seed": seed, "result": result}),
                                encoding="utf-8")
                print("dumped %s: %d items" % (path.name, len(result["items"])))
                visit = "%s dpr %s seed %d" % (leg, dpr, seed)
                for line in diag_report(visit, result["items"], first_diag):
                    print(line)
                # told here too, so the job log says it without the compare step
                for line in invariant_lines(visit, result["items"]):
                    print(line)
                sys.stdout.flush()
                if first_diag is None:
                    first_diag = {k: v for k, v in leaves(result["items"]).items() if is_diag(k)}
    finally:
        server.shutdown()
    return 0


# -
# Compare: every leg, every visit, against each other and against retail.

KINDS = ("identity", "noise", "network", "measurement", "known_defects")


def load_exceptions(path: Path = EXCEPTIONS) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - set(KINDS)
    if unknown:
        raise SystemExit("exceptions.json: unknown kinds %s" % sorted(unknown))
    for kind in KINDS:
        for entry in data.get(kind, []):
            if not entry.get("keys") or not entry.get("why"):
                raise SystemExit("exceptions.json: every %s entry needs keys and why" % kind)
    return data


def matching(entries: list, key: str, leg: str | None = None) -> list:
    return [e for e in entries
            if any(fnmatch.fnmatchcase(key, p) for p in e["keys"])
            and (leg is None or not e.get("legs") or leg in e["legs"])]


def is_image(value) -> bool:
    return isinstance(value, dict) and "data" in value and "enc" in value


def leaves(items: dict) -> dict:
    """Every value as a leaf with a dotted key; an image stays one value."""
    out = {}

    def walk(key, value):
        if isinstance(value, dict) and value and not is_image(value):
            for k, v in value.items():
                walk("%s.%s" % (key, k), v)
        else:
            out[key] = value

    for key, value in items.items():
        walk(key, value)
    return out


def describe(a, b) -> str:
    """A short account of a difference; for images, how many bytes differ."""
    if isinstance(a, dict) and isinstance(b, dict) and "data" in a and "data" in b:
        x, y = base64.b64decode(a["data"]), base64.b64decode(b["data"])
        if len(x) != len(y) or (a.get("w"), a.get("h")) != (b.get("w"), b.get("h")):
            return "image %sx%s vs %sx%s" % (a.get("w"), a.get("h"), b.get("w"), b.get("h"))
        diff = [abs(p - q) for p, q in zip(x, y) if p != q]
        return "image: %d of %d bytes differ, max delta %d" % (len(diff), len(x), max(diff, default=0))
    text = lambda v: json.dumps(v, sort_keys=True)[:120]
    return "%s vs %s" % (text(a), text(b))


def compare(dumps: Path, reference: Path, exceptions: dict, notes: list | None = None) -> list:
    """Every failure as one line. Empty means the gate passes. notes, when
    given, receives what was not checked and why (REFERENCE_PENDING)."""
    visits = {}   # (leg, dpr, seed) -> items
    failures = []
    for path in sorted(dumps.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        items = d["result"]["items"]
        visits[(d["leg"], d["dpr"], d["seed"])] = {k: v for k, v in leaves(items).items() if not is_diag(k)}
        # the invariants: every leg, every visit, every state; no exception and no reference applies
        failures += invariant_lines("%s dpr %s seed %d" % (d["leg"], d["dpr"], d["seed"]), items)
    if not visits:
        return ["no dumps in %s" % dumps]
    legs = sorted({k[0] for k in visits})
    refs = {}
    pending = {}  # dpr -> the REFERENCE_PENDING namespaces the reference does not record yet
    for dpr in DPRS:
        items = reference_items(reference, dpr)
        if items is None:
            return ["no retail reference for dpr %s in %s" % (dpr, reference)]
        refs[dpr] = {k: v for k, v in leaves(items).items() if not is_diag(k)}
        pending[dpr] = tuple(p for p in REFERENCE_PENDING if not any(k.startswith(p) for k in refs[dpr]))
        for p in pending[dpr]:
            if notes is not None:
                notes.append("the retail reference has no %s* at dpr %s: those values are held to "
                             "host-independence and the invariants, not to retail" % (p, dpr))

    identity = exceptions.get("identity", [])
    excusable = exceptions.get("noise", []) + exceptions.get("network", []) + exceptions.get("measurement", [])
    known = exceptions.get("known_defects", [])
    used = set()

    def excused(key, leg):
        if matching(excusable, key):
            return True
        hit = matching(known, key, leg)
        for e in hit:
            used.add(id(e))
        return bool(hit)

    for dpr in DPRS:
        for leg in legs:
            missing = [s for s in SEEDS if (leg, dpr, s) not in visits]
            if missing:
                failures.append("%s dpr %s: no dump for seeds %s" % (leg, dpr, missing))
        present = [(leg, s) for leg in legs for s in SEEDS if (leg, dpr, s) in visits]
        keys = sorted(set().union(*(visits[(l, dpr, s)].keys() for l, s in present)) | set(refs[dpr]))
        for key in keys:
            # 1. host-independence: for each seed, every leg gives the Windows
            # leg's value. Windows is the anchor because it is the platform the
            # identity claims: a leg that differs from it is the one that moved.
            for seed in SEEDS:
                values = {leg: visits[(leg, dpr, seed)].get(key, "<missing>")
                          for leg in legs if (leg, dpr, seed) in visits}
                base = ANCHOR if ANCHOR in values else next(iter(values), None)
                for leg, value in values.items():
                    if leg != base and value != values[base] and not excused(key, leg):
                        failures.append("host  %s dpr %s seed %d: %s differs from %s: %s"
                                        % (key, dpr, seed, leg, base, describe(value, values[base])))
            # 2. retail equality, for a value that is the same for every seed
            if key.startswith(pending[dpr]):
                continue
            for leg in legs:
                per_seed = [visits[(leg, dpr, s)].get(key, "<missing>")
                            for s in SEEDS if (leg, dpr, s) in visits]
                if len({json.dumps(v, sort_keys=True) for v in per_seed}) != 1 or matching(identity, key):
                    continue  # depends on the identity: property 1 only
                retail = refs[dpr].get(key, "<missing>")
                if per_seed[0] != retail and not excused(key, leg):
                    failures.append("retail %s dpr %s on %s: %s"
                                    % (key, dpr, leg, describe(per_seed[0], retail)))

    for e in known:
        if id(e) not in used:
            failures.append("known defect no longer seen, remove it from exceptions.json: %s (%s)"
                            % (e["keys"], e["why"]))
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("firefox")
    d.add_argument("--leg", required=True)
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--reference", type=Path, default=REFERENCE)
    c = sub.add_parser("compare")
    c.add_argument("dumps", type=Path)
    c.add_argument("--reference", type=Path, default=REFERENCE)
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        return dump(args.firefox, args.leg, args.out, args.reference)
    notes = []
    failures = compare(args.dumps, args.reference, load_exceptions(), notes)
    for n in notes:
        print("NOTE " + n)
    for f in failures:
        print("FAIL " + f)
    print("HOST GATE %s: %d failures" % ("OK" if not failures else "FAILED", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
