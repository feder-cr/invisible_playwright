"""The sink of the "nothing of a context leaves but through its proxy" e2e, held to what it says with no browser.

``_egress_sink.py`` reads the engine's own log and the witness, and answers four questions: did the browser connect or send direct, did it ask the
machine's resolver, did it ask in user context 0, and can the answer be trusted. Here each one is shown on a known input that makes it say yes, and
on a known-bad input that must NOT pass: a sink that answers "nothing" because it cannot see is the failure this file exists for.

Known-bad inputs: a log with no resolver start (blind), a Windows host whose second sink is not written (raises, never answers empty), a witness that
heard a datagram (a leak), a probe name asked with no user context.
"""
from __future__ import annotations

import pytest

import _egress_sink as sink_mod
import test_nothing_of_a_context_leaves_but_through_its_proxy_e2e as e2e

pytestmark = pytest.mark.unit

NONCE = "3fa91c"
LOG = """\
[Parent 100: Socket Thread]: D/nsHostResolver nsHostResolver::Init
[Parent 100: Socket Thread]: D/nsHostResolver Resolving host [turn-name-3fa91c.test]<^userContextId=6> type 0. [this=0x1]
[Parent 100: Socket Thread]: D/nsHostResolver Resolving host [turn-name-3fa91c.test]<> type 0. [this=0x1]
[Parent 100: Socket Thread]: D/nsHostResolver Resolving host [elsewhere.test]<> type 0. [this=0x1]
[Parent 100: DNS Resolver #1]: D/nsHostResolver DNS resolve task - Calling getaddrinfo for host [fetch-local-3fa91c.local].
[Parent 100: DNS Resolver #2]: D/nsHostResolver DNS resolve task - Calling getaddrinfo for host [detectportal.firefox.com].
"""


