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

Store status transitions instead of the last value:

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

## A complete watcher, with disappearance handled

```python
import json, time
from invisible_playwright import InvisiblePlaywright

AIRPORT, DIRECTION = "BRS", "departures"

def key_of(row, service_date):
    return (AIRPORT, DIRECTION, row["flight"], service_date)

previous, out_path = {}, "board.jsonl"

with InvisiblePlaywright(seed=42) as browser, open(out_path, "a", encoding="utf-8") as out:
    page = browser.new_page()
    page.goto(f"https://example-airport.com/{DIRECTION}")
    page.wait_for_selector("table.board tbody tr")

    while True:
        service_date = page.inner_text(".board-date").strip()
        rows = page.evaluate(READ_BOARD)
        observed = time.time()
        current = {}

        for row in rows:
            k = key_of(row, service_date)
            current[k] = row
            before = previous.get(k)
            if before is None or before["status"] != row["status"] or before["estimated"] != row["estimated"]:
                out.write(json.dumps({
                    "airport": AIRPORT, "direction": DIRECTION,
                    "service_date": service_date, "observed_at": observed,
                    "event": "seen", **row,
                }, ensure_ascii=False) + "\n")

        for gone in set(previous) - set(current):
            out.write(json.dumps({
                "airport": AIRPORT, "direction": DIRECTION,
                "service_date": gone[3], "flight": gone[2],
                "observed_at": observed, "event": "left_board",
                "last_status": previous[gone]["status"],
            }) + "\n")

        out.flush()
        previous = current
        time.sleep(120)
```

The `left_board` event carries the last status seen, which is what makes it interpretable
later. A flight that leaves the board an hour after "Departed" left normally; one that
leaves while still "Delayed" is the cancellation you actually wanted to catch.

## Why a long-lived page is its own problem

This scraper does something unusual: it keeps one page open for hours. That avoids
hammering the airport with reloads, and it introduces failure modes a request-per-read
design never meets.

**The page stops refreshing.** Boards throttle their own updates when the tab looks
inactive, and a headless context can look inactive permanently. The tell is a snapshot
that stops changing while the wall clock moves. Guard on it explicitly rather than
trusting the loop:

```python
    if rows == last_rows and time.time() - last_change > 900:
        page.reload()
        page.wait_for_selector("table.board tbody tr")
        last_change = time.time()
```

**The session ages out.** Edge protection frequently issues a token with a lifetime, and
when it expires the board silently stops updating or returns an interstitial in place of
the table. A reload re-establishes it; a fresh context is the heavier fallback.

**A reload lands mid-render.** Waiting for the selector is not enough on its own, because
the table exists before it is populated. Waiting for at least one row with a flight number
is the version that does not read an empty board:

```python
    page.wait_for_function(
        "() => document.querySelectorAll('table.board tbody tr .flight-number').length > 0"
    )
```

Underneath all three, the browser has to keep looking like a browser for hours rather than
seconds, which is where a patched engine driven by stock Playwright earns its place. The
order to debug a degraded read is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md), and the
resilience patterns for a run measured in hours are in
[retrying failed requests](how-to-retry-failed-requests-playwright.md).

## What the event stream is worth

One row per state change, keyed on airport, direction, flight and service date, is what
turns a live board into an answerable dataset:

- **Real punctuality**, computed from scheduled against the last estimate before
  departure, rather than from an airline's own published figure.
- **How delays propagate**, by following the same aircraft's rotations across a day when
  the board exposes the inbound flight.
- **Which gates and stands are chronically late**, which no public dataset publishes.
- **Cancellation rate by route**, from `left_board` events with a non-departed last
  status.

None of these come from a snapshot table holding today's board. They all come from the
transitions, which is why the loop above writes changes rather than states, and why the
last status travels with the disappearance.

## Short answers to the questions that lead here

**Why should I read the whole board in one evaluation?** Because the board rewrites
itself while you read. Iterating the DOM row by row from Python crosses into the page
once per call, and a refresh between two calls gives you half of one board and half of
the next. One evaluation returns one consistent snapshot.

**Scheduled, estimated, actual: which one do I store?** All of them, in separate
fields. Collapsing them into a single time destroys the only thing a status board is
for, which is the difference between what was planned and what happened.

**What key identifies a row?** Flight number plus service day plus direction.
Direction belongs in the key because a code-shared arrival and a departure can carry
the same number at the same airport on the same day.

**A flight disappeared from the board. Is that an error?** No, it is data. Boards show
a window around now, so a flight leaves the board when it ages out. Record the
disappearance with the last status you saw, or the series will look like the flight
never landed.

**See also:** [How to scrape virtual scrolling tables with
Playwright](how-to-scrape-virtual-scrolling-tables-playwright.md), [How to retry
failed requests when scraping Playwright](how-to-retry-failed-requests-playwright.md),
[How to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)

## Sources

- Playwright, Locators, https://playwright.dev/python/docs/api/class-locator -
  `locator.evaluate_all`, which runs one piece of JavaScript over every matching
  element, checked as the mechanism for taking a whole board in a single pass instead
  of one round trip per row.
- This project's page on virtual scrolling tables, for the case where the board is
  windowed in the DOM as well as in time.
