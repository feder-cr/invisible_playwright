---
title: "HTTP/2 fingerprint: the layer above the TLS handshake"
description: "The HTTP/2 SETTINGS frame, window update and pseudo-header order form a fingerprint one layer above JA3/JA4, emitted by the engine, unreachable from JavaScript."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 12
---


# HTTP/2 fingerprint: the layer above the TLS handshake

The HTTP/2 fingerprint is the pattern a client reveals when it opens an HTTP/2 session,
its SETTINGS frame, connection window update, priority tree and pseudo-header order,
one signal layer above the JA3/JA4 TLS handshake, emitted by the networking engine
before any script runs and unreachable from JavaScript. Everyone writes about JA3 and
JA4; this is the next signal down, produced by the same part of the browser and just as
far out of a script's reach. If you have matched the TLS handshake and are still refused
before a page loads, this is the layer to look at next.

This page is what an HTTP/2 connection reveals, why no header spoof and no page-level
patch can change it, what a plain HTTP library gives away that a real engine does not,
and how to check your own.

## What HTTP/2 adds on top of the TLS handshake

The TLS handshake decides which stack you are before a byte of HTTP is sent. That is
[why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md): the ClientHello
is out the door before any script exists.

HTTP/2 sits immediately above it. Once the encrypted connection is up, the very first
thing a client does is open the HTTP/2 session, and that opening exchange is not free
text you compose. It is a sequence of binary frames the networking library emits on its
own, with values baked into how that library was built and tuned. Two clients that both
"speak HTTP/2" open the session differently, in ways that are stable per implementation
and visible to the server on the very first round trip.

So there are two engine-bound fingerprints stacked on top of each other. The TLS one
says which TLS library opened the socket. The HTTP/2 one says which HTTP stack is
driving the connection. A server that checks both is asking the same question twice, and
a stack that gets one right and the other wrong answers itself.

## What actually gets fingerprinted in an HTTP/2 connection

Four things travel in that opening exchange, and each is characteristic of the
implementation.

