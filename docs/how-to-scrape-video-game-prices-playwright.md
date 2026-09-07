---
title: "How to scrape video game prices with Playwright"
description: "Scrape video game prices with Playwright: key rows on the edition or SKU instead of the title, record region, currency and read time with every price, and keep physical, digital and pre-order listings as separate rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 139
---


# How to scrape video game prices with Playwright

To scrape video game prices with Playwright, key every row on the edition or SKU
instead of the title text, capture the region and currency the storefront used to
decide that price, record the final discounted price and the total percent-off
exactly as shown instead of reconstructing the sale, publisher and bundle discounts
stacked behind it, stamp the row with the moment you read it because a
countdown-driven discount reverts on its own schedule, and keep physical, digital
and pre-order listings as separate rows with their own release status.

A game's storefront page looks like one product with one price. It is closer to a
small catalog wearing a single title. The same name can cover a base edition, a
deluxe edition with a season pass bundled in, and a cross-title bundle, each a
distinct SKU with its own price and its own sale. The number on screen is usually
the final result of two or three discounts stacked on top of each other, ticking
down against a clock that resets the price the moment it hits zero. None of that
shows up if you scrape the way you would scrape a static product listing.

This page is the row shape that survives contact with all of it: what to use as the
key instead of the title, what to record instead of what to infer, and which
listings look like duplicates of each other but are not.

## Why the title on the box is not the row key

A storefront search result for one game commonly returns three or four separate
product cards under a name that reads identically on every one of them: a base
edition, a deluxe edition, a bundle that pairs the base game with an older title. Each
of those cards is its own product, with its own price, its own discount, and
sometimes its own release date. If a scraper matches rows by title text, the deluxe
edition's price and the base edition's price land under the same key, and whichever
one was scraped last overwrites the other. A price series built that way reports a
sudden jump that is really just two unrelated products taking turns writing to the
same row.

The identifier that stays stable is whatever SKU or edition code the storefront
itself assigns, because the display title is deliberately reused across editions to
keep search results relevant to a fuzzy game name. Pull that value from the card,
not from the heading text.

```python
from invisible_playwright import InvisiblePlaywright

def read_listing_rows(page):
    rows = []
    for card in page.locator("[data-sku]").all():
        edition_el = card.locator(".edition-label")
        rows.append({
            "sku": card.get_attribute("data-sku"),
            "title": card.locator(".product-title").inner_text().strip(),
            "edition": edition_el.inner_text().strip() if edition_el.count() else "standard",
            "format": card.get_attribute("data-format") or "digital",
            "price_text": card.locator(".price-final").inner_text().strip(),
        })
    return rows

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/game/skyfall-chronicles", wait_until="domcontentloaded")
    for row in read_listing_rows(page):
        print(row["sku"], row["edition"], row["format"], row["price_text"])
```

Three cards, one title text, three different SKUs: that is the correct output for a
storefront selling a base game, a deluxe edition and a bundle under one name. A row
store keyed on `sku` keeps all three; one keyed on `title` collapses them into one
row that means nothing.

## A price without a region and currency is not one you can compare

Storefronts commonly price by detected region before any currency conversion enters
the picture at all. The same SKU can carry a different number in two countries even
when the symbol on screen looks the same, and the currency itself often changes
outright, not just the amount. A price you recorded without its region and currency
cannot be compared against a price scraped later from a different exit, because you
cannot tell whether the number moved because the game actually changed price or
because the second scrape landed in a different region and read a different
number that was never comparable to the first.

The region is a consequence of where the request appears to originate, not a field
you set directly, so the honest fix is to record what actually informed the price:
the currency the page rendered in, and, where the storefront states a detected
region explicitly, such as a locale selector or a country field in the page's own
metadata, that value too.

```python
def read_price_with_region(page):
    currency = page.locator("[data-currency]").get_attribute("data-currency")
    region_el = page.locator("[data-region-selector]")
    region = region_el.inner_text().strip() if region_el.count() else None
    return {
        "currency": currency,
        "region": region,
        "price_text": page.locator(".price-final").inner_text().strip(),
    }
```

