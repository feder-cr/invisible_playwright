"""Cloak guard (e2e) - verifies the source-level "invisible headless" cloak:
the chrome window is hidden from the screen YET keeps rendering on the real GPU
(not Playwright's native headless, which has no WebGL). Runs per-platform in CI:
- Windows: the DWMWA_CLOAK attribute (queried via DWMWA_CLOAKED).
- macOS:   the NSWindow alpha (queried via Quartz CGWindowListCopyWindowInfo).
- Linux:   skipped - there the wrapper hides via Xvfb, not a source-level cloak.

This is the CI validation for the macOS cocoa cloak patch, which can't be built
or run on the Windows/Linux dev boxes.
"""
from __future__ import annotations

import sys
import time

import pytest

from invisible_playwright import InvisiblePlaywright

# Occlusion tracking is no longer part of the cloak: it is applied to EVERY
# session by invisible_core (see prefs.compose_session_prefs), because a window treated as
# backgrounded is readable by a page - rAF at 1 Hz, timers clamped to 1000 ms,
# visibilityState hidden, enumerateDevices that never resolves.
CLOAK_PREFS = {
    "zoom.stealth.cloak_windows": True,
}

_WEBGL_RENDERER = """() => {
  const g = document.createElement('canvas').getContext('webgl');
  if (!g) return 'NO-WEBGL';
  const d = g.getExtension('WEBGL_debug_renderer_info');
  return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : (g.getParameter(g.RENDERER) || '');
}"""


