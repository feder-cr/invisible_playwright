---
title: "Run invisible_playwright concurrently with asyncio"
description: "Run invisible_playwright with asyncio: bound pages with a Semaphore, give each worker its own seed for distinct identities. Concurrency is speed, not stealth."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 82
---


# Run invisible_playwright concurrently with asyncio

The async API lets one launched browser hold many pages and contexts at once, and
`asyncio.gather` lets them make progress together instead of one after another. That is
a throughput change. It makes a run finish faster. It is worth being clear up front that
it does nothing to make the run look more human, and past a point it does the opposite.

This page shows the two kinds of concurrency, how to bound them with a semaphore, how to
keep each concurrent identity distinct and reproducible with a per-worker seed, and the
honest limit: concurrency trades against the behavioural signal, so it is a speed lever
rather than a disguise.

## Two kinds of concurrency, and they are not the same

Before any code, separate the two things people mean by "run it concurrently", because
they have different fingerprints and different risks.

**Many pages under one launched browser.** One call to `InvisiblePlaywright(seed=...)`
produces one identity: one GPU, one canvas hash, one font set, one screen. Every page or
context you open under that browser shares that identity. This is the right shape when
you are fanning many URLs through a single persona and you want them to look like one
person opening several tabs.

```python
import asyncio
from invisible_playwright.async_api import InvisiblePlaywright

async def one_identity_many_pages():
    async with InvisiblePlaywright(seed=42) as browser:
        urls = ["https://example.com/%d" % i for i in range(5)]
        pages = [await browser.new_page() for _ in urls]
        await asyncio.gather(*(p.goto(u) for p, u in zip(pages, urls)))
        return [await p.title() for p in pages]

print(asyncio.run(one_identity_many_pages()))
```

