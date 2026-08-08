---
title: "How to scrape map-based local results with Playwright"
description: "Scrape map-based local results with Playwright: drive the viewport, capture the bounds-keyed XHR per step, and tile an area through an in-region proxy."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 49
---


# How to scrape map-based local results with Playwright

A map-based results list is not a page of rows you can paginate. It is a projection of
whatever falls inside the current latitude and longitude bounding box, and the list only
changes when the box changes. Pan the map, zoom the map, and the box moves, which fires a
request keyed to the new bounds and returns a new set of results. Scroll the list without
moving the map and nothing new arrives, because the list is not the query. The viewport
is the query.

That single fact reorganises the whole job. You are not walking pages, you are walking a
grid of bounding boxes across an area, capturing the request each box fires, and stopping
when the boxes overlap enough to have covered everything. This page is how to do that with
stock Playwright, plus the one stealth detail that decides whether the map draws at all
and whether it draws the right city.

## How map-based results actually load

A map-based results list loads as three synced but separate layers, all repainted from one
bounds-keyed request each time the view moves: the map tiles, the pins, and the DOM list.
Three moving parts, synced but separate:

- **The map tiles** are drawn on a WebGL canvas. The browser asks the GPU to rasterise
  vector or raster tiles for the current view. No usable GPU, no tiles.
- **The pins** are markers positioned over the canvas at screen coordinates derived from
  each result's real coordinates.
- **The list** is a set of DOM rows, one per result in view, kept in sync with the pins by
  shared IDs but rendered by completely separate code.

When you move the view, the front end computes the new bounds, issues an XHR (or fetch)
whose parameters are the corners of the box and the zoom level, and repaints all three
from the response. The response is the thing worth capturing: it is structured data, it
already contains every field the pins and rows are built from, and it is keyed to bounds
you control. Scraping the rendered rows instead means re-deriving what the response
already handed you, and missing anything the row template chose not to show.

So the plan is: set the bounds, let the bounds-keyed request fire, capture its response,
move to the next box, repeat.

## Why the fingerprint has to match the region you are mapping

Two independent things have to be true before a single tile or result appears, and both
are easy to get wrong from a server.

**The map needs a real GPU renderer string to draw.** A WebGL map that reads a software
rasteriser renderer will often fall back to a blank or degraded canvas, and some map
libraries refuse to initialise at all. This is the datacenter tell described in the
[detection checklist](playwright-detected-as-bot.md): a headless browser on a server with
no graphics hardware reports a software renderer, and the map is the first thing to break
on it. A build that reports a plausible hardware GPU string, consistently, is what lets
the WebGL context come up and the tiles rasterise. The
[WebGL renderer strings](webgl-renderer-strings.md) page covers why the string alone is
not enough and has to agree with what the pixels actually do.

**The results follow the exit IP's geography.** Local results are geo-gated. The front end
resolves your region from the request IP and from browser signals like timezone and
locale, and it returns the area around wherever it thinks you are. If your proxy exits in
one country and your browser timezone says another, you get a mismatch that both flags the
session and, worse for this job, returns the wrong city's results. The proxy has to sit in
the region you intend to map, and the browser's timezone has to match that exit, which is
exactly the failure the [timezone and proxy mismatch](timezone-proxy-mismatch.md) page is
about. This is the honest caveat of the whole approach: the fingerprint makes the map
render, but it cannot relocate you. Geography comes from the exit.

With `invisible_playwright` both of these come from one seed. A fixed seed gives the same
hardware GPU string every run, so the map draws the same way every run, and the timezone
is auto-derived from the proxy's egress IP by default so it agrees with where you exit
without your having to pin it by hand.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# seed fixes the WebGL renderer so the map draws identically every run;
# timezone auto-derives from the proxy exit so it matches the region.
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/map")
    page.wait_for_selector("canvas")  # the WebGL surface came up
