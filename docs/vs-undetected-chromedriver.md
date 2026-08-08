---
title: "undetected-chromedriver vs a patched Firefox browser"
description: "undetected-chromedriver strips the cdc_ and webdriver leaks but still ships a stock Chromium fingerprint; a patched Firefox fixes the engine fingerprint."
parent: "Comparisons"
nav_order: 29
---


# undetected-chromedriver vs a patched Firefox browser

These two tools are often mentioned in the same breath, as if they were competing
answers to one question. They are not. They fix problems at different layers, and once
you name the layer each works at, choosing between them stops being a matter of taste
and becomes a matter of which problem you actually have.

undetected-chromedriver patches the automation driver. A patched browser build patches
the engine underneath it. That distinction decides what each one can and cannot hide,
so this page starts there.

## The layer each tool works at

undetected-chromedriver is a Selenium driver, maintained on its public repository, whose
job is to make ChromeDriver stop announcing itself. Stock ChromeDriver injects a set of
`cdc_`-prefixed properties into every page and drives a Chrome that reports
`navigator.webdriver` as `true`. undetected-chromedriver patches the driver binary to
strip those markers, so the two most famous automation tells - the
[`cdc_` variables](cdc-variable-explained.md) and
[the `navigator.webdriver` flag](navigator-webdriver-explained.md) - are gone from the
page by the time your code sees it.

That is real work and it solves a real problem. But notice the ceiling: everything it
touches lives in the driver, or in a page-level tweak applied after the browser is
already running. The browser it launches is still a stock Chromium. Whatever that
Chromium reports for canvas, WebGL, fonts, audio and screen is whatever the host machine
produces, unchanged.

A patched Firefox build works one layer down. The canvas readback, the WebGL vendor and
renderer strings, the font enumeration and the screen geometry are decided inside the
compiled browser before any driver or any script is involved. That is a different
problem than "hide the driver", and it needs a different tool.

## Why the driver layer is not the fingerprint layer

Here is the part that surprises people. You can run undetected-chromedriver on a
headless Linux server, confirm that `navigator.webdriver` is `undefined` and that no
`cdc_` property exists anywhere, and still be trivially identifiable - because the
canvas hash, the WebGL renderer and the font list are all still those of a headless
Linux server.

A stock Chromium on a machine with no GPU tends to report a software renderer in its
WebGL strings. The font list is whatever the container has installed, which under a
Windows user agent is a contradiction a
[one-line check](bundled-fonts-cross-platform.md) catches. The screen geometry is
whatever a headless surface invents. None of these is an automation flag. They are all
"this is a datacenter" facts, and stripping the driver's markers does not move any of
them, because they were never in the driver's gift to change.

This is the concrete difference the two tools disagree on:

| Signal | undetected-chromedriver | A patched Firefox build |
|---|---|---|
| `navigator.webdriver` | Removed | Never present |
| `cdc_` driver properties | Stripped | Never injected |
| Canvas / WebGL fingerprint | Whatever the host Chromium reports | Reads as Windows Firefox, host-independent |
| Font enumeration | Host's installed fonts | Windows font set, on any host |
| Screen / device pixel ratio | Host or headless surface | Coherent Windows desktop values |

The left column is the driver layer. The right column is the engine layer. A tool that
only owns the left column cannot fill the right one, however well it does its own job.

## What a patched build looks like in code

invisible_playwright is a patched Firefox driven by stock Playwright. The engine reports
a Windows Firefox fingerprint regardless of the machine it runs on, so the same code
produces the same identity on your laptop and on a headless Linux server. Switching from
plain Playwright is two lines, and after that every standard Playwright method works
unchanged:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

The `browser` object is a real Playwright `Browser`, so `new_page`, `goto`, `click`,
`fill` and the rest behave exactly as documented upstream. Passing `seed=42` pins the
identity: the GPU, canvas hash, audio context, fonts and screen come back identical run
after run, which is what makes a failing run reproducible instead of a fresh guess each
time. Omit the seed and each session gets a distinct fingerprint. Configuration such as
a proxy or an explicit timezone is passed the same way:

