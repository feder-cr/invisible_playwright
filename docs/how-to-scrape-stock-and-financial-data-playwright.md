---
title: "How to scrape stock and financial data with Playwright"
description: "Scrape stock fundamentals from tables, capture the live quote stream at the WebSocket or polling XHR, and read price history from the feed with Playwright."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 44
---


# How to scrape stock and financial data with Playwright

A financial quote page is three different data sources wearing one layout, and the
mistake almost everyone makes is scraping all three the same way. The fundamentals sit
in a table and hold still. The live price does not live in the DOM in any stable sense:
it streams in over a WebSocket or a polling request and the number you read flickers
between two reads a second apart. The price history is not text at all, it is drawn onto
a canvas.

Scrape each of the three at the layer where it actually is, and the job goes from flaky
to boring. This page shows where each layer is, the code that reads it, and the one
honest limit: the chart carries no numbers you can extract, so the series only ever comes
from the feed underneath it.

| Data on the page | Where it actually lives | How to read it | Reliable |
|---|---|---|---|
| Fundamentals (market cap, P/E, dividend yield, 52-week range) | A table in the first paint | Read the DOM directly | Yes, it holds still |
| Live price | A WebSocket stream or a polling request | Listen on the `websocket`/`framereceived` or `response` event | Yes, read at the wire |
| Price history | An array the chart was drawn from | Capture the history request | Yes, read at the feed |
| The chart drawing itself | Pixels on a `<canvas>` | Not recoverable, there are no numbers in it | No |

## Why the price in the DOM is a moving target

Open a quote page, read the last-price element, read it again 500 ms later, and you will
often get two different strings. That is not a bug in your selector. The page is holding
an open connection and repainting that node every time a tick arrives, so the DOM value
is a snapshot of whenever your read happened to land, and two reads straddle an update.

Scraping that node in a loop gives you a jittery, gappy series with no timestamps you can
trust, because the timestamp you would attach is your read time, not the tick time. You
are sampling a stream through a keyhole.

The data you actually want is the stream itself, and Playwright can see it directly. The
same is true of the chart: what looks like a line of prices is a `<canvas>` the page drew
from an array it fetched, and you want that array, not the pixels.

## Read the fundamentals from the tables, because they hold still

Fundamentals (market cap, P/E, dividend yield, the 52-week range) are rendered once into
a table and do not move. This is the one part of the page you can read straight from the
DOM without fighting anything. Switching from stock Playwright is a two-line change and
every method below is the ordinary Playwright API:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/quote/EXMPL", wait_until="domcontentloaded")

    fundamentals = {}
    for row in page.query_selector_all("table.key-stats tr"):
        cells = row.query_selector_all("td, th")
        if len(cells) >= 2:
            label = cells[0].inner_text().strip()
            value = cells[1].inner_text().strip()
            fundamentals[label] = value

    print(fundamentals)
```

`wait_until="domcontentloaded"` is deliberate: the table is in the first paint, so you do
not need to wait for the live-quote socket to connect before you read it. The values come
back as locale-formatted strings ("1,234.56", a currency glyph, "2.3%"); converting them
to numbers is its own step, covered at the end.

## Capture the quote stream at the WebSocket, not the DOM

Instead of polling a node that flickers, subscribe to the source. Playwright raises a
[`websocket`](https://playwright.dev/python/docs/network) event for every socket the page
opens and a
[`framereceived`](https://playwright.dev/python/docs/api/class-websocket#web-socket-event-frame-received)
event for every frame that arrives on it, with the real payload and Playwright's own
receive time. That is a clean, timestamped tick series that owes nothing to your read
timing:

```python
import json, time

ticks = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_websocket(ws):
        def on_frame(payload):
            ticks.append((time.time(), payload))
        ws.on("framereceived", on_frame)

    page.on("websocket", on_websocket)
    page.goto("https://example.com/quote/EXMPL")

    # let the stream run; every tick the page receives, you receive
    page.wait_for_timeout(60_000)

print(len(ticks), "frames captured")
```

Each `payload` is whatever the page's own client receives, usually a small JSON object
per tick that you `json.loads`. You are reading the same bytes the page's JavaScript
reads, before it formats them into the flickering DOM node, so there is no parsing of
rendered text and no lost updates.

Some portals poll a plain request on an interval instead of holding a socket open. Same
idea, different event: listen on
[`response`](https://playwright.dev/python/docs/network) and keep the ones from the quote
endpoint.
The general technique, including how to tell the polling calls apart from everything else
the page fetches, is in
[how to capture XHR and API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md):

```python
quotes = []

def on_response(response):
    req = response.request
    if req.resource_type == "xhr" and "/api/quote" in response.url:
        try:
            quotes.append(response.json())
        except Exception:
            pass  # not every matching response is JSON

page.on("response", on_response)
```

Either way, you have moved the capture point from the rendered value to the wire, and the
jitter disappears.

## Get the price history from the feed, not the chart

The chart is the trap. It looks like the richest data on the page, and it is the only
part you cannot read. The line is painted onto a `<canvas>`, and a canvas exposes pixels,
not numbers: there is no text to select, no element per data point, nothing `inner_text`
can reach. A screenshot of it is an image, and on this build even those pixels carry a
per-session noise layer by design, which is
[why a Playwright screenshot can come back looking noisy](playwright-screenshot-returns-noise.md)
and [why the canvas fingerprint is not stable across identities](canvas-fingerprint-noise.md).

The number series the chart was drawn from arrived over the network as an array, usually
one request for a range of candles when the page loads or when you switch the range
control. Capture it exactly like the polling quotes above, keyed on the history endpoint:

```python
history = []

