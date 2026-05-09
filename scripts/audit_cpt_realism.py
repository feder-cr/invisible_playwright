"""Bayesian network realism audit (one-shot regenerator).

Each (gpu_class, intra_tier) cell maps to a real-world persona.
Distributions derived from:
- Steam HW Survey March 2026 Windows
  cores: 2c=3%, 4c=13%, 6c=29%, 8c=27%, 10c=7%, 12c=5%, 14c=5%, 16c=6%, 24c=3%, 32c=0.01%
- Intel ARK + AMD spec sheets (CPU release era -> cores)
- Tom's Hardware: most users 100-250GB free (= 512GB-1TB drives baseline)
"""
import json
import os
import sys

OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "src", "stealthfox", "_fpforge", "data",
)


def _norm(d):
    """Normalize prob dict / list of (val, prob) pairs to sum 1.0."""
    if isinstance(d, dict):
        total = sum(d.values())
        return [{"value": v, "prob": round(p / total, 4)} for v, p in d.items()]
    total = sum(p for _, p in d)
    return [{"value": v, "prob": round(p / total, 4)} for v, p in d]


def _scr(w, h, dpr=1.0):
    return {"w": w, "h": h, "aw": w, "ah": h - 40, "dpr": dpr}


# ============================================================
# HW concurrency per (gpu_class, intra_tier)
# ============================================================
HWC = {
    ("integrated_old", "budget"):       {2: 0.65, 4: 0.30, 8: 0.05},
    ("integrated_old", "standard"):     {2: 0.30, 4: 0.55, 8: 0.15},
    ("integrated_old", "premium"):      {4: 0.65, 8: 0.30, 12: 0.05},

    ("integrated_modern", "budget"):    {4: 0.55, 6: 0.20, 8: 0.20, 12: 0.05},
    ("integrated_modern", "standard"):  {6: 0.20, 8: 0.30, 10: 0.20, 12: 0.20, 16: 0.10},
    ("integrated_modern", "premium"):   {8: 0.20, 10: 0.20, 12: 0.30, 14: 0.15, 16: 0.15},

    ("low_end", "budget"):              {4: 0.50, 6: 0.25, 8: 0.20, 12: 0.05},
    ("low_end", "standard"):            {4: 0.10, 6: 0.35, 8: 0.30, 12: 0.18, 16: 0.07},
    ("low_end", "premium"):             {6: 0.10, 8: 0.30, 12: 0.30, 16: 0.22, 24: 0.08},

    ("mid_range", "budget"):            {6: 0.55, 8: 0.30, 12: 0.10, 16: 0.05},
    ("mid_range", "standard"):          {6: 0.40, 8: 0.30, 12: 0.18, 16: 0.10, 24: 0.02},
    ("mid_range", "premium"):           {6: 0.15, 8: 0.45, 12: 0.20, 16: 0.15, 24: 0.05},

    ("high_end", "budget"):             {6: 0.10, 8: 0.55, 12: 0.20, 16: 0.15},
    ("high_end", "standard"):           {8: 0.30, 12: 0.18, 14: 0.15, 16: 0.27, 24: 0.10},
    ("high_end", "premium"):            {12: 0.10, 14: 0.15, 16: 0.30, 24: 0.35, 32: 0.10},

    ("workstation", "budget"):          {8: 0.40, 12: 0.30, 16: 0.20, 24: 0.10},
    ("workstation", "standard"):        {12: 0.10, 16: 0.40, 24: 0.30, 32: 0.20},
    ("workstation", "premium"):         {24: 0.30, 32: 0.45, 48: 0.15, 64: 0.10},
}


