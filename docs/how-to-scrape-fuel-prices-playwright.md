---
title: "How to scrape fuel prices with Playwright"
description: "Scrape fuel prices with Playwright: set the location explicitly instead of letting the site infer one, keep the grade and the currency unit with every price, and record who reported the number and when."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 152
---


# How to scrape fuel prices with Playwright

To scrape fuel prices, set the location **explicitly** and keep three things with every
number: the grade, the unit, and who reported it. A fuel price page answers a question
about a place, so the page has to decide where you are before it can show you anything,
and if you do not decide for it the site decides from your exit IP.

That inference is the whole difficulty. A run through one proxy and a run through another
return different stations and different prices for the same site and the same minute,
and nothing in the captured row records which location produced it. The data looks
noisy. It is not noisy, it is unlabelled.

This page covers pinning the location, reading a price table that mixes grades and
units, and handling the fact that many of these prices are crowd-reported rather than
observed.

## Set the place before you read the price

Drive the site's own location control instead of relying on where it thinks you are:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/fuel-prices")

    box = page.wait_for_selector("input[name='location'], input#search-location")
    box.click()
    box.type("Bristol", delay=90)          # typed, not assigned
    page.keyboard.press("Enter")
    page.wait_for_selector(".station-list .station")
```

Type into the field rather than setting its value. Location inputs are usually
autocomplete widgets that only commit on a real key sequence, and a value written
directly leaves the widget's internal state empty, so the search runs against nothing.
The same pattern applies to any
[autocomplete or typeahead input](how-to-scrape-autocomplete-typeahead-playwright.md).

Where the site offers no control and geolocates silently, you are in the territory of
[geotargeted content](how-to-scrape-geotargeted-content-playwright.md), and the exit you
browse from becomes part of the query whether you like it or not. Record it either way:

```python
    row["queried_location"] = "Bristol"
```

A dataset of fuel prices without a location column is not recoverable later. Write the
column even when it feels redundant.

## Grade and unit belong to the number

Fuel prices come as a small matrix: several grades per station, each with a price, in a
unit that varies by country and sometimes by page. Read the matrix as a matrix:

```python
    stations = []
    for card in page.query_selector_all(".station-list .station"):
        prices = []
        for row in card.query_selector_all(".fuel-row"):
            prices.append({
                "grade": row.query_selector(".grade").inner_text().strip(),
                "price_text": row.query_selector(".price").inner_text().strip(),
            })
        stations.append({
            "name": card.query_selector(".name").inner_text().strip(),
            "address": card.query_selector(".address").inner_text().strip(),
            "prices": prices,
        })
```

Keep `price_text` unparsed at capture time. The string carries the unit and the currency,
and those are exactly what a naive float conversion throws away. Parse it in a second
step where the failure is visible:

```python
import re

MONEY = re.compile(r"(?P<sym>[$€£]|\b[A-Z]{3}\b)?\s*(?P<amount>\d+[.,]\d+)\s*(?P<per>/\s*\w+)?")

def parse_price(text):
    m = MONEY.search(text)
    if not m:
        return None
    return {
        "amount": float(m.group("amount").replace(",", ".")),
        "currency": m.group("sym"),
        "per": (m.group("per") or "").lstrip("/ ").strip() or None,
    }
```

The `per` group matters more than it looks. The same page can show a price per litre and
a price per gallon in different regions, and a series that silently mixes them produces a
chart with a step in it that no one can explain.

## Most of these prices are reports, not observations

A large share of fuel price sites are crowd-sourced. The number is what a visitor said
they paid, at a time they may not have recorded accurately. Sites usually show this next
to the price as an age, a username, or a confidence marker. Capture it:

```python
            "reported_age": row.query_selector(".age").inner_text().strip()
                            if row.query_selector(".age") else None,
```

A price reported two hours ago and a price reported nine days ago are not comparable
data points, and the difference is often the largest source of variance in the whole
dataset. Sites that publish operator-fed prices instead usually say so; record which kind
of source you are reading, per station, because a single site can carry both.

## Walking a region without hammering the search

To cover an area, iterate over places rather than paginating a single huge result set:

```python
    for place in ["Bristol", "Bath", "Weston-super-Mare"]:
        set_location(page, place)
        harvest(page)
        page.wait_for_timeout(3000)
```

This is slower than requesting a wide radius, and it is the version that works. Wide
radius queries on these sites are commonly capped at a fixed number of results with no
indication that anything was dropped, so a national sweep with three requests returns a
confident subset. If you do use radius, look for a total count in the response and check
it against the number of rows you received.

Keep the pace conservative and the run scheduled rather than continuous. Prices move a
few times a day at most, so a poll loop buys nothing and costs the goodwill described in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## Store it in long form

One row per station per grade per observation, with location, unit, source type and both
timestamps. It is more rows than a wide table with one column per grade, and it is the
shape that survives a site adding a grade, dropping one, or changing the unit in one
region. Writing it as you go, in
[a SQLite database](how-to-scrape-into-a-database-playwright.md) or
[JSON Lines](how-to-scrape-to-json-lines-playwright.md), also means an interrupted run
leaves usable data rather than nothing.
