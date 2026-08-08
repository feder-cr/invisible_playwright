---
title: "Intercept and mock network requests with page.route"
description: "Use Playwright page.route in invisible_playwright to intercept, mock, abort, or rewrite network requests, with the caveat on forged responses and headers."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 31
---


# Intercept and mock network requests with page.route

`page.route` is the Playwright feature for catching requests before they leave the
browser and deciding what happens to them: return a canned body, drop the request, or
let it through with edited headers. Because invisible_playwright is stock Playwright
with a patched Firefox underneath, `page.route` works exactly as it does upstream. There
is no wrapped API to learn and no special method to call.

This page shows the three things a route handler can do, with runnable examples on the
real launch API, and then the one honest caveat that matters when the browser is also
trying to look like a real person: a response you forge by hand, or a header you edit by
hand, can contradict the request pattern the rest of the browser produces.

## The shortest useful example

A handler is a function you register against a URL glob. Playwright calls it for every
matching request, and the handler decides the outcome. Here it returns a fixed JSON body
for one endpoint and lets everything else load normally:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def handle_status(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"status": "ready"}',
        )

    page.route("**/api/status", handle_status)
    page.goto("https://example.com")
```

The `browser` here is a real `playwright.sync_api.Browser`, so
[`page.route`](https://playwright.dev/python/docs/api/class-page#page-route),
[`route.fulfill`](https://playwright.dev/python/docs/api/class-route#route-fulfill),
[`route.abort`](https://playwright.dev/python/docs/api/class-route#route-abort) and
[`route.continue_`](https://playwright.dev/python/docs/api/class-route#route-continue) are
the upstream methods with the upstream signatures. The `seed=42` argument only fixes the
fingerprint so the run is reproducible; it has nothing to do with routing. Everything below
is plain Playwright.

## fulfill: return a canned body

`route.fulfill` answers the request yourself, without it ever reaching the network. This
is how you stub a slow or flaky endpoint in a test, or pin a response so a run is
deterministic:

```python
def stub_slow_endpoint(route):
    route.fulfill(
        status=200,
        content_type="application/json",
        body='{"items": [], "cached": true}',
    )

page.route("**/api/search**", stub_slow_endpoint)
```

You can also force an error path that is hard to trigger for real, which is often the
whole point of the test:

```python
page.route("**/api/checkout", lambda route: route.fulfill(status=503, body="down"))
```

Everything not matched by a registered glob still goes out over the network as usual, so
you are stubbing a hole in an otherwise normal page rather than replacing the page.

## abort: drop the request

`route.abort` refuses the request. The common use is speed: block images, fonts and
media you do not need so a scrape or a test runs faster and lighter.

```python
def drop_heavy_assets(route):
    if route.request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()

