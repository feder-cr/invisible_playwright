---
title: "How accurate is browser fingerprinting?"
description: "Browser fingerprinting accuracy is a uniqueness-versus-stability trade-off: a visitor ID carries a confidence score that drops when signals contradict each other."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 14
---


# How accurate is browser fingerprinting?

Accurate at what, exactly. Fingerprinting is not one measurement, and "how accurate is
it" collapses two different questions that pull in opposite directions: can this browser
be told apart from every other browser, and will it still look like the same browser an
hour from now. A method that is good at the first is usually worse at the second, and
most of the confusion around fingerprinting accuracy comes from not saying which one you
mean.

This page is that trade-off, the confidence score that measures it, why a browser that
contradicts itself scores worse than one that is merely common, and how a
seed-reproducible identity sits on the stable side of the line. It ends with the honest
limit: stability is the opposite of anonymity, and a browser is only one of the things a
site scores.

## Uniqueness and stability are the two axes

A fingerprinting component is useful only when it is both distinctive and stable. Those
two properties fight.

Push for **uniqueness** and you reach for signals that vary a lot between machines: the
exact GPU renderer string, the precise set of installed fonts, a canvas or audio hash
computed from hardware quirks. The more a signal varies, the more it tells two visitors
apart, and the more fragile it is. A driver update, a window resize, a font install, and
the value moves.

Push for **stability** and you reach for signals that almost never change: the platform,
the broad screen category, the language. Those survive across sessions, and they barely
distinguish anyone, because half the web shares them.

Accuracy is where a service lands between those poles. A component that is unique but
unstable identifies you once and then loses you. A component that is stable but common
recognises you forever and also recognises a million other people as you. Real systems
weight many components precisely to buy uniqueness without spending all their stability,
and the weighting is the product.

## The visitor ID and its confidence score

A visitor ID's confidence score answers one question: how sure is the service that this
ID belongs to the same device across visits, not how automated the browser looked.
[FingerprintJS computes a visitor_id](fingerprintjs-visitor-id.md) by hashing many
components together and reports that ID alongside this **confidence score**, which
rewards stability and internal agreement over rarity.

The score is high when the components are stable across the session and agree with each
other. It drops, or the visit gets flagged, when they contradict:

- A **canvas hash that changes on every load** is the textbook case. A returning real
  device paints the same canvas twice; a value that churns per call is either broken
  hardware or active tampering, and [that instability is itself the tell](canvas-fingerprint-changes-every-run.md).
- A **user agent that fights the rest of the stack**: a string claiming one platform on
  an engine that behaves like another, a language list from a different region than the
  exit, a timezone that argues with the address.
- A signal that is simply **absent**, blocked or suppressed, which reads as evasion
  rather than as a clean pass.

Two visitors with identical component values get the same ID. A single visitor whose
values disagree with themselves gets a low-confidence, suspect one. So the "accuracy" of
the ID is really a statement about internal consistency, and a browser is punished more
for contradicting itself than for being common.

## A stable identity reads as a returning device

A seed-reproducible browser sits naturally on the confident side of that score, because
the same seed reproduces every fingerprint field exactly, which is what a confidence
score rewards. invisible_playwright derives every fingerprint field from one seed: pass
the same seed and the GPU, canvas hash, audio context, fonts and screen come back
identical on every run.

```python
from invisible_playwright import InvisiblePlaywright

# same seed -> same GPU, same canvas hash, same audio context, every run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/account")
    page.click("#submit")
```

The `browser` object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every method works
exactly as documented upstream. There is no wrapped subset of the API to learn; the only
new thing is where the browser comes from.

Run that twice and a visitor-ID service sees the same components both times, self-consistent
and unchanging, which is exactly the shape of a returning real device rather than a
churning or contradictory one. You can watch it yourself: read the visitor ID and its
confidence in two separate sessions launched from the same seed, and both should match.
Change the seed and you get a different, equally self-consistent device.

```python
sf = InvisiblePlaywright()
with sf as browser:
    # log the seed so the same identity can be replayed later
    print("seed =", sf.seed)
    page = browser.new_page()
    page.goto("https://example.com")
```

To pin one field, a specific screen size, while leaving the rest seed-derived, see
[pinning fingerprint fields](pinning.md). The rule there matters for this page:
a value pinned in isolation, without its correlated neighbours, is exactly the kind of
contradiction that lowers the score.

