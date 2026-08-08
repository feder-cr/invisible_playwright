---
title: "Does Playwright Support Firefox Stealth?"
description: "Stock Playwright drives a plain Firefox with no stealth layer, so automation signals show. Put the spoofs in a patched browser and keep the standard Playwright API."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 25
---


# Does Playwright Support Firefox Stealth?

Short answer: Playwright drives Firefox well, but it does not make Firefox
stealthy. The bundled Firefox it launches is an ordinary build, and it exposes
the same automation signals any driver would. Stealth is not something the
driver can add, because the driver is not where the signals live. It has to be
compiled into the browser.

That distinction is the whole answer, and the rest of this page is why it is
true and what to do about it.

## The short version: stealth lives in the browser, not the driver

A browser automation stack has two halves. The driver is the process that sends
commands - click here, go there, read this element. The browser is the process
that renders the page and answers every fingerprinting question a site asks:
what GPU do you have, what fonts, what is `navigator.webdriver`, what does your
TLS handshake look like.

Detection happens entirely in the second half. A site never sees your driver.
It sees the browser's answers. So a stealth layer has to change what the browser
reports, which means it has to be inside the browser. A patched driver talking
to an unpatched browser changes nothing a detector can read.

Stock Playwright is a very good driver. It is not, and does not try to be, a
stealth browser.

## What stock Playwright's Firefox actually exposes

Playwright ships [its own Firefox build](https://playwright.dev/python/docs/browsers)
and talks to it over the Juggler protocol. That build is a normal Firefox with
the automation hooks enabled. It was never meant to hide, so it does not:

- [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
  reports the automated state.
- The fingerprint is whatever the host machine happens to have. On a server that
  means a software WebGL renderer, no audio device, a font set that belongs to
  the container rather than to the platform the user agent claims, and a screen
  size nobody actually runs.
- The TLS handshake is the build's own, which [no in-page test can see but a
  server-side detector can](ja3-ja4-tls-fingerprint.md).

None of this is a defect in Playwright. It is a driver doing its job. But it
means "use Playwright with Firefox" and "have a stealthy Firefox" are two
different projects, and the first does not get you the second.

## Why patching the driver is the wrong layer

The common instinct is to reach for a stealth plugin: a piece of JavaScript
injected into every page that overwrites the properties a detector reads. This
works at the edges and fails at the core, for a reason that is structural rather
than a matter of plugin quality.

Injected JavaScript runs after the page's own code can already have captured the
original built-ins, and a serious check compares the built-ins against a clean
copy taken from a fresh iframe. An overwrite leaves a fingerprint of its own: a
patched function has a different `toString`, a redefined property has a
telltale descriptor, and [setting `navigator.webdriver` to `false` is itself a
signature because a real browser reports `undefined`](navigator-webdriver-explained.md).
The machine tells - GPU, fonts, audio, screen - are not in JavaScript's gift to
change at all.

So the driver layer can paper over a handful of properties and cannot touch the
rest. The deeper you push the disguise, the closer to the engine it has to be.
[The full ladder of where a spoof can live, and what each rung can and cannot
reach, is its own page](playwright-stealth-levels.md).

## The split invisible_playwright uses: stock Playwright, patched Firefox

This is exactly the split invisible_playwright is built on. The driver stays
stock: an unmodified, current Playwright, with the standard API and nothing
wrapped or subsetted. The stealth moves entirely into the browser, which is a
Firefox patched at the C++ level so the spoofs are compiled in - `webdriver`
reads the way a normal browser's does, the fingerprint surfaces (GPU, canvas,
audio, screen, fonts) are answered from a seed-derived identity, and real fonts
are bundled so the list matches the platform being claimed.

Because the disguise is in the engine and not in an injected script, there is no
overwritten built-in to catch and no second spoofer contradicting the first. And
because the driver is ordinary Playwright, switching is two lines and every
method you already know keeps working:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the whole identity reproducible run to run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # the browser, not a plugin, answers this - and it reads like a real one
    print("webdriver:", page.evaluate("navigator.webdriver"))
    print("renderer:", page.evaluate(
        "document.createElement('canvas').getContext('webgl')"
        ".getParameter(0x1F01)"))
