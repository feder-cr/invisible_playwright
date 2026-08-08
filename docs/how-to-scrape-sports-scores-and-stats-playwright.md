---
title: "How to scrape sports scores and stats with Playwright"
description: "Scrape live sports scores and stats with Playwright: read the score feed over a WebSocket, click each stat tab to trigger its XHR, and hold one long session."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 50
---


# How to scrape sports scores and stats with Playwright

**To scrape sports scores and stats with Playwright, read the data feed instead of
the rendered widget: subscribe to the WebSocket (or the polling XHR) that carries the
score, click each stat tab to trigger the request that loads its panel, route live and
finished-match data to separate parsers, and hold one seed-pinned session at a human
poll rate.** Each step below explains why the obvious static-page approach fails.

A sports page is not a document you read once. The score changes in place while the
page stays put, the deep stats hide behind tabs that only load when clicked, and a
finished match is formatted nothing like the live one. Scrape it the way you scrape a
static article and you will capture a number that was already stale by the time your
parser saw it, and you will miss most of the data entirely.

This page is the shape of the problem and four techniques that match it: read the feed
instead of the widget, click each tab to trigger its request, keep live and historical
formats apart, and hold the session open long enough to capture a whole match without
becoming a rate tell.

## Why a sports page is a moving target

Three properties make these pages different from a normal scrape, and each one breaks a
default assumption.

**The score updates in place.** The page loads once and then a WebSocket or a short
poll rewrites the score node every few seconds. There is no navigation, so the usual
signal that "new data arrived" never fires. The number you read at 20:14:03 can be a
different number at 20:14:09, and if you read the DOM on a fixed timer you will sample
it at moments that mean nothing.

**The stats live behind tabs.** Box score, play-by-play, per-period splits: each is a
separate panel, and each fetches its own XHR the first time you click its tab, not on
page load. If you never click, that data was never requested, and it is not sitting in
the HTML waiting to be parsed. The page is a set of lazy requests wearing one URL.

**Live and historical are two formats.** The compact live widget and the full
post-match stat table are built by different code paths and rarely share a shape. A
selector that reads the live score will not read the final box score, and a parser
tuned for finished matches will find nothing during play.

## Subscribe to the score feed instead of re-reading the DOM

