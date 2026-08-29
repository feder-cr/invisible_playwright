---
title: "JA3 and JA4: why a TLS fingerprint cannot be patched"
description: "JA3 and JA4 are set by the TLS library before your code runs, so no stealth plugin can patch them. What they measure, why JA3 decayed, and the two real fixes."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 6
---


# JA3 and JA4: why a TLS fingerprint cannot be patched

A TLS fingerprint is decided before your code runs, before a single header is sent, and
before any JavaScript exists to patch. That is the whole reason it works as a detection
signal, and the reason no stealth plugin fixes it.

This page is what JA3 and JA4 actually are, why automation stacks stand out, why JA3 is
less reliable than it used to be, and the only two things that genuinely change the
answer.

## What is being fingerprinted

When a client opens an HTTPS connection it sends a **[ClientHello](https://datatracker.ietf.org/doc/html/rfc8446)**: the TLS version it
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
ciphers and extensions, and the **[ALPN](https://datatracker.ietf.org/doc/html/rfc7301)**
value, followed by truncated hashes of the sorted lists.

The `d` in a JA4 string being a `d` rather than something else tells an analyst
something. An MD5 hash tells them nothing until they look it up.

## Why JA3 stopped being dependable

JA3 stopped being dependable because modern browsers now **randomise the order of TLS
extensions on every connection**, and extension order is one of the five fields JA3
hashes. The same browser therefore produces a different JA3 hash on every connection.
This is the part most guides are still behind on.

Chrome introduced the randomisation deliberately, to stop servers hardcoding assumptions
about client behaviour.

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

The same is true of **[HTTP/2 settings frames](https://datatracker.ietf.org/doc/html/rfc9113)**
and header ordering, which travel with it and are checked alongside.

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

## A closed example: one extra cipher suite was the entire gap

"The engine is real Firefox, so the handshake is right" is a claim worth checking
rather than trusting, and once it was checked here, it wasn't quite true yet.

A same-IP, same-Firefox-version comparison against a stock build turned up a real
ClientHello difference: our build offered a 17th cipher suite that stock Firefox of
the same version does not. JA3 and JA4 diverged accordingly - one specific pair of
hashes on our build, a different pair on stock, both measured through a public TLS
fingerprint checking page.

The cause had nothing to do with the TLS stack itself. A single boolean preference
controlling that one cipher suite is declared with a channel-gated default in the
pref list our fork carries forward from upstream - on release builds it resolves to
on. Upstream had since moved that same preference to a flat, ungated default of off
for release builds; our fork's copy of the pref declaration had not been carried
forward past the point where that changed. The handshake code was never touched. The
default one preference resolves to had quietly drifted out of sync with the
upstream release it was supposed to match.

The consequence was immediate and pre-JavaScript: a production site running a
commercial protection stack that fingerprints TLS handshakes closely treated the
17-cipher ClientHello as an instant tell, on a connection that had not sent a single
byte of HTTP yet. No amount of page-level stealth work touches this - by the time any
script exists to patch anything, the handshake carrying the mismatch has already
gone out and, if the server cared, already been judged.

The fix was one line: force that preference to its upstream-current default in the
baseline. Re-measuring afterward showed the ClientHello, JA3, JA4 and the broader
handshake fingerprint byte-identical to stock Firefox of the same version.

The generalizable point is the maintenance side of "we don't touch the TLS stack":
not touching the code is not the same guarantee as staying in sync with every
default the code reads, and a forked browser needs an explicit, repeated parity
check against the currently shipped upstream release - not a one-time claim - to
keep that promise true release over release.

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

## Sources

- Salesforce's own GitHub repository for JA3, [retrieved 2026-08-28](https://github.com/salesforce/ja3),
  for the exact fields it joins and hashes with MD5.
- FoxIO's own GitHub repository for JA4, [retrieved 2026-08-28](https://github.com/FoxIO-LLC/ja4),
  for the sorted-list construction that survives extension-order permutation.
- The Chromium project's own shipping announcement, [Intent to Ship: TLS ClientHello
  extension permutation, retrieved 2026-08-28](https://groups.google.com/a/chromium.org/g/blink-dev/c/bYZK81WxYBo),
  for why Chrome randomises extension order.
- This project's own measurement of a stray cipher-suite default against stock Firefox
  of the same version, described above.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
where the network layer is step six,
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md),
which covers the transport underneath, and
[why a plain Python requests scraper gets blocked before it sends a header](web-scraping-tls-fingerprint-requests-blocked.md),
the non-browser version of this same problem.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The TLS stack is one of the parts we deliberately do
not touch, and this page is the reason why.*
