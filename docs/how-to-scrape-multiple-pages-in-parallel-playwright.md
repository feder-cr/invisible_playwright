---
title: "How to scrape pages in parallel with Playwright"
description: "Run many Playwright workers at once with asyncio.gather, one identity per worker, and see why a shared fingerprint or exit IP is the tell that undoes it."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 18
---


# How to scrape pages in parallel with Playwright

To scrape pages in parallel with Playwright, run each worker as its own browser launch,
gather them on one event loop with `asyncio.gather`, and give every worker a distinct seed
and a distinct exit IP so it reads as a separate person. Concurrency is the easy half; the
half that decides whether parallelism helps or hurts is whether the workers are also
distinct identities.

Running one page at a time is slow, and the obvious fix is to run many at once. The
mechanics of that in Python are easy: `asyncio.gather` and a handful of coroutines. The
part that is not obvious is what each of those coroutines looks like from the other side of
the connection.

This page covers the concurrency itself, the mistake that makes concurrency worse than
serial work, and the shape that keeps N workers looking like N different people instead of
one person in a hurry.

## What "parallel" has to mean here

There are two different things people call parallel scraping, and they have opposite
requirements.

The first is **throughput**: fetch more pages per minute. Pure asyncio solves that, because
network waits overlap.

The second is **identity**: the pages must not look like they came from the same place at
the same time. That is not solved by asyncio at all, and it is where the naive version
fails. A detector that keeps any per-session state does not see "twenty requests". It sees
whether those twenty requests share a machine, an address, or a clock, and twenty
simultaneous requests that share all three are more suspicious than one, not less.

So the working definition on this page: parallel means concurrent **and** distinct. If the
workers are concurrent but identical, you have built a machine that announces it is
pretending to be a crowd.

## The naive version, and why it shares one identity

The pattern in every tutorial is one browser, many contexts, gathered:

```python
import asyncio
from invisible_playwright.async_api import InvisiblePlaywright

async def worker(browser, url):
    page = await browser.new_page()   # a fresh context each time
    try:
        await page.goto(url, wait_until="domcontentloaded")
        return await page.title()
    finally:
        await page.close()

async def main(urls):
    async with InvisiblePlaywright(seed=42) as browser:
        return await asyncio.gather(*(worker(browser, u) for u in urls))

asyncio.run(main([f"https://example.com/page/{i}" for i in range(20)]))
```

This is genuinely faster, and for a site that only counts requests per address it is fine.
But every one of those twenty pages reports the **same** canvas hash, the same GPU, the
same fonts, the same audio profile and the same screen, because they are contexts inside
one browser process and a context isolates storage, not hardware. That is the whole subject
of [what a proxy per context does not isolate](playwright-proxy-per-context.md): five
contexts on five proxies are one machine appearing from five countries at once, and the
constant fingerprint links them to each other.

You can measure the sharing directly. Point each worker at a linkability probe and read the
[FingerprintJS](fingerprintjs-visitor-id.md) visitor ID it computes: eight workers under one
seed came back with **one** visitor ID, eight times over. The site did not see eight
visitors. It saw one visitor open eight tabs.

## One identity per worker: seed plus exit

A separate identity is a separate machine, and a separate machine here is a separate
launch. Each `InvisiblePlaywright` launch derives its full fingerprint from its seed and
resolves its timezone, locale and WebRTC exit address from its own proxy, once, at launch.
So the unit of parallelism is not the context, it is the browser:

```python
import asyncio
from invisible_playwright.async_api import InvisiblePlaywright

async def fetch(seed, proxy, url):
    async with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        return await page.title()

# distinct seed AND distinct exit per worker
jobs = [
    (42, {"server": "socks5://gate.example.com:1080", "username": "u1", "password": "p1"}),
    (99, {"server": "socks5://gate.example.com:1080", "username": "u2", "password": "p2"}),
    (7,  {"server": "socks5://gate.example.com:1080", "username": "u3", "password": "p3"}),
]

async def main(urls):
    coros = [fetch(seed, proxy, url) for (seed, proxy), url in zip(jobs, urls)]
    return await asyncio.gather(*coros)
```

Re-run the linkability probe against this and the eight-workers-on-eight-seeds case returns
**eight distinct visitor IDs**, one per worker, because canvas, GPU, font set and audio
profile now come from eight different seeds. Passing a seed also means the run is
reproducible: if worker three gets blocked, you relaunch seed 7 behind the same exit and get
the identical machine back, which is the difference between debugging and guessing.

The two knobs are independent and both matter. A distinct seed with a **shared** exit is
several machines behind one address, which is its own velocity signal. A shared seed with
distinct exits is the naive case above: one machine in several countries. The identity holds
only when the seed and the exit vary together.

## Fanning workers out with asyncio.gather

