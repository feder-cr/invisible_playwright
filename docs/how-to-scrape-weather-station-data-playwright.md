---
title: "How to scrape weather station data with Playwright"
description: "Scrape weather station data with Playwright by capturing the observation feed instead of the rendered table, converting timestamps to UTC once, storing readings at their reported interval, and treating unit toggles as display only."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 128
---


# How to scrape weather station data with Playwright

To scrape weather station data with Playwright, capture the underlying observation
feed instead of parsing the rendered table, convert every timestamp to UTC once using
the offset the feed itself carries, keep each reading tagged with a station id, a
timestamp, a timezone and a variable name, store it at the interval the station
actually reported instead of resampling it to a fixed grid, and read the station
status field to tell a missing reading apart from a station that went offline.

A weather dashboard looks like the simplest scraping target there is: a table of
numbers that refreshes every few minutes. It is not. A single temperature value is
meaningless without a station, a clock and a variable attached to it, the page prints
the time with no offset, and the unit under the wind speed column can flip depending on
who is signed in. None of that shows up until two stations refuse to merge into one
dataset. This page is the mechanics that keep them lined up.

## A reading is four identifying fields, not just a number

A bare value is not a reading. A temperature of 18.3 tells you nothing until you also
know which station recorded it, at what instant, in which timezone that instant was
expressed, and which variable it is. Drop any one of the four and the number stops
being comparable to anything: two stations reporting "18.3" an hour apart are not the
same fact unless their timestamps agree on a clock, and a temperature and a dew point
are not interchangeable just because they arrived in the same payload.

Treat the row shape as non-negotiable before you write a single selector. Every reading
needs a `station_id`, a `timestamp` normalized to a single clock, the offset that
timestamp was converted from, a `variable` name, and the `value` itself. A scraper that
emits bare `(time, value)` pairs works fine for one station and falls apart the day you
add a second one, because nothing in the row says where it came from.

## Convert local time to UTC once, at read time

The rendered page nearly always shows local time with no offset printed next to it. A
row that says "14:35" tells you nothing about which 14:35 it is unless you already know
the station's timezone from somewhere else, and that somewhere else is not the table.
The feed the page fetches to fill that table is a different story: it usually carries
either a UTC timestamp directly or a local timestamp plus an explicit offset in minutes.
Read the feed, not the table, and convert exactly once, at the moment you first see the
reading.

```python
from datetime import datetime, timedelta
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    with page.expect_response(
        lambda r: "observations" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.goto("https://example.com/station/KXXX0001")

    feed = caught.value.json()
    rows = []
    for obs in feed["observations"]:
        # the offset lives in the feed; the rendered page never prints it
        offset_minutes = obs.get("utcOffsetMinutes", 0)
        local_dt = datetime.fromisoformat(obs["obsTimeLocal"])
        utc_dt = local_dt - timedelta(minutes=offset_minutes)

        rows.append({
            "station_id": feed["stationId"],
            "timestamp_utc": utc_dt.isoformat(),
            "source_offset_minutes": offset_minutes,   # record what you converted from
            "variable": "temperature",
            "value": obs["metric"]["tempAvg"],
        })
```

Keep the original offset alongside the converted timestamp. If a station later turns
out to have the wrong offset in its metadata, you can recompute every stored row from
the value you kept instead of re-scraping the archive. Converting without recording
what you converted from is a one-way trip.

## Store readings as reported, never resampled to a fixed grid

Stations do not report on a shared clock. One network polls every five minutes, another
every ten, a home-station network every fifteen or sixty, and a single archive mixes all
of them across its station list. The instinct is to resample everything onto one tidy
grid before it ever reaches storage. Do not. Resampling at ingest throws away the true
reporting cadence, and the raw intervals are never written down again.

Store the reading exactly as the feed reported it, with its own timestamp, and store the
cadence as data rather than assuming it. A downstream consumer that genuinely needs a
fixed grid can resample from the as-reported table at query time.

One row per observation, with whichever variables that station actually reports and no
padding for the ones it does not, is the case
[JSON Lines was already the right container for](how-to-scrape-to-json-lines-playwright.md):
a station reporting three variables and one reporting twenty append to the same file,
and a partial run leaves a file that still parses.

```python
def rows_from_feed(feed, variable_map):
    """Emit one row per (variable) per observation, using the feed's own timestamps.
    No interval is assumed or enforced here."""
    rows = []
    station_id = feed["stationId"]
    for obs in feed["observations"]:
        offset_minutes = obs.get("utcOffsetMinutes", 0)
        for source_key, variable in variable_map.items():
            value = obs.get("metric", {}).get(source_key)
            if value is None:
                continue
            rows.append({
                "station_id": station_id,
                "timestamp_utc": obs["obsTimeUtc"],   # already UTC on this feed
                "source_offset_minutes": offset_minutes,
                "variable": variable,
                "value": value,
            })
    return rows
```

