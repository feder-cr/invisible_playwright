---
title: "How to download images in bulk with Playwright"
description: "Download images in bulk with Playwright: resolve lazy srcset and data-src to full-res URLs, then fetch the bytes inside the page session so hotlink checks pass."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 61
---


# How to download images in bulk with Playwright

To download images in bulk with Playwright reliably, resolve each image's
full-resolution URL from `srcset` or `data-src` after triggering the lazy load, then
fetch the bytes through the page's own request context (`page.request`) so every request
carries the same session cookies, `Referer` and fingerprint the server used to serve the
page. That one move is what turns placeholders and 403s back into real files.

Bulk image scraping looks like a one-liner and then breaks in two places that have
nothing to do with each other. First the URL you read off the DOM is not the image you
wanted: it is a tiny placeholder, or a `data:` blob, or empty, because the real source
lives in `srcset` or a `data-src` attribute that only becomes a real request when the
element scrolls into view. Second, once you have the right URL, fetching it from a
separate HTTP client returns a watermarked placeholder or a `403`, because the asset is
hotlink-protected and your out-of-band request does not carry the page's cookies, its
`Referer`, or a handshake that matches the browser that loaded the page. This page fixes
both, and the second fix is where the browser you use actually matters.

## Why bulk image scraping breaks twice

Bulk image scraping breaks for two unrelated reasons: the DOM's `src` is often still a
placeholder because the real image is waiting on lazy-loading, and even the correct
URL can be refused when it is fetched outside the browser session that hotlink
protection expects. The two failures are worth separating because they have different
fixes and people usually only notice the first one.

**Lazy loading.** Modern galleries ship a placeholder in `src` and put the real
candidates in [`srcset`](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/img)
(a comma-separated list of URL plus width or density descriptors) or in a framework
attribute like `data-src`. Until a scroll or an intersection observer fires,
[`img.currentSrc`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLImageElement/currentSrc)
is empty or points at a 1x1 pixel. Read `src` naively and you download hundreds of
identical placeholders. If your galleries defer through `data-src`,
[reading that attribute directly off the DOM](how-to-scrape-lazy-loaded-images-playwright.md)
is often lighter than scroll-forcing every image.

**Hotlink protection.** The full-resolution asset is often served only to a request that
proves it came from the page: a session cookie set during the visit, a `Referer` header
pointing at the gallery, sometimes a signed query parameter with a short expiry. A plain
`requests.get(url)` from Python has none of that, and on top of it presents a TLS and
header fingerprint that is obviously not the browser that just rendered the page. The
server answers that request with a placeholder, a watermark, or a block, while the same
URL opened in the browser returns the real bytes.

The fix for the second problem is the interesting one, and it is where the browser you
use actually matters.

## Resolve the lazy srcset to the full-resolution URL

Do the resolution in the page, where `srcset` parsing and the element's own state are
available, rather than trying to reconstruct it in Python.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/gallery", wait_until="networkidle")

    # Scroll the page in steps so lazy images actually request their real source.
    page.evaluate("""
        async () => {
            const step = window.innerHeight;
            for (let y = 0; y < document.body.scrollHeight; y += step) {
                window.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 250));
            }
            window.scrollTo(0, 0);
        }
    """)

    # For each <img>, pick the highest-resolution candidate from srcset,
    # falling back to currentSrc / data-src / src in that order.
    urls = page.evaluate("""
        () => {
            const best = (img) => {
                const set = img.getAttribute('srcset');
                if (set) {
                    let bestUrl = null, bestScore = -1;
                    for (const part of set.split(',')) {
                        const [url, d] = part.trim().split(/\\s+/);
                        // d looks like "1024w" or "2x"; treat a bare url as 1x
                        const score = d ? parseFloat(d) : 1;
                        if (url && score > bestScore) { bestScore = score; bestUrl = url; }
                    }
                    if (bestUrl) return new URL(bestUrl, location.href).href;
                }
                const raw = img.currentSrc || img.dataset.src || img.src || '';
                return raw ? new URL(raw, location.href).href : null;
            };
            return [...document.querySelectorAll('img')]
                .map(best)
                .filter(u => u && !u.startsWith('data:'));
        }
    """)

    print(f"resolved {len(urls)} full-resolution image URLs")
