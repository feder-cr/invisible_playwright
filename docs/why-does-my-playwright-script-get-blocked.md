---
title: "Why Does My Playwright Script Get Blocked?"
description: "A four-layer diagnostic for blocked Playwright - browser fingerprint, IP reputation, rate/quota, behavior - and which a stealth browser fixes, which stay yours."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 9
---


# Why Does My Playwright Script Get Blocked?

Because "blocked" is not one thing. A block is a decision made by combining several
independent signals, and the frustrating part is that fixing the loudest one does
nothing if the block came from a different one. People rewrite their fingerprint for a
week and stay blocked because the problem was the address the whole time.

There are four layers, and they fail independently. This page is how to tell which one
blocked you, because the fix for each is different and doing the wrong one costs days.
The honest headline up front: a real-browser engine like this one fixes the first layer
cleanly and does not fix the other three for you. Knowing which layer you are on is the
whole game.

## The four layers, and why fixing one does not fix the others

1. **Browser fingerprint.** What your browser reports about itself: the engine, the GPU,
   the fonts, the screen, the canvas and audio hashes, whether
   [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
   is set. Stock Playwright's default fingerprint can be internally inconsistent - a value
   claiming one operating system next to a value that belongs to another - and a detector
   scores the contradiction, not the individual values.
2. **IP reputation.** Your exit address, scored before your browser sends a single byte.
   Datacenter ranges and heavily shared proxy pools are pre-scored low. This is not a
   browser property and no browser setting reaches it.
3. **Rate and quota.** How much you do, per account and per minute. Too many requests,
   too many actions, too many sessions from one identity, and you are throttled or
   challenged regardless of how real each individual request looked.
4. **Behaviour.** The timing and shape of what you do. Instant form fills, uniform
   keystroke intervals, a pointer that teleports, a page never scrolled, a pause shaped
   exactly like model latency.

These are ANDed together. A perfect fingerprint on a flagged IP still loses. A clean
residential IP with `navigator.webdriver` still exposed still loses. This is why a single
fix so often changes nothing: you improved a layer that was already passing.

## What a real-browser engine fixes: layer 1

invisible_playwright addresses the first layer directly, and only the first. It drives a
Firefox patched at the C++ level, so the engine, the TLS handshake and the driver surface
read as a genuine Firefox rather than an automated one, and every reported surface is
derived together from one seed so the values agree with each other and with the operating
system they claim. The switch from stock Playwright is two lines, and every Playwright
method you already use keeps working:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

The `browser` object is a real Playwright `Browser`, so `new_page`, `goto`, `click`,
`fill` and the rest behave exactly as
[documented upstream](https://playwright.dev/python/docs/api/class-page). Passing `seed=42` makes the
identity reproducible: the same seed yields the same GPU, the same canvas hash, the same
fonts every run, which is what turns a flaky "sometimes blocked" failure into one you can
replay and bisect. That is the mechanism behind why the browser-check layer passes -
consistency, not any single magic value. If you want to see it measured rather than
asserted, [how to test whether your browser is detected](how-to-test-bot-detection.md)
walks through comparing the report against a stock browser field by field, and
[the one-site checklist](playwright-detected-as-bot.md) is the order to work a block in.

The honest boundary: this fixes what the browser reports. It does not touch your address,
your request rate, or your timing. Those are the next three sections, and they are yours.

## Layer 2: the address you cannot patch

If layer 1 is clean and you are still blocked, suspect the exit. The quickest test costs
nothing: open the same URL by hand from the same machine and network that runs the
automation. If the manual visit is also blocked, this is not a fingerprint problem at all,
and no browser setting will move it.

Around 90% of proxy pools are public, so their addresses are already known and scored
before you connect. A flawless browser on a known-bad IP still loses, and the tool cannot
make a flagged address clean - that is a property of the address, not the browser. The fix
is a cleaner exit, ideally residential and not already on shared lists;
[whether a site can tell your proxy is a datacenter IP](can-websites-detect-a-datacenter-proxy-ip.md)
covers what gives the range away. You pass the proxy straight through, and the browser
timezone auto-derives from the egress IP so the two stories match:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

A mismatch between the address and what the browser says about location is its own signal;
[when the timezone does not match the proxy](timezone-proxy-mismatch.md) is the common
version of that.

## Layer 3: how much you do, per account and per minute

Some blocks are not about who you are but how much you ask for. A single identity making
requests far faster than a person could, or opening many sessions in a short window, trips
a velocity signal that no fingerprint work can hide - because the fingerprint was fine, the
volume was not. We have flagged our own product this way during testing, hammering one
scoring endpoint from one address, and the flag belonged to the harness rather than the
browser.

The fix is pacing and spreading, not stealth: fewer actions per minute, real gaps between
sessions, and not routing everything through one identity.
[Rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md) is the practical
side. A tell here is timing rather than content: the block arrives after a burst, or a
certain number of requests in, rather than on the first page.

## Layer 4: behaviour, which arrives minutes in

The last layer watches what you do once you are on the page. A form filled in eighty
milliseconds, keystrokes at a metronome interval, a pointer that jumps corner to corner
without crossing the space between, a session with no scroll and no mouse movement before a
click. If the block appears only after an interaction, and only on some pages, this is the
layer.

The engine helps at the edges - pointer motion follows a Bezier curve rather than
teleporting - but the shape of your flow is yours to make human. Space actions out, let
pages settle, move before you click, and do not fill a form faster than a person could read
it. This is the layer that explains a block arriving minutes into an otherwise clean
session rather than at the first request.

## Conclusion

A block is an AND of four independent decisions, so debugging it is a locating problem
before it is a fixing problem. Prove it is detection by hand, then work the layers: is the
browser report consistent, is the address clean, is the rate human, is the behaviour human.
invisible_playwright makes the first answer yes by driving a real patched Firefox with a
seed-consistent fingerprint, which is why the browser-check layer passes. The other three
are yours - a clean residential proxy, sane pacing, and realistic flows - and no tool that
is honest with you will claim otherwise. Anything promising to make all four disappear at
once is selling the part it cannot deliver.

## Short answers to the questions that lead here

**Will a stealth browser stop me getting blocked?** It fixes the browser-fingerprint layer,
which is often the one blocking you, but it cannot clean a flagged IP, raise a per-account
quota, or make robotic timing look human. Those three stay yours.

**I fixed my fingerprint and I am still blocked. Why?** Because the block came from a
different layer. Check the exit address, then your request rate, then your behaviour - in
that order, since the first is the cheapest to test.

**How do I know which layer blocked me?** Open the URL by hand from the same machine. Blocked
by hand means the IP; fine by hand but blocked automated means the browser or the behaviour;
blocked only after a burst means the rate.

**Is it always the proxy?** No, and buying a better proxy first is the most common wasted
money. It is one of four layers and often not the one that failed. Test the free things
first.

**Does invisible_playwright need a proxy?** Not to run, but for real targets yes - a clean
residential exit is layer two, and the tool fixes layer one, so you supply the address.

**Can any tool make me undetectable?** No, and treat that word as a warning sign. Detection
is a model over four independent layers; a real browser makes one of them read as genuine,
and the honest claim stops there.

## Sources

- This project's four-layer model of a block, and the release gate that flagged our own
  product for velocity during testing when the flag belonged to the test harness.
- The public detection suites (CreepJS, BotD, FingerprintJS, sannysoft, BrowserLeaks), each
  answering a different one of these layers, read from their own source rather than their
  rendered verdict.
- The wrapper's real API surface: seed-reproducible identity, proxy pass-through, and
  egress-derived timezone.

**See also:** [the one-site detection checklist](playwright-detected-as-bot.md) for the
order to work a block in, [how to test whether your browser is detected](how-to-test-bot-detection.md)
for measuring layer one, and [running Playwright in Docker without being detected](how-to-run-playwright-docker-undetected.md)
for the machine tells that surface in the cloud.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It fixes the layer it can
prove it fixes, and says plainly which three it leaves to you.*
