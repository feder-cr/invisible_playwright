#!/usr/bin/env python3
"""Launch the browser, open one page, close everything - many times - and keep
what the engine said each time.

    python scripts/hunt_launch_close.py --binary <firefox> --cycles 100 --out evidence/launch

It exists to find where `BrowserType.launch: [Errno 32] Broken pipe` comes from. That
message is a `BrokenPipeError` from the first write of the launch to a browser that has
already exited: the launch gives no exit code and no output with it, so the reason the
engine went away is lost unless somebody keeps the engine's output. This script keeps it.

Each cycle is a CHILD process (`--one`), so an exit status exists per cycle and a cycle
cannot poison the next. The child:

  * launches the way the tests do (`InvisiblePlaywright` as a context manager, the fetched
    binary, `humanize=False`), opens one page on about:blank and leaves the block;
  * records the engine's output. The engine's stdout and stderr are ONE pipe that the core
    reads with `invisible_core.juggler.connection.ProcessOutput` and keeps (last 200
    lines); the public API does not hand that object out, so the child records every
    instance that is created (a wrapper on its constructor, in this script, no product code
    is changed) and writes their kept lines to `engine.log` in its `finally`, also when the
    launch itself failed. If the core on the machine has no such class the child says so in
    `engine.log` and the cycle's record says `engine_captured: false`;
  * leaves its own stderr and stdout to the parent, which writes them to `stderr.log` and
    `stdout.log` (the launcher's logging and the traceback are there).

The parent then records: exit status, duration, whether `Broken pipe` (any case) appears in
what was captured and in which file, and how many processes of the browser binary are left
(they must be 0; leftovers are counted, then killed so one leak cannot cascade).

Caught failures (a cycle that fails) are the result; the script ends 0. It ends non-zero
only when it could not run at all (no binary, no way to count processes).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

MARKER = "broken pipe"
CONTEXT = 6          # lines kept on each side of a hit
CYCLE_TIMEOUT_S = 240
ENGINE_LOG = "engine.log"
CAPTURED_HEADER = "=== engine output"


# ── pure parts ──────────────────────────────────────────────────────────────


def find_marker(text: str, marker: str = MARKER, context: int = CONTEXT) -> list[str]:
    """Every hit of `marker` (any case) with `context` lines on each side, whole.
    Hits whose windows touch are one excerpt."""
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if marker.lower() in line.lower()]
    out: list[str] = []
    start = end = None
    for i in hits:
        lo, hi = max(0, i - context), min(len(lines), i + context + 1)
        if start is not None and lo <= end:
            end = hi
            continue
        if start is not None:
            out.append("\n".join(lines[start:end]))
        start, end = lo, hi
    if start is not None:
        out.append("\n".join(lines[start:end]))
    return out


def count_browser_processes(processes, binary: str) -> int:
    """How many of `processes` (pairs of exe path and argv) are the browser `binary`
    or its children (content processes are the same executable)."""
    wanted = os.path.normcase(os.path.realpath(binary))
    n = 0
    for exe, argv in processes:
        first = argv[0] if argv else ""
        for candidate in (exe, first):
            if candidate and os.path.normcase(os.path.realpath(candidate)) == wanted:
                n += 1
                break
    return n


def cycle_record(*, cycle: int, headless: bool, status, seconds: float, captured: dict,
                 leftover: int, engine_captured: bool) -> dict:
    """`captured` maps a file name to its text; `status` is the child's exit code, or
    'timeout'."""
    hits = {name: find_marker(text) for name, text in captured.items()}
    where = sorted(name for name, found in hits.items() if found)
    return {"cycle": cycle, "headless": headless, "status": status,
            "ok": status == 0 and leftover == 0, "seconds": round(seconds, 2),
            "broken_pipe": bool(where), "broken_pipe_in": where,
            "excerpts": {name: found for name, found in hits.items() if found},
            "leftover": leftover, "engine_captured": engine_captured}


def tally(records: list[dict]) -> dict:
    return {
        "cycles": len(records),
        "failures": sum(1 for r in records if r["status"] != 0),
        "broken_pipe": sum(1 for r in records if r["broken_pipe"]),
        "leftover_cycles": sum(1 for r in records if r["leftover"] > 0),
        "leftover_processes": sum(max(0, r["leftover"]) for r in records),
        "engine_not_captured": sum(1 for r in records if not r["engine_captured"]),
    }


def render_summary(records: list[dict], job: str = "") -> str:
    t = tally(records)
    out = ["### launch-close %s" % job, "",
           "Caught results (the job stays green): cycles that failed, `Broken pipe`, leftovers. "
           "Broken (the job ends red): the script could not run.", "",
           "| cycles | failed | with `Broken pipe` | cycles with leftover processes | engine output missing |",
           "|---|---|---|---|---|",
           "| %(cycles)d | %(failures)d | %(broken_pipe)d | %(leftover_cycles)d | %(engine_not_captured)d |" % t]
    bad = [r for r in records if r["status"] != 0 or r["leftover"] > 0 or r["broken_pipe"]]
    if bad:
        out += ["", "| cycle | headless | status | seconds | Broken pipe in | leftover |", "|---|---|---|---|---|---|"]
        for r in bad:
            out.append("| %d | %s | %s | %s | %s | %d |" % (
                r["cycle"], r["headless"], r["status"], r["seconds"],
                ", ".join(r["broken_pipe_in"]) or "-", r["leftover"]))
    for r in records:
        for name, excerpts in r["excerpts"].items():
            for excerpt in excerpts:
                out += ["", "Cycle %d, `%s`, around `Broken pipe`:" % (r["cycle"], name), "", "```", excerpt, "```"]
    return "\n".join(out) + "\n"


# ── the child: one cycle ────────────────────────────────────────────────────


def _install_output_capture():
    """Record every `ProcessOutput` the core creates. None when the core has none."""
    try:
        from invisible_core.juggler import connection
    except ImportError:
        return None
    cls = getattr(connection, "ProcessOutput", None)
    if cls is None:
        return None
    seen: list = []
    original = cls.__init__

    def recording_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        seen.append(self)

    cls.__init__ = recording_init
    return seen


def _write_engine_log(path: Path, seen) -> None:
    parts: list[str] = []
    if seen is None:
        parts.append("(engine output NOT captured: invisible_core.juggler.connection.ProcessOutput not found)")
    elif not seen:
        parts.append("%s: none (no browser process was started by this cycle)" % CAPTURED_HEADER)
    for i, out in enumerate(seen or [], 1):
        try:
            out.settle()
        except Exception as exc:  # noqa: BLE001 - the log must be written whatever happens
            parts.append("(settle failed: %r)" % (exc,))
        keep = getattr(out, "KEEP", 200)
        parts.append("%s %d of %d (last %d lines, stdout+stderr of the engine) ===" % (
            CAPTURED_HEADER, i, len(seen), keep))
        parts += out.tail(keep)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def run_one(binary: str, engine_log: Path, headless: bool) -> int:
    seen = _install_output_capture()
    try:
        from invisible_playwright import InvisiblePlaywright
        with InvisiblePlaywright(seed=42, binary_path=binary, humanize=False,
                                 headless=headless) as browser:
            page = browser.new_page()
            page.goto("about:blank")
        return 0
    except BaseException:  # noqa: BLE001 - the traceback is the evidence
        import traceback
        traceback.print_exc()
        return 1
    finally:
        _write_engine_log(engine_log, seen)


# ── the parent: the loop ────────────────────────────────────────────────────


def _processes():
    import psutil
    for proc in psutil.process_iter(["exe", "cmdline", "pid"]):
        info = proc.info
        yield proc, (info.get("exe") or "", info.get("cmdline") or [])


def _leftovers(binary: str) -> int:
    """Browser processes alive now; they are killed after being counted."""
    deadline = time.monotonic() + 3.0      # the engine's tree takes a moment to go
    while True:
        alive = [proc for proc, (exe, argv) in _processes()
                 if count_browser_processes([(exe, argv)], binary)]
        if not alive or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    for proc in alive:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - already gone
            pass
    return len(alive)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--binary", default=os.environ.get("INVPW_BINARY_PATH"))
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--start", type=int, default=1, help="number of the first cycle")
    ap.add_argument("--out", default="evidence/launch")
    ap.add_argument("--job", default="")
    ap.add_argument("--headless", choices=("yes", "no", "alternate"), default="alternate",
                    help="the library's headless flag; alternate = odd cycles headed, even headless")
    ap.add_argument("--one", metavar="ENGINE_LOG", help="(internal) run one cycle in this process")
    args = ap.parse_args(argv)
    if not args.binary or not Path(args.binary).exists():
        print("hunt_launch_close: binary not found: %r" % args.binary, file=sys.stderr)
        return 2
    if args.one:
        return run_one(args.binary, Path(args.one), args.headless == "yes")
    try:
        import psutil  # noqa: F401
    except ImportError:
        print("hunt_launch_close: psutil is needed to count leftover processes", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for cycle in range(args.start, args.start + args.cycles):
        headless = {"yes": True, "no": False}.get(args.headless, cycle % 2 == 0)
        d = out_dir / ("cycle_%04d" % cycle)
        d.mkdir(exist_ok=True)
        cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--binary", args.binary,
               "--headless", "yes" if headless else "no", "--one", str(d / ENGINE_LOG)]
        t0 = time.monotonic()
        with open(d / "stdout.log", "wb") as so, open(d / "stderr.log", "wb") as se:
            try:
                status = subprocess.run(cmd, stdout=so, stderr=se, timeout=CYCLE_TIMEOUT_S).returncode
            except subprocess.TimeoutExpired:
                status = "timeout"
        seconds = time.monotonic() - t0
        leftover = _leftovers(args.binary)
        engine_text = _read(d / ENGINE_LOG)
        engine_captured = CAPTURED_HEADER in engine_text
        rec = cycle_record(cycle=cycle, headless=headless, status=status, seconds=seconds,
                           captured={ENGINE_LOG: engine_text, "stderr.log": _read(d / "stderr.log"),
                                     "stdout.log": _read(d / "stdout.log")},
                           leftover=leftover, engine_captured=engine_captured)
        (d / "record.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        records.append(rec)
        print("cycle %d headless=%s status=%s %.1fs broken_pipe=%s leftover=%d engine_captured=%s" % (
            cycle, headless, status, seconds, "YES(%s)" % ",".join(rec["broken_pipe_in"]) if rec["broken_pipe"] else "no",
            leftover, engine_captured), flush=True)
    text = render_summary(records, args.job)
    (out_dir / "summary.md").write_text(text, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(tally(records), indent=1), encoding="utf-8")
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
