---
title: "How to scrape cryptocurrency prices with Playwright"
description: "Scrape live cryptocurrency prices with Playwright by reading WebSocket frames, not the flickering DOM node, and hold the feed open with a stable identity."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 51
---


# How to scrape cryptocurrency prices with Playwright

To scrape live cryptocurrency prices with Playwright, subscribe to the page's
WebSocket and read each price from the frames it delivers, not from the DOM node: a
live ticker overwrites that node several times a second, so the value you read back is
stale the instant you have it. Handle the ranked market-cap table as a separate
scroll-and-collect job, and hold the connection open with a stable browser identity so
that a dropped-and-reconnected feed reads as one steady client rather than a churn of
new devices.

Before writing a line of code, though: if the exchange or aggregator you are reading
publishes a public data API, use it. It is almost always the right tool, it is faster,
and it will not break the next time the page markup changes. This guide is for the
other case, the one where the numbers you need only ever appear in the rendered page,
and you have to get them out of a live browser without capturing garbage.

That case is harder than it looks, for a reason specific to prices: they do not sit
still. A crypto ticker updates many times a second over a persistent WebSocket, and
the number you see in the DOM is the tail end of a stream that has already moved on by
the time your `text_content()` call returns. This page is about reading the stream
rather than the tail, paging the market table as a separate job, and keeping the
connection alive long enough to matter.

## Why the DOM value is a race you lose

Reading the DOM node is a race you cannot win: a live ticker replaces that node
several times a second, so between the frame that paints a value and the moment your
read resolves, the WebSocket has already delivered another tick. The fix is to read
the frames, not the node.

Open a live prices page and watch one cell. The number changes several times a second,
and on most sites the node briefly changes color on each update - green up, red down -
before settling back. That flash is a CSS transition firing on every write, which
tells you exactly how often the value is being replaced.

Now think about what a scraper does. You locate the node, you read its text, and
between the frame that painted the value and the moment your read resolves, the
WebSocket has delivered another tick and the framework has swapped the text again. You
did not read "the price". You read whichever value happened to be mounted during your
call, which is stale the instant you have it.

A screenshot is worse, not better. It captures a single compositor frame, and if that
frame lands mid-transition you get a half-recolored cell or a number caught between two
renders. Screenshots have [their own separate gotchas](playwright-screenshot-returns-noise.md)
on top of that, but the core problem here is timing: a shutter is the wrong instrument
for a value that changes faster than you can point it.

The DOM is a rendering of the data. The data is in the WebSocket frames. Read the
frames.

## Capture the price feed with Playwright

Stock Playwright exposes the WebSocket layer directly, and `invisible_playwright`
returns a real Playwright `Browser`, so every method below is the upstream API with no
wrapper to learn. You subscribe to the socket, then to each frame it receives, and
parse the payload yourself:

```python
import json
from invisible_playwright import InvisiblePlaywright

ticks = []

def handle_ws(ws):
    # ws is a playwright WebSocket. framereceived fires for every inbound frame.
    def on_frame(payload):
        # payload is a str (text frame) or bytes (binary frame)
        if isinstance(payload, (bytes, bytearray)):
            return  # handle binary protocols separately if the site uses them
        try:
            msg = json.loads(payload)
        except ValueError:
            return
        # shape is site-specific; inspect a few frames to learn the schema first
        if "price" in msg and "symbol" in msg:
            ticks.append((msg["symbol"], msg["price"]))
    ws.on("framereceived", on_frame)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("websocket", handle_ws)
    page.goto("https://example.com/markets")
    # let the feed run; every inbound tick lands in `ticks` as it arrives
    page.wait_for_timeout(10_000)

print(f"captured {len(ticks)} ticks")
```

