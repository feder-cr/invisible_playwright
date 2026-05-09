# -*- coding: utf-8 -*-
"""stealth_forge — Bayesian fingerprint generator for Firefox 150 Windows.

Everything the Firefox build exposes to JS (screen, hardwareConcurrency,
WebGL, audio, MSAA, theme, media codecs) is sampled from a Bayesian network
with coherent cross-field dependencies. Identity (userAgent, platform,
oscpu, webdriver=false, maxTouchPoints=0) is locked by the compiled build.

Graph:

    gpu (root, 444 real Windows ANGLE renderers)
     │
     └─> gpu_class (deterministic classifier, 6 classes)
          ├─> hw_concurrency       (CPT per class)
          ├─> screen (w/h/dpr/av)  (CPT per class)
          └─> msaa_samples         (CPT per class)

    audio (root, joint rate+latency+channels — marginal)
    dark_theme                     (marginal)
    av1_enabled                    (marginal)
    webm_encoder_enabled           (marginal)

    font_exclude  ← deterministic hash of stealth_seed (seed-derived)

CPTs live in `data/*.json` (easy to tune without code changes).
Sampling is deterministic per stealth_seed via a private random.Random.
"""
import json
import os
import re
from typing import Any, Dict

from ._network import Network, Node

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(filename: str) -> Any:
    with open(os.path.join(_HERE, "data", filename), "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════
#  LOCKED IDENTITY (compiled into our Firefox 150 build — never varies)
# ═══════════════════════════════════════════════════════════════════════
_LOCKED: Dict[str, Any] = {
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
        "Gecko/20100101 Firefox/150.0.1"
    ),
    "platform": "Win32",
    "oscpu": "Windows NT 10.0; Win64; x64",
    "app_code_name": "Mozilla",
    "app_version": "5.0 (Windows)",
    "product_sub": "20100101",
    "webdriver": False,
    "max_touch_points": 0,
}


# ═══════════════════════════════════════════════════════════════════════
#  DATA
# ═══════════════════════════════════════════════════════════════════════
_GPU_POOL = _load("webgl_renderer_pool.json")["entries"]
# hwc/screen/storage now keyed on (gpu_class, intra_tier) for triangulation
_CPT_HWC = _load("cpt_hwc_given_class_tier.json")["table"]
_CPT_SCREEN = _load("cpt_screen_given_class_tier.json")["table"]
_CPT_STORAGE = _load("cpt_storage_given_class_tier.json")["table"]
# Hidden tier variable that makes hwc/screen/storage jointly coherent
_CPT_INTRA_TIER = _load("cpt_intra_tier_given_class.json")["table"]
# MSAA depends on (gpu_class, screen_tier) — 4K gaming → MSAA=0, 1080p+GPU → MSAA=4
_CPT_MSAA = _load("cpt_msaa_given_class_screen.json")["table"]
# Codec unchanged
_CPT_CODEC = _load("cpt_codec_given_class.json")["table"]
# Audio now conditional on gpu_class (workstation → pro audio, old → 44.1kHz onboard)
_CPT_AUDIO = _load("cpt_audio_given_class.json")["table"]
_INDEP = _load("priors_independent.json")
_FONT_POOL = _load("font_pool.json")
# Each entry is a dict {"name": "<lowercase family>", "factor": float}.
# - name: the font family advertised to the page.
# - factor: per-family width scale used by the consumer to make the family
#   detectable by width-diff probes.
# Core = always-included; Optional = sampled with P(font | gpu_class).
_FONT_CORE: list = _FONT_POOL["core"]
_FONT_OPTIONAL: list = _FONT_POOL["optional"]
_CPT_FONTS_OPT = _load("cpt_fonts_optional_given_class.json")["table"]


# ═══════════════════════════════════════════════════════════════════════
#  GPU CLASSIFIER (deterministic function of gpu → gpu_class)
# ═══════════════════════════════════════════════════════════════════════
_GPU_CLASSES = (
    "integrated_old", "integrated_modern", "low_end",
    "mid_range", "high_end", "workstation",
)


