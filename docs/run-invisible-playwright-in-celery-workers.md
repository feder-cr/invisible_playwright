---
title: "Run invisible_playwright in Celery task workers"
description: "Run invisible_playwright in Celery workers: one browser reused across tasks, seed as task argument for reproducible retries, worker concurrency read as request rate."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 85
---


# Run invisible_playwright in Celery task workers

Running invisible_playwright inside Celery means launching one browser per
worker process at startup and reusing it for every task, opening and closing
a page per task, passing the seed as a task argument so retries stay
reproducible, and tuning worker concurrency down as a request-rate knob rather
than up as a throughput dial.

Putting a browser behind a task queue is a good idea for the wrong reasons and
a good idea for the right ones. The wrong reason is that a queue will hide a
detection problem: it will not. The right reasons are pacing, retries and
backpressure, and those are real. This page is how to wire invisible_playwright
into Celery so the browser is fast, the retries are reproducible, and the
concurrency setting means what you think it means.

The one mistake that dominates every other: launching a browser per task. Do
that and the launch dominates the runtime and the worker falls over. The whole
page is really about not doing that.

## Launch one browser per worker process, not per task

A Firefox launch is expensive. For a task that fetches one page and extracts a
few fields, the launch can cost more than the work, sometimes several times
more. Multiply that by every task in the queue and the worker spends its life
starting and stopping browsers instead of doing the job.

The pattern that works is one browser per **worker process**, started when the
process starts and reused by every task that process runs. Celery gives you a
signal for exactly this, `worker_process_init`, which fires once inside each
forked worker process:

```python
# tasks.py
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from invisible_playwright import InvisiblePlaywright

app = Celery("scraper", broker="redis://localhost:6379/0")

_browser = None
_ctx = None  # the InvisiblePlaywright context manager, kept open for the process

@worker_process_init.connect
def start_browser(**_):
    global _browser, _ctx
    _ctx = InvisiblePlaywright(
        seed=42,
        proxy={"server": "socks5://gate.example.com:1080",
               "username": "u", "password": "p"},
    )
    _browser = _ctx.__enter__()  # returns a real Playwright Browser

@worker_process_shutdown.connect
def stop_browser(**_):
    global _ctx
    if _ctx is not None:
        _ctx.__exit__(None, None, None)
        _ctx = None
```

The `_browser` here is a real `playwright.sync_api.Browser`. Every method you
know from stock Playwright works unchanged, which is the point of the wrapper:
the only thing that differs from plain Playwright is the two lines that launch
it. See [the quickstart](quickstart.md) for the before/after diff.

A note on the fork model: create the browser in `worker_process_init`, not in
`worker_init` or at import time. A browser handle created in the parent and
inherited across a fork is a subprocess owned by the wrong process, and it will
misbehave. Each worker process must launch its own.

## Give each task its own page, and keep the browser alive

With the browser owned by the process, a task's job is to open a page, do its
work, and close the page. Open a fresh page (or a fresh context) per task so
one task's cookies and open tabs do not leak into the next:

```python
@app.task(bind=True, max_retries=3)
def scrape(self, url):
    page = _browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.click("#load-more")   # mouse arcs to the target on a Bezier curve
        return page.inner_text("#result")
    finally:
        page.close()
```

