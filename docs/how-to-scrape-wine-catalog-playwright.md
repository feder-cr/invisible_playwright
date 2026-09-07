---
title: "How to scrape wine and spirits catalogs with Playwright"
description: "Scrape wine and spirits catalogs with Playwright: key rows by name, vintage and bottle size, pass the age gate once per context, and normalize critic scores before averaging them."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 130
---


# How to scrape wine and spirits catalogs with Playwright

To scrape a wine and spirits catalog with Playwright, key every row by name, vintage
and bottle size together, not by name alone, treat the size selector the way
you would treat a variant selector on any e-commerce page and wait for its own price
and stock response, pass the age gate once per browser context instead of solving it
on every page, keep "NV" as a real vintage value instead of coercing it to null,
normalize each critic score to a common scale before averaging anything, and match
the proxy exit to the region the catalog is gating its assortment on, since that is a
different question from whether one item happens to be in stock.

A wine catalog reads like a product catalog with a label glued on, and that reading
throws away the one field the whole trade prices against. Two bottles under the
identical name, from the identical producer, in the identical region, are two
different products the moment their vintages differ: different stock, a different
price, sometimes a rating that exists for one year and not the other. A scraper that
groups rows by name alone has already merged data that the retailer, the critic and
the regulator all keep apart on purpose.

## The product key is name, vintage and bottle size, not name alone

A bottling's name is stable across years; almost nothing else is. The 2018 and the
2020 of the same wine can differ in price by a wide margin, sell out independently of
each other, and carry a rating that one vintage earned and the other never received.
Collapse them into a single row keyed on the name and you either overwrite one
vintage's price with the other's or silently keep whichever the page happened to
render last.

The fix is mechanical: carry vintage and size in the key from the first line of code
that reads a row, not as an afterthought added once a bug report shows two prices
merged into one.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class BottleKey:
    name: str
    producer: str
    vintage: str     # "2019", or "NV" - never coerced to a number here
    size_ml: int      # 375, 750, 1500 ...

def row_key(cell):
    return BottleKey(
        name=cell["name"].strip(),
        producer=cell.get("producer", "").strip(),
        vintage=(cell.get("vintage") or "NV").strip(),
        size_ml=int(cell.get("size_ml", 750)),
    )
```

Treat `BottleKey` as the unit you store, sort and deduplicate on. A dictionary keyed
only by name will happily let a later vintage clobber an earlier one during a naive
`rows[name] = data` write, and nothing in that line will tell you it happened.

## Bottle size is a variant selector, and only one size prices on load

Most catalog pages sell more than one bottle size under a single listing: a half
bottle, the standard 750ml, sometimes a magnum. That size choice behaves exactly like
a clothing size or a shoe width on a general retail page, a selector that swaps out
price and availability underneath the same product photo. The
[e-commerce variant pattern](how-to-scrape-ecommerce-product-pages-playwright.md)
applies here without modification: the price shown when the page first loads belongs
to whichever size the storefront defaulted to, and every other size only prices
itself once you select it and the request fires.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/456")

    sizes = page.locator("[data-size-option]")
    rows = []
    for i in range(sizes.count()):
        option = sizes.nth(i)
        label = option.get_attribute("data-size-option")   # "375ml", "750ml", "1.5L"

        with page.expect_response(
            lambda r: "price" in r.url or "availability" in r.url
        ) as caught:
            option.click()

        payload = caught.value.json()
        rows.append({"size_ml": label, "price": payload.get("price"),
                     "in_stock": payload.get("inStock")})
```

Read the page once and stop, and you have captured one size's price mislabeled as
"the" price for the whole listing. The magnum is not an accessory field on the
750ml row; it is its own row with its own price and its own stock count.

## ABV and vintage together decide whether a price can show at all

Some regions do not gate on age alone. A spirit above a certain proof, or a vintage
tied to a futures allocation, can trip a stricter legal path than an ordinary bottle
of the same name at a lower strength or a later release. The catalog reacts by
hiding the price entirely, showing a "contact to purchase" label, or refusing to
render the product page until a stronger age or residency check passes, and it can
do this for one SKU on a listing while a neighboring size or vintage of the same
product sails through untouched.

