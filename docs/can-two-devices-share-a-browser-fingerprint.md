---
title: "Can two devices share a browser fingerprint?"
description: "Yes - that's the goal: a shared browser fingerprint hides you among real users, while a unique one is trackable. How anonymity sets work and how to land in one."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 26
---


# Can two devices share a browser fingerprint?

Yes, two devices can present the same browser fingerprint, and if your goal is not to
be followed around, that is the outcome you want rather than the one to avoid.

The intuition most people bring to this is backwards. It feels like a fingerprint should
be unique, like a real one, and that a collision is a failure. For tracking it is the
opposite: a fingerprint that only you have is a fingerprint that follows only you. A
fingerprint that thousands of real people also have tells a tracker almost nothing about
which of them you are.

This page is about that trade, why a common configuration beats a clever one, and how to
land on a shared-looking identity on purpose instead of by luck.

## The anonymity set is the whole game

A browser fingerprint is not one value. It is a hash of many components read from
JavaScript: the GPU string, the canvas and audio outputs, the installed fonts, the
screen geometry, the platform, the language list, and dozens more. [FingerprintJS calls
the resulting hash a visitor ID](fingerprintjs-visitor-id.md), and its job is to
recognise you again on the next visit.

The number that decides whether it can is the size of the group that shares your value.
This group is the anonymity set. If ten thousand other browsers produce exactly your
hash, the visitor ID identifies a crowd, not you, and the tracker has to fall back on
other signals to tell you apart. If your hash is unique, the visitor ID identifies you
and only you, and it will do so every session until one of those components changes.

So the useful question is not "is my fingerprint unique". Uniqueness is the failure mode.
The useful question is "how many real people look exactly like this", and you want that
number large.

A one-of-a-kind fingerprint loses this even when every individual value is plausible. A
GPU nobody else reports, a screen size nobody else has, a font set assembled by no real
installer: each looks fine alone and, taken together, marks one browser out of everyone.
That is why [pinning fields by hand is risky](pinning.md) unless you know the
combination is one that real machines actually ship.

## Why a common configuration beats a clever one

The instinct to look special is exactly the wrong instinct here.

Trackers do not need to prove you are automated to follow you. They only need your
combination of values to be rare. Rarity is the signal, and it does not care whether the
values are suspicious. A perfectly innocent, perfectly consistent, perfectly unique
browser is perfectly trackable.

So the design target is the boring middle of the distribution: a Windows version many
people run, a GPU many people have, a screen size that sits on a common laptop panel, a
language list a large population shares. Land there and you disappear into a group. Land
on the tails, even with clean values, and you stand out by being precise.

This is also why "resist fingerprinting" and "blend into a crowd" are two different
strategies that can conflict. The browser's own
[resistFingerprinting mode](resist-fingerprinting.md) makes many browsers report the same
flattened values, which is one way to build an anonymity set, but the set it puts you in
is the set of people who turned that mode on, and that set has its own recognisable shape.
Blending in with the general population is a different bet: look like the common case, not
like the privacy case.

## How a seed lands you in a crowd

invisible_playwright generates each identity to look like a common, real-world Windows
machine rather than a rare or unusual one, because a configuration many genuine users
also present blends in, while a one-of-a-kind combination stands out and is trackable.
The GPU string, the renderer, and the capability limits it reports are chosen together
and cross-checked, so
[the WebGL parameters agree with each other](webgl-parameters-are-identical.md) the way a
real driver's do rather than being mixed from different machines.

Switching from stock Playwright is two lines, and the identity is generated for you:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright() as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

