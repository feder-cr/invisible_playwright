---
title: "How to scrape ferry schedules with Playwright"
description: "Scrape ferry timetables with Playwright: query by date because sailings are seasonal, keep the route direction as part of the key, and record cancellations as data rather than as missing rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 153
---


# How to scrape ferry schedules with Playwright

To scrape a ferry timetable, query it **date by date** rather than pulling a published
schedule page, keep the direction in the key, and treat a sailing that disappears as an
event rather than as an absence. Ferry schedules are seasonal, weather-dependent and
frequently amended, which makes them one of the few timetable types where yesterday's
capture genuinely does not describe today.

The trap is that these operators also publish a tidy PDF or HTML timetable for the
season, and it is very tempting to parse that once and be done. That document is the
plan. The booking engine is the truth, and on any given day the two disagree.

This page covers driving the date-based query, modelling a sailing so that two
directions do not collapse into one, and recording the amendments that make the data
worth having.

## Query by date, because the season decides the timetable

Drive the operator's own date control and step through the range you need:

```python
from datetime import date, timedelta
from invisible_playwright import InvisiblePlaywright

def sailings_for(page, route, day):
    page.goto(f"https://example.com/timetable?route={route}")
    page.fill("input[name='date']", day.isoformat())
    page.click("button[type='submit']")
    page.wait_for_selector(".sailing, .no-sailings")
    if page.query_selector(".no-sailings"):
        return []
    out = []
    for row in page.query_selector_all(".sailing"):
        out.append({
            "date": day.isoformat(),
            "depart": row.query_selector(".depart-time").inner_text().strip(),
            "arrive": row.query_selector(".arrive-time").inner_text().strip(),
            "vessel": (row.query_selector(".vessel") or row).inner_text().strip(),
        })
    return out

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    day = date.today()
    for offset in range(0, 60):
        rows = sailings_for(page, "north-crossing", day + timedelta(days=offset))
```

The explicit `.no-sailings` branch is the part people leave out. A day with no service
and a day where the selector silently failed produce the same empty list, and once that
distinction is lost the dataset quietly grows holes that look like winter.

## Direction is part of the identity, not a field you can add later

A route has two directions and the operator often shows them on the same page, sometimes
in two tables, sometimes in one table with a column. A sailing keyed on
`(route, date, departure time)` will collide across directions, and the collision is
silent because both rows look plausible:

```python
            "from_port": header_port(row, "from"),
            "to_port": header_port(row, "to"),
```

Capture the two ports per row, even when the page groups them under a heading. If the
page really does put direction only in a heading, read the heading into every row
underneath it rather than trusting yourself to remember the grouping later:

```python
    current_direction = None
    for node in page.query_selector_all(".timetable h3, .timetable .sailing"):
        cls = node.get_attribute("class") or ""
        if node.evaluate("e => e.tagName") == "H3":
            current_direction = node.inner_text().strip()
            continue
        row = read_sailing(node)
        row["direction"] = current_direction
```

## Cancellations and amendments are the interesting data

Operators publish disruption separately from the timetable, usually as a notice banner or
a status column. That is the part that a seasonal PDF can never give you:

```python
    notices = [n.inner_text().strip()
               for n in page.query_selector_all(".service-notice, .disruption")]
    for row in rows:
        status_cell = row_node.query_selector(".status")
        row["status"] = status_cell.inner_text().strip() if status_cell else "scheduled"
```

Store the status per sailing and the notices per query. When a sailing vanishes between
two captures of the same date, write that transition explicitly rather than deleting the
row: a schedule dataset whose history is overwritten cannot answer the one question it is
uniquely able to answer, which is how often this crossing actually runs.

## Two mechanical traps on these sites

**The date field is a picker, not an input.** Many operators use a calendar widget that
ignores a written value and only commits on a click. If `fill` leaves the results
unchanged, open the picker and click the day, which is the same problem described in
[scraping date-picker calendars](how-to-scrape-date-picker-calendar-playwright.md).

**Results load into the page without a navigation.** The submit button triggers a request
and swaps a region. Waiting for a load event returns immediately and you read the previous
day's table. Wait for the result region to change, or capture the response directly with
the technique in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## Pace it like the resource it is

Sixty days across a handful of routes is a few hundred queries, which is fine once a day
and rude every hour. Timetables change on the scale of a season, with same-day amendments
layered on top, so a daily full sweep plus a short poll of the current day covers the real
volatility. The pacing rules are in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

Ferry operators are also small sites with real seasonal traffic spikes. Running the sweep
overnight in the operator's own timezone is a courtesy that costs you nothing and keeps
the run out of the window where it would actually be felt.