```

A quick demonstration of why the renderer matters: on a build reporting a software
rasteriser the map canvas came up blank and no tile request fired; on the same page with a
hardware GPU string the tiles rasterised and the first bounds-keyed request went out
within a second. The blank canvas was not a code bug, it was the map refusing to draw
without a GPU.

## Driving the map by setting bounds and zoom

You move the viewport, not the scrollbar. There are two reliable ways to move it.

Most map front ends encode the view in the URL, as a fragment or query like
`#<zoom>/<lat>/<lng>` or `?bounds=<west>,<south>,<east>,<north>`. Setting that and
reloading (or letting the client react to the hash change) moves the box deterministically,
which is ideal for a grid walk because each step is a plain URL.

```python
def view_url(west, south, east, north, zoom):
    return f"https://example.com/map?bbox={west},{south},{east},{north}&z={zoom}"

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto(view_url(-0.14, 51.49, -0.11, 51.51, 14))
    page.wait_for_selector(".result-row")
```

When the view lives only in the map object and not the URL, drive the object through the
page. Most libraries expose a global map instance with a method to fit a bounding box or
set centre and zoom. `page.evaluate` reaches it, and standard Playwright waiting handles
the repaint:

```python
def go_to_box(page, west, south, east, north):
    page.evaluate(
        """([w, s, e, n]) => {
            // window.__map is whatever the page names its map instance
            window.__map.fitBounds([[w, s], [e, n]]);
        }""",
        [west, south, east, north],
    )
    page.wait_for_load_state("networkidle")
```

Either way the principle holds: the bounds you set are the query you run, and every
distinct box you set is a distinct result set you can collect.

## Capturing the bounds-keyed XHR per viewport step

Reading the rendered rows works, but the request that feeds them is cleaner and complete.
Attach a response listener before you move, so the request that fires on the move is
caught. Standard Playwright, nothing wrapper-specific:

```python
import json

def collect_area(page, boxes):
    captured = []

    def on_response(response):
        url = response.url
        # match the endpoint that carries the bounding box parameters
        if "/api/search" in url and "bbox" in url:
            try:
                captured.append(response.json())
            except Exception:
                captured.append({"url": url, "body": response.text()})

    page.on("response", on_response)

    for (west, south, east, north) in boxes:
        go_to_box(page, west, south, east, north)
        page.wait_for_timeout(800)  # let the bounds request settle

    page.remove_listener("response", on_response)
    return captured
```

