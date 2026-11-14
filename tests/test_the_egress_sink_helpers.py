"""The sink of the "nothing of a context leaves but through its proxy" e2e, held to what it says with no browser.

``_egress_sink.py`` reads the engine's own log and the witness, and answers four questions: did the browser connect or send direct, did it ask the
machine's resolver, did it ask in user context 0, and can the answer be trusted. Here each one is shown on a known input that makes it say yes, and
on a known-bad input that must NOT pass: a sink that answers "nothing" because it cannot see is the failure this file exists for.

Known-bad inputs: a log with no resolver start or no file at all (blind: ``native_lookup_names`` raises, it never answers empty), a witness that heard a
datagram (a leak), a probe name asked with no user context. Windows reads the same log as Linux: there is no second sink to be missing.
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
    """The native sink as a Linux host answers it (the log, and the trap), whatever host the unit test runs on."""
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


def test_native_lookup_names_on_windows_is_the_engine_log_like_on_linux(tmp_path):
    """No second sink and no ``raise``: Windows answers from the same log, and a resolver trap (which does not run there) is never added."""
    directory = _log(tmp_path)
    trap = _Trap(["fetch-local-3fa91c.local", "only-the-trap-3fa91c.test"])
    assert sink_mod.native_lookup_names(directory, NONCE, None, platform="win32") == ["fetch-local-3fa91c.local"]
    assert sink_mod.native_lookup_names(directory, NONCE, trap, platform="win32") == ["fetch-local-3fa91c.local"]
    assert not hasattr(sink_mod, "_windows_dns_client_lookups")


def test_a_blind_log_raises_instead_of_answering_nothing(tmp_path):
    """The known-bad inputs: no log file, and a log of a process that never logged the start of its resolver. On every system."""
    empty = tmp_path / "none"
    empty.mkdir()
    for platform in ("linux", "win32"):
        with pytest.raises(sink_mod.BlindSink, match="wrote no log"):
            sink_mod.native_lookup_names(empty, NONCE, None, platform=platform)
        with pytest.raises(sink_mod.BlindSink, match="start of its resolver"):
            no_start = _log(tmp_path, "Resolving host [a-3fa91c.test]<> type 0." + chr(10), "moz-7.log")
            sink_mod.native_lookup_names(no_start, NONCE, None, platform=platform)


def test_the_parent_file_of_the_log_is_read_whatever_its_name(tmp_path):
    """On both systems the engine names the files ``moz--main.<pid>.log.moz_log`` (parent) and ``moz--child.<pid>.log.child-<n>.moz_log``: the
    lines of the parent are found, the empty child files are not a problem."""
    (tmp_path / "moz--child.9.log.child-1.moz_log").write_text("", encoding="utf-8")
    _log(tmp_path, LOG, "moz--main.100.log.moz_log")
    assert sink_mod.native_lookup_names(tmp_path, NONCE, None, platform="win32") == ["fetch-local-3fa91c.local"]
    assert sink_mod.engine_log_is_alive(tmp_path)


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


def test_a_leak_check_over_a_blind_log_says_so_instead_of_passing(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    sink = sink_mod.EgressSink(_Witness(), _Watcher(), empty, NONCE, native=_on_linux)
    assert any("native-lookup sink is blind" in p for p in sink.leak_problems())


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
    # the OCSP cases: a name and an IP, each with a reason on firefox-63 and none on firefox-64 (the fix of the engine)
    assert e2e.OCSP_CASES == ("name", "ip")
    assert set(e2e.EXPECTED_OCSP) == {"firefox-63", "firefox-64"} and e2e.EXPECTED_OCSP["firefox-64"] == {}
    assert set(e2e.EXPECTED_OCSP["firefox-63"]) == set(e2e.OCSP_CASES)
    assert all(what.startswith("RED") and len(what) > 20 for what in e2e.EXPECTED_OCSP["firefox-63"].values())


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


def test_the_names_of_the_ocsp_cases_carry_the_nonce_and_stay_out_of_the_zone_of_the_cases():
    lab = e2e.Lab(_Witness(), NONCE)
    assert lab.ocsp_name == "ocsp-3fa91c.lab.test"
    assert {lab.tls_name(by) for by in e2e.OCSP_CASES} == {"tls-name-3fa91c.lab.test", "tls-ip-3fa91c.lab.test"}
    # the TLS page stands at the loopback and the responder name at the witness: the test adds them to the DoH itself, so the zone of the probes stays one address
    assert lab.ocsp_name not in lab.zone and all(lab.tls_name(by) not in lab.zone for by in e2e.OCSP_CASES)


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


def test_a_process_that_reads_no_filter_while_it_starts_is_read_again_and_one_that_stays_unfiltered_is_not_dropped(monkeypatch):
    import test_the_socket_process_is_sandboxed_e2e as sandbox
    reads = {4090: iter(["0", "0", "2"])}
    monkeypatch.setattr(sandbox, "_seccomp", lambda pid: "2" if pid != 4090 else next(reads[4090]))
    assert sandbox._settled_seccomp([3905, 4090], seconds=5) == {3905: "2", 4090: "2"}
    monkeypatch.setattr(sandbox, "_seccomp", lambda pid: "0" if pid == 4090 else "2")
    assert sandbox._settled_seccomp([3905, 4090], seconds=0.6) == {3905: "2", 4090: "0"}


def test_the_filter_wait_of_the_sandbox_test_is_bounded():
    import test_the_socket_process_is_sandboxed_e2e as sandbox
    assert 0 < sandbox.STARTUP_SECONDS <= 15