- **The [SETTINGS frame](https://datatracker.ietf.org/doc/html/rfc9113#section-6.5.2).**
  The client announces its parameters: header table size, whether server push is enabled,
  the maximum concurrent streams it will accept, the initial window size, the maximum frame
  size, the maximum header list size. The exact set present, their order, and the values
  chosen differ between a browser and a scripting library. A browser picks one recognisable
  profile; an HTTP library picks another.
- **The initial WINDOW_UPDATE.** Right after SETTINGS, most clients grow the connection
  level flow-control window by a fixed increment. The size of that bump is a per-stack
  constant.
- **The priority tree.** Older HTTP/2 clients build a dependency and weight structure for
  streams, and the shape of that tree, or its absence, is a strong tell. Browsers and
  libraries build very different trees, or none.
- **[Pseudo-header order](https://datatracker.ietf.org/doc/html/rfc9113#section-8.3.1).**
  Every HTTP/2 request carries `:method`, `:authority`, `:scheme` and `:path` as
  pseudo-headers before the ordinary ones. The order in which a client emits those four is
  fixed per implementation, and it is one of the cheapest checks a server can run because it
  is present on every single request, not just the session opener.

None of these are things you set. They are emitted by the networking code the moment the
session starts, the same way the ClientHello is emitted the moment the socket opens.

## Why JavaScript cannot reach this layer

JavaScript cannot reach the HTTP/2 frame layer because those frames are emitted by the
networking stack before any document or script exists, the same property the TLS handshake
has and nothing else on the page shares.

`navigator.webdriver` is a value the browser reports, and a patch can change what it
reports. A canvas hash is an output the page produces, and a patch can shape the output.
The SETTINGS frame is neither. It is a property of the program that opened the
connection, sent before any document exists, and there is no API, init script or header
override that reaches it. By the time your code runs, the frames are already on the wire
and, if the server cared, already judged.

That is why `extra_http_headers`, a hand-set user agent, or any header-ordering trick
cannot help here. Those operate on the ordinary headers you compose. The HTTP/2 frame
layer underneath them is composed by the engine. You can dress the request perfectly and
still announce, one layer down, that a scripting library is the thing sending it. The
same reasoning that makes [Sec-Fetch and client hints](client-hints-sec-fetch.md) have
to agree with the engine applies here with no override at all: there is not even a knob
to get wrong.

## An HTTP library emits its own HTTP/2 fingerprint

The clearest way to see the layer is to send an HTTP/2 request from something that is not
a browser and read back what the server saw. A public HTTP/2 fingerprint endpoint returns
the frames it received as JSON.

```bash
pip install "httpx[http2]"
```

```python
import httpx

# A scripting HTTP client. It speaks HTTP/2 correctly and is not a browser.
with httpx.Client(http2=True) as client:
    r = client.get("https://example.com/")
    print(r.http_version)   # "HTTP/2"
```

The request above is valid HTTP/2. It is also unmistakable: the SETTINGS values, the
window increment and the pseudo-header order belong to that library's HTTP/2
implementation, not to any browser. You can set the user agent to a real Firefox string,
copy a browser's header order byte for byte, and it changes nothing one layer down. The
frames still say "scripting library".

This is the HTTP/2 twin of
[why a plain requests scraper is blocked before it sends a header](web-scraping-tls-fingerprint-requests-blocked.md).
The user agent is a string you write. The handshake and the frames are not.

## What a real engine emits, and how to check yours

A patched-but-real browser opens the HTTP/2 session with the engine's own networking
stack, so the SETTINGS frame, the window update, the priority tree and the pseudo-header
order are the engine's, because they are the engine. There is nothing to impersonate and
no table of frame templates to keep current, in the same way the TLS stack here is left
untouched so the handshake is a genuine Firefox handshake rather than an approximation.

Drive the browser to the same endpoint and read what it saw:

```python
import json
from invisible_playwright import InvisiblePlaywright

# seed=42 -> the same identity every run, so a network check is reproducible
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/http2-check")   # a page that echoes the frames it received
    report = page.evaluate("() => document.body.innerText")
    print(report)
```

The `browser` object is a real Playwright `Browser`, so this is ordinary Playwright once
it is launched. The point is what the endpoint reports: the HTTP/2 profile of a real
Firefox connection, not a library's.

The right way to read the result is the same rule as everywhere else in this set: do not
trust that it "looks like a browser", compare it against a stock browser of the same
version on the same machine, field by field.

```python
import json
from invisible_playwright import InvisiblePlaywright

# Read the HTTP/2 report as structured data, then diff it against a stock
# Firefox of the same version opened by hand on the same machine.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/http2-check.json")
    ours = json.loads(page.evaluate("() => document.body.innerText"))

print("SETTINGS:", ours.get("settings"))
print("WINDOW_UPDATE:", ours.get("window_update"))
print("pseudo-header order:", ours.get("pseudo_header_order"))
# Every one of these must match the stock browser's, not merely resemble a browser's.
```

If the HTTP/2 profile matches stock Firefox exactly and the JA4 string matches too, the
whole network layer is consistent with the browser the user agent claims. If either
diverges, that divergence is the tell, and it is decided before any page renders.

## The closed JA3 example proves how little it takes

It is worth being concrete about how small a network-layer delta a fingerprinter will
catch, because the HTTP/2 layer has the same sensitivity as the TLS layer beneath it.

On this project, a same-IP, same-version comparison against a stock build once turned up a
real ClientHello difference: the build offered one extra cipher suite that stock Firefox
of the same version does not. JA3 and JA4 diverged accordingly. The cause was not the TLS
code, which was never touched. It was a single boolean preference whose default is gated
by release channel: upstream had moved that default to off for release builds, and the
fork's copy of the pref declaration had not been carried forward past that change, so it
still resolved to on. A production site fingerprinting TLS handshakes treated the
one-extra-cipher ClientHello as an instant tell, on a connection that had not sent a byte
of HTTP yet.

The fix was one line, forcing that preference back to its upstream-current default, after
which the ClientHello, JA3 and JA4 measured byte-identical to stock. The lesson generalises
straight to HTTP/2: a single value out of place in the SETTINGS frame is the same kind of
delta, checked at the same pre-JavaScript moment, and the only durable defence is a
repeated parity check against the currently shipped upstream release rather than a one-time
claim that the stack is real.

## Conclusion

The HTTP/2 fingerprint is the layer above the TLS handshake and below everything a page
can touch. Its SETTINGS values, window update, priority tree and pseudo-header order are
emitted by the networking stack on the first round trip, so a header spoof cannot hide
them and a page-level patch cannot reach them. An HTTP library announces its own HTTP/2
profile no matter what user agent it wears; a real browser engine announces the browser's.
The only way to make the frames Firefox's is for the connection to be opened by Firefox.
Check yours the same way you check JA4: read the frames from the endpoint, and diff them
against a stock browser rather than reading them as a verdict.

## Short answers to the questions that lead here

**Can I change my HTTP/2 fingerprint in Playwright?** No. The SETTINGS frame and the
pseudo-header order are emitted by the networking stack when the session opens, before any
script runs, so there is nothing in Playwright or JavaScript to override.

**Does setting extra_http_headers or fixing header order fix it?** No. Those act on the
ordinary headers you compose. The HTTP/2 frame layer underneath is composed by the engine
and is a separate fingerprint entirely.

**Why does my requests or httpx scraper get blocked with a perfect user agent?** Because
the user agent is a string you set and the HTTP/2 frames are not. The library emits its
own SETTINGS and pseudo-header order, which no header spoof hides.

**Is HTTP/2 fingerprinting the same as JA3 or JA4?** No, it is one layer up. JA3 and JA4
fingerprint the TLS handshake; the HTTP/2 fingerprint reads the frames sent right after
it. A server often checks both.

**How do I test my HTTP/2 fingerprint?** Send a request to a public HTTP/2 fingerprint
endpoint from the browser or client you deploy, then from a stock browser of the same
version on the same machine, and compare the SETTINGS, window update and pseudo-header
order field by field.

**Does a residential proxy change my HTTP/2 fingerprint?** A SOCKS5 proxy forwards the TCP
stream, so your frames arrive intact and unchanged. Only a proxy that terminates the
connection and re-opens HTTP/2 itself would replace the fingerprint with its own.

## Sources

- RFC 9113, the HTTP/2 specification, [retrieved 2026-08-28](https://datatracker.ietf.org/doc/html/rfc9113),
  for the SETTINGS frame parameters (section 6.5.2), the WINDOW_UPDATE frame and the
  request pseudo-headers (section 8.3.1) described above.
- This project's own network-layer parity work, including the closed cipher-suite delta
  measured against a stock build of the same version and re-checked until the handshake
  fingerprints matched byte for byte.
- pagpeter's TrackMe, the open-source server behind the public tls.peet.ws fingerprint
  checker, [retrieved 2026-08-28](https://github.com/pagpeter/TrackMe), for how a public
  endpoint reads back the SETTINGS values, window update and pseudo-header order a
  connection actually sent.

**See also:** [why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) for
the layer directly beneath this one, [why a plain requests scraper is blocked before it
sends a header](web-scraping-tls-fingerprint-requests-blocked.md) for the non-browser
version of the same problem, and [the checklist for being detected on one
site](playwright-detected-as-bot.md), where the network layer is step six.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The networking stack is one
of the parts left deliberately untouched, so the HTTP/2 frames are the engine's own.*
