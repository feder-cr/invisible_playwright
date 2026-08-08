---
title: "How to scrape vacation rental listings with Playwright"
description: "Scrape vacation rental listings with Playwright: drive the date and guest inputs, read the fee-inclusive total from the pricing XHR, and iterate date windows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 32
---


# How to scrape vacation rental listings with Playwright

To scrape vacation rental listings with Playwright, drive a listing's date and guest
inputs so the site prices a concrete stay, then read the fee-inclusive total from the
pricing XHR response rather than the headline number on the card. Because a full picture
of one property means pricing many date windows, run the whole sweep under one fixed
`seed`, so the burst looks like a single person comparing dates instead of a swarm of new
devices.

Most people scrape a rentals search the way they scrape a product grid: load the
results, read the headline price off each card, paginate, done. On a vacation-rental
marketplace that gives you a table of numbers that nobody will ever be charged, and it
does it while quietly training the availability endpoint to rate-limit you.

This page is about why the card number is not a price, how to make the site compute the
real total for you, where that total actually arrives, and how to iterate many date
windows per property without looking like a fresh device on every request.

## Why the number on the card is not the price

A nightly rate on a rentals listing is not a single value. It is a function of three
inputs, and the card shows you the answer for none of them:

- **The date range.** A weekday in November and a Saturday in July are different rates
  for the same room, because the host prices by season, day of week and local demand.
- **The guest count.** Many listings add a per-guest surcharge above a threshold, so the
  same dates cost more for four people than for two.
- **The length of stay.** Weekly and monthly discounts, and minimum-stay rules, mean the
  per-night figure changes with how many nights you select.

And even once you fix all three, the number a reservation would actually cost is the
**fee-inclusive total**: nightly subtotal, plus a cleaning fee, plus a service fee, plus
any local tax. That total does not exist on the card at all. It only materialises after
the site prices a concrete stay. The headline "from $X a night" is a starting point for
a search, not a quote, and a dataset built from it is a dataset of numbers that were
never real.

So the job is not "read the price". The job is "make the site price a stay, then read
what it computed".

## Drive the dates and the guests, then read the total

The switch from plain Playwright is two lines, and everything after the launch is stock
Playwright, so the parts below are ordinary page automation. Fill the date and guest
inputs on a listing, trigger the update, and let the site do the pricing math.

```python
from invisible_playwright import InvisiblePlaywright

LISTING_URL = "https://example.com/rooms/12345"

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto(LISTING_URL, wait_until="domcontentloaded")

    # Open the date picker and choose a concrete window.
    page.click("[data-testid='change-dates-button']")
    page.click("[data-date='2026-09-10']")   # check-in
    page.click("[data-date='2026-09-13']")   # check-out

    # Set the guest count, above and below any surcharge threshold as needed.
    page.click("[data-testid='guest-picker']")
    page.click("[data-testid='adults-increase']")   # 2 guests
    page.click("[data-testid='guest-picker-apply']")

    # Confirm the total redrew for these inputs, not the placeholder.
    page.wait_for_selector("[data-testid='price-breakdown'] .total")
    total_text = page.inner_text("[data-testid='price-breakdown'] .total")
    print("shown total:", total_text)
```

Reading the total off the DOM works, but the rendered breakdown is often the last thing
to settle and the easiest to read half-drawn. The reliable source is the request the
page made to price the stay. Choosing the right wait condition here is the whole
difficulty of the page, and it has [its own guide on what to wait
for](how-to-wait-for-page-load-playwright.md).

## Capture the fee-inclusive total from the XHR

When you pick dates and guests, the page calls an availability or pricing endpoint and
renders the response. That JSON response is the authoritative total, with every fee
itemised, before any presentation layer rounds or reformats it. Wait for the response with
Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
context manager rather than scraping the redrawn DOM.

```python
def is_pricing_response(response):
    # Match the endpoint the listing calls to price a concrete stay.
    return "availability" in response.url and response.status == 200

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto(LISTING_URL, wait_until="domcontentloaded")

    # The click that changes the stay is what fires the pricing call, so arm
    # the wait first, then trigger it, then read the parsed body.
    with page.expect_response(is_pricing_response) as pricing:
        page.click("[data-testid='guest-picker-apply']")

    quote = pricing.value.json()
    print("nightly:", quote["nightly_subtotal"])
    print("cleaning:", quote["cleaning_fee"])
    print("service:", quote["service_fee"])
    print("all-in total:", quote["total"])
```

