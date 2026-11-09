"""Do the two servers receive the same thing, and answer the same thing?

⛔ THIS IS THE QUESTION THAT DECIDES WHETHER THE NODE DRIVER CAN BE DELETED, and
it is not the same question `judge_both_transports.py` answers. That one runs
tests and reports which arm goes red - a behavioural check, and a coarse one: a
field the client does not happen to read today is missing in complete silence,
and shows up as a bug months later in a code path nobody exercised. This one
compares the WIRE.

**What it compares, and why at this level.**

* **input**: every method the client SENDS, and the set of PARAMETER NAMES it
  sends with each. Both servers must accept the same vocabulary. A method
  present on one side and absent on the other is a hole; a method present on
  both but reached with different fields means the client is being driven
  differently, which makes the rest of the comparison meaningless.
* **output**: every object TYPE the server creates, and the set of INITIALIZER
  FIELD NAMES for each; plus every EVENT name the server emits, per type. This
  is the half that reading `_impl` cannot give you: it tells you which fields
  are CONSUMED today, never which are SENT, and the difference is exactly the
  set a future code path will want.

⛔ **FIELD NAMES, NOT VALUES.** guids are allocated by whoever allocates them,
ports are ephemeral, versions and build ids differ by construction. Comparing
values would drown the real differences in noise that is not a difference at
all. Where a VALUE matters it belongs in a test, not here.

⛔ **AND IT RUNS THE SAME SESSION TWICE, IN TWO SUBPROCESSES.** The transport is
chosen once, at import time, from the environment: two transports in one process
would mean one of the two arms was not the thing it claims to be.

    python scripts/diff_protocol.py <firefox-binary>
    python scripts/diff_protocol.py <firefox-binary> --keep   (leave the traces)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

#: Fields whose ABSENCE on one side is not a finding, because the two paths
#: legitimately disagree about them. Kept short and each one argued: a long
#: list here is how a comparison stops comparing.
EXPECTED_DIFFERENCES = {
    # The driver reports the bundled Playwright's version string; we report the
    # engine's. Neither is wrong and no client behaviour depends on the text.
    ("Playwright", "initializer", "preLaunchedBrowser"),
    # ⛔ `previewUpdated` is a REAL gap, listed here because it is argued and
    # not because it is small. The driver pushes a new preview string every
    # time a handle's value changes, and `_js_handle.py` does listen for it -
    # so on this server `repr(handle)` keeps the preview it was born with.
    # That is the whole consequence: it reaches error messages and the REPL,
    # never a page, never a decision, never a byte on the wire. Implementing it
    # would mean re-serialising every handle after every evaluation, which is
    # protocol traffic and parent-process work for a string a human reads.
    # If it ever stops being cosmetic - a client that BRANCHES on the preview -
    # this line is where to start.
    ("ElementHandle", "event", "previewUpdated"),
    ("JSHandle", "event", "previewUpdated"),
}


#: A checkout that still carries the Node driver, for the day this one does not.
#: Same contract and same reason as in `judge_both_transports.py`: a worktree,
#: not an installed release, because a published wrapper pins a published core
#: and a sealed engine and cannot drive a locally built binary.
#:
#:     git worktree add /tmp/judge <the last commit that carried _driver/>
#:     INVPW_DRIVER_TREE=/tmp/judge python scripts/diff_protocol.py <binary>
DRIVER_TREE_ENV = "INVPW_DRIVER_TREE"


def capture(binary: str, transport: str, out: pathlib.Path) -> None:
    """Run the scripted session under one transport, writing its trace."""
    env = dict(os.environ)
    env["INVPW_TRANSPORT"] = transport
    env["INVPW_TRACE_OUT"] = str(out)
    tree = os.environ.get(DRIVER_TREE_ENV)
    if transport == "driver" and tree:
        # ⛔ The driver comes from the older tree; the RECORDER does not. The
        # capture script has to be this one, or the two traces are produced by
        # two different instruments and every difference between them measures
        # the instruments - which is the mistake this whole comparison exists
        # to avoid.
        src = str(pathlib.Path(tree) / "src")
        env["PYTHONPATH"] = os.pathsep.join(
            [src] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    script = ROOT / "scripts" / "capture_protocol.py"
    r = subprocess.run([sys.executable, str(script), binary, "-o", str(out)],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("the %s arm did not produce a trace:\n%s\n%s"
                           % (transport, r.stdout[-2000:], r.stderr[-2000:]))


def sent_vocabulary(trace: list) -> dict:
    """method -> the set of parameter names ever sent with it."""
    out: dict = {}
    for entry in trace:
        if entry["dir"] != "send":
            continue
        message = entry["msg"]
        method = message.get("method")
        if not method:
            continue
        params = message.get("params") or {}
        out.setdefault(method, set()).update(params.keys())
    return out


def created_shapes(trace: list) -> dict:
    """type -> the set of initializer field names ever seen for it."""
    out: dict = {}
    for entry in trace:
        if entry["dir"] != "recv":
            continue
        message = entry["msg"]
        if message.get("method") != "__create__":
            continue
        params = message.get("params") or {}
        kind = params.get("type")
        if not kind:
            continue
        out.setdefault(kind, set()).update(
            (params.get("initializer") or {}).keys())
    return out


def events_by_owner(trace: list, shapes_of_guid: dict) -> dict:
    """type -> the set of event names the server emitted on objects of it."""
    out: dict = {}
    for entry in trace:
        if entry["dir"] != "recv":
            continue
        message = entry["msg"]
        method = message.get("method")
        if not method or method.startswith("__"):
            continue
        kind = shapes_of_guid.get(message.get("guid"), "?")
        out.setdefault(kind, set()).add(method)
    return out


def parentage(trace: list, types: dict) -> dict:
    """child TYPE -> the set of parent TYPES it was ever created under.

    ⛔ A FOURTH DIMENSION, ADDED BECAUSE ITS ABSENCE LET A REAL DEFECT
    THROUGH. This server created `ElementHandle` as a child of `Page`; the
    driver creates it under `Frame`. Types matched, initializer fields matched,
    events matched - and every call that reads a timeout off the handle's frame
    died with `'Page' object has no attribute '_timeout'`, because
    `_element_handle.py` learns its frame from the guid TREE and from nothing
    else. Parentage is protocol, not bookkeeping.
    """
    out: dict = {}
    for entry in trace:
        if entry["dir"] != "recv":
            continue
        message = entry["msg"]
        if message.get("method") != "__create__":
            continue
        params = message.get("params") or {}
        kind = params.get("type")
        if not kind:
            continue
        out.setdefault(kind, set()).add(types.get(message.get("guid"), "root"))
    return out


def disposed_types(trace: list, types: dict) -> dict:
    """type -> whether an object of it was ever disposed ("yes" or nothing).

    ⛔ A FIFTH DIMENSION, AND THE FOUR ABOVE ARE BLIND TO IT BY CONSTRUCTION:
    `events_by_owner` skips every method starting with `__`, so `__dispose__`
    was invisible to this comparison no matter how far the two servers drifted.
    It is not bookkeeping either - the client ACTS on it, dropping the object
    from its registry - so a server that never disposes anything grows the
    client's registry for the life of the browser, and a server that disposes
    something the client still holds hands back a dead reference.

    It was added on 2026-08-28 to answer a question the PARITY verdict could
    not: this server never disposed a closed page, and the count of live
    objects grew by one per page (9 at page 0, 508 at page 499). Whether that
    is a defect or a difference depends entirely on what the driver does, and
    nothing here could see it.

    The value is a one-element set rather than a bool so `report` can compare
    it like every other dimension.
    """
    out: dict = {}
    for entry in trace:
        if entry["dir"] != "recv":
            continue
        message = entry["msg"]
        if message.get("method") != "__dispose__":
            continue
        kind = types.get(message.get("guid"))
        if kind:
            out.setdefault(kind, set()).add("disposed")
    return out


def guid_types(trace: list) -> dict:
    """guid -> its type, so an event can be attributed to a KIND of object."""
    out: dict = {}
    for entry in trace:
        if entry["dir"] != "recv":
            continue
        message = entry["msg"]
        if message.get("method") != "__create__":
            continue
        params = message.get("params") or {}
        if params.get("guid"):
            out[params["guid"]] = params.get("type")
    return out


def report(label: str, ours: dict, theirs: dict, kind: str) -> list:
    """Differences in one dimension, as lines. Empty means parity."""
    faults = []

    def kept(name, fields):
        """⛔ The argued differences are filtered HERE TOO, and they were not.
        A field can be missing two ways: the key exists on both sides and one
        set is short, or the key is absent entirely. The first version only
        filtered the first, so an event type that appears on the driver and
        nowhere on ours stayed red forever no matter what was written about
        it - and a gate with a permanent red is a gate people stop reading."""
        return sorted(f for f in fields
                      if (name, kind, f) not in EXPECTED_DIFFERENCES)

    for name in sorted(set(theirs) - set(ours)):
        rest = kept(name, theirs[name])
        if rest:
            faults.append("%s %r reaches the DRIVER and not us: %s"
                          % (label, name, rest))
    for name in sorted(set(ours) - set(theirs)):
        rest = kept(name, ours[name])
        if rest:
            faults.append("%s %r reaches US and not the driver: %s"
                          % (label, name, rest))
    for name in sorted(set(ours) & set(theirs)):
        missing = theirs[name] - ours[name]
        extra = ours[name] - theirs[name]
        missing = {f for f in missing
                   if (name, kind, f) not in EXPECTED_DIFFERENCES}
        extra = {f for f in extra
                 if (name, kind, f) not in EXPECTED_DIFFERENCES}
        if missing:
            faults.append("%s %r: the driver carries %s and we do not"
                          % (label, name, sorted(missing)))
        if extra:
            faults.append("%s %r: we carry %s and the driver does not"
                          % (label, name, sorted(extra)))
    return faults


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("binary")
    p.add_argument("--keep", action="store_true",
                   help="leave the two traces on disk for inspection")
    a = p.parse_args()

    work = pathlib.Path(tempfile.mkdtemp(prefix="protocol_diff_"))
    driver_trace = work / "driver.json"
    ours_trace = work / "juggler.json"

    print("recording the DRIVER arm ...", flush=True)
    capture(a.binary, "driver", driver_trace)
    print("recording the PYTHON arm ...", flush=True)
    capture(a.binary, "juggler", ours_trace)

    driver = json.loads(driver_trace.read_text(encoding="utf-8"))
    ours = json.loads(ours_trace.read_text(encoding="utf-8"))
    print()
    print("  driver: %d messages    python: %d messages"
          % (len(driver), len(ours)))

    faults = []
    faults += report("method", sent_vocabulary(ours), sent_vocabulary(driver),
                     "params")
    faults += report("object", created_shapes(ours), created_shapes(driver),
                     "initializer")
    faults += report("events on", events_by_owner(ours, guid_types(ours)),
                     events_by_owner(driver, guid_types(driver)), "event")
    faults += report("parent of", parentage(ours, guid_types(ours)),
                     parentage(driver, guid_types(driver)), "parent")
    faults += report("disposal of", disposed_types(ours, guid_types(ours)),
                     disposed_types(driver, guid_types(driver)), "dispose")

    print()
    for line in faults:
        print("  " + line)
    if a.keep or faults:
        print()
        print("  traces: %s" % work)
    if faults:
        print("PROTOCOL DIFF: %d difference(s). The Node driver cannot be "
              "deleted while any of these stand, or a caller who reaches one "
              "gets silence instead of a browser." % len(faults))
        return 1
    print("PROTOCOL DIFF: PARITY - same methods with the same parameters in, "
          "same object types with the same initializer fields, the same "
          "events, the same parentage and the same disposals out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
