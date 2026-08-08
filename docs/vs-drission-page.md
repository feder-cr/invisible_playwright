---
title: "invisible_playwright vs DrissionPage"
description: "DrissionPage fuses an HTTP session mode and a CDP browser mode in one object, so each half fails a different half of a strict detection check. Here is why."
parent: "Comparisons"
nav_order: 18
---


# invisible_playwright vs DrissionPage

invisible_playwright and DrissionPage solve two different problems. DrissionPage fuses a
fast HTTP session mode with a CDP-driven Chromium browser mode in one object;
invisible_playwright is a single patched-Firefox engine driven by stock Playwright. Against
a strict detection check the difference decides the outcome: DrissionPage's two modes each
fail a different half of the check, while one real-browser engine presents a single
consistent identity all the way down. Pick DrissionPage for speed where nobody inspects the
connection, invisible_playwright when the connection and the fingerprint are read together.

DrissionPage is a well-liked Python automation library, around 12.3k stars and actively
maintained (its repository was last pushed on 2026-07-22 as of this writing). Its
signature idea is genuinely clever and worth understanding on its own terms: a single
object that can act as an HTTP client one moment and drive a real Chromium browser the
next, carrying cookies and state across the switch. Its own documentation calls these the
`session` mode and the `browser` mode, unified through a page object that speaks both.

That fusion is the feature people come for, and it is also the reason a strict detection
check tends to catch it. The two modes do not share an identity. Each half of the design
is exposed to a different half of the check, and passing one does nothing for the other.

This page is what the two modes actually are, where each is seen, why the dual design
splits the failure in two rather than covering both, and what a single-engine approach
does instead. It is not a put-down: DrissionPage is a solid tool for the job it was built
for, and that job is not the same as looking like a real browser end to end.

## What DrissionPage is, and the one thing it does that nothing else does

Most automation tools pick a lane. A library is either an HTTP client that never opens a
browser, or a browser driver that never sends a raw request. DrissionPage refuses the
choice. You can scrape a listing page over plain HTTP for speed, then, on the same object
with the same cookies, switch into a controlled Chromium instance to click through a step
that needs JavaScript.

For throughput that is a real advantage. The HTTP path is fast and cheap, and you only pay
for a full browser on the pages that require one. If your target does not look closely at
who is connecting, this is an efficient design and there is little reason to argue with it.

The trouble starts when the target does look closely, because "who is connecting" now has
two different answers depending on which mode is live, and neither answer is a real
browser.

## Browser mode: the CDP-Chromium ceiling

In browser mode, DrissionPage controls Chromium by speaking the Chrome DevTools Protocol
directly. This is the same transport that most modern Chromium automation uses, and it
carries the same ceiling. The identity you present is a CDP-driven Chromium build, and a
detector that fingerprints the engine is reading exactly that.

Two structural facts sit underneath this, both of which have their own pages here:

- The thing you are driving is Chromium, not Chrome, and the difference is
  [visible in the fingerprint before any automation is added](chromium-is-not-chrome.md):
  codec support, the internal build strings, and a handful of surfaces that a real
  consumer Chrome carries and the open-source build does not.
- Driving over CDP is [the same architectural position as any other CDP tool](vs-nodriver.md),
  and property-level patching from inside the page cannot repair what the engine reports
  about itself from outside it.

None of this is a criticism specific to DrissionPage. It is where every CDP-over-Chromium
tool lands, and the honest framing is that browser mode inherits the Chromium engine's
detectability rather than solving it.

## Session mode: a handshake that is not any browser's

Session mode is the more interesting failure, because it fails earlier and more quietly.

When DrissionPage drops to its HTTP session mode, there is no browser in the loop at all.
The request is made by a Python HTTP stack, and a Python HTTP stack has its own TLS
fingerprint. That handshake does not match Chrome's, it does not match Firefox's, and it
does not match any browser's, because no browser produced it.

This matters because the TLS handshake happens before your code sends a single header. A
server can compare the fingerprint of the connection against the browser your user agent
claims to be, notice they disagree, and reject the request before your carefully set
headers, cookies, or user agent are ever read. That is
[why a plain Python HTTP client gets blocked before it sends a header](web-scraping-tls-fingerprint-requests-blocked.md),
and session mode is a plain Python HTTP client wearing a browser's clothes at the header
layer only.

You cannot patch this from the header layer, because the mismatch lives one layer below
the headers. The connection has already announced what it is. See
[JA3 and JA4 TLS fingerprinting](ja3-ja4-tls-fingerprint.md) for what that announcement
actually contains and why in-page tricks never reach it.

## Why the dual mode splits the failure in two

A modern detection stack does not look at one surface: it reads the TLS handshake, reads
the in-page fingerprint, then checks whether the two tell the same story. That is what
turns DrissionPage's signature feature into a signature liability against a strict check -
the dual mode hands it two different stories to catch instead of one:

