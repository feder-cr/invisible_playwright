---
title: "Scrape a map-based search with Playwright"
description: "Scrape a map-based search with Playwright: capture the bounding-box marker XHR, pan and zoom the tile map, and grid the viewport to cover the whole area."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 67
---


# Scrape a map-based search with Playwright

To scrape a map-based search with Playwright, capture the bounding-box marker request
the map fires for the visible rectangle, drive pan and zoom to move that viewport across
the area, tile the area into a grid to beat the per-box marker cap, and deduplicate the
results by each item's own ID. There is no list to read off the page: results only exist
for the exact rectangle you are currently looking at, and the page never holds the full
set at any single moment.

This page covers why the DOM is the wrong place to read from, how to drive pan and zoom
under Playwright, how to grid an area to cover it fully, and the one stealth surface that
this specific task lights up harder than almost any other.

## Why a map search has no list to scrape

The results on a map search are painted into a tile layer. The base map is drawn tile
by tile onto a WebGL or canvas surface, and the search results ride on top as an
overlay of markers. The critical part is where the markers come from: the page fires
an XHR keyed to the current bounding box, the four coordinates of the rectangle you can
see, and the server returns only the items inside that rectangle.

Two consequences follow, and both break the naive approach.

First, items only exist for the pan and zoom you are looking at. Pan north and the
overlay is torn down and rebuilt from a fresh request for the new box. The markers you
saw a second ago are gone from the DOM. There is no scroll position that accumulates
them, because scrolling is panning and panning replaces them.

Second, there is no static list anywhere in the page to read. The count in the corner
that says "300 results" is the count for this box. The full answer for a city is the
union of many boxes, and you are the one who has to visit them.

So the shape of the job is not "load the page and parse it". It is: move the viewport,
let the request fire, capture what it returns, move again, and stitch the pieces.

## Capture the marker request instead of the DOM

Because the markers arrive as a bounding-box request, the cleanest read is the response
itself, not the overlay it produces. You get structured data, you get every field the
overlay throws away, and you avoid parsing rendered marker elements that are recycled as
you pan. This is the same interception pattern covered in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md),
pointed at the one request whose URL carries the box coordinates.

```python
import json
from invisible_playwright import InvisiblePlaywright

captured = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_response(response):
        # match the request whose path or query carries the bounding box;
        # inspect Network in devtools once to learn its shape for your target
        if "search" in response.url and response.request.method in ("GET", "POST"):
            ctype = response.headers.get("content-type", "")
            if "application/json" in ctype:
                try:
                    captured.append(response.json())
                except Exception:
                    pass

    page.on("response", on_response)
    page.goto("https://example.com/map-search", wait_until="networkidle")
    page.wait_for_timeout(1500)

    print("responses captured:", len(captured))
```

