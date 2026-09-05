---
title: "Keep Playwright Firefox memory flat on long runs"
description: "Playwright Firefox memory grows from unclosed contexts, a list holding every result, or one context reused too long, not a Python leak. How to measure it."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 27
---


# Keep Playwright Firefox memory flat on long runs

Playwright Firefox memory grows for four ordinary reasons: contexts and pages that
never get closed, listeners piling up on a page you keep reusing, response data held
in a Python list across thousands of requests, and one context accumulating state
across a long run. Close what you open, stream what you collect, and restart the
browser on a page budget.

## A four-hour crash is rarely a Python leak

A scraper that dies after a few hours almost never has a Python-side memory leak.
Python's own objects, the strings, the dicts, the lists you build, get
garbage-collected the same way they would in any long-running script. What actually
grows is the browser process the scraper is driving, and the growth follows a small
number of predictable causes rather than a mystery leak in the language itself.

## What actually grows over a long run

Four causes account for almost all of it, and each has a fix that costs little once
you know to look for it:

| What grows | Why | The fix |
|---|---|---|
| Pages and contexts never closed | Each one keeps its own DOM, JS heap, and network cache alive until closed | Close every context (and page) in a `finally`, one context per unit of work |
| Listeners and console handlers | `page.on("console", ...)` and similar handlers accumulate if a new one is attached on every page without removing the old | Attach handlers once per page, not once per navigation, or remove them before reusing the page |
| Response bodies held in memory | Appending every scraped record to a list that lives for the whole run | Stream each result to disk as you get it; keep only the current record in memory |
| One context reused across thousands of navigations | Cookies, cache entries, and storage accumulate in a context that never gets replaced | Restart the browser (or the context) on a page budget rather than running one context forever |

## One context per unit of work, closed in a finally

A context is cheap to create and close, and it is the natural unit for one page, one
form, one product to scrape. Closing it in a `finally` guarantees the close runs even
when the scrape raises, which matters because a skipped `context.close()` is exactly
how a long run accumulates hundreds of half-closed contexts unnoticed.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    for url in urls:
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(url)
            save_result(page.locator("h1").inner_text())
        finally:
            context.close()
```

Running several of these loops at once multiplies the same discipline across
contexts rather than replacing it; see
[scraping multiple pages in parallel](how-to-scrape-multiple-pages-in-parallel-playwright.md)
for how the parallel version still has to close what it opens.

## Stream results to disk instead of holding them in a list

A list that grows by one entry per page, across a run touching tens of thousands of
pages, does exactly what a leak does: grows without bound for the life of the
process. Holding the whole run's output in memory when you only need the current
record is the actual mistake, not the list itself.

```python
import json

with open("results.jsonl", "a") as f:
    for url in urls:
        row = scrape_one(url)
        f.write(json.dumps(row) + "\n")
        # nothing is kept in a Python list across iterations
```

Appending to a file is one write call per record and nothing left to
garbage-collect later. If a downstream step needs the whole dataset, read the file
back once the run is done rather than keeping it live during the run.

## Restart the browser on a page budget

Some growth in a context is not a bug at all, it is cache and storage the site
itself wrote, and it is honest for that to grow across thousands of navigations in
the same context. The fix is not to chase it field by field: it is to stop asking
one context to live forever.

```python
from invisible_playwright import InvisiblePlaywright

PAGES_PER_BROWSER = 500

def run_batch(urls):
    with InvisiblePlaywright(seed=42) as browser:
        for url in urls:
            context = browser.new_context()
            try:
                page = context.new_page()
                page.goto(url)
                scrape_one(page)
            finally:
                context.close()

for i in range(0, len(all_urls), PAGES_PER_BROWSER):
    run_batch(all_urls[i:i + PAGES_PER_BROWSER])
```

Exiting the `with` block closes the browser process outright, and the next batch
starts clean. Pick the budget from how the run behaves, not a round number pulled
from nowhere, and weigh it against launch cost; see
[the launch timeout budget](slow-browser-launch-timeout-budget.md) before restarting
so often that launching becomes the bottleneck.

## Measure the browser's own process tree, not your script's

A Python-side memory profiler tells you nothing about the browser process
Playwright is driving. The browser is a separate process, several, on Firefox, and
its memory does not show up in a Python heap profiler at all. Measure the operating
system's view of the process tree instead.

```python
import psutil

before = {p.pid for p in psutil.process_iter()}

