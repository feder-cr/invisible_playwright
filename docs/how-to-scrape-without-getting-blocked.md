---
title: "How to scrape without getting blocked"
description: "Blocking is at least five independent layers, not one signal. The order to fix them cheapest first, and why looking real beats rotating user agents and proxies."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 1
---

# How to scrape without getting blocked

Getting blocked is not one problem, it is at least five independent ones: the address,
the handshake, the machine, the automation layer, and behaviour. Each needs a different
fix, and the fastest way to stay blocked is to work on the wrong one first, in the order
a vendor sells them rather than the order that is cheapest and most likely to be the
actual cause.

Almost everyone arrives at this question with the same first move already made: rotate
the user agent, buy a proxy pool, add a stealth plugin. That is the expensive end of the
problem and the least likely single cause, and starting there is why the block usually
survives all three.

This page is the mental model that puts the five layers in order, cheapest and most
likely first, so the effort lands where the block actually is.

## Blocking is not one signal, it is a stack of them

A site can turn automation away at five different layers, and each one is invisible to a
fix aimed at another:

- **The address.** [Datacenter range](can-websites-detect-a-datacenter-proxy-ip.md), a
  bad-reputation ASN, a country that does not match the rest of the session, an exit a
  thousand other clients are using this minute.
- **The handshake.** The TLS ClientHello and HTTP/2 settings are decided before any page
  loads, and they are distinctive per engine.
- **The machine.** GPU, fonts, audio device, screen. A server has none of these and its
  defaults say so.
