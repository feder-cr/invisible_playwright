---
title: "Scrape lazy-loaded images with Playwright"
description: "Scrape lazy-loaded images with Playwright by reading the data-src attribute off the DOM: no scrolling, no downloads. Scroll-into-view fallback included."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 71
---


# Scrape lazy-loaded images with Playwright

To scrape lazy-loaded images with Playwright, read the deferred `data-src` attribute
(or `data-srcset`) straight off the DOM with `get_attribute` instead of scrolling every
image into view. The real URL is in the markup from first paint, so you get every link
without fetching a single image and without the top-to-bottom scroll a behaviour-watching
site can see you perform.

The first time you scrape an image gallery you read `img.src`, get a one-pixel
transparent gif for every element, and conclude the page is broken. It is not. The
page deferred the real image on purpose, and the URL you want is sitting in a
different attribute the whole time.

This page is about reading that attribute directly instead of scrolling the page to
force every image to download, why the direct read is both faster and quieter, and the
narrow case where you genuinely have to scroll.

| Approach | Speed | Images fetched | Detection exposure | Use when |
|---|---|---|---|---|
| Read the deferred attribute | Fast (string read) | None | Low, no scroll motion | The URL is in the DOM (the usual case) |
| Scroll into view and decode | Slow (per image) | Every image | Higher, scrolling is a behavioural tell | The URL is computed only inside the observer callback |

## Why src is empty and where the real URL lives

Lazy loading exists so a page with two hundred images does not fetch two hundred files
before the visitor has seen the second one. The markup ships a cheap placeholder in
`src` and stores the real URL somewhere the browser will not fetch until it decides to:

- **A deferred attribute.** The real URL is in `data-src`, `data-original`,
  `data-lazy-src`, `data-srcset` or similar, and a small script copies it into `src`
  when the image approaches the viewport. Until then `src` is a blur or a gif.
- **`loading="lazy"`.** The native browser attribute. `src` holds the real URL from
  the start, but the browser does not issue the request until the element is near the
  viewport, so the bytes are not there yet even though the string is.
- **An IntersectionObserver.** The general form of the first case. A script watches
  each image and swaps the attribute in when the element crosses into view.

In all three the value you actually want exists in the DOM from first paint. What is
missing early is not the URL, it is the decoded pixels. That distinction is the whole
trick: if you only need the URL, you never have to make the image load at all.

## Read the deferred attribute instead of scrolling

The cheapest correct approach is to skip the placeholder in `src` and read the
deferred attribute straight off the DOM. Playwright reads any attribute with
`get_attribute`, and the wrapper hands you a real Playwright `Browser`, so nothing
here is special to the stealth engine:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/gallery", wait_until="domcontentloaded")

    urls = []
    for img in page.query_selector_all("img"):
        # prefer the deferred attribute, fall back to src for eager images
        url = (
            img.get_attribute("data-src")
            or img.get_attribute("data-original")
            or img.get_attribute("data-lazy-src")
            or img.get_attribute("src")
        )
        if url and not url.startswith("data:"):
            urls.append(url)

    print(len(urls), "image urls")
```

Notice `wait_until="domcontentloaded"`. You do not need `networkidle` here, because you
are reading strings, not waiting for images to arrive. The `data:` guard drops inline
placeholder gifs that some pages leave in `src` even after they have populated the real
attribute elsewhere.

For `srcset`-based images the same idea applies, you just parse the candidate list:

```python
srcset = img.get_attribute("data-srcset") or img.get_attribute("srcset")
if srcset:
    # each candidate is "url widthdescriptor", take the last (largest) one
    largest = srcset.split(",")[-1].strip().split(" ")[0]
    urls.append(largest)
```

This gets you every URL on the page without a single image ever being fetched. You can
then [download the files with your own HTTP client](combine-invisible-playwright-with-httpx-for-speed.md),
in parallel, at whatever size you want, entirely outside the browser.

## The stealth case: forcing a load is a motion you can be seen making

If reading the attribute is faster, why does everyone reach for scrolling first? Habit,
and the occasional page that really does hide the URL until the observer fires.

There is a second reason to prefer the direct read, and it matters more than speed.
Scrolling the entire page to trigger every lazy image is a mechanical behaviour. A
site that watches interaction sees a pointer that never moves, a scroll that advances
in identical increments, and a page walked top to bottom at a constant rate with no
pauses. That is one of the behavioural tells the
[checklist for being flagged on a single site](playwright-detected-as-bot.md) puts near
the end, precisely because it survives a perfect fingerprint. You spent effort making
the browser look real, then announced yourself with how you moved it.

The honest caveat, so this reads as engineering and not a slogan: the direct-attribute
read is not a stealth feature of the engine and it is not universal. It works because
most pages put the URL in the DOM before any interaction, which is a property of how
lazy loading is normally built, not something the wrapper does. On a page that computes
the URL only inside the observer callback, the attribute is not there to read and you
have to fall back to the next section. When you do fall back, the same principle that
runs through the rest of these notes applies: a suppressed or absent signal is itself a
signal, so a load you fake should look like a load a person caused. The engine gives you
Bezier-curve pointer motion and a coherent machine fingerprint for exactly that reason,
but it cannot make a rigid, evenly-timed scroll look human. That part is your loop.

## When you must scroll: scroll into view and wait for the decode

Some pages genuinely defer the URL until the element intersects the viewport. For those,
scroll each image into view and wait for it to finish decoding before you read `src`.
Playwright's `scroll_into_view_if_needed` positions the element; `Image.decode()` in the
page resolves once the pixels are ready:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/gallery", wait_until="domcontentloaded")

    urls = []
    for img in page.query_selector_all("img"):
        img.scroll_into_view_if_needed()
        # wait for the swapped-in src to actually decode, not just change
        page.wait_for_function(
            "el => el.complete && el.naturalWidth > 0",
            arg=img,
            timeout=5000,
        )
        url = img.get_attribute("src")
        if url and not url.startswith("data:"):
            urls.append(url)
```

