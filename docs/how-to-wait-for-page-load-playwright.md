---
title: "How to wait for content to load in Playwright"
description: "How to wait for content to load in Playwright: why networkidle stalls on long-poll and websockets, and when wait_for_selector or wait_for_function is right."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 16
---


# How to wait for content to load in Playwright

To wait for content to load in Playwright, wait for the specific signal you actually
need - a specific element with `wait_for_selector`, or a condition with
`wait_for_function` - not for the network to go quiet with `networkidle`, and never for a
fixed `time.sleep`. The right wait is a correctness decision, not a speed one.

Waiting in Playwright is not a style preference. Pick the wrong wait and you do not get
a slower script, you get a wrong one: a handle read against a document that has already
gone, a page scraped before its content exists, an intermittent error that only appears
on a loaded machine or in CI.

This page is about choosing between the three real options - `networkidle`,
`wait_for_selector` and `wait_for_function` - as a correctness decision, and about the
one non-option, a fixed `sleep`, that is both unreliable and, on the kind of site that
watches behaviour, a tell.

The examples use this project, but everything here is stock Playwright. The `browser`
object below is a real Playwright `Browser`, so every method is the one you already
know:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

## The error that a bad wait actually produces

The clearest way to see why this matters is the error you get when you guess wrong:

```
Error: Execution context was destroyed, most likely because of a navigation
```

When you evaluate JavaScript or hold an element handle, it is bound to the current
document. If the page navigates while you are still talking to the old one, the context
is gone and every reference into it fails. Two of the most common ways to trigger this
are pure timing:

- **Racing the load.** You evaluate while the page is still settling, so your call lands
  just as a redirect fires.
- **A redirect chain.** `goto` returns on the first response; a page that then bounces
  through two more URLs destroys contexts under you while you work against the first.

The fix is not a longer sleep. It is to wait for the navigation instead of guessing, and
to re-query handles after it rather than carrying a reference across one:

```python
with page.expect_navigation():
    page.click("#submit")
page.query_selector("#thing").inner_text()   # fresh handle, new context
```

That single distinction - wait for the event, then re-query - is most of what separates
a script that works once from one that works every time.
[The full breakdown of that error](execution-context-destroyed.md) also covers the case
where the same message means the site moved you somewhere, not that your code raced,
which no waiting logic can fix.

## Why networkidle is not the default you want

[`page.wait_for_load_state("networkidle")`](https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state)
waits until there have been no network
requests for a short quiet window. It reads like "wait until the page is done", and on a
static page it is fine. On a modern application it is a trap for a specific reason: many
pages never go idle.

A page that holds a long-poll connection open, keeps a websocket alive, or fires
periodic analytics beacons has network activity by design, forever. `networkidle` on
that page does not settle early, it does not settle at all - it waits out its own full
timeout on every navigation and then either raises or hands you a page it never actually
confirmed was ready. You have paid the maximum latency to learn nothing.

```python
# Fragile on any page with a websocket, long-poll, or a heartbeat beacon:
page.goto("https://example.com/app")
page.wait_for_load_state("networkidle")   # may never settle; waits out the timeout
```

`networkidle` is a signal about the network, and what you almost always care about is a
signal about the content. Those are different questions, and the next two waits answer
the one you actually have. When the signal you want is one specific network response
rather than an element or a condition, there is a targeted wait for exactly that, covered
in [wait for a specific API response in Playwright](wait-for-specific-api-response-playwright.md).

## wait_for_selector: wait for the thing you need

Most of the time you are not waiting for "the page" at all. You are waiting for one
specific element - the results container, the price, the next batch of rows - and
Playwright can wait for exactly that:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/results")

    # wait for the actual content, not for the network to go quiet
    page.wait_for_selector("#results .item", timeout=15_000)

    items = page.locator("#results .item")
    print("rows:", items.count())
```

This is more correct than `networkidle` and usually faster. It returns the moment the
thing you need exists, it does not care whether a beacon is still firing in the
background, and its failure mode is honest: if the selector never appears, you get a
timeout that names what was missing instead of a page that quietly loaded the wrong
content. Assert on the presence of the signal you want, not on the absence of network
traffic.

The `locator` API waits implicitly too - `page.locator("#results .item").inner_text()`
auto-waits for the element before reading it - so in a lot of code you do not need an
explicit [`wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector)
at all. Reach for the explicit form when you want to wait
at one point and act at another, or when you want a clear timeout at a known step.

## wait_for_function: when the signal is a state, not an element

