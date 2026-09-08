---
title: "How to scrape conference agendas with Playwright"
description: "Scrape conference schedules with Playwright: read a grid with parallel tracks without losing the track, resolve the event timezone once, and follow the session pages for speakers and abstracts."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 162
---


# How to scrape conference agendas with Playwright

To scrape an agenda, read the schedule as a **grid of tracks over time**, not as a
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
and attach it, not letting each row inherit whatever your machine thinks:

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

Keep speakers as a list of objects instead of a joined string. A comma-separated field
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
timestamp instead of updating rows in place, and diff captures later. If it does not,
a daily pass in the fortnight before the event, and one after it, is enough.

Either way this is a small site with a spiky audience, so keep to the pacing in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) and prefer
running outside the conference hours themselves, when the site is serving the people
actually at the event.

## A complete capture, days and sessions

```python
import json, time
from invisible_playwright import InvisiblePlaywright

def capture_agenda(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector(".schedule-grid .session", timeout=20000)

    tz_node = page.query_selector("meta[name='event-timezone'], .event-timezone")
    tz = page.evaluate("e => e.content || e.textContent.trim()", tz_node) if tz_node else None

    days, sessions = page.query_selector_all(".day-tabs [role='tab']"), []
    if not days:
        for s in page.evaluate(GRID):
            sessions.append({**s, "day": None})
    else:
        for tab in days:
            label = tab.inner_text().strip()
            tab.click()
            page.wait_for_function(
                "() => document.querySelectorAll('.schedule-grid .session').length > 0")
            page.wait_for_timeout(400)          # let the grid settle after the swap
            for s in page.evaluate(GRID):
                sessions.append({**s, "day": label})

    return {"url": url, "event_timezone": tz,
            "captured_at": time.time(), "sessions": sessions}

with InvisiblePlaywright(seed=42) as browser, open("agenda.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    agenda = capture_agenda(page, "https://example-conf.com/schedule")
    out.write(json.dumps(agenda, ensure_ascii=False) + "\n")

    for session in agenda["sessions"]:
        if not session.get("href"):
            continue
        detail = session_detail(page, session["href"])
        out.write(json.dumps({"session_href": session["href"], **detail,
                              "captured_at": time.time()}, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(1500)
```

Writing the whole agenda as one record per capture, rather than one row per session, is
deliberate. Agendas are edited constantly in the final fortnight, and the interesting
analysis is the diff between two captures: which sessions moved, which speakers dropped,
which rooms changed. A row-per-session table updated in place erases exactly that.

## Why conference sites break in the last two weeks

These are usually built quickly on a platform, deployed once, and then edited under
pressure. The failures are not defences.

**The grid is rebuilt on every tab click.** Elements captured before the click are detached
afterwards, so a Python-side loop holding handles across a tab change throws or reads
stale nodes. Capturing each day in one evaluation, as above, avoids the whole class.

**Session pages 404 while the grid still lists them.** A withdrawn talk is removed from the
detail route before the schedule is regenerated. Record the failure as a row rather than
letting it stop the run, and the disappearance becomes data about the event.

**The timezone toggle changes the grid under you.** Hybrid events render either venue time
or viewer time, and the control sometimes defaults from a stored preference. Read the
resolved zone back after setting it rather than assuming, which is the same verify-the-lever
discipline that applies to the unit toggles on other targets.

Where a platform does put the schedule behind protection, which a few of the larger ones
do, the page is worth a look before assuming a selector problem, using the order in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## The shape that answers scheduling questions

From captures, derive two tables:

| table | key | holds |
|---|---|---|
| `session` | `event`, `day`, `track`, `start` | title, room, href, first and last seen |
| `speaker` | `session_id`, `name` | affiliation, ordinal |

Keeping speakers in their own table rather than a joined string is what makes the useful
queries possible: who appears most across tracks, which affiliations dominate a programme,
whether a speaker is double-booked against themselves. The last one happens more often than
organisers would like, and it is only visible when track and time are both kept.

The clash query is the one that justifies the grid work at the start of this page. Two
sessions clash when they share a day and overlap in time on different tracks, and that is a
single join once the track is a real column rather than a heading you read past.

## Short answers to the questions that lead here

**Where does the track name live in a grid agenda?** In the column header, once, and
not in the cells. Read the header first and map each session to its column, or every
session arrives without the one field that makes a grid a schedule.

**The agenda prints times with no timezone. Which one applies?** The event's,
essentially always. Agendas print local times bare, so resolve the event zone once and
attach it to every session; your own machine's zone is the wrong answer and the
easiest one to record by accident.

**Why is my capture missing half the sessions?** Because the agenda splits by day
behind tabs that swap the grid without changing the URL. The failure is quiet: you get
a full, valid, complete-looking grid for one day.

**How stable is a conference agenda?** Not stable at all in the final weeks. Rooms
move, speakers cancel and sessions swap slots up to the morning of the event, which is
why a capture is stored as a dated record and not as the truth.

**See also:** [How to scrape accordion and tab content with
Playwright](how-to-scrape-accordion-and-tab-content-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md), [How to
scrape without getting blocked](how-to-scrape-without-getting-blocked.md)

## Sources

- MDN, the `time` element,
  https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/time - the
  `datetime` attribute as the machine-readable form of a printed time, checked for
  where an agenda may carry a zone the visible text omits.
- Playwright, Locators, https://playwright.dev/python/docs/api/class-locator - reading
  a header row once and mapping cells to it, and driving tabs that swap content
  without a navigation.
