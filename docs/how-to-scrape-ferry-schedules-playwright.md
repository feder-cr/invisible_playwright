---
title: "How to scrape ferry schedules with Playwright"
description: "Scrape ferry timetables with Playwright: query by date because sailings are seasonal, keep the route direction as part of the key, and record cancellations as data, not as missing rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 153
---


# How to scrape ferry schedules with Playwright

To scrape a ferry timetable, query it **date by date** instead of pulling a published
schedule page, keep the direction in the key, and treat a sailing that disappears as an
event, not as an absence. Ferry schedules are seasonal, weather-dependent and
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

## A complete sweep, resumable by construction

```python
import json, time
from datetime import date, timedelta
from invisible_playwright import InvisiblePlaywright

ROUTES = ["north-crossing", "island-hop"]
HORIZON = 60

def already_done(path):
    seen = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                seen.add((r["route"], r["date"]))
    except FileNotFoundError:
        pass
    return seen

path = "sailings.jsonl"
done = already_done(path)

with InvisiblePlaywright(seed=42) as browser, open(path, "a", encoding="utf-8") as out:
    page = browser.new_page()
    for route in ROUTES:
        for offset in range(HORIZON):
            day = date.today() + timedelta(days=offset)
            if (route, day.isoformat()) in done:
                continue
            rows = sailings_for(page, route, day)
            out.write(json.dumps({
                "route": route,
                "date": day.isoformat(),
                "observed_at": time.time(),
                "sailings": rows,
                "notices": [n.inner_text().strip()
                            for n in page.query_selector_all(".service-notice, .disruption")],
            }, ensure_ascii=False) + "\n")
            out.flush()
            page.wait_for_timeout(2500)
```

Reading back what is already on disk is a cheaper resume than a checkpoint file, because
it cannot drift out of sync with the data it describes. Note that the record is written
even when `sailings` is empty: a day with no service is a fact, and skipping the write
would make it indistinguishable from a day the sweep never reached. The longer form of
that argument is in
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md).

## Booking engines defend harder than timetable pages

A ferry timetable lives inside a booking engine, and booking engines are protected because
they are a target for inventory scraping and card testing. That shapes what works.

**The date query is a real search, not a page view.** Each submission runs an availability
lookup against live inventory, which is expensive for the operator and is exactly the
request that gets rate limited first. Sixty days across two routes is 120 lookups, which
is fine overnight and rude in a tight loop.

**Refusal is often silent.** Rather than an error, you get the shell with an empty result
region, which parses as a day with no sailings. That failure writes plausible rows and is
never noticed, so guard on the positive marker:

```python
    page.wait_for_selector(".sailing, .no-sailings", timeout=20000)
    if not page.query_selector(".sailing") and not page.query_selector(".no-sailings"):
        raise RuntimeError("neither sailings nor a no-service marker: failed read")
```

**A real browser is the price of entry.** These engines fingerprint aggressively and a
stripped client usually never reaches the timetable. Driving stock Playwright against a
patched Firefox is what makes the query look like the query a passenger makes; the debug
order when it stops working is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## The schema that answers "does this crossing actually run"

Keep sailings nested under a query record rather than flattened into one table, or flatten
with the query context copied down. Either way these five fields are what make the dataset
worth having:

| field | why |
|---|---|
| `route` and `direction` | two directions collide on time alone |
| `date` | the query date, not the date you ran |
| `status` | scheduled, cancelled, full, at the sailing level |
| `observed_at` | the same date read twice tells you when it changed |
| `notices` | the disruption text, per query, verbatim |

The interesting analysis is the difference between two captures of the same date. A
sailing that was scheduled on Monday and gone by Thursday is a cancellation, and that is
the number a seasonal PDF can never give you: not what the operator plans to run, but what
the operator actually ran. Storing every capture rather than updating in place is what
makes that question answerable at all.

## Short answers to the questions that lead here

**Why does the same route return a different timetable on different days?** Because
the season decides the timetable. Ferry operators publish per-date, not a single
annual grid, so a query without an explicit date returns whatever the site thinks
today is and a series built that way silently mixes seasons.

**Do I need to scrape each direction separately?** Direction is part of a sailing's
identity, not a column you can add afterwards. Operators often show both directions on
one page, sometimes in two tables and sometimes in one with a toggle, so decide which
you are reading before you store a row.

**The date field ignores what I type. What now?** It is a calendar widget, not a text
input. Open it and click the day, the way a person does. A written value leaves the
widget's internal state unchanged and the search runs against the previous date.

**Where do cancellations show up?** Separately from the timetable, usually as a notice
banner or a status column, and they are the most interesting part of the data. A
timetable without the disruption record describes an intention, not a service.

**See also:** [Scrape date-picker calendars with
Playwright](how-to-scrape-date-picker-calendar-playwright.md), [How to resume an
interrupted scrape with
Playwright](how-to-resume-an-interrupted-scrape-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Input, https://playwright.dev/python/docs/input - `locator.fill()`,
  which focuses the element and fires an input event, against `press_sequentially()`,
  which types character by character with an optional delay. Checked for why a written
  date does not commit in a calendar widget.
- This project's page on driving date pickers and calendar widgets, which this page
  relies on instead of restating.
