---
title: "Why content_frame() returns None for a cross-origin iframe"
description: "content_frame() returns None on a cross-origin iframe, frame.evaluate() throws, frame_locator times out - one shared cause: process isolation, not a permissions bug."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 9
---

# Why content_frame() returns None for a cross-origin iframe

`content_frame()` returns `None` for a cross-origin iframe when Firefox's
site-isolation feature puts that iframe in a separate OS process: the automation
driver's frame tree holds only an empty placeholder for it, so there is no real frame
reference to hand back. It is not a permissions bug, and the same single cause is why
`frame.evaluate()` throws and `frame_locator` times out on the same iframe.

That cluster of symptoms all lands on the same page, all against the same cross-origin
iframe: [`element_handle.content_frame()`](https://playwright.dev/python/docs/api/class-elementhandle)
returns `None`, [`frame.evaluate()`](https://playwright.dev/python/docs/api/class-frame)
throws a permission error naming a cross-origin object, and
[`frame_locator(...).click()`](https://playwright.dev/python/docs/api/class-framelocator)
times out with `force=True` changing nothing. Disabling JavaScript works around it and
defeats the point of automating the page at all.

These read like three unrelated bugs. They are one bug, and it isn't a permissions
problem. If the iframe you are after is same-origin instead, none of this applies and
[scraping it is a short job with `frame_locator`](how-to-scrape-iframe-content-playwright.md).

## What's actually different about that iframe

The common thread is that the iframe is cross-origin from the page embedding it -
the shape a third-party widget almost always has: a consent banner, a payment form, a
support chat, anything served from a different domain than the page around it.

Firefox's site-isolation feature can put a cross-origin iframe in an entirely
separate OS process from the page that embeds it, as a security boundary between
origins. That boundary is exactly right for security and exactly wrong for an
automation driver that assumed it could reach into every frame from one process.

## Why the driver's own frame tracking breaks

The frame tracking breaks because it is built from a single process while the iframe
lives in another. Playwright drives Firefox through
[Juggler, an internal automation protocol](playwright-protocol-drift.md), which keeps
a tree of every frame on the page so `page.frames`, `content_frame()` and friends can
find them. That tree is built and maintained from the parent process.

When the iframe's browsing context lives in a different, isolated process, the
parent-side frame tree registers a placeholder for it instead of the real thing: no
URL, no docShell reference, no execution context that actually wraps the iframe's
global object. Every one of the three failing operations needs exactly the piece
that placeholder doesn't have, which is why fixing one never fixed the others - they
were never three separate problems to fix.

## Why this is easy to misdiagnose the first time

The first fix attempt targeted the wrong layer entirely, and it's worth walking
through why, because the failure mode generalises.

The instinct was to wrap the suspect property read in a try/catch, on the theory that
a security exception was firing and needed handling. It wasn't. The property in
question simply isn't exposed to page-level script at all, so reading it from that
scope returns `undefined` rather than throwing - there was never an exception to
catch, and the code silently did nothing.

A second attempt tried comparing two identifiers to detect the isolated case
directly. Both sides of that comparison turned out to be unreachable from the
context the check ran in, so the comparison was comparing two things that were
always going to be equal (or always going to be absent) regardless of whether the
real bug was present.

Neither fix was tested against a reproduction that failed on the broken binary and
passed on a fixed one - both were committed on the strength of a plausible-sounding
theory. [The general version of that mistake, and the discipline that catches it,](how-to-test-bot-detection.md)
is worth reading before trusting your own first hypothesis on a bug like this one.

## The actual fix, and why it's a single preference

The fix is a single preference forcing the non-isolating strategy, so every
cross-origin iframe loads into the same process as the page around it instead of a
separate one. That is a real trade: the security boundary the isolating strategy adds
is gone, in exchange for an automation driver whose frame tree actually reaches every
frame on the page. For a stealth-automation engine that already assumes a fully
trusted, single-purpose session, that trade is the right one; it would not be for a
general-purpose browser handling untrusted content by default.

Once the site-isolation strategy was identified as the actual variable, confirming it
took one controlled comparison: the same URL, loaded once with the isolating strategy
active and once with it set to keep same-process handling for every origin. Four
frame-tree entries with empty URLs and no reachable content frame in the first case;
five entries with full URLs and a working `content_frame()` in the second. The
strategy setting was the only thing that differed between the two runs.

## What to check in your own setup

```python
frames = page.frames
for f in frames:
    print(f.url or "(empty - possibly isolated)", f.name)
```

An entry with an empty URL and no name, sitting where a real iframe should be, is the
shape of this exact problem. Confirm it by toggling Firefox's site-isolation strategy
preference and re-running the same check: if the empty entry gains a real URL, that
preference is the cause, not a permissions or timing issue anywhere in your own code.

## Short answers to the questions that lead here

**Why does `content_frame()` return `None` for an iframe that clearly has content?**
The iframe's browsing context is very likely running in a separate, isolated process,
and the driver's frame tree only has a placeholder for it rather than the real
reference.

**Why does `frame.evaluate()` throw a cross-origin permission error here specifically?**
Same root cause. The evaluation target the driver is trying to reach isn't actually
wired to the real execution context.

**Does `force=True` fix a `frame_locator` timeout on a cross-origin iframe?** No,
because the element genuinely isn't reachable through the frame tree the driver is
using; forcing the click doesn't change what frame tree exists.

**Is this a permissions bug?** No. Nothing was denied in the security sense; the
property being read simply isn't exposed to the scope trying to read it, and the
underlying frame reference the driver needs was never populated in the first place.

**See also:** [why an attached debugger makes automation detectable](debugger-timing-detection.md),
for another case where the automation layer itself was the actual surface; and
[how to test whether your setup is actually working](how-to-test-bot-detection.md),
for the reproduction discipline that caught the two wrong fixes here.

## Sources

- This project's own patch history for the root-cause diagnosis, the two earlier
  incorrect fixes and why each one failed to address anything, the single-preference
  fix, and the regression test suite that locks the fixed behaviour in.
- Playwright's own API reference for the three calls involved: [`ElementHandle.content_frame`](https://playwright.dev/python/docs/api/class-elementhandle),
  [`Frame.evaluate`](https://playwright.dev/python/docs/api/class-frame), and
  [`FrameLocator`](https://playwright.dev/python/docs/api/class-framelocator).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on a bug that took two wrong committed fixes before
the actual cause was reproduced.*
