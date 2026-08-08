---
title: "pydoll vs invisible_playwright: CDP without a driver"
description: "pydoll drives Chrome over CDP without chromedriver; invisible_playwright ships patched Firefox whose seeded canvas, WebGL and fonts stay mutually consistent."
parent: "Comparisons"
nav_order: 24
---


# pydoll vs invisible_playwright: CDP without a driver

pydoll is an actively developed async Python library that automates Chrome by
speaking the Chrome DevTools Protocol directly, with no chromedriver or WebDriver
binary in the loop. Removing that binary removes a whole category of classic driver
tells at their source. This project takes a different route: a Firefox patched at
the C++ level, driven by stock Playwright, where every session's canvas, WebGL
renderer and font metrics are derived from one seed and kept mutually consistent.

The two tools sit at different layers of the same problem, and the honest short
version is that each fixes something the other does not, while neither fixes the
part that lives outside the browser. This page is about which layer you are actually
short on.

## Two answers to the same tell, at different layers

A browser gives itself away in more than one place at once. The driver layer is one
of them: the plumbing that connects your script to the browser can leave marks that
a page can read. The fingerprint layer is another: the values the browser reports
for its GPU, its canvas output, its fonts and its audio pipeline, and whether those
values agree with each other and with the platform you claim to be.

pydoll works at the driver layer. It removes the intermediary binary and talks CDP
straight to Chrome, so the tells that came from that binary are simply not there to
find. That is a real and durable fix for a real class of problem.

invisible_playwright works at the fingerprint layer. The engine is a genuine Firefox
build, so its TLS handshake and JavaScript engine already read as Firefox, and the
values it reports are generated together from a seed so they do not contradict one
another. Different layer, different failure it addresses.

Neither of these is a superset of the other, which is the whole reason a comparison
is worth writing instead of just picking the newer tool.

## What pydoll removes: the driver, by speaking CDP directly

The classic Selenium stack drives Chrome through a separate `chromedriver` process,
and that process historically left literal marks in the page, including a global
variable injected by the driver. That is [the same family of tell we describe in the
ChromeDriver cdc variable, explained](cdc-variable-explained.md), and it generalises:
a mark you rename is still a mark if what gets checked is that something is there at
all, not what it is called.

pydoll's answer, by its own documentation, is to drop the driver binary entirely and
connect to Chrome over CDP. With no intermediary process there is no injected
variable to rename, no extra process in the tree, and no separate port signature to
explain away. It is the same architectural move that [nodriver makes on the same
lineage](vs-nodriver.md), applied through an async Python API of pydoll's own rather
than through Playwright.

What this buys you is a clean automation layer. The `navigator.webdriver` flag and
its neighbours are [mostly solved and mostly not your problem in
2026](navigator-webdriver-explained.md) precisely because tools like this one remove
them at the root. What it does not touch is the fingerprint layer below it.

## What a seeded Firefox carries: a fingerprint that agrees with itself

pydoll is Chrome-based and does not spoof the rendering-layer fingerprint. That is
not a criticism, it is a scope statement: the GPU string Chrome reports, the canvas
it draws, the fonts it enumerates and the audio pipeline it exposes are whatever the
underlying machine produces. On a real desktop with a real GPU that is fine. On a
headless server it is often exactly what gives the session away, because a software
renderer or an empty font list says "datacenter" no matter how clean the driver
layer is.

invisible_playwright generates those surfaces together from one seed. The canvas
hash, the WebGL renderer string and the font metrics are drawn as one coherent
identity, so they agree with each other rather than each being individually
plausible and jointly contradictory. That mutual consistency is what a tampering
check like CreepJS actually grades, and it is a harder property to fake one field at
a time than it looks.

One subtlety worth naming, because it is the most common way a spoof at this layer
still fails: a renderer string can claim a real GPU while the pixels are drawn by a
software rasterizer, which is [a mismatch you cannot patch away with a string
edit](renderer-string-vs-render.md). Keeping the reported name and the actual draw
path consistent is part of what "coherent" has to mean here.

