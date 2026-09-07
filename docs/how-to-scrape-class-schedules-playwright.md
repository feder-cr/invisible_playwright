---
title: "How to scrape fitness class schedules with Playwright"
description: "Scrape fitness class schedules with Playwright: key each row by studio, instructor, room and start time instead of the occurrence id, fetch spots remaining and waitlist state from their own endpoints, and stamp every pull with the time it ran."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 132
---


# How to scrape fitness class schedules with Playwright

To scrape fitness class schedules with Playwright, read each row into a natural key
of studio, class name, room and start time instead of trusting the row's own
occurrence id, fetch spots remaining and waitlist state from the separate endpoints
that actually carry them instead of the rendered grid, walk the calendar forward one
week per request because there is no week-agnostic feed, and stamp every pull with
the time it ran so two reads of the "same" class can be compared honestly instead of
assumed identical.

A weekly class grid reads like a static table and is closer to a live report. Five
facts sit on every row, location, instructor, room, time and capacity, and any one of
them can change between two visits to what looks like the same class. The row you
scraped on Monday is not a record of Friday's class; it is a record of what the
booking system believed about Friday's class at the moment you asked. Treat it as
anything sturdier and the dataset drifts out from under you without ever throwing an
error.

## A class row is five facts, and the id is not one of the stable ones

A "class" on the page is really an occurrence: this instructor, in this room, at
this time, on this date, with this many spots open. Booking systems commonly mint a
fresh instance id for every occurrence they generate, so the id you captured for
Tuesday's 6am spin class this week has no guaranteed relationship to the id for the
same slot next week. Some systems do reuse an id across weeks; plenty do not, and the
two behave identically until you diff two weeks and every row looks new.

Do not build a pipeline that assumes the id survives. Build the key out of the facts
that describe the slot itself: studio, class name, room and the ISO start time
carries the schedule's meaning, and the occurrence id becomes metadata you store
alongside it, not the thing you key on.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/schedule?studio=downtown&week=2026-08-24")

    rows = page.locator(".class-row")
    classes = []
    for i in range(rows.count()):
        row = rows.nth(i)
        classes.append({
            "occurrence_id": row.get_attribute("data-occurrence-id"),
            "class_name": row.locator(".class-name").inner_text(),
            "instructor": row.locator(".instructor").inner_text(),
            "room": row.locator(".room").inner_text(),
            "start_time": row.get_attribute("data-start-iso"),
            "location": "downtown",
        })

    # the key that survives a week-to-week diff
    def natural_key(c):
        return (c["location"], c["class_name"], c["room"], c["start_time"])
```

Keep `occurrence_id` in the row. It is useful when you need to fetch capacity or
book a hold, and it is worthless as the field you match two scrapes against.

## Recurring classes are a template you are not allowed to see

Almost every studio runs Tuesday's 6am spin class every Tuesday, generated from a
recurrence template plus a list of exceptions: a holiday cancellation, a substitute
covering for the regular instructor, a one-off time change for a facility closure.
The page you scrape never shows you the template or the exception list. It shows you
the resolved occurrence, the template with any applicable exception already baked in,
and there is no call that hands back the underlying rule.

This matters for what you can and cannot claim about your own data. A row that says
"Jordan teaches Tuesday 6am" is true for that week and that week only. It is not
evidence of a recurring pattern, even though it looks exactly like one, because you
cannot tell from a single week's read whether Jordan is the standing instructor or
today's substitute. The only way to know the pattern is to have scraped enough
consecutive weeks to see it hold, and even then a studio can change the template
itself without announcing it anywhere you can query.

So do not infer a rule from one snapshot. Store what the page actually said, with the
week it applies to, and let the pattern emerge from several honest reads instead of
being asserted from one.

## Spots remaining lives behind its own endpoint

The weekly grid usually paints fast because it is not carrying the number that
changes the most. Spots remaining is fetched per class from a separate endpoint,
often lazily as each row scrolls into view or right after the grid finishes its
first paint, which means the grid you just navigated to is stale from the instant it
renders. Reading `.spots-remaining` out of the DOM the moment `goto()` returns gets
you whatever placeholder the template ships before that fetch resolves, not a real
count.

Capture the capacity responses directly instead of waiting on the paint. A response
listener catches every request matching the capacity path as it lands, keyed by
whatever field the payload uses to identify the class, and you attach that count to
the row after the fact rather than trusting what sits in the DOM. This is the same
technique behind [capturing XHR and API
responses](how-to-capture-xhr-api-responses-playwright.md), applied to a value the
grid never bakes in to begin with.

```python
capacities = {}

def on_response(response):
    if "/capacity" not in response.url:
        return
    if response.request.resource_type not in ("xhr", "fetch"):
        return
    try:
        data = response.json()
    except ValueError:
        return
    occurrence_id = data.get("occurrence_id")
    if occurrence_id:
        capacities[occurrence_id] = data

page.on("response", on_response)
page.goto("https://example.com/schedule?studio=downtown&week=2026-08-24",
          wait_until="networkidle")

for row in classes:
    payload = capacities.get(row["occurrence_id"], {})
    row["spots_remaining"] = payload.get("spots_remaining")
