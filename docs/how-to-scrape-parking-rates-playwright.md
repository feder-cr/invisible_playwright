---
title: "How to scrape parking rates with Playwright"
description: "Scrape parking garage rates with Playwright: read the tariff table as duration bands rather than prices, resolve the band an arrival time falls into, and keep the rules that override the table."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 150
---


# How to scrape parking rates with Playwright

To scrape parking rates, capture the tariff **table** rather than a price: a garage does
not have "a rate", it has duration bands whose boundaries move by day of week and time
of arrival. Read every row as a triple of lower bound, upper bound and amount, record
the day and time window each table applies to, and keep the override rules printed
underneath it, because those are what decide the final amount more often than the rows
above them.

The mistake that produces unusable data is scraping the number that the page shows you.
A garage page usually renders one headline figure, chosen for a default stay that the
site picked, and that figure changes if you arrive an hour later. Two runs a day apart
then disagree, and nothing in the captured row explains why.

This page covers reading the band table without flattening it, handling the arrival
time the page silently assumed, and the overrides that sit in small print and quietly
outrank everything else.

## Read the bands, not the headline price

Capture every row of the tariff table with its bounds intact. The unit of data is the
band, and a band without its bounds is a number with no meaning:

```python
import re
from invisible_playwright import InvisiblePlaywright

DURATION = re.compile(
    r"(?P<lo>\d+(?:\.\d+)?)\s*(?P<lo_unit>min|minutes|hour|hours|hr|day|days)?"
    r"\s*(?:to|-|and)\s*"
    r"(?P<hi>\d+(?:\.\d+)?)\s*(?P<hi_unit>min|minutes|hour|hours|hr|day|days)",
    re.I,
)

def to_minutes(value, unit):
    value = float(value)
    unit = (unit or "hour").lower()
    if unit.startswith("min"):
        return int(value)
    if unit.startswith(("hour", "hr")):
        return int(value * 60)
    return int(value * 60 * 24)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/garages/central/rates")

    bands = []
    for row in page.query_selector_all("table.rates tbody tr"):
        cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
        if len(cells) < 2:
            continue
        m = DURATION.search(cells[0])
        if not m:
            continue          # a footnote row, not a band
        bands.append({
            "from_minutes": to_minutes(m.group("lo"), m.group("lo_unit") or m.group("hi_unit")),
            "to_minutes": to_minutes(m.group("hi"), m.group("hi_unit")),
            "label": cells[0],
            "amount": cells[1],
        })
```

Keep `label` alongside the parsed bounds. When a row parses in a way that later looks
wrong, the original text is the only thing that lets you tell a bad regex from a strange
tariff, and garages write these strings in every format there is.

## The table is scoped to a day and a time, and the page rarely says so twice

The same garage usually publishes several tables: weekday, weekend, event day, overnight.
The page shows one at a time and switches between them with a control that does not
change the URL. Scraping the visible table without recording which one it is produces
rows that contradict each other for no visible reason.

Record the scope with the bands, and drive the control to collect the others:

```python
    tariffs = []
    for tab in page.query_selector_all("[role='tab'], .rate-tabs button"):
        scope = tab.inner_text().strip()        # "Weekday", "Weekend", "Event"
        tab.click()
        page.wait_for_selector("table.rates tbody tr")
        tariffs.append({"scope": scope, "bands": read_bands(page)})
```

Click the control rather than fetching a variant URL you guessed. The tab often triggers
a request whose response the page merges into the table, and driving the real control is
also the version that behaves like a visitor. The same reasoning applies to any
[tabbed or accordion content](how-to-scrape-accordion-and-tab-content-playwright.md), where
the hidden panels are frequently not in the DOM until something opens them.

## Resolve an arrival time instead of storing a spread

Once you have bands, a price is a function of two inputs: when the car arrives and how
long it stays. Store the function, and compute prices at read time:

```python
def price_for(tariff, arrival, minutes):
    """arrival is a datetime, minutes an int. Returns the matching band."""
    scope = "Weekend" if arrival.weekday() >= 5 else "Weekday"
    table = next(t for t in tariff if t["scope"] == scope)
    for band in table["bands"]:
        if band["from_minutes"] <= minutes <= band["to_minutes"]:
            return band
    return table["bands"][-1]      # over the top band: the daily maximum applies
```

The fallback on the last line is not a detail. Most tariffs end with a maximum that
covers everything beyond the final band, and a scraper that returns nothing for a
fourteen hour stay is reporting an absence that the garage does not have.

## The small print outranks the table

Underneath the table sit the rules that decide the real amount: a daily maximum, a
minimum charge, a grace period, a different rate after midnight, an early-bird rate that
requires arriving before a cutoff. These are prose, not rows, and they routinely
contradict the table above them.

Capture them verbatim rather than trying to parse them:

```python
    notes = [n.inner_text().strip()
             for n in page.query_selector_all(".rate-notes li, .rates-footnote")]
```

Verbatim text is the honest form here. A parser that turns "maximum $28 per 24 hours,
in and out privileges not included" into a number throws away the second clause, and the
second clause is the one that makes the number wrong. Store the strings, resolve them
when a human asks a question that needs them.

## Two practical traps

**The price appears after a date picker is filled.** Many operators only show rates once
you enter arrival and departure. That is a form to drive, not a page to read, and it
behaves like any other
[multi-step wizard flow](how-to-scrape-multi-step-wizard-flow-playwright.md): fill it in
order, wait for the result region, then read.

**Rates are location-scoped even on the same site.** A chain publishes the same page
shell for every garage and fills the numbers per location, sometimes from your inferred
position rather than the URL. If the figures change when the exit IP changes, the page is
reading a location you did not set, and the fix is the one in
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md).

## Collect politely, because these pages are cheap to overload

Parking operators run small sites. A run that walks every garage in a city at full speed
is a visible load spike on infrastructure that was not built for it, and the block that
follows is deserved rather than adversarial. Pace the run with
[a rate limit you impose on yourself](how-to-rate-limit-your-scraper-playwright.md), and
prefer one pass per day over a poll: tariffs change on the scale of months.

The result is a table of bands, plus scopes, plus notes. It is more data than a single
price, and it is the only form that still means the same thing tomorrow.

## A complete run, one garage to storage

Putting the pieces together, with the parts that matter in production: a browser that
looks like a browser, a scope loop, and a write that happens per garage rather than at the
end.

```python
import json, re, time
from invisible_playwright import InvisiblePlaywright

def read_bands(page):
    bands = []
    for row in page.query_selector_all("table.rates tbody tr"):
        cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
        if len(cells) < 2:
            continue
        m = DURATION.search(cells[0])
        if not m:
            continue
        bands.append({
            "from_minutes": to_minutes(m.group("lo"), m.group("lo_unit") or m.group("hi_unit")),
            "to_minutes": to_minutes(m.group("hi"), m.group("hi_unit")),
            "label": cells[0],
            "amount": cells[1],
        })
    return bands

def scrape_garage(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector("table.rates tbody tr, .rates-unavailable")
    if page.query_selector(".rates-unavailable"):
        return {"url": url, "observed_at": time.time(), "tariffs": [], "note": "no published rates"}

    tabs = page.query_selector_all("[role='tab'], .rate-tabs button")
    tariffs = []
    if not tabs:
        tariffs.append({"scope": "default", "bands": read_bands(page)})
    else:
        for tab in tabs:
            scope = tab.inner_text().strip()
            tab.click()
            page.wait_for_selector("table.rates tbody tr")
            tariffs.append({"scope": scope, "bands": read_bands(page)})

    return {
        "url": url,
        "observed_at": time.time(),
        "tariffs": tariffs,
        "notes": [n.inner_text().strip()
                  for n in page.query_selector_all(".rate-notes li, .rates-footnote")],
    }

with InvisiblePlaywright(seed=42) as browser, open("garages.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    for url in garage_urls:
        record = scrape_garage(page, url)
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(3000)
```

The `flush` on every garage is deliberate. A run over a city is long enough to be
interrupted, and a buffered file that dies with the process loses an hour of polite
requests you will have to make again. The wider version of that argument is in
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md).

