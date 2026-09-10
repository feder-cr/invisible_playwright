---
title: "What is headless browser automation, and what differs"
description: "A real browser with no window, not a lighter fake one. The three properties that differ by default, and why headless-specific detection mostly closed."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 47
---

# What is headless browser automation?

Headless browser automation is running a real browser engine - the same layout,
JavaScript and rendering code as the normal browser - with no window drawn to a
screen. It is not a simulation and not a stripped-down substitute; it is the
identical engine, started with a flag that tells it not to open a visible
window.

## What it is for

**Servers with no display.** A CI runner or a container has no screen, so a
browser that insists on painting one is a non-starter. Headless is the default
mode for browser-based testing in CI for exactly this reason.

**Speed and resource use.** Not painting pixels to a screen saves work, and
headless instances typically use less memory and start faster, which matters
when you are running hundreds of them in parallel.

**Scale.** Ten browsers with windows compete for screen space that does not
exist on a server anyway. Ten headless instances are ten processes, nothing
more.

## What it is not for

Headless is not a way to make a browser less real. Every layout calculation,
every JavaScript execution, every network request happens exactly as it does
headed. If your problem is that a page's content depends on visual rendering
you can inspect by eye, headless removes the eye, not the rendering.

## The three properties that differ by default

This is the part most explanations skip, and it is why "headless vs headful"
became its own detection category rather than a footnote.

**`navigator.webdriver`.** Set to `true` under most automation setups regardless
of headless or headed, because it reflects the WebDriver protocol being used,
not the display mode. Conflating this with headless specifically is a common
mistake.

**Window and screen dimensions.** A headless launch without an explicit
viewport historically reported unusual or default dimensions that a headed
browser on a real display would not, and older headless-Chrome builds
identified themselves in the user-agent string outright (`HeadlessChrome`).
Modern headless modes have closed the most obvious version of this, but
viewport and screen mismatches remain a real signal if you do not set them
deliberately.

**GPU and rendering behaviour.** A server headless launch commonly has no real
GPU behind it, so [WebGL and canvas output](canvas-fingerprint-changes-every-run.md)
can differ from what a desktop with a graphics card produces - not because it
is headless, but because the machine underneath has no GPU. This is a machine
fact that headless mode exposes rather than causes.

How large that machine effect is, measured in this project rather than
estimated: rendering the same text across **48 family/size/weight
combinations**, the same build produced **9 to 19 distinct alpha levels per
render on Windows and 193 to 256 on Linux**, with 16 of the 48 cases using all
256. Same browser, same code, same display mode. The variable was the host's
rasterizer, and no headless flag touches it. That is the shape of the problem
people are usually looking at when they blame headless: a machine difference
wearing the display mode's name.

## Why headless-specific detection is mostly a solved category

A few years ago, headless Chrome was trivially fingerprintable: a distinct
user-agent substring, missing browser plugins that a real Chrome always
reported, and inconsistent `Notification.permission` behaviour were enough on
their own. Chrome's modern headless mode (the "new headless", the default
since Chrome 112) closed the most obvious ones, and Firefox's headless mode was
never as separable in the first place because it reuses more of the same code
path.

What is left is not "is this headless" but the underlying machine and driving
signals: no GPU, a container font list, the WebDriver flag, and how the
automation drives input. Those exist headed or headless. Running headed on a
machine that has a display available removes one variable for free and costs
nothing; it does not make an otherwise-scripted, WebDriver-flagged, container-fonted
session pass as human.

## What actually changes the outcome

- **Set viewport and screen dimensions explicitly**, rather than accepting
  whatever the launch default is.
- **Run headed when you have a real display and no CI constraint**, since it is
  free and removes one class of headless-specific signal.
- **Do not treat headless-vs-headed as the fix for a detection problem
  elsewhere.** If the machine has no GPU, a container font set and an IP with a
  datacenter reputation, switching to headed changes none of that.
- **If the site is checking WebDriver-protocol flags or the automation build's
  own tells**, that is the driver and the browser, not the display mode.
  [What navigator.webdriver really tells a site](navigator-webdriver-explained.md)
  covers the flag; this project addresses the browser build itself.

## Short answers to the questions that lead here

**Is headless Chrome still detectable?** The obvious markers from the old
headless mode are largely closed in the new one. What remains is GPU absence,
font set and driving-protocol signals, which are machine and driver facts, not
display-mode facts.

**Does running headed avoid bot detection?** No on its own. It removes one
narrow signal class and leaves the IP, the machine and the automation flags
unchanged.

**What is the difference between headless and headful?** Whether a window is
drawn. The rendering engine underneath is identical.

**Why does my headless browser fail a fingerprint test that my headed one
passes?** Usually the machine, not the mode: no GPU changes WebGL and canvas
output regardless of display mode, and a server without a display is often a
container without a GPU too.

**Can a headless browser pass as a real user?** For anything that reads the
DOM and JavaScript state, yes, because that state is identical. For anything
reading GPU-dependent rendering or driver-level flags, the display mode is not
the variable that matters.

**See also:**
[What navigator.webdriver really tells a site](navigator-webdriver-explained.md),
[headless vs headful](headless-vs-headful.md) for the operational trade-offs,
and [scraping a site that blocks headless browsers](how-to-scrape-headless-blocked.md)
for what to check first when a site specifically challenges headless traffic.

## Sources

- [Chromium's own documentation on the new headless mode](https://developer.chrome.com/docs/chromium/headless), retrieved 2026-09-10, for what changed from the old `--headless` implementation.
- [MDN, `Navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver), retrieved 2026-09-10, for the flag's spec-defined behaviour independent of display mode.
- This project's own fingerprint gates, for the GPU and canvas measurements referenced above.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. This page separates
display mode from browser identity because conflating the two sends people
switching a setting that was never the cause.*
