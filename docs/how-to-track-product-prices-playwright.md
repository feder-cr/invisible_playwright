---
title: "How to track product prices with Playwright"
description: "Track product prices with Playwright: wait for the async price widget, keep one stable fingerprint per watched item, and diff a saved daily time series."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 28
---


# How to track product prices with Playwright

Price tracking is not one scrape. It is the same URL sampled on a schedule, usually
daily, where the number you want is not in the HTML the server first sends. It arrives
later, painted into a widget by a script that runs after load, and you have to wait for
it before you read anything. Then you compare today's number against yesterday's and
record the change.

That schedule is the part most guides skip, and it is the part that gets a monitor
caught. A one-off scrape is anonymous. A monitor is a visitor who comes back to the exact
same pages every single day, which is a much stronger thing to have to look ordinary.
This page builds the loop end to end, and is honest about the one structural risk that a
random-fingerprint scraper never has to think about: identity over time.

## Why a price monitor is a longitudinal problem, not a scraping one

A scraper that hits a thousand different URLs once looks like a thousand shallow visits.
A price monitor hits the same handful of URLs, from the same job, on a fixed cadence,
for weeks. Those are opposite shapes, and the second one is what a returning-visitor
model is built to recognise.

Here is the trap that most stealth setups walk into. A tool that draws a fresh random
fingerprint every run does the right thing for a one-off and exactly the wrong thing for
a monitor. Day 1 the same page is visited by a machine with one GPU, one canvas hash, one
audio device, one screen. Day 2 the same page is visited, from a similar address, by a
machine whose every hardware surface is different. That is not two unrelated strangers. It
reads as one page being polled by a rotating cast of devices that never returns in the
same body twice, which is an anomaly a single visitor never produces.

The fix is to stop rotating. A price monitor wants the opposite of variety: it wants to be
the same returning visitor, day after day, byte for byte. That is precisely what a
seed-derived identity gives you.

## One seed per watched identity

`invisible_playwright` derives roughly 400 fingerprint fields - GPU string, canvas hash,
audio context, font list, screen geometry - from a single integer seed. Pass the same
seed and you get the same machine, deterministically, every run:

```python
from invisible_playwright import InvisiblePlaywright

# Day 1 and day 30 produce a byte-identical browser.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/widget")
    # ... read the price ...
```

The rule for a monitor is one seed per identity you want to maintain, chosen once and
written down. If you watch three separate storefronts and want each to see a stable,
independent returning visitor, give each its own seed and keep the mapping:

```python
WATCHERS = {
    "https://example.com/product/widget":   1001,
    "https://shop.example.net/item/frame":  1002,
    "https://store.example.org/sku/lens":   1003,
}
```

The seed is not a secret and it is not a login. It is a stable disguise. Two runs with
`seed=1001` are the same device as far as any in-page fingerprint can tell, which is the
continuity a longitudinal job needs and a random draw destroys. If you ever need to prove
that, log `sf.seed` on a run you keep, and the same seed replays the same identity later -
the [Quickstart](quickstart.md) shows the one-line way to capture it.

## Wait for the price, do not scrape the shell

The number lives in an async widget. The server sends a page skeleton, a script fetches
the price, and only then does the DOM contain it. If you read immediately after `goto`,
you read the skeleton and record a null, or worse, a stale placeholder that looks like a
real value and quietly corrupts your series.

So the read has to wait for the actual price element, not for the network to go idle.
Playwright's [`wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector)
is the honest tool here, because it waits for the thing you care about rather than for a
proxy for it:

```python
def read_price(page, url):
    page.goto(url, wait_until="domcontentloaded")
    # Wait for the widget that carries the price, not for the page shell.
    el = page.wait_for_selector("[data-testid='price']", state="visible", timeout=15000)
    raw = el.inner_text().strip()
    # "$1,299.00" -> 1299.00
    return float(raw.replace("$", "").replace(",", ""))
```

Waiting for a fixed number of seconds is the thing to avoid. It is slower than it needs to
be on a fast day and too short on a slow one, and a monitor that sometimes reads the
skeleton produces a phantom price change that is entirely your own bug. Wait for the
element, assert it is visible, and treat a timeout as a failed sample rather than a price
of zero. The full set of waiting strategies, including why network-idle is the wrong
condition for widget-driven pages, is in
[how to wait for a page to load](how-to-wait-for-page-load-playwright.md).

## Persist a time series and diff against yesterday

A price is only interesting relative to its own history, so the monitor's real output is a
file that grows by one row per run. Append today's sample, compare it to the last one you
recorded, and act only on the difference:

