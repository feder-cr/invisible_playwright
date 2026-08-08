---
title: "invisible_playwright vs SeleniumBase UC Mode"
description: "SeleniumBase UC Mode detaches chromedriver to hide the WebDriver channel, but never changes the Chromium engine, GPU, fonts, canvas or the TLS handshake."
parent: "Comparisons"
nav_order: 16
---


# invisible_playwright vs SeleniumBase UC Mode

invisible_playwright and SeleniumBase UC Mode fix automation blocks at different layers.
UC Mode is a Selenium driver-timing trick: it detaches `chromedriver` from Chrome during
clicks and navigations, so no automation channel is attached at the moment a page looks,
then reconnects. invisible_playwright is an engine-level Firefox patch: the identity a page
reads (GPU, fonts, canvas, audio, the TLS handshake) is built into the browser, not painted
on by the driver. UC Mode hides the WebDriver channel and nothing else; the engine patch
changes what the machine itself looks like. They are not really competing for the same block.

SeleniumBase is a large, actively maintained Python test framework, 12.9k stars at the
time of writing, and its UC Mode is one of the better-known ways to run Selenium without
announcing automation. It is worth understanding on its own terms before comparing it to
anything, because what it does is clever and narrow at the same time.

This page is about one specific design choice UC Mode makes, why that choice hides exactly
one class of tell and no other, and where that leaves you against a detector that reads the
engine and the machine rather than the driver.

## What UC Mode does, precisely

UC Mode drives a Chromium-based browser (Chrome, Edge, Brave or bare Chromium) through a
modified `chromedriver`. Its headline trick, in its own documentation's words, is that it
"disconnects `chromedriver` from Chrome during stealthy actions" and reconnects afterward.

The public API makes the timing explicit:

```python
from seleniumbase import SB

with SB(uc=True, test=True) as sb:
    # opens the URL with the driver detached, reconnecting after a delay
    sb.uc_open_with_reconnect("https://example.com", reconnect_time=2)
    # click scheduled to fire while chromedriver is still disconnected
    sb.uc_click("#submit", reconnect_time=1)
```

The `reconnect_time` is how long, in seconds, the driver stays detached before it
reconnects, typically a fraction of a second up to a couple of seconds. During that window
a JavaScript `setTimeout` performs the navigation or the click, so the moment a page is
most likely to inspect its environment is the moment no driver is attached to observe.

It is a genuinely good idea for the problem it targets. A driver that is not connected
cannot leave a live automation channel for a script to find at the instant it matters.

## Why the disconnect trick works, and what it cannot reach

The disconnect trick works because one whole family of automation tells lives in the
*connection itself*: the `chromedriver` process holding a live control channel, the
renamed DevTools console variables the driver injects,
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
reflecting an attached automation client. Detach the driver at the watched moment and, for that moment,
those signals are simply not there to read. This is a driver-timing defense, and against a
page that only checks for a live driver it is effective.

But it is timing on the *control channel*, not a change to the *thing being controlled*.
The browser rendering the page is still the same Chromium build it was before the
disconnect, drawing the same pixels with the same GPU string, enumerating the same fonts,
producing the same canvas and audio hashes, and completing the same TLS handshake on every
request whether the driver is attached or not. None of those are in the driver's gift to
change by connecting or disconnecting, because none of them come from the driver. They come
from the engine and the machine it runs on.

So the honest way to state UC Mode's scope: it hides the WebDriver channel extremely well
and touches nothing else. That is not a criticism, it is the boundary of a driver-layer
tool. [Which automation-layer tells are actually still worth worrying about in 2026](navigator-webdriver-explained.md)
is a shorter list than most people expect, and UC Mode covers it. The tells outside that
list are the ones a disconnect cannot reach.

## The engine gap it inherits (Chromium is not Chrome)

Driving Chromium, as UC Mode does when pointed at a bare Chromium rather than an installed
Chrome, inherits a capability gap that has nothing to do with automation at all: the open
Chromium build ships without the proprietary media stack a retail Chrome carries, including
the Widevine DRM module. A page can ask, get a negative answer, and conclude "this claims
to be Chrome but cannot do what every real Chrome install can," and no disconnect timing
changes that answer.

This is the same gap [any Playwright-Chromium launch runs into](chromium-is-not-chrome.md),
and it is a capability difference rather than a fingerprint you can spoof: patching the
navigator property that reports it just adds a contradiction between what the browser says
and what it can actually do, which is worse. UC Mode can sidestep it by attaching to a real
installed Chrome instead of downloading Chromium, but then you are back to shipping and
maintaining a full Chrome install on every machine, and you still have the machine-level
tells below.

## What an engine-level patch changes instead

invisible_playwright works at the layer underneath the driver question. It is a Firefox
patched at the C++ level and driven by stock Playwright, so the identity a page reads is
built into the engine rather than painted on by a script or gated by when a driver is
attached. There is no separate control channel to disconnect because the tells the
disconnect trick hides were never emitted in the first place, and the TLS handshake is a
real Firefox handshake because the request is made by a real Firefox.

Switching from plain Playwright is the same two-line change it always is, and the returned
object is a real Playwright `Browser` with every method intact:

```bash
pip install invisible-playwright
```

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # a real Playwright Browser: navigator.webdriver is undefined, not false,
    # and there is no attached-driver moment to hide
    print(page.evaluate("() => navigator.webdriver"))