page.route("**/*", drop_heavy_assets)
```

Note the `else: route.continue_()`. A handler registered against `**/*` sees every
request, so it has to explicitly continue the ones it does not want to abort, or nothing
loads at all. `route.request.resource_type` is Playwright's own classification, so you
do not have to match file extensions by hand.

## continue: let it through with edits

`route.continue_` sends the request on, optionally with a changed URL, method, POST body
or headers. Adding a debugging header is the tame version:

```python
def tag_api_calls(route):
    headers = {**route.request.headers, "x-debug-run": "42"}
    route.continue_(headers=headers)

page.route("**/api/**", tag_api_calls)
```

This is the most powerful of the three and also the one to be most careful with, for the
reason in the next section. Editing what leaves the browser is exactly the operation that
can put your request out of step with the browser presenting it.

## The honest caveat: a forged response is its own signal

invisible_playwright is built to look like a real Firefox driven by a real person, and
that is why it passes most fingerprint, driver-layer and TLS checks: those surfaces read
as a genuine browser because they come from one. `page.route` does not change any of that.
What it changes is the shape of your traffic, and traffic shape is a surface too.

Two concrete ways route handlers can undo the realness the rest of the stack provides:

- **Fulfilling a request a real browser would send over the wire.** If you locally
  fulfill an analytics beacon or a telemetry call so it "succeeds" instantly with a body
  you wrote, you have produced a request pattern a real browser on a real network never
  produces: the call resolves with impossible timing, or a third-party endpoint answers
  with a body it would never send. A site that watches request patterns can notice the
  browser that never actually talks to the services every other visitor talks to. If the
  goal is realness, abort those requests or leave them alone; do not hand-fulfill them.
- **Editing headers into a shape the browser would not send.** `route.continue_(headers=...)`
  lets you write any header you like, and it is easy to write a set that disagrees with the
  browser underneath. Firefox emits a specific header order, a specific set of
  [client hints and Sec-Fetch fields](client-hints-sec-fetch.md), and an
  [Accept-Language that has to match navigator.languages](accept-language-navigator-languages.md).
  A hand-built header dict can contradict all three at once. And none of it touches the
  [TLS handshake](ja3-ja4-tls-fingerprint.md), which is decided below JavaScript, so a
  User-Agent you rewrite in a header still ships on the original browser's handshake.

The safe uses are the ones that keep the browser's own request pattern intact: stubbing
your own backend in a test, dropping assets you do not need, tagging requests for your own
logging. The risky uses are the ones that forge what a third party would have sent, or
edit the fields the browser is careful to get right. Route the requests you own; leave the
ones the site expects the browser to make.

## What this does and does not fix

`page.route` is a testing and traffic-shaping tool, not a stealth feature. It is worth
being explicit about the boundary, the same one that runs through
[the one-site detection checklist](playwright-detected-as-bot.md):

- It **does** let you stub endpoints, force error paths, and drop dead weight, on a
  browser that already reads as genuine.
- It **does not** fix IP reputation, per-account quotas, rate limits, or behaviour and
  timing. Those you supply yourself: a clean proxy set through
  [the proxy configuration](configuration.md), and human pacing. A forged response on a
  known-bad exit IP is still on a known-bad exit IP.

The fingerprint, driver layer and TLS read as a real Firefox because they are one. The
network reputation and the pace of the session are yours to get right, and `page.route`
neither helps nor hurts there.

## Conclusion

Because invisible_playwright is stock Playwright, `page.route` is the upstream feature with
nothing added: register a handler per URL glob, and `fulfill`, `abort` or `continue_` per
request. Use it to stub what you own and drop what you do not, and keep it away from
requests a real browser would send to someone else, because a forged response and a
hand-edited header are the two ways to make an otherwise-real browser produce a pattern no
real browser produces. Test what the change touches the way you would test any other
surface: [compare against a stock browser](how-to-test-bot-detection.md) rather than
trusting that it looks fine.

## Short answers to the questions that lead here

**Does page.route work in invisible_playwright?** Yes, unchanged. It is stock Playwright,
so `page.route`, `route.fulfill`, `route.abort` and `route.continue_` behave exactly as
documented upstream.

**How do I return fake JSON for one endpoint?** Register a handler on the URL glob and call
`route.fulfill(status=200, content_type="application/json", body=...)`. Everything not
matched still loads normally.

**How do I block images to make a scrape faster?** Abort by resource type inside a `**/*`
handler, and `route.continue_()` the rest. Check `route.request.resource_type` rather than
matching extensions.

**Can I change request headers?** Yes, with `route.continue_(headers=...)`, but a
hand-built header set can contradict the browser's real client hints, Accept-Language and
header order, and it never changes the TLS handshake. Edit sparingly.

**Will mocking requests make me look more like a bot?** It can. Locally fulfilling a call a
real browser sends over the wire produces a request pattern no real browser produces. Stub
your own backend; do not hand-fulfill third-party beacons.

**Does intercepting requests fix my IP getting blocked?** No. Routing shapes traffic; it
does nothing for IP reputation, quotas or rate limits. Those need a clean proxy and human
pacing, which you supply.

## Sources

- Playwright's own [`page.route`](https://playwright.dev/python/docs/api/class-page#page-route),
  [`Route.fulfill`](https://playwright.dev/python/docs/api/class-route#route-fulfill),
  [`Route.abort`](https://playwright.dev/python/docs/api/class-route#route-abort) and
  [`Route.continue`](https://playwright.dev/python/docs/api/class-route#route-continue)
  documentation, which invisible_playwright uses unchanged.
- This project's stealth notes on request-shaped signals: client hints, Accept-Language
  consistency, and the TLS handshake that no header edit reaches.

**See also:** [the quickstart two-line launch](quickstart.md) for the API these examples
run on, [the one-site detection checklist](playwright-detected-as-bot.md) for where traffic
shape sits among the other causes, and [how to test without a false pass](how-to-test-bot-detection.md)
for the compare-against-stock method to check any change like this.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. page.route is upstream
Playwright; the caveat about forged responses is ours, learned from watching what a real
browser's traffic actually looks like.*