A missing price on one row and a normal price on the sibling row of the same
listing is not a broken selector. Check the ABV and vintage of the specific row
before assuming the scraper failed, because the site made a per-product legal
decision, not a general one.

## The age gate is answered once per browser context, not once per page

Passing the gate is a session fact, not a page fact. The confirmation writes a
cookie or a local storage flag to the browser context, and every later page opened
in that same context inherits it automatically. Re-running the click-through logic
on every catalog page you visit is wasted work at best, and at worst it looks like a
bot that never remembers anything between requests, which is its own kind of tell.

```python
GATE_COOKIE_NAMES = {"age_verified", "over21"}

def pass_age_gate_once(context, start_url):
    page = context.new_page()
    page.goto(start_url)

    gate = page.get_by_role("button", name="Yes, I am of legal age")
    if gate.count():
        gate.click()
        page.wait_for_load_state("networkidle")

    cookies = {c["name"] for c in context.cookies()}
    if not cookies & GATE_COOKIE_NAMES:
        raise RuntimeError("gate did not set a session cookie; check the selector")
    page.close()
```

Call this once, then open every remaining product page from `context.new_page()`
inside that same context and let the cookie ride along. The mechanics of reading and
seeding that cookie jar, and the caveat that a hand-set cookie can look unearned next
to one a real click produced, are covered in
[reading and setting cookies in a Playwright context](read-set-cookies-playwright-context.md).

## Ratings on one page use three incompatible scales

A single product page routinely stacks scores from several independent critics or
publications, and each one brought its own scale. A 100-point score, a 20-point
score from an older European tradition, and a star rating with half-step increments
can all sit in the same ratings block, describing the same bottle.

| Scale | Typical range | Example value |
|---|---|---|
| 100-point | 50-100 | 94 |
| 20-point | 0-20 | 17.5 |
| Star | 0-5, often half steps | 4.5 |

Average `94` and `4.5` directly and the star score looks like it is describing a
mediocre bottle next to an excellent one, when both actually mean roughly the same
thing on their own scale. The averaging step has to normalize first, and the
normalized value should sit next to the raw one rather than replace it, because a
reader who trusts one critic's scale specifically wants that original number kept.

```python
def normalize_score(value, scale):
    if scale == "100pt":
        return value
    if scale == "20pt":
        return (value / 20) * 100
    if scale == "5star":
        return (value / 5) * 100
    raise ValueError(f"unknown rating scale: {scale}")

def blended_score(ratings):
    # ratings: list of {"value": float, "scale": str, "critic": str}
    normalized = [normalize_score(r["value"], r["scale"]) for r in ratings]
    return sum(normalized) / len(normalized) if normalized else None
```

Store `ratings` as the list it is, not as a single averaged number thrown away after
the fact. The blended score is a convenience field for sorting, the per-critic list
is the data.

## Non-vintage is a value, not a missing one

"NV" on a sparkling wine or a blended spirit means the producer mixed several years
on purpose, and it is exactly as real a value as "2019" is. A parser that tries
`int(vintage)` and falls back to `None` on failure turns every NV bottle into a
missing value, and a missing value drops silently out of any sort or any filter that
expects a year.

```python
def vintage_sort_key(vintage):
    if vintage == "NV":
        return (0, 0)          # NV bottles sort together, deliberately first
    return (1, int(vintage))   # everything else sorts by year

rows.sort(key=lambda r: vintage_sort_key(r["vintage"]))
```

A filter for "vintage 2015 or later" needs the same explicit decision: does NV pass
that filter or not. Pick the rule once, in one place, rather than letting the answer
depend on whatever `int("NV")` happens to raise deep inside a sort call.
Locale-correct handling of the price and date fields sitting next to vintage follows
the same principle of not guessing at a format; see
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for the parsing side of that.

## Availability is geofenced, and that is a separate question from stock

A product being in or out of stock, covered already for variant pricing above, is
a different question from whether the catalog will show you the product at all. Wine
and spirits importers hold regional licenses, and a retailer's catalog can vary by
detected buyer location: some spirits are simply absent from the assortment outside
their licensed territory, independent of anything the stock field says. Querying
from an exit in the wrong region can produce a thinner catalog, a different set of
prices, or a bottle that appears to not exist at all, none of which is a parsing
bug.

