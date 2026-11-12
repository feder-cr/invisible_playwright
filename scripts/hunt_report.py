#!/usr/bin/env python3
"""The pure parts of `.github/workflows/e2e-hunt.yml` (hunt = release-triple).

Standard library only: the plan job runs it before anything is installed.

    python scripts/hunt_report.py plan  --hunt H --which W ...        # matrices for the workflow
    python scripts/hunt_report.py run-line  --log L --out J ...       # one full suite run -> one line + json
    python scripts/hunt_report.py suite-summary DIR                   # table of the runs one job made
    python scripts/hunt_report.py suite-tally DIR                     # all jobs, all runs, distinct messages
    python scripts/hunt_report.py webgl-run --log L --out J ...       # one pytest process -> pass/fail + message
    python scripts/hunt_report.py webgl-tally DIR                     # n failed of N, distinct reasons

What is a CAUGHT failure and what is not. A test that fails, in a run that ended
with a pytest summary, is what the hunt looks for: it is recorded and the job
stays green. A run that produced no verdict at all (the exit code is not 0 or 1,
or there is no summary line, or the test was skipped) is a BROKEN run: the job
ends red after it has recorded everything, because there the hunt itself did not
work.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

__all__ = ["make_plan", "parse_pytest_log", "run_record", "suite_summary", "suite_tally",
           "webgl_record", "webgl_tally", "normalise"]

# ── the plan ────────────────────────────────────────────────────────────────

HUNTS = ("suite", "webgl", "launch")
#: Minutes one full suite run may take; a run is 18-20 on four CPUs. The job
#: ceiling must never be the thing that stops a hunt, so it is generous.
_SUITE_RUN_MIN = 30
_ONE_CPU_RUN_MIN = 100
_MAX_JOB_MIN = 355


def _which(which: str) -> set[str]:
    """`all`, or a comma list of suite / webgl / launch. Anything else is an error."""
    names = {w.strip().lower() for w in which.split(",") if w.strip()}
    if not names:
        raise ValueError("`which` is empty: use all, suite, webgl, launch or a comma list")
    if "all" in names:
        names = set(HUNTS) | (names - {"all"})
    bad = sorted(names - set(HUNTS))
    if bad:
        raise ValueError("`which` names %s; the hunts are %s, or all" % (bad, ", ".join(HUNTS)))
    return names


def make_plan(*, which: str, suite_jobs: int, suite_runs_each: int, onecpu_runs: int,
              webgl_rounds: int, webgl_procs: int, launch_jobs: int,
              launch_cycles_each: int) -> dict:
    """What one dispatch runs: three flags and the matrices.

    The suite matrix has `suite_jobs` ordinary legs of `suite_runs_each` runs, and one
    more leg, labelled, whose processes are limited to ONE CPU (`taskset -c 0`) for
    `onecpu_runs` runs: its reds are reported on their own line and never counted in
    the rate of the ordinary legs.
    """
    names = _which(which)
    for key, value in (("suite_jobs", suite_jobs), ("suite_runs_each", suite_runs_each),
                       ("onecpu_runs", onecpu_runs), ("webgl_rounds", webgl_rounds),
                       ("webgl_procs", webgl_procs), ("launch_jobs", launch_jobs),
                       ("launch_cycles_each", launch_cycles_each)):
        if value < 0:
            raise ValueError("%s must not be negative" % key)
    legs = []
    for k in range(1, suite_jobs + 1):
        if suite_runs_each > 0:
            legs.append({"label": "suite x%d #%d" % (suite_runs_each, k), "slug": "suite-%d" % k,
                         "runs": suite_runs_each, "cpus": "all", "taskset": "",
                         "timeout": min(_MAX_JOB_MIN, suite_runs_each * _SUITE_RUN_MIN + 30)})
    if onecpu_runs > 0:
        legs.append({"label": "suite 1-CPU x%d" % onecpu_runs, "slug": "suite-1cpu",
                     "runs": onecpu_runs, "cpus": "1", "taskset": "taskset -c 0",
                     "timeout": min(_MAX_JOB_MIN, onecpu_runs * _ONE_CPU_RUN_MIN + 30)})
    launch_legs = [{"job": k, "cycles": launch_cycles_each,
                    "start": (k - 1) * launch_cycles_each + 1,
                    "timeout": min(_MAX_JOB_MIN, 60 + launch_cycles_each // 2)}
                   for k in range(1, launch_jobs + 1)] if launch_cycles_each > 0 else []
    run_suite = "suite" in names and bool(legs)
    run_webgl = "webgl" in names and webgl_rounds > 0 and webgl_procs > 0
    run_launch = "launch" in names and bool(launch_legs)
    return {
        "suite": str(run_suite).lower(),
        "webgl": str(run_webgl).lower(),
        "launch": str(run_launch).lower(),
        "suite_matrix": json.dumps(legs if run_suite else []),
        "launch_matrix": json.dumps(launch_legs if run_launch else []),
        "webgl_timeout": str(min(_MAX_JOB_MIN, 40 + webgl_rounds * 4)),
    }


# ── reading a pytest log ────────────────────────────────────────────────────

_SUMMARY = re.compile(r"^=+ (?P<body>.+?) in (?P<secs>[\d.]+)s(?: \([\d:]+\))? =+$")
_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|reruns?|xfailed|xpassed)")
_RESULT = re.compile(r"^(FAILED|ERROR) (\S+)")
_RERUN = re.compile(r"\bRERUN (\S+)")
_SECTION = re.compile(r"^_{2,} (?P<name>.+?) _{2,}$")
_BANNER = re.compile(r"^=+ (?P<name>.+?) =+$")


def normalise(message: str) -> str:
    """The key under which two failure messages are the same message: numbers
    (pids, ports, timings, addresses' digits) differ from run to run."""
    return re.sub(r"0x[0-9a-fA-F]+|\d+(?:\.\d+)?", "N", message)


def _matches(test_id: str, section: str) -> bool:
    tail = test_id.split("::")[-1]
    return section == tail or section.endswith(tail)


def parse_pytest_log(text: str) -> dict:
    """Counts, failed ids and the whole failure messages of one run_e2e.py log.

    A full run is two pytest invocations (the parallel pass, then the wall-clock
    tests alone): the counts are summed, and `summaries` says how many summary
    lines were found (0 = the run never reached a verdict).
    """
    out = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "rerun": 0, "seconds": 0.0,
           "summaries": 0, "failed_ids": [], "rerun_ids": [], "failures": {}}
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    in_failures = False
    short: dict[str, list[str]] = {}
    current_short = None
    in_short = False
    for line in lines:
        line = line.rstrip("\r")
        banner = _BANNER.match(line)
        if banner:
            name = banner.group("name")
            in_failures = name in ("FAILURES", "ERRORS")
            in_short = name == "short test summary info"
            current_short = None
            m = _SUMMARY.match(line)
            if m:
                out["summaries"] += 1
                out["seconds"] += float(m.group("secs"))
                for n, kind in _COUNT.findall(m.group("body")):
                    n = int(n)
                    if kind == "passed":
                        out["passed"] += n
                    elif kind == "failed":
                        out["failed"] += n
                    elif kind.startswith("error"):
                        out["errors"] += n
                    elif kind == "skipped":
                        out["skipped"] += n
                    elif kind.startswith("rerun"):
                        out["rerun"] += n
                in_short = False
            continue
        rr = _RERUN.search(line)
        if rr and rr.group(1) not in out["rerun_ids"]:
            out["rerun_ids"].append(rr.group(1))
        if in_failures:
            sec = _SECTION.match(line)
            if sec:
                sections.append((sec.group("name"), []))
            elif sections:
                sections[-1][1].append(line)
        elif in_short:
            res = _RESULT.match(line)
            if res:
                test_id = res.group(2)
                if test_id not in out["failed_ids"]:
                    out["failed_ids"].append(test_id)
                rest = line[res.end():]
                rest = rest[3:] if rest.startswith(" - ") else rest.strip()
                short[test_id] = [rest]
                current_short = test_id
            elif current_short is not None:
                short[current_short].append(line)
    for test_id in out["failed_ids"]:
        body = None
        for name, section in sections:
            if _matches(test_id, name):
                e_lines = [re.sub(r"^E {0,3}", "", s, count=1) for s in section if re.match(r"^E( |$)", s)]
                body = "\n".join(e_lines) if e_lines else "\n".join(section).strip()
                break
        if not body:
            body = "\n".join(short.get(test_id, [])).strip()
        out["failures"][test_id] = body
    return out


