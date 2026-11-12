#!/usr/bin/env python3
"""Daily snapshot of the public counters, and the two README badges drawn from it.

    python scripts/launch_counters.py --history H.csv --daily D.csv --badges DIR
    python scripts/launch_counters.py --selftest

Run once a day by .github/workflows/counters.yml, which keeps the files on the
unprotected `badges` branch. Nothing here needs a person or a machine of ours:
every input is a public GitHub API answer and every output is a file on that
branch.

WHAT A LAUNCH IS. Every browser process the engine starts makes one GET to a
fixed release asset (platform/launch-counter in the engine series), and GitHub
counts the fetches as that asset's `download_count`. GitHub keeps only the
running total, never a history, which is why this snapshot exists: a day's
launches are the difference between two consecutive daily totals, and a day
nobody recorded is lost for good.

THE TOTAL IS A SUM, and its terms are fixed history:
  * LAUNCHES_BASELINE_V3 is everything counted before the counter moved to the
    two live assets below; those earlier assets no longer exist.
  * usage_counter_v3 is the wrapper repo's asset, counted from zero. Engines
    whose core does not declare the address ping it.
  * usage_counter_v4 is the engine repo's asset above the value it already held
    when it came back into use (USAGE_COUNTER_RESUMED_AT). Current cores point
    every session at it.
The two populations are disjoint, so the launches are the sum.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ENGINE_REPO = "oss-invisible/firefox_antidetect_patch"
WRAPPER_REPO = "oss-invisible/playwright"
COUNTER_TAG = "usage-counter"

LAUNCHES_BASELINE_V3 = 2_084_136
USAGE_COUNTER_RESUMED_AT = 1_162_840

Api = Callable[[str], object]


def github_api(token: str | None) -> Api:
    def get(path: str):
        req = urllib.request.Request("https://api.github.com" + path)
        req.add_header("Accept", "application/vnd.github+json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    return get


def _releases(api: Api, repo: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        batch = api(f"/repos/{repo}/releases?per_page=100&page={page}")
        if not batch:
            return out
        out.extend(batch)
        page += 1


def _counter(api: Api, repo: str) -> int:
    rel = api(f"/repos/{repo}/releases/tags/{COUNTER_TAG}")
    return sum(a["download_count"] for a in rel["assets"])


def launches_total(usage_counter: int, usage_counter_v3: int) -> int:
    return (LAUNCHES_BASELINE_V3 + usage_counter_v3
            + max(0, usage_counter - USAGE_COUNTER_RESUMED_AT))


def collect(api: Api) -> dict[str, int]:
    """Every key the history has always carried, read fresh."""
    out: dict[str, int] = {}
    binaries = 0
    for r in _releases(api, ENGINE_REPO):
        tag = r["tag_name"]
        if tag == COUNTER_TAG:
            continue
        n = sum(a["download_count"] for a in r["assets"]
                if a["name"].endswith((".zip", ".tar.gz")))
        if n or tag.startswith("firefox-"):
            out[tag] = n
            binaries += n
    out["total_binaries"] = binaries
    out["usage_counter"] = _counter(api, ENGINE_REPO)
    out["usage_counter_v3"] = _counter(api, WRAPPER_REPO)
    out["usage_counter_v4"] = max(0, out["usage_counter"] - USAGE_COUNTER_RESUMED_AT)
    out["launches_total"] = launches_total(out["usage_counter"], out["usage_counter_v3"])
    out["stars"] = api(f"/repos/{WRAPPER_REPO}")["stargazers_count"]
    return out


# ── history ─────────────────────────────────────────────────────────────────

def read_history(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                out.setdefault(row["date"], {})[row["key"]] = int(float(row["value"]))
    return out


def append_snapshot(path: Path, day: str, data: dict[str, int]) -> list[str]:
    """Append today's rows. Idempotent per (date, key): a rerun the same day
    writes only keys that are not there yet, and never a second value."""
    seen = set(read_history(path).get(day, {}))
    fresh = {k: v for k, v in data.items() if k not in seen}
    if not fresh:
        return []
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["date", "key", "value"])
        ff = sorted((k for k in fresh if re.fullmatch(r"firefox-\d+", k)),
                    key=lambda k: int(k.split("-")[1]))
        rest = sorted(k for k in fresh if k not in ff)
        for k in ff + rest:
            w.writerow([day, k, fresh[k]])
    return list(fresh)


def launches_per_day(history: dict[str, dict[str, int]]) -> list[tuple[str, int]]:
    """(day counted, launches) for each pair of snapshots ONE day apart.

    The snapshot dated D is taken just after midnight UTC, so the difference
    from D-1 is the launches of calendar day D-1. A gap between snapshots is
    not a day and is never spread over the missing days.
    """
    dates = sorted(d for d in history if "launches_total" in history[d])
    out = []
    for a, b in zip(dates, dates[1:]):
        if (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days != 1:
            continue
        delta = history[b]["launches_total"] - history[a]["launches_total"]
        if delta > 0:
            out.append((a, delta))
    return out


def write_daily(path: Path, history: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["day_utc", "launches"])
        w.writerows(launches_per_day(history))


# ── badges ──────────────────────────────────────────────────────────────────

VALUE_PAD = 6
COLOUR_LABEL = "#555"
CHAR_W = {".": 4.0, "M": 10.0, "k": 6.0}   # Verdana 11px advances
DIGIT_W = 7.0
SAFE_VALUE = re.compile(r"^[0-9]+(\.[0-9]+)?[kM]?$")


@dataclass(frozen=True)
class Badge:
    label: str
    label_box: int          # measured from the committed SVG
    colour: str
    file_name: str
    key: str


BADGES = [
    Badge("browser launches", 116, "#007ec6", "launches.svg", "launches_total"),
    Badge("Stars", 43, "#e8a317", "stars.svg", "stars"),
]


def format_count(n: int) -> str:
    """Two decimals, TRUNCATED: an adoption figure is never rounded up."""
    if n < 1000:
        return str(n)
    scaled, suffix = (n / 1000, "k") if n < 1_000_000 else (n / 1_000_000, "M")
    text = f"{int(scaled * 100) / 100:.2f}".rstrip("0").rstrip(".")
    return text + suffix


def render(value: str, badge: Badge) -> bytes:
    if not SAFE_VALUE.match(value):
        raise ValueError(f"refusing to draw an unexpected value: {value!r}")
    box = int(sum(CHAR_W.get(c, DIGIT_W) for c in value) + VALUE_PAD)
    total = badge.label_box + box
    lx, vx = badge.label_box / 2, badge.label_box + box / 2
    aria = f"{badge.label}: {value}"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20"'
        f' role="img" aria-label="{aria}">',
        f"<title>{aria}</title>",
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0"'
        ' stop-color="#bbb" stop-opacity=".1"/><stop offset="1"'
        ' stop-opacity=".1"/></linearGradient>',
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3"'
        ' fill="#fff"/></clipPath>',
        '<g clip-path="url(#r)">',
        f'<rect width="{badge.label_box}" height="20" fill="{COLOUR_LABEL}"/>',
        f'<rect x="{badge.label_box}" width="{box}" height="20"'
        f' fill="{badge.colour}"/>',
        f'<rect width="{total}" height="20" fill="url(#s)"/>',
        "</g>",
        '<g fill="#fff" text-anchor="middle"'
        ' font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">',
        f'<text x="{lx:.1f}" y="15" fill="#010101" fill-opacity=".3">{badge.label}</text>',
        f'<text x="{lx:.1f}" y="14">{badge.label}</text>',
        f'<text x="{vx:.1f}" y="15" fill="#010101" fill-opacity=".3">{value}</text>',
        f'<text x="{vx:.1f}" y="14">{value}</text>',
        "</g>",
        "</svg>",
    ]
    return "\n".join(lines).encode("utf-8")


def latest(history: dict[str, dict[str, int]], key: str) -> int:
    days = [d for d in history if key in history[d]]
    if not days:
        raise SystemExit(f"no {key} in the history")
    return history[max(days)][key]


# The big row at the top of the README is drawn by shields.io from these
# files (its "endpoint" badge), so the numbers stay ours and the look is theirs.
@dataclass(frozen=True)
class Endpoint:
    label: str
    colour: str
    logo: str
    file_name: str
    key: str


ENDPOINTS = [
    Endpoint("browser launches", "FF7139", "firefoxbrowser", "launches.json", "launches_total"),
    Endpoint("engine downloads", "2EA44F", "github", "downloads.json", "total_binaries"),
]


def endpoint_json(value: str, e: Endpoint) -> bytes:
    if not SAFE_VALUE.match(value):
        raise ValueError(f"refusing to publish an unexpected value: {value!r}")
    return (json.dumps({
        "schemaVersion": 1, "label": e.label, "message": value,
        "color": e.colour, "namedLogo": e.logo, "logoColor": "white",
        "cacheSeconds": 3600,
    }, indent=2) + "\n").encode("utf-8")


def write_badges(directory: Path, history: dict[str, dict[str, int]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for b in BADGES:
        (directory / b.file_name).write_bytes(
            render(format_count(latest(history, b.key)), b))
    for e in ENDPOINTS:
        (directory / e.file_name).write_bytes(
            endpoint_json(format_count(latest(history, e.key)), e))


# ── entry point ─────────────────────────────────────────────────────────────

def run(api: Api, history: Path, daily: Path, badges: Path, today: dt.date) -> int:
    day = today.isoformat()
    before = read_history(history)
    written = append_snapshot(history, day, collect(api))
    after = read_history(history)
    write_daily(daily, after)
    write_badges(badges, after)
    print(f"[counters] {day}: wrote {len(written)} keys, "
          f"launches_total={after[day]['launches_total']}")
    previous = sorted(d for d in before if d < day)
    if previous and (today - dt.date.fromisoformat(previous[-1])).days > 1:
        # Recorded anyway, but a hole is a lost measurement: say it in the run.
        print(f"::warning::no snapshot between {previous[-1]} and {day}: "
              f"those days cannot be split")
    return 0


def selftest() -> int:
    import tempfile
    fails = []
    # The row of 2026-11-04 as recorded: the sum must give its total back.
    if launches_total(2_758_110, 937_317) != 4_616_723:
        fails.append("sum")
    if format_count(4_616_723) != "4.61M" or format_count(2554) != "2.55k":
        fails.append("format")
    hist = {"2026-11-01": {"launches_total": 100}, "2026-11-02": {"launches_total": 160},
            "2026-11-04": {"launches_total": 300}}
    if launches_per_day(hist) != [("2026-11-01", 60)]:
        fails.append("gap")
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "h.csv"
        append_snapshot(p, "2026-11-08", {"stars": 1})
        if append_snapshot(p, "2026-11-08", {"stars": 2}) or read_history(p)["2026-11-08"]["stars"] != 1:
            fails.append("idempotent")
        # The README's top row reads these two files through shields.io.
        h = {"2026-11-11": {"launches_total": 5_319_939, "total_binaries": 232_924, "stars": 2569}}
        write_badges(Path(t) / "b", h)
        got = json.loads((Path(t) / "b" / "launches.json").read_text(encoding="utf-8"))
        if (got.get("schemaVersion"), got.get("label"), got.get("message")) != (1, "browser launches", "5.31M"):
            fails.append("endpoint launches")
        got = json.loads((Path(t) / "b" / "downloads.json").read_text(encoding="utf-8"))
        if got.get("message") != "232.92k":
            fails.append("endpoint downloads")
    print("selftest:", "OK" if not fails else "FAIL " + ", ".join(fails))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--history", type=Path)
    ap.add_argument("--daily", type=Path)
    ap.add_argument("--badges", type=Path)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.history and a.daily and a.badges):
        ap.error("--history, --daily and --badges are required")
    api = github_api(os.environ.get("GITHUB_TOKEN"))
    return run(api, a.history, a.daily, a.badges, dt.datetime.now(dt.timezone.utc).date())


if __name__ == "__main__":
    sys.exit(main())
