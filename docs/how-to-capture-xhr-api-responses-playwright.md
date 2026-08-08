---
title: "How to capture XHR and API responses in Playwright"
description: "Capture XHR and API JSON in Playwright with page.on('response') and page.route() instead of parsing HTML, and why aborting images to save bandwidth is a tell."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 23
---


# How to capture XHR and API responses in Playwright

To capture XHR and API responses in Playwright, attach `page.on("response", handler)`
to read every response, use `page.expect_response(predicate)` to wait for one specific
call, or `page.route()` to inspect a request before it is sent. All three are the stock
Playwright API, running on the real `Browser` object `invisible_playwright` returns
unchanged. Read the JSON the page already fetched instead of parsing the rendered HTML.

Most pages you want to scrape already have the data in a clean form: a background XHR
or fetch call returns JSON, and the DOM you are parsing is just that JSON rendered into
HTML by JavaScript. Parsing the HTML back into structured data is doing the browser's
work twice, and it breaks the moment the markup changes. Reading the response the page
already made is more direct, more stable, and usually less code.

Playwright exposes every response the browser receives, and lets you intercept requests
before they are sent. Because `invisible_playwright` hands you a real Playwright
`Browser`, all of this is the stock API, working exactly as documented upstream. This
page shows the three ways to capture that traffic, and then the one habit to avoid,
because the most common "optimization" applied to a capture script is also a
recognizable automation cadence.

## Why read the response instead of the rendered HTML

When a page loads a list, a price, a search result or a profile, the values almost
always arrive as JSON over an XHR or `fetch` call, and the framework paints them into
the page afterward. That means two copies of the same data exist: the structured
original, and the HTML rendering of it.

Parsing the rendering is the fragile choice. A class name changes, a wrapper `div`
appears, the list virtualizes and only ten of two hundred rows are in the DOM at once,
and your selectors rot. The JSON underneath tends to be stable across all of those,
because it is an internal contract the site's own frontend depends on. Capture it and
you get typed fields instead of scraped strings, pagination cursors instead of
"click next and hope", and totals the DOM never shows.

The launch is the usual two-line change from stock Playwright:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/results")
```

Everything below attaches to that real `page` object.

## Capture every response with page.on('response')

The broadest tool is an event listener. [`page.on("response", handler)`](https://playwright.dev/python/docs/network#network-events)
fires for every response the browser receives, including the sub-resource loads, so you
filter down to the calls you care about.

```python
from invisible_playwright import InvisiblePlaywright

captured = []

def on_response(response):
    # keep only the API calls, not the images, CSS, HTML documents
    if "/api/" in response.url and response.status == 200:
        ctype = response.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                captured.append(response.json())
            except Exception:
                # body already consumed or not valid JSON; skip
                pass

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("response", on_response)
    page.goto("https://example.com/results")
    page.wait_for_load_state("networkidle")

for payload in captured:
    print(payload)
```

Two things worth knowing. Filter by both URL and `content-type` before you call
`response.json()`, because the handler sees every response and calling `.json()` on an
image will raise. And read the body inside the handler or await it promptly: a response
body is fetched lazily, and if the page navigates away before you read it, the buffer
can be gone.

The async form is identical in shape, with `async def` and `await response.json()`:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async def main():
    captured = []

    async def on_response(response):
        if "/api/" in response.url and response.status == 200:
            if "application/json" in response.headers.get("content-type", ""):
                try:
                    captured.append(await response.json())
                except Exception:
                    pass

    async with InvisiblePlaywright(seed=42) as browser:
        page = await browser.new_page()
        page.on("response", on_response)
        await page.goto("https://example.com/results")
        await page.wait_for_load_state("networkidle")
    return captured
```

## Wait for one specific call with page.expect_response

