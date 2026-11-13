---
title: "How to scrape store locator pages with Playwright"
description: "Scrape store locator pages with Playwright: seed the search with a point grid instead of postcodes, read the radius endpoint rather than the cards, and dedupe on the store id because overlapping radii return the same branch."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 95
---


# How to scrape store locator pages with Playwright

To scrape a store locator with Playwright, treat it as a search over geography rather
than a list to paginate: seed the search with a grid of points instead of a postcode
list, capture the radius endpoint the page calls instead of parsing the rendered cards,
dedupe on the store identifier because overlapping radii return the same branch more than
once, and stop when a run of new points stops producing new identifiers.

A locator shows a list, so it invites a list-shaped scraper. Fill in a postcode, read the
cards, move on. That works for one query and quietly fails for a country, because what
the page actually exposes is a radius search: every result set is whatever sits within a
distance of one point, and the points are yours to choose. That is what a store
locator is: a radius endpoint with the query left to the caller.

## A store locator is a radius search, not a page to paginate

There is no page 2 of a country. There is a query for "near this coordinate, within this
distance", repeated until the area is covered.

That reframing decides everything downstream. Pagination logic does not apply. Coverage
does. The question stops being "did I read every card" and becomes "did I search from
enough places".

```python
row = {
    "store_id": "",
    "name": "",
    "street": "",
    "city": "",
    "postcode": "",
    "country": "",
    "lat": None,
    "lon": None,
    "phone": "",
    "hours": [],
    "found_from": {"lat": None, "lon": None, "radius_km": None},
}
```

Keeping `found_from` costs one small object per row and answers the question you will
have later: which seed produced this branch, and therefore whether a gap in the data is a
gap in the chain or a gap in the search.

## Seed with a point grid, not a postcode list

Postcode lists are the obvious seed and the wrong one. They are dense where people are
dense, which is where the radius already overlaps, and sparse exactly where a branch sits
alone off a trunk road. You over-search cities and miss the outliers.

A grid spaced slightly under the search radius covers an area evenly:

```python
import math

def grid(lat0, lat1, lon0, lon1, step_km):
    dlat = step_km / 111.0
    lat = lat0
    while lat <= lat1:
        dlon = step_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        lon = lon0
        while lon <= lon1:
            yield round(lat, 4), round(lon, 4)
            lon += dlon
        lat += dlat
```

The `cos(lat)` term matters. Degrees of longitude narrow toward the poles, so a fixed
step in degrees leaves holes in the south of a country and wastes requests in the north.

Pick `step_km` below the locator's own radius. If the widget searches 50 km, step 40. The
overlap is the point: it is what stops a branch from falling between two circles.

## Read the radius endpoint, not the cards

Almost every locator fetches its results. The form posts a coordinate and a distance, and
the response carries the fields the cards render from, usually including a stable
identifier and exact coordinates the card never shows.

```python
found = {}
seed = {"lat": None, "lon": None, "radius_km": None}

def on_response(resp):
    if "/store" in resp.url and resp.request.resource_type in ("xhr", "fetch"):
        for s in resp.json().get("stores", []):
            if s["id"] in found:
                continue                      # first sighting wins, always
            s["found_from"] = dict(seed)
            found[s["id"]] = s

page.on("response", on_response)
```

Parsing the cards instead costs you the identifier and the coordinates, and it hands you
a phone number formatted for display. The mechanics of attaching to the right call, and
what to do when a page fires several that look alike, are in
[how to capture XHR API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md).

## Dedupe on the identifier, never on the address

Overlapping radii are deliberate, so the same branch arrives many times. That is not a
defect to design away, it is the evidence that the coverage worked.

Dedupe on `store_id`. Deduping on name and address collapses distinct branches that share
one street address, which is exactly what happens inside shopping centres, airports and
department stores, and those are the busiest locations in the set.

The handler above already does this: `seed` is updated before each search and copied
onto a store the first time that store is seen, so the two rules live in one place
instead of two snippets that can drift apart.

Record the first seed that saw it and do not overwrite on later hits. A branch found from
one point and confirmed from three others is the same branch, and the first sighting is
the one that tells you the grid reached it.

## Opening hours are a structure, not a string

`Mon-Fri 9-6, Sat 9-1` is a rendering. Storing it as text pushes the parsing onto whoever
reads the file, and they will do it worse than you can here, because you still have the
response that produced it.

One row per interval:

