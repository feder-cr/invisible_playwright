---
title: "How Akamai Bot Manager actually works"
description: "Akamai's own documentation plus a real GitHub issue, read together: what the sensor collects, how the _abck cookie and Bot Score work, and why a matching TLS/JA3/JA4 fingerprint was not enough to pass it."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 27
---


# How Akamai Bot Manager actually works

Akamai Bot Manager is not one check, it is a scoring pipeline. A JavaScript sensor
collects on the order of a hundred browser and device signals, encodes them into a
payload, and a server-side model combines that payload with the request's network-level
fingerprint into a Bot Score from 0 (human) to 100 (bot). Passing one layer of that
pipeline, even the hardest layer, does not mean you pass the rest.

A real, documented case makes that concrete, not theoretical. In April 2026 a
user of [Camoufox](https://github.com/daijro/camoufox), a separate open-source project
that also patches Firefox at the C++ level for automation, filed
[an issue](https://github.com/daijro/camoufox/issues/555) reporting that an
Akamai-protected site blocked their Camoufox session with a flat 403 while stock,
unpatched Firefox driven through the same Playwright automation reached the page and a
solvable challenge. The reporter had already checked the one thing everyone checks
first, the TLS and HTTP/2 handshake, and it matched byte for byte. This page walks
through how Akamai's own documentation and third-party reverse-engineering describe the
mechanism, then works through that issue in detail, because it is the clearest public
evidence that a matching network fingerprint is necessary and not sufficient.

This page is built from Akamai's own product documentation, two independent
technical write-ups that reverse-engineered the sensor payload, and the GitHub issue
above, all retrieved 2026-08-30. Where Akamai's own docs are deliberately vague about
internals, which they are, the page says so instead of filling the gap with a guess.

## What Akamai actually scores

Akamai's own materials describe Bot Manager as sitting a step above basic protections,
aimed at "APIs or transactional pages that handle things like login, signup, or
checkout." The product page states plainly that it "generates a score from 0 (human) to
100 (bot), looking at all anomalies, starting with the very first request," and that the
underlying models are built from "billions of bot requests and logins daily."

Akamai's technical documentation names five detection methods, and only some of them
involve a browser at all:

| Method | What it does |
|---|---|
| Akamai-validated bots | A maintained list of known bots (roughly 1,400 as of Akamai's own count), Akamai identifies them for you |
| Custom-categorized bots | You define your own categories and rules |
| Transparent detection | Evaluates traits of the request itself, without the client knowing it is being tested |
| Active detection | An interaction that only a real browser typically produces |
| Behavioral detection (Premier tier) | Movement patterns on specific endpoints like login or checkout |

Transparent and active detection are the two that a patched browser engine is actually
answering. Behavioral detection is a separate problem entirely, closer to
[mouse-dynamics biometrics](mouse-dynamics-behavioural-biometrics.md) than to anything a
C++ patch to the rendering engine touches.

## The sensor pipeline, step by step

Akamai does not publish the internal format of what it collects; a Scrapfly write-up on
the mechanism and a GitHub reverse-engineering project both had to work it out from the
obfuscated client script. Read together, the pipeline looks like this:

1. **The sensor script loads.** One reverse-engineering write-up
   ([`Edioff/akamai-analysis`](https://github.com/Edioff/akamai-analysis)) describes a
   roughly 512KB obfuscated JavaScript payload that decrypts an internal string table at
   runtime before it does anything else, which is itself a small piece of anti-analysis
   design.
2. **Signal collection.** Both sources independently land on the same rough figure: over
   a hundred signals, grouped into device environment (screen size, color depth,
   hardware concurrency, device memory, platform), the browser API surface (navigator
   fields, plugin behavior, WebGL renderer and vendor strings, canvas rendering
   behavior), behavioral telemetry (mouse paths, click cadence, key timing, scroll
   rhythm), timing metrics (DOM milestones, script execution intervals), and the results
   of small JavaScript and Web Crypto challenges used to confirm the script actually ran
   rather than being replayed.
3. **The payload is generated and encoded.** Described elsewhere as involving PRNG-based
   shuffling and substitution seeded from a cookie-derived hash in the newer protocol
   version, which is a deliberate anti-tampering design, not just minification.
4. **It is posted back.** Reverse-engineering write-ups describe the payload going to a
   validation endpoint under a deployment-specific path; Akamai's own documentation does
   not publish this, consistent with treating the whole exchange as "tamper-sensitive."
5. **The server responds with an `_abck` cookie.** Akamai's own language for it is
   "mitigation state," and it is explicit that the internal format is deployment-specific
   and "should [not be expected] to remain valid" if manually edited or reused across
   sessions.
6. **Every later request is judged against that cookie's state**, not just against a
   fresh score. Documented failure modes include the cookie expiring, a "replay
   mismatch" when the same cookie shows up from a different IP or TLS profile, integrity
   failures from a modified cookie string, and "behavioral conflicts where cookie history
   contradicts runtime signals."

That last point matters for automation specifically: a session's history is part of what
gets scored, not only its current request. A browser that looks perfect on the first
page load and then behaves inconsistently three requests later is scored against its own
earlier behavior, not just against a static bar.

## What the network layer alone tells Akamai

One useful breakdown of the system groups everything Akamai evaluates into roughly seven
categories: IP reputation, TLS fingerprint consistency, HTTP protocol behavior, cookie
state, JavaScript sensor execution, browser fingerprint integrity, and behavioral
continuity across requests. The network-layer pieces are read the same way [JA3/JA4 and
HTTP/2 fingerprinting are described elsewhere on this
site](ja3-ja4-tls-fingerprint.md): TLS version, cipher and extension ordering, elliptic
curve data for the TLS side, and SETTINGS frame values plus pseudo-header ordering
(`:method`, `:authority`, `:scheme`, `:path`) for the HTTP/2 side.

That is one category out of roughly seven. Matching it removes one input to the score.
It does not remove the other six.

## The case that shows "matching fingerprint" is not "passing"

Here is what the GitHub issue actually contains, read directly, not summarized
from memory.

The reporter, running Camoufox v135.0.1-beta.24 through Playwright 1.58.0 on Linux, was
being blocked with a flat 403 by one specific Akamai-protected site, while the same
automated request pattern against the same site using stock Firefox through Playwright
returned a normal page and a solvable challenge. Before filing, the reporter had already
run the check this whole article leads with: they compared JA3, JA4, and Akamai's own
HTTP/2 signature between the two browsers and found them identical, and confirmed the
HTTP headers matched except for the User-Agent version string. Their own conclusion,
stated in the issue, was that this "means detection is NOT TLS/HTTP2," and that
"something in Camoufox's C++ patches is being detected" further up the stack.

The reporter also tested several other Akamai-protected sites in the same session: most
treated both browsers identically, either passing both or blocking both, which is itself
useful information, because it means the divergence was not a blanket Camoufox
signature, it was specific to one deployment's tuning.

Working the problem, the reporter ruled out a content-blocking browser extension,
changing the reported OS, enabling the page cache, and matching window sizes by hand,
none of which changed the outcome. They did find one concrete inconsistency worth
naming: Playwright reported a viewport of 1280x720, while the page's own JavaScript read
`window.innerWidth` as 1920. A real browser does not produce that split between what the
automation layer thinks the viewport is and what a script running inside the page reads
back. Whether that specific mismatch was the thing Akamai's sensor actually scored, the
issue does not settle. It is offered as a lead, not a proof.

The reporter also named four specific compiled patches as suspects, by filename:
`no-css-animations.patch`, `canvas-spoofing.patch`, `font-list-spoofing.patch`, and
`navigator-spoofing.patch`. Naming a patch as a suspect is not the same as demonstrating
it caused the block, and nobody in the thread went on to confirm which one, or whether
any one of them individually, was responsible.

What actually closed the issue was different from what anyone expected going in. Another
user suggested trying a newer Camoufox build, version 140 or higher from an unofficial
fork rather than the mainline release the reporter had been running. The reporter tried
it, confirmed the previously-blocked site and the others now loaded normally, reopened
the issue asking for the fix to land in the mainline branch, and a third participant
noted that the mainline had since moved to a much newer Firefox base version. The issue
is closed. No maintainer in the thread ever stated which mechanism, out of the four
named patches, the viewport inconsistency, or something never named at all, was actually
responsible. What is documented is that an older patched build was blocked on a network
fingerprint that matched a passing browser, and a newer patched build on the same engine
family was not.

## What this means if you patch a browser engine

Read narrowly, this is a story about one build lagging behind on one site. Read for the
lesson it actually demonstrates, it is broader: engine-level patching, compiling Firefox
or Chromium from source and setting the fingerprint in C++ rather than overriding it from
JavaScript, narrows the gap between an automated session and a real browser. It does not
automatically close it. The TLS and HTTP/2 layer read identical in this case, which is
exactly the layer that
[cannot be patched from a script and is usually the hardest thing to fake](ja3-ja4-tls-fingerprint.md),
and Akamai still told the two sessions apart. Whatever did the telling lived somewhere
else in that roughly-seven-category pipeline: the JavaScript-collected browser
fingerprint, the behavioral signal, or a reputation signal tied to the session's history
that had nothing to do with the browser at all.

That lesson generalizes to any project taking this approach, and it does not stop at the
project this issue was filed against. This project also compiles Firefox from source and
sets its fingerprint at the C++ level rather than in JavaScript, for
[the same reasons Camoufox does](vs-camoufox.md): an injected override is a function
whose source can be read, and a value decided below the JavaScript layer is not. That
shared decision is a real advantage over page-level spoofing, and it is not a guarantee
against a specific commercial detector's tuning on a specific deployment. This project
has not been tested against the site named in that issue, and even if it had, one clean
result would prove exactly as little as this build's years of matching TLS fingerprints
protected it from this block. The honest position is the one this whole page has been
building toward: a matching fingerprint at any single layer is necessary and not
sufficient, for any tool that takes this approach, including this one.

## Short answers to the questions that lead here

**What is the `_abck` cookie?** Akamai's own term for it is "mitigation state." It
represents the outcome of the sensor exchange and is checked on every later request
against that same session's history, not just evaluated once.

**Does a matching TLS/JA3/JA4 fingerprint mean I pass Akamai?** No. It means one input to
the score, out of roughly seven categories Akamai evaluates, agrees with a real browser.
The documented Camoufox case had a byte-identical network fingerprint and was still
blocked.

**What actually happened in the Camoufox GitHub issue?** An older patched build was
blocked by one Akamai-protected site while stock Firefox passed, despite matching
TLS/JA3/JA4/HTTP2 fingerprints. Switching to a newer patched build resolved it in
practice. No mechanism was confirmed in the thread.

**Does this mean invisible_playwright has the same problem?** There is no evidence either
way. The issue was filed against a different project, on a site this project has not
been tested against. What generalizes is the principle, not the specific outcome.

**What is Akamai's Bot Score?** A 0 to 100 number Akamai's own product page describes as
built from "all anomalies, starting with the very first request," derived from the
sensor payload plus network and reputation signals, not from any single check.

**Can any browser automation tool guarantee it beats Akamai Bot Manager?** No, and a tool
that claims it should be treated as making a promise it cannot verify either. This
project does not sell that claim about this or any other commercial anti-bot system.

**Where does most of Akamai's actual signal come from, if not the network layer?** From
the parts that are hardest to verify from outside: the JavaScript-collected browser and
device fingerprint, behavioral telemetry across the session, and reputation tied to the
IP and cookie history, none of which a single clean test can rule in or out.

**See also:** [Playwright stealth vs Camoufox: two patched Firefoxes](vs-camoufox.md),
for how this project and Camoufox compare on everything else; [JA3 and JA4: why a TLS
fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) and [HTTP/2 fingerprint: the
layer above the TLS handshake](http2-fingerprint-detection.md), for the two layers that
matched in the case above; and [How do websites detect bots?](how-do-websites-detect-bots.md),
for where a JavaScript-collected fingerprint sits among the other layers.

## Sources

- GitHub issue [`daijro/camoufox#555`](https://github.com/daijro/camoufox/issues/555),
  "Akamai-protected sites block Camoufox but not stock Firefox via Playwright," retrieved
  2026-08-30, for the test results, the matched network fingerprints, the suspected
  patches, the viewport inconsistency, and the resolution.
- Akamai's own documentation, [Detection
  methods](https://techdocs.akamai.com/cloud-security/docs/detection-methods) and [About
  bots](https://techdocs.akamai.com/cloud-security/docs/about-bots) on
  techdocs.akamai.com, retrieved 2026-08-30, for the five documented detection methods.
- Akamai's [Bot Manager product page](https://www.akamai.com/products/bot-manager),
  retrieved 2026-08-30, for the 0-100 Bot Score description.
- [Akamai Bot Manager: Understanding `_abck` Cookies and Sensor
  Data](https://scrapfly.io/blog/posts/akamai-bot-manager-understanding-abck-cookies-and-sensor-data),
  Scrapfly, retrieved 2026-08-30, for the sensor signal categories, the `_abck` cookie's
  documented failure modes, and the seven-category scoring breakdown.
- [`Edioff/akamai-analysis`](https://github.com/Edioff/akamai-analysis), GitHub, retrieved
  2026-08-30, a reverse-engineering write-up of Bot Manager's client-side sensor script,
  for the signal count, the pipeline steps, and the obfuscation details.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The Camoufox issue above is someone else's bug
tracker and someone else's engine build; it is quoted here because it is the clearest
public evidence that this whole approach narrows a gap rather than closing one, and that
applies to this project exactly as much as it applies to theirs.*
