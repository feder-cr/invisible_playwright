"""What the served browser does about its memory: a malloc trim that is ON and a probe that is OFF.

The trim: on Linux (glibc) `malloc_trim(0)` after a context closes and after a message of
64 KiB or more is sent; INVPW_MALLOC_TRIM=0 turns it off; elsewhere, or with a libc that has
no such function, nothing is called and nothing is said. The probe: INVPW_MEM_LOG=<file>,
off by default. No browser: the stand-in Browser of
`test_the_remote_server_serves_the_protocol.py`, a real WebSocket, a fake libc.

Known-bad: with the trim off by default the default-on tests fail; with a warning on the
platforms that cannot trim, or an exception from a libc without the function, the quiet
tests fail; with the outbox sum reading 0 the growth test fails.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

pytest.importorskip("websockets")

from websockets.asyncio.client import connect  # noqa: E402

from invisible_playwright._juggler import memprobe, remote  # noqa: E402
from invisible_playwright._juggler.dispatcher import Dispatcher  # noqa: E402
from invisible_playwright._juggler.server import LocalUtilsDispatcher  # noqa: E402

pytestmark = pytest.mark.unit

KEYS = {"t", "event", "uss_mb", "rss_mb", "registry_objects", "contexts", "clients",
        "outbox_total_bytes", "outbox_max_messages", "gc_counts", "malloc"}


class FakeContext(Dispatcher):
    TYPE = "BrowserContext"
    METHODS = {"close": "op_close", "big": "op_big"}

    def op_close(self, params):
        self.dispose()

    def op_big(self, params):
        return {"value": "z" * int(params.get("size", 100000))}


class FakeBrowser(Dispatcher):
    TYPE = "Browser"
    METHODS = {"newContext": "op_new_context"}

    def __init__(self, server, parent):
        super().__init__(server, parent, {"version": "firefox/1", "name": "firefox",
                                          "browserName": "firefox"})

    def op_new_context(self, params):
        return {"context": FakeContext(self.server, self).channel}


def make_endpoint():
    server = remote._SharedServer()
    browser_type = remote.SharedBrowserType(server)
    LocalUtilsDispatcher(server)
    browser = FakeBrowser(server, browser_type)
    return remote.RemoteEndpoint(server, browser_type, browser)


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("INVPW_MEM_LOG", raising=False)
    monkeypatch.delenv("INVPW_MEM_LOG_INTERVAL", raising=False)
    monkeypatch.delenv("INVPW_MALLOC_TRIM", raising=False)


def linux_with(monkeypatch, libc):
    monkeypatch.setattr(memprobe, "_is_linux", lambda: True)
    monkeypatch.setattr(memprobe, "_load_libc", lambda: libc)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def call(ws, msg_id, guid, method, params=None):
    await ws.send(json.dumps({"id": msg_id, "guid": guid, "method": method,
                              "params": params or {}, "metadata": {}}))
    seen = []
    while True:
        raw = await ws.recv()
        message = json.loads(raw)
        seen.append(raw)
        if message.get("id") == msg_id:
            return seen


def exchange(endpoint):
    async def go():
        out = []
        async with connect(endpoint.ws_endpoint) as ws:
            out += await call(ws, 1, "", "initialize", {})
            made = await call(ws, 2, endpoint.browser.guid, "newContext", {})
            out += made
            guid = json.loads(made[-1])["result"]["context"]["guid"]
            out += await call(ws, 3, guid, "big", {"size": 200000})
            out += await call(ws, 4, guid, "close")
        return out
    return run(go())


# ── off when asked, and the wire does not change ────────────────────────────

def test_with_the_trim_off_and_no_probe_nothing_is_built_and_the_libc_is_not_asked(
        clean_env, monkeypatch):
    def forbidden():
        raise AssertionError("the libc was loaded with the trim off")
    monkeypatch.setenv("INVPW_MALLOC_TRIM", "0")
    monkeypatch.setattr(memprobe, "_is_linux", lambda: True)
    monkeypatch.setattr(memprobe, "_load_libc", forbidden)
    endpoint = make_endpoint()
    assert endpoint.probe is None and endpoint.trim is None
    endpoint.start()
    try:
        exchange(endpoint)
    finally:
        endpoint.close()


def test_the_wire_is_the_same_byte_for_byte_with_the_trim_and_the_probe_on(
        clean_env, monkeypatch, tmp_path):
    def bytes_of(on: bool):
        if on:
            monkeypatch.delenv("INVPW_MALLOC_TRIM", raising=False)
            monkeypatch.setenv("INVPW_MEM_LOG", str(tmp_path / "m.jsonl"))
            linux_with(monkeypatch, FakeLibc())
        else:
            monkeypatch.setenv("INVPW_MALLOC_TRIM", "0")
            monkeypatch.delenv("INVPW_MEM_LOG", raising=False)
        endpoint = make_endpoint()
        endpoint.start()
        try:
            return exchange(endpoint)
        finally:
            endpoint.close()
    off = bytes_of(False)
    on = bytes_of(True)
    assert off == on


# ── the probe ──────────────────────────────────────────────────────────────

def test_the_probe_writes_a_line_with_the_expected_keys_and_one_per_context_close(
        clean_env, monkeypatch, tmp_path):
    log = tmp_path / "mem.jsonl"
    monkeypatch.setenv("INVPW_MEM_LOG", str(log))
    monkeypatch.setenv("INVPW_MEM_LOG_INTERVAL", "0.05")
    endpoint = make_endpoint()
    assert endpoint.probe is not None and endpoint.probe.interval == 0.05
    endpoint.start()
    try:
        exchange(endpoint)
        time.sleep(0.3)
    finally:
        endpoint.close()
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert rows and all(set(r) == KEYS for r in rows)
    assert {"tick", "context_closed"} <= {r["event"] for r in rows}
    assert all(isinstance(r["t"], float) for r in rows)
    ticks = [r for r in rows if r["event"] == "tick"]
    assert len(ticks) >= 2
    assert all(b["t"] >= a["t"] for a, b in zip(rows, rows[1:]))
    assert set(rows[0]["gc_counts"]) == {"generations", "objects"}
    closed = [r for r in rows if r["event"] == "context_closed"]
    assert len(closed) == 1


class SlowSocket:
    """A socket whose send waits until it is released: a client that does not read."""

    def __init__(self):
        self.release = threading.Event()
        self.sent = []

    async def send(self, text):
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        self.sent.append(text)


def test_the_outbox_grows_with_a_client_that_does_not_read_and_returns_to_zero(clean_env):
    endpoint = make_endpoint()
    probe = memprobe.MemProbe(endpoint, "unused")
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    socket = SlowSocket()
    try:
        client = asyncio.run_coroutine_threadsafe(
            _make_client(endpoint, socket, loop), loop).result(5)
        endpoint.clients.append(client)
        for _ in range(11):
            client.post("a" * 1000)
        deadline = time.time() + 5
        while probe.sample("tick")["outbox_max_messages"] < 10 and time.time() < deadline:
            time.sleep(0.01)
        grown = probe.sample("tick")
        # one text is in the writer's hands, in `send`: ten wait in the queue
        assert grown["outbox_total_bytes"] == 10 * 1000
        assert grown["outbox_max_messages"] == 10
        assert grown["clients"] == 1
        socket.release.set()
        deadline = time.time() + 5
        while probe.sample("tick")["outbox_total_bytes"] and time.time() < deadline:
            time.sleep(0.01)
        drained = probe.sample("tick")
        assert drained["outbox_total_bytes"] == 0 and drained["outbox_max_messages"] == 0
        assert len(socket.sent) == 11
    finally:
        socket.release.set()
        loop.call_soon_threadsafe(client.stop_pump)
        time.sleep(0.1)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


async def _make_client(endpoint, socket, loop):
    client = remote.RemoteClient(endpoint, socket, loop, {})
    client.pump_task = asyncio.create_task(client.pump())
    return client


# ── the trim ───────────────────────────────────────────────────────────────

class FakeLibc:
    def __init__(self):
        self.calls = []

    def malloc_trim(self, pad):
        self.calls.append(pad)
        return 1


def test_the_trim_is_on_by_default_after_a_big_reply_and_at_a_context_close(clean_env, monkeypatch):
    libc = FakeLibc()
    linux_with(monkeypatch, libc)
    endpoint = make_endpoint()
    assert endpoint.trim is not None
    endpoint.start()
    try:
        async def go():
            async with connect(endpoint.ws_endpoint) as ws:
                await call(ws, 1, "", "initialize", {})
                made = await call(ws, 2, endpoint.browser.guid, "newContext", {})
                guid = json.loads(made[-1])["result"]["context"]["guid"]
                await call(ws, 3, guid, "big", {"size": 10})
                small = len(libc.calls)
                await call(ws, 4, guid, "big", {"size": 200000})
                big = len(libc.calls)
                await call(ws, 5, guid, "close")
                return small, big
        small, big = run(go())
        assert small == 0, "a small reply must not trim"
        assert big >= 1 and set(libc.calls) == {0}
        deadline = time.time() + 5
        while len(libc.calls) <= big and time.time() < deadline:
            time.sleep(0.01)
        assert len(libc.calls) > big, "the context close did not trim"
    finally:
        endpoint.close()


def test_INVPW_MALLOC_TRIM_0_never_trims(clean_env, monkeypatch):
    libc = FakeLibc()
    linux_with(monkeypatch, libc)
    monkeypatch.setenv("INVPW_MALLOC_TRIM", "0")
    endpoint = make_endpoint()
    assert endpoint.trim is None
    endpoint.start()
    try:
        exchange(endpoint)
    finally:
        endpoint.close()
    assert libc.calls == []


def test_off_linux_nothing_is_called_and_nothing_is_said(clean_env, monkeypatch):
    import warnings
    monkeypatch.setattr(memprobe, "_is_linux", lambda: False)
    monkeypatch.setattr(memprobe, "_load_libc", lambda: pytest.fail("loaded off Linux"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert memprobe.trimmer_from_env() is None
        endpoint = make_endpoint()
        endpoint.start()
        try:
            exchange(endpoint)           # the server works
        finally:
            endpoint.close()


@pytest.mark.parametrize("libc", [object(), "no-libc"])
def test_a_libc_without_malloc_trim_is_not_an_error_and_the_server_works(
        clean_env, monkeypatch, libc):
    """musl has no malloc_trim; a libc that cannot be loaded is the same case."""
    import warnings
    monkeypatch.setattr(memprobe, "_is_linux", lambda: True)

    def load():
        if libc == "no-libc":
            raise OSError("libc.so.6: cannot open shared object file")
        return libc
    monkeypatch.setattr(memprobe, "_load_libc", load)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert memprobe.trimmer_from_env() is None
        endpoint = make_endpoint()
        assert endpoint.trim is None
        endpoint.start()
        try:
            exchange(endpoint)
        finally:
            endpoint.close()
