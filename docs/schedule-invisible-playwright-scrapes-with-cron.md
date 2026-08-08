---
title: "Schedule invisible_playwright scrapes with cron"
description: "Schedule invisible_playwright with cron or APScheduler. A job firing at the same clock minute every day is detectable; jitter schedule and pacing to avoid patterns."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 86
---


# Schedule invisible_playwright scrapes with cron

Putting a scraper on a timer is the easy part. The scheduler you reach for - cron,
a systemd timer, APScheduler inside a long-running process - triggers the run, and
the code it triggers is ordinary invisible_playwright. Nothing about being scheduled
changes how a session looks to the page.

That is exactly the trap. A session can be fingerprint-perfect, driven through a
clean exit, and still stand out because of *when* it happens. A job that fires at the
same clock minute every day, or every five minutes on the dot, produces a fixed
access rhythm that is readable server-side no matter how real each individual visit
is. This page shows the real API for the run, then spends most of its length on the
part the scheduler adds and the part it cannot fix.

## The scheduler triggers the run, it does not disguise it

Keep two axes separate in your head:

- **Fingerprint** is what a single session looks like: the GPU string, the fonts,
  the canvas hash, the TLS handshake. This is what invisible_playwright is built to
  get right, which is why a session reads as a genuine Firefox to the common
  in-page and driver-layer checks.
- **Cadence** is the pattern across sessions over time: how often you arrive, at
  what times, how evenly spaced the requests are, how long each visit lasts. No
  fingerprint work touches this. It is set entirely by your scheduler and your
  pacing code.

cron sits on the second axis and does nothing at all for the first. A perfect
disguise worn at 03:00:00 every single night is still a thing that arrives at
03:00:00 every single night, and that regularity is itself a signal. Treat the two
axes as two separate jobs, because they are.

## A minimal scheduled run using the real API

The run itself is the same two-line launch as any other invisible_playwright script.
Pass a seed if you want the same machine every time, or omit it for a fresh identity
per run. Here is a self-contained job function:

```python
# scrape_job.py
from invisible_playwright import InvisiblePlaywright

PROXY = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

def run_once():
    with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
        page = browser.new_page()
        page.goto("https://example.com/listings")
        # browser is a real Playwright Browser - every method works as documented
        items = page.locator(".item").all_inner_texts()
        return items

if __name__ == "__main__":
    for row in run_once():
        print(row)
```