def classify_gpu(gpu_value: Dict[str, str]) -> str:
    """Deterministic: maps (renderer, vendor) dict to one of 6 classes.

    See data/cpt_*.json — each CPT table has an entry for every class.
    """
    r = gpu_value.get("renderer", "")

    if re.search(r"Intel.*HD Graphics (3000|4000|2500)", r):
        return "integrated_old"
    if re.search(
        r"Intel.*(HD Graphics (4[56]|5\d\d|6\d\d)|UHD Graphics|Graphics Family|Iris|Arc)",
        r,
    ):
        return "integrated_modern"
    if re.search(
        r"AMD.*(Radeon(\(TM\))? (Graphics|6\d\dM|7\d\dM|8\d\dM)|Vega [0-9]|"
        r"Renoir|Rembrandt|TM Graphics)",
        r, re.IGNORECASE,
    ):
        return "integrated_modern"

    # NVIDIA: Firefox SanitizeRenderer.cpp collapses every GeForce into one of
    # 3 vintage buckets (8800 GTX / GTX 480 / GTX 980). The renderer string
    # exposed to JS is therefore vintage; pairing it with modern cores/screen
    # creates an internal mismatch that FP Pro's tampering_ml flags. We pick
    # `low_end` for all 3 buckets so cores stay 4-12 and screen 1080-1440p,
    # consistent with what a real user with each of those (vintage) cards
    # would have. Workstation overrides keep their high-tier classification.
    if re.search(
        r"(GeForce (8\d\d\d?|9\d\d\d?|GTX 980|GTX 480|GT 1030|GT 710|GT 730|"
        r"GT 220|GT 240|210|310)|Quadro K\d|Radeon HD [1234]\d\d\d)", r,
    ):
        return "low_end"

    # NVIDIA discrete (any other GeForce — should be rare after the pool was
    # collapsed to the 3 sanitize buckets, but kept as a safety net).
    m = re.search(r"GeForce\s+(?:GTX\s+|RTX\s+)?(\d{3,4})", r)
    if m:
        if "Quadro" in r or "Workstation" in r:
            return "workstation"
        # Anything that survives the sanitize collapse stays low_end to avoid
        # the modern-cores/vintage-renderer pairing.
        return "low_end"

    # AMD discrete
    m = re.search(r"Radeon[^0-9]*(\d{3,4})", r)
    if m:
        n = int(m.group(1))
        if "FirePro" in r or "Radeon Pro" in r:
            return "workstation"
        if n >= 5700:
            return "high_end"
        if 5500 <= n <= 5600 or 580 <= n <= 590:
            return "mid_range"
        return "low_end"

    # Fallback
    return "mid_range"


# ═══════════════════════════════════════════════════════════════════════
#  NETWORK CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════
# Build once at import — the network is stateless, only the RNG varies.

def _gpu_marginal():
    """Build marginal distribution over GPU pool (uniform for now)."""
    n = len(_GPU_POOL)
    p = 1.0 / n
    return [{"value": g, "prob": p} for g in _GPU_POOL]


def _cpt_from_table(table: Dict[str, Any]) -> Dict[str, list]:
    """CPT for conditional nodes: `{class_name: [{value, prob}, ...]}`."""
    return dict(table)


def _screen_tier(ctx):
    """Classify screen width into tier for (gpu_class, screen_tier) CPTs."""
    s = ctx.get("screen", {}) or {}
    w = int(s.get("w", 1920))
    h = int(s.get("h", 1080))
    # Ultrawide: aspect ratio > 2.1 (e.g. 3440x1440, 5120x1440)
    if h > 0 and (w / h) > 2.1:
        return "ultrawide"
    if w <= 1920:
        return "1080p"
    if w <= 2560:
        return "1440p"
    if w <= 3840:
        return "2160p"
    return "ultrawide"


