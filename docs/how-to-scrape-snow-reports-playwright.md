---
title: "How to scrape snow reports with Playwright"
description: "Scrape ski resort snow reports with Playwright: normalise the units before storing, keep base and summit as separate measurements, and record the report time the resort publishes."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 157
---


# How to scrape snow reports with Playwright

To scrape a snow report, normalise the **unit** at capture and keep each measurement
point separate. A resort publishes several numbers that all look like "snow depth": base,
mid-mountain, summit, new snow in 24 hours, new snow in 48 hours, season total. They are
different measurements, taken at different altitudes, and a column called `depth` that
mixes them is worse than no column.

The unit is the other half. The same resort site serves centimetres to one visitor and
inches to another, usually from a toggle that remembers a previous choice or from an
inferred locale. A series that silently switches unit halfway through looks like a
blizzard followed by a thaw.

This page covers pinning the unit, reading the measurement points as separate rows, and
keeping the resort's own report timestamp.

## Pin the unit before reading any number

Set the toggle explicitly rather than trusting the default:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-resort.com/snow-report")

    toggle = page.query_selector("[data-unit='cm'], .units-metric")
    if toggle:
        toggle.click()
        page.wait_for_timeout(500)

    unit = page.eval_on_selector(".depth-value", "e => e.dataset.unit || 'unknown'")
```

Then record the unit with every row, even after pinning it. A toggle that failed to click
leaves the page in the other unit and your rows still say what you intended rather than
what you read. Reading the unit back from the DOM after the click is the version that
catches that.

Where the site has no toggle and decides by locale, you are looking at
[geotargeted content](how-to-scrape-geotargeted-content-playwright.md) and the exit you
browse from becomes part of the measurement.

## One row per measurement point

```python
POINTS = {
    ".base .depth-value": "base",
    ".mid .depth-value": "mid",
    ".summit .depth-value": "summit",
}

    readings = []
    for selector, point in POINTS.items():
        node = page.query_selector(selector)
        if not node:
            continue
        readings.append({
            "point": point,
            "value_text": node.inner_text().strip(),
            "unit": unit,
        })
```

Resorts drop and add points between seasons, and some publish only one. A missing point
should be an absent row, not a zero. Zero snow at the summit and no summit reading are
opposite facts, and only one of them is ever true in January.

## New snow is a window, so store the window

"New snow" is meaningless without the period it covers, and the period varies by resort
and sometimes by season:

```python
        {"metric": "new_snow", "window_hours": 24, "value_text": "12", "unit": "cm"}
```

Read the window from the label next to the number rather than assuming 24 hours. Sites
write "Last 24h", "Overnight", "Since 5pm" and "48 hour total" in the same widget across a
handful of resorts, and "Overnight" is not a fixed duration at all. When the label does
not resolve to a number of hours, keep it as text and leave `window_hours` null. A null is
honest; a 24 you invented is not.

## Keep the resort's report time, not your fetch time

Snow reports are published once or twice a day and the page shows when:

```python
    reported = page.query_selector(".report-updated")
    row["reported_text"] = reported.inner_text().strip() if reported else None
    row["observed_at"] = time.time()
```

Both times matter for the same reason they matter in any live-status scrape. A page
fetched at noon showing a report from 6am is a six hour old measurement, and a series
built on fetch time alone will show snow arriving at whatever hour your cron happens to
run.

## The lift and trail counts are a different kind of number

Most snow reports sit next to counts of open lifts and open runs. They are worth taking,
with the same care about denominators:

```python
    lifts_text = page.inner_text(".lifts-open")     # "8 / 14"
```

Store the numerator and denominator, not a percentage. Resorts change the total number of
lifts between seasons, and a stored percentage cannot be recomputed once the total is
gone. This is the same discipline as
[reading stock levels](how-to-scrape-stock-levels-playwright.md), where the count and the
capacity are two facts that only mean something together.

## Season shape and polite polling

These sites are seasonal and small. Out of season the page often keeps last season's
numbers frozen rather than clearing them, so a scraper running in July happily records a
metre of base snow. Guard on the report timestamp: if it has not moved in a week, stop
storing new rows and record the staleness instead.

In season, once or twice a day matches publication. There is no faster truth to get, and
the pacing advice in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) applies
with the extra note that resort sites see genuine traffic spikes at 7am local time, which
is precisely when a naive scheduler would fire.

## A complete pass over several resorts

```python
import json, time
from invisible_playwright import InvisiblePlaywright

RESORTS = {
    "example-alpine": "https://example-resort.com/snow-report",
    "example-nordic": "https://another-resort.example/conditions",
}

def pin_units(page):
    toggle = page.query_selector("[data-unit='cm'], .units-metric")
    if toggle:
        toggle.click()
        page.wait_for_timeout(500)
    node = page.query_selector(".depth-value")
    return page.evaluate("e => e.dataset.unit || 'unknown'", node) if node else "unknown"

