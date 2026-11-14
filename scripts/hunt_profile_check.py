"""HUNT SCAFFOLDING, NEVER MERGED: is a Gecko profile file the whole, aligned profile of the browser process that was asked for?

Why it exists (run 43551469616 of the profile hunt): ours' files were the profile of ANOTHER process (the `firefox.exe --backgroundtask pingsender` the parent starts
while it shuts down, 1.4-2.5 s long, same MOZ_PROFILER_SHUTDOWN path), the parent's own profile was overwritten or interleaved ("Extra data"), the clock anchor
was 23.6 s off, and nothing said so until the summary. This is the test every profile passes BEFORE its samples are read.

check_profile(path, parent_pid=, anchor_ms=, launch_ms=, typing=(start_ms, end_ms)) -> {"ok": bool, "problems": [str], "facts": {...}}; every clock in epoch
milliseconds. It fails when the file:
  - is not one complete JSON document (truncated, or a second document after the first: two writers);
  - has no parent main thread (GeckoMain, processType default), or its pid is not `parent_pid`;
  - has samples that do not cover the run: the process was created (meta.startTime) after the typing began or after the launch returned, or its last sample is
    before the typing ended;
  - disagrees with the Juggler's anchor (wall - ChromeUtils.now(), both from process creation) by more than ANCHOR_TOL_MS;
  - lost its beginning (the sample buffer wrapped: the first sample is far after meta.profilingStartTime);
  - is too thin (fewer samples than one per 20 ms of its span).

CLI:  python scripts/hunt_profile_check.py --log FILE [--log FILE ...]   reads the "PROFILE-CHECK {json}" lines of hunt_typing.py logs; exit 1 if there is none or any is bad
      python scripts/hunt_profile_check.py --selftest
"""
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hunt_gecko_profile as G

ANCHOR_TOL_MS = 100.0      # stock p50 2.5 ms, max 3.1 ms in the run that worked
LAUNCH_SLACK_MS = 500.0    # the python clock and the browser's wall clock are two readings of one system clock
WRAP_SLACK_MS = 1000.0     # first sample after meta.profilingStartTime
SPAN_PER_SAMPLE_MS = 20.0  # the sampler runs at 1 ms and measured 2 ms at p50


def read_document(path):
    """-> (profile dict or None, [problems]). One complete JSON document, nothing after it."""
    problems = []
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as exc:
        return None, ["cannot read %s: %s" % (path, exc)]
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        return None, ["empty file"]
    try:
        doc, end = json.JSONDecoder().raw_decode(text)
    except ValueError as exc:
        return None, ["not a complete JSON document (truncated?): %s" % exc]
    rest = text[end:].strip()
    if rest:
        problems.append("extra data after the document at char %d of %d: two writers on one file, or a shorter write over a longer one" % (end, len(text)))
    return doc, problems


def check_profile(path, parent_pid=None, anchor_ms=None, launch_ms=None, typing=None, min_samples=100, return_doc=False):
    """return_doc=True: the parsed document is also returned, under \"_doc\" (for a caller that reads the samples next; not JSON)"""
    res = _check(path, parent_pid, anchor_ms, launch_ms, typing, min_samples)
    if return_doc:
        res["_doc"] = res.pop("_doc_", None)
    else:
        res.pop("_doc_", None)
    return res


