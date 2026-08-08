---
title: "Wait for a specific API response in Playwright"
description: "How to wait for a specific API response in Playwright with page.expect_response and a URL predicate, read the JSON at the source, and what waiting cannot fix."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 93
---


# Wait for a specific API response in Playwright

To wait for a specific API response in Playwright, wrap the action that triggers the
call in `page.expect_response` with a URL predicate that matches the request you want,
then read `.json()` on the `Response` object it hands back - no DOM parsing involved.
Most of the data you want to scrape does not live in the HTML in the first place. It
arrives in a background fetch, gets turned into DOM nodes by JavaScript, and the values
you care about are already in that JSON before a single element renders. Waiting for
the rendered DOM means waiting for, and then re-parsing, a worse copy of data the
browser already received.

Playwright can hand you the original response instead. This page covers the URL
predicate to select the request you want, reading the JSON directly, and the honest
limit of the technique: it waits for a response, it does not make a rejected request
succeed.

## Wait for the response, not the rendered DOM

The usual pattern is a polling loop: click something, then wait for a selector to
appear, then scrape the text back out of the DOM. That works, but it has three
problems. It waits for rendering you do not need. It reads numbers that have been
formatted for humans and have to be parsed back. And it is fragile, because the markup
around the value changes far more often than the API that produced it.

The response is the source. If you can grab it as it arrives, you skip the render, you
get typed JSON instead of display strings, and your extractor stops breaking every time
the site adjusts its layout. Playwright exposes exactly this through
`page.expect_response`.

## page.expect_response with a URL predicate

`page.expect_response` is a context manager. You open it *around* the action that
triggers the request, and it returns an object whose `.value` is the matching
`Response` once it arrives:

```python
with page.expect_response(lambda r: "/api/items" in r.url) as resp_info:
    page.click("#load-more")   # this click triggers the fetch

response = resp_info.value      # a Playwright Response object
data = response.json()          # the parsed JSON body, no DOM involved
```

The predicate is any callable that takes a `Response` and returns a boolean, so you are
not limited to a substring. You can match on status, method, or a query parameter:

```python
with page.expect_response(
    lambda r: "/api/items" in r.url and r.request.method == "GET" and r.status == 200
) as resp_info:
    page.click("#load-more")

data = resp_info.value.json()
```

The important part is that the `with` block wraps the trigger. `expect_response` starts
listening *before* the click fires, so there is no race where the response arrives
before you began waiting for it. Put the action that causes the request inside the
block, and read `.value` after it.

## Reading the JSON at the source, end to end

Here is the whole thing against a page that loads its data over a background call.
Switching from stock Playwright to invisible_playwright is the two-line launch change;
everything after the `with` is the same Playwright API you already use, because the
object you get back is a real Playwright `Browser`.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog")

    # Wait for the exact XHR/fetch the page fires, and read its body.
    with page.expect_response(lambda r: "/api/items" in r.url) as resp_info:
        page.click("#load-more")

    response = resp_info.value
    print(response.status)          # 200
    items = response.json()["items"]
    for item in items:
        print(item["id"], item["price"])   # typed JSON, never a display string
```

You never touched a selector for the data itself. The `#load-more` click is only there
to trigger the request; the values come straight from `response.json()`. That is the
whole point of grabbing the response at the source instead of scraping the DOM it
produces.

`seed=42` pins the fingerprint so the run is reproducible: the same seed gives the same
GPU, canvas, audio and font set every time, which matters the moment a run fails and you
need to replay it rather than guess. See [Quickstart](quickstart.md) for what the seed
controls.

## What waiting does not fix

Here is the caveat, and it is the whole reason this page is not just a syntax reminder.

`expect_response` resolves when the response *arrives*, not when the page finishes
rendering it, and it does not retry a request the site rejected. If the endpoint answers
with a challenge page, a `403`, or an empty body, `expect_response` will hand you that
challenge or that empty body, cleanly and on time. Waiting harder, raising the timeout,
adding a retry loop around the `with` block: none of that changes what the server
decided to return. The request already went out with a fingerprint, a TLS handshake and
an IP behind it, and the response is a verdict on those, not on your patience.

So when the JSON comes back empty or challenged, the fix is upstream of the wait. This
is where invisible_playwright is designed to help, and where it is honest about what it
does not touch:

