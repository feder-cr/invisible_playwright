---
title: "How to scrape public transport timetables with Playwright"
description: "Scrape public transport timetables with Playwright: key every row by route, direction, stop and service day, pin the date, keep 24:15 times and request-stop flags, and store the trip id with its validity period."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 102
---


# How to scrape public transport timetables with Playwright

**To scrape a public transport timetable with Playwright, treat every departure as a
function of route, direction, stop and service day rather than a cell in a grid: pin the
date instead of accepting today's, keep clock values like `24:15` as minutes from the
start of the service day, preserve the flags that stand where a time should be, and
store the trip identifier and validity period that the payload carries and the grid
hides.**

A timetable page looks like the most static thing on the web, so the obvious approach is
to read the grid, write the times into a column and call the job finished. That file is
unaddressable: no row says which journey it belongs to, which days it runs, or which
edition printed it.

Nothing about that failure is loud. The scrape succeeds, the numbers are genuine, and
they are simply not a schedule. The gap only shows months later, when a second scrape
disagrees with the first and nobody can say which half is wrong.

## A timetable is a function, not a table

Four coordinates decide which numbers appear on the page: route, direction, stop and
service day. The grid is one projection of that function, chosen for you before the page
rendered, so numbers stored without those coordinates lose the part that made them mean
something.

Direction is the coordinate that fails silently. One stop is served by the outbound
journey and the inbound journey, so it carries two schedules under one name. If the page
switches direction through a tab that does not change the URL, two runs write rows that
look identical in shape and belong to opposite journeys. Sort the merged result by time
and you get a service running twice as often as it does, alternating between two
directions of travel. No exception is raised.

So the key comes first and the times second. Every row carries `route_id`,
`direction_id`, `stop_id` and `service_day` before a single clock value, and direction is
read from the request or the control's state, not from which tab looks highlighted.

## Pin the service day, because the page will pick today

A timetable page renders the pattern that applies to the current date and rarely says
which one it chose. Service days are patterns, not dates: weekday, Saturday, Sunday and
holiday grids, plus a school-term variant on some networks. The same URL returns
different numbers depending on when you ran the job, and no row records which pattern it
came from.

Pin the date in the request when the page accepts one, and read it back before you trust
the grid. A date parameter the page quietly ignores is worse than none, because it hands
you today's timetable labelled with the day you asked for.

```python
from datetime import date
from invisible_playwright import InvisiblePlaywright

SERVICE_DAYS = {
    "weekday":  date(2026, 9, 8),    # a Tuesday, clear of public holidays
    "saturday": date(2026, 9, 12),
    "sunday":   date(2026, 9, 13),
}

def open_timetable(page, route, direction, stop, when):
    page.goto(
        f"https://example.com/timetable?route={route}&dir={direction}"
        f"&stop={stop}&date={when.isoformat()}",
        wait_until="networkidle",
    )
    shown = page.locator("[data-timetable-date]").get_attribute("data-timetable-date")
    if shown != when.isoformat():
        raise RuntimeError(f"the page ignored the date: asked {when}, got {shown}")
    return page
```

Choose those dates by hand and keep them clear of public holidays, since a holiday
falling on a Tuesday returns the Sunday pattern from a URL that says Tuesday. When the
only way in is a calendar widget, the same read-back applies after the click:
[scraping a date picker or calendar](how-to-scrape-date-picker-calendar-playwright.md).

## 24:15 is a real departure and 00:15 is a different row

Timetables count hours from the start of the service day rather than from midnight, so a
journey leaving fifteen minutes after midnight is printed `24:15` and one leaving at
three minutes past one is `25:03`. Hand either to `datetime.strptime(text, "%H:%M")` and
it raises `ValueError` on an hour above 23.

The crash is the good outcome. The damaging version takes the hour modulo 24 and writes
`00:15` against the same service day, which puts a departure fifteen minutes after
midnight at the start of that day instead of the end. The last bus of Saturday night then
sorts ahead of the first bus of Saturday morning.

The convention is worth keeping, because a Saturday night service running until 02:00
belongs to Saturday's pattern and Sunday may not run that route at all. Parse to an
integer offset, store that as the canonical value, and derive a timestamp when something
downstream needs one.

```python
import re
from datetime import date, datetime, time, timedelta

CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")

def parse_service_time(text):
    """Minutes from the start of the service day. '24:15' -> 1455."""
    match = CLOCK.match(text.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes > 59 or hours > 47:
        raise ValueError(f"not a service time: {text!r}")
    return hours * 60 + minutes

def to_wall_clock(service_day: date, offset: int) -> datetime:
    """1455 on a Saturday -> Sunday 00:15, the calendar day it departs."""
    return datetime.combine(service_day, time(0, 0)) + timedelta(minutes=offset)
```

The integer also fixes sorting. Ordering by the rendered string puts `24:15` before
`8:00`, since `2` precedes `8` in a lexical comparison, and that alone has produced
first-departure figures three hours out.

