"""The resolver trap counts the questions of the user that runs the test, and only those (no browser, no root, no network).

Run 43188313516: /etc/resolv.conf is the machine's, and on a CI runner the agent of the runner asks for github.com,
api.github.com and its own orchestrator through it while the trap is up: 6 tests of the realness suite and 1 of the DoH
suite failed with "the browser asked the resolver of the machine: github.com ...". The trap now reads, for each question,
the user that owns the socket it came from (/proc/net on Linux) and counts only the user the test runs as. No list of names.
"""
from __future__ import annotations

import socket

import pytest

from _doh_servers import QTYPE_A, encode_query
from _leaks import (Asker, ResolverTrap, descends_from, owner_of_the_peer, row_from_proc_net, uid_from_proc_net,
                    under_a_browser, who_asked)

pytestmark = pytest.mark.unit

#: The head and two rows of /proc/net/udp as the kernel writes them (uid is the 8th column).
UDP_TABLE = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
  120: 0100007F:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 12345 2 0000000000000000 0
  305: 00000000:C350 00000000:0000 07 00000000:00000000 00:00000000 00000000  1001        0 67890 2 0000000000000000 0
"""


def _ask(trap: ResolverTrap, name: str):
    """One UDP question from this process to the trap; the answer, or None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(5)
        client.sendto(encode_query(name, QTYPE_A), ("127.0.0.1", trap.port))
        try:
            return client.recvfrom(4096)[0]
        except socket.timeout:
            return None


def test_the_owner_of_a_socket_is_read_from_the_row_of_its_local_port():
    assert uid_from_proc_net(UDP_TABLE, 0xC350) == 1001
    assert uid_from_proc_net(UDP_TABLE, 0x35) == 0


def test_a_port_with_no_row_has_no_owner_and_a_garbled_row_is_skipped():
    assert uid_from_proc_net(UDP_TABLE, 4242) is None
    assert uid_from_proc_net("head\nnot a row\n  1: nonsense x y z a b c d\n", 80) is None


def test_the_owner_of_a_peer_is_none_off_linux_and_for_a_socket_that_is_gone(monkeypatch):
    monkeypatch.setattr("_leaks.sys.platform", "win32")
    assert owner_of_the_peer("udp", ("127.0.0.1", 40000)) is None
    monkeypatch.setattr("_leaks.sys.platform", "linux")

    def no_proc(*args, **kwargs):
        raise OSError("no /proc here")

    monkeypatch.setattr("_leaks.open", no_proc, raising=False)
    assert owner_of_the_peer("udp", ("127.0.0.1", 40000)) is None


def test_a_question_of_the_user_of_the_test_is_counted():
    with ResolverTrap({"page-d.test": "127.0.0.1"}, port=0, rewrite_resolv_conf=False, only_uid=1000,
                      owner_of=lambda protocol, peer: 1000) as trap:
        assert _ask(trap, "page-d.test")
        assert trap.names() == ["page-d.test"] and trap.foreign == []


def test_a_question_of_another_user_is_answered_and_listed_not_counted():
    """The agent of the runner (another user) asks for github.com: the browser is not blamed for it."""
    with ResolverTrap({"github.com": "127.0.0.1"}, port=0, rewrite_resolv_conf=False, only_uid=0,
                      owner_of=lambda protocol, peer: 1001) as trap:
        assert _ask(trap, "github.com"), "a question of another user is still answered"
        assert trap.names() == [] and trap.queries == []
        assert [(q.name, a.uid) for q, a in trap.foreign] == [("github.com", 1001)]
        assert trap.describe() == "nothing [and 1 question(s) of other processes, not counted: github.com from pid ? (uid 1001) ]"


def test_a_question_whose_socket_is_gone_counts_as_the_browsers():
    with ResolverTrap(port=0, rewrite_resolv_conf=False, only_uid=0, owner_of=lambda protocol, peer: None) as trap:
        _ask(trap, "page-x.test")
        assert trap.names() == ["page-x.test"] and trap.foreign == []


def test_the_kernel_names_this_process_as_the_owner_of_its_own_socket():
    """On Linux the row of /proc/net says the uid of this process for a socket this process holds; elsewhere there is
    no such table and the answer is None (the question then counts as the browser's)."""
    import os
    import sys
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as mine:
        mine.bind(("127.0.0.1", 0))
        owner = owner_of_the_peer("udp", mine.getsockname())
    assert owner == (os.geteuid() if sys.platform.startswith("linux") else None)