Match the proxy exit to the region the catalog is meant for and let the timezone and
locale follow that exit instead of pinning them by hand. The full set of surfaces
that has to agree with the exit for a geofenced catalog, and why fixing the IP alone
makes the mismatch worse rather than better, is covered in
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md). The
day-to-day question of watching one item's price and stock over time, as distinct
from the assortment question here, belongs to
[tracking product prices](how-to-track-product-prices-playwright.md).

## Putting the row together

Every piece above lands in one function: the identity key, the size loop, the
vintage-specific price and stock, and the normalized score, combined into one row per
SKU rather than one row per listing.

```python
def build_rows(context, product_url):
    page = context.new_page()
    page.goto(product_url, wait_until="domcontentloaded")

    base = read_product_summary(page)              # name, producer, abv
    for size_row in read_size_variants(page):        # the loop from above
        for vintage_row in read_vintage_variants(page, size_row):
            yield {
                "name": base["name"],
                "producer": base["producer"],
                "vintage": vintage_row["vintage"],        # "NV" stays "NV"
                "size_ml": size_row["size_ml"],
                "price": vintage_row["price"],
                "in_stock": vintage_row["in_stock"],
                "score": blended_score(vintage_row["ratings"]),
                "ratings": vintage_row["ratings"],         # keep the raw scale list
            }
    page.close()
```

Every yielded dict is one vintage of one size of one product, which is the grain
buyers actually compare against. Anything coarser than that throws away the exact
dimension the catalog was built around.

## Conclusion

A wine and spirits catalog fails scrapers that treat it like a normal product grid,
because the field that actually varies, vintage, does not look like a variant to
someone skimming the page once. Key rows by name, vintage and size together, read the
size selector as the variant pattern it is, pass the age gate once per context
instead of on every request, keep NV as a value instead of a null, normalize scores
before you average them, and match your exit to the region the assortment is gated
on. Do all of that and the row you extract is the actual product a buyer would see,
not a name with the wrong year's price stapled to it.

## Short answers to the questions that lead here

**Why can't I group rows by wine name alone?** Because vintage changes the price,
the stock level and sometimes the rating, and two vintages under one name key will
overwrite each other the moment you write a row for the second one.

**Why does the price differ between two bottles that look identical on the page?**
They are usually different vintages or different bottle sizes rendered under the
same product photo. Check both fields before assuming a scraping error.

**Why did the age gate block the whole page instead of just hiding the price?**
ABV and vintage together can trigger a stricter legal path for a specific SKU, and
the site can choose to block the page entirely rather than partially disclose it.

**Do I need to solve the age gate on every product page?** No. It sets a cookie or a
local storage flag on the browser context, and every page opened afterward in that
same context inherits it. Solve it once per context, not once per request.

**Should I average a 92-point score with a 4.5-star score directly?** No. Normalize
both to the same scale first. Averaged as-is, the star rating reads as much weaker
than it actually is.

**What do I do with "NV" in the vintage field?** Keep it as the string "NV" and give
it an explicit place in your sort order and your filters. Coercing it to an integer
and catching the failure as a missing value drops those rows out of anything that
depends on vintage.

**Why does the catalog itself look different from another location, not just the
prices?** Availability is geofenced independent of stock. A licensed spirit in one
region can be entirely absent from the assortment in another, which is a proxy-exit
question, not a parsing question.

## Sources

- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  used as documented upstream to catch the variant and age-gate requests.
- Playwright's [`context.cookies()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies),
  which is how an age-gate confirmation is verified as a context-level fact rather
  than assumed from a page redirect.
- This project's own configuration behaviour: locale, timezone and number format
  follow the proxy exit by default, which is what keeps a geofenced catalog's
  surfaces in agreement without hand-pinning any of them.

**See also:** [scraping e-commerce product pages](how-to-scrape-ecommerce-product-pages-playwright.md)
for the variant XHR pattern bottle size reuses,
[reading and setting cookies in a Playwright context](read-set-cookies-playwright-context.md)
for the age-gate session mechanics,
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md) for
the surfaces that must agree with a region-locked exit, and
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for parsing the fields that sit next to vintage on the same row.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The row key on an
early version of this pattern was name and producer alone, and it ran for months
before anyone noticed a later vintage had been silently overwriting an earlier
one's price on every bottling that got re-released.*
