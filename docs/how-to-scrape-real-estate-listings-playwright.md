---
title: "How to scrape real estate listings with Playwright"
description: "Scrape real estate listings with Playwright: portals re-fetch results from a map-bounds XHR, not navigation, and hide price and beds in detail-page JSON-LD."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 25
---


# How to scrape real estate listings with Playwright

To scrape real estate listings with Playwright, drive the map-bounds XHR that repaints the
results as the viewport pans, then read price, beds and baths from each detail page's
JSON-LD. A crawler that only walks URLs fires neither request and comes back with a
fraction of the inventory.

A property portal looks like a list of cards next to a map. It is really a map with a
list bolted to the side, and that inversion is the whole reason a straightforward
crawler comes back with a fraction of the inventory and never notices.

This page is about the two things that trip people up: the results arrive from a
map-bounds request that a URL walk never fires, and the numbers you actually want are
not on the card at all. There is also one honest limit at the end that no fingerprint
fixes.

## Why a URL crawler misses most of the inventory

A URL crawler misses most of the inventory because the results are not tied to the URL at
all: they arrive from a map-bounds request that only fires when the viewport moves. Open a
metro-area search and watch the network panel while you drag the map. The page does not
navigate. It fires an XHR carrying the map viewport (a bounding box, and often a zoom
level), and the card column repaints from the JSON that comes back.

That has three consequences a card-only crawler quietly loses to:

- **Panning and zooming re-fetch, navigation does not.** The URL may not change at all as
  you move the map, so a crawler that only visits URLs sees one viewport's worth of
  results and calls the region done. The next thousand listings are one pan to the east.
- **The card is a teaser.** Price, bedroom and bathroom counts, square footage, the agent
  and the full address usually live on the detail page, frequently inside a JSON-LD
  block, not in the search response. The card gives you an ID and a thumbnail.
- **Photos lazy-load on scroll.** The gallery is empty markup until the row enters the
  viewport, so a detail page grabbed the instant it loads has no images.

So a real harvest is two nested loops: pan the map across the region to enumerate IDs,
then open each ID's detail page and read its structured data. Both loops run from one
browser session, and together they are hundreds to thousands of requests for a single
metro.

## Drive the map-bounds XHR, not just the cards

The reliable pattern is to let Playwright capture responses while you move the map, and
to synchronise each pan against the XHR it triggers rather than guessing at a sleep.

```python
from invisible_playwright import InvisiblePlaywright

def is_search_batch(response):
    # match the map-search endpoint your portal uses; keep it specific
    return "/api/" in response.url and "bounds" in response.url

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/search?region=metro-north")
    page.wait_for_load_state("networkidle")

    seen = {}
    cols, rows = 6, 4  # tile the region into viewports and pan across it

    for _ in range(cols * rows):
        # expect_response ties the pan to the XHR it fires, no blind sleep
        with page.expect_response(is_search_batch) as batch:
            page.mouse.move(640, 420)
            page.mouse.down()
            page.mouse.move(220, 420, steps=24)  # drag one viewport east
            page.mouse.up()

        data = batch.value.json()
        for item in data.get("results", []):
            seen[item["id"]] = item.get("detailUrl")

    print("distinct listings enumerated:", len(seen))
```