def _log(tmp_path, text=LOG, name="moz-100.log"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _on_linux(directory, nonce, trap):
    """The native sink as a Linux host answers it, whatever host the unit test runs on (a Windows one raises, by design: see below)."""
    return sink_mod.native_lookup_names(directory, nonce, trap, platform="linux")


class _Watcher:
    def __init__(self, pids=(1,), direct=()):
        self.watched_pids, self.process_names, self.direct = set(pids), ["firefox"], list(direct)


class _Witness:
    address = "192.0.2.77"
    port = 40000

    def __init__(self, tcp=(), udp=()):
        self._heard = {"tcp": list(tcp), "udp": list(udp)}

    def heard(self):
        return self._heard


class _Trap:
    def __init__(self, names):
        self._names = names

    def names(self):
        return list(self._names)


def test_the_log_gives_the_native_lookups_of_the_probe_and_no_other(tmp_path):
    directory = _log(tmp_path)
    assert sink_mod.engine_log_native_lookups(directory, NONCE) == ["fetch-local-3fa91c.local"]
    assert sorted(sink_mod.engine_log_native_lookups(directory)) == ["detectportal.firefox.com", "fetch-local-3fa91c.local"]


def test_the_log_gives_the_lookups_made_in_user_context_zero(tmp_path):
    directory = _log(tmp_path)
    assert sink_mod.engine_log_context_zero_lookups(directory, NONCE) == ["turn-name-3fa91c.test"]
    # A first-party domain is not a user context: the suffix says no userContextId, so it is context 0.
    other = _log(tmp_path, "Resolving host [x-3fa91c.test]<^firstPartyDomain=a.b> type 0.\n", "moz-101.log.child-1")
    assert "x-3fa91c.test" in sink_mod.engine_log_context_zero_lookups(other, NONCE)


def test_a_log_with_no_resolver_start_is_not_alive(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    assert sink_mod.engine_log_is_alive(empty) is False
    assert sink_mod.engine_log_is_alive(_log(tmp_path)) is True


def test_native_lookup_names_adds_the_trap_on_linux_and_ignores_it_elsewhere(tmp_path):
    directory = _log(tmp_path)
    trap = _Trap(["fetch-local-3fa91c.local", "turn-name-3fa91c.test", "other.test"])
    assert sink_mod.native_lookup_names(directory, NONCE, trap, platform="linux") == ["fetch-local-3fa91c.local", "turn-name-3fa91c.test"]
    assert sink_mod.native_lookup_names(directory, NONCE, trap, platform="darwin") == ["fetch-local-3fa91c.local"]


def test_native_lookup_names_on_windows_asks_the_second_sink_and_its_absence_is_loud(tmp_path):
    directory = _log(tmp_path)
    got = sink_mod.native_lookup_names(directory, NONCE, None, platform="win32", windows_sink=lambda nonce: ["from-the-event-log-3fa91c.test"])
    assert got == ["fetch-local-3fa91c.local", "from-the-event-log-3fa91c.test"]
    with pytest.raises(NotImplementedError):          # the known-bad input: a Windows host whose second sink is not written never answers "nothing"
        sink_mod.native_lookup_names(directory, NONCE, None, platform="win32")


def test_a_clean_probe_has_no_leak_and_a_trusted_sink(tmp_path):
    directory = _log(tmp_path, "nsHostResolver::Init\nResolving host [page-a.test]<^userContextId=6> type 0.\n")
    sink = sink_mod.EgressSink(_Witness(), _Watcher(), directory, NONCE, native=_on_linux)
    assert sink.blind_problems() == []
    assert sink.leak_problems() == []


def test_every_way_to_leave_is_a_leak_sentence(tmp_path):
    directory = _log(tmp_path)
    connection = type("C", (), {"process": "firefox", "protocol": "tcp", "remote": "192.0.2.77:40000", "status": "SYN_SENT"})()
    sink = sink_mod.EgressSink(_Witness(tcp=[("192.0.2.77", 5)], udp=[("192.0.2.77", 6, 20)]), _Watcher(direct=[connection]), directory, NONCE, native=_on_linux)
    said = " | ".join(sink.leak_problems())
    assert "reached direct" in said and "1 TCP" in said and "1 datagram" in said
    assert "watcher saw the browser connect" in said
    assert "fetch-local-3fa91c.local" in said and "user context 0" in said and "turn-name-3fa91c.test" in said


def test_a_connection_to_another_address_is_not_a_leak_of_the_probe(tmp_path):
    directory = _log(tmp_path, "nsHostResolver::Init\n")
    elsewhere = type("C", (), {"process": "firefox", "protocol": "tcp", "remote": "203.0.113.9:443", "status": "ESTABLISHED"})()
    sink = sink_mod.EgressSink(_Witness(), _Watcher(direct=[elsewhere]), directory, NONCE, native=_on_linux)
    assert sink.leak_problems() == []


def test_a_sink_that_sees_nothing_says_it_is_blind(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    blind = sink_mod.EgressSink(_Witness(), _Watcher(pids=()), empty, NONCE).blind_problems()
    assert any("watcher never saw a browser" in p for p in blind) and any("wrote no log" in p for p in blind)
    no_start = sink_mod.EgressSink(_Witness(), _Watcher(), _log(tmp_path, "Resolving host [a.test]<> type 0.\n"), NONCE).blind_problems()
    assert any("start of its resolver" in p for p in no_start)


def test_the_cases_are_unique_and_every_expectation_names_a_case_and_a_road():
    ids = [c.id for c in e2e.CASES]
    assert len(ids) == len(set(ids))
    assert {c.kind for c in e2e.CASES} <= set(e2e.PROBE_JS) and {c.kind for c in e2e.CASES} <= set(e2e.WAIT_MS)
    for engine, rows in e2e.EXPECTED.items():
        for case_id, what in rows.items():
            assert case_id in e2e.BY_ID, (engine, case_id)
            assert what.startswith("RED") and len(what) > 20, (engine, case_id, what)


def test_the_names_of_the_cases_are_all_in_the_zone_with_the_address_of_the_witness():
    lab = e2e.Lab(_Witness(), NONCE)
    zone = lab.zone
    assert all(address == _Witness.address for address in zone.values())
    for case in e2e.CASES:
        host = lab.host_of(case)
        assert (host in zone) == (case.by != "ip"), case.id
        if case.by == "ip":
            assert host == _Witness.address
        else:
            assert host.endswith(".local" if case.by == "local" else ".test") and NONCE in host


def test_the_lab_lets_the_doh_answer_with_the_private_address_of_this_host():
    """The names of a probe are answered with the address of THIS host, an RFC 1918 one on a LAN or a runner (10.1.0.150): the engine's DoH client
    drops such an answer unless ``network.trr.allow-rfc1918`` is true. Without it every probe by name resolves to nothing and never connects."""
    assert e2e.LAB_PREFS.get("network.trr.allow-rfc1918") is True


def test_only_a_probe_the_browser_refuses_has_no_road_to_show():
    """``turns:`` by IP is refused by the browser itself ("TLS requires FQDN"): it can show no CONNECT. Every other TCP probe by a SOCKS5 gateway or an
    HTTP proxy names the road it must have taken."""
    lab = e2e.Lab(_Witness(), NONCE)
    refused = [c.id for c in e2e.CASES if c.refused_by_the_browser]
    assert refused == ["turns_tcp-ip-socks5"]
    for case in e2e.CASES:
        done = e2e._the_work_was_done(case, lab, doh=None, proxy=None)
        if case.tcp and not case.refused_by_the_browser:
            assert done is not None, case.id


def test_the_push_page_says_the_step_it_reached():
    import _page_work as pw
    page = pw.CASES["push"]
    for stage in ("registering", "registered", "ready", "subscription-read"):
        assert '__pushStage = "%s"' % stage in page, stage
    # the request of a content process before the parent's PushServiceParent exists is dropped: the page asks again
    assert "__pushTries" in page and "Promise.race" in page
