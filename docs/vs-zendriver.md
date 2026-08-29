---
title: "zendriver vs invisible_playwright: Chrome CDP vs Firefox"
description: "zendriver drives Chrome over CDP, removes webdriver tells; invisible_playwright drives patched Firefox with cross-checked fingerprints. When helps, what doesn't."
parent: "Comparisons"
nav_order: 21
---


# zendriver vs invisible_playwright: Chrome CDP vs Firefox

These two tools solve overlapping problems from opposite ends, so "which is
better" is the wrong question. One removes the signals that announce
*automation*. The other builds a browser that reads as a genuine *machine* at
every surface. Those are different layers, and a session gets blocked at
whichever one is weakest.

This page is what each tool actually does, the architectural difference that
matters, a runnable example, and the honest list of what neither one touches.

## What zendriver is

zendriver is an actively maintained async fork of nodriver. It drives a real
Chrome or Chromium instance directly over the Chrome DevTools Protocol (CDP),
with no chromedriver binary and no WebDriver layer in between. Its own
documentation describes it as a successor line to nodriver with a fully async
API and blocking-free operation.

The thing it does well follows from that architecture. Because there is no
chromedriver and no WebDriver protocol, the classic automation tells are gone:
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
reads as a normal browser rather than `true`, and the
Selenium-era globals a driver used to inject are not there. That is a real and
useful property, and it is [most of what "undetected" meant for the
Chrome-CDP lineage](vs-nodriver.md).

Note the shape of what that fixes: it is the *driver* layer. It says nothing
about whether the browser's canvas, WebGL, audio and TLS surfaces agree with
each other, because CDP-driven Chrome is still an ordinary Chrome reporting
whatever the host machine actually has.

## What invisible_playwright is

invisible_playwright drives a real Firefox that is patched at the C++ level, and
you drive it with stock Playwright - every standard `page` method, sync or
async, unchanged. The difference is not a page-level script that overrides
properties after load. It is a browser whose fingerprint surfaces are generated
together from a single seed, so canvas, WebGL, audio, the `navigator` fields,
the fonts and the screen are consistent *with each other* rather than each
patched in isolation.

That cross-surface consistency is the point. A detector's strongest move is not
"is this value unusual" but "do two values that must agree, agree". A canvas
hash that implies one GPU while the WebGL renderer string names another is a
contradiction no single-surface patch avoids. Because these surfaces are drawn
from the same seed, they tell one story, which is why a seeded invisible_playwright
session passes most fingerprint-consistency checks - the kind
[CreepJS runs when it walks descriptors and compares surfaces](how-to-test-bot-detection.md).

Being real Firefox rather than patched-Chrome also means the driver layer is
clean for free: `navigator.webdriver` reads as [a genuine browser's
value](navigator-webdriver-explained.md), and the TLS handshake is Firefox's own
handshake rather than an impersonation of it.

## The architectural difference in one line

zendriver removes the tells that a browser is *driven*. invisible_playwright
ships a browser that reads as a real *machine* at every surface and cross-checks
those surfaces against each other.

If the thing blocking you is a driver artifact, zendriver's approach is enough.
If it is a fingerprint that is internally contradictory - a plausible screen
next to an implausible GPU, an audio stack that does not match the platform the
user agent claims - then removing webdriver tells does not touch it, and a
seed-consistent Firefox is the layer that does.

## A runnable example

Switching from stock Playwright is a two-line change, and the object you get
back is a real Playwright `Browser`, so every method works as documented
upstream.

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 -> the same GPU, canvas hash, audio context and fonts every run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
    print(page.title())
```

Async is the same shape:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    print(await page.title())
```

Pass a proxy through the same constructor, and the browser timezone is
auto-derived from the egress IP so the two do not tell different stories:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

The seed is the debugging property that matters: the same seed reproduces the
same machine, so a failing run can be replayed exactly instead of hoping the
next random draw brings it back.

## What neither tool fixes

