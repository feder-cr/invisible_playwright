---
title: "playwright-stealth vs a patched engine: page vs browser"
description: "playwright-stealth patches the page in about four lines; invisible_playwright rebuilds the browser engine. Where each layer works, and where it cannot reach."
parent: "Comparisons"
nav_order: 7
---


# playwright-stealth vs a patched engine: page vs browser

playwright-stealth and invisible_playwright fix bot detection at different layers.
playwright-stealth injects a few lines of JavaScript into the page to hide the obvious
automation properties; invisible_playwright patches Firefox's C++ engine and rebuilds the
browser so the machine-level signals match a real browser too. They are not really
competing for the same job, and the tool's own maintainer says as much.

This is the widest gap between any two tools on this site's comparison pages, and the
honest way to frame it is by layer rather than by which one "wins."
[Three ways to make Playwright undetected](playwright-stealth-levels.md) puts
`playwright-stealth` at level 1, patching the page. This project sits at level 3,
patching the engine and rebuilding the browser.

## What playwright-stealth actually does

`playwright-stealth` traces back to `puppeteer-extra-plugin-stealth` on the Node side,
ported to Python - [and that original plugin has its own separate maintenance story worth knowing](puppeteer-extra-stealth-unmaintained.md).
Mechanically it is init scripts: JavaScript injected into the page
before the site's own code runs, redefining the properties a detector is likely to check.
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver), the plugins array, `navigator.languages`, and similar.

The appeal is real and worth stating plainly. It is a few lines added to an existing
Playwright script, no browser fork, no binary to maintain, and it works immediately
against anything checking those properties directly.

## Where the fix and the check share a runtime

The structural limit is not a bug, it is what the approach is. Every override
`playwright-stealth` installs is a JavaScript value living in the same page realm as the
code trying to detect it. A property you redefine becomes an **own** property where a
native one is **inherited**. A replacement getter is a function, and every function
carries its source, so printing it shows the override rather than `[native code]`. Patch
`Function.prototype.toString` to hide that, and the patched `toString` becomes the new
anomaly, checkable the same way.
[We cover the general version of this race here](tostring-native-code-detection.md): you
are altering values inside the exact runtime the opponent's code is also running in, and
the opponent always gets to look at what you changed.

## What it never touches, by construction

An init script cannot change what the font subsystem enumerates, what the GPU driver
reports through WebGL, how the audio pipeline rounds a signal, or what the TLS stack
sends in a ClientHello. Those answers come from native code below the page, and a script
running inside the page has no path to them. This project's case for patching the engine
instead is built entirely on that gap:
[fonts](headless-fonts-differ.md), [WebGL parameters](webgl-parameters-are-identical.md),
[the audio stack](audiocontext-fingerprinting.md), and
[the TLS handshake](ja3-ja4-tls-fingerprint.md) are four surfaces a page-level tool cannot
reach on any browser, by construction, not by an oversight anyone could patch around.

## The tool is honest about this, more than most of its category

playwright-stealth is unusually direct about its own limits for this space. The
maintainer of the actively developed Python fork describes it as a proof-of-concept
starting point and says plainly not to expect it to get past anything but the simplest
detection methods.
That is not marketing copy undercutting itself, it is an accurate scope statement, and
taking a tool's own stated limits at face value is the same approach this page took with
[Camoufox's](vs-camoufox.md), [Patchright's](vs-patchright.md), and
[nodriver's](vs-nodriver.md) documentation.

## When playwright-stealth is actually the right call

playwright-stealth is the right call when the block is a naive property check. If the
thing flagging you is `navigator.webdriver` being `true`, an empty plugins array, or a
User-Agent string with `HeadlessChrome` in it, four lines of `playwright-stealth` fixes
exactly that, today, with no browser to build or maintain. Reaching for an engine-level rebuild before confirming the block is not that
simple is solving a problem you may not have.
[How to actually test which layer you're failing at](how-to-test-bot-detection.md) covers
how to tell the difference before picking a tool.

## How to actually choose

- **Blocked by an obvious property check, need a fix today?** playwright-stealth, in
  about four lines.
- **The block survives that, and the site inspects your GPU, fonts, audio, or TLS?** No
  page-level tool reaches those; you need
  [driver-level](vs-patchright.md) or engine-level work.
- **Need the fix to also survive a `toString` or own-property check?** Level 1 cannot do
  this by construction; move up a level.
- **Not sure which layer is actually blocking you?** Test it directly rather than
  guessing, [with a method that checks the signal fired](how-to-test-bot-detection.md),
  not just that the run came back green.

## Conclusion

playwright-stealth is honest about what it is: a fast, low-cost fix for the obvious
checks, built by its own maintainer's account as a starting point rather than a
destination. It is often the right first thing to try, and the wrong thing to expect more
from than it claims. This project exists for the surfaces that approach cannot reach at
all, at the cost of a browser fork instead of four lines.

## Short answers to the questions that lead here

**Is playwright-stealth still worth using?** Yes, for the layer it targets: obvious
property checks. It is not intended to fix machine-level signals like fonts or GPU.

**Does playwright-stealth fix navigator.webdriver detection?** Yes, that is exactly its
target. Whether that is enough depends on what else the site checks.

**Why doesn't playwright-stealth fix canvas or WebGL fingerprinting reliably?** Because
an init script runs inside the page, and those values come from native code the page
cannot reach or fully rewrite convincingly.

**Is playwright-stealth a Firefox tool?** No, it works against whatever browser
Playwright launches; it is not engine-specific the way this project or Camoufox are.

**Can I use playwright-stealth and a driver patch like Patchright together?** Yes, they
address different layers; combining them is a normal pattern on Chromium.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
for the full map this page sits inside; [why every override carries its source](tostring-native-code-detection.md),
for the race any page-level fix eventually loses; [how to test which layer is actually blocking you](how-to-test-bot-detection.md), before
picking a tool at any level; [pyppeteer's own maintainer recommending Playwright](pyppeteer-unmaintained-playwright.md),
one layer up from stealth technique - which driver library to build on at all; and
[invisible_playwright vs fingerprint-suite](vs-fingerprint-suite.md), the same
injection layer with a considerably more sophisticated generation model behind it.

## Sources

- The actively developed Python fork's own GitHub repository,
  [Mattwmaster58/playwright_stealth](https://github.com/Mattwmaster58/playwright_stealth),
  read 2026-08-29, for its stated scope ("a proof-of-concept starting point," not
  expected to bypass anything but the simplest detection) and its init-script mechanism.
- This project's own patch catalogue for the engine-level surfaces an init script cannot
  reach.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. playwright-stealth solves the layer it says it solves,
and does not claim the one this project exists for.*
