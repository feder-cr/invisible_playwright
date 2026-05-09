"""Translate an internal Profile into the Firefox prefs dict that the
patched Firefox binary expects.

The output dict keys map 1:1 to ``user.js`` preferences. Playwright passes
them via ``firefox_user_prefs=``. The patched binary propagates them to all
content processes over IPC; C++ patches read the ``zoom.stealth.*``
namespace.

The translation is split into:

  * ``_BASELINE`` — global stealth policy (RFP off, WebRTC leaks blocked,
    safebrowsing disabled, debugger detach, …) plus Windows-canonical
    constants that don't depend on the Profile (system colors palette,
    WebGL extensions whitelist, speech voices, navigator identity).
  * ``translate_profile_to_prefs`` — overlays the Profile fields plus the
    user-supplied ``locale`` and ``timezone``.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, Optional

from ._fpforge import Profile


# ──────────────────────────────────────────────────────────────────────
#  Navigator identity — locked to Firefox 150 Windows so the binary
#  reports the same UA / platform / oscpu regardless of the host OS.
# ──────────────────────────────────────────────────────────────────────

_NAVIGATOR_OVERRIDES: Dict[str, str] = {
    "general.useragent.override":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
        "Gecko/20100101 Firefox/150.0.1",
    "general.platform.override":   "Win32",
    "general.oscpu.override":      "Windows NT 10.0; Win64; x64",
    # general.buildID.override removed 2026-04-28: the previous value
    # "20181001000000" was a 2018 buildID stuck on a 2026-built Firefox 150
    # binary (real BuildID=20260426192818 from application.ini). The 7.5-yr
    # discrepancy is the kind of internal-consistency check Google reCAPTCHA
    # can use to flag bot/spoofed browsers. Deleting the override lets
    # Firefox emit its compiled-in buildID, which auto-tracks the binary.
    # A/B knockout 2026-04-28 (n=30): F2 delete +0.083 RC vs BASE; n=100
    # confirm: +0.021; overnight isolated: +0.155 single-variant. Variable
    # signal, but the underlying data error is unambiguous.
    "general.appversion.override": "5.0 (Windows)",
}


# ──────────────────────────────────────────────────────────────────────
#  System colors — FP Pro probes getComputedStyle(div) with CSS system
#  keywords (ButtonFace, Menu, Highlight, …) and hashes the result into
#  signal s142. On Linux, Firefox resolves these via GTK theme → GTK
#  RGB values diverge from Windows Win32 palette → server-side anomaly
#  even with Windows UA. Pinning the palette to Win10 default closes
#  the gap (see project_css_system_colors.md memory).
# ──────────────────────────────────────────────────────────────────────

_WIN_LIGHT_COLORS: Dict[str, str] = {
    "ui.activeborder":              "#B4B4B4",
    "ui.activecaption":             "#99B4D1",
    "ui.appworkspace":              "#ABABAB",
    "ui.background":                "#000000",
    "ui.buttonface":                "#F0F0F0",
    "ui.buttonhighlight":           "#FFFFFF",
    "ui.buttonshadow":              "#A0A0A0",
    "ui.buttontext":                "#000000",
    "ui.buttonborder":              "#000000",
    "ui.captiontext":               "#000000",
    "ui.graytext":                  "#6D6D6D",
    "ui.highlight":                 "#0078D7",
    "ui.highlighttext":             "#FFFFFF",
    "ui.inactiveborder":            "#F4F7FC",
    "ui.inactivecaption":           "#BFCDDB",
    "ui.inactivecaptiontext":       "#434E54",
    "ui.infobackground":            "#FFFFE1",
    "ui.infotext":                  "#000000",
    "ui.menu":                      "#F9F9FB",
    "ui.menutext":                  "#000000",
    "ui.scrollbar":                 "#C8C8C8",
    "ui.threeddarkshadow":          "#696969",
    "ui.threedface":                "#F0F0F0",
    "ui.threedhighlight":           "#FFFFFF",
    "ui.threedlightshadow":         "#E3E3E3",
    "ui.threedshadow":              "#A0A0A0",
    "ui.window":                    "#FFFFFF",
    "ui.windowframe":               "#646464",
    "ui.windowtext":                "#000000",
    "ui.mark":                      "#FFFF00",
    "ui.marktext":                  "#000000",
    "ui.accentcolor":               "#0078D4",
    "ui.accentcolortext":           "#FFFFFF",
    "ui.selecteditem":              "#0078D7",
    "ui.selecteditemtext":          "#FFFFFF",
    "ui.-moz-hyperlinktext":        "#0066CC",
    "ui.-moz-activehyperlinktext":  "#EE0000",
    "ui.-moz-visitedhyperlinktext": "#551A8B",
}


# ──────────────────────────────────────────────────────────────────────
#  WebGL extensions — Windows ANGLE canonical lists. Empty string =
#  fall back to native Mesa/ANGLE; non-empty = `getSupportedExtensions`
#  returns this list verbatim and `IsSupported()` rejects anything else.
# ──────────────────────────────────────────────────────────────────────

_WEBGL1_EXTENSIONS = ",".join([
    "ANGLE_instanced_arrays",
    "EXT_blend_minmax",
    "EXT_color_buffer_half_float",
    "EXT_float_blend",
    "EXT_frag_depth",
    "EXT_sRGB",
    "EXT_shader_texture_lod",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "OES_element_index_uint",
    "OES_fbo_render_mipmap",
    "OES_standard_derivatives",
    "OES_texture_float",
    "OES_texture_float_linear",
    "OES_texture_half_float",
    "OES_texture_half_float_linear",
    "OES_vertex_array_object",
    "WEBGL_color_buffer_float",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_depth_texture",
    "WEBGL_draw_buffers",
    "WEBGL_lose_context",
    "WEBGL_provoking_vertex",
])

_WEBGL2_EXTENSIONS = ",".join([
    "EXT_color_buffer_float",
    "EXT_color_buffer_half_float",
    "EXT_float_blend",
    "EXT_texture_compression_bptc",
    "EXT_texture_compression_rgtc",
    "EXT_texture_filter_anisotropic",
    "OES_draw_buffers_indexed",
    "OES_texture_float_linear",
    "OES_texture_half_float_linear",
    "OVR_multiview2",
    "WEBGL_compressed_texture_s3tc",
    "WEBGL_compressed_texture_s3tc_srgb",
    "WEBGL_debug_renderer_info",
    "WEBGL_debug_shaders",
    "WEBGL_lose_context",
    "WEBGL_provoking_vertex",
])


# ──────────────────────────────────────────────────────────────────────
#  Speech voices — Windows canonical "Microsoft *" set. Format:
#  "NAME|LANG|DEFAULT|LOCAL,...". Non-empty value drives the
#  speechSynthesis.getVoices() patch; empty disables it.
# ──────────────────────────────────────────────────────────────────────

_WIN_VOICES = ",".join([
    "Microsoft David - English (United States)|en-US|1|1",
    "Microsoft Zira - English (United States)|en-US|0|1",
    "Microsoft Mark - English (United States)|en-US|0|1",
    "Microsoft David Desktop - English (United States)|en-US|0|1",
    "Microsoft Zira Desktop - English (United States)|en-US|0|1",
])


# ──────────────────────────────────────────────────────────────────────
#  Linux font compensation — Linux Firefox uses DejaVu / Liberation
#  fonts which have wider/narrower glyphs than Windows Arial / Segoe.
#  These per-generic factors are prepended to ``zoom.stealth.font.metrics``
#  on Linux only; Windows-native rendering already matches the canonical
#  widths so we pass an empty string (any factor !=1 would distort real
#  metrics).
# ──────────────────────────────────────────────────────────────────────

_LINUX_GENERIC_FONT_FACTORS = (
    # Calibrated to bring DejaVu/Liberation widths in line with what Windows
    # FP Pro probes report for native Segoe/Times. Linux base measurements
    # (font_preferences) and Windows targets:
    #   serif:    162 → 149  factor 0.920
    #   sans:     162 → 144  factor 0.889
    #   monospace:121 → 121  factor 1.000
    #   system:   162 → 147  factor 0.910
    "serif|0.920,sans-serif|0.889,monospace|1.000,"
    "system-ui|0.910,cursive|0.932,fantasy|0.812,"
)


# ──────────────────────────────────────────────────────────────────────
#  Baseline — applied to every session regardless of Profile.
# ──────────────────────────────────────────────────────────────────────

_BASELINE: Dict[str, Any] = {
    # Turn off Firefox's own resistFingerprinting; we do our own via patches.
    "privacy.resistFingerprinting": False,
    "privacy.resistFingerprinting.letterboxing": False,

    # FF150 fingerprintingProtection — enabled by default (or remotely via
    # Mozilla webcompat overrides). FP Pro detects the side-effects and
    # flips `privacy_settings: true`. On FF146 these were all off → False.
    # Force off so FP Pro reports privacy_settings:false (matches FF146).
    "privacy.fingerprintingProtection":                              False,
    "privacy.fingerprintingProtection.pbmode":                       False,
    "privacy.fingerprintingProtection.remoteOverrides.enabled":      False,

    # WebRTC: enabled, no public IP leak.
    # obfuscate_host_addresses=false: our C++ injection handles candidate
    # selection; mDNS causes mDNS-IPC to hang in sandboxed content processes.
    # disableIPv6=true keeps IPv6 out of gathering (less entropy, no IPv6 leak).
    "media.peerconnection.enabled":                       True,
    "media.peerconnection.ice.no_host":                   False,
    "media.peerconnection.ice.default_address_only":      False,
    "media.peerconnection.ice.obfuscate_host_addresses":  False,
    "media.peerconnection.ice.disableIPv6":               True,
    "media.peerconnection.ice.proxy_only":                False,
    "media.peerconnection.ice.relay_only":                False,
    "media.peerconnection.use_document_iceservers":       True,

    # Proxy — route DNS through SOCKS proxies to avoid local DNS leaks.
    "network.proxy.socks_remote_dns":                     True,
    "network.proxy.failover_direct":                      False,

    # Safebrowsing — chatty and fingerprintable.
    "browser.safebrowsing.malware.enabled":               False,
    "browser.safebrowsing.phishing.enabled":              False,
    "browser.safebrowsing.downloads.enabled":             False,
    "browser.safebrowsing.downloads.remote.enabled":      False,

    # First-run / welcome UI noise.
    "browser.startup.page":                               0,
    "browser.shell.checkDefaultBrowser":                  False,
    "browser.aboutwelcome.enabled":                       False,
    "browser.startup.upgradeDialog.enabled":              False,
    "termsofuse.acceptedVersion":                         999,

    # Disable about:newtab auto-load — TopSitesFeed.sys.mjs auto-fetches when
    # a tab opens, triggering a cross-process BC swap that hijacks the first
    # page.goto() (NS_BINDING_ABORTED on creepjs/peet/sannysoft/fppro).
    "browser.newtabpage.enabled":                         False,
    "browser.newtab.preload":                             False,
    "browser.newtabpage.activity-stream.feeds.topsites":  False,
    "browser.newtabpage.activity-stream.feeds.section.topstories": False,
    "browser.newtabpage.activity-stream.enabled":         False,

    # Disable Firefox internal services that hit the network on startup.
    # Through a residential SOCKS5 proxy these compete with the test
    # navigation and trigger NS_BINDING_FAILED (server-side rate-limit /
    # connection drops). Domains observed in MOZ_LOG: push.services,
    # firefox.settings.services, detectportal, ohttp-gateway, location.
    "browser.aboutConfig.showWarning":                    False,
    "network.captive-portal-service.enabled":             False,
    "network.connectivity-service.enabled":               False,
    "dom.push.enabled":                                   False,
    "dom.push.connection.enabled":                        False,
    "geo.enabled":                                        False,
    "geo.provider.network.url":                           "",
    "browser.region.network.url":                         "",
    "browser.region.update.enabled":                      False,
    "services.settings.server":                           "",
    "browser.search.geoSpecificDefaults":                 False,
    "browser.contentblocking.report.lockwise.enabled":    False,
    "browser.contentblocking.report.monitor.enabled":     False,
    "extensions.systemAddon.update.enabled":              False,
    "extensions.update.enabled":                          False,
    "extensions.getAddons.cache.enabled":                 False,
    "browser.discovery.enabled":                          False,
    "browser.ping-centre.telemetry":                      False,
    "app.normandy.enabled":                               False,
    "dom.private-attribution.submission.enabled":         False,
    "browser.translations.enable":                        False,
    "browser.search.update":                              False,

    # HTTP/3 + speculative + Alt-Svc disabled. SOCKS5 proxy doesn't
    # support UDP ASSOCIATE so HTTP/3 fails. Speculative connections
    # under load cause early channel cancel (NS_BINDING_FAILED).
    "network.http.http3.enable":                          False,
    "network.http.http3.enabled":                         False,
    "network.http.altsvc.enabled":                        False,
    "network.http.altsvc.oe":                             False,
    "network.http.speculative-parallel-limit":            0,
    "network.predictor.enabled":                          False,
    "network.dns.disablePrefetch":                        True,
    "network.dns.disablePrefetchFromHTTPS":               True,
    "network.dns.echconfig.enabled":                      False,
    "network.dns.use_https_rr_as_altsvc":                 False,

    # === A/B VARIANT B: Fission disabled ===
    # Force single content-process model (e10s only, no BC outer/inner split).
    # Diagnostic for the FF150 BC-swap theory: if peet_ws/fppro/sannysoft
    # work with this off, the Juggler FF146 baseline breaks specifically on
    # cross-process navigation tracking.
    "fission.autostart":                                  False,
    "fission.autostart.session":                          False,
    "dom.ipc.processCount.webIsolated":                   1,


    # Telemetry & data reporting.
    "datareporting.healthreport.uploadEnabled":           False,
    "datareporting.policy.dataSubmissionEnabled":         False,
    "toolkit.telemetry.enabled":                          False,
    "toolkit.telemetry.unified":                          False,
    "app.shield.optoutstudies.enabled":                   False,

    # Update channels.
    "app.update.enabled":                                 False,
    "app.update.auto":                                    False,

    # Speech synth: enabled (the C++ patch fabricates voices from the
    # comma list above) regardless of the host OS.
    "media.webspeech.synth.enabled":                      True,
    "zoom.stealth.voices.list":                           _WIN_VOICES,

    # WebGL extensions whitelist — non-empty pre-empts native enumeration.
    "zoom.stealth.webgl.extensions":                      _WEBGL1_EXTENSIONS,
    "zoom.stealth.webgl2.extensions":                     _WEBGL2_EXTENSIONS,
    # WebGL numeric param overrides — kept empty (A/B test 2026-04-22 showed
    # mismatches between the values we shipped and ANGLE's real envelope
    # raised FP Pro's ML tampering score). Slot kept for future experiments.
    "zoom.stealth.webgl.int_params":                      "",
    "zoom.stealth.webgl.int2_params":                     "",
    "zoom.stealth.webgl.shader_precisions":               "",
    "zoom.stealth.webgl.float_params":                    "",

    # DevTools anti-detection.
    "zoom.stealth.debugger.force_detach":                 True,

    # Canvas substitution — additive ±1 noise over the OS base pattern;
    # set to True to replace pixels with hash(seed, idx) instead.
    "zoom.stealth.canvas.substitute_pixels":              False,

    # Navigator identity (locked to Windows Firefox 150).
    **_NAVIGATOR_OVERRIDES,
}


# ──────────────────────────────────────────────────────────────────────
#  Linux-only Xvfb workarounds — the Linux Firefox build under Xvfb
#  cannot run WebRender (`ConnectToCompositor` retries forever). We
#  disable WebRender + force WebGL through the GL software path so
#  webgl_basics / webgl_extensions still report.
# ──────────────────────────────────────────────────────────────────────

_LINUX_XVFB_WORKAROUNDS: Dict[str, Any] = {
    "gfx.webrender.all":                       False,
    "gfx.webrender.force-disabled":            True,
    "webgl.force-enabled":                     True,
    # webgl.software-rendering-enabled / webgl.force-layers-readback removed in FF150.
}

# ──────────────────────────────────────────────────────────────────────
#  Windows virtual-desktop workarounds — when headless=True on Windows,
#  Firefox runs on a CreateDesktop virtual desktop. The hardware GPU is
#  inaccessible from the virtual desktop, so the GPU process crashes when
#  it tries to initialize the D3D11 compositor with hardware acceleration.
#
#  Approach: force D3D11 WARP (CPU software renderer) for the GPU process.
#  layers.d3d11.force-warp=True → compositor uses WARP → GPU process stable.
#  webgl.angle.force-warp=True  → ANGLE uses WARP → WebGL context creates.
#
#  CRITICAL: do NOT set webgl.out-of-process=False. That moves WebGL from the
#  GPU process to the sandboxed content process. The content process sandbox
#  blocks D3D11 access entirely → ANGLE crashes the content process →
#  canvas.getContext('webgl') throws instead of returning null.
#
#  gfx.canvas.accelerated=False: default is true, disabling avoids any
#  hardware GPU dependency for 2D canvas in the content process.
# ──────────────────────────────────────────────────────────────────────

_WIN_VIRT_DESKTOP_WORKAROUNDS: Dict[str, Any] = {
    # FF150 regression vs FF146 on CreateDesktop alt-desktop:
    # The GPU process sandbox (level=1, default since FF110) tries to parent
    # its compositor window to the parent process's window. Our worker spawns
    # Firefox on a CreateDesktop-created alt desktop — parent and GPU process
    # do not share the same desktop/HWND namespace, so window parenting fails
    # silently. WebRender falls back to "Software D3D11" and OOP-WebGL never
    # publishes a hardware ANGLE renderer → getContext('webgl') returns a
    # context but extensions/parameters/$hash all come back null/empty (FF146
    # had a more permissive sandbox, so the same setup worked there).
    # Bugzilla refs: 1798091, 1524591, 1229829. Lowering the GPU sandbox to 0
    # restores hardware compositor + functional WebGL on alt desktops.
    "security.sandbox.gpu.level": 0,
}


# ──────────────────────────────────────────────────────────────────────
#  Public helpers
# ──────────────────────────────────────────────────────────────────────

def _accept_language(locale: str) -> str:
    lang = locale.replace("_", "-")
    base = lang.split("-")[0]
    return f"{lang}, {base}" if base != lang else lang


def _font_metrics_for_platform(profile_metrics: str) -> str:
    """Return ``zoom.stealth.font.metrics`` value.

    Windows: empty string. The C++ width-scale hook is a no-op and
    Firefox renders Arial/Segoe/Calibri/etc. at their native canonical
    widths. Applying the Bayesian-sampled per-font factors on a Windows
    build would *distort* real metrics and surface as a font_preferences
    width anomaly to FP Pro / reCAPTCHA.

    Linux: prepend generic-family compensation factors so DejaVu /
    Liberation render at the widths Windows JS expects, then append the
    per-font factors that make each fabricated family detectable by
    width-diff probes.
    """
    if not profile_metrics:
        return ""
    if sys.platform.startswith("linux"):
        return _LINUX_GENERIC_FONT_FACTORS + profile_metrics
    return ""  # Windows: NEVER apply width-scale factors.


def translate_profile_to_prefs(
    profile: Profile,
    *,
    locale: str = "en-US",
    timezone: str = "",
    extra_prefs: Optional[Dict[str, Any]] = None,
    virtual_display: bool = False,
) -> Dict[str, Any]:
    """Return a complete prefs dict ready for Playwright's firefox_user_prefs=.

    Args:
        profile:         Bayesian-sampled fingerprint (from ``generate_profile``).
        locale:          BCP-47 tag, e.g. ``"en-US"``.
        timezone:        IANA timezone name, e.g. ``"America/New_York"``.
        extra_prefs:     Optional overlay applied LAST.
        virtual_display: When True on Windows, apply GPU-disabling workarounds
                         to prevent the GPU process from crashing on virtual
                         desktops that have no D3D11 backend.
    """
    prefs: Dict[str, Any] = dict(_BASELINE)

    # GPU / WebGL renderer/vendor.
    # On Linux we spoof to a Windows ANGLE renderer string (profile.gpu.renderer)
    # so cross-platform sessions report a consistent Windows GPU identity.
    # On Windows, spoofing a different GPU creates a renderer/parameters hash
    # mismatch: FP Pro hashes all 81 CN-set getParameter() values including
    # enum 7937 (RENDERER). Setting GTX 980 while ANGLE returns Intel Arc A750
    # parameters produces an OOD (hash 23d0a74b vs vanilla 66544db) that FP Pro
    # ML scores at ~0.70 (confirmed: direct SF146 vs vanilla on same machine).
    # Fix: leave renderer/vendor empty on Windows → ANGLE reports native hardware
    # (SanitizeRenderer path at ClientWebGLContext.cpp:2592-2595) → consistent.
    if sys.platform.startswith("linux"):
        prefs["zoom.stealth.webgl.renderer"] = profile.gpu.renderer
        prefs["zoom.stealth.webgl.vendor"]   = profile.gpu.vendor
        _renderer_lo = (profile.gpu.renderer or "").lower()
    else:
        prefs["zoom.stealth.webgl.renderer"] = ""
        prefs["zoom.stealth.webgl.vendor"]   = ""
        _renderer_lo = "intel"  # test hardware is Intel Arc A750

    # MSAA: on Windows, pin to 4 (Firefox default for ANGLE) so gl.SAMPLES is
    # constant across all sessions. Different MSAA values cause different CN-set
    # parameters hashes even with the same renderer → detectable variation.
    # Vanilla Intel Arc A750 parameters hash (66544db8) verified at msaa=4.
    _msaa = profile.webgl.msaa_samples if sys.platform.startswith("linux") else 4
    prefs["zoom.stealth.webgl.msaa"]        = _msaa
    prefs["webgl.msaa-samples"]             = _msaa
    prefs["webgl.msaa-force"]               = _msaa > 0

    # Canvas pixel-noise density per vendor. Intel has lower natural
    # rendering variance than NVIDIA/AMD, so the default 1/8 noise rate
    # over-amplifies the FP Pro tampering ML signal. Drop to 1/16 for Intel
    # to keep tampering_ml below the detection threshold while still
    # breaking the canvas geometry hash.
    if "intel" in _renderer_lo:
        prefs["zoom.stealth.canvas.noise_skip_mask"] = 15  # 1/16, ~6.25%
    else:
        prefs["zoom.stealth.canvas.noise_skip_mask"] = 7   # 1/8,  ~12.5%

    # Screen
    prefs["zoom.stealth.screen.width"]        = profile.screen.width
    prefs["zoom.stealth.screen.height"]       = profile.screen.height
    prefs["zoom.stealth.screen.avail_width"]  = profile.screen.avail_width
    prefs["zoom.stealth.screen.avail_height"] = profile.screen.avail_height
    prefs["zoom.stealth.screen.dpr"]          = profile.screen.dpr
    prefs["layout.css.devPixelsPerPx"]        = str(profile.screen.dpr)

    # Hardware
    prefs["zoom.stealth.hw_concurrency"]      = profile.hardware.concurrency
    prefs["zoom.stealth.storage.quota_mb"]    = profile.hardware.storage_quota_mb

    # Audio
    prefs["zoom.stealth.audio.sample_rate"]       = profile.audio.sample_rate
    prefs["zoom.stealth.audio.output_latency_ms"] = profile.audio.output_latency_ms
    prefs["zoom.stealth.audio.max_channel_count"] = profile.audio.max_channel_count

    # Codec
    prefs["media.av1.enabled"]                = profile.codec.av1_enabled
    prefs["media.encoder.webm.enabled"]       = profile.codec.webm_encoder_enabled
    prefs["media.mediasource.webm.enabled"]   = profile.codec.mediasource_webm
    prefs["media.mediasource.mp4.enabled"]    = profile.codec.mediasource_mp4

    # Fonts
    prefs["zoom.stealth.font.whitelist"] = ",".join(profile.fonts)
    prefs["zoom.stealth.font.metrics"]   = _font_metrics_for_platform(
        profile._raw.get("font_metrics", "") or ""
    )

    # UI / dark mode + Windows colors palette (only when light theme).
    prefs["ui.systemUsesDarkTheme"] = int(profile.dark_theme)
    if not profile.dark_theme:
        prefs.update(_WIN_LIGHT_COLORS)

    # Locale prefs.
    locale = locale or "en-US"
    lang = locale.replace("_", "-")
    prefs["intl.accept_languages"]     = _accept_language(locale)
    prefs["general.useragent.locale"]  = lang
    prefs["intl.locale.requested"]     = lang
    prefs["privacy.spoof_english"]     = 0

    if timezone:
        prefs["zoom.stealth.timezone"] = timezone
        prefs["juggler.timezone.override"] = timezone

    # Cross-process seed (canvas noise + DWrite gamma share this).
    prefs["zoom.stealth.fpp.hw_seed"] = profile.seed
    prefs["zoom.stealth.seed"]        = profile.seed

    # Synthetic host ICE candidate — injected by C++ when addr_ct==0 (SOCKS5
    # proxy suppresses all local addresses so Firefox can't gather host cands).
    # LAN IP is seed-derived so it's consistent per session and looks like a
    # real home router assignment (192.168.x.x range).
    _s = profile.seed
    _lan_ip = f"192.168.{(_s >> 8) % 254 + 1}.{_s % 254 + 1}"
    prefs["zoom.stealth.webrtc.host_ip"] = _lan_ip

    # On Windows, native ANGLE extension list already matches real Windows users.
    # The baseline hard-codes a curated _WEBGL1/2_EXTENSIONS list designed for
    # Linux Mesa → clear it so Windows sessions report the native extension set
    # (hash matches real Intel Arc A750 vanilla captures).
    if not sys.platform.startswith("linux"):
        prefs["zoom.stealth.webgl.extensions"]  = ""
        prefs["zoom.stealth.webgl2.extensions"] = ""

    # Linux Xvfb workarounds (no-op on Windows).
    if sys.platform.startswith("linux"):
        for k, v in _LINUX_XVFB_WORKAROUNDS.items():
            prefs.setdefault(k, v)

    # Windows virtual-desktop workarounds (headless=True on Windows).
    if virtual_display and sys.platform == "win32":
        for k, v in _WIN_VIRT_DESKTOP_WORKAROUNDS.items():
            prefs.setdefault(k, v)

    # Caller overlay LAST so users can override anything we set. A value of
    # None is treated as a sentinel meaning "delete this pref entirely from
    # the final dict" — useful for A/B harnesses that need to test what
    # happens when an override is unset (vs set to empty string, which for
    # some prefs like general.useragent.override means literally empty UA).
    if extra_prefs:
        for k, v in extra_prefs.items():
            if v is None:
                prefs.pop(k, None)
            else:
                prefs[k] = v

    return prefs