```

Two details do most of the work here. Scrolling in steps with a short pause gives the
intersection observers time to fire, so `currentSrc` becomes real; and taking the largest
`w`/`x` descriptor from `srcset` gets you the full-resolution asset instead of the phone
thumbnail. Dropping `data:` URLs removes the placeholders that never resolved.

## Pull the bytes inside the browser's own session

This is the step that turns a placeholder into the real image. Instead of handing the
URLs to a separate HTTP client, fetch them through the page's request context. In stock
Playwright that is `page.request`, an `APIRequestContext` bound to the same browser
context as the page, so it automatically sends the context's cookies and reuses the same
connection pool. Add the page's own URL as `Referer` and hotlink checks see a request
that is indistinguishable from the browser loading the image itself.

```python
import os, hashlib

os.makedirs("images", exist_ok=True)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/gallery", wait_until="networkidle")
    # ... resolve `urls` exactly as above ...

    referer = page.url
    for url in urls:
        # page.request shares this page's cookies and session automatically.
        resp = page.request.get(url, headers={"referer": referer})
        if not resp.ok:
            print(f"skip {resp.status} {url}")
            continue
        body = resp.body()                      # raw bytes, no re-encoding
        name = hashlib.sha1(url.encode()).hexdigest()[:16]
        ext = (resp.headers.get("content-type", "image/jpeg")
               .split("/")[-1].split(";")[0]) or "jpg"
        # Write bytes, never text: image data is binary and must not be re-encoded.
        with open(f"images/{name}.{ext}", "wb") as fh:
            fh.write(body)
        print(f"saved {len(body)} bytes  {url}")
```

Why this returns the real asset when an external client returns a block: the request
leaves from the same browser context that rendered the page, so it carries the same
session cookies, the same `Referer`, and, because this is a real patched Firefox driven by
stock Playwright, the same TLS and header fingerprint as every other request the page
already made. A separate `requests.get` with a mismatched user agent and handshake is a
different client asking for a protected asset out of context, and that is exactly the
request hotlink protection exists to refuse.

Because `InvisiblePlaywright(seed=42)` derives the whole identity from one seed, that
fingerprint is also stable and reproducible: rerun the job with the same seed and the
image requests present the same browser every time, which is what makes a failed pull
debuggable instead of a coin flip.

## Throttle the fetches so the burst is not its own signal

Getting the fingerprint right on each request does not license firing two hundred of them
in a second: a page load followed by a sudden burst of same-origin asset requests, evenly
spaced to the millisecond and denser than any human gallery view would generate, is a
behavioural signal on its own, independent of how good each individual request looks. It
is the same class of tell as
[a suppressed or contradictory fingerprint surface](how-to-test-bot-detection.md): the
individual values are fine and the pattern is not.

So cap concurrency and add jitter. A small bounded pool with a randomised gap per request
looks like a browser lazy-loading a gallery; an unbounded flood looks like a scraper.

```python
import time, random

def download_all(page, urls, referer, max_at_once=4):
    saved = 0
    for i, url in enumerate(urls):
        resp = page.request.get(url, headers={"referer": referer})
        if resp.ok:
            body = resp.body()
            with open(f"images/{i:04d}.jpg", "wb") as fh:
                fh.write(body)
            saved += 1
        # jittered pause; every `max_at_once` requests, rest a little longer
        time.sleep(random.uniform(0.15, 0.6))
        if (i + 1) % max_at_once == 0:
            time.sleep(random.uniform(0.8, 1.5))
    return saved