## Why this passes most checks, and what it does not touch

Because the fingerprint, the engine behaviour and the driver layer all read as a genuine
Firefox, this design passes most detection checks that live in the browser. The canvas
produces a real hash that is stable across the session. The engine answers descriptor and
prototype probes the way a real Firefox does, so a [tampering-focused suite like CreepJS](creepjs-explained.md)
finds nothing contradicting anything else. There is no
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
no leftover automation global, no headless user agent for a [verdict tool like
BotD](botd-explained.md) to catch.

That is genuinely most of the surface, and it is why the [aggregate trust score a site
computes](browser-trust-score-explained.md) starts from a good place instead of a bad one.

It is not all of it, and pretending otherwise would be both false and reckless. A
consistent browser fixes the browser and nothing else:

- **IP reputation.** A datacenter address, or a residential one already on a shared
  blocklist, fails no fingerprint check and loses anyway. You supply a clean exit.
- **Per-account quotas and rate limits.** These count actions, not fingerprints. A
  perfect identity making a thousand requests an hour is still making a thousand requests
  an hour.
- **Behaviour and timing.** Pointer motion, typing rhythm, the pace of a session. A
  browser that looks real and acts like a script is a real-looking script. Human pacing
  is on you.
- **The TLS and network layer**, decided before any page renders. Because the engine is a
  real Firefox its handshake is a real Firefox's, but that is a property of the engine,
  not something the fingerprint layer can paper over on top of a different client.

So the answer to the title is: accurate enough that consistency is what it measures, and
useful to you precisely when your browser is the honest, consistent one. It helps with the
browser fingerprint. It does not help with the address, the quota, or the behaviour.

## Conclusion

Fingerprinting accuracy is a trade between telling machines apart and recognising the same
machine twice, and a visitor ID's confidence score is where that trade is written down:
high when signals are stable and agree, low when they churn or contradict. A
seed-reproducible browser is built for the stable, self-consistent side of that line, which
is why it reads as a returning real device and passes the checks that live in the browser.

Keep the honest half in view. A stable, high-confidence identity is the opposite of
anonymity; reproducibility is for looking like a consistent real person, not for
disappearing. And the browser is one input among several, so pair it with a clean exit and
human pacing or the parts fingerprinting never touched will find you anyway.

## Short answers to the questions that lead here

**How accurate is browser fingerprinting?** Accurate at measuring consistency. It is good
at recognising a stable, self-consistent browser again and good at flagging one whose
signals contradict each other; it is not a single "is this a bot" verdict.

**What is the confidence score on a visitor ID?** A measure of how stable and
self-consistent the components were, not how automated the browser looked. It drops when a
canvas hash churns per load or a user agent fights the rest of the stack.

**Does a stable fingerprint make me harder to track?** No, the opposite. A stable,
high-confidence identity is easy to recognise again. Reproducibility is for looking like a
consistent real person, not for anonymity.

**Why does a randomised fingerprint score worse than a fixed one?** Because randomising
per call makes signals contradict themselves, which is exactly what the confidence score is
built to catch. A value that changes every load reads as tampering.

**Does looking like a real browser fix everything?** No. It fixes the browser fingerprint,
the engine behaviour and the driver layer. It does not fix IP reputation, per-account
quotas, rate limits, or your behaviour and timing.

**Can two different machines share one visitor ID?** Yes, if their components hash the
same, which is why a common-but-stable signal recognises many people as one. Uniqueness and
stability pull against each other.

## Sources

- Fingerprint's own documentation, [Identification, accuracy, and confidence
  score](https://docs.fingerprint.com/docs/identification-accuracy-and-confidence),
  retrieved 2026-08-28, for the visitor ID as a hash of many components reported
  alongside a confidence score, read from its published description rather than
  its rendered output.
- This project's release gates, which assert that a value read twice in one session
  matches, and that the same seed reproduces the same identity across sessions.

**See also:** [what a visitor ID actually is](fingerprintjs-visitor-id.md),
[why a canvas hash that changes every run is a tell](canvas-fingerprint-changes-every-run.md),
and [how a site turns all of this into one trust score](browser-trust-score-explained.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Seed-determinism is the
feature this page is really about: the same seed gives the same self-consistent device, run
after run.*
