---
title: "curl_cffi vs invisible_playwright: TLS client vs browser"
description: "curl_cffi replays a browser's TLS/HTTP/2 at the byte level but no JavaScript; invisible_playwright is Firefox answering both the network handshake and JS surface."
parent: "Comparisons"
nav_order: 23
---


# curl_cffi vs invisible_playwright: TLS client vs browser

These two tools solve overlapping problems from opposite ends. `curl_cffi` is an
HTTP client that makes its TLS handshake look like a browser's. `invisible_playwright`
is an actual browser. If your only obstacle is the network handshake, the client is
lighter and faster. The moment a page runs JavaScript to inspect who is calling, the
client has nothing to show it, and that is the line this page is about.

The honest short version: `invisible_playwright` is built to look like a real Firefox
driven by a real person, which is why it clears most fingerprint, TLS and driver-layer
checks. It does not, on its own, fix a bad exit IP, a per-account quota, a rate limit,
or robotic timing. You still supply a clean proxy and human pacing. Neither tool is a
skeleton key, and any page selling you one is selling you a lawsuit.

## What curl_cffi actually does

`curl_cffi` is a Python binding over curl-impersonate, a build of curl linked against
the same TLS and HTTP/2 stacks a real browser ships. Its whole point is byte-level
impersonation: the JA3/JA4 class of TLS fingerprint, the cipher and extension ordering,
the [HTTP/2 SETTINGS frame](https://datatracker.ietf.org/doc/html/rfc9113#name-settings),
the header order. To a server reading only the handshake, a
`curl_cffi` request is indistinguishable from the browser it imitates. That is a real,
well-engineered capability, and for a plain JSON endpoint behind a network-layer check
it is often all you need.

```python
import curl_cffi

# a request whose TLS/HTTP2 handshake mimics a specific browser build
r = curl_cffi.get("https://example.com/api/data", impersonate="firefox")
print(r.status_code, r.json())
```

For the mechanics of the handshake it is imitating, see
[JA3 and JA4 TLS fingerprinting](ja3-ja4-tls-fingerprint.md), and for why a request
library gets blocked the instant that handshake disagrees with the user agent, see
[web scraping blocked by a TLS fingerprint](web-scraping-tls-fingerprint-requests-blocked.md).

## The wall: there is no JavaScript surface

`curl_cffi` has no JS engine, and that single fact decides most real cases. There is no
DOM, no `document`, no `navigator`, no canvas, no WebGL context, no event loop, no
timers. It fetches bytes and hands them back.

So any challenge that ships JavaScript and waits for the client to execute it sees an
empty surface. A script that reads
[`navigator.hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency),
draws to a canvas and hashes it, enumerates fonts, times an animation frame, or simply
sets a cookie from JS and reloads, gets nothing back from a client that cannot run it.
The handshake looked perfect and the page still fails, because the page was never
asking the network a question. It was asking the browser one.

This is not a bug in `curl_cffi`. It is the category. An HTTP client answers network
questions. It cannot answer browser questions because it is not a browser.

## What invisible_playwright answers instead

`invisible_playwright` runs a real Firefox, patched at the C++ level, driven by stock
Playwright. Because the engine is genuinely Firefox, the TLS handshake is a real Firefox
handshake for free, and so is everything a JavaScript challenge probes: the DOM exists,
canvas and WebGL render on a consistent GPU persona, fonts enumerate for the claimed
platform, the audio context answers, timers behave like a real event loop. Both layers,
the network and the JavaScript surface, read as the same real browser instead of one
real byte stream wrapped around an empty room.

Switching from plain Playwright is two lines, and the returned object is a real
Playwright `Browser`, so every method you already know works unchanged:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    html = page.content()   # after any in-page JS challenge has run
    print(html[:200])
```

The `seed=42` makes the whole identity reproducible: same GPU, same canvas hash, same
fonts, every run. That matters less for evasion than for debugging, because a failing
run you can replay is a failing run you can fix, which is the habit the
[detected-on-one-site checklist](playwright-detected-as-bot.md) is built around.

Because it drives real Playwright, you can add a proxy and let the timezone follow the
exit automatically:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

## The honest trade: a browser is not free

A real browser is heavier than a request. It uses more memory and CPU, it is slower per
page, and it needs the download of an engine once per version. If your target has no
JavaScript check at all, `curl_cffi` will beat it on throughput and simplicity, and you
should use `curl_cffi`.

And the part no tool on either side of this comparison fixes: neither one repairs a bad
egress IP, a datacenter ASN, an exhausted per-account quota, a rate limit, or timing
that reads as a machine. A flawless Firefox on an address a thousand other clients are
using this minute still loses, and so does one that fills a form in eighty milliseconds.
The browser answers the fingerprint and the handshake. You answer the IP and the pacing.
A user agent that claims Firefox over a handshake that is not Firefox's is a giveaway in
either tool if you override it by hand, which is why
[the user-agent / TLS mismatch](tls-fingerprint-user-agent-mismatch.md) has its own page.

## How to choose

Read the target before you pick the tool.

- **A plain API or a page with no client-side challenge:** `curl_cffi`. Lighter,
  faster, and the byte-level handshake is genuinely enough.
- **Anything that runs JavaScript to inspect the client:** a real browser, because the
  empty JS surface is the wall the client cannot climb.
- **You are not sure which it is:** open the page by hand with scripting off, then with
  it on, and see whether the content you need survives. If it only appears with JS, you
  need a browser.

The two are not really rivals; they sit at different points on the same axis. The
mistake is bringing a TLS client to a JavaScript fight, or a whole browser to a job a
request would have finished.

## Conclusion

`curl_cffi` impersonates a browser's network handshake at the byte level and does it
well, but it runs no JavaScript, so any in-page challenge sees an empty surface.
`invisible_playwright` is an actual Firefox, so the handshake and the JavaScript surface
both read as a real browser. That is why it clears most fingerprint, TLS and driver
checks, and it is also why it cannot, by itself, fix your IP reputation, your quota, or
your timing. Match the tool to the check, bring your own clean exit and human pacing,
and distrust anything that promises more than that.

## Short answers to the questions that lead here

**Can curl_cffi pass a JavaScript challenge?** No. It has no JS engine, so a challenge
that ships JavaScript and waits for it to run sees nothing to execute. Use a real
browser for those.

**Is invisible_playwright's TLS fingerprint as good as curl_cffi's?** It is a real
Firefox handshake because the engine is really Firefox, so there is no impersonation gap
to maintain. The difference is that it also answers the JavaScript layer.

**Why did curl_cffi work yesterday and fail today?** The likeliest change is the target
adding or arming a client-side check, which turns a network-only obstacle into one that
needs a browser. The handshake did not stop working; the question changed.

**Is a browser always the safer choice?** No. On a target with no JavaScript check, a
browser is slower and heavier for no gain, and `curl_cffi` is the right tool.

**Does invisible_playwright fix my blocked IP?** No. It makes the browser look real; it
does nothing about a datacenter address, a bad ASN, or a per-account quota. Supply a
clean residential exit yourself.

**Can I use both?** Yes, and people do: `curl_cffi` for the endpoints that only guard
the network, the browser for the pages that guard the client. Route each request to the
tool its target actually tests.

## Sources

- The `curl_cffi` project's own README and its curl-impersonate basis, read for what it
  claims: browser-grade TLS and HTTP/2 impersonation, and no JavaScript engine.
- [RFC 9113 section 6.5](https://datatracker.ietf.org/doc/html/rfc9113#name-settings),
  which defines the HTTP/2 SETTINGS frame a TLS-impersonation client has to match.
- [MDN, `navigator.hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency),
  one example of the JS-surface properties a client with no JS engine cannot answer.
- This project's release gates and fingerprint measurements, which exercise the DOM,
  canvas, WebGL, fonts and audio surfaces a request client does not have.

**See also:** [how to test whether your browser is detected](how-to-test-bot-detection.md),
[web scraping blocked by a TLS fingerprint](web-scraping-tls-fingerprint-requests-blocked.md),
and [the checklist for when one site detects you](playwright-detected-as-bot.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It looks like a real
browser driven by a real person, which is most of the job; the clean proxy and the human
pacing are the part you bring.*
