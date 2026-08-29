---
title: "Is changing the user agent enough to avoid detection?"
description: "User agent changes alone do not avoid bot detection - detectors cross-check navigator.platform, oscpu, TLS, WebGL and Client Hints. Mismatches flag harder."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 24
---


# Is changing the user agent enough to avoid detection?

Short version: no. The user agent is one string the browser reports about itself, and
every modern detector treats it as a claim to be checked rather than a fact to be
recorded. It gets compared against half a dozen other surfaces that describe the same
machine, and if the string disagrees with any of them, you have not hidden anything. You
have manufactured a contradiction that an honest, unedited user agent would never have
produced.

This page walks through what the string actually is, what it gets checked against, why a
mismatch is worse than leaving it alone, and where a consistent user agent stops helping
so you know what you still have to supply yourself.

## What the user agent actually is

[`navigator.userAgent`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/userAgent) is a single self-reported line. The browser hands it out, the same
string rides in the `User-Agent` request header, and nothing about it is verified at the
point you read it. Because it is trivially settable, it is also the first thing people
reach for: change the string, claim to be a different browser or platform, done.

The trouble is that the string was never the thing being measured. It is the easiest
signal to forge, which is exactly why no serious detector trusts it on its own. It is
kept around as one input among many, and its main use is as a claim to test the other
inputs against. [Rotating the user agent does not rotate the browser](playwright-user-agent.md):
everything else keeps answering from the real engine, so the only thing you changed is
the one value already assumed to be a lie.

## Everything the string gets cross-checked against

A detector does not ask "is this user agent unusual". It asks "do the values that must
agree, agree". The user agent names a browser and an operating system, and each of those
claims has independent witnesses:

- **`navigator.platform` and `navigator.oscpu`.** These are derived from the OS the
  engine was compiled on, not from the string you set. A Windows user agent on a Linux
  build [leaks Linux on platform and oscpu](navigator-platform-oscpu-consistency.md) while
  the string insists on Windows.
- **The TLS handshake.** The first packets of the connection carry a fingerprint of the
  engine that made them, decided before a single line of JavaScript runs. A Chrome user
  agent riding on a Firefox TLS stack is [a contradiction the server sees before the page
  loads](tls-fingerprint-user-agent-mismatch.md), and you cannot patch it from the page.
- **The WebGL renderer.** The GPU vendor and renderer strings describe real graphics
  hardware and driver. A Windows user agent next to [a Linux or software renderer
  string](webgl-renderer-strings.md) names two different machines at once.
- **[Client Hints](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Client_hints).** `Sec-CH-UA`, `navigator.userAgentData` and the user agent string are
  all generated from one internal state in a genuine browser, so [they must tell the same
  story](client-hints-sec-fetch.md). Set the string by hand and the structured hints keep
  reporting what the engine really is.

Any one of these disagreeing with the string is a decision. Together they mean the user
agent is not evidence of anything except that someone edited it.

## Why a mismatched user agent flags harder than an honest one

This is the part that surprises people. Leaving the user agent alone on a real browser is
boring: everything agrees because it all comes from the same engine, and boring is what
you want. Editing the string to claim a browser or OS you are not running takes a browser
that was internally consistent and makes it internally contradictory.

An honest user agent that happens to say "automation-friendly build" is a mild signal. A
Windows user agent sitting on a Linux WebGL renderer, or a Chrome string on a Firefox
handshake, is a hard signal, because real browsers are physically incapable of producing
that combination. You did not lower your score. You created a pattern that only ever
appears when a string has been forged, which is the single most self-incriminating thing a
session can carry.

The rule that falls out of this: never set the user agent by hand unless every correlated
surface moves with it, and in practice you cannot move the TLS handshake or the compiled
platform from Python at all. So the honest answer to "should I change the user agent" is
almost always "no, and stop trying to".

## What consistency looks like with a real engine

The way out is not a better string. It is not having to set the string at all, because
the whole stack already agrees. That is the approach invisible_playwright takes: it ships
a real Firefox patched at the C++ level and driven by stock Playwright, so the user agent,
`navigator.platform`, `oscpu`, the TLS handshake, the WebGL renderer and the Client Hints
all come from one real engine and therefore already tell the same story. The user agent is
derived from the actual upstream Firefox version rather than hand-set, so it cannot drift
away from the browser reporting it.

