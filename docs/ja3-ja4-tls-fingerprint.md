---
title: "JA3 and JA4: why a TLS fingerprint cannot be patched"
description: "A TLS fingerprint is decided before your code runs and before a single header is sent, which is why no stealth plugin touches it. What JA3 and JA4 measure, why JA3 decayed, and the two things that change the answer."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 6
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Guides",
      "item": "https://feder-cr.github.io/invisible_playwright/guides.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Network, Proxy and WebRTC",
      "item": "https://feder-cr.github.io/invisible_playwright/guides-network-proxy-webrtc.html"
    },
    {
      "@type": "ListItem",
      "position": 4,
      "name": "JA3 and JA4: why a TLS fingerprint cannot be patched"
    }
  ]
}
</script>

# JA3 and JA4: why a TLS fingerprint cannot be patched

A TLS fingerprint is decided before your code runs, before a single header is sent, and
before any JavaScript exists to patch. That is the whole reason it works as a detection
signal, and the reason no stealth plugin fixes it.

This page is what JA3 and JA4 actually are, why automation stacks stand out, why JA3 is
less reliable than it used to be, and the only two things that genuinely change the
answer.

## What is being fingerprinted

When a client opens an HTTPS connection it sends a **ClientHello**: the TLS version it
proposes, the cipher suites it supports and in what order, the extensions it offers, the
elliptic curves, the point formats. That message is sent in the clear, and it is
entirely determined by the TLS library and how it was configured.

Different software produces different ClientHellos. A browser, a Python HTTP client and
a Go program are not trying to look different; they simply link against different TLS
implementations with different defaults.

**JA3** takes five of those fields, joins them with commas, and hashes the result with
MD5. One 32-character string that says which stack you are.

**JA4** is the successor, and the readable one. Instead of a single opaque hash it
produces a string with parts you can inspect, for example the protocol, the number of
ciphers and extensions, and the ALPN value, followed by truncated hashes of the sorted
lists.

The `d` in a JA4 string being a `d` rather than something else tells an analyst
something. An MD5 hash tells them nothing until they look it up.

## Why JA3 stopped being dependable

This is the part most guides are behind on.

Modern browsers **randomise the order of TLS extensions on every connection**. Chrome
introduced this deliberately, to stop servers hardcoding assumptions about client
behaviour. Since extension order is one of the five JA3 fields, the same browser
produces a different JA3 hash on every connection.

That has two consequences worth understanding:

- A JA3 blocklist built on exact hashes decays, because the legitimate browsers it was
  built from no longer produce a stable value.
- JA4 sorts the lists before hashing, which is precisely why it exists. It is stable
  across permutation and still distinguishes stacks.

So if you are reading advice about matching a specific JA3 hash, check its date. The
ground moved.

## Why JavaScript cannot touch any of this

The ClientHello is sent by the network stack when the socket opens. At that moment
there is no document, no `window`, no init script and nothing to override. By the time
any page-level stealth code exists, the fingerprint has already been sent and, if the
server cared, already judged.

This is a different kind of signal from everything else in fingerprinting, and it is
worth naming the difference. `navigator.webdriver` is a value the browser reports.
[A canvas hash](canvas-fingerprint-noise.md) is an output the browser produces. A TLS fingerprint is **a property of
the program that opened the connection**. You cannot report it differently, because you
are not the one reporting it.

The same is true of HTTP/2 settings frames and header ordering, which travel with it and
are checked alongside.

## The two things that actually change it

**Impersonate the handshake.** Use a client built to reproduce a specific browser's
ClientHello. This is what the various impersonating HTTP libraries do, and it works
well for requests that do not need a browser at all. What it does not give you is a
browser: no JavaScript execution, no rendering, no DOM.

**Make the request from a real browser.** If the connection is opened by an actual
browser build, the ClientHello is that browser's, because it is that browser. There is
nothing to impersonate.

That second one is the position this project is in, and it is worth being precise about
what it does and does not buy. The engine here is Firefox, patched in its own source,
and the TLS stack is untouched. So the handshake is a real Firefox handshake, not an
approximation of one, and it stays correct without anybody maintaining a table of
ClientHello templates.

The honest limits, because this is where people overclaim:

- **The version has to agree with the user agent.** A handshake from one Firefox version
  underneath a user agent announcing another is a contradiction, and it is the same
  class of mistake as any other mismatch. This is one reason the user agent here is
  derived from the engine's real version rather than written by hand.
- **A real browser is expensive.** If a page does not need JavaScript, an impersonating
  HTTP client is an order of magnitude cheaper and the right tool.
- **TLS being right proves nothing about the rest.** It is one layer. The machine you
  run on still answers for its GPU, its fonts and its audio device.

## Checking your own

Open one of the public JA3 or JA4 check pages from the browser or client you actually
deploy, then open the same page from a stock browser of the same version on the same
machine, and compare.

What you are looking for:

- The JA4 string matches the stock browser's, not merely "looks like a browser".
- The ALPN value is what a browser sends.
- If you are behind a proxy, run the check through it, because a proxy that terminates
  TLS replaces the fingerprint with its own and you are then measuring the proxy.

That last point catches people. A SOCKS5 proxy forwards the TCP stream and your
ClientHello arrives intact. Many HTTP proxies terminate and re-establish TLS, so the
server sees the proxy's handshake, and whether that is good or bad for you depends
entirely on what the proxy is.

## Short answers to the questions that lead here

**Can I change my JA3 fingerprint in Playwright?** Not from Playwright, and not from
JavaScript. The handshake belongs to the process that opens the socket.

**Does playwright-stealth fix TLS fingerprinting?** No. It operates inside the page,
which is several layers above where this happens.

**Is JA3 or JA4 better?** JA4 is more robust, because it sorts the lists it hashes and
therefore survives the extension-order randomisation that modern browsers do. JA3 is
still widely deployed, which is why both get checked.

**Why does my JA3 change between requests?** Almost certainly extension permutation,
which is normal browser behaviour now and not a sign that something is wrong.

**Do I need to worry about this at all?** Only if the target checks it. Many do not. If
your requests are refused before any JavaScript runs and before any page is returned,
this layer is a reasonable suspect.

**Does a residential proxy fix it?** No. A proxy changes where the connection comes
from, not what the ClientHello looks like, unless it terminates TLS itself, in which
case the fingerprint becomes the proxy's.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
where the network layer is step six, and
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md),
which covers the transport underneath.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The TLS stack is one of the parts we deliberately do
not touch, and this page is the reason why.*