Listening to everything is right when you do not know in advance which call carries the
data. When you do know, [`page.expect_response()`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
is cleaner: it waits for a matching response and hands it straight back, so you can
trigger the action and read the result in one place.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/search")

    # arm the wait, then perform the action that causes the call
    with page.expect_response(lambda r: "/api/search" in r.url and r.status == 200) as info:
        page.fill("#q", "widgets")
        page.click("#search-button")

    response = info.value
    data = response.json()
    print(data["total"], "results")
```

The predicate is a plain function, so you can match on the URL, the status, the method,
or anything on the response object. This is the pattern for
[scraping paginated endpoints](how-to-scrape-paginated-pages-playwright.md) too:
click "next", wait for the next page's response, read its cursor, repeat, all without
touching a single rendered row.

## Inspect and rewrite in flight with page.route

`page.on("response")` observes. [`page.route()`](https://playwright.dev/python/docs/network#handle-requests)
intercepts, which lets you read a request before it is sent, change it, fulfill it
yourself, or let it continue. For capture work the useful move is reading the request's
post body or headers, then calling `route.continue_()` so the page behaves exactly as it
would have.

```python
from invisible_playwright import InvisiblePlaywright

seen_requests = []

def on_route(route):
    request = route.request
    if "/api/" in request.url:
        seen_requests.append({
            "url": request.url,
            "method": request.method,
            "post_data": request.post_data,   # the JSON body a POST sends
        })
    # let it proceed untouched so the page loads normally
    route.continue_()

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.route("**/*", on_route)
    page.goto("https://example.com/results")
    page.wait_for_load_state("networkidle")

for r in seen_requests:
    print(r["method"], r["url"])
```

Capturing the request side matters when the endpoint needs a signed header, a body
parameter or a token that the frontend computes in JavaScript. Reading it here tells you
exactly what the real call looks like, which is the thing you would otherwise reverse
out of minified code.

Notice the route pattern above is `**/*`, which matches everything and continues
everything. That is deliberate, and the next section is why.

## The bandwidth-saving abort that gets you blocked

Here is the tempting next step, and the reason this page exists. Once you are already
intercepting with `page.route()`, it looks free to save bandwidth by aborting the loads
you do not need. The images, the stylesheets, the fonts, the analytics beacons: none of
them carry the JSON you came for, so why download them?

```python
# DON'T do this on a page you need to look human on
def block_heavy(route):
    if route.request.resource_type in ("image", "stylesheet", "font", "media"):
        route.abort()
    else:
        route.continue_()

page.route("**/*", block_heavy)
```

It works, it is faster, and on a surface that measures how real your browser is, it is
a tell. The reason connects to the machine-layer model in
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md): a
real browser loading a real page fetches dozens of sub-resources. It pulls the CSS, the
web fonts, the sprite sheets, the icons, the tracking pixels, the lazy-loaded images as
you scroll. That fan-out of requests, in that order, with that timing, is part of what a
human session looks like on the wire.

A client that requests the HTML document and exactly one XHR and nothing else has a
request pattern no human browser produces. You suppressed the noise, and the suppression
is itself the signal. It is the same trap as an empty canvas or a blocked WebRTC section:
[a suppressed signal is a tell, not stealth](how-to-test-bot-detection.md). The gate is
not "did you leak something wrong", it is "do you look like the real thing", and the real
thing is noisy.

So the rule for capture scripts:

> Read the responses you want, but do not strip the loads that make you look real.
> Observe with `page.on("response")`, capture request bodies with a `route` that always
> calls `continue_()`, and let the browser fetch its sub-resources the way a browser does.

If bandwidth genuinely is a hard constraint, the honest version is narrower than "abort
everything heavy": drop only the third-party beacons a real user's ad blocker would drop
anyway, keep first-party CSS, fonts and images, and never reduce a page to a
document-plus-one-XHR silhouette. The safest capture script changes what you *read* off
the traffic, not what the browser *loads*. The engine is already spending effort to look
like a real machine on the GPU, fonts, audio and screen surfaces; do not undo that at the
network layer for a few saved kilobytes.

## Conclusion

The data you want is usually already JSON before the page turns it into HTML, and
Playwright gives you three clean ways to read it: `page.on("response")` to watch
everything, `page.expect_response()` to wait for a named call and read it inline, and
`page.route()` to inspect the request side including bodies and signed headers. All of it
is the stock Playwright API, because `invisible_playwright` returns a real `Browser`.

The one discipline that separates a capture script that lasts from one that gets blocked
is at the network layer, not the parsing layer. Reading responses costs you nothing in
realness. Aborting the sub-resource loads to save bandwidth trades a recognizable
automation cadence for a few kilobytes, and on a site that is watching, that is a bad
trade. Read the JSON; let the browser load like a browser.

## Short answers to the questions that lead here

**How do I get the JSON a page loads instead of parsing HTML?** Attach
`page.on("response", handler)`, filter by URL and `content-type`, and call
`response.json()` in the handler. The data arrives as typed fields instead of scraped
strings.

**How do I wait for one specific API call?** Use
`with page.expect_response(predicate) as info:` around the action that triggers it, then
read `info.value`. The predicate is a plain function matching on URL, status or method.

**Can I read the POST body a request sends?** Yes, with `page.route()`:
`route.request.post_data` gives you the body, `route.request.headers` the headers, then
call `route.continue_()` so the page proceeds normally.

**Should I block images and CSS to scrape faster?** Not on a page you need to look human
on. A real browser loads dozens of sub-resources; a client that fetches only the document
and one XHR has a request pattern no human browser produces, and that silhouette is
itself detectable.

**Why does response.json() sometimes throw?** Because the handler sees every response,
including images and HTML. Filter on `content-type` containing `application/json` before
calling it, and read the body before the page navigates away.

**Is capturing XHR more reliable than DOM scraping?** Usually, because the JSON is an
internal contract the site's own frontend depends on, while the rendered markup changes
often and can virtualize away most of the rows you wanted.

## Sources

- Playwright's [`Response`](https://playwright.dev/python/docs/api/class-response) and
  [`Route`](https://playwright.dev/python/docs/api/class-route) classes, including
  [`response.json()`](https://playwright.dev/python/docs/api/class-response#response-json)
  and [`request.post_data`](https://playwright.dev/python/docs/api/class-request#request-post-data),
  read from Playwright's own documentation rather than a rendered example, and exercised
  through the real `Browser` object this project returns unchanged.
- This project's machine-layer model of blocking, and the release gate lesson that a
  suppressed signal is a tell rather than stealth, both linked above.

**See also:** [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for the layer model this page's network caveat comes from,
[the checklist for being detected on one site](playwright-detected-as-bot.md) when a
capture run starts getting a different page than a human does, and
[how to test whether your browser is detected](how-to-test-bot-detection.md) for why an
absent signal fails the same way a wrong one does.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The abort-everything-heavy
optimization at the end is one I shipped before I measured what it did to the request
silhouette.*