```

The `browser` object is a real
[`playwright.sync_api.Browser`](https://playwright.dev/python/docs/api/class-browser).
Everything in the upstream Playwright documentation applies unchanged; the only
difference is what the browser reports when a page asks. The async surface is
identical:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    print(await page.evaluate("navigator.webdriver"))
```

Run the first snippet against a plain Playwright Firefox and against this one and
diff the two reports field by field. The plain build announces automation and a
server machine; the patched build reads like a real desktop Firefox, and the
same seed produces the same reading every time.

## What this fixes and what you still supply

Here is the honest boundary, because the split above solves one layer and one
layer only.

What the patched browser handles: the fingerprint, the driver tells, and the TLS
handshake all read as a genuine Firefox. That is most of what a public detection
suite - CreepJS, BotD, FingerprintJS, sannysoft, BrowserLeaks - actually
measures, and it is why a well-configured session passes most of those checks.

What it does not, and cannot, handle on its own:

- **IP reputation.** A perfect browser on a known datacenter address still loses.
  You supply a clean exit; [the proxy configuration is its own
  page](configuration.md).
- **Per-account quotas and rate limits.** These are about the account and the
  request volume, not the browser.
- **Behaviour and timing.** Pointer motion, typing rhythm, and the pace of a
  session are yours to make human. The tool arcs the mouse on a Bezier curve, but
  it will not invent realistic pauses for you.

Looking like a real browser is necessary and not sufficient. This is not an
undetectable browser and there is no such thing; it is a browser that looks real,
paired with the parts only you can supply. Anyone selling "bypass everything" is
selling the half of the problem that is not theirs to sell.

## Conclusion

Does Playwright support Firefox stealth? It supports driving Firefox, and stealth
is not a driver feature - it belongs in the browser, so the right answer is stock
Playwright plus a patched Firefox rather than a patched Playwright. That keeps the
standard API and moves the disguise to the only layer that a detector can read.
It gets you a browser that looks real, which is most of the battle and not all of
it. The clean IP and the human pacing are still yours to bring.

## Short answers to the questions that lead here

**Does Playwright have a stealth mode?** No. It is a driver, and the bundled
Firefox it launches is a normal build that exposes the usual automation signals.
Stealth has to be added in the browser.

**Can a stealth plugin make Playwright's Firefox undetectable?** It can hide a
few properties and it leaves its own signature doing so, and it cannot touch the
machine tells - GPU, fonts, audio, screen. Injected JavaScript is the wrong layer
for the hard part.

**Do I have to patch Playwright itself?** No, and you should not. Patch the
browser and keep Playwright stock, so the standard API stays intact and the
disguise sits where detectors actually look.

**Will this pass every bot check?** It passes most fingerprint, driver, and TLS
checks because those read as a real Firefox. It does nothing about your IP,
your quotas, or your behaviour, and those get sessions blocked too.

**Do I have to change my Playwright code?** Two lines at launch. The returned
object is a real Playwright `Browser`, so every method works exactly as
documented upstream.

**Is Firefox better than Chromium for this?** They have different tradeoffs;
[the comparison is its own page](firefox-vs-chromium-antidetect.md).

## Sources

- [Playwright's own docs on the Firefox build it drives](https://playwright.dev/python/docs/browsers),
  and the [`Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  read from their own behaviour rather than a summary of it.
- [MDN's `navigator.webdriver` reference](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
  for what the property signals and when.
- This project's release gates, which assert the patched browser reports a real
  fingerprint and driver state, run through a proxy and compared field by field
  against a stock browser on the same machine.

**See also:** [the levels a stealth layer can live at](playwright-stealth-levels.md),
[what navigator.webdriver really signals](navigator-webdriver-explained.md), and
[Firefox versus Chromium for anti-detect](firefox-vs-chromium-antidetect.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The split it is built
on is the whole point of this page: keep the driver ordinary, put the disguise in
the browser.*
