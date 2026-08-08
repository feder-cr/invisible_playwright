---
title: "Firefox or Chromium for anti-detect automation"
description: "Firefox vs Chromium for anti-detect: no CDP surface, one identity not three, detection tuned for Chromium - traded against Firefox's smaller, rarer traffic share."
parent: "Comparisons"
nav_order: 2
---


# Firefox or Chromium for anti-detect automation

Firefox is the stronger engine for anti-detect automation when the target's detection is
Chromium-tuned, which most of it is: it exposes no CDP automation protocol, keeps one copy
of its identity instead of three, and ships one binary instead of a separate headless one.
The cost is real and specific - Firefox is a small single-digit share of real traffic, so
its fingerprint is rarer than a Chrome one. Pick whichever your stack supports, then fix
the machine, because in most cases the engine is not the binding constraint.

The usual framing is that Firefox gives stronger engine-level stealth at a higher
resource cost, and Chromium gives better compatibility. Both halves are true and neither
is the interesting part.

The interesting part is that the two engines differ **structurally** in how much automation
surface they have, in how many copies of their identity they maintain, and in how much
attention detection has paid them. And that one of those differences cuts hard against
Firefox in a way most comparisons skip.

This page is the four structural arguments for Firefox, the one serious argument against
it, what the choice does not fix, and how to decide.

## Firefox vs Chromium at a glance

The differences that matter are structural, not cosmetic. This table summarises them; each
row is unpacked in its own section below.

| Dimension | Firefox (patched) | Chromium |
|---|---|---|
| Automation protocol | No CDP; driven by a patch whose artefacts are smaller and less studied, not absent | CDP shipped in the browser, a well-studied detection surface |
| Copies of identity to keep in sync | One (no Client Hints) | Three: user agent string, `Sec-CH-UA` headers, `navigator.userAgentData` |
| Capability parity with what people run | Firefox is Firefox; no stripped variant runs in automation only | Default managed build fails a DRM capability check that real Chrome, Edge, Brave and Opera pass |
| Headed vs headless | One binary with a flag | Different binaries by default in recent Playwright |
| Detection attention | Comparatively little; most research targets Chromium | The overwhelming majority of accumulated detection work |
| Share of real traffic | Small single-digit share, so the fingerprint is rarer (a real cost) | Large majority of real browsers |
| AI agent framework fit | Usually not; most frameworks drive Chromium over CDP | Native fit for CDP-based frameworks |

## Firefox has no CDP surface

Firefox has no CDP. Playwright drives it through a patch of its own, which means there is
no shipped protocol to detect.

Chromium's automation protocol is the Chrome DevTools Protocol, and it is a first-class,
shipped part of the browser. That is convenient and it is also a surface: enabling the
runtime domain has observable effects, the listener exists, and a body of detection work
targets exactly those artefacts. Chromium stealth builds spend a lot of their effort
stripping them.

**And here is the honest half**, which the comparisons that say "Firefox has no CDP"
always omit: that patch has its own artefacts, and they are not trivial. Attaching a
JavaScript debugger disables the optimising JIT, which is measurable through timing
alone. Its own frames appeared in `Error.stack`. Evaluated code was labelled as debugger
evaluation. And a serialisation helper ran in the page's realm before the page's first
script, touching built-in prototypes in a world that should have been untouched.

[All four are written up here](debugger-timing-detection.md), because we had to find and
fix them.

So the correct statement is not "Firefox has no automation surface". It is that the
surface is **different, smaller, and much less studied**, because far fewer people are
looking.

## The automation Chromium is not the Chromium people run

Playwright's default managed Chromium closed its codec gap in 2026 - as of Playwright 1.57
it ships Chrome for Testing, which carries H.264 and AAC. Widevine, the DRM real streaming
services require, did not come along with it: checked directly, the current default still
rejects a Widevine session request. Real people run Chrome, Edge, Brave or Opera, all of
which have it - this is the strongest version of the argument, and the one most comparisons
skip entirely.

So the capability set that automation reports belongs to almost nobody on that one
remaining axis, and it is testable in one line. Worse, it is a **capability rather than a
value**: a missing DRM module is missing machine code, so no property override produces it,
and a user agent claiming Chrome on a build that can't negotiate Widevine is a provable
contradiction rather than an unusual reading.

Firefox has no equivalent split. There is no stripped Firefox that automation runs and
people do not, so a patched Firefox is Firefox in the ways a capability check can test.

[The full version is here](chromium-is-not-chrome.md), including the branded-channel
escape and what it costs.

## Firefox has one identity, Chromium has three

Modern Chromium reports who it is in three places: the user agent string, the
[`Sec-CH-UA`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Client_hints)
request headers, and `navigator.userAgentData`. In a real browser all three
come from one internal state and agree by construction.

For anything modifying that identity, they are three copies to keep in sync, and the
headers are sent **before any script runs**, so a page-level patch cannot reach them at
all. Comparing them is a few lines on a server.

Firefox does not implement Client Hints. There is no second pipeline and no third copy.
The absence is correct, expected, and shared with every other Firefox on the internet.

That is a genuine structural advantage, and it comes with a matching constraint:
[you cannot then claim to be Chrome](client-hints-sec-fetch.md), because a request
claiming Chrome and carrying no Client Hints contradicts itself.