Because it is all derived from a seed, the identity is reproducible:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # same GPU string, same canvas hash, same font metrics on every run
```

Pass the same seed tomorrow and you get the same machine, which is what makes a
failing run reproducible instead of a guess.

## The honest caveat: neither approach touches your IP or your behaviour

This is the part that gets skipped, so it goes in its own section.

Removing the driver tells does not fix your IP reputation. Spoofing the fingerprint
does not fix it either. A coherent Firefox on a datacenter address is still on a
datacenter address, and a clean CDP Chrome from the same range is in the same spot.
Neither tool changes the exit you come from.

Neither one changes your behaviour, either. Per-account quotas, rate limits, the
pace of your requests and the shape of your pointer motion are all still yours to
supply. A session that fills a form in eighty milliseconds and never moves the mouse
reads as automation whatever engine drew the page.

invisible_playwright is designed to look like a real browser driven by a real
person, and that is why it passes most in-page detection checks: the fingerprint,
the TLS handshake and the driver layer all read as a genuine Firefox. It passes
those checks. It does not, on its own, fix IP reputation, quotas, rate limits or
timing. You supply those, with a clean exit and human pacing, and the working order
for diagnosing which one is biting you is the
[checklist for being detected on one site](playwright-detected-as-bot.md).

## Using invisible_playwright, and how to think about pydoll alongside it

Switching from stock Playwright is a two-line change, and the returned `browser` is
a real Playwright `Browser`, so every method you already use works unchanged:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

```bash
pip install invisible-playwright
```

If your project is committed to Chrome and to an async Python API of its own, and
your machines have real GPUs so the fingerprint layer is not your problem, pydoll's
driverless CDP approach is a clean fit. If your problem is that the fingerprint layer
gives you away on headless infrastructure, or you already have Playwright code you do
not want to rewrite, an engine that carries a coherent seeded identity is the layer
you are short on. Some people run a CDP-native driver and a separate fingerprint
solution together for exactly this reason; here the engine is the fingerprint
solution.

## How to choose

- **Driver tells are your problem and you are on Chrome with real hardware?** pydoll
  removes the driver binary cleanly and gives you an async CDP API.
- **Fingerprint layer gives you away on servers?** A seeded Firefox keeps canvas,
  WebGL and fonts mutually consistent, which is the property a tampering check grades.
- **Have existing Playwright code?** invisible_playwright is a two-line swap; pydoll
  is a different API to adopt.
- **Need Firefox specifically, or a Firefox TLS handshake?** pydoll is Chrome-based;
  this project is a real Firefox build.
- **Blocked despite a clean browser?** Look at the exit and the behaviour next,
  because neither tool changes those.

## Conclusion

pydoll and invisible_playwright are not the same bet. pydoll removes the driver
layer by speaking CDP with no binary, which is a genuine fix for the tells that
binary left behind. invisible_playwright ships a real Firefox whose fingerprint is
generated coherently from a seed, which is a fix for a layer pydoll does not claim to
touch. Pick the one that matches the layer you are actually failing at, and remember
that both leave your IP reputation and your behaviour exactly where they were.

## Short answers to the questions that lead here

**Is pydoll undetectable?** No tool is, and pydoll does not claim to be. It removes
the driver-layer tells by dropping the chromedriver binary and speaking CDP directly,
which helps with one category and leaves the fingerprint, IP and behaviour layers to
you.

**Does pydoll spoof the browser fingerprint?** No. It is Chrome-based and reports
whatever the underlying machine produces for GPU, canvas, fonts and audio. That is
fine on real hardware and often the tell on a headless server.

**What does invisible_playwright do that pydoll does not?** It generates canvas,
WebGL renderer and font metrics together from one seed so they stay mutually
consistent, and it runs on a real Firefox whose TLS handshake reads as Firefox.

**Will either one fix my IP getting blocked?** No. Neither changes your exit
address. A clean browser on a datacenter IP is still on a datacenter IP, so pair
either tool with a clean proxy.

**Can I keep my existing Playwright code?** With invisible_playwright, yes, it is a
two-line launch change. pydoll has its own async API, so adopting it means rewriting
the automation layer.

**Is pydoll maintained?** Yes, it is an actively developed async library at the time
of writing; check its own repository for the current state before you depend on it.

## Sources

- pydoll's own repository documentation, for its async CDP-direct architecture, its
  lack of a chromedriver or WebDriver binary, and its own API surface. Read from the
  project's own source rather than its rendered summary.
- This project's own [cdc-variable-explained.md](cdc-variable-explained.md) and
  [renderer-string-vs-render.md](renderer-string-vs-render.md) for the driver-tell
  and fingerprint-coherence mechanisms described above.
- The [how-to-test-bot-detection](how-to-test-bot-detection.md) notes for what an
  in-page suite can and cannot see, which is why the IP and behaviour caveats are
  stated separately here.

**See also:** [invisible_playwright vs nodriver and undetected-chromedriver](vs-nodriver.md)
for the same driverless-CDP idea on the Selenium lineage;
[the checklist for being detected on one site](playwright-detected-as-bot.md) for the
order to diagnose in; and [how to test whether your browser is
detected](how-to-test-bot-detection.md) for reading suites as evidence rather than
verdicts.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. pydoll solves the
driver layer honestly and says what it does not cover; this project works the
fingerprint layer, and neither of us touches your IP or your pacing.*