Parsing the number itself into a typed value depends on the same locale the price
was rendered in, since the decimal separator and the currency symbol's position both
follow it. [Cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
covers that parsing step once region and currency are recorded.

## Sampling more than one region on purpose

A deliberate cross-region comparison needs a matched proxy exit per region, the same
way any [geotargeted content](how-to-scrape-geotargeted-content-playwright.md) works:
point the exit at the region you want, let the browser's timezone and locale follow
it, and read whatever price that region actually renders. A scraper that hits the
same URL from a single exit and expects two different regional prices back is asking
the storefront to disagree with itself, and it never will.

```python
REGION_PROXIES = {
    "US": {"server": "socks5://us-exit.example.com:1080", "username": "u", "password": "p"},
    "DE": {"server": "socks5://de-exit.example.com:1080", "username": "u", "password": "p"},
    "BR": {"server": "socks5://br-exit.example.com:1080", "username": "u", "password": "p"},
}

def sweep_regions(sku_url):
    rows = []
    for region, proxy in REGION_PROXIES.items():
        with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
            page = browser.new_page()
            page.goto(sku_url, wait_until="domcontentloaded")
            snapshot = read_price_with_region(page)
            snapshot["region_target"] = region
            rows.append(snapshot)
    return rows
```

Let the timezone and locale derive from the exit rather than pinning them by hand.
A hand-set locale that disagrees with the exit is exactly the
[timezone and locale mismatch](timezone-proxy-mismatch.md) a storefront's own
cross-check is built to notice, on top of being the wrong locale to parse the price
against anyway.

## Discounts stack, so record what the page shows, not what you infer

A storefront-wide sale, a publisher-specific discount and a bundle discount can all
apply to the same SKU at once, and the page almost never itemizes them. It shows one
final price and one total percent-off badge, not a breakdown of which layer
contributed what. Trying to back out "10% sale plus 15% publisher plus 5% bundle"
from a single visible percentage is solving math the page never published, and the
guess breaks the first time the storefront changes its rounding or the order it
applies discounts in. Record the final price, the pre-discount list price when it is
shown struck through, and the total percent-off text, precisely as rendered.

```python
def read_discount_fields(card):
    final_price = card.locator(".price-final").inner_text().strip()
    list_el = card.locator(".price-list-strike")
    list_price = list_el.inner_text().strip() if list_el.count() else None
    badge_el = card.locator(".discount-badge")
    percent_off = badge_el.inner_text().strip() if badge_el.count() else None
    return {
        "price_final": final_price,
        "price_list": list_price,
        "percent_off": percent_off,
    }
```

Some deal-style listings hide the actual code or fine print behind a reveal
interaction rather than showing it in the card at all. The mechanics for firing that
reveal with a trusted click, not a scripted one a bot check can see through, are in
[scraping deals and coupon codes](how-to-scrape-deals-and-coupon-codes-playwright.md).

## A discount has a clock, so stamp the row with the moment you read it

The countdown next to a deal is not decoration. It counts down to the exact moment
the discount ends and the price reverts automatically, usually with no announcement
beyond the timer running out. A price row without a read timestamp attached is not
meaningful as part of a series, because nothing in the row itself says whether a
jump back to full price happened on schedule or for some other reason. Where the
page exposes a machine-readable end time, a data attribute or an ISO string sitting
near the timer, read that directly instead of parsing the rendered "2d 14h 03m"
text, which is relative to render time and already stale by the time your script
reads it.

```python
import datetime
import re

def read_discount_window(card, read_at):
    end_attr = card.get_attribute("data-discount-ends")
    if end_attr:
        return {"discount_ends": end_attr, "read_at": read_at.isoformat()}

    # Fallback: parse "2d 14h 03m" relative to this exact read, not to
    # whenever the row is examined later.
    countdown_text = card.locator(".countdown").inner_text().strip()
    match = re.match(r"(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?", countdown_text)
    days, hours, minutes = (int(g) if g else 0 for g in match.groups())
    remaining = datetime.timedelta(days=days, hours=hours, minutes=minutes)
    return {
        "discount_ends": (read_at + remaining).isoformat(),
        "read_at": read_at.isoformat(),
    }
```

Sampling on a recurring schedule and diffing against the last reading is its own
problem, separate from the row shape covered here.
[Tracking product prices](how-to-track-product-prices-playwright.md) covers the
longitudinal side: keeping one stable identity across daily runs so a returning
monitor does not read as a fresh device every morning.

## Physical and digital are different products, not two views of one

A retailer or a storefront commonly lists a physical disc or cartridge SKU next to a
digital download SKU for the same title, and the two prices move on their own
schedules. A digital sale can run while the physical copy sits at list price, or a
physical clearance can undercut a digital price that has not moved in months. Folding
both into one row because the title text matches erases exactly the comparison
someone tracking game prices usually wants: whether digital is cheaper than physical
right now, and by how much.

Treat format as part of the row key, the same way edition already is in the
`read_listing_rows` function above. Its `format` field, read from a `data-format`
attribute, is what keeps the physical SKU and the digital SKU as two separate rows
under the same title instead of one row that silently picks whichever card the DOM
happened to list last. A title with both formats live should produce two rows in
every scrape, not one, and a scrape that returns only one is a sign the selector
missed a card rather than a sign the retailer only sells one format.

## Pre-order prices are a different state than released prices

A title in pre-order status often prices differently than it will after release,
sometimes locked at a guaranteed number regardless of sales that run before launch,
sometimes discounted specifically to reward buying early. Storefronts usually mark
that state explicitly, with a "Pre-order" badge, a release date field, or a
`data-release-status` attribute, precisely because the pricing logic differs while a
title has not shipped yet. A scraper that assumes every SKU it finds has already
released will treat a locked pre-order price as an ordinary steady price, when it is
actually a different pricing regime that ends the day the game ships and gets
replaced by whatever the post-release price turns out to be.

```python
def read_release_state(card):
    return {
        "release_status": card.get_attribute("data-release-status") or "released",
        "release_date": card.get_attribute("data-release-date"),
    }
```

Record `release_status` on every row, even for titles you assume have shipped. The
assumption is the part that breaks: a bundle SKU can bring an already-released title
and an unreleased one under the same product page, with two different pricing
regimes sitting one card apart.

## Putting it together: the row shape that survives a second scrape

One function folds everything above into a single row: the SKU and edition from the
first section, the region and currency from the second, the discount fields as
shown, the discount window stamped with a read time, and the release status. Run it
per region, and each region's sweep produces its own set of rows rather than
overwriting the others.

```python
import datetime
from invisible_playwright import InvisiblePlaywright

def build_price_row(card, region, currency):
    read_at = datetime.datetime.now(datetime.timezone.utc)
    row = {
        "sku": card.get_attribute("data-sku"),
        "title": card.locator(".product-title").inner_text().strip(),
        "edition": card.locator(".edition-label").inner_text().strip()
            if card.locator(".edition-label").count() else "standard",
        "format": card.get_attribute("data-format") or "digital",
        "region": region,
        "currency": currency,
    }
    row.update(read_discount_fields(card))
    row.update(read_discount_window(card, read_at))
    row.update(read_release_state(card))
    return row

def scrape_regional_listing(url, region, proxy):
    with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        currency = page.locator("[data-currency]").get_attribute("data-currency")
        return [
            build_price_row(card, region, currency)
            for card in page.locator("[data-sku]").all()
        ]
```

The output is one row per SKU per region per read, and every field on that row
answers a question a plain "price: 39.99" cannot: which edition, which format, which
region set that number, what it looked like before the discount, when the discount
ends, whether the title has even shipped, and the exact moment it was all true.

## Conclusion

A game's price is not a single number attached to a title. It is a number that
depends on which edition and which format you asked about, which region and
currency the storefront answered in, how many discounts are stacked into the figure
on screen, how much longer that figure has left before it reverts, and whether the
title has shipped at all. Key rows on the SKU, record what the page actually shows
instead of the math behind it, stamp every row with a read time, and keep physical,
digital and pre-order listings apart. Get that row shape right once and the same
function scrapes a single listing or sweeps a whole catalog across regions without
quietly merging products that were never the same row.

## Short answers to the questions that lead here

**Why does the same game show up with different prices on the same page?** It is
probably not one product. A base edition, a deluxe edition and a bundle can share a
title and still be three separate SKUs, each with its own price. Key rows on the SKU
or edition code, not the title text.

**Why did my price series jump for no reason?** Two likely causes: a countdown-driven
discount reverted on its schedule and the row has no timestamp to explain it, or two
different SKUs, such as a base edition and a bundle, were merged under one title-based
key and are taking turns writing to the same row.

**Should I try to work out each individual discount that applies?** No. Storefronts
show a final price and a total percent-off, not each layer that produced it.
Reconstructing sale, publisher and bundle discounts from one visible number is a
guess that breaks the moment the storefront changes its rounding or ordering. Record
what is shown.

**How do I compare a price scraped today against one from last week?** Only if both
carry the same region, currency and format, and both are stamped with a read time.
Without those fields a raw number is not comparable to anything.

**Does a locked pre-order price mean the game is on sale?** Not necessarily. A
pre-order price can be locked regardless of sales running before release, or
discounted specifically to reward early buyers. Record release status alongside
price rather than assuming a scraped title has already shipped.

**Are physical and digital versions the same row?** No. They are separate products
that move independently. Read format from the page, the same way you read edition,
and keep it as part of the row key.

## Sources

- Playwright's [`Locator`](https://playwright.dev/python/docs/api/class-locator) and
  [`get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute),
  used exactly as documented upstream to read SKU, edition, format and discount
  attributes off each card.
- This project's own configuration behaviour: the browser's timezone and locale are
  derived from the egress IP by default, which is what keeps a regional sweep's
  currency and locale consistent with the exit that produced the price.

**See also:** [tracking product prices](how-to-track-product-prices-playwright.md)
for the scheduling and identity side of a recurring price monitor,
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md) for
matching a proxy exit to a region in general, [cleaning scraped prices and
dates](how-to-clean-scraped-prices-and-dates-playwright.md) for turning a
locale-formatted price string into a typed number, and
[scraping deals and coupon codes](how-to-scrape-deals-and-coupon-codes-playwright.md)
for discounts that hide their code behind a reveal interaction.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of a
game price tracker keyed rows on title text alone, so a deluxe bundle's price
overwrote its base edition's row under the same title, and a rolling series reported
a price cut on the day the bundle's own launch discount expired, when the base
edition had not moved at all.*
