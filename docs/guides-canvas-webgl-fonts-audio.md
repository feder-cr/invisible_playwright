---
title: "Canvas, WebGL, Fonts and Audio"
description: "The surfaces that are drawn or rendered rather than merely declared - canvas, WebGL, fonts and audio - and why that makes them harder to fake convincingly than a plain property."
parent: "Guides"
has_children: true
nav_order: 2
---


# Canvas, WebGL, Fonts and Audio

Everything in this group has the same shape: the value a page reads is not a
declared property, it is the output of something actually being rendered - a canvas
drawn, a WebGL context queried, a font measured, an audio buffer processed. That
makes these surfaces higher entropy than a plain property check, and also harder to
spoof convincingly, because the output has to agree with itself and with the
platform the browser claims to be, not just look plausible in isolation.

## Canvas and WebGL rendering

- [Canvas fingerprint noise: why per-call randomising fails](canvas-fingerprint-noise.md) - Per-call noise is flagged as masking when a detector reads twice.
- [Canvas and WebGL fingerprints, identical across OSes](canvas-webgl-cross-platform-consistency.md) - Intercept readback, not the render, so one seed hashes identically on every OS.
- [Your renderer string says NVIDIA. Your pixels say software.](renderer-string-vs-render.md) - You can spoof the GPU renderer string, but not the pixels a rasterizer draws.
- [WebGL parameters: the numbers are the same on every GPU](webgl-parameters-are-identical.md) - ANGLE clamps every card to the same limits, so raising them gets you caught.
- [Firefox WebGL renderer strings: what ANGLE reports](webgl-renderer-strings.md) - What renderer strings report per platform, and why a software renderer is hardest to explain.
- [WebGL shader precision as a fingerprint surface](webgl-shader-precision-fingerprint.md) - getShaderPrecisionFormat is a third WebGL fingerprint, hashed apart from parameters and extensions.
- [Is WebGPU a browser fingerprint?](is-webgpu-a-browser-fingerprint.md) - navigator.gpu exposes a second GPU identity most guides ignore, and it must match WebGL.

## Fonts, emoji and text metrics

- [How to make Linux and macOS report real Windows fonts](bundled-fonts-cross-platform.md) - Bundle the real font files and read only from them; filtering a list leaves the host underneath.
- [Detecting installed fonts in JavaScript by width](detect-installed-fonts-javascript.md) - A width-comparison probe tells present from absent, and headless browsers fail it.
- [Emoji fingerprinting: why emoji look the same on any OS](emoji-fingerprinting-cross-platform.md) - Firefox draws colour emoji from a bundled font, so they look identical everywhere.
- [Why headless browsers render different fonts](headless-fonts-differ.md) - Headless and headful render differently because of OS font configuration and rasterising.
- [measureText and TextMetrics as a fingerprinting surface](measuretext-textmetrics-fingerprinting.md) - These leak font and platform data with no prompt; per-glyph noise backfires, so we fixed it differently.

## Audio

- [AudioContext fingerprinting, and why adding noise backfired](audiocontext-fingerprinting.md) - We shipped the noise defence, measured it, and it made sessions easier to detect.
- [AudioContext sampleRate and latency as a fingerprint](audiocontext-samplerate-latency-fingerprint.md) - sampleRate, outputLatency and maxChannelCount come from the driver, so a headless server leaks.
