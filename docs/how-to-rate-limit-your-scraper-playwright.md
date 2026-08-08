---
title: "How to rate limit your own Playwright scraper"
description: "Request velocity is a scored detection signal, not politeness. Throttle your Playwright scraper with a minimum gap, jitter, concurrency caps, and 429 backoff."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 19
---


# How to rate limit your own Playwright scraper

Rate limit your own Playwright scraper by putting a minimum gap plus random jitter between
navigations, capping how many pages run in parallel, and backing off when the site returns
a `429`. Do it because request velocity is a scored detection signal, not courtesy: a
machine cadence flags the session on a layer that a perfect fingerprint never touches.

Most guides file rate limiting under manners: be a good citizen, do not overload the
server. That framing is why people skip it, because a scraper that is trying not to get
blocked has no obvious reason to slow itself down on purpose.

The reason is not manners. Request velocity is one of the signals a site scores you on,
and it sits on the layer that a good fingerprint does not touch. You can render a browser
that is internally consistent on every surface a page can read, and still lose the session
to a cadence no human produces. This page is why velocity is scored, the mistake we made
that proves it, and the throttle code that fixes it.

## Velocity is a behaviour signal, not politeness

Blocking is not one decision. A site can turn automation away at several independent
layers, and a fix aimed at one is invisible to the others. The address, the TLS handshake,
the machine fingerprint and the automation layer are four of them. The fifth is behaviour:
pointer motion, typing rhythm, how fast a form is filled, and how fast requests arrive.

Request velocity lives on that last layer. Ten page loads in a second from one identity,
each spaced by the same number of milliseconds, is a pattern a person cannot make with a
mouse and a keyboard. It does not matter that each individual request carries a perfect
browser. The disguise is per request; the tell is in the sequence.

This is the part that surprises people who have spent their effort on the fingerprint. A
[browser that looks real on every readable surface](how-to-scrape-without-getting-blocked.md)
still has a cadence, and a machine cadence is its own fingerprint. A perfect identity on a
20-requests-per-second schedule loses on the behaviour layer, and the fingerprint work is
not what failed.

## We flagged our own product on velocity

The clearest evidence we have that this is scored came from doing it to ourselves.

During a fingerprint validation run, one of the commercial detectors we test against
returned a high-activity flag next to an otherwise clean report. For a moment it read as a
product regression. It was not. The flag came from the test harness hammering one scoring
endpoint through the same exit addresses, run after run, at machine speed while collecting
samples. The browser was clean. The cadence was not, and the detector scored the cadence.

The lesson stuck because it inverts the usual worry. We were measuring the fingerprint and
the harness manufactured a behaviour signal underneath the measurement. The fix was not to
the browser at all: it was to space the runs out and stop reusing the same address at
volume. A detector that watches velocity does not care how good the browser is.

## Mouse motion is per action, cadence is separate

The engine's mouse model handles per-action realism, not request cadence, so it does not
rate limit your loop for you. It is worth being precise about what the engine already does,
because it covers one behaviour signal and not the other.

Every click arcs the pointer to the target along a Bezier curve rather than teleporting to
the coordinate, which adds a realistic, variable latency to each action and is
[movement that is produced rather than declared](human-mouse-movement.md). That defeats
the pointer-teleport tell inside a page.

What it does not do is govern how fast your loop issues navigations. The time between one
`page.goto` and the next is decided by your code, not by the mouse model, and if your loop
has no delay then your network cadence is a tight machine loop regardless of how human each
individual click looks. Per-action realism and per-session cadence are two different
signals, and the second one needs its own throttle.

## Throttle navigations with a minimum gap and jitter

The smallest useful fix is a gate that enforces a minimum time between requests, plus
random jitter so the interval is never uniform. Uniformity is itself a tell: a perfectly
regular four-second gap is as mechanical as no gap at all.

```python
import random
import time

from invisible_playwright import InvisiblePlaywright


class Throttle:
    """Enforce a minimum gap between requests, with jitter on top."""

    def __init__(self, min_gap=4.0, jitter=3.0):
        self.min_gap = min_gap
        self.jitter = jitter
        self._last = 0.0

    def wait(self):
        target = self._last + self.min_gap + random.uniform(0.0, self.jitter)
        now = time.monotonic()
        if now < target:
            time.sleep(target - now)
        self._last = time.monotonic()


urls = [f"https://example.com/page-{n}" for n in range(1, 40)]
throttle = Throttle(min_gap=4.0, jitter=3.0)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for url in urls:
        throttle.wait()               # 4 to 7 seconds since the last request
        page.goto(url, wait_until="domcontentloaded")
        # ... extract here ...
```

The `browser` object is a real Playwright `Browser`, so `new_page`, `goto` and everything
else behave exactly as documented upstream. The seed keeps the identity fixed across the
whole run, which matters here: a coherent identity moving at a human pace reads as one
returning visitor, while churning through many shallow identities at speed reads as a
crawl. Keeping one seed is the [debugging habit](playwright-detected-as-bot.md) and the
stealth choice at once.

