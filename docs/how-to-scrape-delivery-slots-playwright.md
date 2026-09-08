---
title: "How to scrape delivery slots with Playwright"
description: "Scrape delivery slots with Playwright: a slot row is keyed to a postcode, a basket and a UTC moment, the grid arrives one week per request, and full is not the same as not offered."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 99
---


# How to scrape delivery slots with Playwright

To scrape delivery slots with Playwright, set the postcode and the basket the reading
belongs to, capture the grid response the page fetches per week instead of reading the
rendered cells, and write every row with a UTC timestamp, the postcode, the basket state
and the last date the retailer was willing to show, because a slot exists only for one
visitor at one moment and can stop existing minutes later.

Slots look like a calendar and behave like inventory. The grid on screen is not a
schedule the retailer publishes. It is the answer to a question that already included a
delivery address, a trolley and the clock. Two visitors loading the same page in the same
second can see different rows, and neither of them is wrong.

## A slot is scoped to a postcode, a basket and a moment


The same grid shape with different inputs, a provider and a service type instead of
a postcode and a basket, is in
[scraping appointment availability](how-to-scrape-appointment-availability-playwright.md).

Three inputs decide which rows come back. A slot row that records none of them is not
data, because nobody can say what question it answered.

The same URL, loaded twice from two machines, returns two different grids. Drop any one
of the three inputs and the row you saved cannot be reproduced, cannot be compared to
tomorrow's row, and cannot be defended when someone asks where the number came from.

Treat the triple as the key, not as metadata bolted on afterwards:

```python
row = {
    "postcode": "",        # the delivery address the grid was resolved for
    "basket_state": "",    # "empty", or a hash of the basket contents
    "read_at": "",         # UTC, stamped when the response landed
    "slot_date": "",       # the day the slot covers
    "slot_start": "",      # start time as the retailer prints it, local
    "slot_end": "",
    "status": "",          # "available", "full", or "not_offered"
    "price": None,         # delivery charge, which often varies by slot
    "horizon_end": "",     # last date this reading was allowed to see
}
```

Those first three fields are part of what the row asserts. Without them, two readings
taken an hour apart describe two different questions instead of a change in
availability, and the series you build on top of them measures your own inputs.

## Read the grid response, not the rendered cells

The cell is a rendering, and it has already thrown away the reason it is unclickable. A
class name and a tooltip survive. The slot identifier, the delivery charge, the cutoff
time and the reason code usually do not, because the page had no need to print them.

The payload behind the grid keeps all of it. Subscribe to responses and hold the parsed
body, then read your fields from there:

```python
grids = []

def on_response(resp):
    if "/slots" in resp.url and resp.request.resource_type in ("xhr", "fetch"):
        grids.append(resp.json())

page.on("response", on_response)
page.goto("https://example.com/book-delivery", wait_until="domcontentloaded")
```

One page load often fires several calls that look alike: one for the week grid, one for
the basket summary, one for a banner. Telling them apart by URL alone gets fragile fast,
and
[how to capture XHR API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md)
covers matching on the response body when the paths collide.

## Paging forward is a request, not a scroll

The grid is fetched per week, or per day on sites with tighter capacity. Clicking forward
does not reveal rows that were already in the DOM. It asks the server a new question, and
the answer arrives as a new response you have to wait for.

Scrolling gets you nothing here. There is nothing below the fold to reach.

```python
def read_next_week(page):
    with page.expect_response(
        lambda r: "/slots" in r.url and r.status == 200
    ) as info:
        page.click("[data-testid='next-week']")
    return info.value.json()
```

`expect_response` opens the wait before the click, so the request cannot land in the gap
between the two statements. A loop that clicks and then reads the DOM after a fixed sleep
records the previous week twice, under two different dates, and nothing in the output
looks wrong. The forward-and-back mechanics of the control itself are the same ones in
[how to scrape a date picker or calendar with Playwright](how-to-scrape-date-picker-calendar-playwright.md).

## Full and not offered are different observations

A greyed cell has two causes, and they mean close to opposite things. A slot that sold
out is evidence of demand at an address the retailer serves. A slot that was never
offered for that postcode is evidence about the service area, and nothing at all about
demand.

In the DOM they are frequently identical. In the response they are usually distinct,
which is the strongest practical argument for reading the payload:

```python
def classify(cell):
    if cell.get("available"):
        return "available"
    if cell.get("reason") in ("SOLD_OUT", "CAPACITY_REACHED"):
        return "full"
    return "not_offered"
```

Collapse the two into one "unavailable" bucket and every demand figure you derive later
is inflated by the slots that never existed. The error compounds at the edge of the
service area, which is exactly where the question is usually interesting.

## A reading without a UTC timestamp cannot be compared

Stamp the row when the response lands, not when it reaches disk. A price scraped ten
minutes late is still that price. A slot scraped ten minutes late may have been booked
twice over since, which makes the timestamp part of the observation rather than
housekeeping.

```python
import datetime as dt

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
```

Use UTC, always. The slot's own printed start time is local to the delivery address and
belongs in `slot_start` as the retailer wrote it, untouched. Keep `read_at` separate and
absolute. A run that spans midnight while mixing local times produces slots that appear
to be released and withdrawn on a schedule that is purely a timezone artifact.

