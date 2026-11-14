"""The sink of "nothing of a context leaves but through its proxy": what a test reads to know that a request went AROUND the proxy (no pytest in here).

A context with a proxy and a session with none: the default context is then the machine's own network, so "everything that leaves" cannot be
judged (the browser's own background resolves and connects there, and may). What a test judges is what ITS probe made the browser do: every
name of a probe carries a nonce (``fetch-name-3fa91c.test``) and every address a probe uses is the one of a WITNESS no proxy of the test
forwards to (the gateways refuse a CONNECT past loopback and relay no datagram), so whatever arrives at the witness arrived direct. The sink
has four parts, each of which a test proves is not blind:

* the WITNESS (``_witness.Witness``) at an address of this host: a TCP connection or a datagram that reaches it came from the browser, direct;
* the WATCHER (``_leaks.Watcher``): a connection of a browser process to that address, seen in ``/proc`` or the connection table;
* the NATIVE LOOKUPS of the probe's names: ``native_lookup_names`` is the ONE function a test calls. On Linux and on Windows alike it is the
  engine's own log (``nsHostResolver.cpp`` writes ``DNS resolve task - Calling getaddrinfo for host [<name>]`` before every native lookup; on
  Windows that is the call into the system resolver too) and, where the resolver trap runs (Linux, root), the trap's questions. The lines are
  in the PARENT process's file (``moz--main.<pid>.log.moz_log``); the child files are empty, under the sandbox of both systems (measured on a
  runner of each, run 43574494194). A log that holds no resolver start is blind and ``native_lookup_names`` raises ``BlindSink``: it never
  answers "nothing" for a sink that cannot see;
* the LOOKUPS IN USER CONTEXT 0: the same log says in which context each name was asked (``Resolving host [<name>]<^userContextId=N>``): a
  probe name asked with no ``userContextId`` is a lookup that lost the context of its page, whichever road it then took.

The log is the engine's, not a hook of the test: ``MOZ_LOG=sync,nsHostResolver:5`` and ``MOZ_LOG_FILE=<dir>/moz-%PID.log`` give one file per
process (``moz-<pid>.log`` and ``moz-<pid>.log.child-<n>``), as ``test_the_hosts_file_of_the_host_is_never_read_e2e.py`` reads it.
"""
from __future__ import annotations

import glob
import os
import re
import secrets
import sys
from typing import Callable, Dict, Iterable, List, Optional, Sequence

MOZ_LOG = "sync,nsHostResolver:5"
LOG_FILE_NAME = "moz-%PID.log"

_NATIVE = re.compile(r"Calling getaddrinfo for host \[([^\]]+)\]")
_RESOLVING = re.compile(r"Resolving host \[([^\]]+)\]<([^>]*)>")
_STARTED = "nsHostResolver::Init"


def new_nonce() -> str:
    """Six hex digits: unique in a run, and a legal label."""
    return secrets.token_hex(3)


def log_files(directory) -> List[str]:
    return sorted(glob.glob(os.path.join(str(directory), "moz-*.log*")))


def _lines(directory) -> Iterable[str]:
    for path in log_files(directory):
        with open(path, "rb") as handle:
            for raw in handle:
                yield raw.decode("utf-8", "replace")


def engine_log_native_lookups(directory, nonce: Optional[str] = None) -> List[str]:
    """The names the engine looked up with the machine's resolver (``getaddrinfo``), in the order it did, only those with ``nonce`` when given."""
    out = []
    for line in _lines(directory):
        found = _NATIVE.search(line)
        if found and (nonce is None or nonce in found.group(1)):
            out.append(found.group(1))
    return out


def engine_log_context_zero_lookups(directory, nonce: Optional[str] = None) -> List[str]:
    """The names asked of the resolver in a request with NO user context (user context 0: the suffix has no ``userContextId``)."""
    out = []
    for line in _lines(directory):
        found = _RESOLVING.search(line)
        if found and "userContextId" not in found.group(2) and (nonce is None or nonce in found.group(1)):
            out.append(found.group(1))
    return out


def engine_log_is_alive(directory) -> bool:
    """The control of the log sink: some process logged the start of its resolver. A log with no such line says nothing."""
    return any(_STARTED in line for line in _lines(directory))


class BlindSink(RuntimeError):
    """The engine's log cannot say anything: no file, or no process logged the start of its resolver."""


