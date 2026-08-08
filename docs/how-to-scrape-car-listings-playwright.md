---
title: "How to scrape car listings with Playwright"
description: "Scrape car listings with Playwright: drive the faceted filter sidebar, wait on the results XHR, pull VIN and specs, then dedupe overlapping permutations."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 33
---


# How to scrape car listings with Playwright

To scrape car listings with Playwright, drive the faceted filter sidebar with real inputs,
wait on the results XHR keyed to each filter state and read the typed JSON it returns,
follow the listing ids to each detail page for the VIN and specs, and dedupe on the VIN
because overlapping filters return the same car many times. The rest of this page is how
each of those steps works.

A car marketplace does not look like a list of pages you can walk. It looks like a
faceted filter sidebar: make, model and year checkboxes, plus mileage and price as range
sliders. Change any of them and the results grid updates in place, over an XHR, with no
navigation and no new URL to bookmark. The listings themselves mix dealer inventory and
private sellers, and the parts you actually want, the VIN, the full specs table and
sometimes a price history, live one click away on each detail page.

That shape decides the whole approach. You are not crawling numbered pages, you are
operating a control panel and reading what the server sends back each time the filter
state changes. This page shows how to drive the sidebar with real inputs, wait on the
results XHR keyed to that filter state, pull the VIN and specs from the detail page, and
deal with the one thing a real browser does not solve for you: overlapping filters return
the same car more than once.

## Why a car marketplace is a filter problem, not a page problem

The single fact that changes everything is that the grid updates without a navigation.
When you tick "make = X" the sidebar fires an XHR, the server returns a fresh result set
keyed to the current combination of every active facet, and the framework repaints the
grid. The address bar does not move. There is no page two to request, there is a filter
state to set and a response to catch.

This has two consequences for scraping. First, `page.goto` gets you exactly one thing:
the unfiltered landing grid. Everything after that is interaction, so the tool has to be
able to move a slider and tick a box and have the site believe a person did it. Second,
the data you want per query is in the XHR response, not only in the repainted HTML, which
is the same argument as [reading the response instead of the rendered
HTML](how-to-capture-xhr-api-responses-playwright.md): the JSON the sidebar fetches is a
typed, stable contract, and the grid is just that JSON painted into cards.

A range slider in particular is why a plain HTTP client struggles here. It is a real DOM
control that emits `input` and `change` events, and the sidebar usually debounces those
before it fires the XHR. Forging the final query string by hand works until the site adds
a signed filter token or a cursor the slider computes client-side, and then you are
reverse-engineering the sidebar instead of using it. Driving the actual control sidesteps
all of that.

## Launch a real browser and open the filtered grid

The switch from stock Playwright is the usual two lines. `InvisiblePlaywright` hands you a
real Playwright `Browser`, so every method below is the stock API working exactly as
documented upstream.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://cars.example.com/search", wait_until="domcontentloaded")

    # the sidebar and the first grid are painted by an XHR after load,
    # so wait for a real card to exist rather than for the network to idle
    page.wait_for_selector("[data-testid='listing-card']")
```

Waiting for a concrete element rather than a load event matters more here than on a static
page, because the grid arrives after the document does. The difference between the load
events and why `networkidle` misleads on a feed like this is covered in [how to wait for a
page to load](how-to-wait-for-page-load-playwright.md); on a faceted search the honest
signal is "the first card exists", not "the document parsed".

## Operate the sliders and wait on the results XHR

The core loop is: change one facet, wait on the XHR that the change triggers, read the
response. Playwright's
[`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
is built for exactly this. It arms a wait, then you perform the action inside the block,
and it returns the matching response once it arrives, so there is no sleep and no race.

Checkboxes are the easy half:

```python
# the URL fragment your results endpoint actually uses; read it once
# from the network panel and pin it here
RESULTS_XHR = "**/api/search/results*"

with page.expect_response(RESULTS_XHR) as resp_info:
    page.get_by_role("checkbox", name="Sedan").check()
results = resp_info.value.json()
print(results["total"], "listings match")
```

A range slider has no `.check()`. It is an element you move, and moving it with the mouse
is what makes the sidebar treat it as a real interaction and fire its debounced query. Grab
the handle, read its box, and drag it along the track:

```python
handle = page.get_by_role("slider", name="Max price")
box = handle.bounding_box()

with page.expect_response(RESULTS_XHR) as resp_info:
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] - 120, box["y"] + box["height"] / 2, steps=12)
    page.mouse.up()
filtered = resp_info.value.json()
```

The `steps=12` matters: a slider handle that teleports from one coordinate to the next,
with nothing in between, is one of the behavioural tells covered in [the checklist for when
Playwright is treated as a bot](playwright-detected-as-bot.md). Real drags pass through the
space between the start and the end. For a slider that snaps to discrete stops, focusing the
handle and pressing `ArrowRight` or `ArrowLeft` a known number of times is often more
reliable than pixel math, and it emits the same `input` events:

```python
handle.focus()
with page.expect_response(RESULTS_XHR) as resp_info:
    for _ in range(8):
        page.keyboard.press("ArrowLeft")
filtered = resp_info.value.json()
```

Either way, the rule from [capturing XHR responses](how-to-capture-xhr-api-responses-playwright.md)
applies: assert that a response arrived and that it carries listings. A grid that came back
empty because your selector missed the handle looks identical to a grid that came back empty
because nothing matched, and only one of those is a real result.

## Sweep filter permutations without changing machines

A useful crawl of a marketplace is a permutation sweep: every make against a handful of price
bands against a couple of mileage bands, dozens or hundreds of combinations, each one a
slider move and a checkbox tick and an XHR read. That is a lot of rapid, identical-looking
requests to one results endpoint from one session, which is precisely the pattern a velocity
check is built to notice.

This is where a seed-stable fingerprint earns its place. Passing `seed=` fixes every field
the identity implies, so the session that fires permutation one and the session that fires
permutation two hundred are, to the results endpoint, the same machine. The GPU, the fonts,
the screen, the audio context and the roughly four hundred other fields do not drift between
requests, so an hour-long sweep reads as one visitor working through a search rather than a
rotating cast that happens to share an IP.

```python
MAKES = ["Make A", "Make B", "Make C"]
PRICE_STOPS = [4, 8, 12]   # ArrowLeft presses from the max

rows = []
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://cars.example.com/search", wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid='listing-card']")

    for make in MAKES:
        make_box = page.get_by_role("checkbox", name=make)
        with page.expect_response(RESULTS_XHR):
            make_box.check()

        for presses in PRICE_STOPS:
            handle = page.get_by_role("slider", name="Max price")
            handle.focus()
            with page.expect_response(RESULTS_XHR) as resp_info:
                for _ in range(presses):
                    page.keyboard.press("ArrowLeft")
            for item in resp_info.value.json()["listings"]:
                rows.append({
                    "listing_id": item["id"],
                    "vin": item.get("vin"),
                    "seller": item["sellerType"],   # dealer or private
                    "url": item["detailUrl"],
                })

        with page.expect_response(RESULTS_XHR):
            make_box.uncheck()
```

Note the `sellerType` field: the grid mixes dealer inventory and private listings, and the
XHR usually labels which is which even when the card styling does not, so you can partition
them without a heuristic. If a permutation sweep needs to spread its exit addresses as well
as hold one identity, [rotating proxies](how-to-rotate-proxies-playwright.md) is a separate
control from the fingerprint and the two are set independently.

## Pull VIN and specs from the detail page

The results XHR gives you enough to enumerate and to partition, but the VIN, the full specs
table and any price history usually live only on the detail page. That is a per-listing
navigation, so it belongs after the sweep, driven from the ids you collected, not
interleaved with the slider work.

```python
def scrape_detail(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid='specs-table']")

    specs = {}
    for row in page.get_by_test_id("specs-table").get_by_role("row").all():
        cells = row.get_by_role("cell").all_inner_texts()
        if len(cells) == 2:
            specs[cells[0].strip()] = cells[1].strip()

    vin = page.get_by_test_id("vin").inner_text().strip()
    return {"vin": vin, "specs": specs}
```

Many detail pages load the price history through its own XHR when you open a tab or scroll to
it, in which case the same `expect_response` pattern from above captures it directly, and you
read the series from JSON instead of scraping a chart.

