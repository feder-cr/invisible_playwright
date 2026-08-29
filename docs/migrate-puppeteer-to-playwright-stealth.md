---
title: "Migrating from Puppeteer to Playwright for stealth"
description: "Migrate from Puppeteer to Playwright: API mapping one-to-one, and the one thing that matters for bot detection - off Chromium CDP and onto a patched Firefox."
parent: "Comparisons"
nav_order: 28
---

# Migrating from Puppeteer to Playwright for stealth

If you are moving off Puppeteer because your sessions get flagged, it helps to be
precise about what the move buys you. Most of the migration is a boring API rename:
`page.goto`, `page.$`, `waitForSelector` all have close equivalents and the mental
model barely changes. The part that matters for detection is not in the API at all.
It is which engine you end up driving, and over which protocol.

This page covers both: the near one-to-one API mapping, and the single change that
actually moves the needle on how automated your browser looks.

## What Puppeteer's architecture fixes into place

Puppeteer is built around one engine and one protocol. It drives Chromium or Chrome
over the Chrome DevTools Protocol (CDP). That has two consequences that no amount of
page-level patching removes.

First, the fingerprint is always a Chromium fingerprint. Canvas, WebGL, the audio
stack, the font metrics, the JavaScript engine quirks: all of them read as Chromium
because they are Chromium. You can rewrite `navigator` properties from a script, but
you cannot rewrite what the rendering pipeline actually produces. That is
[the difference between what a browser claims and what it renders](renderer-string-vs-render.md),
and it is why a Chromium build spoofing a different browser tends to contradict itself
under a careful check.

Second, attaching over CDP is itself observable. Enabling the runtime domain that
Puppeteer needs leaves a trace a page can probe for, which is one of the
[automation tells detectors look for](navigator-webdriver-explained.md) rather than a
fingerprint value you can edit. It is a property of how the driver connects, not of
what you set.

Puppeteer has added stable Firefox support through WebDriver BiDi, so "Puppeteer
is Chromium only" is no longer strictly true. But the mature, default path is still
Chromium over CDP, and neither path hands you a patched engine. You get whichever
stock browser you launch, with its stock fingerprint.

## The API maps almost one to one

Playwright and Puppeteer grew from the same lineage, so the migration is mostly
find-and-replace. The concepts line up:

| Puppeteer | Playwright |
|---|---|
| `page.goto(url)` | `page.goto(url)` |
| `page.$(selector)` | `page.query_selector(selector)` / `page.locator(...)` |
| `page.waitForSelector(sel)` | `page.wait_for_selector(sel)` |
| `page.click(sel)` | `page.click(sel)` |
| `page.type(sel, text)` | `page.type(sel, text)` / `locator.fill(...)` |
| `browser.newPage()` | `browser.new_page()` |

