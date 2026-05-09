"""Public dataclass surface for fpforge."""
from __future__ import annotations

from dataclasses import dataclass, field, replace as _dc_replace
from typing import Any, Dict, List, Optional

from ._sampler import sample as _sample_raw


@dataclass(frozen=True)
class GPUProfile:
    vendor: str
    renderer: str
    class_tier: str        # "low_end" | "mid_range" | "high_end" | "integrated_old" | "integrated_modern"


@dataclass(frozen=True)
class ScreenProfile:
    width: int
    height: int
    avail_width: int
    avail_height: int
    dpr: float
    tier: str


@dataclass(frozen=True)
class HardwareProfile:
    concurrency: int
    storage_quota_mb: int


@dataclass(frozen=True)
class AudioProfile:
    sample_rate: int
    output_latency_ms: int
    max_channel_count: int


@dataclass(frozen=True)
class CodecProfile:
    av1_enabled: bool
    webm_encoder_enabled: bool
    mediasource_webm: bool
    mediasource_mp4: bool
    webspeech_synth: bool


@dataclass(frozen=True)
class WebGLProfile:
    msaa_samples: int


# ──────────────────────────────────────────────────────────────────────
#  Pin map: flat dotted-path -> value. Set via `pin=` on generate_profile.
#
#  Supported keys:
#      "gpu.vendor", "gpu.renderer", "gpu.class_tier"
#      "screen.width", "screen.height", "screen.avail_width",
#      "screen.avail_height", "screen.dpr", "screen.tier"
#      "hardware.concurrency", "hardware.storage_quota_mb"
#      "audio.sample_rate", "audio.output_latency_ms",
#      "audio.max_channel_count"
#      "codec.av1_enabled", "codec.webm_encoder_enabled",
#      "codec.mediasource_webm", "codec.mediasource_mp4",
#      "codec.webspeech_synth"
#      "webgl.msaa_samples"
#      "fonts"            (replaces the whole list)
#      "dark_theme"
# ──────────────────────────────────────────────────────────────────────

_PIN_GROUPS = {
    "gpu": {"vendor", "renderer", "class_tier"},
    "screen": {"width", "height", "avail_width", "avail_height", "dpr", "tier"},
    "hardware": {"concurrency", "storage_quota_mb"},
    "audio": {"sample_rate", "output_latency_ms", "max_channel_count"},
    "codec": {
        "av1_enabled", "webm_encoder_enabled",
        "mediasource_webm", "mediasource_mp4", "webspeech_synth",
    },
    "webgl": {"msaa_samples"},
}
_PIN_TOP = {"fonts", "dark_theme"}


def _validate_pin_key(key: str) -> None:
    if key in _PIN_TOP:
        return
    if "." not in key:
        raise ValueError(
            f"pin key {key!r} is not valid. "
            f"Use 'group.field' (e.g. 'screen.width') or one of {sorted(_PIN_TOP)}."
        )
    group, field_name = key.split(".", 1)
    if group not in _PIN_GROUPS:
        raise ValueError(
            f"pin key {key!r}: unknown group {group!r}. "
            f"Known groups: {sorted(_PIN_GROUPS)}."
        )
    if field_name not in _PIN_GROUPS[group]:
        raise ValueError(
            f"pin key {key!r}: unknown field {field_name!r} in group {group!r}. "
            f"Known fields: {sorted(_PIN_GROUPS[group])}."
        )


@dataclass(frozen=True)
class Profile:
    """Coherent browser fingerprint profile sampled from a single integer seed.

    Use `generate_profile(seed)` to build one. Pin specific values at build
    time with `generate_profile(seed, pin={"screen.width": 2560, ...})`.
    """
    seed: int
    gpu: GPUProfile
    screen: ScreenProfile
    hardware: HardwareProfile
    audio: AudioProfile
    codec: CodecProfile
    webgl: WebGLProfile
    fonts: List[str]
    dark_theme: bool
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_prefs_dict(self) -> Dict[str, Any]:
        """Return the flat dict of raw sampler fields, as produced by the
        underlying Bayesian sampler. Stable across releases for a given seed."""
        return dict(self._raw)


