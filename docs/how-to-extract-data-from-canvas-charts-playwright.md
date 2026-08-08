---
title: "Extract data from canvas charts with Playwright"
description: "Extract data from a canvas or WebGL chart in Playwright by reading the data XHR or the chart library's JS state with page.evaluate, not the noised pixels."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 72
---


# Extract data from canvas charts with Playwright

**To extract data from a canvas chart with Playwright, read one of the two places the
numbers exist before they are painted: the network response the page fetched to build the
chart, or the chart library's in-memory JavaScript state read with `page.evaluate`. Do not
read the pixels** - a `<canvas>` or WebGL chart is a bitmap with no DOM nodes, so selectors
return nothing, and on a stealth browser the pixels are perturbed on purpose.

You point a selector at a chart and get nothing back. The axis labels are there in the
DOM, maybe a legend, but the bars, the line, the values you actually came for return an
empty list. This is not a timing problem and waiting longer will not fix it.

A chart drawn to a `<canvas>` or through WebGL has no DOM nodes to read. Every bar, point
and label inside the plot area is painted as pixels, and the numbers themselves live only
in the chart library's in-memory JavaScript state. There is no element to query because
there is no element. This page is the three places the data actually is, in the order you
should try them, and one honest caveat specific to a stealth browser: reading the pixels
back is the one approach that is actively wrong here.

## Why the selector returns nothing

The selector returns nothing because a canvas or WebGL chart paints every bar, line and
label as pixels on a single `<canvas>` element with no child nodes - there is no element
for `page.query_selector_all` to find, no matter how long you wait. An HTML chart and a
canvas chart look identical on screen and are completely different underneath.

An SVG or HTML chart builds a node per mark: a `<rect>` per bar, a `<path>` per line, a
`<text>` per label. Those are in the DOM, so `page.query_selector_all` finds them and you
read attributes off them. A canvas chart calls drawing commands - `fillRect`, `lineTo`,
`fillText` - against a single `<canvas>` element. The result is a bitmap. The `<canvas>`
is one node with a width and a height and no children, and the two hundred data points
inside it are pixels with no structure a selector can address.

So the data is in exactly two recoverable places: the network response the page fetched
to build the chart, and the JavaScript object the chart library keeps in memory. Reach
either and you get typed numbers. Reach for the pixels and you get a bitmap you would have
to reverse a rendering out of, which is both fragile and, on this browser specifically,
corrupted on purpose.

## First choice: read the data endpoint

The chart was drawn from data the page fetched, almost always as JSON over an XHR or
`fetch` call. That response is the cleanest possible source: typed fields, full precision,
every series including the ones the chart truncated or hid.

Attach a listener before you navigate, then let the page load normally.

```python
from invisible_playwright import InvisiblePlaywright

captured = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_response(response):
        url = response.url
        if "/api/" in url and "chart" in url:
            try:
                captured.append(response.json())
            except Exception:
                pass

    page.on("response", on_response)
    page.goto("https://example.com/dashboard")
    page.wait_for_load_state("networkidle")

for payload in captured:
    print(payload)
```