## The basket changes which slots you are eligible to see

Chilled goods can restrict a delivery to a narrower set of vans. Bulky items can push a
basket into a different fulfilment route with its own capacity. The result is that the
grid returned for an empty basket and the grid returned for a real one are not the same
measurement, even at the same postcode in the same second.

An empty basket is a legitimate thing to record. It is a clean, comparable baseline, and
it is cheap. It is just not a customer's view, so the row has to say which one it holds.
That is what `basket_state` is for, and a hash of the contents is enough to make two
readings comparable without storing the trolley.

If you want both readings, run them as two sessions rather than emptying and refilling
one basket between passes. Basket state lives in cookies and storage, so a fresh context
is the cleanest way to guarantee the second reading is not contaminated by the first;
[isolating identities with a browser context per session](isolate-identities-browser-context-per-session.md)
covers that separation.

## Record where the horizon ended, not just what you saw

Every retailer stops the grid somewhere. Four days out, three weeks out, a fixed number
of pages forward. That boundary is theirs, it moves between postcodes, and it moves
between days for the same postcode.

So absence past the horizon is not absence of slots. It is absence of information, and
the two must not be written the same way.

Record `horizon_end` as an observation in its own right, taken from the last page you
were actually served rather than from the range you intended to walk. Without it, a
reading that stopped on a Tuesday and one that ran to the following Sunday look like the
same coverage, and those extra days read as slots that disappeared. A long walk across
many postcodes will also be interrupted at some point, and the checkpoint you resume from
is the postcode and horizon pair, not a row count;
[how to resume an interrupted scrape with Playwright](how-to-resume-an-interrupted-scrape-playwright.md)
has the shape of that.

## Pace it, because slot polling is the reseller pattern

Repeated slot checking against a small set of postcodes is the signature retailers watch
for most closely, because it is what resellers and slot-sniping tools do. The endpoint is
also expensive on their side, since every answer is a live capacity query rather than a
cached page. Both facts point the same way.

One context, sequential requests, and an interval set by how fast capacity actually
changes rather than by how fast the machine can ask:

```python
import time

from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=7) as browser:
    page = browser.new_page()
    for postcode in POSTCODES:
        set_postcode(page, postcode)
        rows.extend(read_all_weeks(page))
        time.sleep(45)   # pace to the endpoint, not to the hardware
```

Parallel contexts across postcodes look like exactly the thing the limit exists to stop.
The reasoning behind choosing an interval is in
[how to rate limit your scraper with Playwright](how-to-rate-limit-your-scraper-playwright.md),
and what to do once the endpoint starts answering with a status code instead of a grid is
in
[how to handle 403 and 429 backoff mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## Conclusion

Delivery slots punish a careless row shape harder than most fields, because a slot is not
a property of the page. It is the answer to a question with three inputs, and it decays
in minutes. Key every row on the postcode, the basket state and a UTC timestamp taken
when the response landed. Page forward with a request and wait for it. Keep full and not
offered apart, since only the response can tell them apart. Write down where the horizon
ended. Then slow the whole thing down, because this endpoint is watched more closely than
any product page you have scraped.

## Short answers to the questions that lead here

**Why do two runs return different slots for the same page?** Because the grid was
resolved against a postcode, a basket and a moment, and at least one of those differed.
Record all three on every row or the difference is unexplainable after the fact.

**Can I read the slots straight from the DOM?** You can, and you will lose the reason a
cell is greyed out. The response usually carries a reason code, a slot id and the
delivery charge that the rendered cell never shows.

**How do I get next week's slots?** Click the forward control and wait for the response
it fires, using `expect_response` so the wait opens before the click. The next week is not
in the DOM waiting to be scrolled to.

**Is a greyed slot sold out?** Not necessarily. It is either full or never offered for
that postcode, the DOM often renders both identically, and merging them inflates every
demand number you derive.

**Does an empty basket give me the real grid?** It gives a consistent baseline, not a
customer's view. Chilled and bulky items can change eligibility, so the row has to state
which basket the reading came from.

**How often can I poll?** Slower than instinct suggests. Repeated slot checks on a few
postcodes are the classic reseller pattern, so use one context, keep requests sequential,
and set the interval by how fast capacity really moves.

## Sources

- Playwright documentation, [Events and response handling](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- Playwright documentation, [Network](https://playwright.dev/python/docs/network), retrieved 2026-08-28
- Playwright documentation, [Browser contexts](https://playwright.dev/python/docs/browser-contexts), retrieved 2026-08-28
- Playwright documentation, [Auto-waiting](https://playwright.dev/python/docs/actionability), retrieved 2026-08-28

**See also:** [scraping date picker calendars](how-to-scrape-date-picker-calendar-playwright.md)
for the grid widget underneath, [rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for pacing against an endpoint that watches, and [isolating identities per session](isolate-identities-browser-context-per-session.md)
for keeping one basket coherent across a run.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-basket reading was taken for weeks before anyone checked that chilled
goods change which slots the page will even offer.*
