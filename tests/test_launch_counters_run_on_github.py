"""The launch counter is recorded by GitHub Actions, never by a machine of ours.

GitHub keeps only the running total of the counter asset, so a day's launches
exist only if the total is written down that day. The snapshot used to run
from a scheduled task on one PC with a token file beside it; when that task
went, the badge stopped and three days of launches were lost with no error
anywhere. These tests pin the replacement: a scheduled workflow, the script it
runs, and the arithmetic the history has always used.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import launch_counters as lc  # noqa: E402

pytestmark = pytest.mark.unit

WORKFLOW = ROOT / ".github" / "workflows" / "counters.yml"

# docs/badges/launches.svg on the badges branch as the old renderer left it.
BADGE_461 = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="157" height="20" role="img" aria-label="browser launches: 4.61M">\n'
    "<title>browser launches: 4.61M</title>\n"
    '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>\n'
    '<clipPath id="r"><rect width="157" height="20" rx="3" fill="#fff"/></clipPath>\n'
    '<g clip-path="url(#r)">\n'
    '<rect width="116" height="20" fill="#555"/>\n'
    '<rect x="116" width="41" height="20" fill="#007ec6"/>\n'
    '<rect width="157" height="20" fill="url(#s)"/>\n'
    "</g>\n"
    '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">\n'
    '<text x="58.0" y="15" fill="#010101" fill-opacity=".3">browser launches</text>\n'
    '<text x="58.0" y="14">browser launches</text>\n'
    '<text x="136.5" y="15" fill="#010101" fill-opacity=".3">4.61M</text>\n'
    '<text x="136.5" y="14">4.61M</text>\n'
    "</g>\n"
    "</svg>"
).encode()


def test_the_snapshot_is_a_scheduled_workflow_with_no_personal_secret():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "schedule:" in text and "cron:" in text
    assert "scripts/launch_counters.py" in text
    assert "ref: badges" in text and "HEAD:badges" in text
    assert "secrets." not in text, "it must run on the built-in token alone"


def test_the_script_reads_no_local_file_for_its_token():
    src = (ROOT / "scripts" / "launch_counters.py").read_text(encoding="utf-8")
    assert "C:/" not in src and ".ghtok" not in src


def test_the_sum_gives_back_a_recorded_total():
    # 2026-11-04 row: usage_counter 2758110, usage_counter_v3 937317.
    assert lc.launches_total(2_758_110, 937_317) == 4_616_723


def test_the_badge_is_byte_for_byte_the_old_one():
    assert lc.render(lc.format_count(4_616_723), lc.BADGES[0]) == BADGE_461


def test_a_gap_is_not_spread_over_the_missing_days():
    hist = {"2026-11-03": {"launches_total": 10}, "2026-11-04": {"launches_total": 70},
            "2026-11-08": {"launches_total": 400}}
    assert lc.launches_per_day(hist) == [("2026-11-03", 60)]


def test_a_second_run_the_same_day_keeps_the_first_value(tmp_path):
    p = tmp_path / "h.csv"
    assert lc.append_snapshot(p, "2026-11-08", {"stars": 1, "launches_total": 5})
    assert lc.append_snapshot(p, "2026-11-08", {"stars": 2, "launches_total": 6}) == []
    assert lc.read_history(p)["2026-11-08"] == {"stars": 1, "launches_total": 5}


def test_a_run_writes_history_daily_and_badges(tmp_path):
    def api(path):
        if path.startswith("/repos/feder-cr/firefox_antidetect_patch/releases?"):
            if path.endswith("&page=1"):
                return [{"tag_name": "firefox-56", "assets": [
                    {"name": "x.tar.gz", "download_count": 3},
                    {"name": "checksums.txt", "download_count": 9}]}]
            return []
        if path == "/repos/feder-cr/firefox_antidetect_patch/releases/tags/usage-counter":
            return {"assets": [{"download_count": 2_758_110}]}
        if path == "/repos/feder-cr/invisible_playwright/releases/tags/usage-counter":
            return {"assets": [{"download_count": 937_317}]}
        if path == "/repos/feder-cr/invisible_playwright":
            return {"stargazers_count": 2554}
        raise AssertionError(path)

    h, d, b = tmp_path / "history.csv", tmp_path / "daily.csv", tmp_path / "badges"
    h.write_text("date,key,value\n2026-11-07,launches_total,4556723\n", encoding="utf-8")
    import datetime as dt
    assert lc.run(api, h, d, b, dt.date(2026, 11, 8)) == 0
    row = lc.read_history(h)["2026-11-08"]
    assert row["launches_total"] == 4_616_723 and row["firefox-56"] == 3
    assert d.read_text(encoding="utf-8").splitlines() == ["day_utc,launches", "2026-11-07,60000"]
    assert (b / "launches.svg").read_bytes() == BADGE_461
    assert b"Stars: 2.55k" in (b / "stars.svg").read_bytes()
