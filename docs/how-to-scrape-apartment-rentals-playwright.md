---
title: "How to scrape apartment rentals with Playwright"
description: "Scrape apartment rentals with Playwright: trigger the unit-availability XHR, read the per-floorplan price table, and key every row on its move-in date."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 34
---


# How to scrape apartment rentals with Playwright

To scrape apartment rentals with Playwright, launch a real browser, wait for the
unit-availability XHR the listing fires after it loads, and parse the per-floorplan price
table it returns, keying each row on its move-in date. A requests-only scraper never fires
that request, so it only ever sees the marketing shell.

An apartment building listing looks like one page with one price. It is not. Underneath
the marketing shell is a unit-level table: several floorplans, each with its own per-unit
prices, each unit carrying a specific availability date, and a price that shifts by
move-in date and lease term. That table almost never ships in the initial HTML. It arrives
by XHR after the page renders, and a "check availability" action pulls in more units still.

This is the gap that trips up rental scraping. A requests-only scraper fetches the HTML,
sees the hero image and a "starting at" number, and calls it done. It never fired the
request that returns the units, so it never had the data. The rest of this page is how to
fire that request with a real browser and turn what comes back into rows you can trust.

## Why requests-only scraping sees the wrong page

The "starting at $1,850" you see in the initial HTML is a marketing figure. It is the
cheapest unit the building has ever offered, not a unit you can rent. The rentable units,
with real prices and real dates, live behind a second request that the page issues from
JavaScript once it has loaded.

That request is the whole point of the page, and it is exactly what a plain HTTP fetch
misses:

- The initial document is a shell. It renders a price band, a gallery, and an empty table
  the browser is expected to fill.
- The units come back from an XHR keyed to the building ID, often as JSON, sometimes as an
  HTML fragment the page splices in.
- "Check availability", or picking a move-in month, issues further requests that reveal
  units the first call withheld.

You could try to reverse-engineer that endpoint and call it directly. Sometimes that
works for an afternoon. Then the response starts coming back short, or empty, or a
challenge, because the request arrived without a browser session behind it: no matching
TLS handshake, no prior page load, no consistent fingerprint. A real browser firing the
site's own request is the path that does not rot, and it is the reason this guide launches
one instead of hand-rolling the API call.

## Launching a browser that fires the site's own requests

Switching from stock Playwright is two lines, and every method below is ordinary
Playwright. The `browser` you get back is a real Playwright `Browser`.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/building/riverside-lofts")
    page.wait_for_load_state("networkidle")
    print(page.title())
```

The `seed=42` is doing more than making the run repeatable. It fixes the whole synthetic
machine behind the browser: GPU, canvas, audio, fonts, screen, roughly 400 fields, all
derived from that one number. The same seed gives the same device every run, which matters
later when you revisit the same building day after day to watch a unit. More on that below.

If you scrape through a proxy, pass it here and let the timezone follow the exit IP rather
than pinning it by hand. [Configuration](configuration.md) covers the proxy schemes and
why an explicit timezone is usually the wrong move.

## Trigger the unit-availability XHR

The building's units arrive in a background request. The reliable way to capture it is to
wait for the response whose URL matches the availability endpoint, rather than scraping the
DOM and hoping the table has filled in. Playwright's `expect_response` does exactly this:
it registers the wait, then you perform the action that triggers the call.

```python
import json
from invisible_playwright import InvisiblePlaywright

BUILDING_URL = "https://example.com/building/riverside-lofts"

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    # Register the wait BEFORE navigating, so the response cannot arrive first.
    with page.expect_response(lambda r: "availability" in r.url and r.ok) as resp_info:
        page.goto(BUILDING_URL)

    response = resp_info.value
    units = response.json()
    print("captured", len(units.get("floorplans", [])), "floorplans")
```

Some buildings do not load the units until you interact. If the table stays empty after
navigation, the units are behind a "check availability" button or a move-in date picker.
Wait on the response while you click:

```python
with page.expect_response(lambda r: "availability" in r.url and r.ok) as resp_info:
    page.click("button:has-text('Check availability')")