_NETWORK = Network([
    Node("gpu", parents=[], cpt=_gpu_marginal()),
    Node("gpu_class", parents=["gpu"], classifier=lambda ctx: classify_gpu(ctx["gpu"])),
    # Hidden variable: within a gpu_class, user's OTHER components (RAM, SSD,
    # cores, screen) correlate — a 'premium' mid_range user has more cores,
    # larger SSD, higher-res screen than a 'budget' mid_range user. Without
    # this, hwc/screen/storage would be independent given gpu_class (noisy).
    Node("intra_tier", parents=["gpu_class"], cpt=_cpt_from_table(_CPT_INTRA_TIER)),
    # hwc/screen/storage now jointly coherent via (gpu_class, intra_tier).
    Node("hw_concurrency", parents=["gpu_class", "intra_tier"],
         cpt=_cpt_from_table(_CPT_HWC)),
    Node("screen", parents=["gpu_class", "intra_tier"],
         cpt=_cpt_from_table(_CPT_SCREEN)),
    # Derive screen_tier from screen for msaa parent lookup.
    Node("screen_tier", parents=["screen"], classifier=_screen_tier),
    # MSAA: realistic combo (4K + high_end GPU → MSAA=0 due to perf cost;
    # 1080p + high_end → MSAA=4 common; 1080p + integrated → MSAA=0).
    Node("msaa_samples", parents=["gpu_class", "screen_tier"],
         cpt=_cpt_from_table(_CPT_MSAA)),
    # Joint codec distribution (gpu_class only).
    Node("codec", parents=["gpu_class"], cpt=_cpt_from_table(_CPT_CODEC)),
    # Storage quota: coherent within gpu_class × intra_tier (premium workstation
    # user → 2-3TB SSD; budget workstation user → 512GB; budget integrated_old
    # → 128GB).
    Node("storage_quota_mb", parents=["gpu_class", "intra_tier"],
         cpt=_cpt_from_table(_CPT_STORAGE)),
    # Audio: pro users (workstation) → 48/96kHz 6-8ch; old onboard → 44.1kHz
    # 2ch high latency. Workstation GPU + 44.1kHz mono was previously
    # implausible; now blocked by the CPT.
    Node("audio", parents=["gpu_class"], cpt=_cpt_from_table(_CPT_AUDIO)),
    Node("dark_theme", parents=[], cpt=_INDEP["dark_theme"]["table"]),
])


# ═══════════════════════════════════════════════════════════════════════
#  FONT WHITELIST (Bayesian: core ∪ sampled_optional | gpu_class)
# ═══════════════════════════════════════════════════════════════════════
# Semantic flip: previously exclude-list (block N probed fonts per seed).
# Now whitelist (browser sees ONLY these fonts, everything else hidden).
# Core (~112): always included — fresh Win11 + Office 2021 English.
# Optional (~40): sampled per-session with P(present | gpu_class). Gives
# small realistic variance (~3-8 optional fonts differ per session) while
# keeping the profile strongly centered on 'typical Windows user'.


def derive_font_prefs(gpu_class: str, rng) -> Dict[str, str]:
    """Build COHERENT whitelist + metrics strings for the session.

    Sampling:
      - Core fonts always included.
      - Optional fonts sampled with P(font | gpu_class) from the CPT table.

    Returns:
      {
        "whitelist": "arial,calibri,marlett,...",
        "metrics":   "arial|0.978,calibri|0.934,marlett|0.855,..."
      }

    The whitelist is the list of font families to advertise. The metrics
    string encodes per-family width scale factors that the consumer can
    use to make each family detectable by width-diff font probes.

    Each entry in font_pool.json carries its own {name, factor} pair so the
    two pref strings are GUARANTEED coherent — no chance of a fabricated
    font with factor 1.0 (undetectable) or a metrics entry for a font not
    in the whitelist (useless).

    Markers & add-new-font: simply add an entry to font_pool.json:core (with
    a factor at least 4% away from 1.0) — no special-case code needed.
    """
    cpt = _CPT_FONTS_OPT.get(gpu_class)
    if cpt is None:
        cpt = _CPT_FONTS_OPT["integrated_modern"]
    included: list = list(_FONT_CORE)  # always present
    for entry in _FONT_OPTIONAL:
        name = entry["name"]
        p = cpt.get(name, 0.7)  # default 0.7 if CPT has no row for this font
        if rng.random() < p:
            included.append(entry)
    # Deterministic ordering: sort by name
    included.sort(key=lambda e: e["name"])
    whitelist = ",".join(e["name"] for e in included)
    metrics = ",".join(
        f'{e["name"]}|{e["factor"]:.3f}' for e in included
    )
    return {"whitelist": whitelist, "metrics": metrics}


