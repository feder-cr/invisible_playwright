---
title: "Pinning fingerprint fields"
description: "Force specific fingerprint fields like GPU model or screen size while the rest stays seed-derived. Control exactly what stays random and what stays fixed."
parent: "Documentation"
nav_order: 4
---


# Pinning fingerprint fields

`pin` lets you **force specific fingerprint fields** to a fixed value while everything else stays seed-derived. Use it to replicate a known device (e.g. an NVIDIA 1080p laptop), test a specific GPU/screen combo, or hold down just one noisy signal that a target site weighs heavily.

By default, every field of the fingerprint is sampled from a Bayesian network of real-world Firefox telemetry, seeded by an integer. Pass the same `seed` and you get the same fingerprint; omit it and each session is fresh. `pin` sits on top of that: it overrides individual fields without giving up the seed for the rest.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(
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

Pinning a field skips the sampler only for that field - every other field still draws from its own conditional distribution, using the parent's original posterior rather than the value you just pinned. A pinned value does not pull correlated fields along with it.

The generator is a Bayesian network: every field has a probability distribution **conditioned on its parents**. For example `gpu_class_tier` conditions `screen.tier` and the MSAA sample count (drawn, though no longer pinnable or emitted - see below). It does NOT condition `hardware.concurrency`: that one is a root, sampled from the real Windows marginal (`Node("hw_concurrency", parents=[])`), because core count is an OS-level fact rather than a GPU-conditioned one. A high-end GPU will tend to pair with a 2560x1440+ screen; the core count is drawn independently.

When you pin a field:

1. The pinned value is written directly, bypassing the sampler.
2. **Unpinned children are still sampled from their conditionals** - using the parent's original posterior, not the pinned value.

That last point is the subtle one: pinning breaks the conditional chain. If you pin `gpu.renderer` to an RTX 4090 string but leave `screen` unpinned, the sampler will pick `screen` from the seed-derived tier (which might be `low_end`), producing a physically implausible "RTX 4090 + 1366x768" pairing.

**Rule of thumb:** pin correlated fields together, or just trust the sampler.

## Full list of pinnable keys

Keys are dotted paths. All values are optional - omitted keys fall back to the sampler.

### `gpu.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `gpu.class_tier` | str | `"mid_range"` | Selects a persona of that class. Only classes the validated pool actually contains can be pinned; anything else raises. |
| `gpu.vendor` | str | `"Google Inc. (NVIDIA)"` | Selects a persona by vendor. Combined with `gpu.renderer` both must match the same persona. |
| `gpu.renderer` | str | see below | Selects a persona by its Windows ANGLE string, the one WebGL reports as [`UNMASKED_RENDERER_WEBGL`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info). |

**A GPU pin SELECTS a persona, it does not set a string.** This is the one place
in this table where you are not free to invent a value, and the reason is that
the renderer string does not travel alone. Around 81 `getParameter` values, the
shader-precision formats and the whole extension list belong to the same GPU, and
a detector cross-checks the name against them - a name over another card's
parameters is the mismatch that scores ~0.70 on a commercial checker. So the
engine ships a pool of validated personas, each one a complete and coherent set,
and a pin picks one of them.

Two consequences:

- **A renderer, vendor or class the pool cannot present is REFUSED**, with a
  `ValueError` that names the values that are available. It is not silently
  ignored. Up to 2026-09-15 it was: a pin for an RTX 4090 set the label on the
  profile object and left the browser reporting the seed's own GPU, so
  `InvisiblePlaywright(pin={"gpu.renderer": ...})` returned a profile that
  disagreed with the page. Do not hard-code the valid strings from this page -
  read them off the exception, which is generated from the pool itself.
- **A GPU pin conditions the rest of the profile.** Screen, concurrency, MSAA and
  [storage quota](hardware-concurrency-device-memory.md) are re-sampled around
  the pinned persona's class, so you cannot end up with a low-end GPU behind
  high-end storage. You do not need to pin `class_tier` alongside `renderer`;
  pinning `class_tier` alone is still the coarse handle, it just picks the
  persona too.

### `screen.*`

`screen.tier` was in this table until 2026-09-15 and is not a knob: pins are
applied after the sampler has drawn, so pinning the tier could not condition the
screen it names, and it emitted no preference. It is the sampler's own label for
the screen it chose, and it is still on the profile as one. Pin `screen.width`
and `screen.height` to choose a screen.

`codec.webspeech_synth` left the table the same day, and left the profile with
it. `media.webspeech.synth.enabled` is emitted as a constant `true`, which is
what retail Firefox does on desktop; the sampled field only ever made the
profile disagree with the browser, on about 12% of seeds.


| Key | Type | Example |
|-----|------|---------|
| `screen.width` | int | `2560` |
| `screen.height` | int | `1440` |
| `screen.avail_width` | int | `2560` |
| `screen.avail_height` | int | `1400` |
| `screen.dpr` | float | `1.0`, `1.25`, `1.5`, `2.0` |
| `screen.color_depth` | int | `24` | `screen.colorDepth` and `screen.pixelDepth`. Declared rather than read off the panel: the engine only returned a fixed 24 when resistFingerprinting was on, which we do not turn on because it is itself a tell, so before this it reported the real display - 30 on a wide-gamut monitor, and a persona claiming an office laptop with a 30-bit panel is a contradiction a page can read. |

### `hardware.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `hardware.concurrency` | int | `16` | [`navigator.hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency). |
| `hardware.storage_quota_mb` | int | `10_000` | `navigator.storage.estimate().quota / 1024**2`. |
| `hardware.max_touch_points` | int | `0` | `navigator.maxTouchPoints`. `0` is what a desktop without a touchscreen reports, which is what the personas claim; it was a constant compiled into the binary until 2026-08-08, correct but not inspectable and not overridable. |
| `hardware.voices` | str | *(five en-US voices)* | The speechSynthesis voice list, as the engine parses it: `name\|lang\|default\|localService`, comma separated. Always the English (United States) set today, whatever locale the session resolved to - a real Windows machine running in Italian reports Italian voices, so an it-IT session declaring only American ones contradicts itself. The per-locale tables have to be measured on a real install of each locale, not invented; this field is the level at which that becomes possible. |
| `hardware.fake_media_devices` | bool | `True` | One fake audio input and one fake video input on every host, so `enumerateDevices` does not report the machine's real hardware. Measured in a secure context (about:blank is not one, and measuring there made the two hosts look like they agreed because both returned nothing): Linux enumerated 0 real devices and Windows 2. |
| `hardware.storage_enabled` | bool | `True` | Whether cookies, localStorage, sessionStorage and indexedDB all work. One field for four booleans because Gecko exposes them through two levers, not four. They used to be true because nobody touched `network.cookie.cookieBehavior` or `dom.storage.enabled`, i.e. because the upstream defaults happened to be right. |
| `hardware.generics` | str | *(20 rows)* | The CSS generic families, as `generic\|lang\|family` records separated by newlines. It was ten rows compiled into gfxPlatformFontList.cpp; the `x-math` row is load-bearing and easy to lose, because without it every MathML glyph renders in Times New Roman on every host, which no cross-OS gate can see. |
| `hardware.accessibility_overrides` | bool | `False` | Reduced motion, reduced transparency and inverted colours. Content-exposed media features that read the HOST through different code on each platform. They agreed across our two builds when measured - by luck, both machines having no accessibility settings on - and nothing declared them. Costs nothing to close: Firefox reads these generic prefs before the native path. |
| `screen.taskbar_px` | int | `48` | How much shorter `availHeight` is than `height`. It was the literal 48 in three places - the generator, nsScreen.cpp and nsGlobalWindowOuter.cpp - kept in step by hand. |
| `screen.chrome_w` | int | `0` | `outerWidth - innerWidth`, i.e. how much wider the window is than the page. Zero, because a real Firefox has no horizontal chrome: measured against stock 151, it answers 0 and this wrapper answered 14 for months. It was a module constant in `launcher.py`, so nothing could pin it and nothing could compare it to anything. |
| `screen.chrome_h` | int | `85` | `outerHeight - innerHeight`: tab strip plus navigation toolbar. Also measured against stock 151, which answers 85 where we answered 91. Pin it if a persona needs a bookmarks toolbar or a different tab density. |
| `screen.window_x` | int | `0` | `window.screenX`, and with it `screenLeft` and `mozInnerScreenX`. `outerWidth` already claims a maximized window filling the screen, and a maximized window is at the origin - but the position was never declared, so it stayed whatever the OS gave the headless widget: `(4,4)` on Windows, which put the right edge of a 1920-wide window at 1924 on a 1920 screen. That is impossible on a real machine and takes one addition to spot. |
| `screen.window_y` | int | `0` | `window.screenY` / `screenTop`, and the base of `mozInnerScreenY` (`window_y + chrome_h`). Pin both this and `window_x` together with a smaller viewport if you want a window that is not maximized. |

### `audio.*`

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `audio.sample_rate` | int | `48000`, `44100` | `AudioContext.sampleRate`. |
| `audio.output_latency_ms` | float | `20.0` | `AudioContext.outputLatency * 1000`. |
| `audio.max_channel_count` | int | `2`, `6`, `8` | `AudioDestinationNode.maxChannelCount`. |

### `codec.*` (booleans)

| Key | Effect |
|-----|--------|
| `codec.av1_enabled` | `true` -> `canPlayType('video/av01')` returns `"probably"`. |
| `codec.webm_encoder_enabled` | `MediaRecorder` advertises WebM support. |
| `codec.mediasource_webm` | `MediaSource.isTypeSupported('video/webm')`. |
| `codec.mediasource_mp4` | `MediaSource.isTypeSupported('video/mp4')`. |

### `webgl.*` - retired

There is no pinnable `webgl.*` key. `webgl.msaa_samples` was one until 2026-09-15,
and pinning it did nothing on Windows: the emitted sample count is held at 4 so
`gl.SAMPLES` is constant across sessions, because a varying count changes the
WebGL parameters hash even when the renderer does not. Honouring the pin on Linux
alone is what made the two builds emit different counts for the same seed - seven
of eight measured seeds. Both emit 4 now, so a pin has nothing left to move, and
the key refuses rather than looking like it worked.

### `font.*`

The Windows **system-font surface**: what a page reads from `font: menu` and the
other CSS system-font keywords, plus the default monospace size. Not the font
*list* - see the note about `fonts` below, which is a different thing that went
away for a different reason.

Unlike every other group here, these are **not sampled**. Every Windows machine
answers Segoe UI at 12px, so varying them per profile would manufacture a
diversity that does not exist in the population being imitated - the variation
would be the signal. They are pinnable for A/B work, not for realism.

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `font.ui_family` | str | `"Segoe UI"` | Family behind `font: menu`, `font: caption`, and the `-moz-` widget fonts. |
| `font.ui_size` | str | `"12"` | **A string, not an int.** Gecko reads this pref through `Preferences::GetFloat`, which parses the value from its text form; an int is not rejected, it is ignored, and the UI silently falls back to 16px. |
| `font.monospace_size` | int | `13` | Default monospace size. Firefox ships 13 on Windows and 12 in its Unix block, and the gap is directly readable as the width of the `monospace` generic at the default size. |
| `font.alpha_ladder` | tuple of int | `(0, 18, 35, ..., 255)` | The distinct alpha levels a Windows rasteriser leaves on an antialiased glyph edge, ascending, first `0` and last `255` so a fully transparent or fully opaque pixel never moves. Canvas readback snaps onto these. An empty tuple disables the snap, which is what you want when measuring what the unsnapped edge looks like. |
| `font.manifest` | str | *(the bundled manifest)* | The whole font manifest the engine parses: families, per-face vertical metrics, the alias table, the coverage ladder and the per-script fallback lists. Pin it to hand the engine a different font surface without rebuilding it. An empty string tells the engine to use the copy in its own directory. |
| `font.cleartype_gamma` | int | `2200` | DirectWrite's text gamma, x1000. One of six values the engine used to read from `IDWriteRenderingParams`, i.e. from the machine's own ClearType settings, which differ per monitor and per user. |
| `font.cleartype_contrast` | int | `100` | Enhanced contrast level, x100. |
| `font.cleartype_level` | int | `100` | ClearType level, x100. |
| `font.cleartype_pixel_structure` | int | `1` | Subpixel geometry: 0 flat, 1 RGB, 2 BGR. |
| `font.cleartype_rendering_mode` | int | `5` | DirectWrite rendering mode. |
| `font.freetype_gamma` | int | `220` | The FreeType equivalent of `cleartype_gamma`, x100. Declared so the Linux build rasterises with Windows' curve instead of Skia's linear default. |
| `font.freetype_contrast` | int | `100` | The FreeType equivalent of `cleartype_contrast`, x100. |

### Top-level

| Key | Type | Example | Notes |
|-----|------|---------|-------|
| `dark_theme` | bool | `False` | [`prefers-color-scheme: dark`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme). Real traffic is ~85% light, 15% dark. |

`dark_theme` is the ONLY top-level key. Anything else raises `ValueError: pin key
'...' is not valid`.

**`fonts` is not one of them, and no longer exists as an axis.** This table used
to list a per-profile font allowlist ("the sampler usually picks 14-24 system
fonts"). Passing it raises. The engine stopped varying fonts per profile when it
moved to a bundled font list: the exposed set is now the same 68 families on
every install and every OS, built from files the browser carries rather than
enumerated from the host, and the release gate asserts they are identical across
all five build legs with zero host fonts leaking. Varying it per profile would
put back the entropy the bundle exists to remove - so the right pin for fonts is
no pin.

**`browsing_history` is a profile field but is not pinnable either.** It is
generated from the seed (18-26 entries of `{name, category, cookie_profile}`),
so a fixed seed already fixes it. Read it back off the profile; do not pass it.

## Reading the chosen values back

Every sampled (or pinned) value lands in a `zoom.stealth.*` pref inside the browser. Open `about:config` in a launched invisible_playwright session and filter for `zoom.stealth` to see the exact values in effect.

Alternatively, inspect the instance before the `with` block exits:

```python
sf = InvisiblePlaywright(seed=42)
with sf as browser:
    # sf.seed is set; the full profile is in browser's prefs
    ...
```

## Common patterns

### Mimic a specific real device

Pin the whole visible tuple - GPU, screen, concurrency, audio:

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

## Short answers to the questions that lead here

**What can I pin?** Fields of the generated fingerprint, so specific values stay fixed
while everything else stays derived from the seed.

**Why pin instead of just choosing a seed?** A seed gives you one whole machine.
Pinning lets you hold one attribute steady, a GPU model or a screen size for example,
while the rest still varies.

**Can I pin anything I like?** No. Some fields are refused deliberately, because
setting them independently would produce a combination that does not occur on real
hardware.

**Does pinning make me easier to identify?** It can. Every value you fix is a value you
share with every other session that fixed it the same way, so pin the minimum you
actually need.

**What happens when I upgrade?** The rest of the profile can move with the engine while
pinned fields stay put. Keep a note of what you pinned and why, or a future mismatch is
hard to explain.

**See also:** [giving an agent a reproducible browser identity via `seed`](reproducible-agent-browser-identity-seed.md),
[what the WebGL renderer strings mean](webgl-renderer-strings.md), [hardwareConcurrency,
deviceMemory and storage quota](hardware-concurrency-device-memory.md), and
[why fonts are bundled rather than sampled per profile](bundled-fonts-cross-platform.md).
