"""The two knobs of the FakeDoh used by `test_doh_under_stress_e2e.py`: a latency on every answer and a first query that
is refused. Tested with plain Python clients, on HTTP/1.1 and on h2, like `test_the_fake_doh_answers.py`.

* `delay_s` / `delay_names`: each answer is held that long after its QUERY arrived (not only the connection set-up), and
  over h2 the answers of one connection are held independently, so the A and the AAAA of one lookup both come back after
  the delay, not one after the other (the second after twice the delay would turn a 2 s latency into a 4 s one);
* `fail_first`: the first query of each type for a name is refused as `close` (no answer: an h2 stream reset or a closed
  HTTP/1.1 connection), `http503` or `servfail`; every later one is served; the other names are untouched.
"""
from __future__ import annotations

import ssl
import time

import pytest

import _doh_servers as ds
from _doh_servers import DOH_HOST, DOH_PATH, FakeDoh, QTYPE_A, QTYPE_AAAA, encode_query
from test_the_fake_doh_answers import NAMES, _h1_request, _tls, read_response

pytestmark = pytest.mark.unit

DELAY = 0.6
#: Slack for a loaded machine: the answer must not come EARLIER than the delay, and not much later.
LATE = 1.5


# ------------------------------------------------------------------------------------------------- clients
def _h1_post(tls, name, qtype=QTYPE_A):
    started = time.monotonic()
    status, _headers, body = _h1_request(tls, "POST", DOH_PATH, encode_query(name, qtype), "application/dns-message")
    return status, body, time.monotonic() - started


def _h2_exchange(doh, messages):
    """Send every message as its own POST stream at once on one h2 connection. Returns one entry per message, in order:
    ``("reset", seconds)`` or ``(status, body, seconds)``, seconds counted from the moment all streams were sent."""
    h2_config = pytest.importorskip("h2.config")
    h2_connection = pytest.importorskip("h2.connection")
    h2_events = pytest.importorskip("h2.events")
    tls = _tls(("127.0.0.1", doh.port), alpn=["h2", "http/1.1"])
    assert tls.selected_alpn_protocol() == "h2"
    conn = h2_connection.H2Connection(config=h2_config.H2Configuration(client_side=True, header_encoding="utf-8"))
    conn.initiate_connection()
    ids = []
    for message in messages:
        stream_id = conn.get_next_available_stream_id()
        conn.send_headers(stream_id, [(":method", "POST"), (":scheme", "https"), (":authority", DOH_HOST),
                                      (":path", DOH_PATH), ("accept", "application/dns-message"),
                                      ("content-type", "application/dns-message"),
                                      ("content-length", str(len(message)))])
        conn.send_data(stream_id, message, end_stream=True)
        ids.append(stream_id)
    tls.sendall(conn.data_to_send())
    started = time.monotonic()
    status, body, done = {}, {i: b"" for i in ids}, {}
    tls.settimeout(10)
    try:
        while len(done) < len(ids):
            data = tls.recv(65536)
            assert data, "the server closed the connection"
            for event in conn.receive_data(data):
                if isinstance(event, h2_events.ResponseReceived):
                    status[event.stream_id] = dict(event.headers)[":status"]
                elif isinstance(event, h2_events.DataReceived):
                    body[event.stream_id] += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2_events.StreamEnded):
                    done[event.stream_id] = ("answer", time.monotonic() - started)
                elif isinstance(event, h2_events.StreamReset):
                    done[event.stream_id] = ("reset", time.monotonic() - started)
            tls.sendall(conn.data_to_send())
    finally:
        tls.close()
    out = []
    for i in ids:
        kind, seconds = done[i]
        out.append(("reset", seconds) if kind == "reset" else (status[i], body[i], seconds))
    return out


# ----------------------------------------------------------------------------------------------- the defaults
def test_the_defaults_change_nothing():
    with FakeDoh(NAMES, h2=False) as doh:
        assert (doh.delay_s, doh.delay_names, doh.fail_first) == (0.0, {}, {})
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            status, body, took = _h1_post(tls, "page-a.test")
        finally:
            tls.close()
        assert status == 200 and read_response(body)[4] and took < DELAY
        (answer,) = doh.answers
        assert (answer.name, answer.qtype, answer.via, answer.outcome) == ("page-a.test", QTYPE_A, "direct", "answered")
        assert answer.delay < DELAY


# ----------------------------------------------------------------------------------------------------- latency
def test_every_answer_over_http1_is_held_for_the_delay():
    with FakeDoh(NAMES, h2=False, delay_s=DELAY) as doh:
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            first = _h1_post(tls, "page-a.test")
            second = _h1_post(tls, "other.test", QTYPE_AAAA)
        finally:
            tls.close()
        for status, body, took in (first, second):
            assert status == 200 and DELAY - 0.05 <= took < DELAY + LATE, took
        assert read_response(first[1])[4], "the answer is the normal one, only late"
        assert [round(a.delay, 1) for a in doh.answers] == [round(DELAY, 1)] * 2
        assert all(a.delay >= DELAY - 0.05 for a in doh.answers)


def test_every_answer_over_h2_is_held_for_the_delay_and_the_a_and_the_aaaa_wait_together():
    with FakeDoh(NAMES, delay_s=DELAY) as doh:
        a, aaaa = _h2_exchange(doh, [encode_query("page-a.test", QTYPE_A), encode_query("page-a.test", QTYPE_AAAA)])
    for status, _body, took in (a, aaaa):
        assert status == "200" and DELAY - 0.05 <= took < 2 * DELAY + 0.3, (
            "both answers are held in parallel: %.2f s, not %.2f s" % (took, 2 * DELAY))
    assert read_response(a[1])[4] and not read_response(aaaa[1])[4]
    assert {x.qtype for x in doh.answers_for("page-a.test")} == {QTYPE_A, QTYPE_AAAA}
    assert all(x.delay >= DELAY - 0.05 for x in doh.answers)


