---
title: "How to scrape restaurant menu data with Playwright"
description: "Scrape restaurant menu data with Playwright: read JSON-LD first, fall back to the View menu tab XHR, and flatten category, size and variant prices into rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 43
---


# How to scrape restaurant menu data with Playwright

To scrape restaurant menu data with Playwright, read the structured tree the page
already ships rather than the rendered cards: parse the JSON-LD `Restaurant` node and
its `hasMenu` field first, fall back to the "View menu" tab's XHR when the menu is not in
the initial HTML, flatten categories, sizes and variants into one row per price, and match
the proxy exit to the city so the menu you extract is the local one.

Menu data looks simple on the page and is anything but underneath. What renders as a
tidy column of dishes is, in the data model, a tree: items grouped under categories,
each item carrying its own size and variant rows with separate prices, plus an
opening-hours block off to the side, plus a menu that often is not even in the document
until you click a "View menu" tab and it fetches itself over XHR.

Parse the rendered cards and you inherit every one of those problems at once. This page
takes the other route: read the clean tree the page already ships as JSON-LD, fall back
to the tab request when it does not, flatten the result into rows, and get the session
geography right so the menu you extract is the one a local would see.

## Where the clean data actually lives

Before writing a single selector, open the page and look at what the server sent, not
what the browser drew. Most restaurant and aggregator pages embed a
`application/ld+json` block describing the venue and its menu in schema.org terms. That
block is the same tree the visible cards are rendered from, minus the styling, the lazy
loading and the markup churn. It is the difference between parsing a data structure and
scraping a layout.

```python
import json
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/venue/123", wait_until="networkidle")

    blocks = []
    for handle in page.query_selector_all('script[type="application/ld+json"]'):
        raw = handle.text_content()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blocks.extend(data if isinstance(data, list) else [data])

    # a page can wrap several types in one @graph array
    nodes = []
    for b in blocks:
        nodes.extend(b.get("@graph", [b]) if isinstance(b, dict) else [])
```

You now have a list of typed nodes. The one you want is the `Restaurant` (or
`FoodEstablishment`) node, and the menu hangs off its `hasMenu` field. The styled cards
in the visible DOM are frequently rendered inside web components, so if you do end up
reading the DOM, know that Playwright locators reach into open shadow roots for you and
you do not need a special traversal helper - the mechanics are in
[scraping shadow DOM content](how-to-scrape-shadow-dom-playwright.md).

## Walk the menu tree from JSON-LD

A schema.org `Menu` is three levels deep: the menu holds `hasMenuSection` (the
categories), each section holds `hasMenuItem` (the dishes), and each item can hold its
own `offers` (the prices) and, for sizes and variants, either multiple offers or a
nested section of its own. Any of those fields can be a single object or a list, so
normalise as you descend.

```python
def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

def find_restaurant(nodes):
    wanted = {"Restaurant", "FoodEstablishment", "CafeOrCoffeeShop"}
    for node in nodes:
        types = as_list(node.get("@type"))
        if wanted.intersection(types):
            return node
    return None

def iter_items(menu):
    for section in as_list(menu.get("hasMenuSection")):
        category = section.get("name", "")
        for item in as_list(section.get("hasMenuItem")):
            yield category, item
        # some feeds nest a section inside an item for sizes; recurse
        for sub in as_list(section.get("hasMenuSection")):
            for item in as_list(sub.get("hasMenuItem")):
                yield f"{category} / {sub.get('name', '')}", item
```

The recursion matters. A pizza with small, medium and large is often modelled as a
menu item that owns a nested section, one entry per size, each with its own offer. Flat
iteration silently drops the sizes and keeps only the headline price.

## Flatten categories, sizes and variants into rows

A tree is awkward to store and query; a table is not. Emit one row per priced variant so
that "Margherita, large, 12.50" and "Margherita, small, 8.00" are two rows sharing a
name and a category. Pull the currency and amount from the offer, and keep the item
description when it is present.

```python
def offer_rows(category, item):
    name = item.get("name", "")
    description = item.get("description", "")
    offers = as_list(item.get("offers"))
    if not offers:
        yield {"category": category, "item": name, "variant": "",
               "price": None, "currency": "", "description": description}
        return
    for offer in offers:
        yield {
            "category": category,
            "item": name,
            "variant": offer.get("name", ""),      # size or variant label, if any
            "price": offer.get("price"),
            "currency": offer.get("priceCurrency", ""),
            "description": description,
        }

def menu_to_rows(nodes):
    restaurant = find_restaurant(nodes)
    if not restaurant:
        return []
    rows = []
    for menu in as_list(restaurant.get("hasMenu")):
        for category, item in iter_items(menu):
            rows.extend(offer_rows(category, item))
    return rows
```

`menu_to_rows(nodes)` gives you a flat list of dicts you can hand straight to a CSV
writer or a dataframe. Every price is a row, every size is its own row, and the category
travels with each one.

## When the menu is not in the document: the tab XHR

Plenty of pages ship the venue node in JSON-LD but leave the menu itself out of the
initial HTML. It arrives only when the visitor clicks a "View menu" tab, which fires an
XHR and paints the cards from the response. If `hasMenu` is empty, that request is your
source, and it is usually cleaner JSON than anything you could scrape from the rendered
result.

