---
title: "How to scrape museum and gallery exhibition dates with Playwright"
description: "Scrape museum and gallery exhibition dates with Playwright: read the run's start and end dates from the page's own Event node, key each run by exhibition plus venue, and track a last-checked timestamp so an extension does not read as stale data."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 135
---


# How to scrape museum and gallery exhibition dates with Playwright

To scrape museum and gallery exhibition dates with Playwright, read the run as a range
instead of a point: pull `startDate` and `endDate` from the page's own Event node instead
of a single displayed date, key each run by the pair of exhibition and venue so a touring
show does not collapse into one row, store a last-checked timestamp so a moved close date
reads as an extension instead of an error, and keep a timed-entry booking widget's slot
data out of the run fields entirely, since the two answer different questions.

An exhibition is not an event the way a concert is. A concert happens once, at a stated
hour, and the thing you track is whether tickets are on sale yet. An exhibition opens on
one date and stays open for weeks or months, admission is frequently folded into general
entry rather than sold per show, and there is no on-sale window at all, only a run. The
[guide to scraping ticketed event listings](how-to-scrape-event-and-ticket-listings-playwright.md)
covers the concert case: a single start time, a seat count that depletes, a countdown to
opening. This page covers the opposite shape, and treating a run like a point date is
where most exhibition scrapers go wrong first.

## An exhibition is a run, not a date

The field you want is rarely a single `date` attribute. It is a pair, an open date and a
close date, and the page usually states both even when the visible copy only shows one
("On view through March 2027"). Read both ends of the range and store them as a pair, not
as a single "exhibition date" column with the close date thrown away, because the close
date is the one that moves.

Admission matters here too. A ticketed concert scraper watches a state that flips from
"not yet on sale" to "on sale" to "sold out". An exhibition usually has no such state: it
is either within its run or it is not, and general admission covers it the way it covers
the rest of the museum. Do not model a "sold out" field for an exhibition unless the venue
has actually added one, which the next section covers separately.

## Read the run from the page's own Event node

Most museum and gallery sites that bother with structured data describe an exhibition as
an `Event`, often typed more specifically as `ExhibitionEvent`, with `startDate`, `endDate`
and a `location` pointing at a `Place` node for the venue. That structured node is more
reliable than the visible date string, which is often phrased loosely ("through the end of
spring") for a human reader while the JSON-LD carries an exact ISO date underneath.

```python
import json
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.org/exhibitions/light-and-space", wait_until="networkidle")

    events = []
    for handle in page.query_selector_all('script[type="application/ld+json"]'):
        raw = handle.text_content()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blocks = data if isinstance(data, list) else data.get("@graph", [data])
        for node in blocks:
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "ExhibitionEvent" in types or "Event" in types:
                events.append(node)

    for ev in events:
        venue = ev.get("location", {}) or {}
        print(ev.get("name"), ev.get("startDate"), ev.get("endDate"), venue.get("name"))
```

When a page ships no JSON-LD at all, the fallback is the same one used for menus and
listings elsewhere in this corpus: read the rendered date string with a locator and parse
it defensively, because free text ("Sept 2026 - Jan 2027") needs its own small parser
rather than a strict `datetime.fromisoformat` call. The mechanics of reading a page's own
structured data in general, not just for exhibitions, are in
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md).

## An extension keeps the same page, so track a last-checked timestamp

Exhibitions get extended past their announced close date more often than concerts get
rescheduled, and an extension is almost never a new page. The venue edits the same URL,
the same JSON-LD node, and the close date simply becomes a later one. Nothing marks the
change as an extension rather than a correction or a typo fix, so a single scrape of a
single row cannot tell "this just got extended" from "this data has always been stale".

The fix is cheap and easy to skip: store when you last checked, alongside what you found,
so the next scrape has something to compare against.

```python
from datetime import datetime, timezone

def merge_run(previous, current):
    """previous and current are the stored and freshly scraped rows for the
    same (exhibition, venue) key. Returns current, annotated."""
    current["last_checked"] = datetime.now(timezone.utc).isoformat()
    if previous is None:
        current["extended"] = False
        current["first_seen_end_date"] = current.get("end_date")
        return current

    old_end = previous.get("end_date")
    new_end = current.get("end_date")
    current["extended"] = bool(old_end and new_end and new_end > old_end)
    current["first_seen_end_date"] = previous.get("first_seen_end_date", old_end)
    return current
```

`first_seen_end_date` is worth keeping even after the row updates. A show announced through
March that keeps getting pushed to April, then May, tells a different story than one that
moved once, and you lose that history the moment you overwrite the field in place instead
of appending to it.

## Timed-entry slots are a booking layer, not the run

