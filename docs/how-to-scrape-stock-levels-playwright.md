---
title: "How to scrape stock levels with Playwright"
description: "Scrape stock levels with Playwright: the badge is a bucket, the number lives in the variant endpoint, and a reading without a timestamp and a variant id is not a stock level."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 96
---


# How to scrape stock levels with Playwright

To scrape stock levels with Playwright, read the variant endpoint rather than the
availability badge: the badge is a bucket like "In stock" or "Low stock" that hides the
integer, stock is a property of the variant and not of the product, and every reading
needs a timestamp and a variant identifier attached or it cannot be compared to the next
one.

Stock looks like the easiest field on the page. It is a word next to a button. The
trouble starts the moment you want two readings to mean something together, because the
word is a rendering of a number you were not shown, taken at a moment nobody recorded.

Stock here means inventory, not share prices, and this page is about recording integers
that can be compared across time. Watching a single availability flag flip from true to
false, which is the restock-alert problem, is
[tracking product stock](how-to-track-product-stock-playwright.md) instead.

## The badge is a bucket, and the buckets are arbitrary

"In stock" can mean four units or four thousand. "Low stock" is a threshold the retailer
picked, and different categories on the same site often use different thresholds.

So a badge is usable for one thing, presence, and useless for the thing people actually
want, which is movement. You cannot subtract two buckets.

```python
row = {
    "variant_id": "",
    "sku": "",
    "product_id": "",
    "options": {"size": "", "colour": ""},
    "available": None,
    "quantity": None,
    "badge_text": "",
    "read_at": "",
    "store_id": None,
}
```

Keep `badge_text` next to `quantity` rather than instead of it. When the endpoint stops
returning an integer, and it will, the badge is the fallback that tells you the reading
was still taken.

## Stock belongs to the variant, not to the product

A product page shows one availability line because it shows one selected variant. Change
the size and the line changes. Scrape the product and you have recorded whichever variant
the page happened to preselect, which is often the first in the list and sometimes the
only one in stock.

The variant identifier is the row key. Without it, two readings of the same product are
not comparable, because they may describe different sizes.

```python
variants = {}

def on_response(resp):
    if "/variants" in resp.url and resp.request.resource_type in ("xhr", "fetch"):
        for v in resp.json().get("variants", []):
            variants[v["id"]] = v

page.on("response", on_response)
```

Attaching to the right response, and telling apart several calls that look alike, is
covered in
[how to capture XHR API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md).

## A reading without a timestamp is not a reading

Stock is the one field on a product page that is worthless without knowing when it was
observed. A price scraped an hour late is still that price. A quantity scraped an hour
late is a different quantity.

Record the time at the moment the response arrives, not when the row is written:

```python
import datetime as dt

def record(v):
    return {
        "variant_id": v["id"],
        "quantity": v.get("inventory_quantity"),
        "available": v.get("available"),
        "read_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
```

Use UTC. A stock series that mixes local times across a run spanning midnight produces
apparent restocks that are timezone artifacts.

## Selecting a variant changes the page, and sometimes the URL

Most sites update availability by clicking a size or colour swatch, which fires a request
and repaints one region. Some also push a new URL, which means a naive loop that reloads
the page loses the selection it just made.

Click, then wait for the region tied to that variant rather than for a timeout:

```python
options = page.locator("[data-variant-id]")
ids = [options.nth(i).get_attribute("data-variant-id")
       for i in range(options.count())]         # read ids first, hold no handles

for vid in ids:
    page.locator(f"[data-variant-id='{vid}']").click()   # re-resolved every round
    page.locator(f"[data-availability-for='{vid}']").wait_for()
    rows.append(record(variants.get(vid, {"id": vid})))
```

Waiting on a selector keyed to the variant matters. A generic wait on the availability
element passes instantly, because the previous variant's element is still in the DOM, and
you record the same number twice under two identifiers.

The general shape of that trap is in
[how to scrape a load-more button with Playwright](how-to-scrape-load-more-button-playwright.md).

## Store-level stock is a different question

Retailers with shops usually carry two numbers: online availability and per-store
availability behind a postcode field. They are not the same field and they move
independently, so a row needs to say which one it holds.

That is what `store_id` is for, with `None` meaning online. Collapsing both into one
column produces a series where a warehouse restock and a single shop receiving two units
look identical.

For the geography side of walking a store list, see
[how to scrape store locator pages with Playwright](how-to-scrape-store-locator-pages-playwright.md).

## Pace it, because stock is what rate limits are built for

Price pages tolerate a brisk crawl. Inventory endpoints are the ones retailers watch,
because repeated variant-level polling is the signature of a competitor or a reseller
bot, and it is also the request that costs them a database read.

One context, sequential requests, and a revisit interval set by how fast the number
actually changes rather than by how fast you can ask. Most stock does not move minute to
minute, and polling as if it did buys noise and a block.

The reasoning is in
[how to rate limit your scraper with Playwright](how-to-rate-limit-your-scraper-playwright.md),
and the backoff side in
[how to handle 403 and 429 backoff mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## Zero is a value, and missing is not zero

When a variant sells out, some endpoints return `0`, some drop the variant from the
response, and some keep it with a null quantity and a changed badge. All three mean
different things for a series.

Write them differently. `quantity: 0` is an observed zero. `quantity: None` with
`available: false` is an observed unavailability without a number. A variant absent from
the response is not a row at all, and inventing a zero for it turns a page change into a
sellout that never happened.

## Conclusion

Stock is the field that punishes a careless row shape hardest, because its whole value is
in comparing two readings. Read the variant endpoint, not the badge, and keep the badge
beside the number as the fallback. Key every row on the variant. Stamp it in UTC at the
moment the response lands. Say whether it is online or a named store. Then treat absent,
null and zero as three different observations, because they are.

## Short answers to the questions that lead here

**Why not just record the In stock badge?** Because the buckets are arbitrary and differ by category, so two badges cannot be
subtracted. The badge answers presence, not movement.

**The product page shows one availability. Is that enough?** Only for the variant the page preselected. Availability belongs to the size and colour
combination, so a product-level row silently describes whichever variant loaded first.

**The variant disappeared from the response. Is that a zero?** No. Record it as absent. A sold-out variant that the endpoint drops and a variant
reported at zero are different observations, and merging them invents sellouts.

**How often should I re-read?** At the rate the number actually changes, which for most catalogues is hours rather than
minutes. Variant-level polling is the pattern inventory endpoints are rate limited
against.

## Sources

- Playwright documentation, [Events and response handling](https://playwright.dev/python/docs/events), retrieved 2026-08-28
- Playwright documentation, [Auto-waiting](https://playwright.dev/python/docs/actionability), retrieved 2026-08-28

**See also:** [scraping store locator pages](how-to-scrape-store-locator-pages-playwright.md)
for walking the shops the per-store numbers belong to, [capturing XHR API responses](how-to-capture-xhr-api-responses-playwright.md)
for the variant endpoint, and [handling 403 and 429 backoff](how-to-handle-403-429-backoff-mid-scrape-playwright.md)
for what inventory polling earns you.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. Treating an absent variant as a zero is the error that produced a week of
sellouts that never happened.*