Two networks in the same dataset reporting at five and sixty minutes is not a defect to
paper over. It is a fact about the network, and it belongs in the stored row, not erased
by a resample step nobody can undo.

## A missing reading and an offline station look identical until you read the status field

A naive time series treats every gap the same way: no row at that timestamp. A missing
reading and a station that went offline are different facts. One means the sensor was
live and a single sample dropped. The other means the whole station stopped reporting,
possibly for days, with every variable absent, not just the one you happened to be
graphing. Averages, interpolation and uptime statistics all depend on knowing which.

Some feeds carry a station status field precisely for this reason, separate from any
individual variable's value. Read it and store it. When the field is not present at
all, the honest move is to record that the disambiguation is not possible on this feed
rather than to guess from the shape of the gap.

```python
def classify_reading(obs):
    status = obs.get("stationStatus") or obs.get("qcStatus")

    if status is None:
        # this feed carries no status field: a gap here cannot be classified,
        # and the row should say so instead of pretending it can.
        return "unknown_no_status_field"

    if status.upper() in ("OFFLINE", "INACTIVE"):
        return "station_offline"

    if obs.get("metric", {}).get("tempAvg") is None:
        return "missing_reading"

    return "ok"
```

A dataset that silently drops both kinds of gap into the same blank cell cannot answer
"was the sensor working" later, and re-deriving that answer from the pattern of missing
timestamps alone is a guess dressed up as a measurement.

## Units mix within a region, and a page toggle does not change what the API returns

Wind speed shows up in miles per hour on one station and kilometers per hour on another
inside the same country, and pressure alternates between inches of mercury and
hectopascals the same way. Plenty of pages also let a signed-in visitor flip a display
preference, mph to km/h or Fahrenheit to Celsius, and that toggle changes only what gets
rendered. The value the API returns stays fixed to whatever unit that station's feed
always used.

Read the unit from the payload itself, never assume it from the page's current display
mode, and convert explicitly with a named function so the conversion is auditable later.

```python
MPH_TO_KMH = 1.609344
INHG_TO_HPA = 33.8639

def normalize(raw_value, raw_unit, target_unit):
    if raw_value is None:
        return None, raw_unit
    if raw_unit == "mph" and target_unit == "km/h":
        return raw_value * MPH_TO_KMH, "km/h"
    if raw_unit == "inHg" and target_unit == "hPa":
        return raw_value * INHG_TO_HPA, "hPa"
    if raw_unit == target_unit:
        return raw_value, raw_unit
    raise ValueError(f"no conversion registered for {raw_unit} -> {target_unit}")

# read straight from the feed's own unit field, whatever the rendered page happens
# to be displaying for the current session's preference
raw_speed, raw_unit = obs["metric"]["windSpeed"], obs["metric"].get("windSpeedUnit", "mph")
value_kmh, unit = normalize(raw_speed, raw_unit, "km/h")
```

Store both the original value and unit next to the converted one. If a conversion
constant turns out wrong for one provider's rounding, you correct it from the stored
raw pair instead of re-scraping.

## Pull a multi-year archive one period at a time

A historical archive is almost never paginated by row count the way a search results
list is. It is paginated by day or by month, so a request asks for one calendar period
and gets back everything that station reported in it. A five-year pull across one
station is roughly sixty separate requests, one per month, not one long scroll.

Sixty requests is long enough that something will interrupt one of them, and a
calendar walk has the property that makes recovery cheap: the period is the key, so
[a run can pick up from the period it stopped on](how-to-resume-an-interrupted-scrape-playwright.md)
without re-reading the years already stored.

Walk the calendar explicitly and issue one request per period. `page.request` shares
the browsing context's cookies with a plain HTTP call, so an archive endpoint gated
behind a session the browser already holds answers without a second login flow.

```python
from datetime import date
import calendar

def month_ranges(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last_day = calendar.monthrange(y, m)[1]
        yield date(y, m, 1), date(y, m, last_day)
        m = m + 1 if m < 12 else 1
        y = y if m != 1 else y + 1

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/station/KXXX0001/history")   # establishes the session

    all_rows = []
    for start, end in month_ranges(date(2019, 1, 1), date(2024, 12, 31)):
        resp = page.request.get(
            f"https://example.com/api/history?station=KXXX0001&start={start}&end={end}"
        )
        payload = resp.json()
        all_rows.extend(rows_from_feed(payload, {"tempAvg": "temperature"}))
        page.wait_for_timeout(400)   # pace the pull across many months
```

A single wide date range in one request looks convenient and is usually rejected or
silently truncated: archive endpoints built around monthly storage tend to cap a query
at one period's rows even when the URL accepts a wider range. Walking the calendar
yourself is the version that actually returns five years of data instead of one month
with no error to say so.

## Where this stops: derived indices belong to the provider's formula