# ── one suite run ───────────────────────────────────────────────────────────


def run_record(log_text: str, *, run: int, of: int, label: str, cpus: str, exit_code: int,
               seconds: int) -> dict:
    p = parse_pytest_log(log_text)
    ended = p["summaries"] > 0 and exit_code in (0, 1)
    if not ended:
        verdict = "no-verdict"
    elif exit_code == 0:
        verdict = "green"
    else:
        verdict = "red"
    return {"run": run, "of": of, "label": label, "cpus": cpus, "exit": exit_code,
            "seconds": seconds, "verdict": verdict, **{k: p[k] for k in (
                "passed", "failed", "errors", "skipped", "rerun", "failed_ids", "rerun_ids",
                "failures")}}


def run_line(rec: dict) -> str:
    ids = ",".join(rec["failed_ids"]) or "-"
    return ("run %d/%d [%s]: %s exit=%d passed=%d failed=%d errors=%d rerun=%d skipped=%d "
            "duration=%ds failed_ids=%s" % (
                rec["run"], rec["of"], rec["label"], rec["verdict"].upper(), rec["exit"],
                rec["passed"], rec["failed"], rec["errors"], rec["rerun"], rec["skipped"],
                rec["seconds"], ids))


def _load(directory: Path, pattern: str) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.rglob(pattern))]


