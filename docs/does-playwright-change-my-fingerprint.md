---
title: "Does Playwright Change My Browser Fingerprint?"
description: "Stock Playwright does not randomize your fingerprint - it inherits the host browser's real values, which is how a Linux server leaks past a spoofed Windows user agent."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 23
---


# Does Playwright Change My Browser Fingerprint?

Short version: no. Stock Playwright launches a browser and hands you whatever that
browser reports on the machine it runs on. It does not randomize the fingerprint, it
does not repair it, and it does not make it look like anything other than what it is.
That surprises people who expected the automation layer to also be a disguise layer,
and the surprise usually arrives as a block on a server that worked fine on a laptop.

This page is what Playwright does and does not touch, the specific trap that catches
server deployments, and where a seed-derived identity replaces the values you inherit.

## What Playwright actually does to the fingerprint

Playwright drives a browser. It does not synthesize one. When you call
[`p.firefox.launch()`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch),
the fingerprint you present is the real fingerprint of the real
Firefox on the real host: its GPU string comes from the host's graphics stack, its
font list from the host's installed fonts, its `navigator.platform` from the host OS,
its screen metrics from the host display, its audio characteristics from the host
audio device.

Playwright's job is the driver channel and the automation surface. It exposes a few
[context knobs](https://playwright.dev/python/docs/api/class-browser#browser-new-context) -
`user_agent`, `locale`, `timezone_id`, `viewport` - but those are
overrides you type in, not a coherent identity. Set a `user_agent` and you have
changed one string; every other value the page can read still comes from the machine.
That is the gap this whole page is about.

So the honest answer to the title is: Playwright inherits, it does not invent. If you
want a different machine, you have to supply one.

## The server-OS mismatch trap

Here is the exact failure, because it is the one that ships.

You develop on a Windows or Mac laptop, everything passes, you deploy to a Linux
server. Now the browser reports Linux fonts, a datacenter or software GPU renderer,
and a Linux `navigator.platform`. Then, because a lot of guides say to, you set a
Windows `user_agent` to "look normal".

You have just built a contradiction. A consistency detector does not care that any
single value is plausible. It cares whether values that must agree, do.
[CreepJS is built specifically to catch that](creepjs-explained.md): a user agent
claiming Windows, sitting on top of a font set that only exists on Linux, next to a
GPU string that only exists in a datacenter. Each field is fine alone. Together they
describe a machine that cannot exist, and that is a cleaner signal than any single bad
value would have been.

None of these are automation flags. They are "this is a Linux server wearing a Windows
costume" flags, and [they survive every JavaScript stealth plugin ever written](playwright-docker-detection.md)
because the fonts, the GPU and the platform are not in JavaScript's gift to change.
The [font list mismatch alone](headless-fonts-differ.md) is a one-line check on the
detector's side.

## What a seed-derived identity changes

invisible_playwright replaces the inherited values with a generated identity, and the
word that matters is *coherent*. From one seed it derives every surface together -
fonts, screen, device pixel ratio, canvas, WebGL vendor and renderer, audio, timezone,
platform - so they describe a single believable Windows machine instead of the host it
actually runs on. The Linux server underneath stops showing through, because the page
never reads the Linux values.

Two properties follow from deriving everything from one seed:

- **Consistency.** The user agent, the fonts, the GPU string and the platform are drawn
  as one machine, so a cross-field detector finds agreement instead of a costume. This
  is the same principle as
  [keeping canvas and WebGL consistent across platforms](canvas-webgl-cross-platform-consistency.md):
  the surfaces have to corroborate each other, not just each look normal in isolation.
- **Reproducibility.** The same seed yields the same identity every run. A
  [FingerprintJS visitor ID](fingerprintjs-visitor-id.md) is a hash over many
  components; feed it the same seed twice and you get the same ID, which is what lets
  you replay a failing run exactly instead of hoping the next random draw reproduces it.

The default (no seed) still gives every session a distinct coherent identity. Passing a
seed just pins which one.

## A runnable example

Switching from stock Playwright is a two-line change, and after that every standard
Playwright method works unchanged, because the object you get back is a real Playwright
`Browser`.

```python
from invisible_playwright import InvisiblePlaywright

# same seed -> same coherent Windows identity, every run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # navigator.platform, fonts, GPU, canvas, timezone all describe
    # ONE machine - not the Linux host this happens to run on
    print(page.evaluate("navigator.platform"))
```

Run that on your laptop and on a Linux server and the reported platform, fonts and GPU
match, because they come from the seed and not from the host. Log the seed if you did
not pass one, and you can reproduce the run later:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)   # replay this exact machine later
    page = browser.new_page()
    page.goto("https://example.com")
