---
title: "How to scrape event and ticket listings with Playwright"
description: "Scrape event and ticket listings with Playwright: wait for the availability XHR, normalise event times to the source timezone, and step the calendar widget."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 42
---


# How to scrape event and ticket listings with Playwright

To scrape event and ticket listings with Playwright, wait for the availability request
instead of the initial HTML, attach each event's declared source timezone and store UTC
alongside it, step the calendar widget to reach future dates, and poll the state attribute
rather than the visible label. The one thing that decides whether any of it runs is that
the browser's timezone matches the proxy's exit IP.

| What trips up an event scraper | The fix |
|---|---|
| Price tiers and seat counts arrive over a later request, not in the initial HTML | Wait for the availability response and read its JSON, not the shell |
| Start times are written in the venue's timezone, not yours | Attach the declared source zone and store UTC alongside the local value |
| Future dates hide behind a calendar widget | Step the "next" control and wait for each panel's data before reading |
| Sold-out and on-sale are often the same node with a different value | Read the machine-readable state attribute and poll on an interval |
| The browser's timezone disagrees with the proxy's exit IP | Let the timezone auto-derive from the egress IP so the two agree |

Event and ticket pages look simple and are not. The shell renders fast, but the two
fields you actually came for, price tiers and seat availability, arrive later over a
separate request. The date on the page is written in the venue's timezone, not yours,
so a naive extractor records a time that is off by hours. Future dates hide behind a
calendar widget you have to click. And the same DOM node that says "on sale" now says
"sold out" a minute later, so reading it once tells you the state at one instant and
nothing about the trend.

This page is the order that survives all four: wait for the data request rather than the
shell, normalise every time against the declared source timezone, step the calendar, and
poll availability for long enough to matter. The honest caveat is at the end, because it
decides whether any of the rest runs at all.

## Wait for the availability request, not the shell

The first mistake is reading the DOM as soon as it exists. On a listing page the price
tiers and the remaining seats are almost never in the initial HTML. The server sends a
skeleton, and a script fetches the real numbers over XHR or fetch a few hundred
milliseconds later. If you extract at `domcontentloaded`, you capture placeholders or an
empty table, and it looks like the site returned nothing.