[`asyncio.gather`](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather)
schedules the coroutines onto one event loop and lets their network waits
overlap; it does not use threads, so nothing here needs a lock. Two mechanical details
decide whether the fan-out behaves under load.

**Do not let one failure sink the batch.** By default `gather` propagates the first
exception and abandons the rest. Pass `return_exceptions=True` and inspect the results, so a
single timeout does not discard the nineteen pages that succeeded:

```python
results = await asyncio.gather(*coros, return_exceptions=True)
ok  = [r for r in results if not isinstance(r, Exception)]
bad = [r for r in results if isinstance(r, Exception)]
```

**Match the identity pool to the work.** If you have twelve exits and a thousand URLs, you
are not running a thousand identities; you are reusing twelve. Assign each URL to a
(seed, proxy) worker and let that worker drain its share sequentially, so the same machine
keeps the same address across the pages it visits, the way a real session does.

## Bounding concurrency so the fan-out is not the tell

Launching a thousand coroutines at once is both a local resource problem and a remote
signal. Every launch is a real browser process, and a thousand of them will exhaust memory
long before the network does. From the site's side, a wall of simultaneous first-requests is
itself the pattern worth flagging.

Bound both with an
[`asyncio.Semaphore`](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Semaphore),
and space the work inside each worker:

```python
import asyncio, random
from invisible_playwright.async_api import InvisiblePlaywright

async def fetch(sem, seed, proxy, url):
    async with sem:                       # at most N launches live at once
        async with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            await asyncio.sleep(random.uniform(0.5, 2.0))   # not all at once
            return title

async def main(work):
    sem = asyncio.Semaphore(6)            # tune to your RAM and your exit count
    coros = [fetch(sem, seed, proxy, url) for seed, proxy, url in work]
    return await asyncio.gather(*coros, return_exceptions=True)
```

Pick the semaphore size from whichever ceiling you hit first: available memory, or the number
of distinct exits you actually have. There is no point running twelve concurrent workers
through six exits, because six of them are sharing an address and you are back to the shared
identity the whole page is about. Bounding concurrency and pacing requests is the same
discipline as [rotating proxies deliberately rather than per request](how-to-rotate-proxies-playwright.md),
and it composes cleanly with ordinary [pagination](how-to-scrape-paginated-pages-playwright.md):
one worker owns a page range, drains it in order behind one identity, and the workers run
side by side.

## Conclusion

The concurrency is the easy half: `asyncio.gather`, `return_exceptions=True`, and a
semaphore to keep the machine and the site from being overwhelmed at once. The half that
decides the outcome is that each concurrent worker has to be a distinct identity, which means
a distinct seed and a distinct exit moving together, one launch per worker.

Get that wrong and parallelism is a net loss: you have taken the one thing a linkable
fingerprint most wants to see, a single machine, and shown it doing many things at the same
instant. Get it right and N workers are N people, which is the only version of parallel
scraping that survives a site that keeps score.

## Short answers to the questions that lead here

**How do I run Playwright pages in parallel in Python?** Use `asyncio.gather` over
coroutines on one event loop. The network waits overlap without threads. Add
`return_exceptions=True` so one failure does not discard the batch.

**Can I just open many contexts in one browser?** For throughput, yes. For distinct
identities, no: contexts share the browser process, so they report the same canvas, GPU,
fonts and audio. That is one machine, not many.

**Why is running everything at once making detection worse?** Because simultaneous requests
that share a fingerprint or an address are a velocity signal. Concurrency without distinct
identities looks like one machine pretending to be a crowd.

**How many workers should I run at once?** Bound it with a semaphore to whichever you hit
first: your RAM, or the number of distinct exits you have. More concurrent workers than
exits just means some of them share an address.

**Do I need a different proxy for every worker?** You need the seed and the exit to vary
together. A distinct fingerprint behind a shared IP, or a shared fingerprint behind distinct
IPs, is still one linkable identity.

**How do I reproduce a worker that failed?** Relaunch with that worker's seed behind the same
exit. The seed fixes the whole fingerprint, so the failing machine comes back identical for a
clean bisect.

## Sources

- Python's [`asyncio.gather`](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather)
  and its `return_exceptions` behaviour, and
  [`asyncio.Semaphore`](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Semaphore)
  for bounding concurrency.
- Playwright's [context model](https://playwright.dev/python/docs/browser-contexts), in which a
  `BrowserContext` isolates storage and the proxy but not the hardware the browser reports.
- This project's per-launch identity: fingerprint derived from the seed, and timezone, locale
  and exit address resolved once per launch from that launch's proxy.

**See also:** [what a proxy per context does not isolate](playwright-proxy-per-context.md),
[rotating proxies without creating a pattern](how-to-rotate-proxies-playwright.md), and
[when the timezone does not match the proxy](timezone-proxy-mismatch.md), which is the
mismatch a shared launch most reliably creates.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The parallelism is the easy
part; the reason it usually backfires is that every worker was quietly the same machine.*