def read_resort(page, resort_id, url):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector(".depth-value, .report-closed", timeout=20000)
    if page.query_selector(".report-closed"):
        return [{"resort": resort_id, "state": "closed_for_season",
                 "observed_at": time.time()}]

    unit = pin_units(page)
    reported = page.query_selector(".report-updated")
    reported_text = reported.inner_text().strip() if reported else None

    rows = []
    for selector, point in POINTS.items():
        node = page.query_selector(selector)
        if not node:
            continue
        rows.append({
            "resort": resort_id, "metric": "depth", "point": point,
            "value_text": node.inner_text().strip(), "unit": unit,
            "reported_text": reported_text, "observed_at": time.time(),
        })
    lifts = page.query_selector(".lifts-open")
    if lifts:
        rows.append({"resort": resort_id, "metric": "lifts",
                     "value_text": lifts.inner_text().strip(),
                     "reported_text": reported_text, "observed_at": time.time()})
    return rows

with InvisiblePlaywright(seed=42) as browser, open("snow.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    for resort_id, url in RESORTS.items():
        for row in read_resort(page, resort_id, url):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(5000)
```

The `closed_for_season` branch is what stops a July run from recording last winter's
frozen numbers as today's conditions, which is the single most common defect in this kind
of dataset.

## Stale data is the failure here, not blocking

Resort sites are rarely defended aggressively. What they do instead is serve heavily
cached pages from a CDN, which produces a failure that looks nothing like a block and is
much easier to miss: the request succeeds, the page renders, and the numbers are from
yesterday morning.

Guard on the report timestamp rather than on the response:

```python
def suspect_stale(rows, seen_before, max_repeats=3):
    stamp = rows[0].get("reported_text")
    if stamp and seen_before.get(rows[0]["resort"]) == stamp:
        seen_before[f"{rows[0]['resort']}:count"] = seen_before.get(f"{rows[0]['resort']}:count", 0) + 1
    else:
        seen_before[rows[0]["resort"]] = stamp
        seen_before[f"{rows[0]['resort']}:count"] = 0
    return seen_before.get(f"{rows[0]['resort']}:count", 0) >= max_repeats
```

Three consecutive reads with an unmoved report time, during the season, means you are
reading a cache or the resort has stopped publishing. Both are worth recording as a state
rather than absorbing as unchanged conditions.

The other resort-specific failure is the unit toggle silently not applying, which is why
the code reads the unit back from the DOM after clicking rather than assuming the click
worked. That is the same discipline as any lever in this corpus: verify the setting from
inside the system you are measuring, or the arm is inert and its result means nothing.

## The long-form schema, and what it answers

One row per resort, metric, point and observation:

| field | example | note |
|---|---|---|
| `resort` | `example-alpine` | your id, stable across their redesigns |
| `metric` | `depth`, `new_snow`, `lifts` | separate metrics, never one column |
| `point` | `base`, `mid`, `summit` | absent for metrics without an altitude |
| `window_hours` | `24` or null | only for new snow, null when the label is vague |
| `value_text` / `unit` | `"42"` / `cm` | the string and the unit, both kept |
| `reported_text` | `"Updated 06:15"` | the resort's own clock |
| `observed_at` | epoch | yours |

With that grain, three questions become easy that a wide table cannot answer: how base and
summit diverge through a season, how often a resort's published depth moves at all
(several update weekly while claiming daily), and whether lift openings track snowfall or
track the calendar. The last one is the interesting one, and it needs the numerator and
denominator kept separately, which is why lifts are stored as their original `"8 / 14"`
string rather than a percentage.

## Short answers to the questions that lead here

**Why do my snow numbers jump between runs?** Usually the unit toggle. Resorts serve
centimetres or inches depending on a control that remembers a previous choice, so a
series that never pins the unit mixes two scales that differ by a factor of two and a
half.

**What does new snow mean on these pages?** Nothing, without the window it covers. New
snow is measured over a period that varies by resort and sometimes by season, so store
the window with the number or the figure is not comparable to anything.

**Should I use my fetch time as the observation time?** No. Snow reports are published
once or twice a day and the page says when. The report time is the fact; your fetch
time only tells you when you looked.

**The numbers have not changed in weeks. Is the scraper broken?** Check the calendar
before the code. Out of season these pages often keep last season's figures frozen
instead of clearing them, which reads as fresh data to anything that only looks at the
numbers.

**See also:** [How to scrape geotargeted content with
Playwright](how-to-scrape-geotargeted-content-playwright.md), [How to scrape stock
levels with Playwright](how-to-scrape-stock-levels-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Input, https://playwright.dev/python/docs/input - clicking a toggle and
  waiting for the value to repopulate, checked for pinning the unit before any number
  is read.
- This project's page on stock levels, which carries the same distinction between an
  absent measurement and a measurement of zero.
