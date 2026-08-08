---
title: "How to extract JSON-LD structured data with Playwright"
description: "Parse JSON-LD and @graph structured data with Playwright instead of fragile DOM selectors, filter by @type, and read an empty ld+json result as a blocked page."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 57
---


# How to extract JSON-LD structured data with Playwright

To extract JSON-LD structured data with Playwright, select every
`script[type='application/ld+json']` block, read the text of each with
`all_text_contents()`, and `json.loads` it; then flatten any `@graph` array and filter
by `@type` instead of trusting document order. It is less fragile than chasing CSS
selectors, and an empty result is a useful signal that you were handed a blocked or
simplified page rather than the real one.

Most scraping guides teach you to chase CSS selectors down through a layout that
changes the week after you ship. There is often a cleaner record sitting on the same
page, already structured, already typed, put there by the site itself for search
engines to read. It is JSON-LD, and parsing it is both less fragile and, as it turns
out, a better honesty check on whether you got the real page at all.

This page is how to pull every JSON-LD block off a page with Playwright, how to handle
the `@graph` wrapper and multiple types, and why an empty result should stop your
pipeline rather than be silently skipped.

## Why JSON-LD beats scraping the DOM

JSON-LD beats DOM scraping because you parse a stable, typed JSON contract instead of
walking markup that re-renders without warning. Many pages ship the clean record you
actually want inside one or more `<script type="application/ld+json">` blocks: a JSON
object describing the page as structured data, such as a product with its price and
availability, an article with its author and publish date, or an event with its start
time. The site emits it so that search and social crawlers can read the page without
guessing, which means you get to read it the same way.

The difference in practice:

| | DOM scraping | JSON-LD extraction |
|---|---|---|
| What you read | CSS/XPath selectors over rendered markup | A typed JSON object the site publishes |
| Breaks when | A class renames, a `div` moves, a component re-renders | The site changes its published schema (rare, documented) |
| Shape | Arbitrary, per-site layout | `@type` nodes, sometimes wrapped in `@graph` |
| Empty result means | The layout probably moved | You likely did not get the real page |

A class name changes, a wrapper `div` moves, a component re-renders with different
attributes, and your selector-based extractor breaks. The JSON-LD block is a contract
with search engines, so it changes far less often and, when it does, it changes in a
documented shape rather than an arbitrary one.

The one thing to know before you start: a page can carry several blocks of different
`@type`, and they can be nested inside a `@graph` array. You cannot take the first
block and assume it is yours. You filter by type.

## Extract every ld+json block with Playwright