```

Async is the same shape with `from invisible_playwright.async_api import InvisiblePlaywright`
and `await` on the page calls. There is no wrapped subset of the API to learn;
see [the quickstart](quickstart.md) for the sync-versus-async pair side by side.

## The honest caveat: fingerprint is not everything

A coherent fingerprint fixes exactly one class of problem: the browser now reads as a
genuine Firefox on a consistent Windows machine, at the fingerprint, TLS and driver
layers. That is why it passes most detection checks that inspect what the browser *is*.

It does not fix what the browser *does*, and it does not fix where it connects from.
A perfect, seed-stable Windows identity coming out of a datacenter IP that a thousand
other clients are using this minute is still a datacenter IP. A perfect identity that
fills a form in eighty milliseconds and clicks without the pointer ever passing through
the space between two coordinates is still behaving like a script. The fingerprint is
not a disguise for a bad exit or robotic timing, and anyone who tells you a browser
alone makes you "undetectable" is selling the overclaim.

You supply the other two layers: a clean exit (see the
[proxy and timezone notes in configuration](configuration.md), where the browser
timezone auto-derives from the egress IP so those two stop contradicting each other),
and human pacing. The tool makes the machine coherent. The IP and the behaviour are
yours to get right.

## Conclusion

Playwright does not change your fingerprint - it inherits the host's, which is exactly
why a laptop-tested script fails on a Linux server the moment you paint a Windows user
agent over a Linux machine. A seed-derived identity closes that gap by generating every
surface together, so the fields corroborate one another instead of exposing the host,
and the same seed reproduces the same machine for debugging. Pair it with a clean exit
and human timing and it does the job it is built for. Treat it as a substitute for
those and it will not, and no honest tool claims otherwise.

## Short answers to the questions that lead here

**Does Playwright randomize the fingerprint?** No. It launches a browser and you
inherit that browser's real values on the host it runs on.

**Why does my scraper work locally but get blocked on a server?** Because the server
reports Linux fonts, a datacenter GPU and a Linux platform, which contradict the Windows
user agent you set. Consistency detectors read that mismatch directly.

**Does setting `user_agent` in Playwright change my fingerprint?** It changes one
string. The fonts, GPU, platform, screen and audio still come from the machine, so a
lone user-agent override usually creates a contradiction rather than hiding anything.

**What does a seed do?** It derives one coherent identity - fonts, screen, canvas,
WebGL, timezone - all describing the same Windows machine, and the same seed reproduces
it every run.

**Will a coherent fingerprint make me undetectable?** No. It helps with the fingerprint,
TLS and driver layers. It does nothing for a bad IP, per-account limits, rate limits or
robotic timing, and you have to supply those yourself.

**Is invisible_playwright still real Playwright?** Yes. The `browser` object is a real
Playwright `Browser`; every documented method works unchanged.

## Sources

- Playwright's own [launch](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch)
  and [context](https://playwright.dev/python/docs/api/class-browser#browser-new-context) API,
  read for what it does and does not override.
- This project's fingerprint generator, which derives every surface from one seed, and
  the release gates that compare a generated identity field by field against a stock
  Firefox on the same machine.
- The public consistency detectors named across this doc set, read from their own source
  rather than from a rendered verdict.

**See also:** [what CreepJS actually checks](creepjs-explained.md),
[why a container gives itself away regardless of the stealth layer](playwright-docker-detection.md),
and [keeping canvas and WebGL consistent across platforms](canvas-webgl-cross-platform-consistency.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The server-OS mismatch in
this page is a mistake I shipped before I built the thing that fixes it.*