units = resp_info.value.json()
```

Matching on a URL substring is the fragile part of this. Open the network panel once by
hand and read the real endpoint, because "availability" is an example and yours will
differ. The broader pattern of catching a response by URL, reading its JSON, and keeping
the parse separate from the navigation is covered in
[capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md). If the
table fills in without a request you can catch, the units were server-rendered into the
document after a delay, and [waiting for the page to actually finish loading](how-to-wait-for-page-load-playwright.md)
is the tool for that case instead.

## Read the per-floorplan table, keyed on move-in date

Key every scraped row on the tuple (building, floorplan, unit, move-in date), and treat the
price as a value under that key, not a property of the listing. That is the honest
modelling. Do not flatten a building to one price, and do not flatten a floorplan to one
either: a floorplan holds units, and a unit's price only means something next to its
availability date and lease term.

Each row carries these fields:

| Field | What it holds |
|---|---|
| `building` | the building URL or ID the row belongs to |
| `floorplan` | the floorplan name, with its bed and bath count |
| `unit` | the specific unit number within the floorplan |
| `sqft` | the unit's square footage |
| `available_on` | the move-in date the price is keyed to |
| `lease_term_months` | the lease length the price applies to |
| `price` | the rent for that unit, date, and term |

```python
def rows_from_response(building_url, payload):
    rows = []
    for fp in payload.get("floorplans", []):
        for unit in fp.get("units", []):
            rows.append({
                "building": building_url,
                "floorplan": fp.get("name"),
                "beds": fp.get("beds"),
                "baths": fp.get("baths"),
                "unit": unit.get("unit_number"),
                "sqft": unit.get("sqft"),
                "available_on": unit.get("available_date"),   # the key that moves the price
                "lease_term_months": unit.get("lease_term"),
                "price": unit.get("price"),
            })
    return rows

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    with page.expect_response(lambda r: "availability" in r.url and r.ok) as resp_info:
        page.goto(BUILDING_URL)
    rows = rows_from_response(BUILDING_URL, resp_info.value.json())

for r in sorted(rows, key=lambda x: (x["price"] or 0)):
    print(r["floorplan"], r["unit"], r["available_on"], r["lease_term_months"], "->", r["price"])
```

Two things fall out of modelling it this way. First, the same unit can appear at several
prices, one per lease term, and collapsing them loses the number you probably care about.
Second, the cheapest row is often the one with the furthest-out move-in date, which is why
a "starting at" figure and a "can I move in this month" figure disagree. Keep the date on
every row and both questions stay answerable.

## Monitor availability over time with a stable identity

Rental data is only interesting over time. A single scrape tells you the building's units
this minute; the value is in watching a unit's price drift, or a floorplan sell out, across
days. That means revisiting the same building repeatedly, and repeated visits from the same
target are exactly the pattern a site watches for.

This is where the fixed seed earns its place. Pass the same seed on every poll and each
revisit presents the same device: the same GPU, the same fonts, the same canvas hash, the
same screen. Day over day, that reads like one returning shopper checking back on a
building, not a fresh anonymous device hitting the availability endpoint every morning. A
new random fingerprint per poll is the tell, not the disguise.

```python
import datetime, json, pathlib

SEED = 42                      # same device on every poll
BUILDING_URL = "https://example.com/building/riverside-lofts"
out = pathlib.Path("availability_history.jsonl")

def poll_once():
    with InvisiblePlaywright(seed=SEED) as browser:
        page = browser.new_page()
        with page.expect_response(lambda r: "availability" in r.url and r.ok) as resp_info:
            page.goto(BUILDING_URL)
        rows = rows_from_response(BUILDING_URL, resp_info.value.json())

    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with out.open("a", encoding="utf-8") as fh:
        for r in rows:
            r["scraped_at"] = stamp
            fh.write(json.dumps(r) + "\n")
    return len(rows)

