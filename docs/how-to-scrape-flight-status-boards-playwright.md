---
title: "How to scrape flight status boards with Playwright"
description: "Scrape airport arrival and departure boards with Playwright: read a board that rewrites itself, keep scheduled and estimated times as separate fields, and key rows on the flight and the day."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 156
---


# How to scrape flight status boards with Playwright

To scrape an arrivals or departures board, capture the row while the board is **between
refreshes**, and keep scheduled time and estimated time as two separate fields. These
boards rewrite their own DOM every thirty to sixty seconds, wholesale, and a scraper that
iterates over rows while a refresh lands reads half of the old table and half of the new
one without any error.

The second half of the problem is modelling. A flight is not identified by its number.
The same number flies every day, and on a delayed day it appears on two calendar days at
once, so a row keyed on flight number alone overwrites yesterday with today.

This page covers reading a self-refreshing board safely, keeping the two times apart, and
keying rows so a delay does not eat history.

## Snapshot the table in one evaluation

Do not iterate the DOM row by row from Python. Each call crosses into the page separately,
and the board can replace the table between two of them. Take the whole table in a single
evaluation instead:

```python
from invisible_playwright import InvisiblePlaywright

READ_BOARD = """
() => Array.from(document.querySelectorAll('table.board tbody tr')).map(tr => {
  const cell = (sel) => {
    const el = tr.querySelector(sel);
    return el ? el.textContent.trim() : null;
  };
  return {
    flight: cell('.flight-number'),
    airline: cell('.airline'),
    origin: cell('.origin'),
    destination: cell('.destination'),
    scheduled: cell('.time-scheduled'),
    estimated: cell('.time-estimated'),
    status: cell('.status'),
    gate: cell('.gate'),
  };
})
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-airport.com/departures")
    page.wait_for_selector("table.board tbody tr")
    rows = page.evaluate(READ_BOARD)
```

One evaluation is one moment. Everything it returns came from the same DOM, which is the
property you need and the one a Python-side loop cannot give you. The same reasoning
applies to any table that updates itself, including
[virtual scrolling tables](how-to-scrape-virtual-scrolling-tables-playwright.md), where
the rows are also being recycled underneath you.

## Scheduled and estimated are different facts

Boards show a scheduled time, and once the flight is late, an estimated or actual time
next to it. Collapsing them into one column destroys the only interesting thing on the
board:

```python
    row["delay_minutes"] = None
    if row["scheduled"] and row["estimated"]:
        row["delay_minutes"] = minutes_between(row["scheduled"], row["estimated"])
```

Compute the delay, do not store only the delay. When the board later revises the estimate,
having both times lets you see the revision; having only a delay number means you cannot
tell a corrected estimate from a worsening one.

Times on these boards are local to the airport and usually printed without a date or a
zone. Attach both from context, not from the string:

```python
    row["airport"] = "BRS"
    row["service_date"] = board_date          # read from the board header, not from today()
```

Read the date from the board itself where it shows one. Around midnight, "today" on your
machine and the board's service day are routinely different, and that is exactly when the
delayed flights you care about are on screen.

## Key a row on flight plus service day plus direction

```python
    key = (row["airport"], row["direction"], row["flight"], row["service_date"])
```

Direction belongs in the key because a code-shared arrival and departure can carry the
same number at the same airport. Service day belongs in the key because a flight
scheduled at 23:50 and departing at 00:40 exists on two calendar days, and the board will
show it under one of them in a way you do not control.

Store status transitions rather than the last value:

```python
    if previous.get(key, {}).get("status") != row["status"]:
        write_event(key, row)
```

A board is a live view. Its value as data is the sequence of states a flight passed
through, which is lost the moment you keep only the current one.

## The board is a poll, so poll it politely

The page refreshes itself; you do not need to reload it. Keep one page open and read the
snapshot on an interval, which is both lighter on the airport's servers and closer to what
a person watching a board does:

```python
    import time
    while True:
        rows = page.evaluate(READ_BOARD)
        ingest(rows)
        time.sleep(120)
```

Two minutes is enough for a board whose own refresh is around a minute. If the page stops
updating, which happens when a tab is considered inactive, take a screenshot to check what
is actually on screen before assuming the flights stopped moving, using
[full page screenshots](how-to-take-full-page-screenshots-playwright.md). Longer loops
also need the resilience described in
[retrying failed requests](how-to-retry-failed-requests-playwright.md), because a run
measured in hours will meet a network blip.

## Two things that quietly truncate the data

**The board paginates by time window.** Most show a few hours around now, with controls
for earlier and later. A scraper that reads the default view has a partial day and no
indication of it. Walk the windows explicitly.

**Cancelled and diverted rows leave the board.** They are removed rather than marked in
many implementations. If you only store what is on screen, a cancellation looks like a
flight that never existed. Compare each snapshot with the previous one and write the
disappearance as an event.