```python
import json, os, datetime

STORE = "prices.jsonl"

def last_sample(url):
    if not os.path.exists(STORE):
        return None
    prev = None
    with open(STORE, encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["url"] == url:
                prev = row
    return prev

def record(url, price):
    row = {
        "url": url,
        "price": price,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(STORE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return row

def run_once():
    for url, seed in WATCHERS.items():
        with InvisiblePlaywright(seed=seed) as browser:
            page = browser.new_page()
            try:
                price = read_price(page, url)
            except Exception as exc:
                print(f"sample failed for {url}: {exc}")
                continue
        prev = last_sample(url)
        record(url, price)
        if prev and price != prev["price"]:
            delta = price - prev["price"]
            print(f"CHANGE {url}: {prev['price']} -> {price} ({delta:+.2f})")
        else:
            print(f"steady {url}: {price}")
```

Append-only is deliberate. You want the whole history, not just the latest value, because
the series is the product: it is what lets you tell a real markdown from a widget that
happened to render slowly, and it is the thing a `wait_for` failure shows up in as a gap
rather than a fake zero. Note that each URL gets its own seed inside the loop, so every
storefront keeps its own stable returning visitor.

To put this on a daily cadence, the loop body is the same whether a cron entry or a
scheduler calls it. A minimal container runs the job and exits:

```dockerfile
FROM python:3.12-slim
RUN pip install invisible-playwright
COPY monitor.py .
CMD ["python", "monitor.py"]
```

```bash
# crontab: sample once a day at 07:15
15 7 * * *  docker run --rm -v "$PWD/data:/data" price-monitor
```

## The honest caveat: a stable fingerprint is not a stable session

A seed pins the machine. It does not pin the two other things that a returning visitor
also keeps constant, and if the price is personalised, those matter as much as the
fingerprint.

The first is the network exit. A byte-identical browser that appears on a different
country's IP every day is telling two contradictory stories: the same device, teleporting.
For a monitor, pin the proxy exit the same way you pin the seed, and let the browser's
timezone follow it so the two agree. `invisible_playwright` derives timezone from the
egress IP by default, so a consistent exit gives you a consistent zone for free; the
[Configuration](configuration.md) page covers the proxy dict, and
[geotargeted scraping](how-to-scrape-geotargeted-content-playwright.md) covers the case
where the price itself depends on where the visitor appears to be.

The second is state. A seed is not a logged-in account and not a cookie jar. If the price
you want only appears behind a sign-in, or is quoted per-member, then the identity that
has to stay stable is the profile on disk, not just the fingerprint. Reuse a persistent
profile across runs so the cookies, storage and session survive to the next day - see
[persistent profiles](persistent-profiles.md) for how to keep one identity's state on
disk. Without it, "the same visitor" changes underneath you even though every fingerprint
field is identical, because the account that saw yesterday's price is gone.

Say it plainly: seed stability solves the device-identity half of a longitudinal monitor.
The exit and the profile solve the other half, and a personalised price needs all three.

## Conclusion

A price monitor is a schedule, not a scrape. The number is async, so you wait for the
widget rather than the page. The history is the product, so you append and diff rather
than print. And the hard part is not any single request - it is looking like the same
visitor across weeks of them. A seed-derived fingerprint gives you that continuity where a
random draw would hand the same page a new anonymous device every day, and for a
personalised price you pin the exit and reuse the profile on top of it. Get those three
stable and the daily loop above is the whole job.

## Short answers to the questions that lead here

**Why is the price empty when I scrape it?** Because it loads into an async widget after
the page shell, so an immediate read gets the skeleton. Wait for the price element with
`wait_for_selector`, not for network idle.

**Should I use a new fingerprint for each run?** Not for a monitor. A monitor revisits the
same URLs daily, and a fresh random device each time reads as one page being polled by a
rotating cast. Use one fixed seed per watched identity so day 2 is byte-identical to day 1.

**How do I detect a price change?** Persist every sample to an append-only file and compare
today's value for a URL against the last one you recorded for that same URL. The series is
the output, not just the latest number.

**Does a stable seed keep me logged in?** No. A seed pins the machine's fingerprint, not
cookies or a session. If the price is behind a login or personalised, reuse a persistent
profile as well.

**How often should I sample?** As often as the price meaningfully changes, which for most
things is daily. Space the runs out on a real schedule rather than looping tightly, so the
cadence itself looks like a person checking a page and not a machine hammering it.

**What if the price is different by country?** Then it is geo-personalised, and the exit
IP is part of the identity you have to keep stable. Pin the proxy exit and let the timezone
follow it, so the browser and the network tell the same story every day.

## Sources

- This project's real API, as documented in [Quickstart](quickstart.md) and
  [Configuration](configuration.md): the seed-to-fingerprint derivation, the returned
  object being a stock Playwright `Browser`, and the egress-derived timezone.
- Playwright's own documentation for
  [`wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector)
  and [locator waiting](https://playwright.dev/python/docs/api/class-locator#locator-wait-for),
  used unchanged because the returned browser is stock Playwright.

**See also:** [how to wait for a page to load](how-to-wait-for-page-load-playwright.md)
for the widget-timing half of this, and [persistent profiles](persistent-profiles.md) for
keeping one identity's state across daily runs.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed-per-identity rule
on this page is the one thing that separates a monitor that looks like a returning visitor
from one that looks like a new device every morning.*