Pass a seed and every field it implies comes back identical, run after run, so you can
inspect the exact identity a seed produces and reuse it:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # same GPU, same canvas hash, same fonts, same screen, every run
```

The `browser` returned is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser). Every method works
exactly as documented upstream; there is no reduced API to learn.

Two different seeds give two different plausible identities, and because each is
generated to be a common real-world configuration, both can land in a well-populated
part of the space.
That is the sense in which two of your own devices can "share" a look: not that they hash
to the same value by accident, but that each is a common configuration, and common
configurations are shared by construction with the real population.

## The caveat: fingerprint is not the only thing matched

Blending in on fingerprint does not blend in everything, and this is the honest limit of
what any fingerprint layer can do.

A browser identity that looks like a common real machine is why invisible_playwright
passes most in-page detection: the fingerprint, the TLS handshake and the driver layer
read as a genuine Firefox rather than an automated one. That is a real and load-bearing
result. It is also only one of the axes a serious system matches on, and the others are
matched separately:

- **Your IP address.** A crowd-blending fingerprint on a datacenter address, or on a
  proxy IP that is already known and shared by a thousand scrapers this minute, is
  singled out by the address alone. Fingerprint collisions do not help you here; supply a
  clean exit yourself.
- **Your request pattern.** Rate, timing, the order you touch pages, per-account quotas.
  A hundred sessions that each look like a different real person, all arriving in the same
  ten seconds from the same place, are a pattern no fingerprint hides. Human pacing is
  yours to supply.
- **The story your surfaces tell together.** A common browser fingerprint with a timezone
  or language that contradicts the exit IP is not blended in, it is inconsistent, and
  [a timezone that does not match the proxy](timezone-proxy-mismatch.md) is one of the
  most common self-inflicted versions of this.

So the accurate claim is narrow and worth stating plainly: a shared, common fingerprint
helps you avoid being tracked and identified by fingerprint. It does nothing for IP
reputation, quotas, rate limits, or behaviour. Those are matched on their own, they can
single you out on their own, and the reader supplies the clean proxy and the human pacing
that address them. Anyone promising that one browser makes a session undetectable is
selling the part that does not exist.

## Conclusion

Two devices sharing a browser fingerprint is not a bug, it is the objective. Uniqueness
is what makes a browser trackable across sessions; membership in a large group of
look-alike real users is what makes it hard to follow. Sampling common, cross-checked
real-world configurations lands you in that group on purpose. Then pair it with an
address and a rhythm that fit the same story, because fingerprint blending buys you the
fingerprint axis and only that one.

## Short answers to the questions that lead here

**Can two devices have the same browser fingerprint?** Yes. Many components are shared by
large populations, so identical hashes across devices are normal, and for anti-tracking
they are what you want.

**Is a unique fingerprint good or bad?** Bad, if you do not want to be followed. Unique
means recognisable on every future visit. Common means lost in a crowd.

**Does sharing a fingerprint make me anonymous?** On the fingerprint axis it hides you in
a group. It does nothing about your IP address or your request pattern, which are matched
separately and can identify you on their own.

**Why does invisible_playwright aim for a common configuration instead of a rare one?**
Because rarity is the tracking signal. A configuration many real users also present puts
you in a large anonymity set instead of marking you out.

**If I pass the same seed on two machines, do they look identical?** Yes. The same seed
produces the same GPU, canvas, audio, fonts and screen, which is deliberate: it makes a
run reproducible and lets two devices present the same known-good identity.

**Will a good fingerprint alone stop me from being blocked?** No. It handles the
fingerprint, TLS and driver layers. A datacenter IP or a robotic request pattern is still
a datacenter IP or a robotic pattern, and you supply the clean exit and the human pacing.

## Sources

- The FingerprintJS open-source library's visitor ID model, [read from its own
  source](https://github.com/fingerprintjs/fingerprintjs), retrieved 2026-08-28, for how
  many components combine into one recognisable hash.
- Mozilla's own documentation of the `privacy.resistFingerprinting` mode, [read on its
  own wiki page](https://wiki.mozilla.org/Security/Fingerprinting), retrieved
  2026-08-28, for how the mode flattens timezone, locale and window size to one shared
  value instead of a unique one.
- This project's approach of generating common, real-world Windows and GPU
  configurations rather than unique ones, and its cross-checking of GPU string, renderer
  and capability limits against each other.
- This project's release gates, which separate fingerprint realness from IP and request
  signals precisely because a clean fingerprint does not clear the other two.

**See also:** [what a FingerprintJS visitor ID actually measures](fingerprintjs-visitor-id.md),
[why the WebGL parameters have to agree with each other](webgl-parameters-are-identical.md),
and [when the timezone does not match the proxy](timezone-proxy-mismatch.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The anonymity-set framing
is the one that keeps me from "fixing" a shared fingerprint that was doing its job.*