`page.on("response", ...)` and `page.remove_listener` are the stock, documented
[Playwright API](https://playwright.dev/python/docs/api/class-page); `response.json()`
and `response.text()` are the [Response API](https://playwright.dev/python/docs/api/class-response).

If the endpoint is hard to identify, log every response URL for one manual pan and read
which one carries the box corners as parameters. The general technique, including matching
by method and payload rather than URL and handling paginated map responses, is on the
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) page.
One habit from the [testing notes](how-to-test-bot-detection.md) applies here too: take a
screenshot after a step and confirm the pins actually moved, because an empty capture and
a map that never repainted look identical in a text log.

## Tiling an area to cover it

A single viewport shows one box. Covering a city, a region or a country means stepping the
box across the area in a grid, with two constraints that decide whether coverage is real.

**Overlap the boxes.** Pins near an edge can be clipped or deduplicated against the
neighbouring view, so step by less than the full box width. An overlap of ten to twenty
percent between adjacent boxes is a reasonable default.

**Respect the per-view result cap.** Map endpoints usually return at most N results per
box regardless of how many exist inside it. If a box comes back at exactly the cap, it is
almost certainly truncated, and the fix is to zoom in one level and split that box into
four smaller ones. This makes coverage adaptive: dense areas get subdivided, empty ocean
does not.

```python
def grid(west, south, east, north, step_lng, step_lat, overlap=0.15):
    boxes, y = [], south
    dy = step_lat * (1 - overlap)
    dx = step_lng * (1 - overlap)
    while y < north:
        x = west
        while x < east:
            boxes.append((x, y, x + step_lng, y + step_lat))
            x += dx
        y += dy
    return boxes

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/map")
    page.wait_for_selector("canvas")

    boxes = grid(-0.20, 51.45, -0.05, 51.55, 0.03, 0.02)
    results = collect_area(page, boxes)

    seen, rows = set(), []
    for payload in results:
        for item in payload.get("results", []):
            if item["id"] not in seen:
                seen.add(item["id"])
                rows.append(item)
    print(f"{len(rows)} unique results across {len(boxes)} viewport steps")
```

Deduplicate by the stable ID in the response, not by list position, because overlapping
boxes will return the same result more than once by design. Keep the seed fixed across the
whole crawl so every box is fetched by the same identity and a failure at box 400 is
reproducible at box 400, which is the whole point of a seed-stable fingerprint when a run
is long. If you need to map several regions, change the proxy exit per region and let the
timezone follow it, rather than mapping every region through one exit and getting one
city's results back for all of them. The
[geotargeted content](how-to-scrape-geotargeted-content-playwright.md) page covers pairing
exits to regions.

## Conclusion

Map-based scraping is viewport scraping. The list is a projection of a bounding box, new
results arrive only when the box moves, and the request that fires on the move is the
clean, complete source. Drive the box by URL or by the map object, capture the
bounds-keyed response per step, and tile the area with overlap and cap-aware subdivision to
cover it. The two things that decide whether any of it works are not in your crawl logic at
all: the WebGL renderer has to be real enough for the map to draw, and the proxy has to
exit in the region you are mapping with a timezone that agrees. Get those from one seed and
one in-region exit, and the rest is a grid walk.

## Short answers to the questions that lead here

**Why do new results not load when I scroll the list?** Because the list is not the query.
It is a projection of the current map bounds, and it only changes when the map moves. Pan
or zoom, do not scroll.

**Should I scrape the rendered rows or the XHR?** The XHR. It is structured, complete, and
keyed to the bounds you set, so the rendered rows are just a lossy view of it.

**Why is the map canvas blank in my automation?** Usually a software WebGL renderer. A map
needs a real GPU string to rasterise tiles, and a server without graphics hardware reports
a software rasteriser, which many map libraries will not draw on.

**Why do I get the wrong city's results?** Local results follow the exit IP and the browser
timezone. If the proxy exits elsewhere, or the timezone disagrees with the exit, you get
the region the site thinks you are in, not the one you meant to map.

**How do I cover a whole area, not one screen?** Step a grid of overlapping bounding boxes
across it, capture each box's response, and subdivide any box that returns exactly the
per-view cap by zooming in one level.

**How do I avoid double-counting overlapping boxes?** Deduplicate on the stable result ID
in the response, never on list position, because overlap is deliberate and returns
repeats.

## Sources

- This project's release gates for WebGL renderer behaviour, including the difference
  between a build that reports a hardware GPU string and one that falls back to a software
  rasteriser, and what that does to a WebGL map.
- The wrapper's documented API for seed, proxy and auto-derived timezone, used exactly as
  the quickstart and configuration pages describe it.
- Direct observation of map front ends firing a bounds-keyed request on every viewport
  change, read from the network panel rather than inferred.
- The [Playwright Python API reference](https://playwright.dev/python/docs/api/class-page)
  for `page.on("response")`, `page.evaluate`, and `page.wait_for_selector`, and the
  [Response API](https://playwright.dev/python/docs/api/class-response) for
  `response.json()`/`response.text()` - all stock, documented Playwright, used exactly as
  shown.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for matching and reading the bounds request, [scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md)
for pairing exits to regions, and [WebGL renderer strings](webgl-renderer-strings.md) for
why the map needs a real one.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The blank-canvas-on-a-
software-renderer failure above is one we watched happen before the seed-stable GPU string
fixed it.*
