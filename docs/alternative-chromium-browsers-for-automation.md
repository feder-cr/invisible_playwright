---
title: "Alternative Chromium browsers for automation: what differs"
description: "Brave, Edge, Opera, Vivaldi, Ungoogled: what actually changes between the forks, and why none of it touches the CDP tells that really decide detection."
parent: "Comparisons"
nav_order: 40
---

# Alternative Chromium browsers for automation, and the one question that matters

Brave, Microsoft Edge, Opera, Vivaldi, Ungoogled Chromium: all of them build on
the open-source Chromium project, and all of them are Chromium for automation
purposes in the way that matters most. This page is about what actually
changes between the forks, and the one thing that does not.

## What Chromium being the base actually means

Every fork above takes Chromium's rendering engine (Blink), its JavaScript
engine (V8), and most of its automation surface (CDP, the same protocol
Playwright and Puppeteer speak to Chrome) largely unchanged. What each fork
adds is UI chrome, some default settings, and in a couple of cases genuine
engine-level changes - which is the part that matters and the part most
comparisons skip.

## What actually differs

**Ungoogled Chromium** strips Google-service integration and telemetry calls.
The rendering and automation surface underneath is stock Chromium. For
detection purposes this changes almost nothing observable from a page; it
changes what phones home, not what the DOM reports.

**Brave** adds a built-in ad and tracker blocker and fingerprinting
countermeasures aimed at *passive, cross-site* tracking - randomizing certain
canvas and audio outputs per session by default. That is a genuine engine-level
difference, and it cuts both ways for automation: it can make canvas-based
fingerprint matching noisier, but a browser that randomizes its own canvas
output on every load is itself an unusual, identifiable behaviour to a detector
that checks for consistency across repeat visits.

**Edge, Opera, Vivaldi** are UI and feature layers - vertical tabs, built-in
VPNs, sidebar panels - on essentially stock Chromium/Blink underneath, with UA
strings that identify them as themselves rather than as Chrome.

## The one question that matters, and none of these answer it

**Is the browser being driven by an automation protocol, and does that show?**
Every fork above, driven by Playwright or Puppeteer over [CDP](bidi-vs-cdp-detection.md),
inherits the same automation surface Chrome does, because CDP is the layer
Playwright actually talks to - the browser skin on top is close to irrelevant
to what CDP exposes. [`navigator.webdriver`](navigator-webdriver-explained.md),
the CDP-specific runtime properties, the automation flags: present in
Brave-under-Playwright exactly as in Chrome-under-Playwright, because they
come from the driving protocol, not the fork.

Picking a different Chromium skin to solve a detection problem is picking a
different paint color to fix an engine problem. The tells that matter live in
the automation layer, not in whether the tab bar says Brave or Edge.

## Where the fork does matter

- **Feature availability for your test surface.** If you are testing behaviour
  specific to Edge's rendering quirks or Brave's Shields, you need that
  specific browser, automation-detectability aside.
- **A UA string that must say the right thing.** If a site branches logic on
  the UA rather than on capability detection, the fork's declared identity
  matters for that reason alone.
- **Brave's own fingerprint randomization**, if you specifically want noisier
  canvas/audio output and are not worried about the randomization itself being
  a signal.

## The actual alternative, if the browser is your problem

If what you are after is a Chromium-family browser that reads as a normal
consumer install rather than an automation-driven one, the fork choice is the
wrong lever - the automation layer is. [Patchright](vs-patchright.md) takes
this approach on Chromium specifically: patching Playwright's driver to
remove the most common CDP-level tells rather than switching which skin sits
on top.

This project takes the same idea on a different base entirely: a Firefox
patched at the C++ source, chosen over any Chromium fork because Firefox is
not driven over CDP at all - a different protocol with a different, smaller
set of shared tells, on an engine most anti-bot vendors have spent
proportionally less effort fingerprinting given Chromium's dominant share.
[Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md)
is the fuller argument for that choice, independent of which Chromium fork you
were comparing against.

## Short answers to the questions that lead here

**Is Brave harder to detect than Chrome for automation?** Not meaningfully,
when driven by the same protocol. Its fingerprint randomization changes canvas
and audio output but does not touch the automation-protocol signals a detector
checks first.

**Does using Edge instead of Chrome avoid bot detection?** No. Same Blink
engine, same CDP surface when automated, different UA and UI.

**Is there a Chromium fork built for automation stealth?** Not among the
consumer forks above. Patchright and undetected-chromedriver-style projects
patch the driver layer on stock Chromium/Chrome rather than relying on a
different skin.

**Does Ungoogled Chromium avoid fingerprinting?** It removes Google telemetry
calls, not fingerprintable page-level properties. A detection script reads the
same DOM and JS surface either way.

**Which non-Chromium browser is worth trying instead?** Firefox is the
structurally different option - different rendering engine, different
automation protocol - which is the actual axis that moves the needle rather
than a Chromium skin.

**See also:**
[Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md),
[patchright vs invisible_playwright](vs-patchright.md) for the driver-patching
approach on Chromium, and
[what navigator.webdriver really tells a site](navigator-webdriver-explained.md)
for the flag every CDP-driven fork inherits alike.

## Sources

- [The Chromium Project](https://www.chromium.org/chromium-projects/), retrieved 2026-09-10, for what the open-source base provides to every fork.
- [Brave's own documentation on fingerprinting protections](https://brave.com/privacy-features/), retrieved 2026-09-10, for the canvas/audio randomization behaviour described above.
- [Chrome DevTools Protocol documentation](https://chromedevtools.github.io/devtools-protocol/), retrieved 2026-09-10, for the automation surface every CDP-driven Chromium fork exposes identically.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, precisely because a Chromium fork does not
change the automation protocol underneath it. The page recommends switching
engines rather than skins for that reason, and says so plainly rather than
implicitly.*
