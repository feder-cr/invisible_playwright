---
title: "How to scrape image galleries with Playwright"
description: "Scrape image galleries with Playwright: scroll to trigger lazy-loaded tiles, take the widest srcset, open the lightbox for full-res, and beat hotlink 403s."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 47
---


# How to scrape image galleries with Playwright

To scrape an image gallery with Playwright, do four things in order: scroll so
lazy-loaded tiles promote their real URLs, parse each `srcset` and take the widest
candidate, open the lightbox and read `currentSrc` for the full-resolution asset, then
fetch that asset from inside the browser context so it still carries the `Referer` and
headers that hotlink checks require. Skip any one step and you get placeholders,
thumbnails, or 403s.

A gallery looks like a grid of images and behaves like nothing of the sort. What the
page ships you first is mostly placeholders, the real URLs are hidden in attributes the
browser has not read yet, and the full-resolution asset does not exist in the DOM at all
until a click asks for it. Extract the `src` of every tile and you get a folder of grey
1x1 pixels and thumbnails.

This page is the order that actually gets you the pictures: make the lazy-load fire,
pick the right candidate out of the several a tile offers, open the lightbox for the
full asset, and then fetch it without throwing away the one thing that lets you fetch it
at all.

## Why the tile `src` is a placeholder

Modern galleries defer image loading so the first screen paints fast. A tile that is not
near the viewport carries no real image. The pattern you will meet, in rough order of
frequency:

- `src` is a placeholder: a transparent pixel, a `data:` blur, or a tiny low-quality
  image meant to be swapped later.
- The real URL sits in `data-src`, `data-lazy`, `data-original` or similar, waiting for
  a script to promote it once the tile scrolls close.
- A `srcset` lists the same image at several widths, and the browser is supposed to
  choose one based on the layout and the device pixel ratio.
- The full-resolution version is not in the grid markup at all. It only loads when you
  open the item, and the lightbox may swap in a `blob:` URL or a base64 string rather
  than a plain link.

So a scraper that reads the grid once, without interacting, is reading the state before
any of the real work has happened. Everything below is about forcing that work to
happen while you watch.

The launch is stock Playwright. If you have driven Playwright before, the only change is
the two lines that open the browser:

```bash
pip install invisible-playwright
```

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/gallery", wait_until="networkidle")
    # `browser` is a real Playwright Browser; every documented method works.
```

The `seed=42` fixes the identity so a run that fails on one tile can be replayed exactly
rather than guessed at. See the [quickstart](quickstart.md) for what the seed pins.

## Trigger the lazy-load by scrolling each tile into view

The reliable way to promote every tile is to bring it near the viewport, the same event
the page's own script listens for. Scroll in steps, let the network settle, and repeat
until the page height stops growing. This is the same mechanism as an
[infinite-scroll feed](how-to-scrape-infinite-scroll-playwright.md), applied to a fixed
grid instead of an appending one.

```python
def scroll_until_settled(page, step=800, pause_ms=400, max_rounds=60):
    last_height = 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, step)
        page.wait_for_timeout(pause_ms)
        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            break
        last_height = height

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/gallery", wait_until="networkidle")
    scroll_until_settled(page)
```

Scrolling with `page.mouse.wheel` also produces real wheel events with real coordinates,
which matters when the page is watching interaction and not just fingerprints. A grid
that only ever jumps to the bottom by script, with no pointer motion in between, reads
differently from one a person browsed.

For a tile that still holds a placeholder after the whole page has been scrolled, force
it individually and wait for the swap:

```python
def promote_tile(page, handle):
    handle.scroll_into_view_if_needed()
    page.wait_for_function(
        """el => {
            const s = el.getAttribute('src') || '';
            return s && !s.startsWith('data:') && !s.includes('placeholder');
        }""",
        arg=handle,
    )