Two ways to wait for the right thing. The blunt one is Playwright's
[`wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector),
waiting for the element that only appears once the numbers land:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/events/summer-series")
    # the price row only exists after the availability call resolves
    page.wait_for_selector("[data-testid='price-tier']", state="visible", timeout=15000)
    tiers = page.query_selector_all("[data-testid='price-tier']")
    for t in tiers:
        print(t.inner_text())
```

The precise one is Playwright's
[`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response),
which waits for the response itself and also hands you the clean JSON before the page has
finished painting it into markup you would have to parse back out:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    with page.expect_response(
        lambda r: "/api/" in r.url and "availability" in r.url and r.status == 200
    ) as resp_info:
        page.goto("https://example.com/events/summer-series")
    data = resp_info.value.json()
    for tier in data["tiers"]:
        print(tier["name"], tier["price"], tier["remaining"])
```

Reading the response body is usually better than scraping the rendered table: it is
already structured, it carries fields the page chooses not to display, and it does not
break when the site restyles its markup. The mechanics of catching these calls, including
the ones fired from a service worker, are in
[how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## Normalise event times against the source timezone

Every listing has a start time, and that time is written in the timezone of the venue or
the seller, not the timezone your browser is running in. A show at 20:00 local to the
venue is 20:00 on the page whether you are polling from the same city or three time zones
away. If you store the naive string, you have stored a number that means nothing without
its zone.

The zone is almost always somewhere on the page or in the JSON: an
[IANA name](https://www.iana.org/time-zones) like `America/Chicago`, a UTC offset, or an
`Z`-suffixed ISO timestamp that is already absolute. Read it explicitly and attach it,
rather than assuming the browser's own zone:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

# values you extracted from the listing
raw_start = "2026-08-14T20:00:00"       # naive, local to the venue
source_zone = "America/Chicago"          # declared on the page or in the JSON

local_dt = datetime.fromisoformat(raw_start).replace(tzinfo=ZoneInfo(source_zone))
utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
print(local_dt.isoformat())   # keep the venue-local time for display
print(utc_dt.isoformat())     # store UTC so rows from different venues sort correctly
```

Keep both. The venue-local time is what a human sees and what you show back. The UTC
value is what lets you sort listings from three different regions into one coherent feed.
Never let the browser's own timezone silently become the assumed zone of the data, and
never convert a naive string using `datetime.now()`'s offset, which is the bug that makes
half a feed land an hour off after a daylight-saving change.

## Step the calendar widget to reach future dates

The dates on the first paint are usually the near ones. Anything further out sits behind a
calendar or a "next" control, and the future listings do not exist in the DOM until you
click to them. Scraping only what is visible gives you this week and misses the on-sale
date three weeks out that you were actually watching for.

Treat the calendar as a loop: read the current panel, advance, wait for the new panel's
data request to settle, read again. Waiting between steps matters, because clicking
"next" fires the same availability XHR the first load did, and reading before it resolves
gives you the previous panel's numbers under the new panel's dates.

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/events/summer-series")

    all_dates = []
    for _ in range(6):   # walk six panels forward
        page.wait_for_selector("[data-testid='day-cell']", state="visible")
        cells = page.query_selector_all("[data-testid='day-cell']")
        all_dates.extend(c.get_attribute("data-date") for c in cells)

        nxt = page.query_selector("button[aria-label='Next']")
        if not nxt or nxt.is_disabled():
            break
        with page.expect_response(lambda r: "availability" in r.url):
            nxt.click()   # mouse arcs to the control on a Bezier curve

    print(sorted(set(d for d in all_dates if d)))
```

The `expect_response` wrapper around the click is what keeps the loop honest: it does not
read the new panel until the panel's own data has arrived.

## Poll availability, and read the state, not just the label

Sold-out and on-sale are frequently the same node with a different value. The button that
says "Buy" becomes "Sold out"; the counter that read "40 left" reads "0". Because it is
one node, a single read tells you the instant you happened to look and nothing about
where it is heading. What you usually want is the transition, which means polling on an
interval and comparing.

```python
import time

def snapshot(page):
    page.wait_for_selector("[data-testid='avail-state']", state="visible")
    node = page.query_selector("[data-testid='avail-state']")
    return {
        "label": node.inner_text().strip(),
        "state": node.get_attribute("data-state"),   # e.g. on_sale / sold_out
        "remaining": node.get_attribute("data-remaining"),
    }

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/events/summer-series")

    previous = None
    for _ in range(20):
        current = snapshot(page)
        if current != previous:
            print(time.strftime("%H:%M:%S"), current)
            previous = current
        time.sleep(30)          # space the polls out; do not hammer one endpoint
        page.reload()
```

Read the machine-readable `data-state` attribute rather than the visible text where you
can. The label is localised and restyled; the state attribute is the site's own
canonical value and it does not change when the copywriter does. Space the polls out, both
because a tight loop against one endpoint is itself a velocity signal and because the
numbers do not change fast enough to justify it.

## The caveat that decides whether any of this runs

None of the code above matters if the session is flagged before the first availability
call returns. Ticket listings are among the most aggressively defended pages on the web,
and the single cheapest thing they check is whether your browser's story is internally
consistent. A browser reporting one timezone while its exit IP sits on another continent
is not a subtle tell; it is a contradiction a first-party script finds in one line, and it
is enough to serve you a challenge or a stripped page before any pricing loads.

This is where a real fingerprint earns its place and where it is not sufficient on its
own. Passing [CreepJS](creepjs-explained.md) means the browser is internally consistent
and does not announce automation, which this project's engine handles by deriving every
surface from one seed so the GPU, canvas, audio and font values all agree. But
consistency across the browser's own fields does not cover the browser against its exit.
The timezone has to match the proxy's exit, or the mismatch flags you no matter how clean
the rest is.

By default this wrapper derives the browser timezone from the egress IP, so a proxied
session is self-consistent without you setting anything:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    # timezone auto-derived from the proxy exit; the browser and the IP agree
    page = browser.new_page()
    page.goto("https://example.com/events/summer-series")
```

The failure mode is pinning a timezone by hand that does not match where the proxy comes
out, which reintroduces exactly the contradiction the default avoids.
[When the timezone does not match the proxy](timezone-proxy-mismatch.md) walks every
surface that has to agree, and [resolving the zone from the IP offline](offline-geoip-timezone-proxy.md)
covers doing it without a lookup call on the hot path. If you need to lock the identity to
one region deliberately, [pinning fields while leaving the rest seed-derived](pinning.md)
is the safe way to do it.

## Conclusion

Event and ticket scraping is four problems wearing one page. Wait for the availability
request instead of the shell, and read its JSON where you can. Attach the source timezone
to every event time and store UTC alongside the local value. Step the calendar and wait
for each panel's data before reading it. Poll the state attribute rather than the label,
and space the polls out. Do all four and the fingerprint that keeps the session real is
what buys you the time to. Set the timezone against the proxy exit first, because the
mismatch flags you before a single price tier loads.

## Short answers to the questions that lead here

**Why is the price table empty when I scrape it?** Because you read the DOM before the
availability XHR resolved. Wait for the price element or the response itself, not
`domcontentloaded`.

**Why are my event times off by a few hours?** You stored a naive string in the venue's
timezone and let your own zone become the assumed one. Read the declared source zone,
attach it, and keep a UTC copy for sorting.

**How do I get dates further in the future?** They are behind the calendar widget and do
not exist in the DOM until you click forward. Loop the "next" control and wait for each
panel's data request before reading.

**How do I tell sold-out from on-sale?** They are often the same node with a different
value. Read the machine-readable state attribute rather than the localised label, and poll
on an interval to catch the transition.

**Why do ticket sites block automation so fast?** They cross-check the browser's story
against itself and against your exit IP. A timezone that disagrees with the proxy is a
one-line tell that fires before any pricing loads.

**Does a good fingerprint alone get me through?** It makes the browser internally
consistent, which is necessary and not sufficient. The browser also has to agree with its
exit, so the timezone must match the proxy.

## Sources

- The real API surface of this wrapper, from its own quickstart and configuration pages:
  `InvisiblePlaywright(seed=...)` returns a stock Playwright `Browser`, and the timezone
  is auto-derived from the egress IP unless you override it.
- Playwright's own documentation for
  [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
  and [`wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector),
  both exercised through the real `Browser` object this project returns unchanged, and the
  [IANA Time Zone Database](https://www.iana.org/time-zones), the authoritative source for
  zone names like `America/Chicago`.
- This project's own measurements on defended listing pages, where a timezone that
  disagreed with the proxy exit produced a challenge before the availability request ever
  returned.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the availability call directly, and
[when the timezone does not match the proxy](timezone-proxy-mismatch.md) for the surface
that most often flags a listing scraper.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty price table and
the off-by-hours timestamp are both mistakes I made before writing them down.*