`browser` here is a real `playwright.async_api.Browser`, so `new_page`, `new_context`
and every other method behave exactly as [documented upstream](https://playwright.dev/python/docs/api/class-browser).
There is no wrapped subset to learn.

**Many launched browsers, one identity each.** When each unit of work should look like a
different person, launch a separate seeded browser per worker. That is the section below.

## Bounded fan-out with a semaphore

Unbounded `asyncio.gather` over a hundred launches will try to start a hundred browsers
at once and fall over on memory long before the site notices. Bound it with
`asyncio.Semaphore(N)`: at most `N` render in parallel, the rest wait their turn.

```python
import asyncio
from invisible_playwright.async_api import InvisiblePlaywright

MAX_PARALLEL = 3
sem = asyncio.Semaphore(MAX_PARALLEL)

async def render(seed, url):
    async with sem:                       # only MAX_PARALLEL are inside at once
        async with InvisiblePlaywright(seed=seed) as browser:
            page = await browser.new_page()
            await page.goto(url)
            return seed, await page.title()

async def main():
    jobs = [(11, "https://example.com/a"),
            (22, "https://example.com/b"),
            (33, "https://example.com/c"),
            (44, "https://example.com/d"),
            (55, "https://example.com/e")]
    results = await asyncio.gather(*(render(s, u) for s, u in jobs))
    for seed, title in results:
        print(seed, title)

asyncio.run(main())
```

`N` is the only knob that matters here. Raise it and the run finishes sooner and the
machine works harder; lower it and the reverse. It has nothing to do with whether any one
page looks automated, and the [parallel-scraping walkthrough](how-to-scrape-multiple-pages-in-parallel-playwright.md)
covers the pool-and-queue variant of the same idea for larger job lists.

## One seed per worker keeps identities distinct and reproducible

Passing a distinct `seed` to each launch gives each worker its own machine: its own GPU,
canvas hash, audio context, fonts and screen, all derived from that seed. Two workers
with two seeds are two different browsers, and they stay different every run because the
seed fixes them. Two workers with the *same* seed are the same machine twice, which is a
correlation a detector can use, so pick distinct seeds when the identities are meant to
be distinct.

The reproducibility is the payoff when something breaks mid-run. Because seed 44 always
yields the same browser, a worker that failed can be replayed exactly by launching seed
44 again by hand, instead of hoping the next random draw reproduces the fault. Log the
seed with every result and a failing concurrent run stays debuggable:

```python
async def render(seed, url):
    async with sem:
        async with InvisiblePlaywright(seed=seed) as browser:
            page = await browser.new_page()
            try:
                await page.goto(url)
                return seed, "ok", await page.title()
            except Exception as exc:
                return seed, "fail", repr(exc)   # seed is enough to replay it
```

This is the same reproducibility the [quickstart's async example](quickstart.md) relies
on, applied across many workers at once.

## What concurrency does not buy you

invisible_playwright is built to look like a real Firefox driven by a real person, and
that is why it clears most fingerprint, TLS and driver-layer checks: the engine is a
genuine patched Firefox, so those surfaces read as genuine. This is the part the
throughput framing hides: concurrency changes none of that for better, and it can change
the parts it does not cover for worse.

- **IP reputation is unchanged.** Ten workers behind one address are ten sessions from
  one address. If that address is a known or datacenter exit, running ten at once makes
  the pattern louder, not quieter. Route each worker through a clean exit; see the proxy
  setup in [Configuration](configuration.md).
- **Per-account quotas and rate limits are unchanged.** A limit of X actions per hour is
  still X whether you spend it serially or all at once. Concurrency lets you hit the wall
  sooner, not move it.
- **Behaviour and timing are unchanged, and often made worse.** Ten pages arriving in the
  same second from one address is a velocity signal a person does not produce. The
  [detection checklist](playwright-detected-as-bot.md) puts request pattern and exit ahead
  of the browser for exactly this reason.

Concurrency helps you finish faster. It does not help you look human.

## A throughput lever, not a stealth one

Treat `N` as a speed setting with a cost, and measure both sides. Raising the semaphore
from 1 to 4 on a fixed batch cut wall-clock time by roughly the same factor, and it also
multiplied the request rate from a single exit by roughly the same factor. The first
number is why you reach for concurrency; the second is the one that gets a session
flagged. The right `N` is the largest one whose combined request rate still sits under
what a real person, or your per-account budget, would produce - which is usually a
smaller number than the machine could handle.

That makes concurrency and pacing two ends of the same dial. If you push `N` up, you
generally have to space the work out to compensate, which is a
[rate-limiting problem](how-to-rate-limit-your-scraper-playwright.md), not a fingerprint
one. And you can only tell whether the combined pattern reads as robotic by measuring it
the way a detector would, which is what the
[bot-detection testing method](how-to-test-bot-detection.md) is for: run more than once,
compare against a real browser, and watch the request rate rather than a single verdict.

## Conclusion

The async API gives you real, cheap parallelism: many pages under one seeded identity, or
many seeded browsers bounded by a `Semaphore(N)`, each with a distinct reproducible
machine. Use it to finish sooner. Do not use it to hide, because it does the opposite -
it concentrates request volume and timing, the two things the genuine-Firefox fingerprint
was never going to fix for you. Set `N` by the request rate you can defend, seed each
worker for a distinct and replayable identity, and give it a clean exit and human pacing.
Those you supply; the browser supplies the rest.

## Short answers to the questions that lead here

**How do I run many invisible_playwright sessions at once?** Use the async API and
`asyncio.gather`, wrapped in an `asyncio.Semaphore(N)` so only `N` render in parallel.
Give each worker its own seed if the identities should differ.

**Does running more browsers in parallel help me avoid detection?** No. It helps you
finish faster and, if it pushes your request rate past a human's, it makes the session
look more automated, not less.

**How many should I run at once?** The largest `N` whose combined request rate from one
exit still sits under what a person or your per-account budget would produce. That is
usually smaller than the machine can handle.

**Do parallel workers share a fingerprint?** Pages under one launched browser share one
identity. Separate seeded launches each get their own. Two workers with the same seed are
the same machine twice, which is a correlation you probably did not want.

**Can concurrency fix a bad proxy or a hit rate limit?** No. IP reputation and per-account
quotas are unchanged by how you schedule the work; concurrency only spends the same budget
sooner. Supply a clean exit and pacing yourself.

**Sync or async for this?** Async. `asyncio.gather` with a semaphore is the natural way to
bound many overlapping page loads; the sync API runs one call at a time.

## Sources

- This project's async API and seed model, as documented on the
  [Quickstart](quickstart.md) and [Configuration](configuration.md) pages.
- Standard library `asyncio` - `gather`, `Semaphore` and `run` - used exactly as
  documented upstream, over a real
  [`playwright.async_api.Browser`](https://playwright.dev/python/docs/api/class-browser).
- This project's own release runs, where raising the parallel count cut wall-clock time
  and multiplied the per-exit request rate by roughly the same factor.

**See also:** [scraping multiple pages in parallel](how-to-scrape-multiple-pages-in-parallel-playwright.md)
for the pool-and-queue shape, [rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for the pacing that concurrency trades against, and the
[detection checklist](playwright-detected-as-bot.md) for why the exit and the request
pattern outrank the browser.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It passes most fingerprint,
TLS and driver checks because the engine is a genuine Firefox; the exit, the pacing and the
per-account budget are still yours to get right.*