print(poll_once(), "unit-rows appended")
```

Append, never overwrite. Each poll is a snapshot with a timestamp, and the history is the
sequence of snapshots; diffing yesterday's file against today's is what surfaces a price
drop or a unit that vanished. If you would rather the revisits also carry a browser profile
that persists cookies and local storage across runs, so the site sees a continuous session
rather than a clean browser each time, [persistent profiles](persistent-profiles.md)
combines with the fixed seed for that. And if you want the device stable but need to force
one specific field, a particular screen size for example, [pinning](pinning.md) forces that
field while leaving the rest seed-derived.

## The honest caveat: availability is a snapshot, not a promise

The reproducibility this product gives you covers the browser, not the leasing office. The
seed makes the same machine come back every run. It cannot make the same units come back,
because unit availability is real-time state on the building's side, and it flips without
warning.

Be precise about what a scraped "available" means. It means the unit was available at the
instant that XHR responded. Between two floorplans in the same crawl, someone can sign a
lease and the unit you read on page one is gone by the time you reach page three. A crawl is
not a transaction; there is no consistent snapshot across a multi-request scrape of a live
inventory. Treat every price and every availability date as observed-at-a-timestamp, which
is why the monitor above stamps each row, and never as a fact that will still hold when you
act on it. The browser is reproducible. The leasing state is not, and no scraper can make
it so.

## Conclusion

Rental scraping fails when you treat a building as a page with a price. It is a live,
unit-level table delivered by a request the page makes after it loads, priced by move-in
date and lease term. Fire that request with a real browser using `expect_response`, model
every row as (building, floorplan, unit, date) with the price underneath, and poll over
time from one stable seed so the revisits read as a returning shopper. Then remember the one
thing the code cannot promise you: availability is real-time, so a scraped "available" is a
snapshot with a timestamp, not a guarantee.

## Short answers to the questions that lead here

**Why does my scraper only get one price for a whole building?** Because you read the
initial HTML, which is a marketing shell with a "starting at" figure. The real per-unit
prices arrive in a later XHR that a plain HTTP fetch never triggers.

**Can I just call the availability API directly instead of running a browser?** Sometimes,
briefly. The endpoint expects a real browser session behind it, and direct calls tend to
start returning short or empty responses. Firing the site's own request from a real browser
is the path that keeps working.

**How do I capture the request that has the units?** Register `page.expect_response` on the
availability URL before you navigate or click, then read `.json()` off the captured
response rather than scraping the DOM.

**What should I use as the key for each row?** The tuple (building, floorplan, unit, move-in
date), with lease term. Price is a value under that key. The cheapest unit is often the one
with the furthest-out date, so a price without its date is ambiguous.

**How do I track a unit's price over days without looking like a new bot each time?** Poll
with the same seed every run. The identity stays fixed, so each revisit presents the same
device and reads as a returning shopper instead of a fresh anonymous fingerprint per poll.

**Is a scraped "available" reliable?** Only as of its timestamp. Availability is real-time
leasing state and can flip mid-crawl. The seed makes the browser reproducible; it cannot
make the inventory reproducible.

## Sources

- The real `invisible_playwright` API as documented in [Quickstart](quickstart.md) and
  [Configuration](configuration.md): the two-line launch, the seed, and the proxy handling
  used in every example above.
- Playwright's own [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`wait_for_load_state`](https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state)
  methods, and navigation methods generally, which the wrapper exposes unchanged because the
  returned object is a real Playwright `Browser`.
- This project's own testing notes on why a browser that fires a page's real requests
  outlasts a hand-rolled API call, and why a per-request fingerprint is a tell.

**See also:** [capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md)
for the general response-capture pattern, [waiting for the page to finish loading](how-to-wait-for-page-load-playwright.md)
for the case where units are rendered in rather than fetched, and
[persistent profiles](persistent-profiles.md) for keeping a session continuous across polls.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The unit-level table, the
move-in-date key, and the snapshot caveat are all things a real rental crawl teaches you the
hard way.*