## Cap concurrency, do not just space single requests

Spacing a single loop is not enough once you run pages in parallel, because two workers
with a four-second gap each still produce eight requests in the window one worker would
produce two in. What a site scores is total arrivals per identity per unit time, so the
throttle has to bound how many requests are in flight, not just how far apart one worker's
are.

A semaphore caps concurrency, and a per-worker delay keeps each lane from running hot:

```python
import asyncio
import random

from invisible_playwright.async_api import InvisiblePlaywright


async def scrape(urls, max_concurrency=2):
    sem = asyncio.Semaphore(max_concurrency)

    async with InvisiblePlaywright(seed=42) as browser:

        async def worker(url):
            async with sem:                     # at most 2 pages at once
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    # ... extract here ...
                finally:
                    await page.close()
                await asyncio.sleep(random.uniform(4.0, 8.0))

        await asyncio.gather(*(worker(u) for u in urls))


asyncio.run(scrape([f"https://example.com/page-{n}" for n in range(1, 40)]))
```

Two is a deliberately low default. The right number is whatever keeps your aggregate
arrival rate below the point a site starts scoring it, and that is lower than throughput
math suggests. It is cheaper to run slow and finish than to run fast and get the wrong page
returned for the rest of the day.

## Back off when the site tells you to

A throttle sets a pace you guess at up front. Backoff reacts to the pace the site actually
tolerates, which you only learn once it pushes back. A `429` status, or a soft block that
returns a challenge instead of the page, is the signal to slow down rather than retry
immediately at the same rate.

```python
import random
import time


def fetch_with_backoff(page, url, attempts=4):
    delay = 5.0
    for attempt in range(attempts):
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status == 429:
            retry_after = response.headers.get("retry-after", "")
            wait = float(retry_after) if retry_after.isdigit() else delay
            time.sleep(wait + random.uniform(0.0, 2.0))
            delay *= 2                          # exponential, not fixed
            continue
        return response
    return None
```

`response.status` and `response.headers` are standard Playwright, since `goto` returns a
real [`Response`](https://playwright.dev/python/docs/api/class-response). The exponential growth matters: a fixed retry delay against a rate limit
is just a slower version of the same machine cadence, and doubling it means a genuinely
overloaded lane keeps quiet instead of pounding a door that is already closed.

## Conclusion

Rate limiting your own scraper is not courtesy, it is the behaviour layer of evasion. A
site scores how fast requests arrive from one identity, that score is independent of how
real the browser looks, and a machine cadence flags a session that a flawless fingerprint
would otherwise carry. We know because our own harness manufactured the flag while the
browser under test was clean.

Put a minimum gap with jitter between navigations, cap concurrency so parallel workers do
not defeat the gap, keep one seed so the pace reads as one returning visitor, and back off
when the site returns a `429`. The engine gives every action a human latency through its
mouse model; the cadence between actions is the part your loop still owns.

## Short answers to the questions that lead here

**Why would I slow down a scraper on purpose?** Because request velocity is a scored
detection signal, not a courtesy. A machine cadence flags the session on the behaviour
layer no matter how real each individual request looks.

**How many requests per second is safe?** There is no universal number, and it is lower
than throughput math suggests. Bound the aggregate arrival rate per identity, start slow,
and let a `429` teach you the ceiling rather than guessing high.

**Does the Bezier mouse motion already handle this?** No. It adds realistic latency to each
click, which is a different signal. The time between navigations is decided by your loop
and needs its own throttle.

**Is a fixed delay between requests enough?** A fixed interval is itself a tell, because it
is too regular to be human. Add jitter so no two gaps match, and cap concurrency so
parallel workers do not blow past the gap.

**Can a perfect fingerprint make rate limiting unnecessary?** No. The fingerprint and the
cadence are independent layers. A perfect identity on a 20-requests-per-second schedule
still loses on velocity.

**What should I do when I get a 429?** Back off exponentially and honour any `retry-after`
header, rather than retrying at the same rate. A fixed retry delay is just a slower version
of the cadence that got you limited.

## Sources

- This project's causal model of blocking, in which request velocity is a behaviour-layer
  signal independent of the fingerprint. See
  [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md).
- This project's own fingerprint validation runs, including the high-activity velocity flag
  that turned out to be the test harness reusing one address at machine speed rather than a
  product signal.
- The Playwright API for
  [`page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto),
  [`Response.status`](https://playwright.dev/python/docs/api/class-response#response-status)
  and [`Response.headers`](https://playwright.dev/python/docs/api/class-response#response-headers),
  read from its own upstream documentation.

**See also:** [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for the full layer model, [human mouse movement](human-mouse-movement.md) for the
per-action signal the engine already covers, and
[stealth for AI browser agents](ai-browser-agents-stealth.md) for the pause shaped like
model latency, which is the same behaviour layer seen from the other side.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The velocity flag at the
centre of this page was one our own harness produced before we spaced the runs out.*
