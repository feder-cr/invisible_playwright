import faulthandler
import http.server
import os
import random
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

import pytest

# ── The watchdog that says WHERE it is stuck, while it is still stuck ─────────
#
# The e2e hangs in 12% of CI runs (4 out of 33, from 2026-08-01 to 08-13) and
# until 2026-08-13 it left nothing behind: the job was killed by the 40-minute
# ceiling, so it came back `cancelled` and not `failure`, and the following
# steps never ran. `--timeout 420 --timeout-method thread` in run_e2e.py now
# kills it at 7 minutes printing the stack, but seven minutes is a long time
# and a single stack alone does not say whether the process is STUCK or just
# slow.
#
# This is what says it: at 150 seconds inside ONE test it prints the stack of
# every thread, and repeats it. Two identical dumps mean stuck; two different
# dumps mean slow. Those are opposite diagnoses and no other tool tells them
# apart.
#
# 150 seconds because the slowest legitimate test measured on CI is
# `test_webgl_readpixels_no_masking_signature` at 94.4 s, and the second at
# 11.4: on a healthy run it prints nothing, and on a slow runner it prints at
# most once, which is output and not a failure.
#
# To a FILE, not stderr, and this is not a detail: with pytest's default
# capture it redirects file descriptors 1 and 2 at the OS level, so even
# writing to the original `sys.__stderr__` ends up in the capture buffer -
# which pytest shows only once a failure has occurred, and here the process
# is being KILLED. Tested: with the threshold at 3 seconds on a test stuck in
# `accept()`, the same call prints four stacks when run by hand and ZERO
# under pytest. A file survives the kill and gets uploaded as an artifact on
# CI.
_WATCHDOG_S = float(os.environ.get("INVPW_TEST_WATCHDOG_S", "150"))
_WATCHDOG_FILE = os.environ.get(
    "INVPW_TEST_WATCHDOG_FILE",
    str(Path(tempfile.gettempdir()) / "invpw-e2e-watchdog.log"))
_watchdog_handle = None


def _watchdog_open():
    """Opens the file on the FIRST occurrence, not at session start.

    Only `e2e` tests are watched: they are the only ones that open a browser,
    and the one measured hang lives there. Arming it on the unit suite too
    wrote 439 header lines for 38 KB of file on every run, i.e. noise on a
    bench that did not need it.
    """
    global _watchdog_handle
    if _watchdog_handle is not None or _WATCHDOG_S <= 0:
        return _watchdog_handle
    try:
        _watchdog_handle = open(_WATCHDOG_FILE, "a", buffering=1, encoding="utf-8")
    except OSError:
        # A watchdog that fails the suite over its OWN problem is worse than
        # the defect it is watching for.
        return None
    _watchdog_handle.write("\n=== session started, threshold %ss ===\n" % _WATCHDOG_S)
    print("[watchdog] test stacks past %ss -> %s" % (_WATCHDOG_S, _WATCHDOG_FILE))
    return _watchdog_handle


def pytest_runtest_setup(item):
    if item.get_closest_marker("e2e") is None:
        return
    handle = _watchdog_open()
    if handle is not None:
        handle.write("\n--- %s ---\n" % item.nodeid)
        faulthandler.dump_traceback_later(_WATCHDOG_S, repeat=True, file=handle)


def pytest_runtest_teardown(item, nextitem):
    if _watchdog_handle is not None:
        faulthandler.cancel_dump_traceback_later()


def pytest_unconfigure(config):
    global _watchdog_handle
    if _watchdog_handle is not None:
        faulthandler.cancel_dump_traceback_later()
        _watchdog_handle.close()
        _watchdog_handle = None

# GUARDED, and not for tidiness. The user-path e2e files are collected in an
# environment that deliberately does NOT have this package installed - they
# build their own venv and install from the index, because a second copy on the
# runner's path would mean their assertions read the wrong one. A hard import
# here made collection fail outright (exit 4, "No module named
# invisible_playwright") on both CI runners while passing locally, where a
# checkout is always importable.
#
# The fixtures below still need it; they fail loudly at USE time instead, which
# is a clear message about one test rather than a collection error covering the
# whole run.
try:
    from invisible_playwright._fpforge import generate_profile
    from invisible_playwright.constants import BINARY_ENTRY_REL
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - only in the e2e-only environment
    generate_profile = None
    BINARY_ENTRY_REL = None
    _IMPORT_ERROR = exc


