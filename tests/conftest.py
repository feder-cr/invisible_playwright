import faulthandler
import os
import random
import sys
import tempfile
from pathlib import Path

import pytest

# ── Il cane da guardia che dice DOVE e' fermo, mentre e' ancora fermo ─────────
#
# L'e2e si impicca nel 12% delle corse su CI (4 su 33, dal 2026-08-01 al 08-13) e
# fino al 2026-08-13 non lasciava niente: il job veniva ucciso dal tetto dei 40
# minuti, quindi risultava `cancelled` e non `failure`, e i passi successivi non
# giravano. `--timeout 420 --timeout-method thread` in run_e2e.py adesso lo uccide
# a 7 minuti stampando lo stack, ma sette minuti sono lunghi e uno stack solo non
# dice se il processo e' FERMO o soltanto lento.
#
# Questo lo dice: a 150 secondi dentro UN test stampa lo stack di ogni thread, e
# lo ripete. Due dump identici significano bloccato; due dump diversi significano
# lento. Sono due diagnosi opposte e nessun altro strumento le distingue.
#
# 150 secondi perche' il test legittimo piu' lento misurato in CI e'
# `test_webgl_readpixels_no_masking_signature` a 94,4 s, e il secondo a 11,4: su
# una corsa sana non stampa niente, e su un runner lento al massimo stampa una
# volta, che e' output e non un fallimento.
#
# Su FILE, non su stderr, e non e' un dettaglio: con la cattura predefinita
# pytest redirige i descrittori 1 e 2 a livello di sistema operativo, quindi
# anche scrivere sul `sys.__stderr__` originale finisce nel buffer di cattura -
# che pytest mostra solo a fallimento avvenuto, e qui il processo viene UCCISO.
# Provato: con la soglia a 3 secondi su un test bloccato in `accept()`, la stessa
# chiamata stampa quattro stack se lanciata a mano e ZERO sotto pytest. Un file
# sopravvive all'uccisione e su CI si carica come artifact.
_WATCHDOG_S = float(os.environ.get("INVPW_TEST_WATCHDOG_S", "150"))
_WATCHDOG_FILE = os.environ.get(
    "INVPW_TEST_WATCHDOG_FILE",
    str(Path(tempfile.gettempdir()) / "invpw-e2e-watchdog.log"))
_watchdog_handle = None


def _watchdog_open():
    """Apre il file alla PRIMA occorrenza, non all'avvio della sessione.

    Solo i test `e2e` sono sorvegliati: sono gli unici che aprono un browser, e
    l'unico impiccamento misurato vive li'. Armarlo anche sulla suite unitaria
    scriveva 439 righe di intestazione per 38 KB di file a ogni corsa, cioe'
    rumore su un banco che non ne aveva bisogno.
    """
    global _watchdog_handle
    if _watchdog_handle is not None or _WATCHDOG_S <= 0:
        return _watchdog_handle
    try:
        _watchdog_handle = open(_WATCHDOG_FILE, "a", buffering=1, encoding="utf-8")
    except OSError:
        # Un cane da guardia che fa fallire la suite per un problema PROPRIO e'
        # peggio del difetto che sorveglia.
        return None
    _watchdog_handle.write("\n=== sessione avviata, soglia %ss ===\n" % _WATCHDOG_S)
    print("[watchdog] stack dei test oltre %ss -> %s" % (_WATCHDOG_S, _WATCHDOG_FILE))
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