def test_the_delay_of_a_name_only_holds_that_name_and_the_names_under_it():
    with FakeDoh(NAMES, delay_names={"page-a.test": DELAY}) as doh:
        slow, fast, below = _h2_exchange(doh, [encode_query("page-a.test"), encode_query("other.test"),
                                               encode_query("_80._https.page-a.test", QTYPE_A)])
    assert slow[2] >= DELAY - 0.05 and below[2] >= DELAY - 0.05
    assert fast[0] == "200" and fast[2] < DELAY, "an unrelated name is not held: %.2f s" % fast[2]


def test_the_delay_can_be_changed_while_the_server_runs():
    with FakeDoh(NAMES, h2=False) as doh:
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            assert _h1_post(tls, "page-a.test")[2] < DELAY
            doh.delay_s = DELAY
            assert _h1_post(tls, "page-a.test")[2] >= DELAY - 0.05
        finally:
            tls.close()


# ------------------------------------------------------------------------------------------------ first refused
def test_an_unknown_failure_kind_is_refused_at_construction():
    with pytest.raises(ValueError):
        FakeDoh(NAMES, fail_first={"page-a.test": "explode"})


def test_http503_once_over_http1_then_the_name_is_served_and_other_names_never_fail():
    with FakeDoh(NAMES, h2=False, fail_first={"page-a.test": "http503"}) as doh:
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            assert _h1_post(tls, "other.test")[0] == 200
            assert _h1_post(tls, "page-a.test")[0] == 503, "the first query of the name is refused"
            assert _h1_post(tls, "page-a.test")[0] == 200, "the second one is served"
            assert _h1_post(tls, "page-a.test")[0] == 200
        finally:
            tls.close()
        assert [a.outcome for a in doh.answers_for("page-a.test")] == ["http503", "answered", "answered"]
        assert len(doh.queries_for("page-a.test")) == 3, "a refused query is still a query"


def test_servfail_once_is_a_dns_answer_with_rcode_2():
    with FakeDoh(NAMES, h2=False, fail_first={"page-a.test": "servfail"}) as doh:
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            status, body, _took = _h1_post(tls, "page-a.test")
            _i, _f, rcode, name, answers = read_response(body)
            assert (status, rcode, name, answers) == (200, ds.RCODE_SERVFAIL, "page-a.test", [])
            assert read_response(_h1_post(tls, "page-a.test")[1])[4], "the second one has its address"
        finally:
            tls.close()


def test_close_once_over_http1_ends_the_connection_without_an_answer():
    with FakeDoh(NAMES, h2=False, fail_first={"page-a.test": "close"}) as doh:
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            with pytest.raises((AssertionError, OSError, ssl.SSLError)):
                _h1_post(tls, "page-a.test")
        finally:
            tls.close()
        tls = _tls(("127.0.0.1", doh.port), alpn=["http/1.1"])
        try:
            assert _h1_post(tls, "page-a.test")[0] == 200
        finally:
            tls.close()
        assert [a.outcome for a in doh.answers_for("page-a.test")] == ["close", "answered"]


def test_close_once_over_h2_resets_the_stream_and_keeps_the_connection():
    with FakeDoh(NAMES, fail_first={"page-a.test": "close"}) as doh:
        first, = _h2_exchange(doh, [encode_query("page-a.test")])
        second, = _h2_exchange(doh, [encode_query("page-a.test")])
    assert first[0] == "reset" and second[0] == "200"
    assert [a.outcome for a in doh.answers_for("page-a.test")] == ["close", "answered"]


def test_the_first_query_of_each_type_is_refused_so_a_whole_first_lookup_fails():
    """A and AAAA of the first lookup both fail, whichever arrives first; the second lookup is served in full."""
    with FakeDoh(NAMES, fail_first={"page-a.test": "http503"}) as doh:
        first = _h2_exchange(doh, [encode_query("page-a.test", QTYPE_A), encode_query("page-a.test", QTYPE_AAAA)])
        second = _h2_exchange(doh, [encode_query("page-a.test", QTYPE_A), encode_query("page-a.test", QTYPE_AAAA)])
    assert [r[0] for r in first] == ["503", "503"]
    assert [r[0] for r in second] == ["200", "200"]


def test_a_refusal_can_be_slow_too():
    with FakeDoh(NAMES, delay_s=DELAY, fail_first={"page-a.test": "http503"}) as doh:
        (refused,) = _h2_exchange(doh, [encode_query("page-a.test")])
    assert refused[0] == "503" and refused[2] >= DELAY - 0.05
    assert doh.answers_for("page-a.test")[0].delay >= DELAY - 0.05


def test_a_gateway_hands_the_knobs_over_with_the_connection():
    """Through a DohGateway the FakeDoh is the same object: the delay holds on the tunnelled road too."""
    from _doh_servers import DohGateway
    from test_the_fake_doh_answers import _socks_connect
    with FakeDoh(NAMES, h2=False, delay_s=DELAY) as doh, DohGateway("gw1", doh) as gw:
        s, code = _socks_connect(gw.port, ds.DOH_BOOTSTRAP, 443)
        assert code == 0
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(["http/1.1"])
        tls = context.wrap_socket(s, server_hostname=DOH_HOST)
        try:
            status, _body, took = _h1_post(tls, "page-a.test")
        finally:
            tls.close()
        assert status == 200 and took >= DELAY - 0.05
        assert doh.answers[0].via == "gw1"