def on_response(response):
    if response.request.resource_type == "xhr" and "/api/history" in response.url:
        try:
            history.append(response.json())   # list of OHLC rows, with epoch times
        except Exception:
            pass

page.on("response", on_response)
page.goto("https://example.com/quote/EXMPL")

# click the range you want; the click triggers the fetch you are listening for
page.click("button[data-range='1Y']")
page.wait_for_timeout(3000)
```

This is the honest limit stated plainly: there is no way around going to the feed. The
canvas holds no recoverable numbers, so the only source of the historical series is the
request the chart itself consumed. Read that request and you have the exact data the page
had; try to read the drawing and you have nothing.

## Why a stable identity is what keeps a long session alive

A stable browser identity is what keeps a long capture session alive, because a quote
capture is not a fetch-and-leave: to collect a stream you hold the page open for minutes or
hours, and a long-lived polling or socket session from one identity is precisely the pattern
finance portals watch. A headless or drifting fingerprint that would sail through a single page
load gets throttled part-way through a long one, and your stream goes quiet with no error
to catch.

The defense is a single, real, consistent identity that lasts the whole session. That is
what the `seed` argument buys: pass one and every fingerprint field (GPU, audio, fonts,
screen) is derived once and stays fixed, so the same returning device
holds the connection for hours instead of looking like a new machine on every reconnect.

```python
# one pinned identity for the entire capture window
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("websocket", on_websocket)
    page.goto("https://example.com/quote/EXMPL")
    page.wait_for_timeout(4 * 60 * 60 * 1000)  # a four-hour capture, one device
```

The fingerprint is only half of it. A quote portal cross-checks your browser timezone and
locale against your exit address, and a session whose clock says one continent while the
IP says another is a mismatch that shows up faster on a long session than a short one. Let
the timezone auto-derive from the egress IP rather than pinning it by hand; the surfaces
that have to agree are in
[when the timezone does not match the proxy](timezone-proxy-mismatch.md). A stable
fingerprint plus a coherent geo is what turns a four-hour capture from a gamble into a
routine.

## Parsing the locale-formatted numbers

The strings you pulled from the table and the wire are formatted for humans: thousands
separators, currency glyphs, percent signs, sometimes a comma for the decimal point. Strip
to the numeric core before converting, and know which separator convention the page uses:

```python
import re

def to_number(text):
    core = re.sub(r"[^0-9.,\-]", "", text.strip())  # drop currency, spaces, %, letters
    if not core:
        return None
    # US-style: comma groups thousands, dot is the decimal
    core = core.replace(",", "")
    return float(core)

to_number("$1,234.56")   # -> 1234.56
to_number("2.3%")        # -> 2.3
```

If the portal serves a locale that uses a comma as the decimal separator ("1.234,56"),
swap the two replacements. The feed payloads captured from the WebSocket and the history
request usually sidestep this entirely, because they carry raw numeric JSON rather than
formatted display strings, which is one more reason to prefer the wire over the rendered
text.

## Conclusion

A financial page is not one scrape, it is three. Read the fundamentals from the table
where they sit still, capture the live price at the WebSocket or polling request instead
of the flickering DOM node, and pull the history from the feed the chart was drawn from,
because the canvas has no numbers to give back. Hold it all together with one seeded
identity so the long capture reads as a single returning device, and let the timezone
follow the exit. Do that and the flakiness people blame on the site turns out to have been
scraping the wrong layer the whole time.

## Short answers to the questions that lead here

**Why does the price I scrape keep changing between reads?** Because it is streamed, not
static. The page repaints that node on every tick, so your read catches whatever the value
was at that instant. Capture the stream instead of the node.

**How do I get the live quote reliably?** Listen on the `websocket` event and its
`framereceived` frames, or on the `response` event for a polling endpoint. Both give you
the payload the page itself receives, with a real timestamp.

**Can I extract the numbers from the price chart?** No. The chart is drawn on a canvas,
which exposes pixels and not text. The numeric series only comes from the request the
chart was built from, so capture that request.

**Why does my long capture session get throttled or blocked partway through?** A long-lived
polling session from a headless or shifting fingerprint is exactly the pattern these
portals watch for. A single stable identity across the whole session is what avoids it.

**How do I keep the same identity for hours?** Pass a `seed`. Every fingerprint field is
derived from it once and stays fixed for the life of the session, so you look like one
returning device rather than a new one on each reconnect.

**Do I need to handle the number formatting?** For text read from the DOM, yes: strip the
currency and separators and know whether the decimal is a dot or a comma. The captured feed
payloads are usually raw JSON numbers and need none of it.

## Sources

- Playwright's [`websocket` / `response`](https://playwright.dev/python/docs/network) and
  [`framereceived`](https://playwright.dev/python/docs/api/class-websocket#web-socket-event-frame-received)
  events, read from the upstream API documentation rather than from a wrapper.
- This project's own measurements on long-lived capture sessions, where a fixed seeded
  identity outlasts a drifting one, and the canvas surface notes behind why a chart cannot
  be read as text.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the response-interception technique in full, and
[the checklist for when Playwright is detected on one site](playwright-detected-as-bot.md)
for the order to debug a session that starts failing mid-capture.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The three-layers-in-one-page
mistake is one I made before I learned to watch the network tab instead of the DOM.*
