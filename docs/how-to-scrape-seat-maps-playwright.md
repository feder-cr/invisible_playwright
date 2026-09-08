---
title: "How to scrape flight seat maps with Playwright"
description: "Scrape flight seat maps with Playwright by reading each seat's own data attribute instead of its position, joining status and pricing from two separate responses, and recording which carrier actually operates a codeshare's map."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 133
---


# How to scrape flight seat maps with Playwright

To scrape flight seat maps with Playwright, capture the seat map response scoped to the
exact flight number, date and cabin you selected, not to the route you searched,
read each seat's row, column and id from its own data attribute instead of its position
in the rendered grid, keep a blocked seat as a status distinct from an occupied one, and
join seat fees onto the map afterward from a separate pricing response, matched by seat
id.

A seat map looks like one fixed grid sitting under a fare, and it is really tied to a
single departure. The same origin, destination and date can carry two or three different
maps if the route flies on more than one aircraft type, or if the flight you searched is
a codeshare and the map that comes back belongs to the airline actually operating the
leg. Scrape the map at the wrong scope and every field in it, row, status, price,
describes a flight that is not quite the one your record names.

## The map belongs to a flight, a date and a cabin, never a route

A route is a convenient key for a scraper and the wrong key for a seat map. The server
does not hand back "the seat map for this route": it hands back the map for one specific
flight number, one specific departure date, and often one specific fare class or cabin,
because first class and economy on the same aircraft are laid out differently and priced
differently. Ask for the map with anything less specific than that triple and you either
get an error, a default map that belongs to a different departure, or the map for
whichever cabin the site assumes you meant.

The practical fix is to read the map response's own identifiers back and assert them
against the selection that triggered the request, rather than trusting that the click you
fired produced the map you expected.

```python
from invisible_playwright import InvisiblePlaywright

FLIGHT = {"number": "AB1234", "date": "2026-09-14", "cabin": "economy"}

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/booking/select-flight")

    with page.expect_response(
        lambda r: "seatmap" in r.url and r.request.method == "POST"
    ) as caught:
        page.get_by_role("button", name="Select").click()

    seatmap = caught.value.json()
    # the response names its own flight; a click does not guarantee
    # the map that comes back matches what you think you selected
    assert seatmap["flightNumber"] == FLIGHT["number"]
    assert seatmap["departureDate"] == FLIGHT["date"]
    assert seatmap["cabin"] == FLIGHT["cabin"]
```

If the same route flies on two aircraft that day, wide-body one way and narrow-body the
other, storing only origin, destination and date will silently merge two different
layouts into one row set. Store the flight number and the equipment type alongside every
seat you extract, not just once at the top of the run.

## The seat's row and column live on the node, not on its place in the list

The map itself is drawn as an SVG or a canvas grid, one visual element per seat, and a
reader assumes the seat labelled "14C" is simply the third element in the fourteenth row
of the markup. It usually is not. The label a passenger sees is derived from a data
attribute the server writes onto each seat node, and position in the DOM is an
implementation detail of how the renderer laid the grid out, not a promise about which
row or letter a node represents.

The gap between the two shows up first on an irregular row. An exit row on many
wide-body aircraft skips a letter, going from `G` straight to `J` because the row over
the wing carries fewer seats than the rows around it. A scraper that counts positions
instead of reading the label assigns every seat after that row one letter off from where
it actually sits, and the error does not throw, so a naive parser can run clean and still
ship a seat map that is wrong for one whole side of the aircraft.

```python
seat_nodes = page.query_selector_all("[data-seat-id]")

seats = []
for node in seat_nodes:
    seats.append({
        "seat_id": node.get_attribute("data-seat-id"),   # e.g. "14C", already correct
        "row": node.get_attribute("data-row"),
        "column": node.get_attribute("data-column"),
    })
```

If the grid is painted to a `<canvas>` rather than an SVG, there is no per-seat node to
query at all, and the fix is the same one that applies to any canvas-drawn chart: read
the data the chart was built from, either the network response or the client library's
in-memory state, covered in full in [extracting data from canvas
charts](how-to-extract-data-from-canvas-charts-playwright.md).

## Status is four values, and collapsing it to two loses the interesting one

