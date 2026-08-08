---
title: "How to test bot detection without a false pass"
description: "What each public bot detection suite actually proves, why a passing verdict can hide a broken feature, and the method that catches what a verdict alone misses."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 1
---


# How to test bot detection without a false pass

Test bot detection by comparing your automated browser against a stock browser on the
same machine, field by field, run at least ten times through the same proxy production
uses - and treat any suppressed or empty signal as a failure, not a pass. A single
suite's verdict cannot tell a working feature from a broken one.

Most people test this by opening a suite, reading the verdict, and stopping. That method
has a specific failure mode: it cannot tell "working correctly" from "not working at
all", and it will happily report success on a browser that is broken in the exact way you
were trying to avoid.

This page is what each public suite proves, the false pass that method produces, the
comparison that replaces the verdict, where and how many times to run it, and what none
of these tools cover.

## What each suite actually proves

Five public tools, each answering a different question. Using them interchangeably is
most of why people get confused by contradictory results.

| Suite | Question it actually asks | What a pass proves |
|---|---|---|
| sannysoft | Are you an unmodified, old headless browser? | Table stakes only - nothing about a modern one |
| CreepJS | Are you lying about your environment? | Nothing here contradicts anything else here |
| BotD | Which engine are you really running? | A verdict, not a fingerprint |
| FingerprintJS | Can you be recognised again? | Linkability, not "looks human" |
| BrowserLeaks | What does one specific surface report? | A single value, not a score |

**[bot.sannysoft.com](sannysoft-explained.md)** is a smoke test. Its main table
describes headless Chrome as it behaved around 2018, and every serious tool fixes
those on day one. Passing it proves you are not running an unmodified headless browser.
The interesting part is not the table: three canvas tests are also run inside an iframe
and compared, which is a consistency check rather than a fingerprint check.

**[CreepJS](creepjs-explained.md)** asks whether you are lying, not what you report. It
takes a clean copy of the built-ins from a fresh iframe, inspects stack traces, walks
descriptors and prototypes, and records a blocked probe as a lie by name. A high score
means nothing here contradicts anything else here.

**[BotD](botd-explained.md)** returns a verdict rather than a fingerprint, and most of its
twenty detectors are really asking which engine you are, by testing behaviours that
differ between engines.

**[FingerprintJS](fingerprintjs-visitor-id.md)** gives you a visitor ID, which is a hash
of roughly forty-one components. It answers "can I be recognised again", which is a
different question from "do I look automated". The commercial version adds signals the
open-source library does not have.

**[BrowserLeaks](browserleaks-explained.md)** is per-surface rather than a verdict:
WebRTC, canvas, WebGL, fonts. It is the one to reach for when you want to read a
specific value rather than a score.

None of these is a superset of the others, and a green result on one says nothing about
the rest.

## The false pass, which is the reason for this page

A verdict-based test cannot distinguish a working feature from an absent one.

We learned this the expensive way. Our [WebRTC gate](webrtc-leak-proxy.md) asserted the
sensible things: the host candidate does not expose the LAN address, no IPv6 candidate
appears. Both passed, run after run. Behind a proxy on real pages, WebRTC was returning
**nothing at all**, and the gate passed because a dead feature leaks nothing.

Every negative assertion is trivially satisfied by a feature that does not run.

So the rule, which applies to every surface and not just that one:

> **Assert the presence of the right signal, not the absence of a wrong one.** A page that
> comes back empty, blocked or still loading is a failure, not a pass.

Concretely: the WebRTC section must complete and show a host candidate and a server
reflexive one. The canvas must produce a hash, twice, matching. The font list must be
non-empty and belong to the platform you claim. A suppressed signal is itself a signal,
and [CreepJS records blocking by name](creepjs-explained.md).

The same trap shows up one level down, inside a test itself rather than in what it
tests. [A cleanup-identification check once passed for exactly this reason](orphaned-browser-process-windows.md) -
the input it claimed to reject never actually reached the code path being checked, so
removing the guard entirely still left it green.

## Compare, do not read verdicts

The single highest-value change to how you test bot detection is direct comparison, not
a verdict.

Open the same page in your automated browser and in a **stock browser on the same
machine**, and diff the two reports field by field. Not the scores, the fields.

What that catches which a verdict does not:

- Values that are individually plausible and disagree with each other.
- Values you fixed that now differ from a real browser in the other direction, which is
  what happens when a spoof overcorrects.
