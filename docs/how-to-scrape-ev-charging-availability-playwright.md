---
title: "How to scrape EV charging availability with Playwright"
description: "Scrape EV charger availability with Playwright: capture the live status feed the map is drawing from, record a timestamp with every reading, and treat availability as a measurement rather than a fact."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 151
---


# How to scrape EV charging availability with Playwright

To scrape charger availability, capture the **status feed** the map is drawing from
rather than the map, and stamp every reading with the time you took it. Availability is
not an attribute of a charging point. It is a measurement that was true for a few
seconds, and a row without a timestamp is a claim you cannot check later.

This is what separates charger data from most listing data. A restaurant menu scraped
yesterday is still roughly the menu. A connector that read "available" ninety seconds
ago tells you almost nothing, and a database of such rows without times is not a dataset,
it is a pile of expired assertions.

This page covers finding the feed behind the map, modelling a charging point correctly
as a site with several connectors, and polling at a rate that is honest about both the
data and the operator's servers.

## Read the feed, not the pins

Charger maps are drawn from a JSON response, and the pins are a rendering of it. Capture
the response instead of parsing markers out of the canvas or the marker layer:

```python
import json, time
from invisible_playwright import InvisiblePlaywright

payloads = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_response(response):
        if "/api/" in response.url and "station" in response.url.lower():
            try:
                payloads.append({
                    "at": time.time(),
                    "url": response.url,
                    "body": response.json(),
                })
            except Exception:
                pass          # not JSON: a tile, a sprite, an analytics beacon

    page.on("response", on_response)
    page.goto("https://example.com/map")
    page.wait_for_timeout(4000)     # let the map settle and issue its queries
```

The map usually re-queries as you pan, with a bounding box in the request. That is the
handle you want: it lets you walk a region deliberately instead of scrolling a map and
hoping. The mechanics of capturing these responses, including the ordering problems, are
in [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## A charging point is not one thing

The single most common modelling error here is treating a pin as a charger. A pin is a
site. A site has several charging points. A point has several connectors, often of
different types and speeds, and availability lives at the connector level:

```python
def flatten(station):
    site = {"site_id": station["id"], "name": station.get("name")}
    for point in station.get("evses", []):
        for connector in point.get("connectors", []):
            yield {
                **site,
                "point_id": point.get("uid"),
                "connector_id": connector.get("id"),
                "standard": connector.get("standard"),      # CCS, CHAdeMO, Type 2
                "power_kw": connector.get("max_electric_power", 0) / 1000 or None,
                "status": point.get("status"),              # AVAILABLE, CHARGING, OUTOFORDER
            }
```

Flattening to one row per connector costs nothing and makes every later question
answerable. Flattening to one row per site makes "is there a fast charger free" a
question your data cannot answer, and you will not notice until someone asks it.

## Stamp the time, and keep the operator's own timestamp separately

Two times matter and they are not the same. There is when **you** read the value, and
there is when the **operator** last heard from the hardware. The second is often in the
payload as `last_updated`, and it is frequently minutes or hours old:

```python
row["observed_at"] = payloads[-1]["at"]              # when we looked
row["reported_at"] = station.get("last_updated")     # when the network last knew
```

Keep both. A connector reported as available with a `last_updated` from six hours ago is
a different fact from one updated forty seconds ago, and collapsing them into a single
"available" is how a dataset becomes confidently wrong. When the operator gives you no
`last_updated` at all, record that absence rather than substituting your own time.

## Poll on the data's clock, not on yours

Status changes on the scale of a charging session. Polling every ten seconds produces
almost entirely duplicate rows and an obvious traffic pattern; polling once an hour
misses most sessions. A few minutes is the honest middle, and storing only changes keeps
the volume sane:

```python
last = {}

def changed(row):
    key = (row["site_id"], row["connector_id"])
    if last.get(key) == row["status"]:
        return False
    last[key] = row["status"]
    return True
```

Write the transitions rather than the polls. The result is a state history you can
replay, at a fraction of the rows, and it survives a gap in collection better than a
dense series with holes in it.

Pace the loop deliberately. The rules and the reasoning are in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md), and they
matter more here than on a static site: a poll loop is by definition repetitive traffic,
which is the easiest kind to notice.

## Two traps specific to this data

**The map filters before it queries.** Most of these interfaces default to hiding
out-of-service points, or to showing only connector types matched to a vehicle the site
remembers. The feed then arrives pre-filtered and looks complete. Check the request
parameters for a filter you did not set, and clear it in the interface rather than in the
URL, so the site's own state matches what you asked for.

**Coverage is bounded by the viewport.** The query carries a bounding box, and a large
box is often silently capped at a maximum number of results. Walk a grid of small boxes
instead of one large one, and check for a `truncated` or `total` field that tells you the
response was cut.

## What this is good for, and what it is not

Charger availability is a live operational signal. It supports questions about
utilisation, reliability and which sites are chronically full. It does not support a
claim about what is free right now, unless the reading is seconds old, because by the time
a row lands in a database it may already be wrong.

If you keep the connector-level rows with both timestamps, that limitation is visible in
the data itself, which is the point. Store it in something you can query by time, such as
[a SQLite database written as you go](how-to-scrape-into-a-database-playwright.md), and
resist the temptation to keep only the latest value per connector.