```

## Resolve the highest-resolution srcset candidate

Once a tile is real, you often have a choice rather than a URL. A
[`srcset`](https://html.spec.whatwg.org/multipage/images.html) is a list of `url
widthDescriptor` pairs, and the browser picks one for its layout, which is not the one
you want. You want the largest. Parse the attribute yourself and take the widest
candidate, falling back to the lazy attributes and then to `src`:

```python
def best_url(tile):
    # tile is a dict of the attributes we pulled from the DOM
    srcset = tile.get("srcset") or ""
    candidates = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0]
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            width = int(bits[1][:-1])
        candidates.append((width, url))
    if candidates:
        candidates.sort()
        return candidates[-1][1]  # widest descriptor wins
    return tile.get("data-src") or tile.get("src")

tiles = page.eval_on_selector_all(
    "img",
    """imgs => imgs.map(el => ({
        src: el.getAttribute('src'),
        'data-src': el.getAttribute('data-src'),
        srcset: el.getAttribute('srcset'),
    }))""",
)
urls = [best_url(t) for t in tiles]
```

Two cautions. A `srcset` can carry `2x` density descriptors instead of `w` widths, in
which case there is no pixel width to sort on and you take the highest density. And the
widest candidate in the grid is still the grid's largest, which is usually smaller than
the asset behind the lightbox. That larger asset is the next step.

## Open the lightbox to get the full-resolution asset

The grid rarely holds the full image. Clicking a tile opens a lightbox or detail view
that loads a bigger asset, and that is the one worth keeping. Click, wait for the larger
image to appear, and read its resolved URL from the live element rather than the markup:

```python
def full_res_url(page, tile_selector):
    page.click(tile_selector)   # mouse arcs to the tile on a Bezier curve
    page.wait_for_selector(".lightbox img, [role='dialog'] img", state="visible")
    # currentSrc is what the browser actually chose and loaded, not the attribute
    url = page.eval_on_selector(
        ".lightbox img, [role='dialog'] img",
        "el => el.currentSrc || el.src",
    )
    page.keyboard.press("Escape")
    return url
```

Read [`currentSrc`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLImageElement/currentSrc),
not `src`. `currentSrc` is the URL the browser resolved and actually fetched after
applying `srcset` and the viewport, so it is the truth about what loaded.

If the lightbox swaps in a `blob:` URL or a base64 `data:` string, there is no plain
link to hand anyone. The bytes already live in the page, so pull them out where they
are rather than trying to re-request a URL that only means something inside this tab:

```python
def read_inline_asset(page, selector):
    return page.evaluate(
        """async (sel) => {
            const el = document.querySelector(sel);
            const url = el.currentSrc || el.src;
            const resp = await fetch(url);          // same-origin, uses the page's context
            const buf = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let bin = '';
            for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
            return btoa(bin);                        // base64 back to Python
        }""",
        selector,
    )
```

```python
import base64

b64 = read_inline_asset(page, ".lightbox img")
with open("asset.jpg", "wb") as fh:
    fh.write(base64.b64decode(b64))
```

## Fetch the asset without losing the headers that got you in

Fetch the asset from inside the browser context, not from a separate downloader, so the
request keeps the `Referer`, cookies and headers the image host inspects. This is the
caveat that costs people an afternoon. Image hosts commonly enforce hotlink
protection: the asset server inspects the `Referer` and the rest of the request headers,
and it serves the picture only to a request that looks like it came from the page. A
real browser sends a matching `Referer`, a `User-Agent` that agrees with its engine, and
the `Sec-Fetch-*` and Client-Hints headers a genuine image request carries. That whole
set is [what a real browser attaches to a subresource request](client-hints-sec-fetch.md),
and it is why the same URL that returns the picture inside the tab returns a 403 from a
bare HTTP client that sends none of it.

The mistake is to collect the URLs in the browser and then hand them to an outside
downloader in a separate process. That downloader is a fresh HTTP client with none of
the browser's headers, its own handshake, and no cookies, so the host sees a naked
request and refuses. It is the same failure as
[a requests session being blocked while the browser sails through](web-scraping-tls-fingerprint-requests-blocked.md):
the URL was never the gate, the request identity was.

Two ways to keep the identity. The first, shown above, is to fetch inside the page with
`fetch()`, which carries the tab's `Referer`, cookies, headers and Client-Hints for free
because the browser builds the request. The second, when you want the fetch to run from
Python but still share the session, is
[Playwright's request context](https://playwright.dev/python/docs/api/class-apirequestcontext),
which reuses the browsing context's cookies and user agent:

```python
def download_via_context(page, url, out_path, referer):
    resp = page.request.get(url, headers={"Referer": referer})
    if resp.status != 200:
        raise RuntimeError(f"host refused: {resp.status}")
    with open(out_path, "wb") as fh:
        fh.write(resp.body())