The score you want is arriving over a socket. Read the socket, not the node it writes
to. Playwright exposes every WebSocket frame through
[`page.on("websocket", ...)`](https://playwright.dev/python/docs/network#websockets), so
you can capture each update at the moment it is delivered rather than polling the
rendered value and hoping your timer lines up.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    updates = []

    def on_socket(ws):
        # each frame is one score/clock update, delivered when it happens
        ws.on("framereceived", lambda payload: updates.append(payload))

    page.on("websocket", on_socket)

    page.goto("https://example.com/match/live")

    # watch for a fixed slice of the match; the frames arrive on their own cadence
    page.wait_for_timeout(60_000)

    print(f"captured {len(updates)} live frames")
```

If the site short-polls instead of using a socket, the same idea applies one layer
over: the updates come back as repeated XHR responses, and you subscribe to those with
[`page.on("response", ...)`](https://playwright.dev/python/docs/network#network-events)
filtered to the polling endpoint. Reading the transport
directly, in either form, is the whole technique in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md), and
it is strictly better than scraping the widget because it gives you every update with
its own timestamp instead of whatever happened to be on screen when you looked.

## Click each stat tab to trigger its XHR

A tab that has never been clicked has never loaded. To capture the box score and the
play-by-play you have to open each panel, and the reliable way to know its data
actually arrived is to wait for the request that the click fires, not for a spinner to
vanish.

```python
from invisible_playwright import InvisiblePlaywright

TABS = ["#tab-box-score", "#tab-play-by-play", "#tab-splits"]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/12345")

    panels = {}

    for tab in TABS:
        # the click triggers the panel's own XHR; wait for THAT, not a timeout
        with page.expect_response(
            lambda r: "/api/" in r.url and r.request.method == "GET"
        ) as resp_info:
            page.click(tab)
        response = resp_info.value
        panels[tab] = response.json()
        # let the panel settle before moving to the next tab
        page.wait_for_selector(f"{tab}[aria-selected='true']")

    for tab, data in panels.items():
        print(tab, "->", len(data), "rows")
```

[`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
arms the wait before the click, so there is no race between the
request going out and your code starting to listen. If a panel renders progressively
rather than in one response, the same discipline applies to the render: prefer waiting
for a concrete element or a specific response over a blind sleep, which is the argument
in [how to wait for a page to load](how-to-wait-for-page-load-playwright.md).

## Separate the live widget from the historical tables

Once a match ends, the compact live number is replaced by a full stat table with a
different structure. Treat them as two parsers with a switch between them, decided by
the state of the page rather than by a guess.

```python
from invisible_playwright import InvisiblePlaywright


def parse_live(page):
    # the live widget: one score node, updated in place
    home = page.locator("[data-live='home-score']").inner_text()
    away = page.locator("[data-live='away-score']").inner_text()
    return {"format": "live", "home": home, "away": away}


def parse_final(page):
    # the historical table: a different DOM entirely, many rows
    rows = page.locator("table.box-score tbody tr").all()
    return {
        "format": "final",
        "rows": [r.inner_text() for r in rows],
    }


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/12345")

    # decide by what is actually on the page, not by the clock
    if page.locator("table.box-score").count() > 0:
        record = parse_final(page)
    else:
        record = parse_live(page)

    print(record["format"])
```

The two functions never share a selector, and that is the point: a single parser that
tries to handle both ends up reading neither well. Detect the format from the DOM and
route to the parser built for it.

## Sustain the long session without becoming a rate tell

Capturing a full match means one browser held open for hours while it accumulates
play-by-play. A long-lived session is exactly the kind these sites watch, so two things
have to hold for the whole duration.

The first is a stable identity. If the fingerprint drifts mid-session, the far side
sees one visitor turn into another without a new page load, which is a stronger tell
than any single odd value. Passing a fixed `seed` pins the GPU, canvas, audio, fonts
and screen to one machine that stays the same from the first frame to the last, so a
three-hour session looks like one person watching one match. That same reproducibility
is what lets you replay a run that got throttled instead of guessing why, and it is why
the [seed sits at the centre of the configuration](configuration.md).

The second is honest, and it is the caveat this whole approach rests on: **a consistent
fingerprint sustains the session, but it does not license you to hammer the feed.** If
your poll asks for updates faster than a human could possibly watch, the request rate
is itself the signal, no matter how clean the browser looks. The fix is to match the
natural cadence. A live score updates on the order of every ten to thirty seconds; poll
it on that interval, not ten times a second.

```python
import time
from invisible_playwright import InvisiblePlaywright

# match the update you are watching, do not outrun it
POLL_SECONDS = 20

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/live")

    for _ in range(180):  # ~1 hour at 20s spacing
        score = page.locator("[data-live='score']").inner_text()
        print(time.strftime("%H:%M:%S"), score)
        time.sleep(POLL_SECONDS)
```

We learned the sharp edge of this on our own gates: a test that read a scoring endpoint
in a tight loop produced a velocity flag, and the flag belonged to the harness, not the
browser. The identity was clean and the request rate still gave it away. When the poll
is the load-bearing part, shape it deliberately, which is the whole subject of
[rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md).

## Conclusion

A sports page rewards a scraper that respects its structure. Read the feed instead of
the rendered widget so every update comes stamped with its own time. Click each stat
tab and wait for the request the click fires, because unclicked panels never load.
Route live and historical data to separate parsers, because they are separate formats.
And hold the long session on one stable seed-pinned identity while polling at the pace a
human watches, because the session length is the exposure and the request rate is the
tell.

Do that and a match is a clean, ordered stream of data. Scrape it like a static page
and you get one stale number and a lot of empty panels.

## Short answers to the questions that lead here

**The score keeps changing between reads. How do I get a consistent value?** You do not
want a consistent value, you want every value with its timestamp. Subscribe to the
WebSocket or the polling XHR with `page.on("websocket", ...)` or `page.on("response",
...)` and record each frame as it arrives, instead of sampling the DOM on a timer.

**Why is the box score empty when I parse the HTML?** Because that panel loads its own
XHR only when its tab is clicked. Click the tab, wait for the response with
`expect_response`, then parse.

**My live selector returns nothing after the match ends. Why?** The finished match uses
a different DOM than the live widget. Detect the format from the page and route to a
parser built for the historical table.

**Can I just poll the score every second?** You can, and it is the fastest way to get
throttled. A request rate no human could produce is a signal on its own. Match the
natural update interval, roughly ten to thirty seconds.

**Do I need a new identity for each match?** No. A drifting fingerprint inside one long
session is a worse tell than a stable one. Pin a `seed` so the session looks like one
consistent visitor for its whole length.

**How do I reproduce a run that got blocked?** Log the seed and reuse it. The same seed
gives the same machine every time, so a failed capture can be replayed exactly rather
than hoping the next random draw looks the same.

## Sources

- The real `invisible_playwright` API used throughout: `InvisiblePlaywright(seed=...)`
  returns a standard Playwright `Browser`, and the seed pins every fingerprint surface
  across runs. See [Quickstart](quickstart.md) and [Configuration](configuration.md).
- Standard Playwright event and waiting APIs:
  [`page.on("websocket")`](https://playwright.dev/python/docs/network#websockets),
  [`page.on("response")`](https://playwright.dev/python/docs/network#network-events),
  [`page.expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response),
  `page.wait_for_selector`, read from their documented behaviour.
- This project's release gates, including the velocity flag raised by a poll that read
  a scoring endpoint faster than a human would, which turned out to be the test harness
  rather than the browser.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the feed directly, and [rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for shaping the poll that holds the session.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The velocity flag on our
own poll was ours before it was advice.*