A seat's status usually lives as a class name or a data attribute on the same node that
carries its id, and it is worth treating as a small enum rather than a boolean.
"Available" and "occupied" are the two everyone expects, and they are not the whole set.
"Blocked" is a third status a map assigns for a seat a passenger cannot select for a
reason that has nothing to do with whether it is sold: crew rest, a seat taken out of
service, or an exit-row restriction tied to age or mobility. "Selected" marks the seat
the current session has already chosen.

A binary read, treating anything that is not visibly "available" as "occupied," folds
blocked seats into sold ones and reports an aircraft as fuller than it is. Keep the four
values distinct and let downstream code decide what to do with each.

```python
STATUS_CLASSES = {
    "seat-available": "available",
    "seat-occupied": "occupied",
    "seat-blocked": "blocked",     # crew rest, out of service, exit-row restriction
    "seat-selected": "selected",
}

def read_status(node):
    classes = (node.get_attribute("class") or "").split()
    for cls in classes:
        if cls in STATUS_CLASSES:
            return STATUS_CLASSES[cls]
    return node.get_attribute("data-status") or "unknown"
```

## The price is a second call, joined by seat id

Nothing about a seat's fee is embedded in the map markup itself. Extra legroom and
preferred-seat surcharges are fetched from a pricing endpoint that is keyed to seat id,
fired once the map has finished rendering, and the two responses have to be joined in
your own code because the site joins them only on screen, not in either payload.

```python
with page.expect_response(lambda r: "seat-pricing" in r.url) as priced:
    page.wait_for_timeout(300)   # the pricing call follows the map, not the click

pricing_by_seat = {
    row["seatId"]: row for row in priced.value.json()["seats"]
}

for seat in seats:
    fee = pricing_by_seat.get(seat["seat_id"], {})
    seat["fee_amount"] = fee.get("amount")
    seat["fee_currency"] = fee.get("currency")
```

Waiting on a fixed timeout to let the pricing call fire is the fragile part of this
block, and if the site exposes any trigger for it, an idle event, a rendered marker,
prefer that over a sleep. The mechanics of catching a response you did not click for
directly are the same ones covered in [waiting for a specific API
response](wait-for-specific-api-response-playwright.md).

## One POST gets you one leg, and a return flight is a second request

The seat map is not part of the page that lists flights. It arrives after a POST that
submits one specific flight selection, and that request has to happen before the map
exists to be read. On a round trip, the return leg is not a continuation of the outbound
map: it is a second, independent selection, fired by its own POST, even though both
happen inside the same booking session and the same browser tab.

```python
def capture_seatmap(page, trigger):
    with page.expect_response(
        lambda r: "seatmap" in r.url and r.request.method == "POST"
    ) as caught:
        trigger()
    return caught.value.json()

outbound = capture_seatmap(
    page, lambda: page.get_by_role("button", name="Select outbound").click()
)
inbound = capture_seatmap(
    page, lambda: page.get_by_role("button", name="Select return").click()
)
```

Treat each leg as its own scrape, tagged with its own flight number and date, and never
assume the return map reuses anything from the outbound one beyond the session cookies
that got you there. The general pattern of driving a search and reading what it submits,
rather than the URL it lands on, is the same one in [scraping search results by driving a
form](how-to-scrape-search-results-form-playwright.md).

## A codeshare map belongs to whoever actually flies the plane

A flight sold under one marketing carrier's number is often operated by a different
airline entirely, and the seat map that comes back is the operating carrier's map, laid
out for their aircraft, not a generic map for the number you searched. The response
usually carries both identifiers if you look for them: the marketing flight number the
passenger booked under, and the operating flight number and carrier code for the airline
that actually flies the route.

Recording only the flight number you started the crawl from throws that distinction
away. A row that says "AB1234, seat 14C, available" is missing the fact that AB1234 is a
codeshare and the seats, the layout and the aircraft all belong to a different carrier's
own flight number. The fix costs two extra fields: add `operating_flight`, read from
`seatmap.get("operatingFlightNumber", FLIGHT["number"])`, and `operating_carrier`, read
from `seatmap.get("operatingCarrierCode")`, to the row you already build for every seat,
alongside the marketing number you searched with.

## When the map never loads, that is an answer too