Sometimes the thing you are waiting for is not "an element appeared" but "a condition
became true": a counter reached a value, a spinner attribute cleared, the list stopped
growing. [`wait_for_function`](https://playwright.dev/python/docs/api/class-page#page-wait-for-function)
evaluates a predicate in the page until it returns truthy.

The infinite-scroll case is the clearest example. There, the condition is content
growth, and the reliable loop waits for `document.body.scrollHeight` to actually
increase rather than sleeping between scrolls:

```python
def wait_for_growth(page, last_height, timeout_ms=10_000):
    page.wait_for_function(
        "prev => document.body.scrollHeight > prev",
        arg=last_height,
        timeout=timeout_ms,
    )
    return page.evaluate("document.body.scrollHeight")
```

This is the general-purpose primitive: anything you can express as a JavaScript
condition, you can wait for precisely, with a bounded timeout, instead of approximating
it with a duration. The growth-based loop, deduping, and knowing when the feed has truly
ended are covered in full in
[how to scrape infinite scroll pages](how-to-scrape-infinite-scroll-playwright.md).

## Why a fixed sleep is the worst of the options

`time.sleep(3)` between actions is the most common wait and the only one that is wrong
in two directions at once.

It is **flaky**. Three seconds is a bet about a network and a render pipeline you do not
control. On a slow connection or a heavy page it is too little and you read content that
has not arrived; on a fast one it is too much and you burn time on every iteration for
nothing. A condition-based wait returns as soon as the condition holds and only fails if
it never does; a sleep does neither.

It is also, on some sites, a **behavioural tell**. A script that pauses for exactly the
same interval before every action produces a perfectly uniform rhythm that no human
input generates, and a page that scores behaviour can read that timing directly. This is
the same class of signal as a
[scroll loop that fires at a constant rate](how-to-scrape-infinite-scroll-playwright.md):
the uniformity itself is the signature, separate from anything about the browser's
fingerprint, and it is why blocks sometimes arrive minutes into a session rather than at
the first request. The
[one-site detection checklist](playwright-detected-as-bot.md) puts behaviour at step
five for exactly this reason.

The takeaway is not "never call `sleep`". It is that a sleep is for *pacing* - varying
how long you dwell so the timing looks like a device rather than a timer - and never for
*knowing content arrived*. Those two jobs get two different tools:

```python
import random

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/results")

    # knowing the content is there: a condition
    page.wait_for_selector("#results .item")

    # pacing between actions: a varied dwell, never the load signal
    page.wait_for_timeout(random.randint(200, 900))
```

## A note on launch, before the first goto

One wait sits before any of the above: waiting for the browser to be ready at all. A
per-request timeout on each network call during startup does not bound the launch step
as a whole, which is a different question with a different answer - written up in
[why one launch in six was randomly slow](slow-browser-launch-timeout-budget.md). Worth
knowing that the same "bound the right thing" logic applies one level below the page.

## Conclusion

Waiting well in Playwright is choosing the wait that matches the signal you actually
have. `networkidle` answers a question about the network that a long-poll or a websocket
page never lets settle. `wait_for_selector` waits for the specific element you need and
fails honestly when it is missing. `wait_for_function` waits for any condition you can
express, which is what the infinite-scroll growth loop is built on. A fixed sleep answers
neither question and adds a uniform-timing tell on top, so keep it for pacing and never
for knowing content arrived.

The correctness stakes are real, not cosmetic: the difference between these waits is the
difference between a script that re-queries a fresh context and one that reads a handle
into a document that has already navigated away. Wait for the event, re-query after it,
and let the API's own primitives do the waiting.

## Short answers to the questions that lead here

**How do I wait for a page to fully load in Playwright?** Wait for the specific content
you need with `wait_for_selector`, or for a condition with `wait_for_function`. "Fully
loaded" is rarely the real requirement, and on an app that keeps a connection open it is
a state the page may never reach.

**Why does networkidle never finish on some pages?** Because those pages have network
activity by design - a websocket, a long-poll, or a periodic analytics beacon - so there
is no quiet window for `networkidle` to detect. It waits out its full timeout instead of
settling.

**Should I use time.sleep between actions?** Only to vary pacing, never as the signal
that content arrived. A fixed sleep is flaky in both directions and, on a site that
scores behaviour, a uniform interval is itself a tell.

**What is the difference between wait_for_selector and wait_for_function?**
`wait_for_selector` waits for an element to appear in the DOM. `wait_for_function` waits
for any JavaScript condition to become true, which is what you need when the signal is a
state - a count, a cleared attribute, a growing height - rather than a single element.

**Why do I get "Execution context was destroyed"?** Usually a navigation happened while
you were still holding a handle or evaluating against the old document. Wait for the
navigation with `expect_navigation`, then re-query your handles against the new context.

**Does networkidle fix the context-destroyed error?** It changes when `goto` returns, so
it helps with some load races and does nothing for a redirect that fires later. Waiting
for the navigation and re-querying afterwards is the reliable fix.

## Sources

- Playwright's own waiting primitives -
  [`wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector),
  [`wait_for_function`](https://playwright.dev/python/docs/api/class-page#page-wait-for-function),
  [`wait_for_load_state`](https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state),
  `expect_navigation` - used here for the condition you actually
  have rather than a fixed duration.
- This project's notes on the context-destroyed error, where racing the load and redirect
  chains are the two timing causes, and the prescription is to wait for the navigation,
  re-query handles after it, and use the API's own waiting rather than sleeping.

**See also:** [the "Execution context was destroyed" breakdown](execution-context-destroyed.md)
for the error a bad wait produces, [how to scrape infinite scroll pages](how-to-scrape-infinite-scroll-playwright.md)
for the growth-based `wait_for_function` loop, and [the one-site detection checklist](playwright-detected-as-bot.md)
for where a uniform-timing tell sits in the overall order.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The waits on this page are
the ones its own test suites settled on after a fixed-sleep version under-collected on a
slower connection.*