Heat index and wind chill are not raw measurements. They are computed from temperature,
humidity and wind speed by a formula the network chose, and different networks use
different formulas, rounding and thresholds for when the index even applies. A
displayed heat index of 41 is the output of somebody else's function, not a fact you can
independently verify from the raw inputs.

If the feed hands you a heat index field directly, keep it and record which field it
came from. If it does not, re-deriving one from temperature and humidity is a reasonable
estimate and nothing more: it can, and regularly will, disagree with what the page
displays, because you do not know which formula variant that network applied. Say so in
the stored row rather than presenting a computed value as the provider's own.

## Assemble the full reading before you store it

Each of the pieces above is a single concern. Put together, they are one function that
takes a raw observation and returns a row that can sit next to any other station's row
without special-casing anything downstream.

```python
def build_reading(feed, obs, source_key, variable, target_unit):
    offset_minutes = obs.get("utcOffsetMinutes", 0)
    local_dt = datetime.fromisoformat(obs["obsTimeLocal"]) if "obsTimeLocal" in obs else None
    utc_iso = obs.get("obsTimeUtc") or (
        (local_dt - timedelta(minutes=offset_minutes)).isoformat() if local_dt else None
    )

    raw_value = obs.get("metric", {}).get(source_key)
    raw_unit = obs.get("metric", {}).get(f"{source_key}Unit", target_unit)
    value, unit = normalize(raw_value, raw_unit, target_unit)

    return {
        "station_id": feed["stationId"],
        "timestamp_utc": utc_iso,
        "source_offset_minutes": offset_minutes,
        "variable": variable,
        "value": value,
        "unit": unit,
        "status": classify_reading(obs),
    }
```

Every field the earlier sections argued for is present on every row: identity, a single
clock, the offset it came from, the variable, the value in a known unit, and the status
that tells a real gap apart from an offline sensor. Nothing downstream has to guess.

## Conclusion

Weather station data punishes shortcuts that other scraping targets tolerate. A number
without its station, clock and variable cannot be merged with anything else. A local
timestamp with the offset thrown away cannot be recovered. A resampled series cannot
tell you the cadence a station actually reported at, and a gap that does not say why it
is a gap cannot separate a dropped sample from a dead station. None of these are edge
cases; they are the normal condition of a multi-station archive, and the fix for each
one is cheap at read time and near impossible after the fact. Capture the feed, convert
once, keep the raw unit and cadence, read the status field, and stop where a value
becomes the provider's own computed guess rather than a measurement.

## Short answers to the questions that lead here

**Why does the same station show different local times on the page and in the API
response?** The page renders local time with no offset printed. The feed carries either
UTC directly or a local time plus an explicit offset. Read the feed and convert once.

**Should I resample all my stations to the same interval before storing them?** No.
Stations report on different native intervals, five, ten, fifteen or sixty minutes
depending on the network, and resampling at ingest destroys that cadence permanently.

**How do I tell a missing reading from a station that is offline?** Read the station
status field if the feed carries one. When it does not, record that the distinction is
not available rather than guessing from the shape of the gap.

**The page shows kilometers per hour but the API returned a different number. Why?**
A signed-in preference changes only the rendered display unit. The API keeps returning
whatever unit that station's feed always used, so read the unit field from the payload.

**How do I pull five years of history without one request timing out?** Archives are
typically paginated by day or by month, not by row count. Walk the calendar and issue
one request per period, since most archive endpoints cap or truncate a wide range.

**Can I recompute heat index or wind chill myself instead of scraping the page's
value?** You can estimate one, but networks use different formulas and rounding for
these derived indices, so your number may not match. Keep the provider's field instead.

## Sources

- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`Response.json`](https://playwright.dev/python/docs/api/class-response#response-json),
  used to capture the feed instead of parsing the rendered table.
- Playwright's [`APIRequestContext`](https://playwright.dev/python/docs/api/class-apirequestcontext)
  (`page.request`), used for the paginated historical pulls; it shares the browsing
  context's cookies, so an archive gated behind a session the browser already holds
  answers without a second login.
- The IANA Time Zone Database convention of an explicit UTC offset per record, the
  reason a bare local timestamp cannot be converted without also reading the offset
  the feed carries next to it.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the general technique behind reading the feed instead of the DOM,
[timezone does not match the proxy IP](timezone-proxy-mismatch.md) for what a mismatched
clock looks like from the detection side, [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for the same locale-aware conversion problem applied to numbers and dates,
[scraping stock and financial data](how-to-scrape-stock-and-financial-data-playwright.md)
for another page that mixes a stable table with a live feed at different layers, and
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) for the re-query
discipline a period-by-period archive pull also depends on.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A wind speed column
stayed in mph in the feed while a signed-in session displayed km/h on the page, and two
exports of the same day disagreed until the unit was read from the payload instead of
the display setting.*