def native_lookup_names(directory, nonce: str, trap=None, platform: Optional[str] = None) -> List[str]:
    """Every name with ``nonce`` that the machine's resolver was asked for by the browser: the ONE function every test calls, on every system.

    The engine's log on Linux and on Windows; plus ``trap`` (a ``_leaks.ResolverTrap``, Linux and root) when given. Raises ``BlindSink`` when
    the log is empty or holds no resolver start. ``platform`` is for the unit test of this function."""
    platform = platform or sys.platform
    if not log_files(directory):
        raise BlindSink("the engine wrote no log in %s: the native-lookup sink is blind" % directory)
    if not engine_log_is_alive(directory):
        raise BlindSink("no process of the browser logged the start of its resolver (%r) in %s: the native-lookup sink is blind" % (_STARTED, directory))
    names = set(engine_log_native_lookups(directory, nonce))
    if trap is not None and platform.startswith("linux"):
        names |= {n for n in trap.names() if nonce in n}
    return sorted(names)


class EgressSink:
    """What a probe of one case left behind, and the verdicts the test makes of it.

    ``witness`` is the ``_witness.Witness`` of the case, ``watcher`` the ``_leaks.Watcher`` (stopped), ``directory`` the folder of the engine's
    log, ``nonce`` the one of the probe's names, ``trap`` the resolver trap or None."""

    def __init__(self, witness, watcher, directory, nonce: str, trap=None, native: Callable[..., List[str]] = native_lookup_names):
        self.witness, self.watcher, self.directory, self.nonce, self.trap = witness, watcher, directory, nonce, trap
        self._native = native

    def arrivals(self) -> Dict[str, list]:
        """Everything the witness heard: nothing is forwarded to it by any proxy of the tests, so each one is a send around the proxy."""
        return self.witness.heard()

    def direct_connections(self) -> list:
        return [c for c in self.watcher.direct if c.remote.rsplit(":", 1)[0] == self.witness.address]

    def native_lookups(self) -> List[str]:
        return self._native(self.directory, self.nonce, self.trap)

    def context_zero_lookups(self) -> List[str]:
        return engine_log_context_zero_lookups(self.directory, self.nonce)

    def blind_problems(self) -> List[str]:
        """The reasons this sink cannot be trusted: a green over a sink that sees nothing proves nothing."""
        out = []
        if not self.watcher.watched_pids:
            out.append("the watcher never saw a browser process (it saw: %s)" % self.watcher.process_names)
        if not log_files(self.directory):
            out.append("the engine wrote no log in %s: the native-lookup sink is blind (MOZ_LOG / MOZ_LOG_FILE did not reach it)" % self.directory)
        elif not engine_log_is_alive(self.directory):
            out.append("no process of the browser logged the start of its resolver (%r): the native-lookup sink is blind" % _STARTED)
        return out

    def leak_problems(self) -> List[str]:
        """The sentences of every leak the sink saw, empty when the probe stayed home."""
        out = []
        heard = self.arrivals()
        if heard["tcp"] or heard["udp"]:
            out.append("the witness at %s was reached direct: %d TCP connection(s) from %s, %d datagram(s) from %s"
                       % (self.witness.address, len(heard["tcp"]), sorted({h[0] for h in heard["tcp"]}), len(heard["udp"]),
                          sorted({h[0] for h in heard["udp"]})))
        seen = self.direct_connections()
        if seen:
            out.append("the watcher saw the browser connect to %s without the proxy: %s"
                       % (self.witness.address, "; ".join("%s %s -> %s %s" % (c.process, c.protocol, c.remote, c.status) for c in seen)))
        try:
            native = self.native_lookups()
        except BlindSink as exc:
            out.append(str(exc))
            native = []
        if native:
            out.append("the machine's resolver was asked for %s (a lookup of a proxied context took the native road)" % native)
        zero = self.context_zero_lookups()
        if zero:
            out.append("%s was looked up in user context 0, not in the context of its page" % sorted(set(zero)))
        return out


def named_connects(gateway_connects: Sequence, is_address: Callable[[str], bool], allowed_names: Sequence[str] = ()) -> List[tuple]:
    """The CONNECTs of a SOCKS5 gateway that carry a NAME (the browser must resolve and connect by address), except ``allowed_names``."""
    return [(h, p) for h, p in gateway_connects if not is_address(h) and h not in allowed_names]
