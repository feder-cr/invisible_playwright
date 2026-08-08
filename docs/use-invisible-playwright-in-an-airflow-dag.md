---
title: "Use invisible_playwright in an Airflow DAG"
description: "Run a patched Firefox under stock Playwright from an Airflow @task or PythonOperator: one browser per task run, seed stored as a param for reproducible retries."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 88
---


# Use invisible_playwright in an Airflow DAG

Airflow is a scheduler, not a browser. It gives you retries, dependencies and a
calendar; it does not touch the fingerprint your browser presents or the address your
requests leave from. So the useful question is not "does invisible_playwright work with
Airflow" - it does, it is ordinary Python - but "where in a DAG does the browser go, and
what does the orchestrator change about detection". The short answer to the second half
is: nothing. Detection is still handled entirely by the engine and your proxy choice.

This page is the shape that works: one browser launched inside one task, the seed carried
as a task parameter so a retry reproduces the same identity, and the honest line about
what the scheduler cannot fix for you.

## Where invisible_playwright fits in a DAG

The natural unit is a single task - a `PythonOperator`, or a `@task` under the TaskFlow
API - that launches one browser, extracts its rows, and hands them onward, either to a
database or to the next task through XCom. Switching from plain Playwright is the same
two-line change it is anywhere else:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")
    rows = [el.inner_text() for el in page.query_selector_all(".row")]
```

The `browser` here is a real `playwright.sync_api.Browser`. Every method you already use
works unchanged, so nothing about the extraction logic is Airflow-specific. What is
Airflow-specific is *when* that block runs, and that is the whole game.

## Never launch the browser at module import

Keep the browser launch inside the task body - a `PythonOperator` callable or a `@task`
function - never at module level in the DAG file. Airflow re-imports every DAG file
repeatedly to build the schedule (the scheduler on a short interval, and the webserver
too), so any code sitting at module level runs on every one of those parses, not once per
scheduled run.

If you launch a browser at the top of the file, you are not launching it once per
scheduled run; you are launching one on every scheduler heartbeat, downloading the engine
on the first parse of a fresh worker, and blocking the parser while a browser starts. The
DAG becomes slow to import, imports time out, and the task has not even been scheduled yet.

Here is what that looks like in practice:

```python
from datetime import datetime
from airflow.decorators import dag, task


@dag(
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    params={"seed": 42},
)
def daily_extract():

    @task
    def extract_rows(**context):
        # Import and launch INSIDE the task. At module level this would fire
        # on every scheduler parse of the file, not once per run.
        from invisible_playwright import InvisiblePlaywright

        seed = context["params"]["seed"]
        proxy = {
            "server": "socks5://gate.example.com:1080",
            "username": "u",
            "password": "p",
        }

        rows = []
        with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
            page = browser.new_page()
            page.goto("https://example.com/listing")
            for el in page.query_selector_all(".row"):
                rows.append(el.inner_text())
        return rows

    @task
    def load_rows(rows):
        # Push onward: write to a DB, or return for the next task.
        for row in rows:
            print(row)

    load_rows(extract_rows())


