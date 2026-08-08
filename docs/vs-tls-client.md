---
title: "tls-client vs a real browser: when TLS is enough"
description: "When tls-client's JA3/HTTP2 socket spoofing beats a real browser on JSON and HTML endpoints, and the seam where JS execution forces a switch to real Firefox."
parent: "Comparisons"
nav_order: 32
---


# tls-client vs a real browser: when TLS is enough

`tls-client` is a Go library with Python bindings that reproduces a browser's TLS
handshake and HTTP/2 settings at the socket level. It is a genuinely good tool, and for
a large class of targets it is the *right* tool, lighter and faster than anything that
opens a real browser. This page is about where that class ends.

The short version: pure-TLS spoofing wins the moment the data you want is already sitting
in a JSON or HTML response, and it loses the moment the page needs JavaScript to run
before that data exists. That seam is the whole comparison.

## What tls-client actually does

A plain HTTP client in Python sends a TLS
[`ClientHello`](https://datatracker.ietf.org/doc/html/rfc8446#section-4.1.2) that looks
nothing like a browser: different cipher order, different extensions, a different
[HTTP/2 settings frame](https://datatracker.ietf.org/doc/html/rfc9113#name-settings).
That signature, the [JA3/JA4 handshake fingerprint](ja3-ja4-tls-fingerprint.md), is read
before a single byte of your request body is parsed, so a request can be rejected purely
for how it opened the connection.

`tls-client` fixes that. It ships a set of client profiles that reproduce the exact
handshake of a specific browser build, so the `ClientHello`, the extension order and the
HTTP/2 frame all read as that browser. It is actively developed and its profile list
tracks current browser releases. If your problem is
[a request being blocked for its TLS fingerprint](web-scraping-tls-fingerprint-requests-blocked.md),
this is a direct, low-overhead answer, and it does it without the memory and startup cost
of a browser process.

Where it fits perfectly:

- A JSON API endpoint you can call directly.
- Server-rendered HTML you can parse from the response body.
- High-volume fetching where per-request cost matters and the response is complete on
  arrival.

For all of these, opening a browser is pure waste. Send the request, read the body, done.

## The ceiling: it never runs the page

`tls-client` is an HTTP client. It never builds a DOM, never runs JavaScript, never fires
an event. That is the source of both its speed and its ceiling.

The moment a response is not the data but a *program that fetches the data*, the socket
client is stuck. Concretely, it cannot handle any of these on its own:

- **Client-side rendering.** The HTML is a near-empty shell and the content is assembled
  by JavaScript from later calls. `tls-client` receives the shell.
- **A JS challenge.** The first response is a script whose job is to compute a token,
  set a cookie, and reload. Nothing runs, so nothing is computed.
- **JavaScript fingerprinting.** Canvas, WebGL, audio, font enumeration, and the checks
  that libraries like CreepJS, BotD, FingerprintJS, sannysoft and BrowserLeaks run all
  execute in a page. A client with no page has no answer to give, and "no answer" is
  itself a signal.

You can sometimes climb this ceiling by hand, tracing the JS, re-implementing the token
math, replaying the internal XHR calls the page would have made. That works right up until
the logic changes, and then you are maintaining a reverse-engineered reimplementation of
someone else's front end. Sometimes that trade is worth it. Often it is a second job.

## Where a real browser starts

A full browser has no ceiling here because it *is* the page. It runs the client-side
render, executes the challenge script, computes the token, answers every JavaScript
fingerprint probe with real values, because they are real. That is the entire reason to
pay the weight.

The catch, historically, is that automating a browser reintroduces the network-layer
problem you just solved. A stock automation stack can present a JavaScript identity that
says "Firefox on Windows" while the underlying handshake says something else, and
[a TLS fingerprint that disagrees with the User-Agent](tls-fingerprint-user-agent-mismatch.md)
is a decisive mismatch. So the naive version of "just use a browser" can be *worse* than
`tls-client`: it fixes the JS layer and breaks the TLS layer.

`invisible_playwright` is built around closing exactly that gap. It is a real Firefox
patched at the C++ level and driven by stock Playwright, so the TLS handshake is a real
Firefox handshake because it comes from a real Firefox, and it already matches the
JavaScript identity the same process reports. There is no second fingerprint to keep in
sync, because there is no impersonation layer: one browser answers both the socket
question and the JS question with the same true answer.

Switching from stock Playwright is a two-line change, and every method on the returned
object is the standard Playwright API:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # the page's JavaScript runs; content assembled client-side is now in the DOM
    html = page.content()
    print(html[:500])
```

The `browser` object is a real `playwright.sync_api.Browser`, so anything you already do
in Playwright works unchanged. The `seed` makes the whole generated identity reproducible,
which is what lets a failing run be replayed instead of guessed at.

## The honest caveat: a browser fixes fingerprint, not everything

Being a genuine browser is why `invisible_playwright` passes most *fingerprint-level*
checks: the TLS, the driver surface and the JavaScript environment all read as a real
Firefox because they are one. But looking like a real browser is not the same as looking
like a real *session*, and it is worth being precise about what it does not do:

- **IP reputation.** A perfect browser on a known datacenter address is still on a known
  datacenter address. You supply a clean proxy; the browser cannot.
- **Rate limits and per-account quotas.** These are counted server-side and no client
  property changes the count. Ten thousand requests an hour looks like ten thousand
  requests an hour whatever opened them.
- **Behaviour and timing.** Instant navigation, uniform intervals, a form filled in
  eighty milliseconds. `invisible_playwright` moves the mouse on Bezier curves rather than
  teleporting it, but pacing across a session is yours to shape.

Same discipline as any other tool: the browser removes the fingerprint and driver tells;
the reader still brings a clean exit and human pacing. Nobody is undetectable, and any
tool that tells you otherwise is selling something.

## How to choose, in one rule

Ask one question: **does the data already exist in the response, or does the page have to
run to produce it?**

- If a direct request returns the JSON or the HTML you need, use `tls-client` (or, if you
  are staying in Python's request idiom, [curl-cffi](vs-curl-cffi.md)). It is lighter,
  faster, and cheaper to run at scale, and a browser buys you nothing.
- If the content is rendered client-side, gated behind a JS challenge, or the target runs
  JavaScript fingerprinting, a socket client cannot see the finished page. That is where a
  real browser earns its weight, and where a browser whose TLS already matches its JS
  identity earns it without reopening the network-layer problem.

Many real pipelines are both: `tls-client` for the plain endpoints, a real browser only
for the pages that need one. Using the heavy tool everywhere is slow; using the light tool
everywhere hits the ceiling above. The split is the point.

## Conclusion

`tls-client` solves a specific, real problem well: it makes an HTTP client's handshake
indistinguishable from a browser's, which is exactly enough when the data is in the
response. Its ceiling is equally specific: it never runs the page, so client-side
rendering, JS challenges and JavaScript fingerprinting are out of reach by construction.

A real browser has no such ceiling because it is the page, and `invisible_playwright`
adds the one thing a naive browser automation misses, a TLS fingerprint that already
agrees with the JavaScript identity, so you do not trade the network layer back to fix the
JS layer. Pick the socket client for response data, the browser for pages that have to
run, and give both a clean IP and human pacing regardless.

## Short answers to the questions that lead here

**Is tls-client enough on its own?** For JSON and server-rendered HTML endpoints, often
yes, and it is the lighter choice. For anything that renders client-side or runs a JS
challenge, no, because it never executes the page.

**Why not just use tls-client for everything?** Because it cannot see content that only
exists after JavaScript runs. A near-empty HTML shell is all a socket client receives from
a client-side-rendered page.

**Does a real browser make tls-client obsolete?** No. When the data is already in the
response, a browser is pure overhead. The two cover different halves of the same pipeline.

**Doesn't automating a browser break the TLS fingerprint?** It can. Stock automation can
present a browser JS identity over a non-browser handshake. `invisible_playwright` avoids
that by being a real Firefox, so the handshake and the JS identity are the same browser.

**Will a real browser get me past every check?** No. It handles fingerprint, TLS and
driver-layer tells because it reads as a genuine browser. It does not fix IP reputation,
rate limits, quotas or session behaviour, which you supply.

**How do I decide between them per target?** Ask whether the data is in the response or
whether the page must run to create it. Response data goes to the socket client; a page
that must run goes to the browser.

## Sources

- The `tls-client` project's own repository and its documented client-profile list, read
  from source rather than from a summary, for what it reproduces at the handshake and
  HTTP/2 layer.
- [RFC 8446 section 4.1.2](https://datatracker.ietf.org/doc/html/rfc8446#section-4.1.2),
  which defines the TLS `ClientHello` message a handshake fingerprint is read from.
- [RFC 9113 section 6.5](https://datatracker.ietf.org/doc/html/rfc9113#name-settings),
  which defines the HTTP/2 SETTINGS frame a TLS-impersonation client also has to match.
- This project's own fingerprint and network gates, for the claim that a browser's TLS
  handshake and its JavaScript identity come from the same process.

**See also:** [JA3 and JA4 TLS fingerprinting](ja3-ja4-tls-fingerprint.md) for what a
handshake actually reveals, [why a TLS/User-Agent mismatch is decisive](tls-fingerprint-user-agent-mismatch.md)
for the trap naive browser automation falls into, and [how to test bot detection without a
false pass](how-to-test-bot-detection.md) for verifying any of this against a stock
browser rather than a verdict.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The rule at the end, socket
client for response data and a browser only for pages that must run, is how the pipeline
here is actually split.*
