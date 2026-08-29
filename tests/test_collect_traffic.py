"""The traffic collector, whose whole value is what it does NOT lose.

GitHub keeps traffic for 14 days and then discards it. `collect_traffic.py`
exists to hold on to it, so the failure that matters here is not a crash - it is
a merge that silently drops or inflates numbers, because the output still looks
like a plausible report and nobody can check it against a source that no longer
exists. Two of those traps are specific and both are pinned below:

  - the API's newest day is TODAY, still in progress, so a later observation of
    the same date must WIN or every day freezes at however much of it had
    happened when the job first ran;
  - `/traffic/popular/paths` is a fourteen-day TOTAL, so adding successive daily
    responses counts every view about fourteen times and looks like growth.

The third is not a number at all: a wiki page outside the top ten is UNMEASURED,
not unvisited, and a report that prints zero for it invites someone to quote the
zero. `render_report` has to say which one it means.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

pytestmark = pytest.mark.unit

_REPO = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "collect_traffic.py"


def _load():
    spec = importlib.util.spec_from_file_location("collect_traffic", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ct = _load()


def _views(*days):
    """A `/traffic/views` response carrying (date, count, uniques) triples."""
    return {"views": [{"timestamp": f"{d}T00:00:00Z", "count": c, "uniques": u}
                      for d, c, u in days]}


def test_a_later_reading_of_the_same_day_replaces_the_earlier_one():
    """Today's number is partial when the job runs. If the first reading stuck,
    every day in the history would be frozen at whatever fraction of it had
    happened by 03:17 UTC - a permanent, invisible undercount."""
    h = ct.merge_views({}, _views(("2026-08-28", 3, 2)))
    assert h["2026-08-28"] == {"count": 3, "uniques": 2}
    h = ct.merge_views(h, _views(("2026-08-28", 11, 7)))
    assert h["2026-08-28"] == {"count": 11, "uniques": 7}


def test_days_the_api_no_longer_reports_are_kept():
    """The entire point. Once a date falls out of GitHub's 14-day window it is
    absent from every future response, and if that meant deletion this would be
    a rolling window with extra steps rather than a history."""
    h = {"2026-01-01": {"count": 5, "uniques": 4}}
    h = ct.merge_views(h, _views(("2026-08-28", 1, 1)))
    assert h["2026-01-01"] == {"count": 5, "uniques": 4}
    assert sorted(h) == ["2026-01-01", "2026-08-28"]


def test_path_snapshots_are_stored_by_date_and_never_added_up():
    """Each response is a 14-day total. Summing yesterday's and today's would
    double-count thirteen days of overlap; doing it daily for a fortnight
    multiplies everything by about fourteen and reads as a traffic spike."""
    p1 = [{"path": "/o/r/wiki/Home", "title": "Home", "count": 100, "uniques": 40}]
    p2 = [{"path": "/o/r/wiki/Home", "title": "Home", "count": 104, "uniques": 41}]
    s = ct.merge_paths({}, p1, "2026-08-28")
    s = ct.merge_paths(s, p2, "2026-08-29")
    assert sorted(s) == ["2026-08-28", "2026-08-29"]
    assert s["2026-08-29"][0]["count"] == 104        # not 204
    when, rows = ct.latest_paths(s)
    assert when == "2026-08-29" and rows[0]["count"] == 104


def test_the_latest_snapshot_is_by_date_not_insertion_order():
    """JSON round-trips through a dict; relying on ordering would make the
    ranking depend on how the file happened to be written."""
    s = {"2026-08-29": [{"path": "/b", "title": "", "count": 2, "uniques": 1}],
         "2026-08-28": [{"path": "/a", "title": "", "count": 9, "uniques": 5}]}
    when, rows = ct.latest_paths(s)
    assert when == "2026-08-29" and rows[0]["path"] == "/b"
    assert ct.latest_paths({}) == ("", [])


def test_wiki_rows_filters_to_the_wiki_and_ranks_by_views():
    rows = [
        {"path": "/o/r", "title": "repo", "count": 900, "uniques": 500},
        {"path": "/o/r/wiki/Home", "title": "Home", "count": 30, "uniques": 20},
        {"path": "/o/r/issues", "title": "issues", "count": 80, "uniques": 40},
        {"path": "/o/r/wiki/botd", "title": "botd", "count": 55, "uniques": 33},
    ]
    assert [r["path"] for r in ct.wiki_rows(rows)] == ["/o/r/wiki/botd", "/o/r/wiki/Home"]


def test_the_report_says_unmeasured_and_never_zero():
    """The failure this prevents is someone reading a table of zeroes and
    concluding the wiki is unread, when in fact the top ten was taken by the
    repo root and the issues list."""
    s = {"2026-08-29": [{"path": "/o/r", "title": "r", "count": 900, "uniques": 5}]}
    md = ct.render_report("o/r", {"2026-08-29": {"count": 900, "uniques": 5}}, s)
    assert "No wiki page reached the top ten" in md
    assert "unmeasured" in md
    # A zero in the wiki section is the specific thing that must not appear.
    wiki_section = md.split("## Wiki pages")[1].split("## What these numbers")[0]
    assert "| 0 |" not in wiki_section


def test_the_report_ranks_the_wiki_pages_when_they_are_there():
    s = {"2026-08-29": [
        {"path": "/o/r/wiki/Home", "title": "Home", "count": 30, "uniques": 20},
        {"path": "/o/r/wiki/botd", "title": "botd", "count": 55, "uniques": 33},
    ]}
    md = ct.render_report("o/r", {}, s)
    assert md.index("botd") < md.index("Home"), "must be ranked, not listed"
    assert "https://github.com/o/r/wiki/Home" in md
    assert "first run" in md  # no view days recorded yet, and it says so


def test_the_report_totals_every_recorded_day_not_the_last_fourteen():
    """The table is trimmed to 14 rows for readability; the totals must not be."""
    views = {f"2026-01-{d:02d}": {"count": 2, "uniques": 1} for d in range(1, 21)}
    md = ct.render_report("o/r", views, {})
    assert "**40** views" in md and "**20** unique" in md
    assert "across **20** recorded days" in md
    assert md.count("| 2 | 1 |") == 14  # trimmed table


def test_a_missing_or_truncated_data_file_does_not_stop_collection(tmp_path):
    """A half-written file from a cancelled run must not be the reason today's
    numbers are lost - the next write replaces it wholesale either way."""
    missing = tmp_path / "nope.json"
    assert ct.read_json(str(missing), {}) == {}
    truncated = tmp_path / "half.json"
    truncated.write_text('{"2026-08-01": {"count":', encoding="utf-8")
    assert ct.read_json(str(truncated), {}) == {}


def test_end_to_end_writes_the_three_files(tmp_path, monkeypatch):
    """`main` with the network stubbed: the merge, the two JSON files and the
    report, in the shapes the workflow commits."""
    monkeypatch.setenv("TRAFFIC_TOKEN", "x")
    calls = []

    def fake_fetch(repo, endpoint, token):
        calls.append(endpoint)
        if endpoint == "traffic/views":
            return _views(("2026-08-28", 4, 3), ("2026-08-29", 6, 5))
        return [{"path": "/o/r/wiki/Home", "title": "Home", "count": 10, "uniques": 8}]

    monkeypatch.setattr(ct, "fetch", fake_fetch)
    monkeypatch.setattr(ct, "today", lambda: "2026-08-29")
    out = tmp_path / "traffic"
    assert ct.main(["collect_traffic.py", "o/r", str(out)]) == 0
    assert calls == ["traffic/views", "traffic/popular/paths"]

    views = json.loads((out / "views.json").read_text(encoding="utf-8"))
    assert views["2026-08-29"] == {"count": 6, "uniques": 5}
    paths = json.loads((out / "paths.json").read_text(encoding="utf-8"))
    assert list(paths) == ["2026-08-29"]
    md = (out / "REPORT.md").read_text(encoding="utf-8")
    assert "**10** views" in md and "Home" in md

    # Second run, same day, richer numbers: the day is replaced, not added to,
    # and no second snapshot appears.
    def fuller(repo, endpoint, token):
        if endpoint == "traffic/views":
            return _views(("2026-08-29", 9, 7))
        return [{"path": "/o/r/wiki/Home", "title": "Home", "count": 12, "uniques": 9}]

    monkeypatch.setattr(ct, "fetch", fuller)
    ct.main(["collect_traffic.py", "o/r", str(out)])
    views = json.loads((out / "views.json").read_text(encoding="utf-8"))
    assert views["2026-08-29"] == {"count": 9, "uniques": 7}
    assert views["2026-08-28"] == {"count": 4, "uniques": 3}  # kept
    assert list(json.loads((out / "paths.json").read_text(encoding="utf-8"))) == \
        ["2026-08-29"]


def test_an_empty_token_fails_before_any_request(monkeypatch, tmp_path):
    """Told to the operator once, clearly, instead of a 403 traceback."""
    monkeypatch.setenv("TRAFFIC_TOKEN", "   ")
    monkeypatch.setattr(ct, "fetch", lambda *a: pytest.fail("must not call the API"))
    with pytest.raises(SystemExit) as e:
        ct.main(["collect_traffic.py", "o/r", str(tmp_path)])
    assert "TRAFFIC_TOKEN" in str(e.value) and "GITHUB_TOKEN cannot" in str(e.value)


def test_the_403_message_names_the_actual_remedy():
    """A 403 here is not a bug, it is the built-in GITHUB_TOKEN being used. The
    message has to say that, because `permissions:` cannot fix it."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "Administration -> Read" in src
    assert "TRAFFIC_TOKEN" in src
    assert "administration" in src


def test_the_workflow_uses_the_pat_and_not_the_builtin_token():
    """The mistake that would make this look broken forever: wiring
    GITHUB_TOKEN in, which cannot read traffic under any `permissions:`."""
    wf = (_REPO / ".github" / "workflows" / "wiki-traffic.yml").read_text(encoding="utf-8")
    assert "TRAFFIC_TOKEN: ${{ secrets.TRAFFIC_TOKEN }}" in wf
    assert "secrets.GITHUB_TOKEN" not in wf
    assert "contents: write" in wf          # it commits what it collects
    assert "workflow_dispatch" in wf        # runnable by hand on day one