def _check(path, parent_pid, anchor_ms, launch_ms, typing, min_samples):
    facts = {"file": os.path.basename(path)}
    doc, problems = read_document(path)
    if doc is None:
        return {"ok": False, "problems": problems, "facts": facts}
    meta = doc.get("meta") or {}
    th = G.parent_main_thread(doc)
    facts["size"] = os.path.getsize(path)
    facts["startTime"] = meta.get("startTime")
    facts["shutdownTime"] = meta.get("shutdownTime")
    facts["processes"] = len(doc.get("processes") or [])
    if th is None:
        problems.append("no parent main thread (GeckoMain, processType default)")
        return {"ok": False, "problems": problems, "facts": facts}
    t = th.s_time
    start = meta.get("startTime")
    facts.update({"pid": th.pid, "samples": len(t), "first_ms": t[0] if t else None, "last_ms": t[-1] if t else None})
    gaps = sorted(b - a for a, b in zip(t, t[1:]) if b > a)
    facts["gap_p50_ms"] = round(gaps[len(gaps) // 2], 3) if gaps else None
    if parent_pid is not None and th.pid != parent_pid:
        problems.append("the profile is of pid %s, not of the parent %s (another process, e.g. a background task, wrote this file)" % (th.pid, parent_pid))
    if start is None or not t:
        problems.append("no meta.startTime or no samples")
        return {"ok": False, "problems": problems, "facts": facts}
    span = t[-1] - t[0]
    facts["span_ms"] = round(span, 1)
    if len(t) < min_samples or len(t) < span / SPAN_PER_SAMPLE_MS:
        problems.append("too thin: %d samples over %.0f ms" % (len(t), span))
    first_after = t[0] - (meta.get("profilingStartTime") or 0.0)
    facts["first_after_profiling_start_ms"] = round(first_after, 1)
    if first_after > WRAP_SLACK_MS:
        problems.append("the sample buffer wrapped: the first sample is %.0f ms after the profiler started" % first_after)
    end_wall = start + t[-1]
    facts["end_wall_ms"] = round(end_wall, 1)
    if anchor_ms is not None:
        facts["anchor_minus_startTime_ms"] = round(anchor_ms - start, 1)
        if abs(anchor_ms - start) > ANCHOR_TOL_MS:
            problems.append("clock misaligned: the Juggler's anchor is %.1f ms from meta.startTime (tolerance %.0f)" % (anchor_ms - start, ANCHOR_TOL_MS))
    if launch_ms is not None:
        facts["startTime_minus_launch_ms"] = round(start - launch_ms, 1)
        if start < launch_ms - LAUNCH_SLACK_MS:
            problems.append("the process was created %.0f ms BEFORE the launch call: not this browser" % (launch_ms - start))
    if typing is not None:
        facts["startTime_minus_typing_start_ms"] = round(start - typing[0], 1)
        facts["last_sample_minus_typing_end_ms"] = round(end_wall - typing[1], 1)
        if start > typing[0]:
            problems.append("the process was created %.0f ms AFTER the typing began: not the browser that typed" % (start - typing[0]))
        if end_wall < typing[1]:
            problems.append("the last sample is %.0f ms BEFORE the typing ended: the profile does not cover the run" % (typing[1] - end_wall))
    return {"ok": not problems, "problems": problems, "facts": facts, "_doc_": doc}


def read_log_checks(paths):
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("PROFILE-CHECK "):
                        try:
                            out.append(json.loads(line[len("PROFILE-CHECK "):]))
                        except ValueError:
                            out.append({"ok": False, "problems": ["unreadable PROFILE-CHECK line"], "facts": {}})
        except OSError:
            pass
    return out


def main(argv):
    logs = [argv[i + 1] for i, a in enumerate(argv) if a == "--log" and i + 1 < len(argv)]
    checks = read_log_checks(logs)
    bad = [c for c in checks if not c.get("ok")]
    print("profile checks: %d, bad %d" % (len(checks), len(bad)))
    for c in checks:
        print("  %s %s %s" % ("ok " if c.get("ok") else "BAD", c.get("what", "?"), json.dumps(c.get("facts"), sort_keys=True) if c.get("ok") else "; ".join(c.get("problems", []))))
    if not checks:
        print("NO PROFILE-CHECK line in %s: the profiler did not produce a checked profile" % ", ".join(logs))
        return 1
    return 1 if bad else 0


def _profile(pid, start, times, profiling_start=10.0, extra=""):
    th = {"name": "GeckoMain", "processType": "default", "pid": pid, "samples": {"schema": {"stack": 0, "time": 1}, "data": [[1, x] for x in times]},
          "stringTable": [], "stackTable": {"schema": {"prefix": 0, "frame": 1}, "data": []}, "frameTable": {"schema": {"location": 0}, "data": []}}
    doc = {"meta": {"startTime": start, "profilingStartTime": profiling_start, "shutdownTime": times[-1], "interval": 1, "categories": []}, "threads": [th], "libs": []}
    return json.dumps(doc) + extra


def selftest():
    import tempfile
    d = tempfile.mkdtemp()

    def write(name, text):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    t0 = 1.7e12
    full = [15.0 + 2.0 * i for i in range(8000)]          # 16 s
    typing = (t0 + 4000.0, t0 + 12000.0)
    good = write("good.json", _profile(100, t0, full))
    r = check_profile(good, parent_pid=100, anchor_ms=t0 + 1.1, launch_ms=t0 - 30.0, typing=typing)
    assert r["ok"], r
    # the pingsender case: another pid, created after the typing, short
    short = [64.0 + 2.0 * i for i in range(600)]
    r = check_profile(write("ping.json", _profile(200, t0 + 20000.0, short)), parent_pid=100, anchor_ms=t0 + 1.1, launch_ms=t0 - 30.0, typing=typing)
    assert not r["ok"] and any("not of the parent" in p for p in r["problems"]) and any("AFTER the typing began" in p for p in r["problems"]), r
    # anchor 23.6 s away
    r = check_profile(good, parent_pid=100, anchor_ms=t0 - 23600.0)
    assert not r["ok"] and any("misaligned" in p for p in r["problems"]), r
    # two documents in one file
    r = check_profile(write("two.json", _profile(100, t0, full) + _profile(200, t0, short)), parent_pid=100)
    assert not r["ok"] and any("extra data" in p for p in r["problems"]), r
    # cut off
    txt = _profile(100, t0, full)
    r = check_profile(write("cut.json", txt[:len(txt) // 2]), parent_pid=100)
    assert not r["ok"] and any("not a complete JSON" in p for p in r["problems"]), r
    # buffer wrapped: the first sample is 5 s after the profiler started
    r = check_profile(write("wrap.json", _profile(100, t0, [5000.0 + 2.0 * i for i in range(8000)])), parent_pid=100)
    assert not r["ok"] and any("wrapped" in p for p in r["problems"]), r
    # profile ends before the typing does
    r = check_profile(write("early.json", _profile(100, t0, [15.0 + 2.0 * i for i in range(1000)])), parent_pid=100, typing=typing)
    assert not r["ok"] and any("BEFORE the typing ended" in p for p in r["problems"]), r
    # a log with one ok and one bad line
    lg = write("log.txt", 'PROFILE-CHECK {"ok": true, "what": "a", "problems": [], "facts": {}}\nPROFILE-CHECK {"ok": false, "what": "b", "problems": ["x"], "facts": {}}\nother\n')
    assert main(["--log", lg]) == 1
    lg2 = write("log2.txt", 'PROFILE-CHECK {"ok": true, "what": "a", "problems": [], "facts": {}}\n')
    assert main(["--log", lg2]) == 0
    assert main(["--log", write("log3.txt", "nothing\n")]) == 1
    print("hunt_profile_check selftest ok")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))