The [`framereceived`](https://playwright.dev/python/docs/api/class-websocket) handler runs for real frames as the browser receives them, so you
are recording every tick the page received, not sampling the DOM after the fact. You
never fight the color flash, because you never touch the node. If you would rather work
at the HTTP layer for the initial snapshot the page loads before the socket opens, the
same subscription pattern applies to responses, covered in
[capturing XHR and fetch API responses](how-to-capture-xhr-api-responses-playwright.md).

One practical note: inspect a handful of frames before you write the parser. Feeds
carry heartbeats, subscription acks, and depth updates mixed in with price ticks, and
the schema differs per site. Log raw payloads first, learn the shape, then filter.

## Page the market-cap table separately

The ranked table - top coins by market capitalization - is a different problem from the
live ticker, and mixing the two is a common mistake. The table does not stream. It
paginates or virtualizes, and it is loaded on demand.

Two things bite here. First, virtualized lists only mount the rows currently in view,
so `query_selector_all` on the table returns the dozen rows on screen, not the
hundreds you expected. You have to scroll and collect incrementally, letting new rows
mount as old ones unmount:

```python
seen = {}
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/markets")
    page.wait_for_selector("[data-row]")

    last_count = -1
    while len(seen) != last_count:
        last_count = len(seen)
        for row in page.query_selector_all("[data-row]"):
            symbol = row.get_attribute("data-symbol")
            if symbol and symbol not in seen:
                seen[symbol] = row.inner_text()
        # scroll one viewport and let the next band of rows mount
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(400)

print(f"collected {len(seen)} rows")
```

Second, precision varies per asset. A coin priced in the tens of thousands shows two
decimals; a micro-cap token shows eight or more, and some cells switch to scientific
notation. Do not assume a fixed decimal count when you parse, and keep the raw string
alongside any float you derive, because the float will silently lose the low digits on
the small-price assets that most need them.

Because pagination and virtualization both depend on content finishing its load before
you read, this is exactly the situation where [waiting for the page to settle
properly](how-to-wait-for-page-load-playwright.md) rather than sleeping a fixed number
of seconds is the difference between a full table and a truncated one.

## Why a stable identity keeps the feed open

A stable, seed-reproducible identity keeps a long-lived feed open because a server can
meter connections, and a client that presents the same device on every reconnect reads
as one returning subscriber rather than a churn of throwaway clients hitting the socket.
It does not make you invisible; it makes a long connection look like the steady client
it actually is.

A one-shot table scrape is a short visit. A continuous price capture is the opposite:
it is a single connection you want to hold open for minutes or hours, receiving frames
the whole time. That changes what the site sees.

A long-lived WebSocket from one client is a resource the server can meter. If the
connection drops and your scraper reconnects, and each reconnect looks like a brand new
and slightly different device - a different GPU string, a different canvas hash, a
different screen - then a feed that should look like one steady subscriber instead
looks like a churn of throwaway clients hammering the socket endpoint. That pattern is
cheap to throttle.

This is where a seed-reproducible identity earns its place. `invisible_playwright`
derives every fingerprint surface from one seed, so passing `seed=42` gives you the
same machine on every launch:

```python
# reconnect logic: same seed, so the site sees the same steady client each time
def run_capture(seed=42):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.on("websocket", handle_ws)
        page.goto("https://example.com/markets")
        page.wait_for_timeout(60_000)

# a dropped connection reconnects as the SAME device, not a new suspicious one
for _ in range(retries):
    try:
        run_capture(seed=42)
    except Exception:
        continue
```

The point is not that a fingerprint makes you invisible. The point is consistency: a
steady subscriber that presents the same identity across reconnects reads as one
returning client, which is what a real person watching prices actually is. A fresh
random device on every drop reads as evasion, and a socket endpoint is a natural place
for a site to notice it. If you do start getting a throttled or different feed than a
human sees, [work the detection checklist in order](playwright-detected-as-bot.md)
rather than assuming it is the browser.

## A measurement worth doing yourself

Here is the honest way to see the DOM-versus-frame gap, and it doubles as a test you
can keep.

Run both readers against the same page for a fixed window. Poll the DOM cell on a tight
loop and record every distinct value you see; at the same time, record every price tick
that arrives on the WebSocket. On a fast-moving pair, the frame recorder consistently
captures several times as many distinct prices as the DOM poller, because the DOM only
ever shows the value that survived long enough to paint, and many ticks are overwritten
before a single frame renders them. The DOM is lossy by construction; the frames are
the full record.

Assert on presence, not absence: a passing run is one where the frame count is high and
the parsed prices are monotonic in time and within a sane band, not merely one where
"no error was thrown". A capture that silently recorded zero frames because the socket
schema changed will pass an absence check and fail you in production.

## Conclusion

Scraping live crypto prices from a rendered page comes down to reading the right layer.
The DOM node is a lossy, flickering view of a stream, and a screenshot is a single
frame of that flicker; the WebSocket frames are the actual data, and stock Playwright
lets you subscribe to them directly. Treat the market-cap table as a separate paging
and virtualization job with per-asset precision, and hold the feed open with a stable
identity so a long connection reads as one steady client rather than a churn of new
devices. And before any of it, check whether a public data API exists, because when one
does, none of this is necessary.

## Short answers to the questions that lead here

**Why does the price I scrape not match what is on screen?** Because you read the DOM
node, which is replaced several times a second by the WebSocket feed. By the time your
read resolves the value has already changed. Read the frames instead.

**Can I just screenshot the ticker?** No. A screenshot captures one compositor frame,
which may land mid-update on a recoloring cell, and it is far slower than the value
changes. It is the wrong instrument for a moving number.

**How do I read the WebSocket in Playwright?** Subscribe with `page.on("websocket", ...)`
and then `ws.on("framereceived", ...)`, parsing each payload yourself. It is standard
Playwright, and `invisible_playwright` returns a real Playwright `Browser`.

**Why does my market table come back with only a dozen rows?** The list is virtualized,
so only visible rows are mounted. Scroll and collect incrementally until the count stops
growing rather than reading once.

**Should I use a public API instead?** Almost always, yes. If the data is exposed
through a documented API, that is the right tool. This guide is only for when the numbers
appear solely in the rendered page.

**Does a fingerprint let me capture forever without limits?** No. A stable identity keeps
a long connection looking like one steady client so it is less likely to be throttled,
but it does not remove rate limits or make you exempt from a site's terms.

## Sources

- [Stock Playwright's WebSocket class](https://playwright.dev/python/docs/api/class-websocket),
  used unchanged; `invisible_playwright` returns a real Playwright `Browser`, so these are the
  upstream methods.
- This project's own measurements comparing distinct prices captured from `framereceived`
  frames against a tight DOM poll over the same window, where the frame recorder captures
  several times as many distinct values.

**See also:** [capturing XHR and fetch API responses](how-to-capture-xhr-api-responses-playwright.md)
for the HTTP-layer version of the same subscribe-then-parse pattern, and
[waiting for the page to settle](how-to-wait-for-page-load-playwright.md) for getting a
full table instead of a truncated one.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The price-is-a-race problem
is one we measured before we wrote about it.*
