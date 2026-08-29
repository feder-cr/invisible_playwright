---
title: "Is WebGPU a browser fingerprint?"
description: "Firefox ships WebGPU on Windows and navigator.gpu exposes a second GPU identity most stealth guides ignore: what it reveals and why it must match WebGL."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 14
---


# Is WebGPU a browser fingerprint?

Yes, and it is one that most stealth guides were written before it existed. WebGL has
been a fingerprinting surface for a decade and everyone patches it. WebGPU is the newer
one running right next to it, exposing the same graphics adapter through a completely
different API, and a browser that spoofs one while leaving the other untouched has just
created a contradiction it did not have before.

This page is what `navigator.gpu` reveals, why it is a distinct signal rather than a
duplicate of WebGL, the specific way it goes wrong, how to read both surfaces in one
session, and the honest limit of what fixing it buys you.

## What WebGPU actually exposes

[WebGPU](https://www.w3.org/TR/webgpu/) shipped in the Firefox 141 series on Windows, which means [`navigator.gpu`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/gpu) is now
a real object on a current desktop Firefox rather than `undefined`. Its mere presence is
already a signal: it tells a page the engine, the platform and roughly the version band
you are on, before you draw anything.

The identity lives one call deeper. `navigator.gpu.requestAdapter()` returns an adapter,
and the adapter carries an [`info`](https://developer.mozilla.org/en-US/docs/Web/API/GPUAdapterInfo) object with `vendor` and `architecture` strings, plus a
`limits` object full of integers: maximum texture dimensions, buffer sizes, workgroup
counts, bind group limits. Those numbers are not arbitrary. They are reported by the
driver for the actual GPU, so the set of them, taken together, narrows the hardware the
way the WebGL renderer string does, only with more fields and no vendor-imposed masking.

So there are now two GPU-identity APIs in the same browser:

- **WebGL**, read through `getParameter(UNMASKED_RENDERER_WEBGL)` and dozens of numeric
  parameters. The one every guide covers.
- **WebGPU**, read through `requestAdapter().info` and `adapter.limits`. The one most
  guides predate.

A detector that reads both and compares them is doing a consistency check, not a
fingerprint lookup, and that is the check that catches a half-finished disguise.

## Why a second GPU API is a fingerprint, not a duplicate

It would be convenient if WebGPU just echoed WebGL, because then patching one would cover
both. It does not. The two APIs describe the same physical adapter through different
vocabularies: WebGL reports a human-readable renderer string filtered through the
browser's own presentation rules, while WebGPU reports normalised vendor and architecture
tokens plus a table of hardware limits. A real machine produces two descriptions that are
different in wording but consistent in what they imply about the hardware underneath.

That consistency is the whole point. On a genuine Windows laptop the WebGL renderer, the
WebGPU vendor and architecture, and the WebGPU limits all trace back to one GPU, so they
agree by construction. Nobody arranged that agreement; it is a side effect of there being
one real device answering every question.

An automated browser only reproduces that agreement if whoever built it thought about the
second API. A stealth layer that rewrites the WebGL renderer to say "Windows NVIDIA" and
does nothing to `navigator.gpu` now ships a machine whose two GPU stories were written by
two different authors, which is exactly the shape of tell that
[reading a specific surface's raw value](browserleaks-canvas-webgl-hash.md) is designed to
expose.

## The failure mode: two GPU stories that disagree

The concrete break, worst case first, so it is obvious why this is worth a page.

Picture a page-level spoofing plugin that overrides the WebGL renderer to a plausible
desktop GPU. It was written when WebGPU did not exist in Firefox, so it never touches
`navigator.gpu`. On a current Windows Firefox the adapter is now present and answers
honestly about whatever the container actually has, or answers with a software fallback,
or is absent on a headless server with no GPU at all. Any of those three outcomes
contradicts the confident desktop GPU the WebGL layer just claimed:

- **Absent `navigator.gpu`** under a user agent that says current Windows Firefox is a
  version mismatch, because the real version ships it.
- **A software or fallback adapter** next to a hardware WebGL renderer string is the same
  contradiction as [a renderer string that does not match the pixels it draws](renderer-string-vs-render.md),
  just read through a second API.
- **A hardware adapter whose vendor and limits point at a different GPU** than the WebGL
  string names is two machines wearing one user agent.

None of these are automation flags in the `navigator.webdriver` sense. They are internal
inconsistency, and inconsistency is what the tampering-oriented suites reward you for
avoiding. The fix is not to patch WebGPU louder. It is to make sure both surfaces are
telling the same true story.

Because invisible_playwright is a real Firefox patched at the C++ level rather than a
JavaScript overlay, `navigator.gpu` reports what a genuine Windows Firefox reports for the
adapter it is running on, and the WebGL renderer persona is derived from the same seed, so
the two GPU surfaces are drawn from one identity instead of two. The job the product does
here is not to invent a WebGPU answer; it is to keep the WebGPU answer from contradicting
the WebGL one, in the same session, on the same machine.

## Reading both surfaces in one session

The launch is the [two-line change](quickstart.md) from stock Playwright, and after that
the `browser` object is an ordinary Playwright `Browser`, so you read WebGPU through a
normal `page.evaluate`. Pass a seed so the identity is fixed and the run is reproducible:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""async () => {
        const out = { hasWebGPU: 'gpu' in navigator };
        if (!out.hasWebGPU) return out;

        const adapter = await navigator.gpu.requestAdapter();
        if (!adapter) { out.adapter = null; return out; }

        out.gpuVendor = adapter.info && adapter.info.vendor;
        out.gpuArchitecture = adapter.info && adapter.info.architecture;
        out.maxTextureDimension2D = adapter.limits.maxTextureDimension2D;

        const gl = document.createElement('canvas').getContext('webgl');
        const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
        out.webglRenderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;

        return out;
    }""")

    print(report)
```

Read the two GPU descriptions side by side. The WebGPU vendor and architecture should be
consistent with the WebGL renderer string, and both should describe a Windows GPU rather
than a software rasterizer. Run it again with the same seed and every field should come
back identical; a value that changes between two reads in one session is randomising per
call and says so, which is the cheapest tampering check there is.

Then do the comparison that a verdict never gives you: run the same snippet on a stock
Windows Firefox on the same machine and diff the fields. This is the
compare-against-a-real-browser method applied to one surface, and anything that differs,
other than the exit address, is a candidate. If you need
to force a particular GPU rather than take the seed-derived one, the same rule applies: pin
the WebGL renderer and its WebGPU-side neighbours together, never one alone, or you
recreate the contradiction by hand.

## What this does not fix

WebGPU consistency is a fingerprint fix, and a fingerprint fix is not a session fix. Make
both GPU surfaces agree perfectly and you have removed one contradiction from the browser.
You have changed nothing about the four things that get consistent browsers blocked
anyway:

- **IP reputation.** A flawless GPU story on a datacenter address is a flawless GPU story
  on a datacenter address. The [checklist for a single-site block](playwright-detected-as-bot.md)
  puts the exit last because it is the expensive fix, not because it never matters.
- **Per-account quotas and rate limits.** These count actions, not adapters. No graphics
  API touches them.
- **Behaviour and timing.** Pointer motion, typing rhythm, and pacing are watched
  independently of any fingerprint, and for an agent the pause shaped like model latency
  is its own tell.
- **Everything the TLS handshake decides** before a single line of JavaScript runs, which
  no in-page GPU read can see or change.

invisible_playwright is built to look like a real browser driven by a real person, and
that is precisely why it clears most fingerprint, driver-layer and TLS checks: those layers
read as a genuine Firefox because they are one. It does not supply a clean network exit,
human pacing, or a budget of actions. You bring those. The product's job is to make sure
the browser is not the thing that gives you away, and the WebGPU-versus-WebGL match is one
more place where it quietly is not.

## Conclusion

WebGPU is a fingerprint the way WebGL is a fingerprint, with the extra hazard that it
arrived recently enough that a lot of spoofing code has never heard of it. The signal that
matters is not the WebGPU adapter in isolation; it is whether `navigator.gpu` and WebGL
describe the same GPU on the same machine, read in one session. A real patched Firefox gets
that agreement for free because there is one real identity answering both APIs. A
JavaScript overlay that patches only the surface it was written for gets a contradiction
instead. Read both, compare against a stock browser, keep the run reproducible with a seed,
and then go solve the address and the behaviour, because the browser was only ever part of
it.

## Short answers to the questions that lead here

**Is WebGPU a fingerprint?** Yes. `navigator.gpu` exposes adapter vendor and architecture
strings plus a table of device limits, which narrow the hardware much as the WebGL renderer
does, through a separate API.

**Does Firefox even support WebGPU?** On Windows, yes, since the 141 series. `navigator.gpu`
is a real object on a current desktop Firefox, and its absence under a current-version user
agent is itself a mismatch.

**Do I need to patch WebGPU separately from WebGL?** You need them to agree. They describe
the same GPU in different vocabularies, so the check that catches you is the contradiction
between them, not either one alone.

**Can I just delete navigator.gpu to hide it?** Removing a feature a real current Firefox
ships is its own tell. A suppressed or absent signal reads as automation, not as privacy.

**Does invisible_playwright handle this?** It reports what a genuine Windows Firefox reports
for the adapter, and derives the WebGL persona from the same seed, so the two GPU surfaces
do not contradict each other. It does not fix your IP, quotas or behaviour.

**Will fixing WebGPU stop me being blocked?** Only if the fingerprint was the reason. A
clean GPU story on a bad address, at machine speed, still loses. Fix the exit and the
pacing too.

## Sources

- [The WebGPU specification](https://www.w3.org/TR/webgpu/), for the adapter and
  `GPUAdapterInfo` interfaces, retrieved 2026-08-28.
- Mozilla Gfx Team Blog,
  [Shipping WebGPU on Windows in Firefox 141](https://mozillagfx.wordpress.com/2025/07/15/shipping-webgpu-on-windows-in-firefox-141/),
  retrieved 2026-08-28.
- This project's own fingerprint parity gates, which compare a seed-derived identity field
  by field against a known-good reference and treat a suppressed surface as a failure
  rather than a pass.

**See also:** [WebGL renderer strings](webgl-renderer-strings.md) for the older half of
the pair, [why the WebGL parameters must be identical to a real machine](webgl-parameters-are-identical.md),
and [reading the canvas and WebGL hashes on BrowserLeaks](browserleaks-canvas-webgl-hash.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. WebGPU got its own page the
week a consistency check read one surface we had covered and one we had not.*