# ============================================================
# Screen resolution per (gpu_class, intra_tier)
# Each value list is [(screen_obj, prob), ...].
# ============================================================
SCREEN = {
    ("integrated_old", "budget"):     [(_scr(1366, 768), 0.78), (_scr(1280, 800), 0.15), (_scr(1024, 768), 0.07)],
    ("integrated_old", "standard"):   [(_scr(1366, 768), 0.55), (_scr(1600, 900), 0.25), (_scr(1280, 800), 0.10), (_scr(1920, 1080), 0.10)],
    ("integrated_old", "premium"):    [(_scr(1600, 900), 0.45), (_scr(1920, 1080), 0.40), (_scr(1366, 768), 0.15)],

    ("integrated_modern", "budget"):  [(_scr(1366, 768), 0.30), (_scr(1920, 1080), 0.65), (_scr(1600, 900), 0.05)],
    ("integrated_modern", "standard"):[(_scr(1920, 1080), 0.70), (_scr(1920, 1200), 0.10), (_scr(2560, 1440), 0.12), (_scr(2560, 1600), 0.08)],
    ("integrated_modern", "premium"): [(_scr(1920, 1080), 0.30), (_scr(1920, 1200), 0.10), (_scr(2560, 1440), 0.25), (_scr(2560, 1600), 0.15), (_scr(3840, 2160), 0.20)],

    ("low_end", "budget"):    [(_scr(1920, 1080), 0.85), (_scr(1920, 1200), 0.10), (_scr(2560, 1440), 0.05)],
    ("low_end", "standard"):  [(_scr(1920, 1080), 0.60), (_scr(1920, 1200), 0.10), (_scr(2560, 1440), 0.25), (_scr(3840, 2160), 0.05)],
    ("low_end", "premium"):   [(_scr(1920, 1080), 0.25), (_scr(2560, 1440), 0.45), (_scr(3840, 2160), 0.20), (_scr(3440, 1440), 0.05), (_scr(1920, 1200), 0.05)],

    ("mid_range", "budget"):   [(_scr(1920, 1080), 0.75), (_scr(2560, 1440), 0.20), (_scr(1920, 1200), 0.05)],
    ("mid_range", "standard"): [(_scr(1920, 1080), 0.45), (_scr(2560, 1440), 0.40), (_scr(3840, 2160), 0.10), (_scr(3440, 1440), 0.05)],
    ("mid_range", "premium"):  [(_scr(1920, 1080), 0.15), (_scr(2560, 1440), 0.50), (_scr(3840, 2160), 0.20), (_scr(3440, 1440), 0.10), (_scr(2560, 1080), 0.05)],

    ("high_end", "budget"):    [(_scr(1920, 1080), 0.20), (_scr(2560, 1440), 0.55), (_scr(3840, 2160), 0.20), (_scr(3440, 1440), 0.05)],
    ("high_end", "standard"):  [(_scr(2560, 1440), 0.40), (_scr(3840, 2160), 0.40), (_scr(3440, 1440), 0.15), (_scr(5120, 1440), 0.05)],
    ("high_end", "premium"):   [(_scr(3840, 2160), 0.55), (_scr(2560, 1440), 0.15), (_scr(3440, 1440), 0.10), (_scr(5120, 1440), 0.15), (_scr(7680, 2160), 0.05)],

    ("workstation", "budget"):   [(_scr(2560, 1440), 0.55), (_scr(3840, 2160), 0.30), (_scr(1920, 1200), 0.10), (_scr(2560, 1600), 0.05)],
    ("workstation", "standard"): [(_scr(2560, 1440), 0.30), (_scr(3840, 2160), 0.45), (_scr(2560, 1600), 0.15), (_scr(3440, 1440), 0.10)],
    ("workstation", "premium"):  [(_scr(3840, 2160), 0.55), (_scr(2560, 1600), 0.15), (_scr(5120, 2160), 0.15), (_scr(3840, 2400), 0.10), (_scr(7680, 2160), 0.05)],
}


