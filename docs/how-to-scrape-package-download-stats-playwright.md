---
title: "How to scrape package download statistics with Playwright"
description: "Scrape package download counts with Playwright: take the series behind the chart rather than the rendered chart, keep the mirror and CI caveats with the numbers, and prefer the published dataset where one exists."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 163
---


# How to scrape package download statistics with Playwright

To scrape download statistics, take the **series the chart is drawing**, not the chart.
Every registry dashboard renders its graph from a JSON response, and that response has the
per-day numbers at full precision, already aligned to dates, without the rounding and
smoothing the picture applies.

The second point matters more than the technique: a download count is not a user count.
Mirrors, CI runs, container builds and caching proxies all appear as downloads, and on
some packages they are the majority. A number captured without that caveat attached will
be read as adoption by whoever sees it next, including you in six months.

This page covers taking the series, the caveats that belong with it, and the case for not
scraping at all.

## First, check whether the data is published

Several registries publish their download data as a dataset or an endpoint, which is
faster, complete, and does not require a browser. That is the correct first move, and it
takes one look at the registry's documentation.

Where a public dataset exists, use it and stop reading here. This page is for the case
where the numbers exist only behind a dashboard.

## Take the response, not the rendering

```python
import time
from invisible_playwright import InvisiblePlaywright

series = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_response(response):
        url = response.url
        if any(k in url for k in ("/downloads", "/stats", "/metrics")):
            try:
                series.append({"url": url, "at": time.time(), "body": response.json()})
            except Exception:
                pass

    page.on("response", on_response)
    page.goto("https://registry.example.com/package/some-package")
    page.wait_for_selector("canvas, svg.chart")
    page.wait_for_timeout(2500)
```

The request usually carries the window as parameters, which is the handle for getting more
than the default view:

```python
    page.goto("https://registry.example.com/package/some-package?range=12m&interval=day")
```

Change the range in the interface and watch which parameter moves, rather than guessing
parameter names. The general mechanics, including the ordering trap where the response
arrives before your listener is attached, are in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## When the numbers really are only in the picture

Some dashboards render server-side to an image, or draw to a canvas with no JSON behind
it. Read the values from the accessible layer before reaching for pixels:

```python
    points = page.eval_on_selector_all(
        "svg.chart [role='graphics-symbol'], svg.chart .datapoint",
        "els => els.map(e => ({ label: e.getAttribute('aria-label'), value: e.dataset.value }))",
    )
```

Charts built for accessibility carry the values in attributes, and reading them is exact
where reading pixels is not. Where that fails too, the approach and its limits are in
[extracting data from canvas charts](how-to-extract-data-from-canvas-charts-playwright.md).

## Keep the caveats in the row, not in your head

Store the qualifiers the registry itself publishes, because they change what the number
means:

```python
    row = {
        "package": "some-package",
        "date": "2026-09-06",
        "downloads": 18432,
        "counts_mirrors": None,        # unknown unless the registry says
        "source": "registry dashboard",
        "captured_at": time.time(),
    }
```

Three specific distortions are worth a column each where the registry tells you:

- **Mirrors and proxies.** A single corporate proxy can multiply or divide the count.
- **CI traffic.** A popular library's weekday-versus-weekend pattern is often CI, not
  people, and it is visible as a flat weekly cycle in the series.
- **Version splits.** Total downloads across versions hide that most traffic is an old
  pinned release, which is usually the interesting fact.

Where the dashboard offers a per-version breakdown, take it. Total-only series answer
almost no question worth asking.

## Comparing packages is where this goes wrong

The tempting use is a comparison, and it is the least reliable one. Two packages with the
same headline count can differ by an order of magnitude in real users, because one is a
transitive dependency of something popular and the other is installed deliberately.

If you make the comparison anyway, at minimum compare the same window, the same interval
and the same registry, and say in the output that the number counts requests. That is the
same honesty a
[realness-first mindset](how-to-scrape-without-getting-blocked.md) asks for elsewhere:
report what you measured, not what you hope it means.