The `browser` object is a real [`playwright.sync_api.Browser`](https://playwright.dev/python/docs/api/class-browser),
so anything you already do with Playwright works unchanged. There is no
scheduler-specific API to learn: you wrap `run_once()` in whatever trigger you like.

Whether to reuse `seed=42` (one stable identity that returns each run) or drop the
seed (a distinct identity per run) is a real decision. A recurring job that always
presents the *same* fingerprint from the *same* exit is easy to link across days; a
job that presents a brand-new machine every fifteen minutes from one account can look
just as odd. Pick deliberately, and log `browser` runs the way the
[quickstart shows for replaying a seed](quickstart.md).

## Cadence is a detection axis of its own

Two scrapers can share an identical, flawless fingerprint and still be trivially
separable server-side, because timing alone is enough to tell them apart. Imagine one
running from a shell script that a human happens to launch at scattered moments
through the day, and the other running from:

```
*/5 * * * *  python /srv/scrape_job.py
```

Server-side, the second one is trivially separable from the first. Its requests land
at :00, :05, :10, :15 with sub-second precision, forever. Nothing human produces a
timestamp histogram with five sharp spikes an hour and nothing in between. The
fingerprint can be beyond reproach and the *arrival pattern* alone is enough to
bucket the traffic as automated and treat it accordingly.

This is not hypothetical fussiness. Fixed-interval access is one of the cheapest
things to measure on the receiving end - it needs no JavaScript, no challenge, just
timestamps you were already logging. It survives a proxy rotation, because rotating
the exit IP does not change *when* the requests come. And it is invisible to every
in-page test you can run against yourself: [sannysoft, CreepJS, BotD and BrowserLeaks
all read one moment](how-to-test-bot-detection.md) and have nothing to say about the
shape of a week.

## Jitter the schedule and vary the pacing

The fix is to stop being metronomic on purpose, on two levels.

**Level one: jitter the trigger.** Do not fire on the exact minute. Add a random
offset so the same job lands at a different time each run. With plain cron, schedule
an outer window and sleep a random amount inside it:

```bash
# crontab: wake once an hour, then wait a random slice of that hour before working
0 * * * *  sleep $(( RANDOM \% 1800 )); python /srv/scrape_job.py
```

That spreads the actual work across the first 30 minutes of the hour instead of
pinning it to :00. If you drive the schedule from a long-lived Python process,
APScheduler has a built-in `jitter` for exactly this:

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from scrape_job import run_once

sched = BlockingScheduler()
# every hour, but +/- up to 900s of random slack so it never lands on the same second
sched.add_job(run_once, "interval", hours=1, jitter=900)
sched.start()
```

**Level two: vary the pacing inside the run.** A run that always visits the same
number of pages in the same order with the same gaps is regular in a second way.
Randomise the per-request spacing and, where it fits your task, the amount of work:

```python
import random
import time
from invisible_playwright import InvisiblePlaywright

def run_once(urls):
    with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
        page = browser.new_page()
        for url in urls:
            page.goto(url)
            # human-scale, uneven gaps instead of a fixed cadence
            time.sleep(random.uniform(3.0, 11.0))
```

The two levels answer different observers: schedule jitter breaks the day-scale
histogram, pacing jitter breaks the within-session rhythm. Both are cheap, and both
are pure behaviour - the engine does not and cannot set them for you. For the general
version of the second level, see
[how to rate limit your scraper](how-to-rate-limit-your-scraper-playwright.md), and
if the job re-fetches the same pages on every tick, [fetching only what changed](how-to-scrape-only-new-items-incremental-playwright.md)
cuts the request volume that a cadence check is counting in the first place.

## What the scheduler still does not fix

Being honest about the boundary is the whole point. invisible_playwright makes a
single session read like a real browser driven by a real person, and jitter makes the
timing read like one too. Neither of those touches:

- **IP reputation.** A datacenter exit is a datacenter exit at every hour of the day.
  The scheduler decides *when* you arrive, not *from where*. You still supply a clean
  proxy; see [configuration](configuration.md) for how the exit and the browser
  timezone have to agree, and [timezone and proxy mismatches](timezone-proxy-mismatch.md)
  for what goes wrong when they do not.
- **Per-account quotas and rate limits.** If an endpoint allows N requests per hour
  per account, jitter changes the spacing but not the total. Ten well-spaced requests
  still spend ten of the budget.
- **Volume itself.** Spreading a thousand daily requests evenly is still a thousand
  daily requests. Cadence shaping hides the *pattern*, not the *amount*.
- **Everything on the [detection checklist](playwright-detected-as-bot.md) above the
  network layer.** A scheduled job that fails a fingerprint check fails it on a nicer
  timetable.

The scheduler is one axis. Fingerprint is another. The exit is a third. Getting one
right does not carry the other two.

## Running it under cron or in a container

A couple of operational notes so the scheduled job actually works where you deploy
it, which is usually not your laptop.

cron runs with a minimal environment, so point at the Python that has the package and
set any variables the engine needs explicitly:

```bash
# /etc/cron.d/scraper - note the absolute interpreter path and the jittered sleep
0 */2 * * *  scraper  sleep $(( RANDOM \% 2400 )); INVISIBLE_PLAYWRIGHT_CACHE_DIR=/var/cache/engines /opt/venv/bin/python /srv/scrape_job.py >> /var/log/scraper.log 2>&1
```

In a container, cache the engine on a volume so it is downloaded once rather than on
every cold start, and remember the container is the machine a fingerprint page will
describe:

```dockerfile
FROM python:3.12-slim
RUN pip install invisible-playwright
ENV INVISIBLE_PLAYWRIGHT_CACHE_DIR=/engines
VOLUME /engines
COPY scrape_job.py /srv/scrape_job.py
CMD ["python", "/srv/scrape_job.py"]
```

Test the scheduled path on the box that runs it, through the proxy it will use, not
on your workstation - a job that passes at home tells you about home.

## Conclusion

cron and APScheduler are triggers, and the code they trigger is ordinary
invisible_playwright with its fingerprint work intact. What scheduling *adds* is a
timing pattern, and a fixed-interval one is a signature that no fingerprint check can
see and no proxy rotation erases. Jitter the trigger so the job never lands on the
same second, vary the pacing inside the run, and keep supplying the things the
scheduler was never going to fix: a clean exit, a sane request budget, and honest
volume. Cadence is its own axis, and this is where you set it.

## Short answers to the questions that lead here

**Does invisible_playwright change how I schedule a scraper?** No. Use cron,
APScheduler or a systemd timer as you would for any script. The scheduler triggers a
normal invisible_playwright run; the wrapper handles the fingerprint, you handle the
timing.

**Will a stealth browser stop a scheduled job from being detected?** It makes each
session look real, which handles the fingerprint axis. It does nothing about a
fixed-interval access pattern, IP reputation, or per-account limits. Those are yours
to solve.

**Why would a perfect fingerprint still get my cron job flagged?** Because arriving at
the same clock minute every day is a pattern in the timestamps, measurable
server-side with no JavaScript at all, and it is independent of how real any single
visit looks.

**How much jitter is enough?** Enough that the arrival times do not form sharp spikes.
A random offset spread across a large fraction of the interval (minutes to tens of
minutes, not seconds) plus uneven per-request gaps inside the run is the usual shape.

**Should I use one seed for a recurring job or a new one each run?** Both are
defensible and both can look odd if taken to an extreme. A stable seed is linkable
across days; a fresh identity every few minutes from one account is its own tell.
Decide deliberately rather than by default.

**Can I just run every 5 minutes on the dot?** You can, and it works until someone
plots your request times. `*/5` with no offset is the exact pattern this page is
about. Add a random sleep before the work.

## Sources

- The invisible_playwright quickstart and configuration pages, for the launch API,
  proxy dict and engine cache variable used in the examples above.
- [Playwright's `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for the `browser` object's API surface used unchanged in the job function above.
- This project's own testing notes on cadence: a scheduled probe run at a fixed
  interval flagged as automated on timing while every in-page fingerprint check
  stayed clean, which is the separation of axes this page describes.
- APScheduler's documented `jitter` option and standard cron behaviour.

**See also:** [rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for the within-run pacing, [scraping only new items](how-to-scrape-only-new-items-incremental-playwright.md)
to cut the volume a cadence check counts, and [the detection checklist](playwright-detected-as-bot.md)
for the axes a schedule does not touch.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine makes a
session look real; making the schedule look real is a separate job, and it is yours.*
