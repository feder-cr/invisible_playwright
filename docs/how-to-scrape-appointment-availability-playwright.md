---
title: "How to scrape appointment availability with Playwright"
description: "Scrape appointment availability with Playwright: key each slot to a provider, service type and location, capture the per-period calendar response, and read the grid without clicking a slot into a hold."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 100
---


# How to scrape appointment availability with Playwright

To scrape appointment availability with Playwright, capture the calendar response the
page fetches for each week or month instead of reading the painted cells, key every row
to the provider, the service type and the location it was resolved for, stamp it in UTC
when the response lands, and stop at the grid instead of clicking a slot, because in
many systems selecting one places a hold that takes it away from every other visitor.

An appointment grid looks like a published schedule and behaves like contested
inventory. What the server returns is the answer to a narrow question: this practitioner,
this appointment type, this site, at this second. Load it again a minute later and the
answer can be different, because somebody booked in between. That volatility is ordinary.
The hold is what makes this calendar different from every other one, and it is the reason
the scraper stops at the grid.

## A slot belongs to a provider, a service type and a location

Three inputs decide which times come back. A row that records none of them is not data,
because nobody can say afterwards what question it answered.

One practitioner works a different diary on Tuesday at one site than on Thursday at
another, so the location is not decoration. The service type matters more than it looks.
Duration quantizes the day: a sixty-minute new-patient visit cannot fit into gaps that a
fifteen-minute follow-up fills easily, so two service types read two different sets of
times out of the same underlying diary. Ask for the wrong one and the scarcity you record
is your own.

Treat the triple as the key rather than as metadata bolted on afterwards:

```python
row = {
    "provider_id": "",        # the practitioner or resource the grid belongs to
    "service_type": "",       # appointment type; it decides the slot duration
    "location_id": "",        # the site, since one provider works several
    "read_at": "",            # UTC, stamped when the response landed
    "slot_start_local": "",   # start time as the system printed it, untouched
    "slot_timezone": "",      # the location's zone, not the scraper's
    "duration_minutes": None,
    "status": "",             # "open", "taken", or "not_bookable_online"
    "horizon_end": "",        # last date this reading was allowed to see
}
```

The three scoping fields are part of what the row asserts, not context around it. Without
them, two readings taken a day apart describe two different questions instead of a change
in availability, and any series built on top measures the scraper's own inputs. The triple
is usually chosen through a form before the calendar renders at all, and each change fires
a fresh query;
[how to scrape search results behind a form](how-to-scrape-search-results-form-playwright.md)
covers driving that selection.

## Read the calendar response, not the painted cells

The painted cell is a rendering, and it has already thrown away most of what makes a slot
useful. A time and a CSS class survive. The slot identifier, the duration, the appointment
mode and the reason a day came back empty usually do not, because the page never needed to
print them.

The payload behind the grid keeps all of it. There is a second reason to read it, specific
to this domain: many systems offer a "first available" view that shows times without naming
who they belong to, and the response still carries the provider identifier for each one.
Subscribe to responses, hold the parsed bodies, and read every field from there.

```python
calendars = []

def on_response(resp):
    if "/availability" in resp.url and resp.request.resource_type in ("xhr", "fetch"):
        calendars.append(resp.json())

page.on("response", on_response)
page.goto("https://example.com/find-appointment", wait_until="domcontentloaded")
```

