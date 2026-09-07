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

## A complete pass over several towns

```python
import json, re, time
from invisible_playwright import InvisiblePlaywright

PLACES = ["Bristol", "Bath", "Weston-super-Mare"]

def set_location(page, place):
    box = page.wait_for_selector("input[name='location'], input#search-location")
    box.click()
    box.fill("")
    box.type(place, delay=90)
    page.keyboard.press("Enter")
    page.wait_for_selector(".station-list .station, .no-results")

def harvest(page, place):
    if page.query_selector(".no-results"):
        return []
    rows = []
    for card in page.query_selector_all(".station-list .station"):
        name = card.query_selector(".name").inner_text().strip()
        address = card.query_selector(".address").inner_text().strip()
        for fuel in card.query_selector_all(".fuel-row"):
            price_text = fuel.query_selector(".price").inner_text().strip()
            age = fuel.query_selector(".age")
            rows.append({
                "queried_location": place,
                "station": name,
                "address": address,
                "grade": fuel.query_selector(".grade").inner_text().strip(),
                "price_text": price_text,
                "price": parse_price(price_text),
                "reported_age": age.inner_text().strip() if age else None,
                "observed_at": time.time(),
            })
    return rows

with InvisiblePlaywright(seed=42) as browser, open("fuel.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    page.goto("https://example.com/fuel-prices")
    for place in PLACES:
        set_location(page, place)
        for row in harvest(page, place):
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(4000)
```

The `box.fill("")` before typing is the line that stops the second town from being
searched as "BristolBath". Autocomplete widgets keep their previous value and their
internal selection, and clearing the field is what resets both.

## Why fuel sites are unusually defensive

These sites carry commercially valuable data that competitors want in bulk, so they tend
to be better defended than their size suggests, and the defence is usually aimed at the
request rather than the person. Three consequences shape the design above.

**A side HTTP client rarely gets the list.** The search result is fetched by the page after
the location commits, from an endpoint that expects the browser's context. Driving the
real control in a real browser is what keeps the response coming, which is why the code
types rather than building a URL.

**Degradation looks like an empty town.** When the protection is unhappy the list renders
with zero cards and no error. That is indistinguishable from a town with no stations
unless you check the negative case explicitly, which is what the `.no-results` branch is
for. Treat "cards absent and no-results absent" as a failed read rather than an empty one:

```python
    if not page.query_selector(".station-list .station") and not page.query_selector(".no-results"):
        raise RuntimeError("neither stations nor a no-results marker: failed read")
```

**Your exit becomes part of the query.** Even with a location typed in, some sites blend
the inferred position into ranking or availability. Pin the exit for a series rather than
letting it rotate, or the same town will return different stations on different days for
reasons that have nothing to do with fuel. The general form is in
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md), and the
debug order when a read degrades is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## Turning reports into a series you can trust

Crowd-reported prices need one more step before they are comparable: a freshness filter
applied at read time, not at capture.

```python
def usable(row, max_age_hours=48):
    age = row.get("reported_age") or ""
    m = re.search(r"(\d+)\s*(hour|day|week)", age, re.I)
    if not m:
        return False                      # unknown age: not comparable
    n, unit = int(m.group(1)), m.group(2).lower()
    hours = n * {"hour": 1, "day": 24, "week": 168}[unit]
    return hours <= max_age_hours
```

Filtering at read time keeps the stale rows in the archive, where they still answer
questions about reporting behaviour, while keeping them out of a price average that would
otherwise be dominated by whichever station has the most enthusiastic reporter.

The series is then one row per station, grade and observation, with `queried_location`,
`unit`, `currency`, `reported_age` and `observed_at`. That is enough to answer where fuel
is cheapest today, how fast a price change propagates across a chain, and how stale a
given site's data really is, which is the question the site itself will never answer.

## Short answers to the questions that lead here

**Why do two runs return different stations for the same site?** Because the page
decides where you are before it shows you anything, and with no explicit location it
decides from the exit IP. The data is not noisy, it is unlabelled: write the queried
location into every row and the difference becomes readable.

**Why does typing into the location box work when setting the value does not?**
Location fields are usually autocomplete widgets that only commit on a real key
sequence. A value written straight into the element leaves the widget's internal state
empty, so the search runs against nothing.

**Should I convert the price to a float at capture time?** No. The string carries the
currency and the unit, and those are exactly what a float conversion throws away. A
page can show a price per litre and a price per gallon in different regions, and a
series that mixes them has a step nobody can explain.

**How reliable are these prices?** Many fuel price sites are crowd-sourced, so the
number is what a visitor said they paid. Capture the reported age next to it: a price
from two hours ago and one from nine days ago are not comparable, and that gap is
often the largest source of variance you have.

**See also:** [Scrape autocomplete and typeahead inputs with
Playwright](how-to-scrape-autocomplete-typeahead-playwright.md), [How to scrape
geotargeted content with Playwright](how-to-scrape-geotargeted-content-playwright.md),
[How to rate limit your own Playwright
scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Input, https://playwright.dev/python/docs/input - `locator.fill()`,
  which focuses the element and fires an input event, and `press_sequentially()`,
  which types character by character with an optional delay. Checked for why an
  autocomplete commits on typing and not on an assigned value.
- This project's pages on autocomplete inputs and on geotargeted content, which carry
  the two mechanics this page depends on.