Two details do the real work. [`page.expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
blocks until the map-bounds request you named actually returns, so you are reading a
completed batch rather than racing the network. And you collect IDs into a dict keyed by
listing ID, because tiles overlap and the same property will come back in several
viewports.

If your portal streams more cards as you scroll the list column instead of paging it, the
same capture idea applies to that scroll loop. The mechanics of reading each fetch as it
arrives are in [capturing the XHR the page already makes](how-to-capture-xhr-api-responses-playwright.md),
and driving a growing list is covered in [scraping an infinite-scroll feed](how-to-scrape-infinite-scroll-playwright.md).

## Pull price, beds and baths from the detail page JSON-LD

Once you have the IDs, the numbers come from each detail page. Many portals publish a
[`application/ld+json`](https://www.w3.org/TR/json-ld11/) block for search engines, and it
is far more stable than scraping the rendered DOM, which changes with every design refresh.

```python
import json

def extract_listing(page):
    blocks = page.locator('script[type="application/ld+json"]').all_text_contents()
    for raw in blocks:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # a JSON-LD block may be one object, a list, or wrapped in @graph
        nodes = doc if isinstance(doc, list) else doc.get("@graph", [doc])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") in ("Product", "Residence", "Offer", "SingleFamilyResidence"):
                offer = node.get("offers", {})
                return {
                    "name": node.get("name"),
                    "price": offer.get("price"),
                    "beds": node.get("numberOfBedrooms"),
                    "baths": node.get("numberOfBathroomsTotal"),
                    "address": node.get("address"),
                }
    return None

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for listing_id, url in seen.items():
        page.goto(url)
        # nudge the page so the lazy gallery and any deferred JSON-LD attach
        page.mouse.wheel(0, 2000)
        page.wait_for_load_state("networkidle")
        row = extract_listing(page)
        if row:
            print(listing_id, row["price"], row["beds"], row["baths"])
```

Read the shape of the JSON-LD once by hand before you trust it. Some portals put the
price under `offers.price`, some under `offers.priceSpecification.price`, and a few omit
it when a listing is off-market, so a missing field is data, not always a bug.

## Why one fixed fingerprint helps a long crawl (and one honest limit)

A metro harvest is a long session: thousands of card-to-detail hits plus repeated
map-bounds requests, all from the same browser. That is exactly the shape a crawl fails
in the middle of, and the recovery is where a seeded identity earns its place.

Pass `seed=42` and every generated field is deterministic. The same seed returns the same
GPU string, the same canvas hash and the same audio context on every run, so a crawl that
dies on listing 4,000 of 9,000 resumes as the identical Firefox rather than reconnecting
as a fresh random device the map endpoint has never seen. Continuity across a resume is a
property you want; a session that changes machines halfway through a region is its own
signal.

```python
sf = InvisiblePlaywright(seed=42)
with sf as browser:
    print("resuming as seed", sf.seed)  # log it so a restart replays the same device
    ...
```

Here is the honest part. **A fixed fingerprint makes you look like one consistent real
browser. It does not make you look like more of them, and it does not raise how many
requests one exit can make before it is throttled.** Thousands of detail hits and hundreds
of map-bounds fetches from a single address in a short window is a volume pattern, and a
byte-perfect fingerprint on top of that volume just means you get rate-limited with an
excellent disguise. Fingerprint consistency and request budget are different problems, and
this page only solves the first.

## Pace it and rotate the exit, or the fingerprint won't save you

Metro-scale harvesting needs the second half: spread the load and change the exit. A
seeded identity keeps each request looking real; pacing and rotation keep the volume from
looking like one machine draining a region.

```python
import random, time

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    for listing_id, url in seen.items():
        page.goto(url)
        row = extract_listing(page)
        # jittered pacing, not a fixed interval a detector can clock
        time.sleep(random.uniform(1.5, 4.0))
```

Rotate the exit across regions or across batches so no single address carries the whole
metro, and keep the browser timezone honest to whatever exit you are on, because a session
whose clock and IP disagree is flagged for the mismatch alone. The rotation mechanics are
in [rotating proxies across a crawl](how-to-rotate-proxies-playwright.md), the geo-matching
in [scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md), and the
clock-versus-exit trap has [its own page](timezone-proxy-mismatch.md). If you need a
specific screen size or GPU held fixed while the rest stays seed-derived, that is
[pinning individual fields](pinning.md).

## Conclusion

Treat the portal as a map, not a list. Drive the map-bounds XHR to enumerate every
viewport, then read price, beds and baths from each detail page's JSON-LD, and scroll to
pull the lazy gallery. Run it under a fixed seed so a crash resumes as the same browser
instead of a new one. Then accept the division of labour: the seed handles who you look
like, and pacing plus rotation handle how much you ask for. Skip the second half and you
get throttled with a perfect fingerprint, which is a very well-disguised way to collect
half a region.

## Short answers to the questions that lead here

**Why does my crawler only get a handful of listings per area?** Because results re-fetch
from a map-bounds XHR when the viewport pans, and navigating URLs never fires it. Drive
the map, do not just walk cards.

**Where are the price, bedrooms and bathrooms?** Usually on the detail page, often inside
a `application/ld+json` block, not on the search card. The card typically carries only an
ID and a thumbnail.

**Why are the photos missing when I scrape a listing?** The gallery lazy-loads on scroll.
Scroll the detail page and wait for the network to settle before you read images.

**Does a fixed seed let me scrape a whole metro from one IP?** No. It keeps every request
looking like the same real browser, but it does not raise your request ceiling. Volume
from one exit still gets throttled, so you need pacing and proxy rotation.

**How do I capture the map request instead of guessing?** Register `page.on("response")`
or wrap each pan in `page.expect_response(...)` so you read the completed batch rather than
racing a sleep against the network.

**Why deduplicate the listing IDs?** Because map tiles overlap, so the same property comes
back in several viewports. Key results by listing ID as you collect them.

## Sources

- The real estate portal request pattern (map-bounds XHR on pan, JSON-LD on the detail
  page, lazy-loaded galleries) as observed across property search portals, described here
  generically.
- [JSON-LD 1.1 (W3C Recommendation)](https://www.w3.org/TR/json-ld11/), the spec behind the
  `application/ld+json` blocks the detail-page extractor reads.
- [`page.expect_response` (Playwright docs)](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  the API this page uses to synchronise each map pan against its XHR instead of guessing at
  a sleep.
- This project's own behaviour: a seed produces a deterministic identity, so the same
  seed returns the same GPU, canvas hash and audio context on every run, verified against
  the reproducibility gates.

**See also:** [capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md),
[scraping an infinite-scroll feed](how-to-scrape-infinite-scroll-playwright.md), and
[rotating proxies across a crawl](how-to-rotate-proxies-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The map-bounds loop is the
part every first crawler of a property site gets wrong, mine included.*