# ... launch the browser and run the workload here ...

def browser_rss_mb(exclude_pids):
    total = 0
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.pid not in exclude_pids and "firefox" in (proc.info["name"] or "").lower():
                total += proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return total / (1024 * 1024)

print(f"{browser_rss_mb(before):.0f} MB in this run's browser processes")
```

Two details in that function are the difference between a number and a fiction, and we
learned the second one by getting it wrong. Filtering on the process name keeps unrelated
children out of the sum. Excluding the pids that already existed keeps out **every other
browser on the machine**, including the one a previous run left behind: our own first
attempt at this measurement reported a baseline of nearly a gigabyte for a single page,
because a browser from the previous arm of the same script had not finished exiting when
the next arm started sampling. The number was real, the attribution was nonsense.

The practical rule that follows: sample the pid set immediately before you launch, never
compare two arms that ran back to back in one process without checking that the first
one's browser is gone, and treat any absolute figure from a machine doing other work as
unusable. Relative growth on an idle machine is the only version of this measurement
worth acting on.

Snapshot the full set of running pids before you launch anything, not just the pid
your launch call returns. On Windows, Firefox starts through a short-lived launcher
process that spawns the real browser and exits within a fraction of a second;
reading children from that pid right after launch can catch the launcher already
gone. Diffing the full pid set before and after sidesteps that, and the same
approach applies when
[several browsers run concurrently](run-invisible-playwright-concurrently-asyncio.md).

## A real leak keeps climbing; ordinary allocator behaviour plateaus

A single reading tells you the process is using some number of megabytes and
nothing about whether that number is a problem. Take the same measurement at
several checkpoints spaced across the run and look at the shape across those
checkpoints, not any one of them.

An allocator that holds onto freed memory for reuse, rather than returning it to
the OS immediately, produces a step up followed by a plateau. A genuine leak keeps
climbing roughly in proportion to pages processed, checkpoint after checkpoint,
with no point where it levels off. The exact numbers depend on the machine, so
treat the shape of the trend as the signal, not any specific figure.

## Conclusion

Most Playwright Firefox memory problems trace back to state nobody closed, not a
leak in the language. Close every context in a `finally`, stream results to disk
instead of a growing list, and restart the browser on a measured page budget. When
you measure, read the operating system's process tree, snapshotted before launch
and diffed after, and judge growth by its shape across checkpoints, not one number.

## Short answers to the questions that lead here

**Why does my Playwright script's memory keep growing over a long run?** Almost
always contexts or pages that never get closed, a list holding every result, or one
context accumulating cache and storage across thousands of navigations, not a leak
in Python itself.

**How do I measure how much memory a Playwright browser is really using?**
Snapshot the full set of running process ids before you launch, diff against the
full set again after the workload runs, and sum the RSS of whatever is new. A
Python profiler will not see the browser process.

**Why does psutil miss part of the Firefox process tree?** Firefox on Windows
starts through a launcher process that spawns the real browser and exits almost
immediately. Reading children from the launcher's pid right after launch can catch
it already gone.

**Should I restart the browser periodically to control memory?** Yes, once a
context has handled a page budget you have measured, rather than letting one
context run forever. Exiting the browser's `with` block and starting a new one is
enough.

**How do I tell a real memory leak from normal browser behaviour?** Measure at
several checkpoints across the run. A leak keeps climbing roughly in proportion to
work done; ordinary allocator behaviour rises once and then plateaus.

**See also:** [scraping multiple pages in parallel](how-to-scrape-multiple-pages-in-parallel-playwright.md),
[running invisible_playwright concurrently with asyncio](run-invisible-playwright-concurrently-asyncio.md),
and [the browser launch timeout budget](slow-browser-launch-timeout-budget.md).

## Sources

- Playwright documentation, [BrowserContext.close()](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-close) and [Browser.close()](https://playwright.dev/python/docs/api/class-browser#browser-close).
- psutil documentation, [process_iter() and Process.memory_info()](https://psutil.readthedocs.io/en/latest/#psutil.process_iter).
- psutil, https://psutil.readthedocs.io/ - `process_iter()` and `Process.memory_info()`,
  used exactly as shown above. The code in this page was run on 5 September 2026; the
  absolute megabyte figures from that run are deliberately not quoted, because the bench
  was not clean enough to attribute them, which is the point the measurement section
  makes.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The memory discipline
here is the same discipline any long-running browser automation needs; nothing
about it is specific to this engine.*
