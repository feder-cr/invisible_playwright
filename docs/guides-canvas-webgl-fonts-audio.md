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