The `goto` call above waits for
[`domcontentloaded`](https://playwright.dev/python/docs/api/class-page#page-goto)
rather than a full page load, which is enough for most scrapes and keeps the
per-task work fast.

`new_page()` is cheap; `launch()` is not. This is the whole trade. The browser
is the expensive, long-lived resource and it stays up for the life of the
worker process; the page is the cheap, per-task resource and it comes and goes
with the task.

If a task does several things across a first-party site, prefer one context
with several pages inside a single task over spreading them across tasks, so
the session stays coherent. A context torn down and rebuilt between related
requests reads as two visitors, not one.

## Pass the seed as a task argument so retries replay the same identity

invisible_playwright derives the entire fingerprint from a seed. The same seed
gives the same GPU, canvas hash, audio context, fonts and screen every run.
That is a gift to a queue, because a queue's whole job is to run a task more
than once: retries, replays from the dead-letter queue, a task re-enqueued by
hand a day later.

If the seed lives only in the worker's startup code, a retry that lands on a
different worker process gets a different identity, and a bug you saw once is
now unreproducible. Pass the seed **as a task argument** instead, so it travels
with the task through the broker and comes back identical on every retry:

```python
@app.task(bind=True, max_retries=3)
def scrape_with_seed(self, url, seed):
    # a per-task browser is the exception, not the rule - use it only when a
    # task genuinely needs an identity different from the worker's default
    with InvisiblePlaywright(seed=seed, proxy=PROXY) as browser:
        page = browser.new_page()
        page.goto(url)
        return page.inner_text("#result")
```

For most workloads you reuse the process-level browser and its fixed seed, and
you pass the seed only so the failing task is reproducible. When a task does
need its own identity, the seed argument gives you a per-task browser whose
identity is stable across every retry of that task. Either way, the rule is the
same: the seed is data that belongs to the task, not to the worker. Log it with
the result and a failure three days later is a bisect, not a guess. That is the
same reproducibility argument that makes [retrying failed requests](how-to-retry-failed-requests-playwright.md)
worth doing carefully rather than blindly.

## Read worker concurrency as your request rate

Here is the part that catches people. A queue gives you retries, rate control
and backpressure, and all three are genuinely useful. What a queue does **not**
give you is a better IP reputation, more headroom under a per-account quota, or
a higher per-site rate limit. Those are properties of the network and the
account, and the queue cannot move them.

So the concurrency setting is not a throughput dial you turn up until the CPU
is busy. It is, effectively, your request-rate knob. Eight worker processes
each holding a browser and each pulling tasks is eight roughly-concurrent
sessions against the target, and if they share an exit they share one address's
reputation budget between them. Tune concurrency as a rate, and tune it down,
not up:

```bash
# one browser per process; concurrency IS your concurrent-session count
celery -A tasks worker --concurrency=4 --prefetch-multiplier=1
```

`--prefetch-multiplier=1` stops a single worker from reserving a pile of tasks
it will run back to back with no gap, which is the velocity signal you were
trying to avoid. If you need real spacing between requests, put it in the pace,
not just the count: see [rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for token-bucket pacing that a queue's concurrency alone will not give you.

## The honest part: what this does and does not fix

invisible_playwright is designed to look like a real browser driven by a real
person, and that is why it clears most detection checks: the fingerprint, the
TLS handshake and the driver layer read as a genuine Firefox rather than as
automation. Running it inside Celery changes none of that for better or worse -
the browser in the worker is the same browser you would launch by hand.

What the queue does not touch, and what you still have to supply:

- **IP reputation.** A clean fingerprint on a known datacenter exit still loses.
  The queue moves tasks around; it does not move addresses onto a better list.
- **Per-account quotas and per-site rate limits.** Twenty workers do not raise a
  limit that is counted per account or per address. They spend it faster.
- **Behaviour and timing.** Uniform intervals and instant form fills are their
  own tell, and a fleet of workers all pulling at once makes the pattern
  sharper, not softer.

The queue is for pacing, retries and backpressure. The clean proxy and the
human pacing are still yours to bring. None of that is a bypass of anything, and
anyone selling a queue as one is selling the wrong thing.

## Conclusion

Launch one browser per worker process in `worker_process_init` and reuse it;
open and close a page per task; pass the seed as a task argument so a retry
rebuilds the same identity; and read your concurrency setting as the request
rate it actually is. Do that and Celery gives you exactly what a queue is good
for - pacing, retries, backpressure - without pretending to fix the IP,
account and behaviour problems that live outside the browser.

## Short answers to the questions that lead here

**Should I launch a browser per Celery task?** No. The launch dominates the
runtime of a short task and exhausts the worker. Launch one browser per worker
process at startup and reuse it; open a page per task.

**Where do I create the browser?** In a `worker_process_init` handler, so each
forked worker process gets its own. A browser inherited across a fork belongs to
the wrong process and misbehaves.

**How do I make a retried task reproducible?** Pass the seed as a task argument.
It travels with the task through the broker, so every retry rebuilds the exact
same fingerprint instead of drawing a new one.

**Does a queue help me get past detection?** It helps you pace requests, retry
cleanly and apply backpressure. It does not change IP reputation, account quotas
or your behaviour, so on its own it does not get you past anything.

**What should I set concurrency to?** Treat it as your concurrent-session count
against the target, not as a CPU dial. Tune it as a request rate, usually down,
and pair it with `--prefetch-multiplier=1`.

**Do I still need a proxy if I run in Celery?** Yes. The worker changes nothing
about the exit address, and a real-looking browser on a bad IP still loses.

## Sources

- The invisible_playwright [quickstart](quickstart.md) and
  [configuration](configuration.md) pages for the real launch API, the seed
  argument and proxy handling used in the examples above.
- [Playwright's `goto` API reference](https://playwright.dev/python/docs/api/class-page#page-goto)
  for the `wait_until` options, including `domcontentloaded`, used in the
  task example above.
- Celery's worker signal model (`worker_process_init` /
  `worker_process_shutdown`) and its concurrency and prefetch settings.
- This project's own measurements that a Firefox launch per short task dominates
  the task runtime, which is the reason the process-level pattern exists.

**See also:** [retrying failed requests](how-to-retry-failed-requests-playwright.md)
for making a replay reproducible, and [rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for pacing the queue's concurrency reads as a request rate.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The one-browser-per-process
rule is here because launching one per task is a mistake that looks fine until the queue fills up.*