Switching from plain Playwright is two lines, and you do not set a user agent anywhere:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    ua = page.evaluate("() => navigator.userAgent")
    platform = page.evaluate("() => navigator.platform")
    print(ua)        # a real Firefox user agent, derived from the engine version
    print(platform)  # agrees with the user agent because it is the same engine
```

The `browser` object is a real Playwright `Browser`, so every standard method works as
documented upstream. The `seed` makes the whole identity reproducible: the same seed gives
the same machine every run, which is what lets you replay a failure instead of guessing at
it. You are not editing a string to look like a browser. You are running one, so the
values agree because they have no way not to.

That consistency is why a real engine passes most in-page and network-layer detection: the
fingerprint surfaces, the TLS handshake and the automation-driver layer all read as a
genuine Firefox rather than as a headless tool wearing a costume.

## Where a consistent user agent stops helping

Being honest about this matters more than any feature claim. A stack that agrees with
itself removes a whole class of tells, but it is not the whole session, and it does not
touch the things that live outside the browser:

- **IP reputation.** A perfectly consistent browser on a datacenter address, or on a proxy
  IP that is already on a block list, still loses. The browser cannot fix where the packets
  come from. Supply a clean exit yourself.
- **Per-account quotas and rate limits.** Consistency does not raise a limit. Too many
  actions from one account or one address is a volume signal, not a fingerprint signal.
- **Behaviour and timing.** Pointer motion, typing rhythm, how fast a form is filled, the
  pause shaped like machine latency. Some sites only watch behaviour, and a flawless
  fingerprint does nothing there. Human pacing is on you.

So the accurate framing is: fixing the user agent, or rather not needing to fix it, closes
the identity-contradiction hole and helps with the fingerprint and network-layer checks.
It does not make you undetectable, and nothing does. Pair the consistent engine with a
clean address and human-shaped behaviour, and each of those is a separate job.

## Conclusion

Changing the user agent is not enough because the user agent was never the thing being
tested. It is a claim, cross-checked against the platform, oscpu, the TLS handshake, the
WebGL renderer and Client Hints, and editing it in isolation converts a consistent browser
into an obviously forged one. The durable fix is consistency across the whole stack, which
you get for free from a real engine and cannot fake with a string. Then supply the parts
the browser cannot: a clean exit and human pacing.

## Short answers to the questions that lead here

**Is changing the user agent enough to avoid detection?** No. It is one self-reported
string, cross-checked against several surfaces that come from the real engine, and editing
it alone creates a contradiction instead of hiding anything.

**Does a mismatched user agent make things worse?** Yes. An honest string on a real browser
is boring; a Windows string on a Linux renderer, or a Chrome string on a Firefox handshake,
is a combination real browsers cannot produce, so it flags harder than leaving it alone.

**What does the user agent get compared against?** navigator.platform and oscpu, the TLS
handshake, the WebGL renderer and vendor strings, and Client Hints including
navigator.userAgentData.

**Should I randomise or rotate the user agent?** No. Rotating the string does not rotate
the engine, so every other surface keeps reporting the real browser and now disagrees with
the string.

**If the user agent is consistent, am I undetectable?** No. A consistent engine passes most
fingerprint and network-layer checks, but it does not fix IP reputation, account quotas,
rate limits or behaviour. Those are yours to supply.

**Why does invisible_playwright not let me set the user agent?** Because it derives the
string from the real Firefox version so it cannot drift from the engine reporting it,
which is the entire point.

## Sources

- This project's own release gates and fingerprint parity checks, which compare every
  identity surface field by field against a stock browser rather than reading a single
  string.
- The public detection suites this documentation set covers page by page, including
  [CreepJS](https://github.com/abrahamjuliot/creepjs), [BotD](https://github.com/fingerprintjs/BotD),
  [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs), [sannysoft](https://bot.sannysoft.com/)
  and [BrowserLeaks](https://browserleaks.com/), retrieved 2026-08-28, each of which
  cross-checks self-reported values against measured ones.

**See also:** [why you should not set the user agent in Playwright](playwright-user-agent.md),
[the TLS handshake versus the user agent claim](tls-fingerprint-user-agent-mismatch.md),
and [navigator.platform and oscpu on a spoofed OS](navigator-platform-oscpu-consistency.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The user agent it reports is
derived from the real engine version, so there is no string to edit and nothing to keep in
sync.*