## A request stop carries a flag where a time should be

Some cells hold a symbol instead of a number, and the symbol is data. A stop can be
request-only, where the vehicle calls only if someone signals; set-down only, where
passengers may leave but not board; pick-up only, the reverse; or not served by that
journey at all. The marker renders as a superscript letter, a footnote reference or a
small glyph, sometimes in place of the time.

A parser that pulls the digits and discards the rest turns a conditional stop into a
scheduled one. Downstream that reads as a departure a passenger can wait for, at a stop
where the vehicle drives past unless someone raises a hand. Classify each cell into a
time and a set of flags, keeping the raw token you do not recognise.

```python
FLAG_TOKENS = {
    "x": "request_only",
    "s": "set_down_only",
    "p": "pick_up_only",
    "|": "does_not_call",
    "-": "does_not_call",
}

def read_cell(cell):
    """Return (offset_or_None, flags) for one stop on one trip."""
    notes = [t.strip().lower() for t in cell.locator("sup, .footnote-ref").all_inner_texts()]
    flags = {FLAG_TOKENS.get(n, f"note:{n}") for n in notes if n}

    text = (cell.inner_text() or "").strip()
    for note in notes:                       # the marker sits inside inner_text too
        text = text.replace(note, "").replace(note.upper(), "")
    text = text.strip()

    if text in FLAG_TOKENS:                  # a flag standing in for the time
        flags.add(FLAG_TOKENS[text])
        return None, sorted(flags)
    return parse_service_time(text), sorted(flags)
```

Scrape the legend in the same run. The glyph-to-meaning mapping is printed once at the
foot of the page and differs between operators, so a hardcoded table ages badly. An
unrecognised marker should survive as `note:<token>` rather than vanish, and a blank cell
should stay distinguishable from a dash, since only the legend says what blank means. The
row and column walk underneath is ordinary table work:
[scraping HTML tables](how-to-scrape-html-tables-playwright.md).

## The trip identifier is in the response, not in the grid

The payload behind the grid almost always carries a trip identifier, and the rendered
table almost never prints it. That identifier is the only key that survives a timetable
change, which makes capturing the response worth more than better selectors.

Column position works until the operator inserts one early-morning journey and every
later column shifts by one, at which point a diff reports that every trip changed.
Departure time is no better: a journey retimed by two minutes is the same journey, but a
time-keyed diff records one deletion plus one insertion, and fifty retimed trips look
like a network rebuild. An identifier moves with the trip.

```python
trips = {}

def on_response(response):
    if "timetable" not in response.url:
        return
    if response.request.resource_type not in ("xhr", "fetch"):
        return
    try:
        payload = response.json()
    except Exception:
        return
    for trip in payload.get("trips", []):
        trips[trip["id"]] = trip           # tripId, journeyRef, vehicleJourney

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("response", on_response)
    open_timetable(page, route="12", direction="outbound", stop="all",
                   when=SERVICE_DAYS["weekday"])
```

The payload usually holds every stop time for the whole trip rather than one stop's
column, and it carries the pick-up and set-down rules as codes instead of glyphs. When the
page is server rendered and no request fires, the identifier is often still in a
`data-trip` attribute or the href of the journey-detail link, so read attributes rather
than text. The hooks are in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## Version every row with the validity period

A timetable is valid between two dates and gets replaced whole. A row without its
validity period is undated evidence, and two scrapes taken months apart then look like a
service change that never happened.

Three different events produce that same diff. The operator changed the service. You
scraped a different service day. Or you scraped the same service day under two editions
of the timetable, which happens whenever the second run lands after a changeover date.
Without `valid_from` the third case is indistinguishable from the first, so every
seasonal changeover enters your history as a real service cut.

The period is usually printed beside the grid or exposed in the payload as `validFrom`
and `validTo`. If nothing states it, record the scrape date, treat it as a lower bound,
and say so in the schema.

```python
def make_row(key, trip_id, offset, flags, scraped_at):
    return {
        "route_id": key.route_id,
        "direction_id": key.direction_id,        # outbound and inbound stay separate
        "stop_id": key.stop_id,
        "service_day": key.service_day,          # weekday, saturday, sunday, holiday
        "valid_from": key.valid_from,            # from the page, not from the clock
        "valid_to": key.valid_to,
        "trip_id": trip_id,                      # stable across the next edition
        "offset_minutes": offset,                # 1455, never the string "24:15"
        "flags": ",".join(flags),                # request_only, set_down_only, ...
        "scraped_at": scraped_at,
    }
```

The natural primary key is route, direction, stop, service day, `valid_from` and
`trip_id` together. Under it a re-scrape of one edition is an idempotent upsert, and a new
edition writes rows beside the old ones instead of overwriting them, so the previous
timetable stays queryable:
[scraping into a database](how-to-scrape-into-a-database-playwright.md).

## Sweeping a route without looking like enumeration