One page load commonly fires several calls that look alike: the calendar grid, a provider
summary, an eligibility check. Separating them by URL alone gets brittle quickly, and
[how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
covers matching on the body when the paths collide.

## Paging the calendar is a request, not a scroll

The calendar is fetched one week or one month at a time. Clicking the forward arrow does
not reveal rows that were already sitting in the DOM. It asks the server a new question,
and the answer arrives as a response you have to wait for. Scrolling achieves nothing at
all here, because there is nothing below the fold to reach.

```python
def read_next_period(page):
    with page.expect_response(
        lambda r: "/availability" in r.url and r.status == 200
    ) as info:
        page.click("[data-testid='next-period']")
    return info.value.json()
```

`expect_response` opens the wait before the click, so the response cannot land in the gap
between the two statements. A loop that clicks and then reads the DOM after a fixed sleep
records the previous period twice under two different dates, and nothing in the output
looks wrong.

Month grids carry one extra trap. The rendered grid almost always includes trailing days
of the previous month and leading days of the next, so a naive walk across three months
collects those edge days three times, or misfiles them under the month that drew them.
Deduplicate on the slot's own date and identifier, never on its position in the grid. The
forward-and-back mechanics of the widget itself are the same ones in
[scraping date-picker calendars](how-to-scrape-date-picker-calendar-playwright.md).

## No availability and not bookable online are different findings

An empty grid has at least three causes, and they mean different things. The provider is
fully booked through the window. The provider does not publish availability online at all
and takes appointments by phone. Or the provider publishes availability, but not for the
service type you asked about, because that one needs a referral or an existing
relationship.

On screen all three render as the same empty grid under the same polite sentence. In the
response they are usually distinct, which is the strongest practical argument for reading
the payload:

```python
def classify(day):
    if day.get("slots"):
        return "open"
    reason = day.get("reason")
    if reason in ("FULLY_BOOKED", "NO_CAPACITY"):
        return "taken"
    if reason in ("PHONE_ONLY", "REFERRAL_REQUIRED", "NOT_ONLINE"):
        return "not_bookable_online"
    return "unknown"   # keep it separate; do not fold it into "taken"
```

The distinction decides what the dataset can honestly claim. Fold phone-only providers
into the same bucket as booked-out ones and a wait-time figure reports scarcity where the
real finding is a booking channel. That error concentrates in exactly the providers a
question about access is usually about.

## Never click through to the booking step

This is the section that makes appointment scraping different from price scraping. In many
booking systems, selecting a slot reserves it immediately, before the visitor types a
single contact detail, and holds it for several minutes so nobody else can take it
mid-form.

So a scraper that clicks a slot to check whether it is genuinely open has just taken it
off the grid for everyone else. Do that across a few hundred slots and the scraper is no
longer measuring availability. It is causing the shortage it reports, and the next read
will faithfully record the damage as though a patient had booked.

Read the grid. Never click through to the booking step. Everything the click was going to
confirm is in the response already, including the slot identifier and the flag saying the
slot is bookable.

Discipline is a weak guard for this, because one stray selector during development is
enough. Make it structural instead: block the reservation endpoints at the network layer,
so a misfired click cannot reach them even when the code is wrong.

```python
from invisible_playwright import InvisiblePlaywright

HOLD_PATHS = ("/hold", "/reserve", "/lock", "/appointments/create", "/booking/confirm")

def read_only_guard(route, request):
    if any(p in request.url for p in HOLD_PATHS):
        route.abort()          # a hold request never leaves the browser
    else:
        route.continue_()

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.route("**/*", read_only_guard)
    page.goto("https://example.com/find-appointment")
```

The route runs before the request leaves the browser, so the guard holds whatever the
page's JavaScript decides to do. Build the path list from the calls the booking flow
actually fires, watched once by hand. A method check looks tempting, but plenty of
calendars fetch over POST, and blocking every non-GET would block the grid itself.

Keep the guard in the scraper permanently, not only while testing. It costs one function
and removes an entire class of harm from the run.

## Stamp the row in UTC when the response lands

Stamp the row when the response lands, not when it reaches disk. A price scraped ten
minutes late is still that price. A slot scraped ten minutes late may already belong to
somebody, which makes the timestamp part of the observation rather than housekeeping.

```python
import datetime as dt

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

def slot_key(row):
    # dedupe across overlapping month grids on identity, not grid position
    return (row["provider_id"], row["location_id"],
            row["service_type"], row["slot_start_local"])
```

Use UTC, always. Then keep the slot's printed start time exactly as the system wrote it,
and store the location's timezone next to it as a separate field.

Converting on the way in is the tempting shortcut and the expensive one. One provider's
sites can sit in different zones, the scraper's own machine is in a third, and a run that
crosses a daylight-saving change produces rows that look duplicated or missing. Store the
local time, the zone and the UTC read time, then convert later when the question is clear.

## Record where the bookable horizon ended

Every booking system stops the calendar somewhere. Some release two weeks at a time. Some
open the next month on the first of the month. The boundary belongs to them, and in this
domain it moves with the service type as well as the provider: a routine check-up can be
bookable six months out while an urgent visit shows three days.

So absence past the horizon is not absence of appointments. It is absence of information,
and writing the two the same way corrupts the series quietly.

Record `horizon_end` from the last period the server actually served, not from the range
the run intended to walk. Without it, a reading that stopped on a Tuesday and one that ran
to the following Sunday look like equal coverage, and the extra days read as appointments
that disappeared.

A long sweep across many providers gets interrupted eventually, and the checkpoint to
resume from is the provider and horizon pair rather than a row count;
[how to resume an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md)
has the shape of that.

## Pace it to the endpoint, not to the machine

Availability polling against a small set of providers is the sniping pattern, and booking
systems watch for it closely, because tools that grab scarce appointments the moment they
open behave exactly this way. The endpoint is expensive on their side too, since every
answer is a live capacity query rather than a cached page. Both facts point the same
direction.

One context, sequential requests, and an interval chosen by how fast the calendar actually
changes rather than by how fast the machine can ask. Parallel contexts fanned across
providers look like the thing the rate limit exists to stop. The reasoning behind picking
an interval is in
[how to rate limit your scraper](how-to-rate-limit-your-scraper-playwright.md), and what
to do once the endpoint answers with a status code instead of a grid is in
[how to handle 403 and 429 backoff mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## Conclusion

Appointment availability punishes a careless row shape harder than most fields, because a
slot is not a property of a page. It is the answer to a question with three inputs, and it
decays in minutes. Key every row to the provider, the service type and the location, and
stamp it in UTC when the response lands.

Page forward with a request and wait for it. Keep no availability and not bookable online
apart, since only the response can separate them. Write down where the horizon ended. And
stop at the grid, because in this domain the click is not a read: it takes an appointment
away from somebody who needed it.

## Short answers to the questions that lead here

**Why do two runs return different appointments for the same page?** Because the grid was
resolved against a provider, a service type and a location, and at least one of them
differed, or somebody booked in between. Record all three plus a UTC read time or the
difference cannot be explained later.

**Can I click a slot to confirm it is real?** No. Many systems place a hold the moment a
visitor selects a slot, which removes it from other visitors for minutes and changes the
next reading. The response already carries the bookable flag the click would have
confirmed.

**How does next week's grid arrive?** Click the forward control and wait for the response
it fires, with `expect_response` opening the wait before the click. The next period is not
sitting in the DOM waiting for a scroll.

**An empty calendar means fully booked, right?** Not necessarily. It can also mean the
provider takes appointments by phone only, or that this service type needs a referral, and
the rendered page shows all three identically.

**Does the service type matter if the provider is the same?** Yes. Duration quantizes the
diary, so a long appointment type and a short one return different times from the same
provider on the same day.

**Why store local time and UTC separately?** Because the slot's start time belongs to the
location's timezone and the read time belongs to the run. Converting on ingest with the
wrong zone shifts every row silently, and a daylight-saving boundary makes rows look
duplicated.

## Sources

- Playwright documentation, [Network](https://playwright.dev/python/docs/network), retrieved 2026-08-28
- Playwright documentation, [Events](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- Playwright documentation, [`page.route`](https://playwright.dev/python/docs/api/class-page#page-route), retrieved 2026-08-28
- Playwright documentation, [`page.expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response), retrieved 2026-08-28

**See also:** [scraping delivery slots](how-to-scrape-delivery-slots-playwright.md) for the
same row discipline against retail capacity, [scraping date-picker calendars](how-to-scrape-date-picker-calendar-playwright.md)
for the widget mechanics underneath, [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for telling near-identical calls apart, and [rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for pacing against an endpoint that watches.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Clicking a slot to verify it
was open turned out to place a hold on it, so the check was quietly removing the
appointments it claimed to be counting.*