def _windows_moz_window_cloaked() -> bool:
    """True if at least one MozillaWindowClass top-level window is DWMWA_CLOAKED."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwm = ctypes.windll.dwmapi
    DWMWA_CLOAKED = 14
    ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def cb(hwnd, _):
        c = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, c, 256)
        if c.value == "MozillaWindowClass":
            v = wintypes.DWORD(0)
            dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED,
                                      ctypes.byref(v), 4)
            found.append(v.value)
        return True

    user32.EnumWindows(ENUM(cb), 0)
    return any(state != 0 for state in found)


def _macos_firefox_window_alpha_zero() -> bool:
    """True if a Firefox on-screen window reports ~0 alpha (cloaked)."""
    from Quartz import (  # type: ignore
        CGWindowListCopyWindowInfo,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
    )

    infos = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
    alphas = []
    for w in infos or []:
        owner = (w.get("kCGWindowOwnerName") or "")
        if "firefox" in owner.lower() or "nightly" in owner.lower():
            alphas.append(float(w.get("kCGWindowAlpha", 1.0)))
    # cloaked windows are alpha 0; if Firefox has any window it must be ~0.
    return bool(alphas) and all(a < 0.05 for a in alphas)


# -----------------------------------------------------------------------------
# SONDA [B193], quarta forma: il MINIDUMP del primo lancio.
#
# Nove esclusioni hanno detto cosa il crash NON e'. Per dire cosa E' serve
# guardarlo, e continuare a bisezionare non ci arriva.
#
# ⛔ IL CRASH REPORTER DI FIREFOX NON LO PRENDE. `0xC0000409` e' `__fastfail`,
# che per progetto AGGIRA l'unhandled exception filter dove Breakpad si
# installa. Serve Windows Error Reporting, che invece lo cattura: si accende
# scrivendo `HKLM\\...\\Windows Error Reporting\\LocalDumps`.
#
# E non servono i simboli. Nel record dell'eccezione c'e' il CODICE FAIL-FAST,
# `ExceptionInformation[0]`, che nomina la classe del guasto: stack corrotto,
# uscita fatale dell'applicazione, controllo CFG fallito, e cosi' via. Un numero
# solo, e il parser sta in trenta righe.
# -----------------------------------------------------------------------------
import os as _os
import pathlib as _pl
import struct as _st
import subprocess as _sp
import sys as _sys
import tempfile as _tf

_DUMP = []

#: I codici che Windows definisce per `__fastfail`, dal winnt.h.
_FAIL_FAST = {
    0: "LEGACY_GS_VIOLATION", 1: "VTGUARD_CHECK_FAILURE",
    2: "STACK_COOKIE_CHECK_FAILURE", 3: "CORRUPT_LIST_ENTRY",
    4: "INCORRECT_STACK", 5: "INVALID_ARG", 6: "GS_COOKIE_INIT",
    7: "FATAL_APP_EXIT", 8: "RANGE_CHECK_FAILURE",
    9: "UNSAFE_REGISTRY_ACCESS", 10: "GUARD_ICALL_CHECK_FAILURE",
    11: "GUARD_WRITE_CHECK_FAILURE", 12: "INVALID_FIBER_SWITCH",
    13: "INVALID_SET_OF_CONTEXT", 18: "INVALID_REFERENCE_COUNT",
    24: "INVALID_JUMP_BUFFER", 25: "MRDATA_MODIFIED",
    26: "CERTIFICATION_FAILURE", 27: "INVALID_EXCEPTION_CHAIN",
    28: "CRYPTO_LIBRARY", 29: "INVALID_CALL_IN_DLL_CALLOUT",
    30: "INVALID_IMAGE_BASE", 37: "GUARD_ICALL_CHECK_SUPPRESSED",
    38: "APCS_DISABLED", 39: "INVALID_IDLE_STATE",
    46: "INVALID_BUFFER_ACCESS", 47: "INVALID_BALANCED_TREE",
    48: "INVALID_NEXT_THREAD", 51: "GUARD_SS_FAILURE",
}


def _leggi_eccezione(percorso):
    """Il record dell'eccezione da un minidump, senza simboli e senza dbghelp.

    Il formato e' documentato e stabile: intestazione, un elenco di stream, e
    lo stream 6 (`ExceptionStream`) che contiene il codice e i suoi parametri.
    """
    b = _pl.Path(percorso).read_bytes()
    if b[:4] != b"MDMP":
        return "non e' un minidump (%r)" % b[:4]
    n_stream, rva_stream = _st.unpack_from("<II", b, 8)
    for i in range(n_stream):
        tipo, _dim, rva = _st.unpack_from("<III", b, rva_stream + i * 12)
        if tipo != 6:           # ExceptionStream
            continue
        # MINIDUMP_EXCEPTION_STREAM: ThreadId, __alignment, poi MINIDUMP_EXCEPTION
        base = rva + 8
        codice, _flag, _record, indirizzo, n_par = _st.unpack_from("<IIQQI", b, base)
        par = _st.unpack_from("<15Q", b, base + 32)
        nome = _FAIL_FAST.get(par[0], "sconosciuto")
        return ("codice 0x%08X, indirizzo 0x%X, %d parametri | "
                "fail-fast[0] = %d (%s) | par[1] = 0x%X"
                % (codice, indirizzo, n_par, par[0], nome, par[1]))
    return "nessuno stream di eccezione nel dump"


@pytest.mark.e2e
@pytest.mark.skipif(_sys.platform != "win32", reason="WER e' Windows")
def test_aab_minidump_del_primo_lancio(firefox_binary):
    """Accende WER, fa morire il primo lancio, e legge il record."""
    cartella = _tf.mkdtemp(prefix="dump-")
    chiave = (r"HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting"
              r"\LocalDumps")
    for nome, tipo, valore in (("DumpFolder", "REG_EXPAND_SZ", cartella),
                               ("DumpCount", "REG_DWORD", "10"),
                               ("DumpType", "REG_DWORD", "2")):
        r = _sp.run(["reg", "add", chiave, "/v", nome, "/t", tipo,
                     "/d", valore, "/f"], capture_output=True, text=True)
        _DUMP.append("reg %s -> %s %s" % (nome, r.returncode,
                                          (r.stderr or "").strip()[:50]))
    _DUMP.append("cartella dump: %s" % cartella)

    # Il primo lancio del processo: quello che muore.
    from invisible_playwright._juggler import connection as _C
    prof = _tf.mkdtemp(prefix="dumpprof-")
    (_pl.Path(prof) / "user.js").write_text("", encoding="utf-8")
    conn = None
    try:
        conn = _C.launch(firefox_binary, prof, headless=False, ready_timeout=30)
        _DUMP.append("il primo lancio NON e' morto: niente da leggere")
    except Exception as e:
        _DUMP.append("primo lancio morto: %s" % str(e).split(chr(10))[0][:60])
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    import time as _t
    _t.sleep(4)          # WER scrive il dump dopo che il processo e' uscito
    trovati = sorted(_pl.Path(cartella).glob("*.dmp"))
    _DUMP.append("dump trovati: %d" % len(trovati))
    for d in trovati[:3]:
        _DUMP.append("  %s  (%d byte)" % (d.name, d.stat().st_size))
        _DUMP.append("  -> " + _leggi_eccezione(d))
    raise AssertionError("MINIDUMP (sonda):" + chr(10)
                         + chr(10).join("    " + r for r in _DUMP))


@pytest.mark.e2e
@pytest.mark.skipif(
    sys.platform.startswith("linux"),
    reason="source-level cloak is Windows/macOS only; Linux hides via Xvfb",
)
def test_cloak_hides_window_but_keeps_rendering(firefox_binary):
    with InvisiblePlaywright(
        seed=42, binary_path=firefox_binary, headless=False, extra_prefs=CLOAK_PREFS
    ) as browser:
        page = browser.new_context().new_page()
        page.goto("https://example.com", timeout=30_000)
        time.sleep(2)

        # 1) still renders on the real GPU pipeline (a non-blank screenshot proves
        #    the compositor is alive despite the window being hidden).
        shot = page.screenshot()
        assert len(shot) > 3000, "cloaked window produced a blank screenshot (rendering paused)"

        # 2) headed pipeline intact: a real WebGL context (Playwright's native
        #    headless has none). Linux (Xvfb + llvmpipe) and Windows (WARP) give a
        #    software context on the GPU-less runners, so a missing context there
        #    is a real regression -> hard fail. macOS GitHub runners expose NO
        #    WebGL in the CI session at all (even vanilla Firefox), and macOS has
        #    no software-GL fallback; the cloak's "still rendering" property is
        #    already proven by the non-blank screenshot above, so we don't also
        #    require a live WebGL context there.
        renderer = page.evaluate(_WEBGL_RENDERER)
        webgl_ok = bool(renderer) and renderer != "NO-WEBGL"
        if not (sys.platform == "darwin" and not webgl_ok):
            assert webgl_ok, f"no real WebGL under cloak: {renderer!r}"

        # 3) the window is actually hidden (per-platform).
        if sys.platform == "win32":
            assert _windows_moz_window_cloaked(), "Firefox window is not DWMWA_CLOAKED"
        elif sys.platform == "darwin":
            try:
                hidden = _macos_firefox_window_alpha_zero()
            except ImportError:
                pytest.skip("pyobjc Quartz not available to verify macOS cloak alpha")
            assert hidden, "Firefox macOS window is not alpha-cloaked"
