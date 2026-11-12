---
title: "Playwright ERR_BLOCKED_BY_CLIENT via page.route"
description: "net::ERR_BLOCKED_BY_CLIENT is the exact string a real ad-blocker extension produces, and page.route()-based request blocking can reproduce it byte for byte. What that means for a page's own error handling, and the two-edged detection question it raises."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 28
---


# Playwright ERR_BLOCKED_BY_CLIENT via page.route

`net::ERR_BLOCKED_BY_CLIENT` means the client itself refused to send a request,
before it ever reached the network. Chromium's own network error list defines it in
five words, code -20: "The client chose to block the request." The name is precise:
not the server, not a network failure, the client's own decision. That client is
usually a browser extension, and it is exactly the string a real ad blocker
produces on every ad and tracker request it stops. It is also, less widely known,
a string `page.route()` can produce on purpose.

## Where the string actually comes from in a page.route() handler

`route.abort()` accepts an optional error code, and Playwright's own API reference
lists `'blockedbyclient'` among the valid values, alongside `'failed'`,
`'aborted'`, `'connectionrefused'`, and the rest of the family. Call
`route.abort('blockedbyclient')` and the aborted request surfaces in Chromium as
`net::ERR_BLOCKED_BY_CLIENT`, identical to what an installed ad blocker would have
produced on the same request. Call bare `route.abort()` with no argument and you
get `'failed'` instead, Chromium's generic `net::ERR_FAILED`, a different string
for the same underlying abort.

