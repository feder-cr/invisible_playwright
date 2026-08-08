---
title: "undetected-playwright vs a patched Firefox binary"
description: "undetected-playwright hides Playwright's init/bindings tells; patchright is patched fork; patched Firefox closes fingerprint layer. Three layers people conflate."
parent: "Comparisons"
nav_order: 34
---


# undetected-playwright vs a patched Firefox binary

Three projects get mentioned in the same breath and treated as interchangeable:
undetected-playwright, patchright, and a browser that is patched at the build level.
They are not interchangeable. Each one fixes a different layer, and the confusion is
expensive because you can install the wrong one, watch it do exactly what it promises,
and still get the wrong page.

This is a disambiguation, not a ranking. The useful question is never "which is best",
it is "which layer is my problem in", and that has a concrete answer per site.

## The three layers, named once

When automation is spotted, the tell lives in one of three places:

- **The injection layer.** The automation framework injects a runtime into every page
  to talk to it. If that runtime leaves an artifact a page can read, the page knows a
  framework is present, regardless of how real the browser looks otherwise.
- **The driver layer.** How the framework talks to the browser over its debugging
  protocol, and what that protocol leaks. This is a property of the framework-plus-engine
  pairing, not of the page.
- **The fingerprint layer.** What the browser itself reports: the
  [canvas](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API), WebGL renderer,
  fonts, the [audio stack](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API),
  screen, the whole physical-machine picture. This is decided by the
  browser build and the host, and no page-level script can rewrite it convincingly.

undetected-playwright works on the first. patchright works mostly on the second. A
patched Firefox binary works on the third. That is the entire distinction, and the rest
of this page is just detail on it.

## What undetected-playwright actually does

undetected-playwright is a Python project that rewrites how Playwright injects its own
bindings into pages, so the framework's init-script and runtime globals do not leak into
the page's own context. That is a real and useful fix: the presence of an automation
runtime is one of the oldest and cheapest tells, and closing it removes a signal that a
page can read directly.

But it is a fix to the injection layer, and only that layer. The browser it drives is a
stock browser. Its canvas hash, its WebGL renderer string, its font list, its audio
fingerprint and its screen geometry are whatever that stock browser reports on whatever
machine you run it on. On a server with no GPU and no fonts, those values still say
"server with no GPU and no fonts". undetected-playwright never claimed otherwise; it
targets the injection tell, and it hits it.

patchright, worth naming here because it is the project most often confused with
undetected-playwright, is a different thing again: a patched fork of Playwright itself,
aimed largely at the driver-layer leak on its supported engine. A fork you install in
place of Playwright, versus a library you layer on top of it. Different mechanism, mostly
different layer. See [the driver-versus-engine split in the Patchright comparison](vs-patchright.md)
for that one in full, and [the independent convergence on the same driver fix](vs-rebrowser-patches.md)
for how a second project arrived at close to the same technique.

## What a patched Firefox binary does instead

This project takes the third layer. Instead of cleaning up the injection or forking the
driver, it patches the Firefox build itself so the values the browser reports are the
values of a plausible real machine, and drives that build with stock, unmodified
Playwright.

Because the fingerprint is generated inside the engine, it is internally consistent by
construction: the canvas hash, the WebGL renderer, the font set, the audio context and
the screen geometry are drawn from one seed and cross-agree, rather than being painted
over one at a time from JavaScript where a detector can catch two surfaces disagreeing.
That is why it reads as a genuine Firefox to the fingerprint, TLS and driver layers at
once: it is a genuine Firefox, built differently.

The switch is two lines, and after that it is ordinary Playwright:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

The `browser` object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser). Every method you already
use works unchanged. The `seed=42` makes the whole identity reproducible: same seed, same GPU, same
canvas hash, same fonts, every run, which is what makes a failing run replayable instead
of a guess. Drop the seed and each session gets a fresh, distinct machine.

## Where the layers overlap and where they do not

The injection layer and the fingerprint layer are not rivals. They fix different tells,
and a site that reads both will catch whichever you left open.

If your only problem is a framework runtime leaking into the page, an injection-layer fix
addresses exactly that and nothing more is required. If your problem is that the browser
looks like a headless machine in a datacenter, no amount of injection cleanup touches it,
because the canvas and the GPU string are not in JavaScript's gift to change. Running two
spoofers at once is its own mistake: a page-level patcher on top of a patched engine
means two things answering the same question, and [two disguises produce a contradiction
neither produces alone](playwright-stealth-levels.md).