- **What it does.** The engine is a Firefox patched at the C++ level and driven by stock
  Playwright, so the fingerprint, the TLS handshake and the driver layer read as a
  genuine Firefox rather than an automation tool. That is why the request in the first
  place looks like it came from a real browser, and why it passes most fingerprint and
  driver-level checks. A blocked request that becomes a normal `200` after you switch
  engines was being rejected on one of those layers.
- **What it does not.** It does not fix IP reputation, per-account quotas, rate limits,
  or the timing and behaviour of the session. A perfect browser on a datacenter address
  that a thousand other clients are using this minute is still on that address. Those you
  supply: a clean residential exit, human pacing, and a request rhythm that is not a
  metronome. If the empty body is the site rate-limiting you, no wait and no fingerprint
  will change it, only slowing down and rotating the exit will.

The short version: `expect_response` decides *when* you read the answer. It has no say in
*what* the answer is. When the answer is a challenge, look at the fingerprint and the IP
behind the request, per the [detection checklist](playwright-detected-as-bot.md), not at
the wait.

## Async, and matching more than one response

The async API is the same shape with `await`, which is what you want when you are
fetching many pages concurrently:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com/catalog")

    async with page.expect_response(lambda r: "/api/items" in r.url) as resp_info:
        await page.click("#load-more")

    response = await resp_info.value
    items = (await response.json())["items"]
```

If one action triggers several calls and you want a particular one, tighten the
predicate rather than taking the first match. Match on a query parameter, a path segment
or the status, and remember the predicate runs on every response the page receives, so
make it specific enough that only the request you want returns `True`. This is the same
tool you reach for when a page loads its data in batches on
[infinite scroll](how-to-scrape-infinite-scroll-playwright.md): each scroll fires a
request, and each request has a response you can wait for by URL.

## Conclusion

`page.expect_response` with a URL predicate lets you read the JSON a page fetches at its
source, wrapping the action that triggers the request and returning the matching
`Response` so you skip the render and the re-parsing entirely. Reach for it whenever the
data you want arrives over a background call, which today is most of it.

Just keep the boundary clear. The wait controls timing, not outcome. When the body comes
back empty or challenged, that is the request's fingerprint and exit talking, and the fix
lives upstream of `expect_response`: a browser that looks real, which invisible_playwright
provides, plus a clean exit and human pacing, which you provide.

## Short answers to the questions that lead here

**How do I wait for a specific API call in Playwright?** Open
`page.expect_response(lambda r: "/api/items" in r.url)` as a context manager around the
action that triggers the call, then read `.value` for the `Response` and `.json()` for
the body.

**Should I wait for the response or for a DOM element?** The response, when you want the
data. It is the source, it is typed JSON rather than formatted display strings, and it
does not break when the site changes its markup.

**Does the predicate have to be a URL substring?** No. It is any callable taking a
`Response`, so you can match on status, method, or a query parameter as well as the URL.

**expect_response times out even though the page loads. Why?** Because the request never
matched your predicate, or it fired before the `with` block opened. Put the triggering
action inside the block, and loosen or correct the predicate.

**The response comes back empty or is a challenge. Will a longer timeout fix it?** No.
`expect_response` returns whatever the server sent; it does not retry a rejected request.
Look at the fingerprint and the IP behind the request, not the wait.

**Does invisible_playwright guarantee the API returns data?** No, and nothing honestly
can. It makes the request look like a real Firefox, which clears the fingerprint and
driver layers. IP reputation, rate limits and pacing are still yours to handle.

## Sources

- Playwright's documented [`page.expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`Response`](https://playwright.dev/python/docs/api/class-response) API, used
  exactly as upstream, because invisible_playwright returns a real Playwright `Browser`.
- This project's own measurements on request-triggered fetches: the response arrives on
  the network before it is rendered, and a rejected request returns its rejection to
  `expect_response` regardless of how long you wait.

**See also:** [scraping behind a login](how-to-scrape-behind-login-playwright.md) for
sessions where the API needs a cookie first, [why you get blocked and buying a proxy is
not the first fix](web-scraping-getting-blocked-proxies.md), and
[how to test bot detection without a false pass](how-to-test-bot-detection.md) for
confirming the request looks real before you blame the wait.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It makes the request look
like a real browser; the clean exit and the human pacing are still yours to bring.*