```python
hours = [
    {"day": "mon", "open": "09:00", "close": "18:00"},
    {"day": "sat", "open": "09:00", "close": "13:00"},
]
```

Watch for the three cases the display string hides: a day with two intervals around a
midday close, a closing time past midnight, and a temporary closure that the endpoint
carries as a flag while the card just omits the day.

## The endpoint usually caps results per query

Most locators return a fixed number of nearest branches, twenty being common, whatever
radius you ask for. In a dense city that cap truncates the answer, and the truncation is
invisible: the response looks complete because it is a full page of results.

That interacts with both rules above. A grid step tuned to the radius under-collects
wherever the cap bites, and the saturation test then stops confidently on an area you
never finished reading.

The check is cheap. If a search returns exactly the cap, the area is denser than the
query can express, so search it again with a smaller radius:

```python
CAP = 20

def search_at(page, lat, lon, radius):
    got = run_query(page, lat, lon, radius)
    if len(got) >= CAP and radius > 2:
        for sub in quarter(lat, lon, radius):     # four smaller circles
            search_at(page, sub[0], sub[1], radius / 2)
    return got
```

## Knowing when the store locator crawl is finished

The counter on the page is not a stopping condition. It usually counts results for the
current query, sometimes counts branches in a marketing region, and on several locators
it is a hardcoded number in a heading.

Use saturation instead. Keep walking the grid and watch the rate of new identifiers:

```python
consecutive_empty = 0
for lat, lon in points:
    before = len(found)
    search(page, lat, lon, radius)
    consecutive_empty = 0 if len(found) > before else consecutive_empty + 1
    if consecutive_empty >= 25:
        break
```

Twenty-five is not a magic number, it is a bet about density that you should set from the
data: in a dense country a real gap of twenty-five points is unlikely, in a sparse one it
is a normal stretch of empty land. Log the count either way, because a run that ends on
saturation and a run that ends on the last grid point are different results.

For picking the crawl back up rather than restarting it, see
[how to resume an interrupted scrape with Playwright](how-to-resume-an-interrupted-scrape-playwright.md).

## Pace it to the endpoint

A grid over a country is thousands of searches against an endpoint sized for one shopper
checking one postcode. Requesting points in parallel is what turns a working scraper into
a blocked one.

Walk the grid sequentially in one browser context. The identity should stay constant for
the whole run, because a locator that sees the same session move across a country looks
like a person planning a trip, while a fleet of fresh sessions hitting radius search from
scattered coordinates looks like exactly what it is.

The reasoning for letting the target set the pace is in
[how to rate limit your scraper with Playwright](how-to-rate-limit-your-scraper-playwright.md),
and keeping one identity across a long crawl is covered in
[isolate identities with one browser context per session](isolate-identities-browser-context-per-session.md).

## Conclusion

The store locator looks like a list and behaves like a spatial index with the query left to
you. Seed a point grid rather than a postcode list, because postcodes cluster where the
radius already overlaps and thin out where the lonely branches are. Capture the radius
response rather than the cards, so you keep the identifier and the real coordinates. Let
the radii overlap, then dedupe on the identifier, never on the address. Turn the hours
string into intervals while you still have the structured response. Then stop on
saturation and record which stopping condition fired.

## Short answers to the questions that lead here

**Why not just use a postcode list?** Postcodes are dense in cities, where the search radius already overlaps, and sparse in
rural areas, where a branch is most likely to sit alone between two of them. A grid
spaced below the radius covers the area evenly for fewer requests.

**The same store keeps coming back. Is the crawl broken?** No, that is the overlap doing its job. Deduplicate on the store identifier and keep the
first seed that found it.

**Can I dedupe on name and address instead?** Not safely. Branches inside shopping centres, airports and department stores share a
street address, and address-level dedup silently merges them.

**How do I know I have every branch?** You do not, from the page. Stop on saturation, meaning a run of consecutive grid points
that returns no new identifier, and record whether the run ended that way or ran out of
grid.

## Sources

- Playwright documentation, [Events and response handling](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- Playwright documentation, [Browser contexts](https://playwright.dev/python/docs/browser-contexts), retrieved 2026-08-28

**See also:** [scraping stock levels](how-to-scrape-stock-levels-playwright.md)
for the per-store numbers behind the same postcode field, [rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for letting the endpoint set the pace, and [capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the radius call rather than the cards.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The postcode list was the first seed tried, and it is the reason the rural
branches went missing for a whole run before anyone noticed.*