And here is the honest boundary that applies to all three projects equally. None of them
touches your IP reputation, your per-account quotas, your rate limits, or your behaviour.
A patched binary can look like a real Firefox driven by a real person, which is why it
passes most fingerprint, TLS and driver checks, but it looks like a real person coming
from whatever address and at whatever pace you supply. A clean residential exit and human
pacing are the reader's job, not the browser's. A perfect fingerprint on a flagged IP
still loses, and [a clean fingerprint is not the whole session](why-blocked-with-a-clean-fingerprint.md).

## How to tell which layer is yours

Before choosing a tool, find the layer. The method is the same one that debugs any
detection: open the page in your automated browser and in a stock browser on the same
machine, and diff the reports field by field.

- If a page reads an automation global or an injected runtime and blocks on that, your
  tell is the injection layer.
- If the fingerprint surfaces disagree with each other, or announce a datacenter, or
  differ from the stock browser in ways the address does not explain, your tell is the
  fingerprint layer.
- If a stock browser fails the same URL by hand, it is not the browser at all: it is the
  exit, and no browser patch of any layer will move it.

The full ordering, cheapest and most likely cause first, is in
[the checklist for being detected on one site](playwright-detected-as-bot.md), and the
way to test it without a false pass is in [how to test bot detection](how-to-test-bot-detection.md).
The [`navigator.webdriver` write-up](navigator-webdriver-explained.md) covers the single
most-cited injection-adjacent tell and why setting it to `false` is not the fix.

## Conclusion

undetected-playwright, patchright, and a patched Firefox binary are not competitors so
much as three different repairs to three different layers. undetected-playwright cleans
the injection tell and leaves the fingerprint stock. patchright forks the driver. A
patched binary rebuilds the fingerprint layer from the engine out and drives it with
stock Playwright. Pick by the layer your failure lives in, and remember that none of the
three supplies the IP or the behaviour, which is often the layer that was actually
failing.

## Short answers to the questions that lead here

**Is undetected-playwright the same as patchright?** No. undetected-playwright is a Python
library that rewrites Playwright's bindings injection so its runtime does not leak into
pages. patchright is a patched fork of Playwright aimed largely at the driver-protocol
leak. Different mechanism, mostly different layer.

**Does undetected-playwright change my canvas or WebGL fingerprint?** No. It targets the
injection layer and drives a stock browser, so canvas, WebGL, fonts and audio are
whatever that stock browser reports on your machine.

**Will a patched Firefox binary make me undetectable?** No, and nothing should promise
that. It makes the fingerprint, TLS and driver layers read as a genuine Firefox, which is
why it passes most fingerprint checks. It does nothing about your IP reputation, your
quotas, or your behaviour.

**Can I use both an injection fix and a patched binary?** You generally should not stack
two spoofers that answer the same questions, because their answers disagree. If the engine
handles the fingerprint, turn off the page-level patching.

**Which one do I actually need?** The one that fixes your layer. Diff your automated
browser against a stock browser on the same machine and see whether the tell is an
injected runtime, the fingerprint surfaces, or the exit IP.

**Do I still need a proxy?** Yes, if your target weighs IP reputation, which most do. A
real-looking browser on a known-bad address is still on a known-bad address.

## Sources

- undetected-playwright and patchright, read from their own repositories and READMEs
  rather than from summaries, for what each one patches and at which layer.
- This project's own fingerprint generation and release gates, for how a seed-derived,
  engine-level fingerprint stays internally consistent across surfaces.
- [MDN, Canvas API](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API) and
  [MDN, Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API),
  read 2026-08-06, for what the browser-reported surfaces named in the fingerprint layer
  actually are.
- [Playwright's own `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  read 2026-08-06, for what the `browser` object in the code example is and does.

**See also:** [invisible_playwright vs Patchright](vs-patchright.md),
[invisible_playwright vs rebrowser-patches](vs-rebrowser-patches.md), and
[the layers of Playwright stealth](playwright-stealth-levels.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The three-layer confusion
on this page is one I have watched cost people a week on the wrong fix.*