This is not a hypothetical pairing. [`@ghostery/adblocker-playwright`](https://github.com/ghostery/adblocker),
a real, actively maintained library applying actual EasyList- and
EasyPrivacy-style filter rules inside a Playwright session, calls
`route.abort('blockedbyclient')` directly in its request handler. A tool built to
block ads and trackers through `page.route()` chose the one error code that makes
its blocking indistinguishable, at the network layer, from a real ad-blocker
extension. It is worth knowing which code your own blocking logic passes, because
the string in your logs depends on it.

## Not the same thing as net::ERR_ABORTED

[`net::ERR_ABORTED`](err-aborted-playwright.md), covered separately on this site,
is a different code (-3, "an operation was aborted") for a different situation: an
in-flight *navigation* getting cancelled, by a download, a redirect, a second
`goto()`, or a `route.abort()` call that unintentionally caught the navigation
request itself rather than a sub-resource. `ERR_BLOCKED_BY_CLIENT` is what shows up
on a *sub-resource* request, an ad script, a tracking pixel, an image, that your
own route handler (or a real extension) deliberately refused, while the page
navigation around it completes normally. If your `page.goto()` itself is failing,
that is the other page's territory. If a specific request inside an otherwise
loaded page is failing, and your code or a library is calling `route.abort()` on
it, this is the right page.

## How the page's own JavaScript sees it

Because `route.abort()` operates in the real network stack rather than mocking
anything at the JavaScript layer, the page's own code experiences the block exactly
as it would a real extension's: a `fetch()` call rejects with a generic
network-error `TypeError` and no further detail, an `XMLHttpRequest` fires its
`error` event with `status` left at `0`, and an `<img>` or `<script>` tag fires its
own `error` event and never loads. None of these code paths know or care whether a
browser extension or a route handler made the decision, which is exactly why a
library choosing this error code produces genuinely indistinguishable
JavaScript-visible behavior.

## The two-edged consideration

Ad-blocker use is itself a real, commonly checked signal, not a hypothetical one.
Independent research into this space, including [Fingerprint's own writeup on
ad-blocker fingerprinting](https://fingerprint.com/blog/ad-blocker-fingerprinting/),
documents two broad detection families sites actually use: watching whether known
ad-network network requests simply never happen, and, described as the more
reliable and more commonly preferred approach, planting a "bait" or "honeypot" DOM
element that matches a known filter-list selector and checking whether the element
got hidden, typically by testing `offsetParent === null` after the page settles.
That second family exists specifically because pure network-request detection, the
same source notes, is "problematic" on its own: it requires attempting to load a
resource and watching how the attempt resolves, which is slow and visible in the
page's own network panel.

This cuts both ways for a `page.route()`-based blocking setup:

- **At the pure network-failure level, blocking via `route.abort('blockedbyclient')`
  is not distinguishable from a real ad-blocker extension.** Both produce the exact
  same error string on the exact same class of request, because that is what the
  error code is for. A detector that only asks "did this ad request fail" learns
  nothing about which of the two happened.
- **At the cosmetic-filtering level, the two are not equivalent.** A real
  ad-blocker extension commonly does more than stop the network request: it also
  injects CSS to hide the placeholder element the ad would have filled, which is
  exactly what the bait-element/`offsetParent` check is built to catch.
  `page.route()` only intercepts the network layer and never touches the DOM, so a
  bait element planted to catch cosmetic hiding stays visible, `offsetParent`
  intact, even while the matching request fails with `ERR_BLOCKED_BY_CLIENT` right
  next to it. A site checking both signals together could read that mismatch,
  failed ad requests with no cosmetic hiding to match, as a tell of its own.

None of this is a reason to avoid `route.abort()`; it is a reason to know what
signal you are actually producing, and that a network-only view of "ad blocked or
not" is not the whole picture a real detector might be checking.

## What this looks like against the real engine here, Firefox

`net::ERR_BLOCKED_BY_CLIENT` is Chromium's own naming, and `invisible_playwright`
drives a [patched Firefox](does-playwright-support-firefox-stealth.md), not
Chromium. Playwright's cross-browser `route.abort('blockedbyclient')` call is
identical on every engine, and [a real Playwright report](https://github.com/microsoft/playwright/issues/22332)
confirms Firefox handles it correctly where your script actually reads it:
`onerror` fires on the affected element the same way it does in Chromium, which is
notably not true of WebKit for this same pair of error codes. What differs is the
string underneath. Firefox's own networking layer reports a cancelled request
through its `nsIRequest`/Necko error family, commonly `NS_BINDING_ABORTED` rather
than a Chromium-style `net::` code; [a separate real report](https://github.com/microsoft/playwright/issues/20749)
shows that exact string on a Firefox navigation Playwright cancelled, and Mozilla's
own bug tracker documents the same string for requests an extension blocked
outright. Grepping raw browser logs against this project's engine, search for
`NS_BINDING_ABORTED`; reading Playwright's own `route`/`request` objects,
`'blockedbyclient'` is what comes back regardless of which engine produced it.

## Diagnostic checklist

1. **Confirm which error code your own code is passing.** `ERR_BLOCKED_BY_CLIENT`
   specifically means something called `route.abort('blockedbyclient')`, or
   equivalent, on that request. Bare `route.abort()` produces `ERR_FAILED` instead;
   if you are seeing the former and did not write it, a dependency is doing it.
2. **Grep your dependency tree for an ad-blocking library** such as
   `@ghostery/adblocker-playwright` or a hand-rolled filter list before assuming
   your own route handler is the source.
3. **Read `route.request().resourceType()` and the URL on the aborted request**,
   via `DEBUG=pw:api`, to confirm it is the sub-resource you intended to block and
   not, for example, a redirect target the handler's glob pattern caught by
   accident.
4. **If the failure is on the navigation itself rather than a sub-resource**,
   you are looking at [`net::ERR_ABORTED`](err-aborted-playwright.md), not this
   error; the fix there is narrowing the route pattern so it does not catch
   in-flight navigation traffic.
5. **If you are testing your own page's ad-blocker-detection logic**, drive the
   comparison with a real extension loaded in a persistent context alongside your
   `page.route()` version, and compare both the network log and any DOM-visibility
   check the page runs, rather than assuming one implies the other.

## What this does and does not fix

`page.route()` is a testing and traffic-shaping tool, the same honest boundary that
runs through [blocking images to speed up
scraping](block-images-speed-up-playwright-scraping-page-route.md) and
[intercepting and mocking requests](intercept-and-mock-requests-page-route-playwright.md)
on this site. It lets you stub, drop, or rewrite requests on a browser that already
reads as a genuine Firefox. It does not make your blocking choices invisible to a
page that inspects its own network failures or its own DOM for the shape a real ad
blocker leaves behind; that shape is a property of what you chose to block and how,
not of the engine underneath it.

## Short answers to the questions that lead here

**What does net::ERR_BLOCKED_BY_CLIENT mean?** The client itself refused to send
the request before it reached the network, most commonly a browser extension like
an ad blocker, or a `page.route()` handler that called
`route.abort('blockedbyclient')`.

**Why does my page.route() blocking produce this exact error?** Because you (or a
library you depend on) passed `'blockedbyclient'` as the error code to
`route.abort()`. Passing no code at all produces `ERR_FAILED` instead, a different
string for the same abort.

**Is this the same as net::ERR_ABORTED?** No. `ERR_ABORTED` is a different code for
a cancelled navigation; `ERR_BLOCKED_BY_CLIENT` is for a sub-resource request the
client deliberately refused to send while the surrounding page load continues.

**Can a site tell my scraper apart from a real ad-blocker user?** Not from the
failed network request alone; that signal is identical either way by design. A
site checking whether a bait element's cosmetic hiding matches the failed requests
could notice the difference, since `page.route()` never touches the DOM the way a
real extension's CSS injection does.

**Does invisible_playwright add anything to how page.route() behaves here?** No.
It is stock Playwright behavior, unchanged; this project's fingerprint and driver
work sits above the network-interception layer entirely.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_BLOCKED_BY_CLIENT` (-20), `ERR_ABORTED` (-3) and `ERR_FAILED` (-2) and
  their exact definitions, retrieved 2026-08-30.
- Playwright's own [`Route.abort()` API reference](https://playwright.dev/python/docs/api/class-route#route-abort),
  for the full list of valid `error_code` values including `'blockedbyclient'` and
  the `'failed'` default, retrieved 2026-08-30.
- [ghostery/adblocker](https://github.com/ghostery/adblocker/blob/master/packages/adblocker-playwright/src/index.ts),
  the real, maintained Playwright ad-blocking integration whose request handler
  calls `route.abort('blockedbyclient')` directly, verified in the source this
  session.
- [microsoft/playwright#23598](https://github.com/microsoft/playwright/issues/23598),
  a real, verified report of `net::ERR_BLOCKED_BY_CLIENT` surfacing from a
  `route.continue()` call in Chromium specifically, confirming the error is not
  limited to `route.abort()` alone.
- Fingerprint's own [writeup on ad-blocker
  fingerprinting](https://fingerprint.com/blog/ad-blocker-fingerprinting/), for the
  distinction between network-request-based ad-blocker detection and the
  bait-element/`offsetParent` cosmetic-hiding check sites prefer instead.
- [microsoft/playwright#22332](https://github.com/microsoft/playwright/issues/22332),
  confirming Firefox fires `onerror` correctly for both `'blockedbyclient'` and
  `'aborted'` error codes, unlike WebKit, which does not for the same pair.
- [microsoft/playwright#20749](https://github.com/microsoft/playwright/issues/20749),
  a real report showing Firefox's own `NS_BINDING_ABORTED` string on a cancelled
  navigation, the same error family Firefox uses for a blocked request underneath
  Playwright's cross-browser `'blockedbyclient'` value.

**See also:** [Block images to speed up scraping (and when not to)](block-images-speed-up-playwright-scraping-page-route.md)
for the related resource-type blocking case and its own request-waterfall tell,
[Intercept and mock network requests with page.route](intercept-and-mock-requests-page-route-playwright.md)
for the full `fulfill`/`abort`/`continue_` picture this page is one caveat of,
[net::ERR_ABORTED in Playwright](err-aborted-playwright.md) for the navigation-level
sibling this error is easy to confuse it with, and [the checklist for being
detected on one site](playwright-detected-as-bot.md) for where request-blocking
behavior sits among the other signals a site can check.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The error code is
upstream Chromium naming; the fact that a route handler and a real extension can
produce it identically is the part worth knowing before you rely on either.*