## Headless is one binary, not a different one

Firefox is one binary with a flag. The flag still changes behaviour, and
[this project avoids it entirely by running headed and hiding the window](headless-vs-headful.md),
but the starting position is one codebase rather than two.

[Recent Playwright versions default Chromium's headed and headless modes to different
binaries](https://playwright.dev/docs/browsers): a regular Chromium build for headed
operations and a separate headless shell for headless mode. A separate implementation
composites and paints differently, which is a real difference in what gets rendered and
when.

## Detection is tuned for Chromium

The overwhelming majority of browser automation is Chromium, so the detectors, the
research, the blog posts and the heuristics are Chromium-first: `window.chrome` shape,
CDP artefacts, the Chromium headless user agent, ChromeDriver's own leftovers - this one
is about attention rather than architecture.
[The `cdc_` variable](cdc-variable-explained.md) is a Chromium-family problem that
Firefox users never think about.

Running Firefox sidesteps a large amount of accumulated, specific work. Not because
Firefox is harder to detect in principle, but because fewer people have written the
detection.

That advantage is real and it is also **borrowed**. It shrinks as Firefox-based tooling
becomes more common, and nothing about it is permanent.

## The argument against Firefox, which is serious

Chrome and Chromium derivatives are the large majority of real browser traffic. Firefox
is a small single-digit share.

So a Firefox fingerprint is intrinsically **rarer than a Chrome one**, and rarity is the
thing this entire documentation set warns about on every other page. A perfectly
consistent Firefox is still a member of a much smaller crowd.

Being straight about what that does and does not mean:

- **It does not make you look automated.** Millions of people use Firefox, and being one
  of them is unremarkable.
- **It does narrow the population** a scoring system places you in, before anything else
  is considered.
- **It matters more where a site's traffic is unusually Chrome-heavy**, and less where the
  audience is broad.

Anyone selling you a Firefox-based tool without mentioning this is not being straight with
you. It is the strongest argument on the other side and it does not go away.

### The compatibility cost, which is concrete

Most AI agent frameworks drive Chromium over CDP. We checked five of them by reading
their source: browser-use, Stagehand and Skyvern are CDP, so a Firefox engine does not
drop into any of them. [The verified table is here](ai-browser-agents-stealth.md).

If your surrounding stack is one of those, the engine choice has already been made for
you.

## What the choice does not fix

Neither engine changes the machine, which is worth stating plainly because the engine
debate absorbs attention that belongs elsewhere. On a server, both report a software
renderer, a container font set, no audio device, no speech voices and a default screen, and
[each of those is a stronger signal than the engine](playwright-docker-detection.md).

Neither changes the TLS handshake beyond making it genuinely that browser's, and
[neither can be patched from JavaScript](ja3-ja4-tls-fingerprint.md).

Neither changes behaviour. A pointer that teleports is a pointer that teleports on both.

If you are choosing an engine before fixing those, you are optimising the smaller term.

## Conclusion

Firefox's advantages are structural: no CDP surface, one identity instead of three, one
binary instead of two, and a detection ecosystem that has spent its effort elsewhere.
Chromium's advantage is that it is what almost everybody runs, which is not a small thing
when the question being asked is "does this look like a normal visitor".

The honest recommendation is conditional. If your target's heuristics are Chromium-tuned,
which most are, Firefox skips a lot of them. If your target weights how common your
browser is, Firefox costs you. If your framework speaks CDP, the question is already
answered.

And in most real cases the engine is not the binding constraint. The machine is.

## Short answers to the questions that lead here

**Is Firefox better than Chromium for anti-detect?** Structurally it has less automation
surface and one identity to maintain instead of three. It is also a much smaller share of
real traffic, which cuts the other way.

**Does Firefox avoid CDP detection?** It has no CDP. Its automation layer has its own
artefacts, which are smaller and less studied rather than absent.

**Why do Client Hints favour Firefox?** Because Firefox does not implement them, so there
is no second copy of the identity to keep consistent. The constraint is that you cannot
claim to be Chrome.

**Is a Firefox fingerprint suspicious?** No. It is less common, which narrows the
population you are placed in. Millions of real people use Firefox.

**Can I use a Firefox engine with an AI agent framework?** Usually not. Most drive
Chromium over CDP.

**Which should I pick?** Whichever your stack supports, then fix the machine. That is the
larger term in almost every case.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
[AI browser agents and stealth](ai-browser-agents-stealth.md) for the framework
constraint, and [Playwright in Docker](playwright-docker-detection.md) for the part that
is the same either way.

## Sources

- The Chrome DevTools Protocol as a shipped Chromium interface, and this project's own
  automation-layer artefacts, documented on
  [the debugger page](debugger-timing-detection.md).
- [User Agent Client Hints](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Client_hints)
  as implemented in Chromium and absent in Firefox.
- Recent Playwright [defaulting headed and headless Chromium to different
  binaries](https://playwright.dev/docs/browsers).
- Five agent frameworks read from source, tabulated on
  [the agents page](ai-browser-agents-stealth.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. We chose Firefox and the section arguing against it is
the one worth reading twice.*