```

The same idea, sitting on the other side of a booking system, applies to
[scraping appointment availability](how-to-scrape-appointment-availability-playwright.md):
capacity is contested inventory fetched per request, never a number the calendar
carries for free.

## The waitlist enum hides in the button's class list, not its disabled flag

A booking button on a class row is usually driven by a small enum with four values:
open, full, waitlist available, waitlist full. The page expresses that enum through a
CSS class on the button rather than through any text you can read directly, and the
button is disabled in two of the four states, not one. Reading `is_disabled()` alone
tells you the button will not respond to a click; it does not tell you whether the
class is full with no waitlist or full with a waitlist that is itself exhausted, and
those are different facts about different weeks.

Read the class list and map it to the enum explicitly, and fall back to an
`unknown` state rather than guessing when a class you have not seen before shows up
on the list.

```python
STATE_FROM_CLASS = {
    "state-open": "open",
    "state-full": "full",
    "state-waitlist": "waitlist_available",
    "state-waitlist-full": "waitlist_full",
}

def waitlist_state(row):
    button = row.locator(".book-button")
    classes = (button.get_attribute("class") or "").split()
    for css_class, state in STATE_FROM_CLASS.items():
        if css_class in classes:
            return state
    return "unknown"
```

An `unknown` result is a signal to go look at the page, not a bug to silence. It
means the studio added a state your mapping does not cover yet, and folding it into
"full" by default is exactly the conflation this section exists to avoid.

## Pulling more than one week is N requests, and an empty week can mean two things

The schedule page defaults to the current week and moves one week at a time on a
forward click, so there is no single load that hands back a month of classes. A
multi-week pull is a loop over N requests, one per week, not one scroll or one wait
for more content to append. The step is cheap to write and easy to get wrong at the
edge: navigate far enough forward and the response comes back with zero rows, and
that empty week is visually identical whether it is a real closure or the page
refusing to render past its own booking horizon.

Distinguish the two before you trust either. A genuinely empty week (a holiday
closure, a studio between session blocks) usually renders the same shell the page
always renders, just with no rows in it. A week past the booking horizon often
carries its own marker, an error banner, a "schedule not yet published" notice, a
redirect back to the last valid week, and that marker is the signal to stop, not to
record a blank week and move on. The same distinction, applied to distinguishing a
soft block from a legitimate empty response, is the subject of [handling 403 and 429
responses mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

```python
from datetime import timedelta

def read_row(row, week_start):
    # the same shape as the first block, plus the week it came from
    return {
        "occurrence_id": row.get_attribute("data-occurrence-id"),
        "class_name": row.locator(".class-name").inner_text(),
        "instructor": row.locator(".instructor").inner_text(),
        "room": row.locator(".room").inner_text(),
        "start_time": row.get_attribute("data-start-iso"),
        "week_start": week_start.isoformat(),
    }

def pull_weeks(page, base_url, studio, start_week, max_weeks=8):
    all_classes = []
    for week_offset in range(max_weeks):
        week_start = start_week + timedelta(weeks=week_offset)
        url = f"{base_url}?studio={studio}&week={week_start.isoformat()}"
        page.goto(url, wait_until="networkidle")

        rows = page.locator(".class-row")
        count = rows.count()
        if count == 0:
            horizon_notice = page.locator(".schedule-error, .out-of-range-notice")
            if horizon_notice.count() > 0:
                break   # past what the site will schedule; stop asking
            # otherwise treat it as a real empty week and keep going
            continue

        for i in range(count):
            all_classes.append(read_row(rows.nth(i), week_start))
    return all_classes
```

Weeks navigated this way are also a fresh, unrelated request each time from the
site's point of view unless you carry state forward yourself; [scraping a date
picker or calendar widget](how-to-scrape-date-picker-calendar-playwright.md) covers
the same forward-navigation shape when the control is a click target rather than a
URL parameter.

## The schedule is a live document, and a comparison needs two timestamps

Instructor substitutions are one of the most common late changes a studio makes, and
they rarely show up more than a few days out. A row you scraped on Monday for a
Friday class can name a completely different instructor by Thursday, with nothing
else about the row changed, and neither read is wrong. Monday's read was accurate on
Monday. Thursday's read is accurate on Thursday. The row disagreeing with itself
across two scrape times is not corruption; it is exactly what a live document does.

The fix is not clever, it is disciplined: stamp every row with the time the scrape
ran, keep prior pulls instead of overwriting them, and diff by the natural key from
the first section against the timestamp rather than assuming the newest read is a
correction of the oldest one. A diff that finds a changed instructor is not a broken
record, it is a fact about the schedule dated to the moment each side of the
comparison was read, and it is exactly the shape [scraping only new items
incrementally](how-to-scrape-only-new-items-incremental-playwright.md) is built
around: compare against a keyed prior state, not against a running total.

```python
from datetime import datetime, timezone

def stamp(rows, scraped_at=None):
    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["scraped_at"] = scraped_at
    return rows