- **The automation layer.**
  [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
  leftover globals, the way the browser is piloted.
- **The behaviour.** Pointer motion, typing rhythm, how fast a form is filled, whether the
  page is ever scrolled.

The reason ordering matters is that these are independent. A perfect proxy does nothing
for a software GPU. A stealth plugin that hides `navigator.webdriver` does nothing for a
handshake that is not the browser it claims to be. Most failed debugging is a fix from one
layer applied to a block from another.

## Prove it is detection, then check what you set yourself

Before touching any of the five, open the same URL by hand from the machine that runs the
automation, on the same network. If the manual visit also gets the wrong page, this is the
address and nothing about the browser will help. If the manual visit works and the
automated one does not, the difference is the browser or the behaviour.

Then check the cheapest cause, which is also the most common and the most embarrassing:
the values you overrode yourself. A user agent claiming one platform on an engine
reporting another, a timezone pinned against an exit on a different continent, a language
list from your own laptop on a server somewhere else. The full order to work through is
[the checklist for being detected on one site](playwright-detected-as-bot.md), and step
one there finds more blocks than the proxy ever will.

## Rotating user agents is the wrong first move

The instinct is that variety is safety, that a fresh user agent each request looks like
many people. It does the opposite, because detectors rarely check whether a value is
unusual. They check whether two values that should agree, do.

A [rotated user agent](playwright-user-agent.md) has to agree with the engine, the
platform, the fonts, the codecs and the client hints. Change it alone and you have not
hidden anything, you have manufactured a contradiction that a single query exposes.
Consistency is the property that survives inspection, not novelty. The same logic runs
through the exit: an address in one country with a browser insisting on another is
[a mismatch that is cheap to detect and cheap to fix](timezone-proxy-mismatch.md), but
only if everything is made to tell one story rather than rotated independently.

## The tells no plugin can touch

Three layers are simply not in JavaScript's gift to change, which is why every page-level
stealth plugin ever written leaves them intact:

- **The machine.** A [software WebGL renderer](webgl-renderer-strings.md) announces a box
  with no graphics hardware, and worse, the string can say NVIDIA while the pixels are
  still drawn by a rasterizer that is not. A [font set that does not match the claimed
  platform](headless-fonts-differ.md) is a one-line check. An empty audio device or voice
  list is a container saying it is a desktop.
- **The handshake.** A [TLS fingerprint that is not the browser's](ja3-ja4-tls-fingerprint.md)
  while the user agent says it is, is decisive, and no property override reaches it.
- **The behaviour.** A pointer that jumps between coordinates without passing through the
  space between, keystrokes at a uniform interval, a form filled in eighty milliseconds.
  This is what explains a block that arrives minutes into a session rather than at the
  first request, and it needs [movement that is actually produced rather than
  declared](human-mouse-movement.md).

These are the reason "without getting blocked" is mostly an engine and infrastructure
problem, not a plugin problem. A property can be set from JavaScript; an output has to be
produced by a real machine.

## What "without getting blocked" actually means

There is no setting that makes a browser permanently invisible, and any page that promises
one is selling the proxy. The achievable goal is narrower and more useful: look like a
real browser on every surface a site can read, and do not create the signal you are trying
to avoid.

Two consequences fall out of that, and both are counterintuitive:

**A suppressed signal is itself a tell.** Blocking
[WebRTC](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API), returning an empty
canvas, or answering a probe with nothing is not stealth, it is a browser announcing that something
is intercepting it. We learned this the expensive way: our own WebRTC gate asserted the
absence of a leak, passed run after run, and was passing because the feature was returning
nothing at all behind a proxy. [Assert the presence of the right signal, not the absence
of a wrong one](how-to-test-bot-detection.md). A real browser leaks the ordinary things a
real browser leaks.

**Volume is its own fingerprint.** Hammering one endpoint from one address at machine
speed produces a velocity signal that no per-request disguise hides. We once flagged our
own product for exactly this, and the flag belonged to the test harness, not the browser.
[Space the requests](how-to-rate-limit-your-scraper-playwright.md), vary nothing you do
not have to, and keep one identity coherent rather than churning through many shallow
ones. The way this project keeps an identity coherent is to derive every surface from a
single seed, so the same seed is the same machine every time and a failing run is
reproducible instead of a new guess.

## Conclusion

The short version of everything above: work the layers in order of cost and likelihood,
not in the order a vendor sells them. Reproduce the block by hand, remove the values you
set yourself, make everything tell one story instead of rotating pieces of it, and accept
that the machine, the handshake and the behaviour are where the durable blocks live.
Rotating proxies is seventh on that list for a reason, and reaching for it first is the
single most common reason a scrape stays blocked.

## Short answers to the questions that lead here

**What is the fastest way to stop getting blocked?** Open the page by hand from the same
machine to prove it is detection, then remove every value you set yourself and add them
back one at a time. That finds more blocks than any purchase.

**Do I need residential proxies?** Sometimes, but it is the last thing to try, not the
first. A datacenter address is a real cause, and it is also the most expensive fix and the
least likely to be the only one.

**Should I rotate the user agent on every request?** No. It has to agree with the engine,
the platform and the fonts, and rotating it alone creates contradictions rather than
hiding anything.

**Why do I get blocked only after a few minutes?** That pattern is behaviour, not
fingerprint. Uniform timing, a pointer that teleports, or a session with no movement
before a click all arrive as a delayed block rather than an instant one.

**Can a stealth plugin make me unblockable?** No. Plugins patch JavaScript properties, and
the machine, the TLS handshake and the behaviour are not JavaScript properties. Those are
where a serious detector looks.

**Is an empty canvas or blocked WebRTC safer?** No, it is a tell. A suppressed signal
tells a detector something is intercepting the browser. The goal is to look real, not to
go blank.

## Sources

- [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
  and the [WebRTC API](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API) on MDN,
  the two documented browser surfaces this page treats as ground truth for what a real
  browser actually exposes.
- [The checklist for being detected on one site](playwright-detected-as-bot.md) and
  [how to test whether your browser is detected](how-to-test-bot-detection.md), which this
  page is the entry point to.
- This project's release gates, including the WebRTC gate whose absence-only assertion
  produced the false pass described above, and the velocity flag that turned out to be the
  test harness rather than the browser.

**See also:** [WebGL renderer strings](webgl-renderer-strings.md) and [why headless
renders different fonts](headless-fonts-differ.md) for the machine layer,
[the TLS handshake no in-page test can see](ja3-ja4-tls-fingerprint.md) for the one that
is decided before the page even loads, and
[how websites detect bots](how-do-websites-detect-bots.md) for the layer-by-layer
scoring model that sits behind this ordering.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level and driven by stock Playwright. The proxy-first mistake
at the top of this page is one I made before I wrote the checklist it points to.*