Wait for the response by URL pattern while you trigger the click, then parse its body.
Capturing the API call directly rather than the repainted DOM is a general technique
worth knowing on its own - [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
covers the response and routing hooks in full.

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/venue/123")

    with page.expect_response(
        lambda r: "menu" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.get_by_role("tab", name="View menu").click()

    response = caught.value
    payload = response.json()
    # payload is the venue's own menu schema; map its sections and
    # items into the same offer_rows() shape as the JSON-LD path
```

Keep both paths in the same scraper and prefer JSON-LD when it is populated: it needs no
interaction, so it is faster and it cannot be broken by a renamed tab label. The XHR path
is the fallback, and having it means one code base handles both kinds of venue.

## Match the session geography to the city, or the menu is wrong

Here is the honest caveat, and it is not a fingerprint problem, so no amount of stealth
fixes it. Local restaurant aggregators geofence their content. The menu, the prices, even
whether a venue appears at all, are decided from where the request looks like it comes
from. Query a city from an exit in the wrong region and you get an empty menu, a
different-region menu, or a soft block, all of which look like a bug in your parser and
are not.

So the proxy exit has to sit in, or near, the city you are querying, and the rest of the
session has to agree with that exit. By default the browser timezone is derived from the
egress IP, and the locale and number format follow, so a geo-matched proxy carries most
of this for you. The failure mode is pinning a value by hand that then disagrees with the
exit - a locale from your own machine, a timezone you hardcoded - which is exactly the
[timezone and locale mismatch](timezone-proxy-mismatch.md) a cross-check looks for. The
broader "get every geo surface to agree with the exit" method is
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md).

```python
# let the geo surfaces follow the exit; do not pin them by hand
proxy = {"server": "socks5://city-exit.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    # timezone, locale and number format are derived from this exit's IP
    ...
```

## Sweeping a city without looking like enumeration

A menu dataset is rarely one venue; it is every venue in a city, which is a request
pattern an aggregator is built to notice. The tell is not any single page - it is the
same identity fetching a hundred listings in a row, fast, from one address, when a real
person opens two or three. Aggregators rate-limit on identity across a city's worth of
venues precisely to catch a datacenter walking the list.

A seed-stable fingerprint plus a geo-matched exit is what keeps a many-venue sweep
reading like one local diner browsing rather than a machine enumerating the directory.
The same `seed=42` gives the same GPU, canvas, audio and font profile on every venue in
the run, so the identity is consistent across the sweep instead of flickering into a new
machine on each page. Then pace the requests like a person and reuse the page.

```python
venue_ids = load_city_venue_ids()   # your own list

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    all_rows = []
    for vid in venue_ids:
        page.goto(f"https://example.com/venue/{vid}", wait_until="networkidle")
        nodes = read_ld_json(page)          # the loop from the first section
        rows = menu_to_rows(nodes)
        if not rows:
            rows = rows_from_tab_xhr(page)   # the fallback from above
        all_rows.extend(rows)
        page.wait_for_timeout(random_delay_ms())   # space the visits out
```

Consistency is doing the work here: not a new disguise per venue, but one believable
local visitor that stays the same across the whole city. If you need to hold specific
fields steady while letting the rest stay seed-derived, that is what
[pinning fingerprint fields](pinning.md) is for.

## Conclusion

Restaurant menus are a tree wearing the costume of a list. Read the tree the page already
ships - JSON-LD first, the "View menu" tab XHR when the document does not carry it - and
flatten category, size and variant prices into rows so the awkward nesting becomes a
table you can store. Then get the geography right, because the content is gated on where
the session appears to be, and hold one seed-stable identity across the whole sweep so a
city-wide pass looks like a local browsing rather than a datacenter enumerating. The
parsing is the easy half; the session realness is what decides whether the menu you
extracted is the real one.

## Short answers to the questions that lead here

**Where is the cleanest menu data on a restaurant page?** Usually in a
`application/ld+json` script tag, as a schema.org `Restaurant` node with the menu under
`hasMenu`. It is the same tree the visible cards render from, without the markup.

**The menu is empty in the HTML. Where did it go?** It probably loads only when you click
a "View menu" tab, which fires an XHR. Wait for that response and parse its JSON instead
of the repainted DOM.

**How do I keep sizes and prices together?** Emit one row per offer. A dish with three
sizes becomes three rows sharing a name and category, each with its own variant label and
price. Recurse into nested sections so sizes are not dropped.

**I get an empty or wrong menu for a city. Why?** The content is geofenced. Your proxy
exit is in the wrong region, so the site served a different-region menu or none. Move the
exit to the city you are querying and let the timezone and locale follow it.

**Will one fingerprint get me through a whole city of listings?** A seed-stable identity
plus a geo-matched exit keeps a many-venue sweep looking like one local visitor rather
than an enumerator, but you still have to pace the requests. Consistency plus rhythm, not
a new disguise per page.

**Should I set the locale by hand to the city's language?** No. Let it follow the exit
IP. A hand-pinned locale that disagrees with the exit is the mismatch a cross-check is
looking for.

## Sources

- schema.org `Menu`, `MenuSection`, `MenuItem` and `Offer` types, which are the shape the
  JSON-LD path above walks.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role) and
  `query_selector_all`, used exactly as documented upstream - the browser returned by this
  library is a real Playwright `Browser`.
- This project's own configuration behaviour: the browser timezone is derived from the
  egress IP by default, which is what makes a geo-matched proxy carry the locale surfaces
  for you.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the tab-request fallback, [scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md)
for the geo surfaces that must agree with the exit, and
[scraping shadow DOM content](how-to-scrape-shadow-dom-playwright.md) for the styled cards
when you do have to read the rendered version.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The geo-gating caveat is
the one that costs people a day: they debug the parser when the exit was in the wrong
city the whole time.*