def diff_by_natural_key(previous_rows, current_rows):
    def key(r):
        return (r["location"], r["class_name"], r["room"], r["start_time"])

    previous = {key(r): r for r in previous_rows}
    changes = []
    for row in current_rows:
        prior = previous.get(key(row))
        if prior and prior["instructor"] != row["instructor"]:
            changes.append({
                "key": key(row),
                "from_instructor": prior["instructor"],
                "to_instructor": row["instructor"],
                "seen_at": (prior["scraped_at"], row["scraped_at"]),
            })
    return changes
```

Two timestamps side by side turn "the data is inconsistent" into "the instructor
changed between these two reads", which is a claim you can actually defend to
whoever consumes the dataset.

## Putting the pull together

The pieces compose into one run: pull N weeks, capture capacity as it lands, read
the waitlist enum off the button class, and stamp the whole batch with when it ran.

```python
def scrape_schedule(studio, start_week, max_weeks=4):
    with InvisiblePlaywright(seed=42) as browser:
        page = browser.new_page()

        capacities.clear()   # the module-level store the listener writes into
        page.on("response", on_response)

        rows = pull_weeks(page, "https://example.com/schedule",
                           studio, start_week, max_weeks)
        for row in rows:
            payload = capacities.get(row["occurrence_id"], {})
            row["spots_remaining"] = payload.get("spots_remaining")

        return stamp(rows)
```

Nothing here is a wrapper method to memorize. `new_page()`, `goto()`, `locator()`
and `page.on("response", ...)` are the same Playwright API you already use; the
browser this library hands back is a real Playwright `Browser`. The additions are
the key you choose, the endpoints you listen for, and the timestamp you attach, and
all three exist because the page itself will not hand you a stable, complete answer
in one request.

## Conclusion

A class schedule looks like one page and is actually four moving parts wearing a
single grid: a resolved occurrence standing in for a template you cannot see, a
capacity number fetched separately from the row that displays it, a waitlist state
expressed through a class name instead of a boolean, and a calendar that only moves
forward one week at a time. Key rows by what describes the slot, not by an id the
booking system may regenerate weekly. Fetch spots and waitlist state from where they
actually live. Stop a multi-week pull on a real horizon marker, not on the first
empty response. Stamp every row with when it was read, because the schedule you are
scraping keeps changing after you leave the page, and a comparison across two visits
is only honest with both timestamps attached.

## Short answers to the questions that lead here

**Can I use the class id as a primary key across weeks?** Usually not. Many booking
systems mint a new occurrence id per instance, so the same Tuesday 6am slot next
week can carry a different id with nothing else about it changed. Key on studio,
class name, room and start time instead, and keep the id as metadata.

**Why does the grid show a capacity number that turns out to be wrong?** Because it
often is not the real number yet. Spots remaining is commonly fetched from a
separate endpoint after the grid's first paint, so reading the DOM immediately after
`goto()` can catch a placeholder. Capture the capacity response directly instead.

**Why does a disabled booking button not tell me if the waitlist is open?** Because
disabled covers two different states, full-with-waitlist and waitlist-full, and the
distinction lives in the button's CSS class, not in whether it responds to a click.
Map the class list to the enum explicitly.

**How far forward can I pull the schedule?** Until the site stops publishing it,
which is usually sooner than you would guess. An empty week can mean a real closure
or a request past the booking horizon, and the two look the same unless the page
also renders an explicit marker for the second case. Stop on the marker, not on the
first zero-row response.

**Why does the same class show a different instructor on two different scrape
dates?** Because the schedule is a live document and substitutions are a common late
change. Neither read is wrong; they are dated observations of the same slot at two
different moments. Stamp every row so a diff can say when each side was read.

**Can I reconstruct the recurrence rule from one week's data?** No. The page shows
you the resolved occurrence with any exception already applied, never the template
underneath. A pattern only becomes trustworthy after enough consecutive weekly reads
show it holding, and even then the studio can change the template without any call
you can query for it.

## Sources

- Playwright's [`page.on("response")`](https://playwright.dev/python/docs/api/class-page#page-event-response)
  and [`Locator`](https://playwright.dev/python/docs/api/class-locator) API, used
  exactly as documented upstream to capture the capacity fetch and read the button's
  class list.
- This project's own configuration behaviour: the browser returned by
  `InvisiblePlaywright` is a real Playwright `Browser`, so response listeners,
  locators and navigation work with no wrapper-specific method to learn.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the response-listening mechanics behind the capacity fetch,
[scraping appointment availability](how-to-scrape-appointment-availability-playwright.md)
for the same contested-inventory shape on a booking calendar,
[scraping a date picker or calendar widget](how-to-scrape-date-picker-calendar-playwright.md)
for forward navigation when the control is a click target instead of a URL parameter,
and [scraping only new items incrementally](how-to-scrape-only-new-items-incremental-playwright.md)
for diffing a keyed dataset against its own prior state.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A week-over-week diff
run against the occurrence id reported every class in the studio as newly created
and every prior week's class as cancelled, when nothing had actually changed except
the id the booking system regenerated on schedule; switching the key to location,
class name, room and start time made the same diff report exactly what had changed,
which that week was one substitute instructor.*
