"""A pytest plugin for the cold-pool hunt (scripts/cold_pool_hunt.py): the wall-clock time of what the test gateway sees.

    python -m pytest -p _hunt_plugin ...      with HUNT_EVENTS=<file>

Appends one JSON line per event to HUNT_EVENTS: {"t": epoch seconds, "kind": ..., ...}. `connect` is a SOCKS5 CONNECT reaching the gateway,
`associate` a UDP ASSOCIATE reaching it, `stun` a STUN request the fake server answers, `test_start` / `test_end` bracket the test. The gateway is
only observed: nothing it does changes.
"""
import json
import os
import threading
import time

_LOCK = threading.Lock()


def _emit(kind, **fields):
    path = os.environ.get("HUNT_EVENTS")
    if not path:
        return
    with _LOCK, open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(t=time.time(), kind=kind, **fields)) + "\n")


def pytest_configure(config):
    import _socks_servers as sv

    connect, associate = sv.Gateway._connect, sv.Gateway._associate

    def seen_connect(self, c, host, port):
        _emit("connect", host=host, port=port)
        return connect(self, c, host, port)

    def seen_associate(self, c):
        _emit("associate")
        return associate(self, c)

    sv.Gateway._connect, sv.Gateway._associate = seen_connect, seen_associate
    stun = getattr(sv, "FakeStun", None)
    if stun is not None and hasattr(stun, "_serve"):
        serve = stun._serve

        def seen_serve(self, *a, **k):
            _emit("stun_server_up")
            return serve(self, *a, **k)

        stun._serve = seen_serve


def pytest_runtest_setup(item):
    _emit("test_start", test=item.nodeid)


def pytest_runtest_teardown(item):
    _emit("test_end", test=item.nodeid)
