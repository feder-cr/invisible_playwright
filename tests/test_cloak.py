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
# SONDA [B193], seconda forma. La prima ha misurato 18 lanci su 18 VIVI con la
# configurazione che nello stesso job faceva morire il test vero - quindi non e'
# la configurazione. La differenza e' che il test vero e' il PRIMO lancio del
# processo e i miei venivano dopo.
#
# Questa sonda e' definita PRIMA del test del cloak apposta, cosi' il suo primo
# lancio e' il primo in assoluto, e riporta i lanci UNO PER UNO: se cade solo il
# primo, un tasso lo nasconderebbe.
# -----------------------------------------------------------------------------
import sys as _sys

_ESITI = []


@pytest.mark.e2e
@pytest.mark.skipif(_sys.platform != "win32", reason="il crash e' su Windows")
def test_aaa_primo_lancio(firefox_binary):
    """I primi sei lanci del processo, uno per uno, con il cloak."""
    from invisible_playwright import InvisiblePlaywright
    # ⛔ IL PRIMO LANCIO PORTA `browser.launcherProcess.enabled: False`.
    # Su Windows Firefox avvia un processo LAUNCHER che si ri-esegue, e il suo
    # stato viene memorizzato dopo la prima esecuzione riuscita: si comporta
    # diversamente proprio al primo giro, che e' l'unico che muore.
    # ⛔ IL PRIMO LANCIO IN ASSOLUTO LO FA IL LANCIATORE NUDO.
    # `--version` non scalda niente (provato: uscita 0, e il lancio dopo muore
    # lo stesso), probabilmente perche' esce prima che il launcher di Windows
    # scriva la sua voce nel registro. Un lancio VERO invece ci passa.
    #
    # Questo separa due ipotesi che finora coincidevano: "muore il primo lancio
    # qualunque esso sia" contro "muore il primo lancio del percorso pubblico".
    from invisible_playwright._juggler import connection as _C
    import tempfile as _tf, pathlib as _pl
    d = _tf.mkdtemp(prefix="scalda-")
    # ⛔ PROFILO NUDO, NESSUNA PREF. Finora il primo lancio l'ho sempre fatto col
    # cloak, quindi "muore il primo" e "muore il primo col cloak" erano ancora
    # la stessa misura. Questo le separa: se muore con un user.js vuoto, il
    # cloak non c'entra per niente.
    (_pl.Path(d) / "user.js").write_text("", encoding="utf-8")
    conn = None
    try:
        conn = _C.launch(firefox_binary, d, headless=False, ready_timeout=30)
        _ESITI.append("lanciatore nudo, profilo VUOTO (primo in assoluto): VIVO")
    except Exception as e:
        _ESITI.append("lanciatore nudo, profilo VUOTO (primo in assoluto): MORTO  %s"
                      % str(e).split(chr(10))[0][:60])
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    print("  " + _ESITI[-1], flush=True)

    for n in range(1, 7):
        try:
            with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                                     headless=False,
                                     extra_prefs=CLOAK_PREFS):
                _ESITI.append("lancio %d: VIVO" % n)
        except Exception as e:
            _ESITI.append("lancio %d: MORTO" % n)
            for riga in str(e).split(chr(10))[:14]:
                _ESITI.append("      | " + riga[:120])
        print("  " + _ESITI[-1], flush=True)
    raise AssertionError("PRIMI LANCI (sonda):" + chr(10)
                         + chr(10).join("    " + r for r in _ESITI))



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
