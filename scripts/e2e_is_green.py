#!/usr/bin/env python
"""Refuse a release when the browser-driving e2e is not green on the commit
being tagged.

    python scripts/e2e_is_green.py --repo owner/name --sha <sha>
    python scripts/e2e_is_green.py --selftest

⛔ WHY THIS EXISTS. `publish.yml`'s gate runs `python -m pytest -q`, which is the
DEFAULT selection, and the default selection deselects `e2e`. So the job that
decides whether a release goes out has never looked at the suite that drives a
browser. The e2e runs on every push and every pull request, and no gate reads
it: a red there stops neither a merge nor a tag.

It has already happened. 0.22.1 was tagged from a main whose e2e was red, and
nothing said so, because nothing was looking. That is the same shape as the
`-m unit` false green the project forbids elsewhere, sitting inside the workflow
that publishes.

⛔ AND IT ASKS RATHER THAN RE-RUNS. The e2e has already run on that commit, and
running it again here would cost five minutes to learn something the API can
answer in one call. What is missing is not a second measurement: it is somebody
reading the first one.

The three verdicts, and the middle one is the one that gets written wrong:

  - a check-run concluded badly            -> REFUSE, naming it
  - NO e2e check-run exists for that commit -> REFUSE
  - still running                           -> wait, then refuse on the deadline

⛔ ZERO CHECK-RUNS IS NOT A VERDICT. An empty list is indistinguishable from
"the workflow was renamed", "it never started" and "nobody configured one", and
mapping it to a pass turns this gate into decoration exactly when it is most
needed. Measured the hard way on a different gate: a poller read an empty result
as DONE and reported a conclusion that the next iteration contradicted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

#: What counts as a conclusion that does not block. `skipped` and `neutral` are
#: here because a matrix leg can legitimately not run; `cancelled` is NOT, since
#: a cancelled run measured nothing and saying otherwise is the same lie as an
#: empty list.
FINE = ("success", "skipped", "neutral")

REFUSED_RED = 1
REFUSED_ABSENT = 2
REFUSED_TIMEOUT = 3


def verdict(runs, pattern):
    """(code, message) for the check-runs of one commit. Pure: no network.

    Separated from the fetching so its known-bad cases can run without a token,
    a repository or a network, which is what `--selftest` exercises.
    """
    ours = [c for c in runs if str(c.get("name", "")).startswith(pattern)]
    if not ours:
        return REFUSED_ABSENT, (
            "no check-run whose name starts with %r exists for this commit.\n"
            "That is not a pass: an empty list reads the same whether the job\n"
            "was renamed, never started, or was never configured. If the e2e\n"
            "workflow no longer produces that name, fix the name here rather\n"
            "than letting a release go out unmeasured." % pattern)

    running = [c["name"] for c in ours if c.get("status") != "completed"]
    if running:
        return None, "still running: %s" % ", ".join(sorted(running))

    bad = [(c["name"], c.get("conclusion")) for c in ours
           if c.get("conclusion") not in FINE]
    if bad:
        return REFUSED_RED, (
            "the e2e is not green on the commit being tagged:\n" +
            "\n".join("  %s: %s" % (n, c) for n, c in sorted(bad)) +
            "\nA release from here ships code the browser-driving suite\n"
            "rejected. Fix it, or re-run the job and let it say so itself.")

    return 0, "e2e green on this commit: %s" % ", ".join(
        sorted(c["name"] for c in ours))


def fetch(repo, sha, token):
    url = ("https://api.github.com/repos/%s/commits/%s/check-runs?per_page=100"
           % (repo, sha))
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer %s" % token,
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("check_runs", [])


SELFTEST = [
    ("a green leg passes", 0,
     [{"name": "e2e (linux, xvfb)", "status": "completed", "conclusion": "success"}]),
    ("a red leg refuses", REFUSED_RED,
     [{"name": "e2e (linux, xvfb)", "status": "completed", "conclusion": "failure"}]),
    ("a cancelled leg refuses, because it measured nothing", REFUSED_RED,
     [{"name": "e2e (linux, xvfb)", "status": "completed", "conclusion": "cancelled"}]),
    ("timed_out refuses", REFUSED_RED,
     [{"name": "e2e (linux, xvfb)", "status": "completed", "conclusion": "timed_out"}]),
    ("no check-run at all refuses", REFUSED_ABSENT, []),
    ("other jobs alone are not an e2e", REFUSED_ABSENT,
     [{"name": "tests (3.12)", "status": "completed", "conclusion": "success"}]),
    ("one green leg does not excuse a red one", REFUSED_RED,
     [{"name": "e2e (linux, xvfb)", "status": "completed", "conclusion": "success"},
      {"name": "e2e (macos)", "status": "completed", "conclusion": "failure"}]),
    ("a skipped leg is allowed", 0,
     [{"name": "e2e (linux, xvfb)", "status": "completed", "conclusion": "skipped"}]),
    ("still running is neither a pass nor a refusal", None,
     [{"name": "e2e (linux, xvfb)", "status": "in_progress", "conclusion": None}]),
    ("running beside a red one waits rather than guessing", None,
     [{"name": "e2e (a)", "status": "in_progress", "conclusion": None},
      {"name": "e2e (b)", "status": "completed", "conclusion": "failure"}]),
]


def selftest():
    bad = 0
    for label, want, runs in SELFTEST:
        got, _ = verdict(runs, "e2e")
        ok = got == want
        bad += not ok
        print("  %-56s %s" % (label, "ok" if ok else
                              "WRONG (wanted %r, got %r)" % (want, got)))
    print("%d/%d known-bad cases behave" % (len(SELFTEST) - bad, len(SELFTEST)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="e2e_is_green.py")
    ap.add_argument("--repo", help="owner/name")
    ap.add_argument("--sha", help="the commit being tagged")
    ap.add_argument("--pattern", default="e2e",
                    help="check-run name prefix to look for (default: e2e)")
    ap.add_argument("--wait-minutes", type=float, default=30.0,
                    help="how long to wait while a leg is still running")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if not (a.repo and a.sha):
        ap.error("--repo and --sha are required without --selftest")

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN is not set, so this gate cannot read the checks.\n"
              "A gate that cannot run is a refusal, not a pass.", file=sys.stderr)
        return REFUSED_ABSENT

    deadline = time.time() + a.wait_minutes * 60
    while True:
        try:
            runs = fetch(a.repo, a.sha, token)
        except urllib.error.HTTPError as e:
            print("the checks API answered %s: %s" % (e.code, e.reason),
                  file=sys.stderr)
            return REFUSED_ABSENT
        code, message = verdict(runs, a.pattern)
        if code is not None:
            print(message, file=sys.stderr if code else sys.stdout)
            return code
        if time.time() >= deadline:
            print("%s\nstill not finished after %g minutes. Refusing rather "
                  "than assuming." % (message, a.wait_minutes), file=sys.stderr)
            return REFUSED_TIMEOUT
        print("%s - waiting" % message)
        time.sleep(30)


if __name__ == "__main__":
    sys.exit(main())