daily_extract()
```

Even the `import invisible_playwright` line is inside the function. That keeps the import
cost off the parse path entirely, which matters on a worker that has to fetch the engine
the first time it runs the task.

## Seed as a task parameter, for reproducible retries

Store the seed as a DAG parameter, as the example above does with `params={"seed": 42}`,
so every run and every retry reads the same seed and produces the same identity - the
same GPU, the same canvas hash, the same fonts, the same screen, run after run. That
matters because Airflow retries a failed task as a fresh process with a fresh random draw
by default: for a browser that generates a distinct fingerprint per session, a retry
without a pinned seed looks like a *different machine* than the attempt that failed, and
when you are debugging a block that is the opposite of what you want - you cannot tell the
site changing from the machine changing.

A retry then replays the exact browser that failed, and a manual re-run with the same
parameter reproduces it too. If you want each scheduled day to have its own stable
identity instead, derive the seed from the logical date (`data_interval_start`) so that
today is reproducible while tomorrow differs deliberately rather than by accident.

That reproducibility is also why a bisect is a bisect: change the seed on purpose, never
by side effect. The same reasoning, at the level of a single run, is in the
[checklist for when Playwright is detected on one site](playwright-detected-as-bot.md).

## What the orchestrator does not do

Airflow changes nothing about detection: it supplies retries, dependencies and a
schedule, not a fingerprint or an IP address. It is easy to assume the scheduler is doing
more than it is, so here is the honest boundary.

invisible_playwright is built to look like a real Firefox driven by a real person, and
that is *why* it passes most detection checks: the fingerprint, the TLS handshake and the
driver layer read as a genuine Firefox rather than an automated one. That covers the
browser-shaped tells. It does not, on its own, cover:

- **IP reputation.** Airflow runs your task from wherever the worker lives, typically a
  datacenter range that is already known. A perfect browser on a flagged address still
  loses. You supply the clean exit - pass a proxy to the task, as above, and see
  [Configuration](configuration.md) for the schemes and how the timezone follows the exit.
- **Per-account quotas and rate limits.** The scheduler makes it trivial to run a task
  every five minutes across a hundred accounts. That velocity is itself a signal, and no
  amount of fingerprint realism hides it.
- **Behaviour and timing.** Uniform request intervals, a form filled in eighty
  milliseconds, a burst of tasks firing at the same second of every hour. Space the runs
  out and pace the actions; the engine draws a human-shaped mouse path, but it cannot make
  a cron-perfect schedule look human.

The orchestrator gives you retries, dependencies and scheduling. It touches neither the
fingerprint nor the IP. Detection is handled by the engine and by the exit you choose, and
the two you own - a clean proxy and human pacing - are exactly the two Airflow will not
supply for you. Before you trust any of this in production, measure it the way
[the testing guide](how-to-test-bot-detection.md) describes: through the proxy you deploy
with, on the machine that runs the worker, more than once.

## Conclusion

The integration is boring in the best way: it is one task that opens a browser, reads
rows, and passes them on. The two things that make it robust are Airflow-specific and
small. Launch the browser inside the task, never at module import, or it fires on every
parse. Carry the seed as a parameter, so a retry reproduces the identity instead of
rolling a new one. Everything else - the retries, the DAG dependencies, the calendar - is
Airflow doing its job, and none of it changes what the site sees. That part is still the
engine and your proxy.

## Short answers to the questions that lead here

**Where do I put the browser launch in a DAG?** Inside the task body - a `PythonOperator`
callable or a `@task` function - never at module level, because Airflow re-imports the DAG
file on every scheduler parse and a module-level launch fires on each one.

**Why does my DAG import time out with a browser in it?** Because the launch (and often an
engine download) is running at parse time. Move both the import and the `with
InvisiblePlaywright(...)` block inside the task so they run only on execution.

**How do I make retries reproducible?** Store the seed as a DAG param and read it in the
task. Same seed, same identity, so a retry replays the browser that failed instead of
generating a new machine.

**Does Airflow make my scraper harder to detect?** No. It schedules and retries; it does
not touch the fingerprint or the IP. Detection realness comes from the engine, and the
exit comes from the proxy you pass.

**Can I run many tasks in parallel this way?** Yes, but the parallelism is a velocity
signal against per-account quotas and rate limits. Give each its own clean exit and space
them out; the scheduler will happily create the exact burst a site is watching for.

**Sync or async in a task?** Either. The sync API is simplest inside an ordinary
`@task`. Use `invisible_playwright.async_api` only if the task is already running an event
loop.

## Sources

- This project's [Quickstart](quickstart.md) and [Configuration](configuration.md) pages
  for the real launch API, proxy dict and timezone behaviour.
- Airflow's own documentation on top-level DAG code and why it runs on every parse, which
  is the reason the launch belongs inside the task.
- Playwright's own documentation on the [sync vs async
  API](https://playwright.dev/python/docs/library), for when `async_api` is the right
  choice inside a task that already runs an event loop.
- This project's release gates and the detection-testing method, for what the browser
  covers and what the proxy and pacing still have to.

**See also:** [scrape straight into a database](how-to-scrape-into-a-database-playwright.md)
for what the load task writes to, and
[resume an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md) for
making a re-run pick up where the last one stopped.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The module-import gotcha is
a mistake I have watched more than one pipeline make.*