Popular exhibitions frequently add a second system on top of the run: timed entry, where a
visitor picks a specific half-hour slot within the open dates rather than walking in at
will. That slot picker is a separate booking layer, built for capacity management, and it
is not the same thing as the exhibition being open or closed. A Tuesday with every slot
booked out is not a Tuesday the exhibition is closed. It is a Tuesday you cannot read from
the run dates alone.

Conflating the two produces a very specific and very wrong row: a scraper that reads "no
slots available today" as "closed" will report an exhibition as ended weeks before its
actual close date, every time a popular weekend books out early. Keep the two fields
apart, and only read the booking widget when it actually exists on the page. The mechanics
of stepping the slot picker itself, the panel-by-panel loop and the wait for its own data
request, match the [date picker and calendar guide](how-to-scrape-date-picker-calendar-playwright.md)
almost exactly; what differs here is what the two fields mean once you have them.

```python
def read_run_and_booking(page):
    run = {
        "start_date": page.get_attribute("[data-testid='exhibition-run']", "data-start"),
        "end_date": page.get_attribute("[data-testid='exhibition-run']", "data-end"),
    }

    widget = page.query_selector("[data-testid='timed-entry-widget']")
    if widget is None:
        run["timed_entry_required"] = False
        run["next_available_slot"] = None
        return run

    run["timed_entry_required"] = True
    slot = widget.query_selector("[data-state='available']")
    run["next_available_slot"] = slot.get_attribute("data-date") if slot else None
    return run
```

`next_available_slot` can legitimately be `None` while `end_date` is still weeks away. That
is not a contradiction, it is the booking layer and the run answering two different
questions, and a downstream reader who only sees one merged "status" field cannot tell
them apart.

## A touring exhibition needs the venue in its identity

A single exhibition frequently visits several venues on a tour, and each stop carries its
own local run, its own open date and its own close date at that location. If a scraper
keys its rows by exhibition name alone, the second venue's dates silently overwrite the
first's, and a show that is still running in one city reads as closed everywhere the
moment its earlier stop ends.

Structured data usually helps here without extra work: a touring show is often described
as several `ExhibitionEvent` nodes, each with its own `location`, on the pages that list
every stop. Building the row key from both fields, not just the exhibition's name, keeps
the legs from colliding.

```python
def run_key(event_node):
    venue = event_node.get("location", {}) or {}
    venue_id = venue.get("name") or venue.get("@id") or ""
    exhibition_id = event_node.get("name") or event_node.get("@id") or ""
    return (exhibition_id.strip().lower(), venue_id.strip().lower())

runs_by_key = {}
for ev in events:
    key = run_key(ev)
    venue = ev.get("location", {}) or {}
    runs_by_key[key] = {
        "exhibition": ev.get("name"),
        "venue": venue.get("name"),
        "start_date": ev.get("startDate"),
        "end_date": ev.get("endDate"),
    }
```

The venue is not a display column here, it is part of what makes a row unique. Matching a
venue name to a stable identifier across pages that spell it slightly differently is the
same problem as matching a physical location for any other kind of listing, and
[scraping store locator pages](how-to-scrape-store-locator-pages-playwright.md) covers the
matching mechanics in more depth than a one-off touring page needs.

## Closure days belong to the venue, not the exhibition

A museum closes on a fixed weekday, most often Monday, and closes again for a handful of
holidays every year. Neither fact lives on the exhibition. Both live on the venue, usually
as the `Place` node's `openingHoursSpecification`, sitting next to the `location` field the
Event node already points at. A scraper that only reads the exhibition's own dates will
happily report a show as running on a day the building itself is shut.

That distinction only matters if you plan to answer "is it open today", not just "is it
within its run", and the two questions need different data. Read the venue's hours
separately and cross them against the run before answering either one.

```python
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def venue_closed_on(opening_hours_spec, check_date):
    """A day of the week absent from the venue's spec list means the venue
    is closed that day, regardless of the exhibition's own run dates."""
    weekday = WEEKDAYS[check_date.weekday()]
    for spec in opening_hours_spec:
        days = spec.get("dayOfWeek")
        days = days if isinstance(days, list) else [days]
        if any(weekday in d for d in days if d):
            return False
    return True

def exhibition_open_on(run, opening_hours_spec, check_date):
    within_run = run["start_date"] <= check_date.isoformat() <= run["end_date"]
    return within_run and not venue_closed_on(opening_hours_spec, check_date)
```

Store the venue's hours once per venue, not once per exhibition. A dozen shows at the same
museum share one closure calendar, and re-scraping it from every exhibition page is wasted
work that also risks a dozen slightly different copies drifting apart.

## Tell a permanent collection piece from a temporary exhibition