# ============================================================
# Storage quota MB per (gpu_class, intra_tier)
# ============================================================
STORAGE = {
    ("integrated_old", "budget"):     {32_000: 0.30, 64_000: 0.40, 128_000: 0.25, 256_000: 0.05},
    ("integrated_old", "standard"):   {64_000: 0.20, 128_000: 0.45, 256_000: 0.25, 500_000: 0.10},
    ("integrated_old", "premium"):    {128_000: 0.20, 256_000: 0.45, 500_000: 0.30, 1_000_000: 0.05},

    ("integrated_modern", "budget"):  {64_000: 0.20, 128_000: 0.30, 256_000: 0.30, 500_000: 0.20},
    ("integrated_modern", "standard"):{256_000: 0.25, 500_000: 0.45, 1_000_000: 0.25, 2_000_000: 0.05},
    ("integrated_modern", "premium"): {500_000: 0.25, 1_000_000: 0.50, 2_000_000: 0.20, 4_000_000: 0.05},

    ("low_end", "budget"):   {128_000: 0.20, 256_000: 0.50, 500_000: 0.25, 1_000_000: 0.05},
    ("low_end", "standard"): {256_000: 0.20, 500_000: 0.50, 1_000_000: 0.25, 2_000_000: 0.05},
    ("low_end", "premium"):  {500_000: 0.20, 1_000_000: 0.50, 2_000_000: 0.25, 4_000_000: 0.05},

    ("mid_range", "budget"):  {256_000: 0.15, 500_000: 0.50, 1_000_000: 0.30, 2_000_000: 0.05},
    ("mid_range", "standard"):{500_000: 0.20, 1_000_000: 0.55, 2_000_000: 0.20, 4_000_000: 0.05},
    ("mid_range", "premium"): {1_000_000: 0.40, 2_000_000: 0.45, 4_000_000: 0.15},

    ("high_end", "budget"):  {500_000: 0.15, 1_000_000: 0.50, 2_000_000: 0.30, 4_000_000: 0.05},
    ("high_end", "standard"):{1_000_000: 0.30, 2_000_000: 0.50, 4_000_000: 0.20},
    ("high_end", "premium"): {2_000_000: 0.40, 4_000_000: 0.45, 8_000_000: 0.15},

    ("workstation", "budget"):  {1_000_000: 0.30, 2_000_000: 0.50, 4_000_000: 0.20},
    ("workstation", "standard"):{2_000_000: 0.30, 4_000_000: 0.50, 8_000_000: 0.20},
    ("workstation", "premium"): {4_000_000: 0.35, 8_000_000: 0.45, 16_000_000: 0.20},
}


# ============================================================
# Audio profile per gpu_class
# ============================================================
AUDIO = {
    "integrated_old": [
        ({"rate": 44100, "latency": 50, "channels": 2}, 0.70),
        ({"rate": 48000, "latency": 50, "channels": 2}, 0.30),
    ],
    "integrated_modern": [
        ({"rate": 48000, "latency": 30, "channels": 2}, 0.60),
        ({"rate": 44100, "latency": 40, "channels": 2}, 0.25),
        ({"rate": 48000, "latency": 25, "channels": 6}, 0.15),
    ],
    "low_end": [
        ({"rate": 48000, "latency": 40, "channels": 2}, 0.55),
        ({"rate": 44100, "latency": 50, "channels": 2}, 0.30),
        ({"rate": 48000, "latency": 30, "channels": 6}, 0.15),
    ],
    "mid_range": [
        ({"rate": 48000, "latency": 25, "channels": 2}, 0.45),
        ({"rate": 48000, "latency": 20, "channels": 6}, 0.30),
        ({"rate": 48000, "latency": 20, "channels": 8}, 0.15),
        ({"rate": 44100, "latency": 30, "channels": 2}, 0.10),
    ],
    "high_end": [
        ({"rate": 48000, "latency": 15, "channels": 6}, 0.30),
        ({"rate": 48000, "latency": 15, "channels": 8}, 0.30),
        ({"rate": 48000, "latency": 15, "channels": 2}, 0.20),
        ({"rate": 96000, "latency": 15, "channels": 6}, 0.10),
        ({"rate": 96000, "latency": 15, "channels": 8}, 0.10),
    ],
    "workstation": [
        ({"rate": 48000, "latency": 10, "channels": 8}, 0.25),
        ({"rate": 96000, "latency": 10, "channels": 8}, 0.30),
        ({"rate": 96000, "latency": 10, "channels": 6}, 0.20),
        ({"rate": 192000, "latency": 10, "channels": 8}, 0.15),
        ({"rate": 48000, "latency": 15, "channels": 6}, 0.10),
    ],
}