```

Pass the page URL as the `Referer` explicitly here, because the request context does not
infer it the way an in-page `fetch` does. If you must download from a wholly separate
tool, the honest requirement is to replay the browser's headers exactly, the full set
and not just the `Referer`, or you are back to the naked request. In practice, fetching
inside the browser context is less to get wrong.

Because the engine here is a real Firefox patched at the C++ level rather than a headless
build wearing a header, the handshake and the header set are consistent with each other
without you assembling them by hand. A measurable version of the difference: on a host
that hotlink-checks, a bare client with a hand-set `User-Agent` returned 403 on the asset
URL, while the same URL fetched inside the browser context returned 200 and the bytes.
The URL was identical; only the request identity changed.

## Conclusion

A gallery is a pipeline, not a page. Scroll so the lazy-load promotes every tile, parse
`srcset` and take the widest candidate rather than whatever the layout chose, open the
lightbox and read `currentSrc` for the asset the grid does not hold, and then fetch that
asset from somewhere that still carries the browser's `Referer` and headers, which means
inside the browser context or with its headers replayed exactly. Skip any one of those
and you get placeholders, thumbnails, or a folder of 403s. Do all four and you get the
pictures.

## Short answers to the questions that lead here

**Why do I only get placeholder or tiny images?** Because the tiles are lazy-loaded and
the real URL lives in `data-src` or a `srcset` that only resolves once the tile nears the
viewport. Scroll each tile into view first, then read it.

**How do I pick the biggest image from a srcset?** Parse the attribute into `url width`
pairs, sort by the `w` descriptor, and take the largest. If it uses `2x` density
descriptors instead, take the highest density. The grid's largest is still usually
smaller than the lightbox asset.

**Where does the full-resolution image come from?** Usually only after a click. The
lightbox loads a bigger asset that is not in the grid markup, sometimes as a `blob:` or
base64 string. Read `currentSrc` off the opened image rather than the tile's `src`.

**Why do the image URLs return 403 when I download them?** Hotlink protection. The host
checks the `Referer` and headers, so a URL that works inside the tab fails from a bare
HTTP client that sends none of them. Fetch within the browser context or replay its
headers exactly.

**Can I hand the URLs to an outside downloader?** Only if you copy the browser's full
header set, including `Referer` and Client-Hints, onto every request. Losing them is the
usual reason an external download 403s while the page loaded the same image fine.

**How do I make the run reproducible?** Pass a `seed` so the identity is fixed. The same
seed gives the same browser every time, so a scrape that broke on one tile can be
replayed instead of re-rolled.

## Sources

- Playwright's documented page, mouse, keyboard and
  [request-context](https://playwright.dev/python/docs/api/class-apirequestcontext) APIs,
  used here exactly as upstream since the returned object is a real Playwright `Browser`.
- The [`srcset`](https://html.spec.whatwg.org/multipage/images.html) candidate-selection
  rules and the [`currentSrc`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLImageElement/currentSrc)
  property these steps depend on, per the HTML Living Standard and MDN, rather than any
  single page's rendering of them.
- This project's own gallery and hotlink notes, including the 403-versus-200 measurement
  above, taken through the patched engine against a host that checks the `Referer`.

**See also:** [scraping an infinite-scroll feed](how-to-scrape-infinite-scroll-playwright.md)
for the scrolling half of this in its own right, [what a real browser sends on a
subresource request](client-hints-sec-fetch.md) for why the in-context fetch passes, and
[why a plain HTTP client gets blocked where the browser does not](web-scraping-tls-fingerprint-requests-blocked.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The 403-on-the-downloader
mistake in the last section is one I made before I made the fetch stay in the tab.*
