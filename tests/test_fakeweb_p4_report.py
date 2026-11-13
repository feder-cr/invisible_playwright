"""The reader of the profile P4 report (scripts/fakeweb_p4_report.py) and the shape of the workflow that runs it.

The verdict of the job is made here, so it is tested here: a difference on a sound walk is red, a broken control is red with
another message and is not counted as a difference of ours, a clean run is green, and a sendBeacon reported with another
resource type than `beacon` is red. With a traced stock walk the trace verdict is "same as stock" per kind of request.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fakeweb_p4_report as report  # noqa: E402


def _walk(rounds=2, steps=3):
    cand, stock = [], []
    for r in range(1, rounds + 1):
        for n in range(1, steps + 1):
            rec = {"round": r, "n": n, "name": "step %d" % n, "ok": True, "idle": n != 2}
            cand.append(dict(rec))
            stock.append(dict(rec))
    return {"profile": "p4", "candidate": cand, "stock": stock, "fails": [], "broken_control": []}


def test_a_clean_run_is_green_and_every_step_is_equal():
    text, code = report.journey(_walk())
    assert code == 0
    assert text.count("| equal |") == 6
    assert "Verdict: green" in text and "sound" in text


def test_a_difference_on_a_sound_walk_is_red():
    data = _walk()
    data["candidate"][1]["idle"] = True      # step 2 of walk 1: stock is busy, ours idles
    data["fails"] = [(1, 2, "idle", False, True)]
    text, code = report.journey(data)
    assert code == 1
    assert "different (idle)" in text
    assert "FAIL  round 1 step 2 idle: stock=False candidate=True" in text
    assert "RED, ours differs from stock" in text


def test_a_broken_control_is_red_with_its_own_message_and_is_not_a_difference_of_ours():
    data = _walk()
    data["stock"][0]["idle"] = False
    data["broken_control"] = [(1, 1, "idle", "idle=False, declared idle")]
    data["fails"] = [(1, 1, "idle", False, True)]      # the difference exists only because the control moved
    text, code = report.journey(data)
    assert code == 2
    assert "BROKEN CONTROL" in text and "broken control (not a difference of ours)" in text


def test_a_difference_on_a_sound_walk_and_a_broken_control_on_another_is_both():
    data = _walk()
    data["broken_control"] = [(1, 1, "ok", "boom")]
    data["fails"] = [(2, 3, "title", "a", "b")]
    assert report.journey(data)[1] == 3


def test_a_missing_report_is_red(tmp_path, capsys):
    assert report.main(["journey", str(tmp_path / "nothing.json")]) == 4
    assert "RED" in capsys.readouterr().out


def _client(method, url, typ):
    return {"ev": "request", "method": method, "url": url, "type": typ, "t": 0}


def _server(method, path, ident="", n="", src=""):
    return {"method": method, "path": path, "id": ident, "n": n, "src": src}


def _trace(beacon_type="beacon", server_ka=1, client_ka=1):
    client = [_client("POST", "/beacon?src=load&n=1", beacon_type), _client("POST", "/beacon?src=ad&n=4", beacon_type),
              _client("POST", "/beacon?src=load&n=3", "fetch")]
    client += [_client("GET", "/r?kind=json&id=ka1", "fetch")] * client_ka
    server = [_server("POST", "/beacon", n="1", src="load"), _server("POST", "/beacon", n="3", src="load")]
    server += [_server("GET", "/r", ident="ka1")] * server_ka
    return {"candidate": [{"client_events": client, "server_events": server}]}


def test_trace_is_green_when_every_sendbeacon_is_a_beacon_and_no_keepalive_is_unreported():
    text, code = report.trace(_trace())
    assert code == 0, text
    assert "| sendBeacon | 2 | beacon: 2 | 1 |" in text
    assert "| keepalive POST | 1 | fetch: 1 | 1 |" in text      # told from sendBeacon by n=3 of src=load
    assert "| keepalive fetch | 1 | fetch: 1 | 1 |" in text


def test_trace_is_red_when_a_sendbeacon_has_another_resource_type():
    text, code = report.trace(_trace(beacon_type="ping"))
    assert code == 1
    assert "other than `beacon`" in text


def test_trace_is_red_when_the_server_saw_a_keepalive_the_client_never_reported():
    text, code = report.trace(_trace(server_ka=2, client_ka=1))
    assert code == 1
    assert "keepalive fetch: the server saw 2, the client reported 1" in text


def test_trace_is_red_when_there_is_no_sendbeacon_at_all():
    assert report.trace({"candidate": [{"client_events": [], "server_events": []}]})[1] == 1


def _records(beacon_type="beacon", server_ka=1, client_ka=1, beacons=2):
    """One step record: `beacons` sendBeacon requests of resource type `beacon_type` (all reported and all seen),
    one keepalive POST, and `client_ka` reported / `server_ka` seen keepalive fetches."""
    return _trace(beacon_type, server_ka, client_ka)["candidate"]


def _both(stock=None, ours=None):
    return {"stock": _records(**(stock or {})), "candidate": _records(**(ours or {}))}


def test_trace_with_a_stock_walk_is_green_when_the_types_are_equal():
    text, code = report.trace(_both())
    assert code == 0, text
    assert "| sendBeacon | 2 | beacon: 2 | 1 | 2 | beacon: 2 | 1 |" in text


def test_trace_with_a_stock_walk_does_not_compare_exact_counts():
    ours = _records()
    ours[0]["client_events"] = ours[0]["client_events"] + [_client("POST", "/beacon?src=ad&n=5", "beacon")]
    assert report.trace({"stock": _records(), "candidate": ours})[1] == 0


def test_trace_with_a_stock_walk_is_red_when_ours_reports_other_where_stock_says_beacon():
    text, code = report.trace(_both(ours={"beacon_type": "other"}))
    assert code == 1
    assert "sendBeacon" in text and "other" in text and "beacon" in text and "stock reported {beacon}" in text


def test_trace_with_a_stock_walk_is_green_when_stock_does_the_same_odd_thing():
    assert report.trace(_both(stock={"beacon_type": "ping"}, ours={"beacon_type": "ping"}))[1] == 0


def test_trace_with_a_stock_walk_is_red_when_ours_leaves_more_unreported_than_stock():
    text, code = report.trace(_both(stock={"server_ka": 2, "client_ka": 2}, ours={"server_ka": 3, "client_ka": 1}))
    assert code == 1
    assert "keepalive fetch: the server saw 2 request(s) ours never reported, stock left 0 unreported" in text


def test_trace_with_a_stock_walk_is_green_when_both_leave_the_same_unreported():
    text, code = report.trace(_both(stock={"server_ka": 3, "client_ka": 1}, ours={"server_ka": 3, "client_ka": 1}))
    assert code == 0, text
    assert "| keepalive fetch | 1 | fetch: 1 | 3 | 1 | fetch: 1 | 3 |" in text


def test_trace_with_a_stock_walk_that_reported_no_sendbeacon_is_a_broken_control():
    stock = [{"client_events": [_client("GET", "/r?kind=json&id=ka1", "fetch")], "server_events": []}]
    text, code = report.trace({"stock": stock, "candidate": _records()})
    assert code == 2
    assert "BROKEN CONTROL" in text and "not a difference of ours" in text


def test_a_stock_synthesized_for_no_stock_has_no_events_and_keeps_the_absolute_rules():
    data = _trace()
    data["stock"] = [{"round": 1, "n": 1, "ok": True, "idle": True}]
    assert report.trace(data)[1] == 0
    data = _trace(beacon_type="ping")
    data["stock"] = [{"round": 1, "n": 1, "ok": True, "idle": True}]
    assert report.trace(data)[1] == 1


def test_the_site_tags_events_with_the_walk_and_a_late_event_is_collected_with_its_step():
    """The lost-event case, without a browser: a request fired on pagehide reaches the server after the step's own read
    but before the next step is set. Reading once at the end of the walk, by walk and step, finds it; the walks do not mix."""
    from fakeweb import journey_site

    state = journey_site.State()
    assert state.begin_walk() == 1
    state.set_step(1)
    state.record(0, "GET", "/", {})
    first_read = state.events_of(1, 1)                      # what the old read at the end of step 1 saw
    state.record(1, "POST", "/beacon", {"t": ["pagehide"], "n": ["1"]})      # the pagehide beacon, after that read
    state.set_step(2)
    state.record(0, "GET", "/article/1", {})
    assert [e["path"] for e in first_read] == ["/"]
    assert [e["path"] for e in state.events_of(1, 1)] == ["/", "/beacon"]
    assert [e["path"] for e in state.events_of(1, 2)] == ["/article/1"]
    assert state.begin_walk() == 2
    state.set_step(1)
    state.record(0, "GET", "/", {})
    assert len(state.events_of(2, 1)) == 1 and len(state.events_of(1, 1)) == 2
    assert all(e["walk"] in (1, 2) for e in state.events)


def test_the_site_records_what_the_report_reads_and_the_tracer_keeps_what_the_client_reports():
    """The two ends of --trace, without a browser: the hits the site logs (with the parameters the classifier needs) and the
    request events the tracer keeps; both must be read back by the report as the same kinds."""
    import urllib.request

    from fakeweb import journey_site, profile_p4

    a, _o, stop = journey_site.start((0, 0))
    try:
        journey_site.STATE.set_step(3)
        urllib.request.urlopen(urllib.request.Request(a + "/beacon?src=load&n=3", data=b"x", method="POST")).read()
        urllib.request.urlopen(a + "/r?kind=json&id=ka1").read()
        events = [e for e in journey_site.STATE.events if e["step"] == 3]
    finally:
        stop()
    assert [report._server_kind(e) for e in events] == ["keepalive POST", "keepalive fetch"]

    class Request:
        def __init__(self, method, url, typ):
            self.method, self.url, self.resource_type = method, url, typ

    handlers = {}

    class Context:
        def on(self, name, fn):
            handlers[name] = fn

    tracer = profile_p4.Tracer(Context(), events=True)
    beacon = Request("POST", a + "/beacon?src=ad&n=9", "beacon")
    handlers["request"](beacon)
    assert tracer.snapshot() == ["beacon beacon?src=ad&n=9"]
    handlers["requestfinished"](beacon)
    taken = tracer.take_events()
    assert [e["ev"] for e in taken] == ["request", "finished"] and report._client_kind(taken[0]) == "sendBeacon"
    assert tracer.take_events() == [] and tracer.snapshot() == []
    assert profile_p4.Tracer(Context()).events is None      # without --trace nothing is kept


# the workflow ------------------------------------------------------------------------------------------------------------
WORKFLOW = (ROOT / ".github" / "workflows" / "fakeweb-p4.yml").read_text(encoding="utf-8")


def test_the_workflow_is_one_job_on_one_linux_runner():
    assert len(re.findall(r"^  [a-z0-9_-]+:\n    name:", WORKFLOW, re.MULTILINE)) == 1
    assert WORKFLOW.count("runs-on:") == 1 and "runs-on: ubuntu-24.04" in WORKFLOW


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses: (\S+)", WORKFLOW)
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_the_workflow_starts_on_a_push_of_its_own_file_and_on_dispatch():
    assert re.search(r"on:\n  push:\n    paths:\n      - '\.github/workflows/fakeweb-p4\.yml'\n  workflow_dispatch:", WORKFLOW)