Reading the response body instead of the DOM removes the entire class of rounding and
formatting differences, and it gives you the fee split the card never shows. The general
technique, including how to buffer responses that arrive before your handler is attached,
is in [capturing XHR and API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md).

One geographic wrinkle worth naming: the same listing can be priced in a different
currency, and taxed differently, depending on where the request appears to come from. If
your totals need to match what a guest in a specific country would see, the exit location
has to be part of the identity, which is [its own topic for
geotargeted content](how-to-scrape-geotargeted-content-playwright.md).

## Iterate date windows per property without becoming a swarm

A vacation-rental crawl differs from most scraping jobs because one listing needs many
pricing calls, not one: to build a real picture of a single property you do not read it
once, you price it across many windows:
a run of weekends, a midweek block, a full week for the discount, two guests and then
five. That is ten or twenty pricing calls for **one** listing, and across a few hundred
listings it becomes a burst of thousands of availability requests in a short span.

An availability endpoint watches exactly that shape. A thousand pricing calls are not
suspicious on their own, but a thousand pricing calls where each one appears to come from
a brand-new device is. The default in this library is a distinct fingerprint per session
(GPU, audio, fonts, screen and hundreds of other fields), which is what you want when
sessions should look unrelated. For a pricing sweep it is the opposite of what you want: it turns
one researcher reading one property's calendar into what looks like a coordinated swarm.

Passing a fixed `seed` makes the whole burst one consistent browser. Every window, every
guest count, every listing in the sweep is priced by the same machine, which is what a
real person comparing dates actually looks like. Fixing the identity handles who the
requests look like; it does not slow them down, so pair it with [deliberate pacing of the
request rate](how-to-rate-limit-your-scraper-playwright.md) when the sweep is large.

```python
from invisible_playwright import InvisiblePlaywright

WINDOWS = [
    ("2026-09-10", "2026-09-13", 2),   # a long weekend
    ("2026-09-17", "2026-09-20", 2),   # the next weekend
    ("2026-09-14", "2026-09-21", 2),   # a full week, for the discount
    ("2026-09-10", "2026-09-13", 5),   # same dates, above the guest threshold
]

def price_window(page, check_in, check_out, guests):
    def is_pricing_response(r):
        return "availability" in r.url and r.status == 200
    with page.expect_response(is_pricing_response) as pricing:
        page.evaluate(
            "([ci, co, g]) => window.__setStay(ci, co, g)",
            [check_in, check_out, guests],
        )
    return pricing.value.json()

# One seed for the whole sweep: the same browser prices every window.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto(LISTING_URL, wait_until="domcontentloaded")
    quotes = []
    for check_in, check_out, guests in WINDOWS:
        q = price_window(page, check_in, check_out, guests)
        quotes.append((check_in, check_out, guests, q["total"]))
    for row in quotes:
        print(row)
```

Keeping the identity fixed also makes the crawl debuggable. If one window's pricing call
fails or returns something strange, you replay the exact same browser and get the exact
same failure instead of a new random machine that behaves differently, which is the whole
argument for [pinning the identity](playwright-detected-as-bot.md) when a run misbehaves.

## The map paginates, so page the map

The results side of a rentals site paginates like real estate rather than like a shop:
the list is bound to a map, and what you see is what falls inside the current map bounds,
capped at a fixed number of pins per view. "Next page" often means "move or shrink the
viewport", not "increment an offset". If you only click through the paginator you will
silently miss every listing the map never brought into view. This map-bound behaviour has
[its own walkthrough for scraping a map-based search](how-to-scrape-map-based-search-playwright.md);
the short version for a rentals sweep is below.

The robust approach is to drive the search by geographic bounds and walk a grid of
smaller boxes, collecting listing IDs into a set as you go, so overlaps deduplicate
themselves.