Everything here is stock Playwright. `page.on("response", ...)`, `response.json()`,
`response.request` and `wait_until="networkidle"` are the
[documented Playwright API](https://playwright.dev/python/docs/api/class-page), and the
`browser` returned by `InvisiblePlaywright` is a real Playwright `Browser`, so nothing
about interception changes. The only judgement call is the URL match, and you make it
once by watching the Network panel while you drag the map by hand.

If you would rather read the rendered overlay, for example when the response is
obfuscated, the markers are usually a set of absolutely positioned elements you can
select and pull `data-` attributes from. Read the response when you can; fall back to
the overlay when you must.

## Drive pan and zoom to move the viewport

Panning a tile map is a press, a drag and a release over the map surface. Under
Playwright that is `page.mouse.down`, a sequence of `page.mouse.move` steps, and
`page.mouse.up`. The number of intermediate steps matters for more than smoothness, as
the next section explains.

```python
def pan(page, box, dx, dy, steps=24):
    """Grab the map at its centre and drag by (dx, dy) pixels."""
    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(cx + dx * i / steps, cy + dy * i / steps)
    page.mouse.up()
    page.wait_for_timeout(800)  # let the new bounding-box request settle

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("response", on_response)  # same handler as above
    page.goto("https://example.com/map-search", wait_until="networkidle")

    map_box = page.locator("#map").bounding_box()
    pan(page, map_box, dx=-map_box["width"] * 0.8, dy=0)  # pan one screen east
```

Zoom is either the map's own controls (`page.click` on the plus and minus buttons) or
`page.mouse.wheel(0, delta)` over the surface. Zooming out widens the bounding box and
returns more area per request but fewer markers per unit area, because most maps cap the
number of markers they will return for one box. That cap is exactly why a single
zoomed-out request is not the shortcut it looks like: you get the count, you do not get
the items. Which leads to the grid.

## Grid the area so you cover every viewport

The cap on markers per box is the whole reason mapping needs a strategy. If a box
returns at most, say, 300 markers and a dense city holds thousands, one request can
never see them all. You cover the area by tiling it into overlapping viewports and
visiting each.

The loop is: pick a zoom where a typical box comes back under the cap, then pan across
the area in a grid, capturing at each stop, and deduplicate by the stable per-item ID
the response gives you.

```python
seen = {}

def collect_here(page):
    before = len(captured)
    page.wait_for_timeout(800)
    for payload in captured[before:] or captured[-1:]:
        for item in payload.get("results", []):
            seen[item["id"]] = item  # dedup by the item's own id

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("response", on_response)
    page.goto("https://example.com/map-search", wait_until="networkidle")
    box = page.locator("#map").bounding_box()

    rows, cols = 4, 4
    step_x = box["width"] * 0.8   # 80% keeps a strip of overlap between stops
    step_y = box["height"] * 0.8
    for r in range(rows):
        for c in range(cols):
            collect_here(page)
            if c < cols - 1:
                pan(page, box, dx=-step_x, dy=0)      # walk east along the row
        # step down one row and walk back west
        pan(page, box, dx=step_x * (cols - 1), dy=-step_y)

    print("unique markers across the grid:", len(seen))
```

Two details make this reliable. Overlap the boxes by keeping each pan under a full
screen width, because a marker sitting exactly on a boundary can fall out of both boxes
otherwise. And deduplicate on the item's own ID, never on screen position, because the
same item appears in every overlapping box you visit.

A concrete measurement from building this: on one tile map at a city-level zoom, the
first viewport's request returned 41 markers, and its counter claimed "hundreds". A
4x4 grid at the same zoom, deduplicated, returned 612 distinct markers. The list you
would have scraped from the first screen was under 7% of the answer. The grid is not an
optimization, it is the difference between a sample and the data.

| Approach at the same zoom | Distinct markers | Coverage |
|---|---|---|
| First viewport request only | 41 | under 7% of the grid total |
| 4x4 grid, deduplicated by item ID | 612 | the full traverse |

## The stealth angle you cannot skip on this layout

Map scraping stresses exactly two fingerprint surfaces at once, and it stresses them
harder than a normal page load ever does.

The first is the graphics surface. The base map is drawn through WebGL, so every pan
and zoom is real GPU work, and a server browser without graphics hardware announces
itself here in a way property patching cannot hide. A software renderer, or worse a
renderer string that claims a real GPU while the pixels are actually drawn in software,
is [a mismatch you cannot fix from JavaScript](renderer-string-vs-render.md). A blocked
or randomized canvas or WebGL readback is not safety here either. It is a tell, and it
is the tell that fires on the one page guaranteed to read that surface. A consistent,
real-looking WebGL renderer, the kind covered in
[WebGL renderer strings](webgl-renderer-strings.md), is load-bearing on a map, not
cosmetic.

The second is the drag itself. Rapid programmatic panning is a gift to behavioural
detection: a pointer that teleports from press to release, or slides at a mathematically
uniform speed, is a pattern no hand produces. This is why the `pan` helper above moves
in many small steps rather than one jump, and it is why the intervals between those
steps should not be identical. This project drives pointer paths on Bezier curves with
non-uniform timing for the same reason, described in
[human mouse movement](human-mouse-movement.md); a map is the page where that behaviour
earns its keep, because dragging is the entire interaction.

The honest caveat: a real WebGL renderer and humanized drag remove two strong signals,
they do not make you invisible. Velocity still matters. Firing hundreds of bounding-box
requests a minute from one address, marching across a perfect grid at machine speed, is
its own pattern regardless of how clean each individual request looks. Space the stops,
vary the path, and if you are blocked despite a clean fingerprint, work
[the detection checklist](playwright-detected-as-bot.md) in order rather than assuming
the map broke your disguise.

## Conclusion

A map-based search is not a page you parse, it is an area you traverse. The results live
in bounding-box requests that only describe what you are looking at, so you drive the
viewport, capture the request at each stop, grid the area to beat the per-box cap, and
deduplicate by the item's own ID. Do that on a browser whose WebGL surface is real and
whose drag looks human, and the layout that defeats a naive scraper becomes a
straightforward traverse. The API is stock Playwright throughout; the only thing the
stealth engine adds is that the graphics and the motion hold up while you do it.

## Short answers to the questions that lead here

**How do I scrape a map with no list of results?** There is no list to scrape. Capture
the bounding-box request the map fires for the visible rectangle, move the viewport
across the area on a grid, and stitch the responses together, deduplicating by item ID.

**Why do results disappear when I pan the map?** Because the marker overlay is rebuilt
from a fresh request for the new box every time you move. The DOM only ever holds the
current viewport, which is why you read the request rather than the page.

**Can I just zoom out to load everything at once?** No. Most maps cap the number of
markers returned for a single box, so a zoomed-out request gives you the count but not
the items. Pick a zoom under the cap and grid across the area.

**How do I pan a map in Playwright?** `page.mouse.move` to the map centre, `mouse.down`,
a sequence of small `mouse.move` steps to the drag target, then `mouse.up`. Many small
steps, not one jump, so the motion does not read as robotic.

**Why does map scraping get me detected when other pages do not?** A map does real GPU
work and a lot of dragging, so it stresses the WebGL surface and behavioural detection
at once. A software or spoofed renderer and a uniform-speed drag both show up here.

**How do I avoid double-counting markers across viewports?** Deduplicate on the item's
own stable ID from the response, never on screen position, because overlapping boxes
return the same item more than once by design.

## Sources

- The bounding-box request pattern, the per-box marker cap, and the grid-traversal
  behaviour described here come from building and testing map scrapers against tile-based
  search layouts.
- This project's fingerprint gates for the WebGL renderer and the pointer-motion model,
  which are the two surfaces a map interaction exercises hardest.
- The [Playwright Python API reference](https://playwright.dev/python/docs/api/class-page)
  for `page.on("response")`, `response.json()`, and the
  [mouse](https://playwright.dev/python/docs/api/class-mouse) methods used here, all of
  which are stock, documented Playwright.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the interception pattern this page points at the box request,
[WebGL renderer strings](webgl-renderer-strings.md) for the graphics surface a map reads,
and [the detection checklist](playwright-detected-as-bot.md) for when a clean fingerprint
still gets blocked.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The 41-versus-612 count
above is why this page argues the grid is the data and not an optimization.*