Neither tool touches IP reputation, per-account quotas and rate limits, or the
pacing of human behaviour - a clean driver layer and a consistent fingerprint
are the browser's contribution, not the whole session. Skipping this list is
how people ship a "stealth" setup that still gets blocked on day one.

- **IP reputation.** A perfect browser on a known datacenter or already-listed
  address still loses. Neither tool changes your exit. You supply a clean proxy;
  the fingerprint cannot rescue a bad IP.
- **Per-account quotas and rate limits.** These are counted server-side against
  the account and the address, and no browser property resets a counter.
- **Behaviour and timing.** Pointer motion, typing rhythm, the pace of a form,
  the pause shaped like a script. invisible_playwright ships Bezier-curve mouse
  motion, but human *pacing* across a whole session is something you supply.
- **The velocity you create yourself.** Hammering one endpoint from one address
  is a signal you generate no matter how real the browser looks.

So the honest framing: invisible_playwright is designed to look like a real
browser driven by a real person, which is why it passes most fingerprint, TLS
and driver-layer checks. It does not, on its own, fix your IP, your quotas or
your behaviour - and any tool that tells you it makes you "undetectable" is
selling you the part that no browser can deliver.

## Conclusion

zendriver is a strong answer to one layer: it drives real Chrome over CDP and
removes the artifacts that announce automation. If your blocks are driver-shaped
and you are committed to Chrome, that may be all you need.

invisible_playwright answers a different and deeper layer. It is a real Firefox,
patched so that canvas, WebGL, audio, `navigator` and TLS are seed-consistent
with one another, driven by stock Playwright with no API to relearn. That
cross-surface consistency is what carries you past the checks that compare
surfaces rather than reading a single flag. Pair it with a clean IP and human
pacing, which is the work neither tool does for you, and the browser stops being
the weak link.

## Short answers to the questions that lead here

**Does zendriver make me undetectable?** No. It removes the webdriver and
Selenium-era tells from CDP-driven Chrome, which is real and useful, but it does
not ship a coherent cross-surface fingerprint and it does not touch your IP or
behaviour.

**What is the core difference from invisible_playwright?** zendriver drives real
Chrome over CDP with the driver tells removed. invisible_playwright drives a
patched real Firefox whose canvas, WebGL, audio, navigator and TLS surfaces are
seed-consistent with each other.

**Do I have to rewrite my Playwright code?** For invisible_playwright, no - it
returns a real Playwright `Browser` and every method works unchanged. zendriver
has its own async API rather than Playwright's.

**Will either one fix my IP getting blocked?** No. IP reputation, quotas and
rate limits are server-side and address-side. Bring a clean proxy; the browser
layer cannot fix the exit.

**Why does fingerprint consistency matter more than "no webdriver"?** Because
strong detectors compare values that must agree. Removing the webdriver flag
does nothing about a canvas hash that contradicts the WebGL renderer; a
seed-consistent browser is what keeps those in agreement.

**Chrome or Firefox for stealth?** Neither wins by engine alone. What matters is
whether the surfaces are consistent and the driver layer is clean, which is a
[longer comparison in its own right](firefox-vs-chromium-antidetect.md).

## Sources

- [zendriver's own GitHub repository and README](https://github.com/cdpdriver/zendriver),
  read 2026-08-29, for its status as an actively maintained fork of nodriver,
  its CDP-over-Chrome architecture, and its removal of the WebDriver and
  chromedriver layers.
- This project's release gates and fingerprint-consistency checks, for the
  cross-surface behaviour described above.
- The public detection suites named on the [testing page](how-to-test-bot-detection.md),
  read from their own source rather than their rendered verdicts.

**See also:** [invisible_playwright vs nodriver and undetected-chromedriver](vs-nodriver.md)
for the rest of the Chrome-CDP lineage, and [the checklist for being detected on
one site](playwright-detected-as-bot.md) for working out which layer is actually
blocking you.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The comparison is
architectural, not a put-down: zendriver does its layer well, and the honest
caveat about IP, quotas and behaviour applies to both.*