```

The `seed` is the part that matters for debugging a block rather than guessing at it.
Every field a page can read, roughly four hundred of them across GPU, canvas, audio, fonts
and screen, is derived from that one seed, so the same seed reproduces the same machine on
every run:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

# timezone auto-derives from the proxy egress IP; the identity is fixed by the seed
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")
    print("canvas + gpu + fonts are identical every run for this seed")
```

If you do not pass a seed you still get one, and logging it lets you replay the exact
identity that failed:

```python
from invisible_playwright import InvisiblePlaywright

sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)   # feed it back in to reproduce this run
    page = browser.new_page()
    page.goto("https://example.com")
```

Where you need a specific value held constant, [pinning a field like the GPU model or the
screen size](pinning.md) leaves everything else seed-derived. The one honest caveat, the
same one that applies to every tool on these pages: none of this touches your IP. A
consistent engine on an address that is already known and blocked still loses, and that is
a separate layer from anything a browser reports.

## How to check the difference yourself

Do not take either description on faith. Open the same reporting page in both tools and diff
the reports field by field, which is [the comparison method that catches what a verdict
misses](how-to-test-bot-detection.md).

Look specifically at the fields the disconnect trick does not touch:

- The WebGL renderer string, and whether a real GPU is drawing the pixels rather than a
  software rasterizer.
- The font list, and whether it matches the platform the user agent claims.
- Canvas and audio hashes, read twice in one session, and whether they are stable and
  seed-consistent.
- What CreepJS, BotD, sannysoft, FingerprintJS and BrowserLeaks each report, remembering
  that they answer different questions and a blocked or empty value is itself a tell.

A driver-timing tool and an engine patch will look identical on `navigator.webdriver` and
diverge everywhere the report describes the machine. That divergence is the whole point of
the comparison, and it is the part [a timing trick on the debugger connection cannot
close](debugger-timing-detection.md).

## How to choose

- **Committed to the Selenium ecosystem and the block is a live automation channel?** UC
  Mode is a strong, well-maintained answer to exactly that problem, and it is Selenium, not
  Playwright.
- **On Playwright and want to keep your existing code?** Stay on Playwright; a driver-layer
  Selenium tool means rewriting the automation layer, not swapping a launch call.
- **Blocked on the engine or the machine, not the driver?** GPU, fonts, canvas, audio, the
  Chromium-is-not-Chrome capability gap, or the TLS handshake. A disconnect cannot reach any
  of these, and this is the layer invisible_playwright works at.
- **Need Firefox specifically?** UC Mode is Chromium-only by design;
  [four structural reasons Firefox can be the easier engine to hide](firefox-vs-chromium-antidetect.md)
  are covered separately.

## Conclusion

UC Mode solves a real problem well and states its scope honestly: it hides the WebDriver
channel by detaching the driver at the moment a page is most likely to look. That closes an
entire family of automation-layer tells and leaves the engine and the machine exactly as
they were. If your block lives in the driver connection, that is enough. If it lives in the
GPU string, the fonts, the canvas, the codecs or the TLS handshake, timing on the control
channel never touches it, because those signals do not come from the driver. That is the
durable difference between a driver-timing tool and an engine patch, and it is the reason
the two are not really competing for the same block.

## Short answers to the questions that lead here

**How does SeleniumBase UC Mode avoid detection?** It disconnects `chromedriver` from the
browser during navigations and clicks, so the page runs with no automation channel attached
at the watched moment, then reconnects. It is a driver-timing trick.

**Does UC Mode change the browser fingerprint?** No. It hides the WebDriver channel and
does not touch the GPU string, fonts, canvas, audio or the TLS handshake, which come from
the engine and the machine, not the driver.

**Is UC Mode a Playwright tool?** No, it is part of SeleniumBase, a Selenium framework.
Adopting it from a Playwright project means rewriting the automation layer.

**Does the disconnect trick work against every detector?** Against ones that look for a
live automation channel, yes. Against ones that read the engine identity or the machine, it
changes nothing, because the browser is the same Chromium before and after the disconnect.

**Is SeleniumBase maintained?** Yes, it is actively maintained with a large user base at
the time of writing. The comparison here is about layer, not activity.

**What does invisible_playwright do differently?** It patches Firefox at the C++ level so
the identity is built into the engine and the TLS handshake is a real Firefox handshake,
with every readable field derived from one reproducible seed.

## Sources

- SeleniumBase's own repository and UC Mode documentation, read 2026-08-05, for the
  disconnect-and-reconnect mechanism, the `uc_open_with_reconnect` and `uc_click`
  `reconnect_time` parameters, the Chromium-based browser support, and the maintenance
  and star count stated above.
- This project's own comparison and testing notes for the field-by-field method and the
  engine-versus-driver distinction, linked throughout.

**See also:** [why Chromium is not Chrome](chromium-is-not-chrome.md) for the capability
gap a driver trick cannot close, [the automation-layer tells still worth checking](navigator-webdriver-explained.md)
for what a disconnect does cover, and [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the comparison method to settle it yourself.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. UC Mode's disconnect trick is
a smart answer to the driver-channel question, and a reminder that the engine and the machine
are a different question entirely.*
