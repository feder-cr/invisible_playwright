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