- Everything about the machine, which no verdict separates from everything about the
  automation.

The stock browser is the reference. Anything that differs between the two, other than the
address, is a candidate. Anything that matches is not your problem, whatever the score
says.

### Separate the machine from the automation

When something differs, ask which of the two it belongs to:

- Automation tells are things like `navigator.webdriver`, leftover globals, an untrusted
  event. [Which are mostly solved and mostly not your problem](navigator-webdriver-explained.md).
- Machine tells are the GPU, the fonts, the audio device, the screen, the codecs.
  [None of which any stealth layer touches](playwright-docker-detection.md).

They need completely different fixes, and most container failures are the second kind
while most people debug the first.

## Test where you deploy, through the proxy you deploy with

Three ways a test can be true and useless.

**Testing on your laptop.** Your laptop has a GPU, fonts, an audio device and a real
screen. The server has none of those. A test that passes at home tells you about home.

**Testing on localhost.** Localhost bypasses the proxy, so you have measured the path you
do not use. Run the page through the same proxy the job uses, and confirm the exit
address inside the browser rather than assuming it.

**Testing the wrong browser version.** If your tool pins one version and you tested
another, you tested something you do not ship.

The general form: feed the test the same inputs production gets. A test that does not is
not evidence.

## Run it more than once

This domain is not deterministic, and single runs mislead in both directions.

- **Repeat at least ten times.** A verdict that appears once in ten is a verdict, and a
  single green run is not a pass.
- **Read the same value twice in one session.** Canvas, audio and WebGL hashes must
  match. If they do not, something is randomising per call, which is
  [the cheapest tampering check there is](canvas-fingerprint-noise.md).
- **Relaunch the same identity and compare.** Values that should be stable across
  sessions must be.
- **Space the runs out.** Hammering one scoring endpoint from one address creates the
  velocity signal you are trying to measure. We flagged our own product for this once,
  and the flag belonged to the test harness.

### Read the screenshot, not the log

A text log tells you what your code extracted. A screenshot tells you what the page
actually rendered, including the parts your extractor did not know to look at. Several
findings in these notes came from opening a PNG after the log said everything was fine.

## What none of these tools cover

Worth stating so a clean sweep is not mistaken for a clean session.

- **The TLS handshake**, which is decided before any page loads.
  [And which no in-page test can see](ja3-ja4-tls-fingerprint.md).
- **Behaviour**, including pointer motion, typing rhythm and, for agents,
  [the pause shaped like model latency](ai-browser-agents-stealth.md).
- **IP reputation**, which is not a browser property at all.
- **What a commercial system does with all of it together**, which is a model rather than
  a checklist.

A perfect score on every public suite means your browser is internally consistent and
does not announce automation. It does not mean the session passes.

## Conclusion

Testing well is mostly a method rather than a tool. Assert what should be present rather
than what should be absent, compare against a stock browser instead of reading a verdict,
run it where you deploy and through the proxy you deploy with, repeat it, and open the
screenshot.

Do that and the public suites become useful instruments. Read them as scores and they
will eventually tell you that a broken browser is fine.

## Short answers to the questions that lead here

**Which bot detection test should I use?** Several, because they answer different
questions. sannysoft as a smoke test, CreepJS for tampering, BotD for a verdict,
FingerprintJS for linkability, BrowserLeaks to read a specific surface.

**I pass every test and still get blocked. Why?** Because the suites do not see the TLS
handshake, your behaviour, or your address, and because a consistent browser on a
datacenter IP is still on a datacenter IP.

**Is passing sannysoft enough?** It proves you are not running an unmodified headless
browser from around 2018.

**Why do I get different results on different runs?** Because the domain is
non-deterministic. That is why ten runs, not one.

**Should I test on my laptop?** Only to find bugs. To find out what production looks
like, test on the machine that runs production.

**What does a blank or stuck section mean?** A failure. An empty result is not a clean
result, and several checks record suppression explicitly.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
which is the order to work in once a test tells you something, and the per-suite pages
linked throughout.

## Sources

- The five public suites named above, each with its own page in this set, read from their
  own source rather than from their rendered output.
- This project's release gates, including the WebRTC gate whose negative-only assertions
  produced the false pass described above, and the velocity flag that turned out to be
  the harness.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Every rule on this page exists because breaking it
cost us something first.*