## Dedupe on VIN or listing id, because the browser does not

Here is the honest caveat, and it is a data problem rather than a stealth one. Overlapping
filter permutations return the same car repeatedly. A given listing satisfies "Make A" and it
satisfies "price under X" and it satisfies "mileage under Y", so it comes back in every sweep
whose bands include it. A three-facet sweep will hand you the popular listings many times
over.

The browser being real does nothing about this. A seed-stable fingerprint keeps you one
machine to the server; it does not keep one car from appearing in ten result sets. Dedupe is
yours to do, and the key is the VIN when the XHR exposes it, or the listing id when it does
not:

```python
seen = {}
for r in rows:
    key = r["vin"] or r["listing_id"]
    seen.setdefault(key, r)   # first occurrence wins

unique = list(seen.values())
print(len(rows), "raw rows,", len(unique), "unique vehicles")
```

Prefer the VIN as the key when you have it, because the same physical car can be relisted
under different listing ids by different sellers, and only the VIN collapses those. Fall back
to the listing id when the VIN is absent, which is common on private listings that omit it.
On a real sweep the gap between the two counts is large: it is normal for a permutation crawl
to pull two or three raw rows for every distinct vehicle, and a script that skips this step
reports inventory numbers that are simply wrong.

## Conclusion

A car marketplace rewards treating it as what it is: a filter panel that answers over XHR,
not a stack of pages. Drive the sliders and checkboxes with real inputs so the sidebar fires
its query, wait on the response keyed to the filter state instead of guessing at a URL, and
read the typed JSON rather than the repainted cards. Hold one identity across the whole sweep
with a seed so an hour of permutations stays one visitor, follow the ids to the detail page
for the VIN and specs, and dedupe on the VIN because overlapping filters guarantee repeats
that no amount of realness removes.

## Short answers to the questions that lead here

**How do I set a price or mileage range slider in Playwright?** Drag the handle with
`page.mouse` using several `steps` so it moves continuously, or focus it and press `ArrowLeft`
/ `ArrowRight` for sliders that snap to stops. Both emit the `input` events the sidebar
debounces before it fires its XHR.

**The results grid updates without changing the URL. How do I read it?** Wrap the facet change
in `page.expect_response(...)` matching the results endpoint, then read `.value.json()`. The
filtered data is in that XHR, not in a new page.

**Why am I getting the same car many times?** Overlapping filter permutations each return any
listing they match, so popular cars appear in many result sets. Dedupe on the VIN, or the
listing id when the VIN is absent.

**Do I need a full browser, or can I hit the search API directly?** You can once you know the
exact query the sidebar builds, but sliders often compute signed tokens or cursors
client-side, so driving the real control is more robust than forging the request.

**Will one fingerprint survive a long permutation crawl?** That is what the seed is for. A
fixed seed holds the GPU, fonts, screen and the rest steady, so a sweep of hundreds of queries
reads as one machine to the results endpoint rather than a rotating set.

**How do I get the VIN and full specs?** They usually live only on the detail page. Collect
listing ids from the sweep, then visit each detail URL and read the specs table and VIN, plus
any price-history XHR that loads on demand.

## Sources

- This project's own API and defaults, from the [Quickstart](quickstart.md) and
  [Configuration](configuration.md) pages: the two-line launch, the real Playwright `Browser`
  it returns, and the seed that fixes the fingerprint across a run.
- Playwright's documented interaction and network APIs used above:
  [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response),
  [`page.mouse`](https://playwright.dev/python/docs/api/class-mouse) for the slider drag,
  [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role) for the
  checkbox and slider locators, and
  [`get_by_test_id`](https://playwright.dev/python/docs/api/class-page#page-get-by-test-id) for
  the specs table - read from the upstream docs rather than guessed.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the filter response directly, [waiting for a page to load](how-to-wait-for-page-load-playwright.md)
for why a card selector beats a load event on a feed, and [the bot-detection checklist](playwright-detected-as-bot.md)
for the behavioural tells a slider drag can create.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The dedupe count in the last
section is from real sweeps: overlapping facets routinely produce two to three raw rows per
distinct vehicle, and being a real browser does not change that.*