# Mapping from flat pin key -> raw sampler dict key, so `to_prefs_dict()`
# and `stealthfox.prefs.translate_profile_to_prefs` observe the pinned value.
_PIN_TO_RAW = {
    "gpu.vendor": "webgl_vendor",
    "gpu.renderer": "webgl_renderer",
    "gpu.class_tier": "gpu_class",
    "screen.width": "screen_w",
    "screen.height": "screen_h",
    "screen.avail_width": "screen_avail_w",
    "screen.avail_height": "screen_avail_h",
    "screen.dpr": "dpr",
    "screen.tier": "screen_tier",
    "hardware.concurrency": "hw_concurrency",
    "hardware.storage_quota_mb": "storage_quota_mb",
    "audio.sample_rate": "audio_sample_rate",
    "audio.output_latency_ms": "audio_output_latency_ms",
    "audio.max_channel_count": "audio_max_channel_count",
    "codec.av1_enabled": "av1_enabled",
    "codec.webm_encoder_enabled": "webm_encoder_enabled",
    "codec.mediasource_webm": "mediasource_webm",
    "codec.mediasource_mp4": "mediasource_mp4",
    "codec.webspeech_synth": "webspeech_synth",
    "webgl.msaa_samples": "msaa_samples",
    "dark_theme": "dark_theme",
    # "fonts" is a list — handled specially (joined into font_whitelist).
}


def _apply_pins_to_raw(raw: Dict[str, Any], pin: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of `raw` with the pinned sampler-level fields updated."""
    out = dict(raw)
    for key, value in pin.items():
        if key == "fonts":
            if not isinstance(value, (list, tuple)):
                raise TypeError("pin 'fonts' must be a list/tuple of strings")
            out["font_whitelist"] = ",".join(value)
            continue
        raw_key = _PIN_TO_RAW.get(key)
        if raw_key is None:
            # Shouldn't happen after validation, but guard anyway.
            continue
        out[raw_key] = value
    return out


def generate_profile(seed: int, pin: Optional[Dict[str, Any]] = None) -> Profile:
    """Return a deterministic Profile for the given integer seed.

    pin: optional dict of dotted-path keys (e.g. "screen.width", "gpu.renderer")
        to values that are FORCED in the resulting profile. All other fields
        are still sampled from the Bayesian network based on `seed`, so the
        same seed + same pin map always yields the same profile.

        Example — force a specific GPU and screen while letting everything
        else vary with the seed (via the public stealthfox API):

            from stealthfox import Stealthfox

            with Stealthfox(
                seed=42,
                pin={
                    "gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)",
                    "gpu.vendor":   "Google Inc. (NVIDIA)",
                    "gpu.class_tier": "high_end",
                    "screen.width":  2560,
                    "screen.height": 1440,
                },
            ) as browser:
                ...

        Warning: pinning breaks Bayesian coherence across the pinned fields
        (if you pin a high-end GPU but leave screen unpinned, you may get a
        1080p screen that would be unusual for that GPU class). Pin related
        fields together when coherence matters.

        Supported keys: see the module-level _PIN_GROUPS / _PIN_TOP tables
        or run `help(generate_profile)` after import.
    """
    if pin:
        for key in pin:
            _validate_pin_key(key)

    raw = _sample_raw(int(seed))
    if pin:
        raw = _apply_pins_to_raw(raw, pin)

    # Font whitelist is stored as a comma-separated string in raw; split it.
    font_wl = raw.get("font_whitelist", "")
    if isinstance(font_wl, str):
        fonts = [f.strip() for f in font_wl.split(",") if f.strip()]
    else:
        fonts = list(font_wl) if font_wl else []

    return Profile(
        seed=int(raw["stealth_seed"]),
        gpu=GPUProfile(
            vendor=raw["webgl_vendor"],
            renderer=raw["webgl_renderer"],
            class_tier=raw["gpu_class"],
        ),
        screen=ScreenProfile(
            width=int(raw["screen_w"]),
            height=int(raw["screen_h"]),
            avail_width=int(raw["screen_avail_w"]),
            avail_height=int(raw["screen_avail_h"]),
            dpr=float(raw["dpr"]),
            tier=str(raw.get("screen_tier", "")),
        ),
        hardware=HardwareProfile(
            concurrency=int(raw["hw_concurrency"]),
            storage_quota_mb=int(raw["storage_quota_mb"]),
        ),
        audio=AudioProfile(
            sample_rate=int(raw["audio_sample_rate"]),
            output_latency_ms=int(raw["audio_output_latency_ms"]),
            max_channel_count=int(raw["audio_max_channel_count"]),
        ),
        codec=CodecProfile(
            av1_enabled=bool(raw["av1_enabled"]),
            webm_encoder_enabled=bool(raw["webm_encoder_enabled"]),
            mediasource_webm=bool(raw["mediasource_webm"]),
            mediasource_mp4=bool(raw["mediasource_mp4"]),
            webspeech_synth=bool(raw["webspeech_synth"]),
        ),
        webgl=WebGLProfile(msaa_samples=int(raw["msaa_samples"])),
        fonts=fonts,
        dark_theme=bool(raw["dark_theme"]),
        _raw=raw,
    )
