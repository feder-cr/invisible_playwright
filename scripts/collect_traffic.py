#!/usr/bin/env python3
"""Accumulate GitHub's traffic numbers into files in this repository.

Usage: collect_traffic.py <owner/repo> <data_dir>

WHY THIS EXISTS. GitHub publishes no per-page numbers for a wiki, and there is
no way to build one on GitHub alone: counting a view needs something that runs
when the page is rendered, and an <img> issues an unauthenticated GET, which
nothing on GitHub can receive. There are no readable access logs for the wiki,
for Pages or for raw.githubusercontent.com, and both `repository_dispatch` and
`workflow_dispatch` need an authenticated POST. So a tracking pixel can be
HOSTED here but never COUNTED here.

What does exist is the Traffic API, and its real problem is not accuracy - the
numbers are GitHub's own, deduplicated, with no proxy in the way - it is that
GITHUB THROWS THEM AWAY. `/traffic/views` is the last 14 days and nothing
before; `/traffic/popular/paths` is a top TEN over the same window. Miss a
fortnight and that fortnight is gone for good.

This turns that into history: run daily, keep every observation in the repo,
and after a month you have a month, after a year a year. The window stops being
a limit on what you know and becomes only a limit on how far back you can start.

WHAT IT CANNOT DO, said plainly because the report is unreadable otherwise: ten
paths. Not ten wiki pages - ten paths of any kind on the whole repository, so
the repo root, /issues, a popular /blob/ link and the wiki all compete for the
same ten slots. Pages outside the ten are not "zero", they are UNMEASURED, and
`REPORT.md` says so rather than printing a zero somebody will quote.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"


def today() -> str:
    """UTC, to match the timestamps the API returns."""
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def fetch(repo: str, endpoint: str, token: str):
    """One traffic endpoint, or a SystemExit that says what to do about it."""
    req = urllib.request.Request(
        f"{API}/repos/{repo}/{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "collect_traffic.py",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # The failure everyone hits first, so it gets the whole answer
            # rather than a status code. The workflow's built-in GITHUB_TOKEN
            # CANNOT read traffic: the endpoints want push access, which for a
            # fine-grained token is `Administration: Read`, and `administration`
            # is not among the permissions a GITHUB_TOKEN can be granted at all.
            # It needs a PAT of its own, stored as the TRAFFIC_TOKEN secret.
            raise SystemExit(
                f"HTTP {e.code} on {endpoint}. The traffic endpoints need push "
                "access to the repository, which the workflow's built-in "
                "GITHUB_TOKEN cannot have - `administration` is not one of the "
                "permissions it can be granted. Create a PAT (fine-grained: "
                "Administration -> Read on this repo; classic: `repo`) and put "
                "it in the TRAFFIC_TOKEN secret.")
        raise SystemExit(f"HTTP {e.code} on {endpoint}: {e.read()[:400]!r}")


def merge_views(history: dict, fresh: dict) -> dict:
    """Fold a `/traffic/views` response into the accumulated per-day history.

    LATER OBSERVATION WINS, and that is the whole subtlety. The API's newest day
    is TODAY, still in progress, so its count is partial. Tomorrow's run sees
    the same date complete. Keeping the first value would freeze every day at
    however much of it had happened when the job ran; refusing to overwrite
    would do the same thing more quietly. Days outside the API's 14-day window
    are simply not in `fresh`, and are left untouched - that is what makes this
    a history rather than a rolling window.
    """
    out = dict(history)
    for day in fresh.get("views", []):
        date = day["timestamp"][:10]
        out[date] = {"count": day.get("count", 0), "uniques": day.get("uniques", 0)}
    return out


def merge_paths(snapshots: dict, fresh: list, when: str) -> dict:
    """Fold a `/traffic/popular/paths` response in as a dated snapshot.

    NEVER SUMMED. Each response is a fourteen-day TOTAL, not a day's traffic, so
    adding successive daily responses counts every view about fourteen times and
    produces a number that looks like real growth. They are stored as snapshots
    and read one at a time; `latest_paths` is what the report ranks.
    """
    out = dict(snapshots)
    out[when] = [
        {
            "path": p.get("path", ""),
            "title": p.get("title", ""),
            "count": p.get("count", 0),
            "uniques": p.get("uniques", 0),
        }
        for p in fresh
    ]
    return out


def latest_paths(snapshots: dict) -> tuple[str, list]:
    """The most recent snapshot, by date. ("", []) when there is none."""
    if not snapshots:
        return "", []
    when = max(snapshots)
    return when, snapshots[when]


def wiki_rows(rows: list) -> list:
    """Only the wiki pages, best first."""
    out = [r for r in rows if "/wiki" in r["path"]]
    return sorted(out, key=lambda r: r["count"], reverse=True)


def render_report(repo: str, views: dict, snapshots: dict) -> str:
    """The file a human reads. Everything it claims is in the JSON beside it."""
    days = sorted(views)
    total = sum(v["count"] for v in views.values())
    uniq = sum(v["uniques"] for v in views.values())
    when, rows = latest_paths(snapshots)
    wiki = wiki_rows(rows)

    out = [
        f"# Traffic for {repo}",
        "",
        "Collected by `.github/workflows/wiki-traffic.yml`, which runs daily and",
        "commits what it finds. GitHub keeps only the last 14 days; this file is",
        "the part that outlives that window.",
        "",
        "## Repository views",
        "",
    ]
    if days:
        out += [
            f"- **{total}** views, **{uniq}** unique visitors",
            f"- across **{len(days)}** recorded days, {days[0]} to {days[-1]}",
            "",
            "| date | views | uniques |",
            "| --- | ---: | ---: |",
        ] + [
            f"| {d} | {views[d]['count']} | {views[d]['uniques']} |"
            for d in days[-14:]
        ] + ["", "_Last 14 recorded days; `views.json` has all of them._", ""]
    else:
        out += ["No days recorded yet - this is the first run.", ""]

    out += ["## Wiki pages", ""]
    if not snapshots:
        out += ["No snapshot yet - this is the first run.", ""]
    elif wiki:
        out += [
            f"Top paths as of **{when}**, 14-day totals:",
            "",
            "| page | views | uniques |",
            "| --- | ---: | ---: |",
        ] + [
            f"| [{r['title'] or r['path']}](https://github.com{r['path']}) "
            f"| {r['count']} | {r['uniques']} |"
            for r in wiki
        ] + [""]
    else:
        out += [
            f"**No wiki page reached the top ten on {when}.**",
            "",
            "That is not zero views. `/traffic/popular/paths` returns ten paths for",
            "the whole repository - the repo root, issues, file views and the wiki all",
            "compete for the same ten slots - so the wiki pages are simply unmeasured",
            "in this snapshot. `paths.json` records what did make the ten.",
            "",
        ]

    out += [
        "## What these numbers are",
        "",
        "GitHub's own counts, deduplicated by visitor, with nothing proxying or",
        "caching in between - accurate, unlike anything a tracking pixel could",
        "produce. The limit is coverage, not fidelity: ten paths, and a page",
        "outside them is unmeasured rather than unvisited.",
        "",
    ]
    return "\n".join(out) + "\n"


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        # A first run has no file, and a truncated one must not be the reason
        # the collection stops - the next write replaces it either way.
        return default


def write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")


def main(argv: list) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: collect_traffic.py <owner/repo> <data_dir>")
    repo, data_dir = argv[1], argv[2]
    token = os.environ.get("TRAFFIC_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "TRAFFIC_TOKEN is empty. It needs a PAT with push access "
            "(fine-grained: Administration -> Read; classic: `repo`); the "
            "built-in GITHUB_TOKEN cannot read traffic.")

    os.makedirs(data_dir, exist_ok=True)
    views_path = os.path.join(data_dir, "views.json")
    paths_path = os.path.join(data_dir, "paths.json")

    views = merge_views(read_json(views_path, {}), fetch(repo, "traffic/views", token))
    snapshots = merge_paths(read_json(paths_path, {}),
                            fetch(repo, "traffic/popular/paths", token), today())

    write_json(views_path, views)
    write_json(paths_path, snapshots)
    with open(os.path.join(data_dir, "REPORT.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(render_report(repo, views, snapshots))

    when, rows = latest_paths(snapshots)
    print("%d days recorded, %d paths in the %s snapshot, %d of them wiki pages"
          % (len(views), len(rows), when, len(wiki_rows(rows))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