The `complete && naturalWidth > 0` check is the important part. `scroll_into_view_if_needed`
returns as soon as the element is positioned, which is before the observer has swapped the
URL and long before the image has decoded. Reading `src` on the line after the scroll gets
you the placeholder again. Waiting on `naturalWidth` asserts a positive fact, that a real
image with real dimensions is present, rather than the absence of an error.

If the gallery itself grows as you scroll, this is really two problems stacked, and the
lazy-image read has to run inside the
[infinite scroll loop that adds the elements](how-to-scrape-infinite-scroll-playwright.md)
rather than once at the end. Scroll a screen, let the new images decode, read them, repeat.

## Verify you got real URLs, not placeholders

Whichever path you took, check the result before trusting it, because the failure mode of
this task is silent: you get a full list of URLs and every one of them is the same
one-pixel gif.

A cheap positive check is that the URLs are distinct and are not `data:` blobs:

```python
real = [u for u in urls if not u.startswith("data:")]
assert len(set(real)) > 1, "every url is identical - you are reading placeholders"
print(len(set(real)), "distinct image urls")
```

The same reproducibility that helps you debug a blocked session helps here. Because
`seed=42` fixes the identity, a run that returns placeholders returns them again, so you
can bisect a broken selector instead of blaming a flaky page. If your extraction depends
on the images that a background request pulls in rather than on `img` elements at all, the
list you actually want may be in the network log, in which case
[capturing the XHR responses](how-to-capture-xhr-api-responses-playwright.md) is the more
direct read than the DOM.

## Conclusion

Lazy loading defers the download, not the URL. The URL is in the DOM from first paint,
in a `data-*` attribute or the `srcset`, and reading it with `get_attribute` gets you
every image without fetching a byte and without the top-to-bottom scroll that a
behaviour-watching site can see you perform. Keep the scroll-into-view approach for the
minority of pages that compute the URL only when the element intersects, and when you use
it, wait for `naturalWidth` rather than reading `src` on the next line. Either way, assert
that the URLs are real and distinct, because the default failure is a tidy list of
identical placeholders.

## Short answers to the questions that lead here

**Why is img.src empty or a one-pixel gif?** Because the page is lazy loading. The real
URL is in a deferred attribute like `data-src`, and `src` holds a placeholder until the
element nears the viewport.

**Do I have to scroll the page to load every image?** Usually no. Read `data-src` (or
`data-original`, `data-srcset`) with `get_attribute` and you get every URL without
loading anything. Scroll only when the page computes the URL inside an observer callback.

**Is scrolling the whole page a detection risk?** It can be. A rigid, evenly-timed scroll
with no pointer motion is a behavioural tell. Reading the attribute avoids both the tell
and the bandwidth.

**How do I wait for a lazy image to actually load?** Scroll it into view, then
`wait_for_function` on `el.complete && el.naturalWidth > 0`. Reading `src` immediately
after the scroll returns the placeholder, because the swap has not happened yet.

**What about loading="lazy"?** The real URL is in `src` from the start there, so reading
the attribute works directly. Only the bytes are deferred, and for a URL scrape you do
not need the bytes.

**How do I know I scraped real URLs and not placeholders?** Check they are distinct and
not `data:` blobs. The silent failure of this task is a full list where every entry is the
same gif.

## Sources

- The Playwright element API used above: `query_selector_all`, `get_attribute`,
  `scroll_into_view_if_needed` and `wait_for_function`, read from the upstream
  [Playwright Python documentation](https://playwright.dev/python/docs/api/class-page)
  rather than paraphrased.
- The native `loading="lazy"` attribute and the `srcset` candidate-list syntax, from
  [MDN's `<img>` element reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/img).
- The behavioural section draws on this project's own release notes on interaction tells,
  where an evenly-timed automated motion flags where a static fingerprint does not.

**See also:** [scraping an infinite-scroll feed](how-to-scrape-infinite-scroll-playwright.md)
when the gallery grows as you scroll, [waiting for the page to actually finish loading](how-to-wait-for-page-load-playwright.md)
for the difference between the DOM being ready and the images being present, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) when the
images come from a background request rather than the initial HTML.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The direct-attribute read is
faster, quieter, and the one I reach for first; the scroll-and-decode fallback is for the
pages that leave me no choice.*
