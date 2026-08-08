---
title: "Is Playwright Headless Detectable?"
description: "Yes, classic headless leaks a distinct user agent token, degraded WebGL, default window metrics and thin fonts - why output parity beats per-tell patching."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 10
---


# Is Playwright Headless Detectable?

Historically, yes, and for a specific reason: headless mode used to answer several
questions differently from a browser with a window on screen. The honest short version is
that headless is only as detectable as the difference between its output and a real
desktop browser's output. Close that difference and "headless" stops being a signal. Leave
one field behind and it does not matter that the other three hundred match.

This page lists the classic headless-specific tells, explains why chasing them one at a
time is the slow way to lose, and shows the approach that actually holds: make the headless
output byte-identical to the headful output, so there is nothing left to detect. It ends
with the honest caveat, because parity closes the rendering tells and nothing else.

## The classic headless tells

These are the signals that made "is it headless" answerable at all. Every one of them is a
place where a browser without a window historically reported something a browser with a
window did not.

- **A headless user agent token.** Older headless Chromium literally shipped a
  `HeadlessChrome` token in `navigator.userAgent`. A single substring answered the whole
  question. This is the one everyone knows and the one every tool fixes first.
- **Missing or degraded WebGL.** No window often meant no real graphics context, so the
  [GPU vendor and renderer strings](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  came back as a software rasterizer, or empty. A software
  renderer under a desktop user agent is a datacenter tell, and
  [the string can even disagree with the pixels](webgl-renderer-strings.md).
- **Zero-size or default window metrics.** `window.outerWidth` and `outerHeight` at zero,
  an `availHeight` equal to the full screen height because there is no taskbar, a device
  pixel ratio no real display uses. These are [the screen-size tells](screen-size-headless-tells.md)
  that a headless launch produces without anyone asking for them.
- **A thin or different font set.** A headless or containerized environment enumerates the
  fonts that are actually installed, which is a shorter and different list than a real
  desktop, so [headless renders text with different fonts](headless-fonts-differ.md) and
  a font-probing page reads the gap.
- **Automation globals alongside all of the above.**
  [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
  and friends, which are
  [mostly solved and mostly not the interesting part](navigator-webdriver-explained.md),
  but which correlate with headless in a detector's mind.

Notice the shape: only the first is really about being headless. The rest are about the
environment that headless usually runs in - no GPU, no display, a minimal font install.
That distinction is the whole point of the next section.

## Why hunting each tell loses

The per-tell approach is to find each of those signals and patch it: rewrite the user
agent string, spoof a renderer, set a viewport, inject a font list. It feels like progress
because each fix makes one check go green.

It loses for two reasons. First, the list is not fixed. A patch that rewrites the user
agent does nothing for the renderer; a viewport override does nothing for the fonts; and a
new detector reads a field you have not thought about yet. You are always one signal
behind. Second, and worse, a patched value has to agree with every value you did not patch.
Spoof a high-end GPU string while the pixels are still drawn in software and you have
turned one tell into a contradiction, which [CreepJS](creepjs-explained.md) is built to
catch. Overcorrecting is how a fix becomes a new fingerprint.

The reliable move is to stop treating "headless" as a list of holes to plug and start
treating it as a single requirement: the output must not depend on whether a window is
drawn.

## Parity: make headless output identical to headful

The safest answer to "is it detectable" is to make the headless output byte-identical to
the headful output. If the two are the same, there is no headless-specific signal to find,
because the field a detector would read is the same field in both modes.

invisible_playwright reaches that parity by sourcing the values that would otherwise leak
from configuration and bundled assets rather than from the live rendering environment:

- The user agent and platform come from browser preferences derived from the seed, so
  there is no separate headless token to appear or not appear.
- The WebGL vendor and renderer, the canvas output, and the audio context are
  seed-determined values, so they are present and consistent whether or not a window is
  composited.
- The screen metrics and device pixel ratio are set as preferences, so `availHeight`,
  `outerWidth` and the pixel ratio describe a plausible desktop instead of a zero-size
  offscreen surface.
- The fonts are bundled and enumerated from that bundle, so the list matches the claimed
  platform instead of whatever a bare container happens to have installed.

Because every one of those comes from a preference or a shipped asset, not from the
presence of a drawn window, `headless=True` and `headless=False` return the same
fingerprint. That is not an aspiration here: the project's own release gates run
`headless=True` against the public detector suites, because headless is the mode that
ships, and testing the headful path would be testing something it does not deploy. See
[headless versus headful output](headless-vs-headful.md) for the field-by-field comparison.

## A runnable example

Switching from plain Playwright is two lines, and
[headless](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch)
is the default posture you want for a server anyway. The `browser` object is a real
Playwright `Browser`, so every method is the one you already know.

```python
from invisible_playwright import InvisiblePlaywright

# seed fixes the identity so the run is reproducible; headless is fine here,
# the fingerprint is the same as it would be headful
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    ua = page.evaluate("navigator.userAgent")
    renderer = page.evaluate(
        "() => { const c = document.createElement('canvas');"
        " const gl = c.getContext('webgl');"
        " const e = gl.getExtension('WEBGL_debug_renderer_info');"
        " return gl.getParameter(e.UNMASKED_RENDERER_WEBGL); }"
    )
    print("ua       =", ua)
    print("renderer =", renderer)
```

The check that matters is not what those two lines print on their own. It is that they
print the *same* strings when you flip the launch to headful, and that neither string
contains a headless token or a software renderer. Run it both ways and diff the output; if
the two agree, "headless" is no longer a field a detector can key on. The reliable method
is to compare against a stock browser on the same machine rather than trusting a single
verdict.

If you deploy in a container, the same code runs unchanged; the fonts, screen and WebGL do
not degrade because they never came from the container in the first place.

## The honest caveat: what parity does not fix

Parity closes the rendering tells. It does not close everything, and claiming otherwise
would be both false and the kind of overclaim that gets a project rightly distrusted.

Making headless output identical to headful does nothing for:

- **IP reputation.** A datacenter address is a datacenter address whether the browser is
  headless or not. You supply a clean exit; the browser cannot.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, a session with no scrolling. Headless parity does not pace your clicks;
  you do.
- **Per-account quotas and rate limits.** Ten identical-looking sessions from one address
  in one minute is a velocity signal no fingerprint hides.
- **The TLS handshake and network layer.** Decided before any page renders. A genuine
  Firefox handshake helps here because the engine is a real Firefox, but the exit and the
  request pacing are still yours to get right.

So the accurate answer to the title is: a headless invisible_playwright browser is not
detectable *by the headless-specific rendering tells*, because it does not produce them.
It is still detectable by your address, your behaviour and your request volume, and those
are the reader's job to supply well.

## Conclusion

Headless was detectable because it used to answer questions differently: a user agent
token, a degraded renderer, default window metrics, a thin font set. The durable fix is not
to hunt those one at a time, which is a race you stay one signal behind in. It is to make
the headless output identical to the headful output, so there is no headless-specific field
left to read. invisible_playwright does that by sourcing those values from preferences and
bundled assets rather than from a drawn window, and it proves it by running its own gates
`headless=True`. Pair that with a clean exit and human pacing, and headless stops being the
part of the session that gives you away.

## Short answers to the questions that lead here

**Is Playwright headless detectable?** The classic rendering tells are, if you leave them
in place. Make headless output identical to headful and those tells disappear; your IP and
behaviour remain detectable regardless.

**What actually gives away a headless browser?** Historically a `HeadlessChrome` user agent
token, a software or missing WebGL renderer, zero-size window metrics, and a font set that
does not match the claimed platform.

**Does headless=True hurt my fingerprint here?** No. The fingerprint comes from preferences
and bundled assets, not from whether a window is drawn, so headless and headful return the
same values. The gates run headless for that reason.

**Should I just run headful to be safe?** You do not need to. Headful costs you a display
and buys you nothing once the output is already identical. Run headless and diff it against
headful to confirm.

**If parity is solved, why am I still blocked?** Because parity does not touch your exit
IP, your request pacing, or per-account limits. A consistent browser on a bad address on a
hammered endpoint still loses.

**Do I need a stealth plugin on top of this?** No, and stacking one on causes the
contradictions it is meant to prevent. One layer answers each question; two layers answer
it twice and disagree.

## Sources

- The classic headless signals as described on the public detector suites named in this set
  (sannysoft, [CreepJS](creepjs-explained.md)), read from their own source rather than
  their rendered verdict.
- This project's release gates, which run `headless=True` against those suites and compare
  the fingerprint field by field against the headful run.
- MDN's reference pages for the two automation-adjacent browser APIs named above:
  [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  and [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver).
- Playwright's own docs for the [`headless` launch option](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch).

**See also:** [headless versus headful output](headless-vs-headful.md),
[why headless renders different fonts](headless-fonts-differ.md), and
[the screen-size tells a headless launch produces](screen-size-headless-tells.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The gates that back this page
run headless, because headless is the mode that ships.*
