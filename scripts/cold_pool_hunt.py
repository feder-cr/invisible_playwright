"""Repeats ONE e2e test many times with the engine's SOCKS logs, one log folder per repetition, and reports the red ones with their times.

    python scripts/cold_pool_hunt.py --test <nodeid> --repetitions 150 --out hunt [--greens 3]

For each repetition i the test runs alone in a fresh pytest process with
    MOZ_LOG="timestamp,sync,append,SOCKS:5,SOCKS-UDP:5,signaling:5"   MOZ_LOG_FILE=<out>/rep-<i>/moz.log   HUNT_EVENTS=<out>/rep-<i>/gateway.jsonl
(`tests/_hunt_plugin.py` writes the wall-clock times of what the test gateway sees). A red repetition keeps its folder; the first
`--greens` green ones keep theirs for comparison; the others are removed, so the artifact stays small. `append` keeps the logs of both engine
launches of a repetition (the test launches two) in one file, told apart by the process id in each line.

It prints "red/total", and for every red repetition the sequence with times (see scripts/cold_pool_report.py). Exit 0: a red test is the
finding, not a failure of the hunt; only a hunt that could not run at all (no pytest, no repetition finished) exits 1.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import cold_pool_report  # noqa: E402

MOZ_LOG = "timestamp,sync,append,SOCKS:5,SOCKS-UDP:5,signaling:5"


def one(test, folder, timeout):
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MOZ_LOG=MOZ_LOG, MOZ_LOG_FILE=str(folder / "moz.log"), HUNT_EVENTS=str(folder / "gateway.jsonl"),
               PYTHONPATH=os.pathsep.join([str(ROOT / "tests"), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep))
    cmd = [sys.executable, "-m", "pytest", test, "-m", "e2e", "-p", "_hunt_plugin", "-p", "no:cacheprovider", "-q", "-s", "-rA",
           "--timeout", str(timeout), "--timeout-method", "thread"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=timeout + 60)
        rc, out = r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        rc, out = 124, "TIMEOUT\n%s" % ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
    (folder / "pytest.log").write_text(out, encoding="utf-8", errors="replace")
    (folder / "result.json").write_text(json.dumps({"rc": rc, "seconds": round(time.time() - t0, 1)}), encoding="utf-8")
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", required=True)
    ap.add_argument("--repetitions", type=int, default=150)
    ap.add_argument("--out", default="hunt")
    ap.add_argument("--greens", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=120, help="seconds one repetition may take")
    a = ap.parse_args()
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    reds, greens, errors, kept_green = [], 0, [], 0
    for i in range(1, a.repetitions + 1):
        folder = out / ("rep-%03d" % i)
        rc = one(a.test, folder, a.timeout)
        if rc == 0:
            greens += 1
            if kept_green < a.greens:
                kept_green += 1
            else:
                shutil.rmtree(folder, ignore_errors=True)
        elif rc == 1:
            reds.append(i)
        else:
            errors.append((i, rc))
            reds.append(i)    # kept as well: a repetition that did not finish is not a green one
        print("repetition %03d: rc %s%s" % (i, rc, "  (kept)" if folder.exists() else ""), flush=True)
    done = greens + len(reds)
    print("\n=== %d red / %d repetitions (%d green; %d ended with something other than pass/fail: %s)"
          % (len(reds), done, greens, len(errors), errors[:10]))
    summary = {"repetitions": done, "red": reds, "green": greens, "other": errors}
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    for i in reds:
        print()
        print(cold_pool_report.report(out / ("rep-%03d" % i)))
    for folder in sorted(p for p in out.glob("rep-*") if p.name[4:] and int(p.name[4:]) not in reds):
        print()
        print("--- a green one, for comparison")
        print(cold_pool_report.report(folder))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