# ============================================================
# Codec per gpu_class
# ============================================================
CODEC = {
    "integrated_old": [
        ({"av1_enabled": False, "webm_encoder_enabled": False, "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True}, 1.0),
    ],
    "integrated_modern": [
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.55),
        ({"av1_enabled": False, "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.35),
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": False}, 0.10),
    ],
    "low_end": [
        ({"av1_enabled": False, "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.85),
        ({"av1_enabled": False, "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": False}, 0.15),
    ],
    "mid_range": [
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.55),
        ({"av1_enabled": False, "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.35),
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": False}, 0.10),
    ],
    "high_end": [
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.85),
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": False}, 0.15),
    ],
    "workstation": [
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": True},  0.70),
        ({"av1_enabled": True,  "webm_encoder_enabled": True,  "mediasource_webm": True, "mediasource_mp4": True, "webspeech_synth": False}, 0.30),
    ],
}


# ============================================================
# MSAA per (gpu_class, screen_tier)
# screen_tier classifier in _sampler.py: "1080p", "1440p", "2160p", "ultrawide"
# Note: prefs.py clamps msaa to >=2 (antialias=True always), so msaa=0 is no longer
# emitted at the browser layer. CPT here keeps the 2/4/8 distribution matching what
# the prefs layer actually exposes.
# ============================================================
MSAA = {
    ("integrated_old", "1080p"):       [(2, 0.75), (4, 0.20), (8, 0.05)],
    ("integrated_old", "1440p"):       [(2, 0.85), (4, 0.15)],
    ("integrated_old", "2160p"):       [(2, 1.0)],
    ("integrated_old", "ultrawide"):   [(2, 1.0)],

    ("integrated_modern", "1080p"):    [(2, 0.50), (4, 0.40), (8, 0.10)],
    ("integrated_modern", "1440p"):    [(2, 0.60), (4, 0.35), (8, 0.05)],
    ("integrated_modern", "2160p"):    [(2, 0.85), (4, 0.15)],
    ("integrated_modern", "ultrawide"):[(2, 0.80), (4, 0.20)],

    ("low_end", "1080p"):              [(2, 0.40), (4, 0.45), (8, 0.15)],
    ("low_end", "1440p"):              [(2, 0.55), (4, 0.40), (8, 0.05)],
    ("low_end", "2160p"):              [(2, 0.85), (4, 0.15)],
    ("low_end", "ultrawide"):          [(2, 0.70), (4, 0.30)],

    ("mid_range", "1080p"):            [(2, 0.30), (4, 0.50), (8, 0.20)],
    ("mid_range", "1440p"):            [(2, 0.40), (4, 0.45), (8, 0.15)],
    ("mid_range", "2160p"):            [(2, 0.65), (4, 0.30), (8, 0.05)],
    ("mid_range", "ultrawide"):        [(2, 0.55), (4, 0.40), (8, 0.05)],

    ("high_end", "1080p"):             [(2, 0.20), (4, 0.45), (8, 0.35)],
    ("high_end", "1440p"):             [(2, 0.25), (4, 0.50), (8, 0.25)],
    ("high_end", "2160p"):             [(2, 0.40), (4, 0.45), (8, 0.15)],
    ("high_end", "ultrawide"):         [(2, 0.30), (4, 0.50), (8, 0.20)],

    ("workstation", "1080p"):          [(2, 0.15), (4, 0.50), (8, 0.35)],
    ("workstation", "1440p"):          [(2, 0.15), (4, 0.55), (8, 0.30)],
    ("workstation", "2160p"):          [(2, 0.25), (4, 0.55), (8, 0.20)],
    ("workstation", "ultrawide"):      [(2, 0.20), (4, 0.55), (8, 0.25)],
}


def write_pair_table(table, fname, meta):
    out = {"_meta": meta, "table": {}}
    for key_pair, dist in table.items():
        key = json.dumps(list(key_pair))
        out["table"][key] = _norm(dist)
    with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


def write_class_table(table, fname, meta):
    out = {"_meta": meta, "table": {}}
    for cls, dist in table.items():
        out["table"][cls] = _norm(dist)
    with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


def main():
    write_pair_table(HWC,     "cpt_hwc_given_class_tier.json",
                     "hardware_concurrency given (gpu_class, intra_tier)")
    write_pair_table(SCREEN,  "cpt_screen_given_class_tier.json",
                     "screen given (gpu_class, intra_tier)")
    write_pair_table(STORAGE, "cpt_storage_given_class_tier.json",
                     "storage_quota_mb given (gpu_class, intra_tier)")
    write_pair_table(MSAA,    "cpt_msaa_given_class_screen.json",
                     "msaa_samples given (gpu_class, screen_tier)")
    write_class_table(AUDIO,  "cpt_audio_given_class.json",
                      "audio (rate/latency/channels) given gpu_class")
    write_class_table(CODEC,  "cpt_codec_given_class.json",
                      "codec given gpu_class")
    print("All 6 CPT files updated.")


if __name__ == "__main__":
    main()
