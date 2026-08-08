---
title: "Why a Python requests scraper is blocked: TLS fingerprint"
description: "A Python requests scraper has its own TLS fingerprint, so a site can block the connection at the handshake before any header, cookie, or user agent exists."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 9
---


# Why a Python requests scraper is blocked: TLS fingerprint

A Python `requests` scraper often gets blocked at the TLS handshake, before it sends
a single header, cookie, or user agent. The TLS library `requests` links against
produces a ClientHello that no browser produces, and a site can read that mismatch and
reject the connection while the headers and user agent you set are still waiting to be
sent.

That is why the usual fixes miss. A scraper built on `requests` or a similar plain HTTP
client sets a convincing user agent, adds the right headers, maybe even rotates a clean
residential proxy - and still gets blocked, sometimes before the response comes back
with any content at all. The header and the user agent were never the problem.
Something earlier decided the outcome first.

## What happens before any of that exists

An HTTPS connection opens with a TLS handshake, and the first message in it - the
[ClientHello](https://datatracker.ietf.org/doc/html/rfc8446) - is sent before a single byte of the HTTP request exists. No headers, no
cookies, no user agent string: none of that is part of the connection yet. The
ClientHello is generated entirely by the TLS library the client links against, and it
lists the cipher suites, extensions and settings that library supports, in the order
that library's code produces them.

`requests` uses Python's `ssl` module, backed by OpenSSL, configured the way Python's
own defaults configure it. A real browser links a completely different TLS stack -
Chrome and its derivatives use BoringSSL, Firefox uses NSS - configured the way that
specific browser's own release configures it. These produce different ClientHellos,
structurally, and that difference is exactly what JA3 and its successor JA4 turn into
a short fingerprint string.

A site checking that fingerprint against a database of known values doesn't need your
user agent to already know you're not the browser you're about to claim to be in the
HTTP request that hasn't been sent yet.

## Why the obvious fixes don't reach this

Setting headers, rotating user agents, adding cookies, even routing through a clean
proxy - every one of these operates at or above the HTTP layer, which starts after
the TLS handshake has already finished. A JA3/JA4 check that runs at the connection
level has already produced its verdict by the time any of that code executes.
[The same "capability, not a value" distinction covered elsewhere on this site](chromium-is-not-chrome.md)
applies here: a TLS fingerprint isn't a field a script can override, it's a
consequence of which library actually opened the socket.

## The two things that genuinely change it

**Impersonate the handshake with a client built for it.** Libraries like `curl_cffi`
wrap `curl-impersonate`, which reproduces a specific browser's TLS and
[HTTP/2 behavior](http2-fingerprint-detection.md) at the connection level rather than
Python's own defaults. This closes the
JA3/JA4 mismatch without needing an actual browser, at a fraction of the resource
cost, for requests that don't need to execute JavaScript or render a page.

**Drive an actual browser.** If a session needs to execute JavaScript, or the target
checks canvas, WebGL, fonts or any other JavaScript-visible surface alongside the
network layer, a real browser's own TLS stack produces a real ClientHello because it
is a real browser making the connection. There's nothing to impersonate.

Neither of these is universally better. A plain page fetch with no JavaScript
requirement is usually cheaper and faster through an impersonating HTTP client. A
page that needs to render, execute scripts, or pass a JavaScript-based check needs
the browser regardless of how good the TLS impersonation is - matching the network
layer and matching everything above it are separate problems, and a TLS-only fix
doesn't touch the second one.

## What to check in your own setup

Fetch the same TLS fingerprint check from your scraper and from a real browser, on
the same machine, and compare the JA3/JA4 values directly rather than assuming either
answer:

- If they match, this specific layer isn't your blocker.
- If they don't, and the target doesn't need JavaScript, an impersonating HTTP client
  is the proportionate fix.
- If they don't, and the target does need JavaScript, no amount of TLS impersonation
  substitutes for the rest of what a browser provides.

## Short answers to the questions that lead here

**Why does my Python scraper get blocked even with the right headers and a good
proxy?** The block may be happening at the TLS handshake, before any header, cookie,
or user agent your code sets even exists as part of the connection.

**Does setting a browser-like user agent fix a TLS fingerprint mismatch?** No. The
user agent is an HTTP header, sent after the handshake the fingerprint is taken from
has already completed, so a
[TLS fingerprint and user agent that disagree](tls-fingerprint-user-agent-mismatch.md)
is itself a tell.

**Can I fix this without running a full browser?** Sometimes. If the target does not
execute JavaScript, an HTTP client that impersonates a browser's TLS and HTTP/2
behavior closes the network-layer mismatch on its own. See
[HTTP client versus real browser](http-client-vs-real-browser.md) for where that
tradeoff breaks down.

**What's the difference between JA3 and JA4?** JA4 sorts the fields it hashes before
hashing them, so it survives the extension-order randomization modern browsers use.
JA3 doesn't, and has become less reliable as a result, though it's still widely
checked.

**See also:** [JA3 and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md),
the full technical breakdown this page summarizes for a non-Playwright audience, and
[Chromium is not Chrome, and detectors know the difference](chromium-is-not-chrome.md),
for the general shape of a capability-based check versus a value-based one.

## Sources

- RFC 8446, the TLS 1.3 specification, which defines the ClientHello and its ordering
  of cipher suites and extensions - the bytes a JA3/JA4 fingerprint is computed from,
  and the reason the fingerprint exists before any HTTP-layer data does.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, where the TLS stack is untouched and real because
the browser making the connection is real.*