def _require_package():
    if _IMPORT_ERROR is not None:
        raise pytest.UsageError(
            f"this fixture needs invisible_playwright importable, and it is "
            f"not: {_IMPORT_ERROR}. That is expected only when running the "
            f"user-path e2e files, which install the package into their own "
            f"venv; any other test needs `pip install -e .`")


@pytest.fixture
def deterministic_rng():
    """Seeded RNG for reproducible tests."""
    return random.Random(42)


@pytest.fixture
def sample_profile():
    """A Profile generated from seed=42 for reuse across tests."""
    return generate_profile(seed=42)


@pytest.fixture(scope="session")
def firefox_binary():
    """Locate the patched Firefox binary for E2E tests, or skip cleanly.

    Single source of truth for every E2E test (previously each test file had its
    own copy - and three of them silently ignored INVPW_BINARY_PATH, so they kept
    testing whatever was in the cache even when you pointed the suite at a
    specific build: a false-confidence trap). Lookup order:

      1. ``INVPW_BINARY_PATH`` env var - point the whole suite at a local build
         or a freshly-extracted release (this is how the full-suite gate runs).
      2. Cached binary under ``cache_dir_for_version()`` (post ``fetch``).
      3. Skip - we never trigger an implicit multi-hundred-MB network download
         inside a test run.
    """
    env_path = os.environ.get("INVPW_BINARY_PATH")
    if env_path:
        if Path(env_path).exists():
            return env_path
        pytest.skip(f"INVPW_BINARY_PATH={env_path!r} does not exist")

    if sys.platform not in BINARY_ENTRY_REL:
        pytest.skip(f"unsupported platform: {sys.platform}")
    from invisible_playwright.download import cache_dir_for_version
    entry = cache_dir_for_version() / BINARY_ENTRY_REL[sys.platform]
    if not entry.exists():
        pytest.skip(
            "patched Firefox binary not cached and INVPW_BINARY_PATH unset; "
            "set INVPW_BINARY_PATH=<firefox binary> or run `invisible-playwright fetch`"
        )
    return str(entry)


# ---------------------------------------------------------------------------
# Local HTTP fixture server - service workers need a real http(s) origin
# (data: and about:blank are opaque-origin, no SW registration possible).
# It lives here because two modules use it (test_service_worker.py and
# test_service_worker_controller_e2e.py, which needs its own module for a
# fresh browser per case): importing a fixture from another test module is
# what ruff reports as F811.
# ---------------------------------------------------------------------------


class _SWFixtureHandler(http.server.BaseHTTPRequestHandler):
    """Serves a tiny set of routes for SW lifecycle testing."""

    PAGES = {
        "/": (200, "text/html", b"""<!doctype html>
<html><head><title>sw-host</title></head>
<body>
<script>
window.__swState = 'loading';
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
        .then(reg => { window.__swState = 'registered'; })
        .catch(err => { window.__swState = 'failed:' + err.message; });
} else {
    window.__swState = 'unsupported';
}
</script>
</body></html>
"""),
        "/sw.js": (200, "application/javascript", b"""
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
self.addEventListener('fetch', e => {
    if (e.request.url.endsWith('/from-sw')) {
        e.respondWith(new Response('hello from SW', {
            headers: {'content-type': 'text/plain'},
        }));
    }
    // Fall through for everything else - exercises the
    // interceptAfterServiceWorkerResets path that was broken pre-firefox-6.
});
"""),
        "/from-sw": (200, "text/plain", b"network-fallback"),
        "/from-network": (200, "text/plain", b"net-only"),
    }

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in self.PAGES:
            status, ctype, body = self.PAGES[path]
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # SW requires HTTPS or localhost - we're on localhost so plain http is fine
            self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args, **kwargs):
        pass  # silence stdout


@pytest.fixture(scope="module")
def fixture_server():
    """Spin up a localhost HTTP server with SW-friendly headers. Yields
    the base URL (e.g., 'http://127.0.0.1:54321')."""
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _SWFixtureHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