You do not have to guess the URL up front. Run it once with the browser visible, open the
network panel, and find the request whose response holds the series. Filter to that path
and you have the numbers with no parsing at all. The full set of interception patterns,
including `page.route` and why aborting images to "save bandwidth" is a recognizable
automation cadence, is in
[how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## Second choice: read the chart's JS state with page.evaluate

Sometimes there is no clean endpoint: the data is inlined into the initial HTML, computed
client-side, or streamed over a websocket you cannot easily reassemble. In that case the
values are still sitting in the chart instance's memory, and `page.evaluate` runs
JavaScript inside the page where that object lives.

Most charting libraries either hang the instance off the canvas element or expose a
registry of live charts. The shape below is common: a library that lets you recover the
instance from the canvas node and read its parsed datasets back out.

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")
    page.wait_for_selector("canvas")

    series = page.evaluate(
        """
        () => {
          const canvas = document.querySelector('canvas');
          // Many libraries expose a lookup from a canvas to its live instance.
          const chart = window.Chart && window.Chart.getChart
            ? window.Chart.getChart(canvas)
            : null;
          if (!chart) return null;
          return chart.data.datasets.map(d => ({
            label: d.label,
            values: d.data,
          }));
        }
        """
    )

    print(series)
```

The exact property path depends on the library, and you find it the same way you find the
endpoint: open the page by hand, inspect the global the library registers, and walk to the
parsed series. Two practical notes. Wait for the chart to finish drawing before you read,
because the instance and its data exist only after the library has run - `wait_for_selector`
on the canvas plus a short settle is usually enough. And return plain data, arrays and
objects, never the chart instance itself, since only serializable values cross back out of
`page.evaluate`.

This path never touches pixels, which is the point of the next section.

## The pixel trap, and why it is worse on a stealth browser

The tempting third option is to screenshot the canvas and read the numbers out of the
image, or to call `getImageData` and infer bar heights from colored columns. Avoid it on
any browser: it is fragile, resolution-dependent, and turns a data-extraction job into a
computer-vision job. On this browser it is not merely fragile, it is defeated by design.

This project defends against canvas fingerprinting by adding a tiny, seed-deterministic
per-pixel perturbation to what canvas readback returns. The change is imperceptible to a
human and stable for a given seed, and it exists so a detector cannot use the exact bytes
of a rendered canvas as a stable machine identifier. The mechanism and what it protects
are covered in [canvas fingerprint noise](canvas-fingerprint-noise.md).

The consequence for scraping is direct: any approach that recovers chart values from
returned pixels is reading through that perturbation, so the numbers you reconstruct are
wrong in a way that is hard to notice and impossible to trust. The same behavior is why
[a screenshot of a canvas comes back subtly noised](playwright-screenshot-returns-noise.md)
rather than byte-identical. This is not a limitation to route around - it is the reason the
data endpoint and the JS-state approaches are the correct ones. Both read the numbers
before they were ever drawn, so the canvas spoof does not touch them at all: `page.evaluate`
reaching the chart object works on the library's parsed data, which is upstream of every
pixel.

Put plainly: the noise corrupts the one method you should not have been using, and leaves
the two you should.

## When you are stuck with tooltips

Occasionally the only source is the tooltip: the endpoint is obfuscated, the library
exposes nothing useful on the global, and the exact value appears only when a human hovers
a point. You can drive that with
[human-like mouse movement](human-mouse-movement.md) and sample the tooltip text after each
move.

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/graph")
    box = page.locator("canvas").bounding_box()

    readings = []
    steps = 20
    for i in range(1, steps):
        x = box["x"] + box["width"] * i / steps
        y = box["y"] + box["height"] / 2
        page.mouse.move(x, y)                 # arcs there on a Bezier curve
        page.wait_for_timeout(120)
        tip = page.locator(".chart-tooltip")
        if tip.count() and tip.is_visible():
            readings.append(tip.inner_text())

    print(readings)
```

This is the slowest and least reliable of the three, and it samples rather than reads: you
get whatever points your hover happens to land on, at the precision the tooltip prints.
Treat it as a last resort. Its one merit is that the tooltip is real DOM text, so no pixel
reading is involved and the canvas spoof is irrelevant to it. Because the mouse motion
follows a Bezier arc rather than teleporting between coordinates, the hover sampling also
does not itself read as automation, which matters on a page watching pointer behavior.

## Conclusion

A canvas chart has no DOM to scrape, so stop asking selectors for marks that were never
elements. The numbers exist in two clean places before they are ever painted: the JSON the
page fetched, and the chart library's in-memory state. Read the endpoint first, drop to
`page.evaluate` against the chart object when there is no endpoint, and reach for tooltip
sampling only when both fail.

Reading pixels is the wrong tool everywhere and a broken tool here, because the per-pixel
anti-fingerprint noise is designed to make canvas readback non-identical. That is a feature
working as intended, and it points you at the better data all along.

## Short answers to the questions that lead here

**Why does my selector return nothing on a chart?** Because the chart is drawn to a
`<canvas>` or WebGL, which is a single node with no children. The marks are pixels and the
values are in JavaScript memory, so there is no element to select.

**How do I get the numbers out of a canvas chart?** Read the data endpoint the page
fetched, or read the chart library's live instance with `page.evaluate`. Both give you
typed values without touching pixels.

**Can I just screenshot the chart and read it?** You can, but do not. It is fragile on any
browser, and on this one the canvas carries seed-deterministic anti-fingerprint noise, so
pixel readback is corrupted by design and your reconstructed numbers will be wrong.

**Does the canvas spoof affect page.evaluate reaching the chart object?** No. The spoof
only perturbs pixel readback. Reading the library's parsed datasets happens upstream of any
drawing, so it is untouched.

**What if there is no data endpoint and no useful global?** Sample the tooltips by moving
the pointer across the plot and reading the tooltip text after each move. It is a last
resort: you get sampled points at tooltip precision, not the full series.

**How do I find the right endpoint or JS object?** Run once with the browser visible,
inspect the network panel for the request holding the series, and inspect the global the
chart library registers. Pin a seed so the run is reproducible while you explore.

## Sources

- The stock Playwright API surfaced unchanged by this wrapper: `page.on("response")` and
  `page.route`, documented in the official
  [Playwright network guide](https://playwright.dev/python/docs/network), plus `page.evaluate`,
  documented in [evaluating JavaScript](https://playwright.dev/python/docs/evaluating), and
  `page.mouse.move`.
- This project's canvas anti-fingerprint behavior, whose per-pixel perturbation is exactly
  why pixel-based chart reading fails here, covered in the two canvas pages linked below.

**See also:** [how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the endpoint-first workflow in full, [canvas fingerprint noise](canvas-fingerprint-noise.md)
for why pixel readback is perturbed, and [why a canvas screenshot comes back noised](playwright-screenshot-returns-noise.md)
for the same mechanism seen from the screenshot side.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The endpoint-first order on
this page is the one that survives both a markup change and this browser's own canvas
defense.*