- **In browser mode**, the handshake is Chromium's and the fingerprint is CDP-driven
  Chromium's. Consistent with each other, and both saying "automated Chromium engine."
- **In session mode**, there is no in-page fingerprint at all, and the handshake is a
  Python client's. A user agent claiming a browser, on a connection that no browser makes.

Neither mode is a real browser presenting a consistent story all the way down. The switch
that makes the tool fast is exactly the switch that hands a detector a second, differently
shaped tell. You do not get to pass the browser check with the browser mode and the speed
check with the session mode, because whichever mode is live is the one being measured, and
each one fails a different half of the same check.

This is not a bug in DrissionPage. It is the direct consequence of building one object out
of two identities that were never the same identity.

## What invisible_playwright does differently

invisible_playwright makes the opposite trade on purpose. There is one identity, one
engine, and it is a real browser all the way down.

The engine is Firefox, patched at the C++ level so that what it reports about itself
matches what it actually is. There is no session mode to drop into, because the whole
point is that every request comes from a genuine browser handshake with a matching
in-page fingerprint. You drive it with stock Playwright, and the object you get back is a
real [Playwright `Browser`](https://playwright.dev/python/docs/api/class-browser) with
every standard method:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # real browser handshake, matching in-page fingerprint
```

The `seed` fixes the identity. The same seed produces the same GPU, canvas hash, audio
context, fonts and screen every run, which is what lets you replay a failing session
instead of guessing at it:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)   # log it to reproduce this exact identity later
    page = browser.new_page()
    page.goto("https://example.com")
```

Async is the same shape, for the
[concurrency DrissionPage's session mode is usually reached for](run-invisible-playwright-concurrently-asyncio.md):

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
```

You lose the raw-HTTP speed path. That is a real cost, and if your workload is
high-volume scraping of pages that never inspect the connection, DrissionPage's session
mode is faster and you should use it. If you want both, you can
[pair invisible_playwright with a raw HTTP client for the fast paths](combine-invisible-playwright-with-httpx-for-speed.md)
and keep the browser only for the pages that read the connection. What you gain is that
there is only ever one story to tell, and it is a true one.

The honest caveat, the same one this whole site repeats: a consistent real browser is not
a magic pass. The IP still matters, behaviour still matters, and a datacenter address on a
perfect fingerprint is still a datacenter address. A single engine removes the split-
identity problem; it does not remove the network and behaviour surfaces that no browser
property controls.

## Conclusion

DrissionPage's dual mode is a smart answer to a throughput problem and a poor answer to a
detection problem, and those are different problems. When the target does not scrutinise
the connection, the fused session-plus-browser design is efficient and pleasant to use.
When the target does scrutinise it, the fusion becomes the weakness: browser mode carries
the CDP-Chromium ceiling, session mode carries a non-browser handshake, and a check that
reads both catches whichever one is facing it.

If your job is "look like a real browser end to end," the trade that helps is the opposite
one: fewer identities, not more. One real engine, driven by the Playwright API you already
know.

## Short answers to the questions that lead here

**Is DrissionPage detectable?** Its browser mode presents as CDP-driven Chromium, which a
detector fingerprints as an automated engine, and its session mode makes requests with a
Python TLS handshake that is not any browser's. Each is caught by a different part of a
strict check.

**What is the point of DrissionPage's session mode then?** Speed. It scrapes over plain
HTTP without paying for a browser, which is a real advantage on pages that do not inspect
the connection. It stops being an advantage the moment the connection is inspected.

**Can I just set a browser user agent on session mode?** No. The user agent is a header,
and the mismatch is in the TLS handshake, which is sent before any header. The connection
has already announced it is not a browser.

**Does invisible_playwright have a fast HTTP-only mode?** No, on purpose. It is one real
browser engine so there is a single consistent identity. That costs you the raw-HTTP speed
path and buys you an end-to-end true story.

**Is DrissionPage abandoned?** No. Its repository was actively maintained as of its last
push on 2026-07-22 with a large user base. This is an architecture comparison, not a
maintenance one.

**Which should I pick?** DrissionPage for high-volume scraping where the target does not
look closely and speed dominates. invisible_playwright when you need one browser identity
to hold up under a check that reads the handshake and the fingerprint together.

## Sources

- DrissionPage's own repository and documentation, read from source, for the session and
  browser mode design, the CDP-based browser control, and the maintenance dates cited
  above.
- This project's own comparison and network notes, linked throughout, for the CDP-Chromium
  ceiling and the TLS-handshake behaviour that no in-page layer can reach.

**See also:** [why Chromium is not Chrome](chromium-is-not-chrome.md) for the browser-mode
ceiling, and [the checklist for being detected on one site](playwright-detected-as-bot.md)
for working a real block in the right order.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The dual-mode split is not a
knock on DrissionPage; it is what happens when one object carries two identities.*