The names shift from camelCase to snake_case in Python, and Playwright leans on
locators and auto-waiting where Puppeteer leans on explicit waits, but the operation
you were performing has a direct equivalent. Nothing about the task changes. What
changes is that Playwright can target
[Firefox as a first-class engine](https://playwright.dev/python/docs/browsers), not a
partial one, which is the door the rest of this page walks through. For the
broader engine comparison see
[Firefox versus Chromium for anti-detect work](firefox-vs-chromium-antidetect.md).

## What actually changes for detection when you swap

Here is the honest version of the value. Swapping the framework, on its own, changes
almost nothing: stock Playwright driving stock Firefox is still a stock browser, and
[Chromium is not Chrome either](chromium-is-not-chrome.md), so plain Playwright over
Chromium inherits the same class of tell.

The thing that moves the needle is the engine underneath. invisible_playwright drives
a Firefox that has been patched at the C++ level so that its canvas, WebGL and font
fingerprint read as a genuine Windows Firefox, driven over Firefox's own automation
protocol rather than over CDP. That combination is what changes the answer to three
questions a detector asks:

- Which engine is this, really. The fingerprint is a real Firefox one, produced by the
  rendering pipeline, not asserted by a script over a Chromium base.
- Is the fingerprint internally consistent. Because the values come from one seed and
  are cross-checked against each other, they agree rather than contradict.
- Is a driver attached in a way I can detect. The connection does not use the CDP
  runtime attach that Puppeteer relies on.

That is the demonstration, not a slogan: the fingerprint, the TLS handshake and the
driver layer read as a genuine Firefox because the browser is a genuine Firefox that
has been modified, rather than a stock engine wearing a script. It is why the setup
passes most fingerprint and driver-layer checks. It is not why a whole session
succeeds, which is the next section.

## A runnable example

The launch is two lines, and everything after it is the Playwright API you already
know. Here is the migration in practice: open a page, wait for an element, read it.

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the fingerprint reproducible: same GPU, same canvas hash,
# same fonts on every run, so a failing run can be replayed exactly.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.wait_for_selector("h1")
    heading = page.query_selector("h1")
    print(heading.inner_text())
```

The `browser` object is a real Playwright `Browser`. Every documented Playwright
method works on it unchanged, which is the whole point: you are not learning a wrapped
subset, you are driving Playwright against a patched engine. Behind a proxy, add the
proxy dict and let the timezone follow the exit:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

## What the swap does not fix

This is the caveat that keeps the rest honest, and it is not optional reading.

Moving from Puppeteer to a patched Firefox changes the engine identity. It does not
change your behaviour, your pacing, or your network. Specifically, it does nothing for:

- IP reputation. A genuine-looking browser on a datacenter address that a thousand
  other automated sessions share is still on a bad address. You supply a clean proxy;
  the browser cannot.
- Per-account quotas and rate limits. These are counted server-side against your
  account and your requests, and no fingerprint hides a request that was made.
- Behaviour and timing. A pointer that teleports, a form filled in eighty
  milliseconds, keystrokes at a perfectly uniform interval. You supply human pacing.
- The TLS and HTTP/2 layers as a story that has to agree with everything else, which
  [an in-page test cannot even see](ja3-ja4-tls-fingerprint.md).

The honest framing is that the swap fixes the engine-identity and driver-attach layer,
which is a real and common failure, and leaves the network and behaviour layers to you.
When something still gets flagged after the migration, the
[checklist for being detected on one site](playwright-detected-as-bot.md) is the order
to work through, and it is usually not the browser anymore. Deciding how many disguise
layers to run at once is its own decision: see
[choosing a single stealth layer](playwright-stealth-levels.md), because running a
patched engine and a page-level plugin together produces contradictions neither
produces alone.

## Conclusion

The migration itself is easy: the API maps almost one to one, and the operation you
were performing has a direct equivalent. The reason to do it for stealth is narrower
and worth stating plainly. Puppeteer pins you to a Chromium fingerprint driven over a
detectable CDP attach; Playwright lets you target Firefox, and invisible_playwright
makes that Firefox a patched build whose fingerprint and driver layer read as genuine.
That helps with the engine and driver layer, which is where a lot of detection lives.
It does not help with your IP, your quotas, or your behaviour, which is where the rest
of it lives, and which you still have to bring.

## Short answers to the questions that lead here

**Does moving from Puppeteer to Playwright make me undetectable?** No, and nothing
does. It changes the engine identity and the driver-attach layer, which are common
failure points, and leaves your IP, quotas and behaviour exactly where they were.

**Why is Puppeteer's fingerprint always a Chromium one?** Because Puppeteer drives
Chromium or Chrome. The fingerprint is produced by that rendering pipeline, so a script
can relabel properties but cannot change what the pipeline actually draws.

**Can Puppeteer drive Firefox?** It has stable Firefox support through WebDriver
BiDi, but the default and mature path is Chromium over CDP, and neither path gives you
a patched engine.

**Is the CDP connection itself detectable?** The runtime attach Puppeteer relies on
leaves an observable trace a page can probe for. That is a property of how the driver
connects, separate from any fingerprint value you set.

**How much of my code has to change?** The launch, and camelCase to snake_case. After
that the `browser` object is a real Playwright `Browser` with every documented method,
so `goto`, `query_selector` and `wait_for_selector` work as you expect.

**If the fingerprint reads as real, why do I still get blocked sometimes?** Because a
real-looking browser on a flagged IP, or behind human-implausible timing, still loses.
Fingerprint is one layer; network and behaviour are separate layers you supply.

## Sources

- Puppeteer documentation, [Supported browsers](https://pptr.dev/supported-browsers) and
  [WebDriver BiDi support](https://pptr.dev/webdriver-bidi), read for the Chromium-over-CDP
  default and the stable Firefox path, retrieved 2026-08-28.
- This project's fingerprint and driver-layer gates, which compare a patched Firefox
  field by field against a stock browser on the same machine, described in
  [how to test bot detection without a false pass](how-to-test-bot-detection.md).

**See also:** [Playwright versus a real-browser Puppeteer wrapper](vs-puppeteer-real-browser.md),
[why Chromium is not Chrome](chromium-is-not-chrome.md), and
[Firefox versus Chromium for anti-detect work](firefox-vs-chromium-antidetect.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The migration is the
easy part; the honest part is that it fixes the engine, not the network or the person.*