```python
with InvisiblePlaywright(
    proxy={"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"},
    timezone="America/New_York",
) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

There is no separate fingerprint step to remember and no page-level patch to keep in
sync with the engine. The engine already reports coherent values, which is the layer
undetected-chromedriver leaves to the host.

## The honest caveat: a clean fingerprint is not a clean session

This is where an honest comparison has to stop short of a slogan. Fixing the engine
fingerprint fixes the engine fingerprint. It does not fix everything a site looks at,
and neither does undetected-chromedriver, because these things live outside the browser
entirely:

- **IP reputation.** A coherent Windows Firefox on a known datacenter address is a
  coherent Windows Firefox on a known datacenter address. You supply the exit; the
  browser cannot.
- **Rate limits and per-account quotas.** Hammer one endpoint from one address and you
  create the velocity signal no fingerprint hides.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, a session with no scrolling. invisible_playwright arcs the mouse on a
  Bezier curve, but pacing and intent are still yours to get right.

So the truthful summary is: a patched build makes the fingerprint, the TLS handshake and
the driver layer read as a genuine Firefox, which is why it clears most fingerprint and
driver checks. It does not make a session undetectable, and any tool that claims to is
selling you the part it cannot deliver. Pair it with a clean exit and human pacing, and
[verify the result the honest way](how-to-test-bot-detection.md) rather than trusting a
single green verdict.

## Which one to reach for

If you are committed to Selenium and Chrome, and your target checks the driver layer
more than the machine layer, undetected-chromedriver removes the obvious tells and may
be all you need. It is a focused tool that does its own job.

If your automation runs on servers and containers, where the giveaway is the machine
rather than the driver, patching the driver is the wrong layer: the canvas, WebGL and
font signals still describe a datacenter, and no amount of driver work reaches them.
That is the case a patched engine is built for. The same reasoning is why
[a patched build beats a page-level stealth plugin](vs-nodriver.md) and why
[stacking a driver patch under a fingerprint layer](vs-seleniumbase-uc-mode.md) tends to
produce contradictions rather than cover.

## Conclusion

undetected-chromedriver and a patched Firefox build are not rivals; they are answers to
different questions. One removes the driver's leaks - the `cdc_` variables and the
`webdriver` flag - from a browser that still fingerprints as its host. The other changes
what the browser fingerprints as, on any host, and leaves the driver clean because
stock Playwright never dirtied it. Name the layer your problem lives at, pick the tool
that owns that layer, and remember that neither one owns your IP, your quotas or your
behaviour.

## Short answers to the questions that lead here

**Does undetected-chromedriver change the browser fingerprint?** No. It patches the
driver to strip the `cdc_` markers and the `webdriver` flag, but it launches a stock
Chromium, so the canvas, WebGL and font fingerprint stay whatever the host reports.

**Will undetected-chromedriver hide that I am on a headless Linux server?** Not by
itself. The driver tells are gone, but a software WebGL renderer, a Linux font set and
a headless screen size still describe the server.

**How is a patched Firefox build different?** It changes the fingerprint inside the
compiled engine, so the browser reads as Windows Firefox regardless of the machine it
runs on, and there is no separate driver to clean up because stock Playwright adds no
markers.

**Is either one undetectable?** No, and be wary of anything that says otherwise. Both
leave IP reputation, rate limits and behaviour untouched, and those get sessions blocked
with a perfectly clean fingerprint.

**Can I just add a stealth plugin to fix the fingerprint?** You can, but two layers
answering the same questions tend to disagree, and the contradiction is its own tell.
Fixing the fingerprint in the engine avoids that whole class of mismatch.

**Do I still need a proxy with a patched browser?** Yes. A clean fingerprint on a known
datacenter IP still loses. The browser supplies the disguise; you supply the exit.

## Sources

- The undetected-chromedriver public repository and its documented approach: patching
  the ChromeDriver binary to remove the `cdc_` properties and the `webdriver` flag, read
  from the project's own README rather than from secondhand summaries.
- This project's own measurements comparing a stock engine's fingerprint on a headless
  host against a patched build's Windows Firefox fingerprint on the same host.
- The public detection suites, including CreepJS and BrowserLeaks, used to read the
  per-surface values the table above compares.

**See also:** [the driver leaks undetected-chromedriver strips](cdc-variable-explained.md),
[a patched build versus a page-level stealth plugin](vs-nodriver.md), and
[how to test the result without a false pass](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The layer distinction on
this page is the one people most often get wrong, and it costs them a week of debugging
the driver when the giveaway was the machine.*
