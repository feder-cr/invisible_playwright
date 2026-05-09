# Pinning fingerprint fields

By default, every field of the fingerprint is sampled from a Bayesian network of real-world Firefox telemetry, seeded by an integer. Pass the same `seed` and you get the same fingerprint; omit it and each session is fresh.

`pin` lets you **force specific fields** while letting the rest stay seed-derived. Useful when you need to replicate a known device (e.g. an NVIDIA 1080p laptop), test a specific GPU/screen combo, or pin just one noisy signal that a target site weighs heavily.

```python
from stealthfox import Stealthfox

with Stealthfox(
    seed=42,
    pin={
        "gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)",
        "gpu.vendor":   "Google Inc. (NVIDIA)",
        "screen.width":  2560,
        "screen.height": 1440,
        "hardware.concurrency": 16,
    },
) as browser:
    ...
```

## How sampling + pinning interact

The generator is a Bayesian network: every field has a probability distribution **conditioned on its parents**. For example `gpu_class_tier` conditions `screen.tier`, `hardware.concurrency` and `webgl.msaa_samples`. A high-end GPU will tend to pair with a 2560x1440+ screen and 16+ cores.

When you pin a field:

1. The pinned value is written directly, bypassing the sampler.
2. **Unpinned children are still sampled from their conditionals** — using the parent's original posterior, not the pinned value.

That last point is the subtle one: pinning breaks the conditional chain. If you pin `gpu.renderer` to an RTX 4090 string but leave `screen` unpinned, the sampler will pick `screen` from the seed-derived tier (which might be `low_end`), producing a physically implausible "RTX 4090 + 1366x768" pairing.

**Rule of thumb:** pin correlated fields together, or just trust the sampler.

## Full list of pinnable keys

Keys are dotted paths. All values are optional — omitted keys fall back to the sampler.

### `gpu.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `gpu.class_tier` | str | `"high_end"` | The **root** of the Bayesian network. One of `"low_end"`, `"mid_range"`, `"high_end"`, `"integrated_old"`, `"integrated_modern"`. Pin this alone to steer the whole profile (screen, concurrency, MSAA, …) toward a coherent tier without having to name each sub-field. |
| `gpu.vendor` | str | `"Google Inc. (NVIDIA)"` | Must exactly match the renderer vendor prefix, otherwise detectors catch the mismatch. |
| `gpu.renderer` | str | `"ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)"` | Windows ANGLE string. Used by WebGL `UNMASKED_RENDERER_WEBGL`. |

**Why `class_tier` is pinnable separately from `renderer`.** They live at different levels of abstraction:

- `class_tier` is a **coarse handle** over the whole Bayesian graph. It gates the distribution of `screen`, `hardware.concurrency`, `webgl.msaa_samples`, and storage quota. Pin `{"gpu.class_tier": "low_end"}` and the sampler returns a *coherent* low-end machine — small screen, 2-4 cores, 4x MSAA — without you having to specify each field.
- `renderer` is an **exact string** that lands verbatim in WebGL's `UNMASKED_RENDERER_WEBGL`. Useful when you want to imitate a specific GPU the target site has seen before. Does **not** condition other fields — if you pin `renderer` to an RTX 4090 but leave `class_tier` unpinned, `class_tier` is re-sampled from scratch and might disagree with the renderer string (see [How sampling + pinning interact](#how-sampling--pinning-interact)).

In practice most users should pin `class_tier` alone, or pin `renderer`+`vendor`+`class_tier` together if they want full control.

### `screen.*`

| Key | Type | Example |
|-----|------|---------|
| `screen.width` | int | `2560` |
| `screen.height` | int | `1440` |
| `screen.avail_width` | int | `2560` |
| `screen.avail_height` | int | `1400` |
| `screen.dpr` | float | `1.0`, `1.25`, `1.5`, `2.0` |
| `screen.tier` | str | `"1080p"`, `"1440p"`, `"4k"`, ... |

### `hardware.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `hardware.concurrency` | int | `16` | `navigator.hardwareConcurrency`. |
| `hardware.storage_quota_mb` | int | `10_000` | `navigator.storage.estimate().quota / 1024²`. |

### `audio.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `audio.sample_rate` | int | `48000`, `44100` | `AudioContext.sampleRate`. |
| `audio.output_latency_ms` | float | `20.0` | `AudioContext.outputLatency * 1000`. |
| `audio.max_channel_count` | int | `2`, `6`, `8` | `AudioDestinationNode.maxChannelCount`. |

### `codec.*` (booleans)

| Key | Effect |
|-----|--------|
| `codec.av1_enabled` | `true` → `canPlayType('video/av01')` returns `"probably"`. |
| `codec.webm_encoder_enabled` | `MediaRecorder` advertises WebM support. |
| `codec.mediasource_webm` | `MediaSource.isTypeSupported('video/webm')`. |
| `codec.mediasource_mp4` | `MediaSource.isTypeSupported('video/mp4')`. |
| `codec.webspeech_synth` | `speechSynthesis.getVoices()` returns a fabricated voice list. |

### `webgl.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `webgl.msaa_samples` | int | `4`, `8`, `16` | `MAX_SAMPLES` WebGL parameter. Conditioned on `gpu.class_tier` when sampled. |

### Top-level

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `fonts` | list[str] | `["Arial", "Segoe UI", ...]` | Complete font allowlist. **Every other font is hidden**. The sampler usually picks 14–24 system fonts. |
| `dark_theme` | bool | `False` | `prefers-color-scheme: dark`. Real traffic is ~85% light, 15% dark. |

## Reading the chosen values back

Every sampled (or pinned) value lands in a `zoom.stealth.*` pref inside the browser. Open `about:config` in a launched stealthfox session and filter for `zoom.stealth` to see the exact values in effect.

Alternatively, inspect the instance before the `with` block exits:

```python
sf = Stealthfox(seed=42)
with sf as browser:
    # sf.seed is set; the full profile is in browser's prefs
    ...
```

## Common patterns

### Mimic a specific real device

Pin the whole visible tuple — GPU, screen, concurrency, fonts, audio:

```python
pin = {
    "gpu.vendor":   "Google Inc. (Intel)",
    "gpu.renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11)",
    "gpu.class_tier": "mid_range",
    "screen.width":  1920,
    "screen.height": 1080,
    "screen.dpr":    1.0,
    "hardware.concurrency": 8,
    "audio.sample_rate": 48000,
}
```

### Test the low-end GPU path only

```python
pin = {"gpu.class_tier": "low_end"}
# screen, msaa, concurrency re-sample from the seed but conditioned
# correctly on the low-end tier.
```

