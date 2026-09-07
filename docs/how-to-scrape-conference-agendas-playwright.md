---
title: "How to scrape conference agendas with Playwright"
description: "Scrape conference schedules with Playwright: read a grid with parallel tracks without losing the track, resolve the event timezone once, and follow the session pages for speakers and abstracts."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 162
---


# How to scrape conference agendas with Playwright

To scrape an agenda, read the schedule as a **grid of tracks over time** rather than as a
list of sessions, and resolve the timezone once for the whole event. A conference agenda
is one of the few web tables where position carries meaning: the column a session sits in
is its track, and the rows above and below it are what it clashes with.

A list-shaped scrape loses that. It gives you every session with a start time and no way
to answer the only question most people have, which is what runs against what.

This page covers reading the grid with its track intact, handling the timezone properly,
and following through to the session pages where the speakers and abstracts live.

## Read the column, because the column is the track

Grid layouts put the track in a header, not in each cell. Read the header once, then map
each session to the column it occupies:

```python
from invisible_playwright import InvisiblePlaywright

GRID = """
() => {
  const tracks = Array.from(document.querySelectorAll('.schedule-grid .track-header'))
    .map(h => h.textContent.trim());
  return Array.from(document.querySelectorAll('.schedule-grid .session')).map(s => {
    const col = Number(getComputedStyle(s).getPropertyValue('grid-column-start')) || null;
    return {
      title: s.querySelector('.session-title')?.textContent.trim() || null,
      start: s.dataset.start || null,
      end: s.dataset.end || null,
      href: s.querySelector('a')?.getAttribute('href') || null,
      track: col && tracks[col - 1] ? tracks[col - 1] : (s.dataset.track || null),
    };
  });
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-conf.com/schedule")
    page.wait_for_selector(".schedule-grid .session")
    sessions = page.evaluate(GRID)
```

Reading `grid-column-start` from computed style is the trick that makes CSS-grid agendas
tractable. The DOM order of sessions in these layouts is frequently meaningless, because
the grid places them, so column position is the only reliable link between a session and
its track header.

Where the site uses a data attribute for the track, prefer it and skip the geometry. Check
both, because a redesign will switch between them.

## The timezone belongs to the event, not to you

Agendas print local times without zones almost universally. Resolve the event's zone once
and attach it, rather than letting each row inherit whatever your machine thinks:

```python
    tz = page.eval_on_selector(
        "meta[name='event-timezone'], .event-timezone",
        "e => e.content || e.textContent.trim()",
    ) if page.query_selector("meta[name='event-timezone'], .event-timezone") else None
    row["event_timezone"] = tz          # e.g. "Europe/Lisbon"
```

When the page does not say, take it from the venue city and record that you inferred it.
An agenda stored in naive local time is usable; one silently converted to your machine's
zone is a schedule that is wrong by hours for everyone who reads it later, and there is
nothing in the data that reveals the error.

Hybrid and online events make this worse by publishing two schedules, one in venue time
and one in the viewer's, sometimes switched by a toggle. Pin the toggle explicitly and
record which one you read.

## Sessions are stubs; the detail page has the substance

The grid cell carries a title and a time. Speakers, abstract, room and level live on the
session page:

```python
def session_detail(page, href):
    page.goto(href)
    page.wait_for_selector(".session-detail")
    return {
        "abstract": page.inner_text(".session-abstract"),
        "room": page.inner_text(".session-room") if page.query_selector(".session-room") else None,
        "speakers": [{
            "name": s.query_selector(".speaker-name").inner_text().strip(),
            "affiliation": (s.query_selector(".speaker-affiliation").inner_text().strip()
                            if s.query_selector(".speaker-affiliation") else None),
        } for s in page.query_selector_all(".speaker")],
    }
```

Keep speakers as a list of objects rather than a joined string. A comma-separated field
looks fine until a name contains a comma, and academic affiliations contain commas
constantly.

## Multi-day tabs and the day you did not read

Agendas split by day, behind tabs that swap the grid without changing the URL. The failure
is quiet: you scrape one day and get a complete-looking dataset:

```python
    for tab in page.query_selector_all(".day-tabs [role='tab']"):
        day = tab.inner_text().strip()
        tab.click()
        page.wait_for_selector(".schedule-grid .session")
        for s in page.evaluate(GRID):
            s["day"] = day
```

This is the same mechanic as any
[tab or accordion panel](how-to-scrape-accordion-and-tab-content-playwright.md), and the
same rule applies: click the control, wait for the content, do not guess a URL.

## Agendas change until the morning of the event

Schedules are edited constantly in the final weeks: rooms move, speakers cancel, sessions
swap slots. If the change history matters to you, keep each capture whole with its
timestamp rather than updating rows in place, and diff captures later. If it does not,
a daily pass in the fortnight before the event, and one after it, is enough.

Either way this is a small site with a spiky audience, so keep to the pacing in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) and prefer
running outside the conference hours themselves, when the site is serving the people
actually at the event.