To pull every JSON-LD block, select `script[type='application/ld+json']`, read all
matches in one call with
[`all_text_contents()`](https://playwright.dev/python/docs/api/class-locator#locator-all-text-contents),
and `json.loads` each block, skipping any that will not parse. Switching from plain
Playwright is a two-line change, and
every standard method still works, because the object you get back is a real Playwright
`Browser`:

```python
import json
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/some-item")

    # Every JSON-LD block on the page, in document order.
    raw_blocks = page.locator(
        "script[type='application/ld+json']"
    ).all_text_contents()

    records = []
    for raw in raw_blocks:
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            # A block that will not parse is worth logging, not swallowing:
            # it is often a trailing comma or an HTML comment left in the tag.
            continue

    print(f"parsed {len(records)} JSON-LD block(s)")
```

`all_text_contents()` returns the text of every matching `<script>` tag, so you get
all of them in one call rather than looping locators by hand. `json.loads` turns each
into a Python object. The `seed=42` gives you a reproducible identity so that if one
run gets the block and another does not, you can replay the exact same browser and
find out why rather than blaming the random draw.

## Filter by @type, and walk the @graph

Two shapes cover almost everything you will meet. Either the page has several separate
`<script>` tags, each a single typed object, or it has one tag holding a `@graph`
array with many typed objects inside it. A robust extractor flattens both into one
list of typed nodes and then picks the type it wants:

```python
def flatten_ld(records):
    """Yield every typed node from a list of parsed JSON-LD blocks,
    expanding any @graph array into its members."""
    for record in records:
        if isinstance(record, list):
            yield from flatten_ld(record)
        elif isinstance(record, dict):
            if "@graph" in record and isinstance(record["@graph"], list):
                yield from record["@graph"]
            else:
                yield record


def has_type(node, wanted):
    """@type can be a string or a list of strings; match either."""
    node_type = node.get("@type", "")
    if isinstance(node_type, list):
        return wanted in node_type
    return node_type == wanted


nodes = list(flatten_ld(records))
products = [n for n in nodes if has_type(n, "Product")]

if products:
    item = products[0]
    print("name:", item.get("name"))
    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    print("price:", offers.get("price"), offers.get("priceCurrency"))
    print("availability:", offers.get("availability"))
```

Note the two small traps this handles. `@type` is sometimes a bare string and
sometimes a list, so you check both. `offers` is sometimes a single object and
sometimes an array, so you normalise before reading it. Filtering by type is what
keeps you from grabbing a `BreadcrumbList` or an `Organization` node that happens to
sit first in the markup.

## An empty extraction is a detection signal, not a missing field

An empty JSON-LD result usually means you were served a challenge or simplified page,
not that a field moved. The structured block is emitted only for the full, indexable
page, so its absence is a detection signal worth stopping on rather than skipping past.
That is also the reason this technique pairs so well with a browser that renders like a
real one.

The JSON-LD is emitted for the full, real page. A challenge page, an interstitial, or
a stripped-down variant served to something a site thinks is automated does not carry
that structured data. It has no reason to: it is not trying to be indexed, it is
trying to make you go away. So the markup comes back with **no `ld+json` block at
all**.

This flips the usual meaning of an empty result. If your extractor returns zero
`Product` nodes, the ordinary instinct is "the layout must have changed, let me fix my
selector". But you were not using a selector, you were reading a block that the real
page always ships. Zero blocks does not mean the field moved. It means you were very
likely handed a page that is not the real one.

So assert presence, the same discipline that
[the guide to testing bot detection](how-to-test-bot-detection.md) argues for on
every surface: a page that comes back empty or blocked is a failure, not a pass. In
code that is one guard:

```python
if not nodes:
    raise RuntimeError(
        "no JSON-LD on page: likely a challenge or simplified variant, "
        "not a layout change"
    )
```

A negative-only check ("did the request 200?") passes on a challenge page that returns
a perfectly healthy 200 with none of the content you wanted. Reading the presence of
the structured block is a positive-realness check: the record either rendered or it
did not, and if it did not, the fix is upstream of your parser. This is the same
reasoning behind [scraping without getting blocked](how-to-scrape-without-getting-blocked.md),
applied to the one artifact that is only present on the genuine page.

## Make the block render: a real browser is the prerequisite

If an empty JSON-LD result signals a blocked page, then the way to keep the block
present is to not get the simplified variant in the first place. That is a
fingerprint-consistency problem, not a parsing one.

A real browser with a coherent fingerprint is precisely what makes the site serve the
full page, JSON-LD and all. Every session here derives its GPU, audio, fonts and
screen from one seed, so the machine that requests the page looks like a machine that
exists, and the values agree with each other rather than contradicting the way a
half-spoofed browser does. When we run our own canonical extraction set through the
patched engine, the full page with its structured data comes back; a stock automation
build on the same network, same address, gets the interstitial and the empty markup on
a measurable share of the same URLs. The delta is not in the parser. It is in which
page the site decided to hand over.

That is the honest caveat to state plainly: this technique is only as good as your
ability to get the real page. Parsing is the easy half. The block renders because the
browser looked real enough to be trusted with it, and if you strip that away the
cleanest JSON-LD parser in the world has nothing to parse. Everything on the
[bot detection testing](playwright-detected-as-bot.md) checklist that keeps you on the
full page applies before a single line of this extractor runs.

## Conclusion

Parse the structured record, do not scrape the layout. Pull every
`script[type='application/ld+json']` block, flatten any `@graph`, and filter by
`@type` rather than trusting document order. Then treat an empty result as what it
usually is: not a moved field, but a page that was never the real one, which is a
signal your pipeline should act on rather than skip. The parsing is a dozen lines. The
part that makes those lines return data is a browser that renders like a real one.

## Short answers to the questions that lead here

**How do I get JSON-LD out of a page with Playwright?** Select every
`script[type='application/ld+json']` tag, read its text with `all_text_contents()`,
and `json.loads` each block. The object you get is standard Playwright, so this is
ordinary code.

**Why parse JSON-LD instead of scraping the DOM?** The block is a contract with search
crawlers, so it changes far less than the visual layout, and you parse structured JSON
instead of walking fragile selectors.

**There are several JSON-LD blocks, which one is mine?** Filter by `@type`, do not take
the first. A page commonly ships a `BreadcrumbList`, an `Organization` and the record
you actually want, in any order.

**What is @graph in JSON-LD?** A single block holding an array of typed nodes under a
`@graph` key. Flatten it into your node list and filter by type exactly as you would
separate blocks.

**My extraction came back empty, is my selector wrong?** Probably not, because you were
not using a layout selector. An empty `ld+json` result usually means you got a
challenge or simplified page that does not carry structured data, so look upstream at
whether you got the real page.

**Why does the block render for a real browser but not my automation?** Sites serve the
full, indexable page to sessions they trust and a stripped variant to ones they do
not. A consistent fingerprint is what keeps you on the version that carries the JSON-LD.

## Sources

- [The JSON-LD structured data format](https://www.w3.org/TR/json-ld11/), as
  documented for search crawlers, including the `@graph`, `@type` and typed-node
  conventions this page parses.
- This project's canonical extraction set, run through the patched engine and a stock
  automation build on the same address, where the empty-markup difference described
  above was measured.

**See also:** [how to scrape HTML tables with Playwright](how-to-scrape-html-tables-playwright.md)
for the other structured-source technique, [scraping e-commerce product pages](how-to-scrape-ecommerce-product-pages-playwright.md)
where JSON-LD `Product` nodes are common, and [scraping without getting blocked](how-to-scrape-without-getting-blocked.md)
for keeping the real page in front of your parser.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-block-as-signal
rule came from a run that "succeeded" on every URL and returned nothing.*
