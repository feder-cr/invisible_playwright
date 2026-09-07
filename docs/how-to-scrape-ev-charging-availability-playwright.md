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

## A complete poller, with the bounding box walked deliberately

```python
import json, time
from invisible_playwright import InvisiblePlaywright

BOXES = [                      # small boxes beat one big one: see the cap below
    (51.44, -2.62, 51.47, -2.56),
    (51.47, -2.62, 51.50, -2.56),
]

def harvest(page, box):
    south, west, north, east = box
    captured = []

    def on_response(response):
        if "station" in response.url.lower() and "/api/" in response.url:
            try:
                captured.append(response.json())
            except Exception:
                pass

    page.on("response", on_response)
    page.goto(f"https://example.com/map?sw={south},{west}&ne={north},{east}")
    page.wait_for_timeout(4000)
    page.remove_listener("response", on_response)
    return captured

with InvisiblePlaywright(seed=42) as browser, open("chargers.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    while True:
        for box in BOXES:
            for payload in harvest(page, box):
                stations = payload.get("stations") or payload.get("data") or []
                if payload.get("truncated") or len(stations) >= 500:
                    print("box capped, split it:", box)
                for station in stations:
                    for row in flatten(station):
                        row["observed_at"] = time.time()
                        out.write(json.dumps(row) + "\n")
                out.flush()
            page.wait_for_timeout(2000)
        time.sleep(300)
```

Removing the listener at the end of each box matters more than it looks. Leaving it
attached across boxes means responses from the previous query keep arriving into the next
box's bucket, and you attribute chargers to a region they are not in. That ordering trap,
and the related one where the response lands before you attach, are covered in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

The explicit cap check is there because a silently truncated response is the standard way
these maps limit load. A box that returns exactly 500 stations is almost never a box that
contains exactly 500 stations.

## What a charging network sees, and why a poll is the hard case

Most listing sites see a scraper once. A charger poller comes back every few minutes,
forever, from the same session, asking for the same regions. That is the traffic shape
that is easiest to identify, and no amount of fingerprint work makes a fixed-interval
loop look like a person watching a map.

Two things genuinely help, and they are about behaviour rather than identity.

Vary the interval rather than sleeping a constant. A jittered wait around a target rate
keeps the average where you want it without producing a metronome:

```python
import random
time.sleep(300 + random.uniform(-45, 45))
```

Read only what changed. If the operator's payload carries a `last_updated`, skip stations
whose value has not moved rather than re-querying their detail:

```python
    if station.get("last_updated") == seen.get(station["id"]):
        continue
```

Underneath that, the browser still has to look like a browser, because these APIs are
usually behind the same edge protection as the site. A patched Firefox driven by stock
Playwright gets the JSON that a stripped client is refused, and the reasoning and debug
order live in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## The schema that makes utilisation answerable

The reason to keep connector-level transitions rather than snapshots is that it makes the
useful questions cheap:

| question | what it needs |
|---|---|
| how often is this site full | transitions per connector, ordered |
| which connectors are broken for weeks | a long run of `OUTOFORDER` with no change |
| when is demand highest | transitions bucketed by hour of day |
| is the operator's data stale | the gap between `reported_at` and `observed_at` |

None of those are answerable from a table holding the current status per connector, which
is the shape almost every first attempt produces. One row per state change, with
`site_id`, `point_id`, `connector_id`, `standard`, `power_kw`, `status`, `observed_at`,
`reported_at`, answers all four.

Write it as [JSON Lines](how-to-scrape-to-json-lines-playwright.md) while collecting and
load it into a database for querying. Appending is what makes an interrupted poll leave
usable data instead of a hole, and a poll that runs for weeks will be interrupted.

## Short answers to the questions that lead here

**Should I read the map pins or the network request behind them?** The request.
Charger maps draw their pins from a JSON response, and the pins are a rendering of it
that has already lost fields. Capture the response and you get the operator's own
model, including the parts the map does not draw.

**Why is my charger count wrong?** Almost always because a pin was treated as a
charger. A pin is a site, a site holds several charging points, and a point holds
several connectors. Availability lives at the connector, so a count taken at the pin
level answers a different question.

**How often should I poll charger status?** On the data's clock, not yours. Status
changes on the scale of a charging session, so a ten-second poll produces almost
entirely duplicate rows and looks like exactly what it is: a script that never sleeps.

**Which timestamp should I store?** Both. When you read the value and when the
operator says the value was true are different facts, and only the second one lets you
tell a stale feed from a busy charger.

**See also:** [How to capture XHR and API responses in
Playwright](how-to-capture-xhr-api-responses-playwright.md), [How to rate limit your
own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md), [How to scrape
into a SQLite database with Playwright](how-to-scrape-into-a-database-playwright.md)

## Sources

- Playwright, Network, https://playwright.dev/python/docs/network -
  `page.expect_response()` and `page.route()`, checked for the response-capture
  pattern this page uses and for attaching and detaching a listener around a single
  query.
- This project's page on capturing XHR and API responses, which covers the general
  form that this page applies to a live availability feed.