def suite_summary(records: list[dict]) -> str:
    """The step summary of one job: a table with one row per run."""
    if not records:
        return "### No run was recorded\n\nThe hunt did not produce a single run: this is a BROKEN job, not a caught failure.\n"
    first = records[0]
    rows = ["### %s" % first["label"], "",
            "| run | verdict | exit | passed | failed | rerun | skipped | duration | failed tests |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        ids = "<br>".join("`%s`" % i for i in r["failed_ids"]) or "-"
        rows.append("| %d/%d | %s | %d | %d | %d | %d | %d | %ds | %s |" % (
            r["run"], r["of"], r["verdict"], r["exit"], r["passed"], r["failed"] + r["errors"],
            r["rerun"], r["skipped"], r["seconds"], ids))
    broken = sum(1 for r in records if r["verdict"] == "no-verdict")
    rows += ["", "**Caught** (red runs, listed above): %d. **Broken** (no verdict, the job ends red): %d."
             % (sum(1 for r in records if r["verdict"] == "red"), broken)]
    return "\n".join(rows) + "\n"


def _group_tally(records: list[dict]) -> list[str]:
    n = len(records)
    green = [r for r in records if r["verdict"] == "green"]
    red = [r for r in records if r["verdict"] == "red"]
    broken = [r for r in records if r["verdict"] == "no-verdict"]
    clean = [r for r in green if r["rerun"] == 0]
    out = ["runs total: %d" % n,
           "runs fully green: %d (of which with no rerun at all: %d)" % (len(green), len(clean)),
           "runs red (caught): %d" % len(red),
           "runs without a verdict (broken): %d" % len(broken)]
    per_test: dict[str, list[tuple[dict, str]]] = {}
    for r in records:
        for test_id in r["failed_ids"]:
            per_test.setdefault(test_id, []).append((r, r["failures"].get(test_id, "")))
    reran: dict[str, int] = {}
    for r in records:
        for test_id in r["rerun_ids"]:
            reran[test_id] = reran.get(test_id, 0) + 1
    if per_test:
        out.append("")
        out.append("tests that failed at least once:")
        for test_id, hits in sorted(per_test.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            out.append("- %s: failed in %d of %d runs" % (test_id, len(hits), n))
            seen: dict[str, tuple[int, str]] = {}
            for _, msg in hits:
                key = normalise(msg)
                count, first_msg = seen.get(key, (0, msg))
                seen[key] = (count + 1, first_msg)
            for count, msg in seen.values():
                out.append("  message (seen %d time%s), whole:" % (count, "" if count == 1 else "s"))
                out += ["      " + s for s in (msg.splitlines() or ["(empty)"])]
    if reran:
        out.append("")
        out.append("tests that were rerun at least once (passed on a rerun, or failed after it):")
        for test_id, count in sorted(reran.items(), key=lambda kv: (-kv[1], kv[0])):
            out.append("- %s: rerun in %d of %d runs" % (test_id, count, n))
    return out


def suite_tally(records: list[dict]) -> str:
    """Every run of every job: the ordinary legs together, the 1-CPU leg on its own line."""
    normal = [r for r in records if r.get("cpus") != "1"]
    onecpu = [r for r in records if r.get("cpus") == "1"]
    out = ["# Suite hunt tally", "",
           "Caught failures are the result of this hunt, not a failure of it. A run without a verdict is broken.",
           "", "## Ordinary jobs (all CPUs): the rate", "", "```"]
    out += _group_tally(normal) if normal else ["no run"]
    out += ["```", "", "## 1-CPU job (processes limited to one CPU): reported apart, not part of the rate above",
            "", "```"]
    out += _group_tally(onecpu) if onecpu else ["no run"]
    out += ["```"]
    return "\n".join(out) + "\n"


# ── the webgl hunt ──────────────────────────────────────────────────────────


def webgl_record(log_text: str, *, round_: int, proc: int, exit_code: int, test_id: str) -> dict:
    p = parse_pytest_log(log_text)
    if p["passed"] == 1 and p["failed"] == 0 and p["errors"] == 0 and exit_code == 0:
        status, message = "pass", ""
    elif p["failed"] >= 1 and p["summaries"] > 0:
        status = "fail"
        message = next(iter(p["failures"].values()), "") or "(no message)"
    else:
        status, message = "no-verdict", "exit=%d, summary lines=%d, skipped=%d" % (
            exit_code, p["summaries"], p["skipped"])
    return {"round": round_, "proc": proc, "exit": exit_code, "status": status,
            "message": message, "test": test_id}


def webgl_tally(records: list[dict]) -> str:
    n = len(records)
    failed = [r for r in records if r["status"] == "fail"]
    broken = [r for r in records if r["status"] == "no-verdict"]
    out = ["runs: %d" % n, "passed: %d" % sum(1 for r in records if r["status"] == "pass"),
           "failed (caught): %d of %d" % (len(failed), n),
           "no verdict (broken): %d" % len(broken)]
    reasons: dict[str, list] = {}
    for r in failed:
        reasons.setdefault(normalise(r["message"]), []).append(r)
    if reasons:
        out += ["", "distinct reasons:"]
        for hits in sorted(reasons.values(), key=lambda h: -len(h)):
            where = ", ".join("round %d proc %d" % (h["round"], h["proc"]) for h in hits)
            out.append("- %d time%s (%s), whole message:" % (len(hits), "" if len(hits) == 1 else "s", where))
            out += ["      " + s for s in hits[0]["message"].splitlines()]
    if broken:
        out += ["", "runs without a verdict:"]
        out += ["- round %d proc %d: %s" % (r["round"], r["proc"], r["message"]) for r in broken]
    return "\n".join(out) + "\n"


# ── command line ────────────────────────────────────────────────────────────


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--hunt", default="release-triple")
    p.add_argument("--which", default="all")
    for name, default in (("suite-jobs", 6), ("suite-runs-each", 5), ("onecpu-runs", 2),
                          ("webgl-rounds", 13), ("webgl-procs", 4), ("launch-jobs", 3),
                          ("launch-cycles-each", 100)):
        p.add_argument("--" + name, type=int, default=default)
    p = sub.add_parser("run-line")
    p.add_argument("--log", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--run", type=int, required=True)
    p.add_argument("--of", type=int, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--cpus", default="all")
    p.add_argument("--exit", type=int, required=True, dest="exit_code")
    p.add_argument("--seconds", type=int, required=True)
    for name in ("suite-summary", "suite-tally", "webgl-tally"):
        q = sub.add_parser(name)
        q.add_argument("dir")
    p = sub.add_parser("webgl-run")
    p.add_argument("--log", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--round", type=int, required=True, dest="round_")
    p.add_argument("--proc", type=int, required=True)
    p.add_argument("--exit", type=int, required=True, dest="exit_code")
    p.add_argument("--test", required=True)
    args = ap.parse_args(argv)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")

    if args.cmd == "plan":
        if args.hunt != "release-triple":
            print("plan: hunt=%s has no plan" % args.hunt, file=sys.stderr)
            return 2
        try:
            plan = make_plan(which=args.which, suite_jobs=args.suite_jobs,
                             suite_runs_each=args.suite_runs_each, onecpu_runs=args.onecpu_runs,
                             webgl_rounds=args.webgl_rounds, webgl_procs=args.webgl_procs,
                             launch_jobs=args.launch_jobs,
                             launch_cycles_each=args.launch_cycles_each)
        except ValueError as exc:
            print("::error::%s" % exc)
            return 2
        text = "".join("%s=%s\n" % kv for kv in plan.items())
        sys.stdout.write(text)
        _append(os.environ.get("GITHUB_OUTPUT"), text)
        _append(summary, "### Plan\n\n```\n%s```\n" % text)
        return 0
    if args.cmd == "run-line":
        text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        rec = run_record(text, run=args.run, of=args.of, label=args.label, cpus=args.cpus,
                         exit_code=args.exit_code, seconds=args.seconds)
        Path(args.out).write_text(json.dumps(rec, indent=1), encoding="utf-8")
        print(run_line(rec))
        if rec["verdict"] == "no-verdict":
            print("::warning::%s: this run has no verdict (broken, not a caught failure)" % run_line(rec)[:80])
        return 0
    if args.cmd == "suite-summary":
        text = suite_summary(_load(Path(args.dir), "run_*.json"))
        sys.stdout.write(text)
        _append(summary, text)
        records = _load(Path(args.dir), "run_*.json")
        return 1 if (not records or any(r["verdict"] == "no-verdict" for r in records)) else 0
    if args.cmd == "suite-tally":
        records = _load(Path(args.dir), "run_*.json")
        text = suite_tally(records)
        sys.stdout.write(text)
        _append(summary, text)
        return 0 if records else 1
    if args.cmd == "webgl-run":
        text = Path(args.log).read_text(encoding="utf-8", errors="replace")
        rec = webgl_record(text, round_=args.round_, proc=args.proc, exit_code=args.exit_code,
                           test_id=args.test)
        Path(args.out).write_text(json.dumps(rec, indent=1), encoding="utf-8")
        first = rec["message"].splitlines()[0] if rec["message"] else ""
        print("round %02d proc %d: %s %s" % (rec["round"], rec["proc"], rec["status"].upper(), first))
        return 0
    if args.cmd == "webgl-tally":
        records = _load(Path(args.dir), "round_*.json")
        text = webgl_tally(records)
        sys.stdout.write(text)
        _append(summary, "### webgl\n\n```\n%s```\n" % text)
        return 1 if (not records or any(r["status"] == "no-verdict" for r in records)) else 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