One route with forty stops, in two directions, across three service days, is 240 page
loads, and that walk is the request pattern a transit front end is built to notice. The
tell is the sequence: one identity requesting stop ids in ascending order, evenly spaced,
from one address, at a rate no passenger would produce.

Hold one seed-stable identity for the whole run so the host sees a single browser rather
than a new device at every stop, shuffle the work so the order is not a counter, and
space the visits unevenly.

```python
import random

def sweep(route, stops, directions=("outbound", "inbound"), seed=42):
    rng = random.Random(seed)
    work = [(s, d, day) for s in stops for d in directions for day in SERVICE_DAYS]
    rng.shuffle(work)                       # not stop 1, 2, 3 in a straight line

    rows = []
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.on("response", on_response)
        for stop, direction, day in work:
            open_timetable(page, route, direction, stop, SERVICE_DAYS[day])
            rows.extend(read_grid(page, route, direction, stop, day))
            page.wait_for_timeout(rng.randint(1200, 4000))
    return rows
```

Drawing the identity and the visit order from one seed keeps the run reproducible: pass
`seed=42` and the same fingerprint and the same sequence come back. Pacing the sweep to a
rate the front end tolerates is a separate decision:
[rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md).

## Where this approach stops helping

Check for a published feed before you write a selector. Many operators release their
schedule as a standard open data file, with trip identifiers, the service calendar and
the pick-up and drop-off codes already normalised, so every section above becomes work
done by hand to rebuild what the file states. Scrape the HTML when no feed exists, when
the feed lags the site, or when the site shows what the feed omits.

[A live departure board](how-to-scrape-flight-status-boards-playwright.md) is also not a
timetable. It shows predictions for the next half
hour and drops a journey once it has gone, so repeated scrapes give a record of
predictions that cannot be diffed against a schedule. The same separation applies to
disruption overlays: a struck-through time means "cancelled today" and belongs in its own
table keyed by date. Fold today's cancellations into the schedule rows and next week's
run reports a service restoration that is really just a normal Tuesday.

## Conclusion

Timetable scraping goes wrong in the parts that never raise an exception. A missing
direction merges two opposite journeys into one impossible service. An unpinned date
returns whichever pattern today happens to be. A naive format string either crashes on
`24:15` or rolls a night departure back to the start of the day. A dropped footnote turns
a request stop into a scheduled one. A missing trip identifier makes the next edition look
like a rebuild, and a missing validity period makes a changeover look like a service cut.
All of it is fixed in the row shape, not in the selectors: key by route, direction, stop
and service day, store an integer offset with a flag set, and carry the trip identifier
and validity period on every row.

## Short answers to the questions that lead here

**Why does my dataset show twice the real service at one stop?** You merged both
directions. Outbound and inbound share a stop name, so rows from both sides of a
direction toggle look identical in shape and interleave into a service that does not
exist. Put `direction_id` in the key.

**`datetime.strptime` throws on 24:15. What is the fix?** Parse to minutes from the start
of the service day, so `24:15` becomes 1455, and derive a timestamp from that when you
need one. Do not take the hour modulo 24, which moves the departure back a full day.

**Which date should I scrape?** Explicit ones you chose, not whatever today is. Pick a
weekday, Saturday and Sunday clear of public holidays, pass the date in the request, and
read it back off the page before trusting the grid.

**The cell has a letter instead of a time. Can I skip it?** No. It is very likely a
request stop, a set-down-only stop or a stop that trip does not serve, and dropping the
marker turns a conditional stop into a scheduled departure. Store the flag beside the
time.

**What should the primary key be?** Route, direction, stop, service day, `valid_from` and
`trip_id`. That makes a re-scrape of one edition an idempotent upsert and lets a new
edition land alongside the old one instead of overwriting it.

**Two scrapes months apart disagree everywhere. Did the service change?** Usually not. A
new edition took effect between the runs, and without a validity period on each row there
is no way to tell that from a real change. Compare rows only within the same `valid_from`.

## Sources

- Playwright's [`page.on("response")`](https://playwright.dev/python/docs/api/class-page#page-event-response)
  and [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  used for the trip-identifier capture, retrieved 2026-08-28.
- Playwright's [`locator.get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute)
  and [`locator.all_inner_texts`](https://playwright.dev/python/docs/api/class-locator#locator-all-inner-texts),
  used for the date read-back and the footnote markers, retrieved 2026-08-28.
- Playwright's [`page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto)
  and its `wait_until` states, retrieved 2026-08-28.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the trip identifier, [scraping HTML tables](how-to-scrape-html-tables-playwright.md)
for the row and column walk, [scraping into a database](how-to-scrape-into-a-database-playwright.md)
for the upsert key, and [scraping only new items](how-to-scrape-only-new-items-incremental-playwright.md)
for re-running a sweep.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The 24-hour clock is the one
that shipped wrong here: a night service parsed with `%H:%M` was written back as 00:15 on
the same service day, so the last departure of Saturday night sorted ahead of the first one
of Saturday morning.*