The same listing page that carries this month's temporary shows often carries permanent
collection highlights in an identical card layout, and those cards have no close date at
all, not because the field failed to parse but because nothing is scheduled to end. A
missing `endDate` on a genuinely temporary show is a parsing problem worth chasing down.
The same absence on a permanent piece is the correct, complete answer, and treating it as
an error produces a scraper that keeps retrying a field that was never going to appear.

The type on the structured node is usually the cleanest signal: a temporary show is typed
as an `Event` or `ExhibitionEvent` with a start and an end, while a permanent piece is
described as the artwork or collection item itself, with no event wrapper around it at
all.

```python
def is_temporary_exhibition(node):
    types = node.get("@type")
    types = types if isinstance(types, list) else [types]
    if "ExhibitionEvent" in types or "Event" in types:
        return True
    # a permanent collection item shares the card layout but carries no
    # Event wrapper and no endDate field, not an endDate that failed to parse
    return "endDate" in node
```

If the venue's own site does not distinguish the two in its markup, look for the words the
page itself uses: "permanent collection", "on view", "ongoing" next to the item, versus a
stated close date next to the temporary shows on the same grid. A missing field paired
with that wording is a signal to store a deliberate null end date, not a retry target.

## Conclusion

An exhibition scraper fails for reasons a concert scraper never runs into. It reads a
single displayed date where the real value is a range. It cannot tell an extension from
stale data because nothing records when it last looked. It confuses a fully booked timed
entry slot with a closed show. It lets a touring exhibition's second stop overwrite the
first because the venue was never part of the row's identity. It reports a museum open on
its weekly closure day because it never read the venue's own hours. And it treats a
permanent collection piece's missing close date as a bug instead of the answer. Fix all
six and the run you extract, and the "is it open right now" you compute from it, both hold
up against a page that keeps quietly changing underneath you.

## Short answers to the questions that lead here

**Why doesn't an exhibition scraper track an on-sale window the way a ticketed event does?**
Because most exhibitions have no on-sale window at all. Admission is usually general entry
rather than sold per show, and the thing that matters is whether today falls inside the run,
not whether tickets have gone live.

**How do I know if a moved close date is a real extension and not stale data?** Store a
last-checked timestamp with every scrape and compare the new close date against the one you
saw last time. A later close date on a later check is an extension; a close date that never
changes across many checks is just a stable run.

**My scraper says an exhibition is fully booked. Is that the same as closed?** No. A timed
entry slot picker is a separate booking layer added on top of the run for capacity control.
No available slots on one day means that day is booked out, not that the run has ended.

**A touring exhibition shows as closed even though it's still running somewhere. Why?** The
scraper is probably keying rows by exhibition name alone, so a later venue's dates overwrite
an earlier one's. Key each row by exhibition and venue together.

**Why does my scraper say a show is open on a day the museum is actually closed?** Because
closure days, the weekly closure and holidays, belong to the venue, not the exhibition. Read
the venue's opening hours separately and cross them against the run before answering "open
today".

**A listing has no close date at all. Is that a scraping error?** Not necessarily. Permanent
collection items appear in the same layout as temporary shows and have nothing scheduled to
end. Check the node's type or the page's own wording before treating a missing end date as a
failure.

## Sources

- schema.org [`ExhibitionEvent`](https://schema.org/ExhibitionEvent) and
  [`Event`](https://schema.org/Event), the types most museum and gallery pages use to carry
  `startDate`, `endDate` and a `location` pointing at the venue.
- schema.org [`Place`](https://schema.org/Place) and
  [`openingHoursSpecification`](https://schema.org/openingHoursSpecification), which carry a
  venue's weekly closure day and holiday hours separately from any exhibition hosted there.
- Playwright's own [`query_selector_all`](https://playwright.dev/python/docs/api/class-page#page-query-selector-all)
  and [`get_attribute`](https://playwright.dev/python/docs/api/class-elementhandle#element-handle-get-attribute),
  exercised through the real Playwright `Browser` this project returns unchanged.

**See also:** [scraping ticketed event listings](how-to-scrape-event-and-ticket-listings-playwright.md)
for the single-date, on-sale-window shape this page contrasts with,
[scraping a date picker or calendar widget](how-to-scrape-date-picker-calendar-playwright.md)
for the mechanics behind stepping a timed-entry slot picker,
[scraping store locator pages](how-to-scrape-store-locator-pages-playwright.md) for matching
a venue name to a stable identifier across a touring show's stops, and
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for reading a page's own structured node in general.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A touring show once read as
closed everywhere the day its first city's run ended, because the row was keyed on the
exhibition's name alone and the second venue's dates had silently overwritten the first.*