```

The numbers are a starting point, not a law. Tune the gap to how a real user would page
through the gallery you are actually reading, and if the site streams more images as you
scroll, drive the [infinite scroll loop](how-to-scrape-infinite-scroll-playwright.md)
first and resolve `srcset` after each batch rather than all at the end.

## A measurement: same session vs a separate client

The reason for fetching inside the context is not theory. Pointing a plain external HTTP
client at a set of hotlink-protected full-resolution URLs, using a generic user agent and
no `Referer`, returned the placeholder or a `403` on the protected assets while the
public thumbnails came through fine, so a spot check on one thumbnail would have reported
everything working. The identical URLs pulled through `page.request` with the page's
`Referer` returned the real bytes across the set, at full resolution, because the request
carried the session and the fingerprint the server was checking for. The lesson is the
same one that runs through [TLS handshake fingerprinting](ja3-ja4-tls-fingerprint.md):
the block is often decided by what the connection looks like before the URL is even read,
which no amount of correct URL parsing in Python can fix.

| Fetch path | Session cookies + `Referer` | Result on hotlink-protected assets |
|---|---|---|
| External HTTP client (generic user agent, no `Referer`) | absent | placeholder or `403` |
| `page.request` (page's `Referer`, same context) | present | real full-resolution bytes |

The public thumbnails came through on both paths, which is the trap: a spot check on one
unprotected thumbnail reports everything working while the protected assets are still
being refused.

For files that are downloads rather than inline images (a ZIP of originals, a PDF behind
the same session), the same principle applies through a different API, covered in
[downloading files with Playwright](how-to-download-files-playwright.md).

## Conclusion

Bulk image scraping is two problems wearing one coat. Resolve `srcset` and `data-src` to
the real full-resolution URL and trigger the lazy load by scrolling, so you are asking for
the right bytes; then fetch those bytes through the page's own request context, so the ask
comes from the session and the fingerprint that is allowed to see them. Throttle the
result so the burst does not undo the disguise. Do those three and the placeholder-and-403
problem that sends people shopping for a different tool mostly disappears, because the
requests were never coming from a different browser than the page in the first place.

## Short answers to the questions that lead here

**Why do my scraped images come back as tiny placeholders?** Because the real source is in
`srcset` or `data-src` and only loads on scroll. Read `currentSrc` after scrolling, or
parse the largest candidate out of `srcset` yourself.

**Why do I get a 403 or a watermark when I download the image?** The asset is
hotlink-protected. It is served only to a request carrying the page's session cookie and a
`Referer` pointing at the page. An out-of-band `requests.get` has neither.

**How do I send the page's cookies with the image request?** Fetch through `page.request`
instead of an external client. It is an `APIRequestContext` bound to the page's browser
context, so it sends the same cookies automatically; add `headers={"referer": page.url}`.

**Should I use requests or httpx to download the images?** Not for protected assets. A
separate client presents a different TLS and header fingerprint than the browser that
loaded the page, which is exactly what hotlink protection checks. This is
[why a plain Python requests scraper is blocked before it sends a header](web-scraping-tls-fingerprint-requests-blocked.md);
fetch inside the context instead.

**How do I get the highest-resolution version instead of the thumbnail?** Parse `srcset`
and pick the candidate with the largest `w` or `x` descriptor, resolving it against the
page URL, before you download anything.

**Is downloading hundreds of images at once safe?** No. A dense, evenly spaced burst is a
behavioural signal by itself. Cap concurrency, add jitter, and
[rate-limit the job so it paces like a human](how-to-rate-limit-your-scraper-playwright.md)
paging through the gallery.

## Sources

- Playwright's official [`APIRequestContext` documentation](https://playwright.dev/python/docs/api/class-apirequestcontext):
  `page.request` uses the same cookie jar as its browser context, and `response.body()`
  returns the raw response bytes.
- MDN's [`srcset` reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/img):
  a comma-separated list of image URLs each with an optional width or pixel-density
  descriptor, which the browser resolves to pick the delivered candidate.
- MDN's [`HTMLImageElement.currentSrc`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLImageElement/currentSrc)
  documentation: it reflects the URL the browser actually selected from `srcset`, and is
  unrelated to whether that image has finished loading.
- This project's own gates on writing uploaded and downloaded content as bytes rather than
  text, so binary image data is never re-encoded on the way to disk.
- The same-session versus separate-client comparison described above, run against
  hotlink-protected full-resolution assets behind a session gate.

**See also:** [downloading files with Playwright](how-to-download-files-playwright.md) for
assets that arrive as downloads rather than inline images, and
[scraping infinite scroll](how-to-scrape-infinite-scroll-playwright.md) when the gallery
streams more images as you go.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The placeholder-and-403
problem is one I chased in Python for an afternoon before moving the fetch back inside the
browser where it belonged.*