## Store the series long, refresh daily

One row per package per day per version, appended. Registries revise recent days for
several days after the fact, so re-fetch a trailing window rather than only yesterday:

```python
    for day in last_n_days(10):
        upsert(package, day, value_for(day))
```

Ten days of overlap costs nothing and quietly repairs the revisions. Daily is the right
cadence, the pacing rules in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) apply, and
[JSON Lines](how-to-scrape-to-json-lines-playwright.md) is a good fit for an append-only
series you will reprocess later.

## A complete daily collector

```python
import json, time
from invisible_playwright import InvisiblePlaywright

PACKAGES = ["some-package", "another-package"]

def collect(page, package):
    captured = []

    def on_response(response):
        if any(k in response.url for k in ("/downloads", "/stats", "/metrics")):
            try:
                captured.append({"url": response.url, "body": response.json()})
            except Exception:
                pass

    page.on("response", on_response)
    page.goto(f"https://registry.example.com/package/{package}?range=12m&interval=day",
              wait_until="domcontentloaded")
    page.wait_for_selector("canvas, svg.chart", timeout=20000)
    page.wait_for_timeout(2500)
    page.remove_listener("response", on_response)

    if not captured:                       # no JSON: fall back to the accessible layer
        points = page.eval_on_selector_all(
            "svg.chart [role='graphics-symbol'], svg.chart .datapoint",
            "els => els.map(e => ({label: e.getAttribute('aria-label'), value: e.dataset.value}))",
        )
        return [{"package": package, "source": "accessible-layer", "points": points}]
    return [{"package": package, "source": "json", **c} for c in captured]

with InvisiblePlaywright(seed=42) as browser, open("downloads.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    for package in PACKAGES:
        for record in collect(page, package):
            record["observed_at"] = time.time()
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(4000)
```

Recording `source` alongside the numbers is what keeps a mixed dataset honest. A series
built partly from the JSON endpoint and partly from chart labels has two different
precisions in one column, and the only way to notice later is if the row says which it was.

## Why the browser is needed for a number that looks public

Download counts feel like open data, and the dashboard usually is. Two things still push
this into browser territory.

**The series endpoint expects the page.** Many registries check the referrer, a session
cookie or a token minted by the page before serving the JSON. A direct request for the
same URL returns an error or an empty series, which is why the code lets the page make the
request and captures the response rather than replaying the URL.

**Degradation is silent and looks like a quiet week.** Under protection the chart renders
with no data and the page looks fine. A collector that stores whatever arrived writes zeros,
and zeros in a download series are indistinguishable from a real collapse in usage. Guard
positively:

```python
    if record["source"] == "json":
        series = record["body"].get("downloads") or record["body"].get("data") or []
        if not series:
            raise RuntimeError("empty series returned: failed read, not a quiet week")
```

The order to debug that, cheapest first, is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md). And the point
worth repeating from the top of this page: if the registry publishes the data as a dataset,
none of this is necessary, and checking takes a minute.

## The schema, and the comparison it refuses to make easy

One row per package, version, date and observation:

| field | note |
|---|---|
| `package`, `version` | version is null only when the registry offers no breakdown |
| `date` | the registry's day, not yours |
| `downloads` | the count as served |
| `source` | `json` or `accessible-layer` |
| `observed_at` | so a revision is visible as two rows for one day |

Keeping the observation timestamp separate from the data date is what makes revisions
detectable: the same `date` collected twice with different `downloads` is a registry
correction, and that is a fact worth having rather than an inconsistency to overwrite.

The comparison this schema deliberately does not make easy is the one people want most:
package A against package B. You can write the query, and the answer will be misleading
unless both series carry the same window, the same interval and the same caveats about
mirrors and continuous integration. Keeping `version` in the key at least surfaces the
usual explanation, which is that most of a popular library's traffic is one old pinned
release being pulled by machines.