```python
BOXES = [
    # (sw_lat, sw_lng, ne_lat, ne_lng) tiles covering the target area
    (45.40, 12.30, 45.45, 12.35),
    (45.45, 12.30, 45.50, 12.35),
    (45.40, 12.35, 45.45, 12.40),
    (45.45, 12.35, 45.50, 12.40),
]

seen = set()
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for sw_lat, sw_lng, ne_lat, ne_lng in BOXES:
        url = (
            "https://example.com/search"
            f"?sw_lat={sw_lat}&sw_lng={sw_lng}"
            f"&ne_lat={ne_lat}&ne_lng={ne_lng}"
        )
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='listing-card']")
        ids = page.eval_on_selector_all(
            "[data-testid='listing-card']",
            "cards => cards.map(c => c.dataset.listingId)",
        )
        seen.update(ids)

print("unique listings found:", len(seen))
```

Collect the IDs first across the whole map, then price each one across your date windows
in the seeded session above. Separating discovery from pricing keeps each phase simple and
keeps the pricing burst uniform.

## What the fixed fingerprint does not do

A consistent identity keeps a large pricing sweep coherent. It does not change what the
total means, and it is worth being precise about the limit so nobody ships a snapshot as
a quote.

The fingerprint does not reserve inventory and it does not hold a session. Nothing about
looking like one real browser puts a room aside for you or freezes its price. The total
you captured is a reading of what the site would have charged **at the moment you read
it**, for dates that were available then. Availability and pricing both move: a stay can
sell out, a host can change the rate, a discount can expire, all between your search and
your read, and certainly between today's crawl and tomorrow's.

So treat every total as a timestamped snapshot, not a quote. Store the check-in,
check-out, guest count and the exact time you priced it alongside the number, because the
number without those four things is not reproducible and not comparable. This is the same
honesty the rest of these notes insist on: a value that looks authoritative and is
actually stale is worse than a value you know is a snapshot.

## Conclusion

Vacation-rental pricing is not a field you read, it is a computation you trigger. Drive
the dates and the guests, wait for the pricing XHR instead of the redrawn DOM, and read
the fee-inclusive total from the response body. Because a full picture of one property is
many priced windows, the sweep is a burst, and a fixed seed keeps that burst one
consistent browser rather than a swarm the availability endpoint is built to throttle.
Then remember what the identity cannot buy you: the total is a snapshot in time, so it
travels with its dates, its guest count and its timestamp or it means nothing.

## Short answers to the questions that lead here

**Why is the price on the card different from the price at checkout?** Because the card
shows a starting nightly rate with no fees, and the checkout total is nightly plus
cleaning plus service plus tax for the specific dates and guests you chose. They are
different numbers by design.

**How do I get the total with fees included?** Pick concrete dates and a guest count so
the site prices a real stay, then read the availability or pricing XHR response rather
than the rendered breakdown. The response itemises every fee.

**Why do I get rate-limited when I scrape many dates?** Because pricing one property
across many windows is a burst of availability calls, and it looks worse if each call
appears to come from a new device. Fix the seed so the whole sweep is one browser.

**Should I use one seed or many?** One seed for a pricing sweep that should look like a
single person comparing dates. Distinct fingerprints are for sessions that should look
unrelated, which a sweep is not.

**Why does pagination miss listings?** Because results are bound to a map and capped per
view, so clicking through pages never surfaces listings outside the current bounds. Walk
a grid of map boxes and deduplicate IDs instead.

**Is the total I scraped a guaranteed price?** No. It is a snapshot at the moment you read
it. The fingerprint does not reserve inventory or hold a session, and availability and
rates change, so store the dates, guests and timestamp with every total.

## Sources

- This project's [Quickstart](quickstart.md) and [Configuration](configuration.md) pages
  for the real launch, seed and proxy API used in every example above.
- The behaviour of date-and-guest-driven pricing endpoints and map-bound pagination, read
  from live rentals marketplaces during testing rather than from any single site's docs.
- This project's own release notes on why a per-session fingerprint is the wrong default
  for a single-actor burst against a scoring endpoint.

**See also:** [capturing XHR and API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md),
[what to wait for before you read the page](how-to-wait-for-page-load-playwright.md), and
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md) when the
currency and tax on a listing depend on where the request appears to come from.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The fixed seed keeps a
pricing sweep looking like one person comparing dates rather than a swarm of new
machines.*