Some fares gate seat selection entirely, behind a paid upsell step, behind an account
login, or behind a fare class that simply does not offer advance seat choice. None of
that is a scraping problem in the sense the rest of this page addresses, and no amount of
waiting or retrying opens a map that the fare rules have closed off. Treating a missing
map the same way you would treat an empty one is the mistake to avoid: an empty map
usually means a parsing bug, while a map that never arrives because the flow requires
payment or login first means the seat data for that flight is legitimately unavailable
to a script running this flow.

```python
from playwright.sync_api import TimeoutError as PlaywrightTimeout

def scrape_seatmap(page, trigger, timeout_ms=8000):
    try:
        with page.expect_response(
            lambda r: "seatmap" in r.url and r.request.method == "POST",
            timeout=timeout_ms,
        ) as caught:
            trigger()
        return {"status": "ok", "raw": caught.value.json()}
    except PlaywrightTimeout:
        # the request never fired: gated behind payment, login, or a fare
        # that does not sell advance seat selection. record it, do not retry
        return {"status": "unavailable", "raw": None}
```

Recording the "unavailable" row is worth as much as recording a full map. A downstream
consumer that expects a row per flight and gets silence instead of a status has no way
to tell "closed" from "broken."

## Conclusion

A seat map is a join of four things that never arrive in the same shape: the flight it
actually belongs to, the row and column a data attribute names rather than a position
implies, a status with four values instead of two, and a price fetched from its own
endpoint after the fact. Get the scope wrong and every field describes the wrong
departure. Get the attribute wrong and an exit row shifts every seat behind it by one
letter. Fold blocked into occupied and the aircraft looks fuller than it is. Skip the
operating carrier on a codeshare and the map's own aircraft goes unrecorded. None of
that is a stealth problem; it is a data-modeling problem that happens to sit behind a
browser, and the fix in every case is to read what the response actually names instead
of what the request seemed to ask for.

## Short answers to the questions that lead here

**Is a seat map tied to a route or to a specific flight?** A specific flight, date and
usually cabin. The same route on the same day can have several different seat maps if
it flies on different aircraft or as a codeshare, so store the flight number and
equipment with every seat, not just the route.

**Why are my seat letters wrong after a certain row?** You are almost certainly reading
position in the grid instead of each seat's own data attribute. An exit row on many
aircraft skips a letter, and counting positions shifts every seat after it by one.

**Is a blocked seat the same as an occupied one?** No. Occupied means sold; blocked
covers crew rest, a seat pulled from service, or an exit-row restriction, and folding the
two together overstates how full the aircraft is.

**Where do seat fees come from if they are not in the map markup?** A separate pricing
endpoint keyed to seat id, fetched after the map renders. Join the two responses in your
own code by seat id.

**Does the return flight share a seat map with the outbound leg?** No. Each leg is its
own POST and its own response, even inside one booking session, so scrape and store them
separately.

**What should I record when a fare does not offer seat selection?** An explicit
"unavailable" status, not an empty seat list. A map that never loads because the fare
gates selection behind payment or login is a different condition from a parsing bug that
returns nothing.

## Sources

- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  used here exactly as documented upstream to catch the seat map and pricing calls a
  click triggers.
- Playwright's [`query_selector_all`](https://playwright.dev/python/docs/api/class-page#page-query-selector-all)
  and [`get_attribute`](https://playwright.dev/python/docs/api/class-elementhandle#element-handle-get-attribute),
  read from the upstream API for pulling a seat's own row, column and status off its
  node.
- Retrieved 2026-08-28.

**See also:** [waiting for a specific API response](wait-for-specific-api-response-playwright.md)
for the mechanics behind catching the pricing call, [extracting data from canvas
charts](how-to-extract-data-from-canvas-charts-playwright.md) for maps painted to a
`<canvas>` instead of an SVG, [scraping search results by driving a
form](how-to-scrape-search-results-form-playwright.md) for the selection flow that
triggers the map's POST, and [scraping flight prices](how-to-scrape-flight-prices-playwright.md)
for the fare search this map sits downstream of.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this
scraper read seat letters from column position instead of the data attribute, and every
seat past the first skipped-letter exit row came out one letter short of the real one.*