The `seed=42` is what makes two runs comparable. A fixed seed pins the browser identity,
so a difference between Monday's rates and Tuesday's is a difference in the tariff rather
than in which visitor the site thought it was serving.

## Why parking sites block, and what actually helps

Parking operators sit behind commodity edge protection more often than their size
suggests, because their booking flows are a target for card testing. That has two
consequences for a rate scraper.

The first is that a plain HTTP client is usually refused before it sees a tariff table at
all, which is why this page drives a real browser rather than requesting the page. A
patched Firefox driven by stock Playwright presents a consistent TLS handshake, a
plausible fingerprint and a real event stream, so the request that asks for the rates
looks like the request a person's browser makes.

The second is subtler and specific to this data. Protection frequently degrades rather
than refuses: you get the page shell, the table renders empty or with placeholder dashes,
and nothing signals an error. A scraper that only checks for an HTTP error records a
garage with no tariffs.

Guard on the positive signal rather than the absence of a negative one:

```python
    rows = page.query_selector_all("table.rates tbody tr")
    parsed = [r for r in rows if DURATION.search(r.inner_text())]
    if rows and not parsed:
        raise RuntimeError("table present but no band parsed: treat as a failed read")
```

That check is the local version of a rule that runs through this whole corpus: a
suppressed or emptied signal is a failure, not a result. The general form, and the order
to debug it in, is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## A schema that survives the next redesign

One row per band, with the scope and the garage attached, plus a separate table for notes:

| column | example | why it is separate |
|---|---|---|
| `garage_id` | `central-01` | stable across redesigns, unlike the page URL |
| `scope` | `Weekend` | the same garage has several tariffs |
| `from_minutes` / `to_minutes` | `0` / `120` | the band, in one unit |
| `amount_text` | `£4.50` | unparsed, so the currency survives |
| `label` | `Up to 2 hours` | the original string, for auditing a bad parse |
| `observed_at` | epoch seconds | tariffs change, and you want the history |

Keeping `label` and `amount_text` unparsed is what lets you re-derive everything later
when a site starts writing "2 hrs" instead of "2 hours". Deriving at read time costs
nothing; re-scraping a city because you stored only a float costs a week.

Store it in [a SQLite database](how-to-scrape-into-a-database-playwright.md) if you will
query it, or [JSON Lines](how-to-scrape-to-json-lines-playwright.md) if you will
reprocess it. Either way, append rather than overwrite: a parking tariff that changed last
month is the most interesting row in the table, and an updated-in-place record cannot tell
you it ever moved.

## Short answers to the questions that lead here

**Why does the price I scraped not match what the garage charges?** Because a tariff
table is a set of duration bands and the amount depends on when the car arrives and
how long it stays. A single headline number is one band read out of context, and the
small print underneath the table, the daily maximum and the minimum charge, usually
outranks the table itself.

**How do I know which tariff table I am looking at?** Read the scope, not just the
rows. The same garage publishes weekday, weekend, event-day and overnight tables, and
the page often names the scope once, above the table, then never again. Store the
scope on every band you capture.

**The rates only appear after I fill in a date. Is that a block?** No, it is the
normal flow on most operator sites: the table is computed from the arrival and
departure you enter. Drive the date control the way a person would and wait for the
table to repopulate before reading it.

**How fast can I crawl parking sites?** Slowly. These are small sites and a city-wide
sweep at full speed is a visible load on infrastructure that was sized for a few
hundred human visitors a day.

**See also:** [How to scrape accordion and tab content with
Playwright](how-to-scrape-accordion-and-tab-content-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md), [How to
resume an interrupted scrape with
Playwright](how-to-resume-an-interrupted-scrape-playwright.md)

## Sources

- Playwright, Locators, https://playwright.dev/python/docs/api/class-locator -
  `evaluate_all` and the locator waiting model, checked for reading a whole table in
  one pass instead of one call per row.
- This project's pages on pacing and on resuming an interrupted run, which carry the
  behaviour this page depends on instead of repeating it.