def test_the_owner_of_a_question_that_waits_for_its_answer_is_always_found():
    """Measured: a client that waits for the answer (every resolver of a browser and of a runner does) has its socket open
    when the trap reads the table, which it does on receipt, before answering: 200 questions, 0 unreadable. A client that
    sends and closes at once (nothing resolves like that) loses its row; its question then counts as the browser's, the
    side a leak would be seen from (measured on Linux: 739 of 3000 such datagrams arrived, all unreadable)."""
    import os
    import sys
    unreadable = []

    def reading(protocol, peer):
        uid = owner_of_the_peer(protocol, peer)
        if uid is None:
            unreadable.append(peer)
        return uid

    with ResolverTrap({"page-w.test": "127.0.0.1"}, port=0, rewrite_resolv_conf=False,
                      only_uid=os.geteuid() if hasattr(os, "geteuid") else 0, owner_of=reading) as trap:
        answered = sum(1 for _ in range(200) if _ask(trap, "page-w.test"))
    assert answered == 200 and len(trap.queries) == 200 and trap.foreign == []
    assert len(unreadable) == (200 if not sys.platform.startswith("linux") else 0), unreadable[:3]


# ----------------------------------------------------------------------------------------------- by the tree of processes
#: A machine: the test is pid 100 (its core makes its own requests); the browser is 400, started by the test, with a content
#: process 777 under it; the agent of the runner is 555 under the provisioner 90 - as ROOT too, the user a filter cannot tell
#: from the root browser.
PARENT = {400: 100, 777: 400, 555: 90, 90: 1, 650: 100}
BROWSERS = {400}


def _trap(owner, answers, **kwargs):
    return ResolverTrap(answers, port=0, rewrite_resolv_conf=False, only_uid=0, root_pid=100, parent_of_pid=PARENT.get,
                        is_browser=lambda pid: pid in BROWSERS, owner_of=owner, **kwargs)


def test_a_process_is_the_root_or_one_of_its_descendants():
    assert descends_from(100, 100, PARENT.get) and descends_from(777, 100, PARENT.get) and descends_from(400, 100, PARENT.get)
    assert not descends_from(555, 100, PARENT.get)
    assert not descends_from(300, 100, PARENT.get), "a process with no known parent is not a descendant"


def test_the_browser_and_what_it_starts_are_under_a_browser_and_the_test_and_the_rest_are_not():
    def under(pid):
        return under_a_browser(pid, 100, PARENT.get, lambda p: p in BROWSERS)
    assert under(400) and under(777)
    assert not under(100), "the test process is not the browser"
    assert not under(650), "a process the test started that is not a browser (a download) is not the browser"
    assert not under(555) and not under(300)


def test_a_row_of_proc_net_gives_the_user_and_the_inode():
    assert row_from_proc_net(UDP_TABLE, 0xC350) == (1001, 67890)
    assert row_from_proc_net(UDP_TABLE, 4242) is None


def test_a_root_process_that_is_not_the_browsers_is_foreign_even_with_the_same_user():
    """The agent of the runner runs as root, like the browser under sudo: the user cannot separate them, the tree does."""
    with _trap(lambda protocol, peer: Asker(0, 555, "Runner.Provisioner: /opt/agent/provisioner"), {"github.com": "127.0.0.1"}) as trap:
        assert _ask(trap, "github.com")
        assert trap.names() == [] and [q.name for q, _ in trap.foreign] == ["github.com"]
        say = trap.describe()
        assert "github.com from pid 555" in say and "Runner.Provisioner" in say and "not counted" in say


def test_the_test_process_itself_and_its_own_python_are_not_the_browser():
    """The core of the test process asks github.com for the latest GeoIP tag: a question of the test, not of the browser."""
    for pid in (100, 650):
        with _trap(lambda protocol, peer, pid=pid: Asker(0, pid, "python"), {"github.com": "127.0.0.1"}) as trap:
            _ask(trap, "github.com")
            assert trap.names() == [] and len(trap.foreign) == 1, pid


def test_the_browser_and_what_it_starts_are_counted():
    for pid in (400, 777):
        with _trap(lambda protocol, peer, pid=pid: Asker(0, pid, "firefox"), {"page-d.test": "127.0.0.1"}) as trap:
            assert _ask(trap, "page-d.test")
            assert trap.names() == ["page-d.test"] and trap.foreign == [], pid


def test_a_process_that_cannot_be_traced_is_judged_by_its_user():
    with _trap(lambda protocol, peer: Asker(1001, None, ""), {"a.test": "127.0.0.1"}) as trap:
        _ask(trap, "a.test")
        assert trap.names() == [] and len(trap.foreign) == 1
    with _trap(lambda protocol, peer: Asker(0, None, ""), {"b.test": "127.0.0.1"}) as trap:
        _ask(trap, "b.test")
        assert trap.names() == ["b.test"] and trap.foreign == []


def test_the_kernel_names_this_process_as_the_asker_of_its_own_socket():
    """On Linux the socket of this process leads, by its inode, to this process and its command; elsewhere nobody is found."""
    import os
    import sys
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as mine:
        mine.bind(("127.0.0.1", 0))
        asker = who_asked("udp", mine.getsockname())
    if sys.platform.startswith("linux"):
        assert asker is not None and asker.pid == os.getpid() and asker.uid == os.geteuid() and asker.command != ""
    else:
        assert asker is None