# Back-compat shim: legacy callers still import derive_font_whitelist.
def derive_font_whitelist(gpu_class: str, rng) -> str:
    return derive_font_prefs(gpu_class, rng)["whitelist"]


# ═══════════════════════════════════════════════════════════════════════
#  PUBLIC API: Forge
# ═══════════════════════════════════════════════════════════════════════
import random


class Forge:
    """Fingerprint forge — single seed → coherent bundle."""

    def __init__(self, seed: int):
        self.seed = int(seed)
        self._rng = random.Random(self.seed)

    def sample(self) -> Dict[str, Any]:
        bundle = _NETWORK.sample(self._rng)
        gpu = bundle["gpu"]
        screen = bundle["screen"]
        audio = bundle["audio"]
        codec = bundle["codec"]
        return {
            # Seed tracking
            "stealth_seed": self.seed,
            # Locked identity
            **_LOCKED,
            # GPU (coherent pair from 444 pool)
            "webgl_renderer": gpu["renderer"],
            "webgl_vendor": gpu["vendor"],
            "gpu_class": bundle["gpu_class"],
            # Hidden-variable debug metadata (not a Firefox pref, just for
            # analysis / test result correlation tracking)
            "intra_tier": bundle["intra_tier"],
            "screen_tier": bundle["screen_tier"],
            # Screen (coherent with GPU class)
            "screen_w": int(screen["w"]),
            "screen_h": int(screen["h"]),
            "screen_avail_w": int(screen.get("aw", screen["w"])),
            "screen_avail_h": int(screen.get("ah", screen["h"] - 40)),
            "dpr": float(screen["dpr"]),
            # Hardware (coherent with GPU class)
            "hw_concurrency": int(bundle["hw_concurrency"]),
            # WebGL MSAA (coherent with GPU class)
            "msaa_samples": int(bundle["msaa_samples"]),
            # Audio (independent joint)
            "audio_sample_rate": int(audio["rate"]),
            "audio_output_latency_ms": int(audio["latency"]),
            "audio_max_channel_count": int(audio["channels"]),
            # Codec prefs (joint, coherent with GPU class). All 5 are
            # JS-visible: av1/webm_encoder via canPlayType/MediaRecorder,
            # mediasource_* via MediaSource.isTypeSupported, webspeech_synth
            # via 'speechSynthesis' in window (CreepJS voices probe).
            "av1_enabled": bool(codec["av1_enabled"]),
            "webm_encoder_enabled": bool(codec["webm_encoder_enabled"]),
            "mediasource_webm": bool(codec["mediasource_webm"]),
            "mediasource_mp4": bool(codec["mediasource_mp4"]),
            "webspeech_synth": bool(codec["webspeech_synth"]),
            # Storage quota MB (coherent with GPU class — workstation larger SSDs).
            "storage_quota_mb": int(bundle["storage_quota_mb"]),
            # Independent marginals
            "dark_theme": int(bundle["dark_theme"]),
            # Bayesian font prefs (coherent pair: whitelist + per-family
            # width scale metrics, both sampled from the same font_pool.json
            # and conditioned on gpu_class).
            **{
                f"font_{k}": v
                for k, v in derive_font_prefs(
                    bundle["gpu_class"], self._rng
                ).items()
            },
        }


def sample(seed: int) -> Dict[str, Any]:
    """Convenience: `Forge(seed).sample()`."""
    return Forge(seed).sample()
