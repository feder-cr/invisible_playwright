---
title: "HTTP/3 and QUIC fingerprint: what a site sees"
description: "How HTTP/3 over QUIC fingerprints distinctly from JA3/JA4, why scripted clients contradict their User-Agent, and how real Firefox engines match the protocol."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 22
---


# HTTP/3 and QUIC fingerprint: what a site sees

Most fingerprint discussion stops at the TLS handshake and the HTTP/2 settings frame.
There is a layer above both of them now, and it is the one a scripted client is least
likely to get right: [HTTP/3](https://datatracker.ietf.org/doc/html/rfc9114), which runs over [QUIC](https://datatracker.ietf.org/doc/html/rfc9000), carries its own fingerprint that is
distinct from JA3/JA4 and distinct from the HTTP/2 one. If the browser negotiates it,
the site can read it, and it has to agree with everything else the browser claims to be.

This page is what that QUIC fingerprint is made of, why a hand-built HTTP client
contradicts its own User-Agent at this layer, why an actual Firefox engine does not, and
the honest limit: matching the protocol fingerprint does nothing for your IP or your
pacing.

## Where QUIC sits in the fingerprint stack

A modern request is fingerprinted at several independent layers, and passing one says
nothing about the next.

- The **TLS handshake** produces a JA3/JA4 hash from the cipher list, extensions and
  their order. [It is decided before any page loads and no in-page test can see it](ja3-ja4-tls-fingerprint.md).
- **HTTP/2** adds its own set: the SETTINGS frame values, the WINDOW_UPDATE, the
  pseudo-header order and the priority tree. [These are a second, separate fingerprint](http2-fingerprint-detection.md)
  that a client can get wrong even with a perfect TLS hash.
- **HTTP/3 over QUIC** is a third. QUIC folds the cryptographic handshake into its own
  transport, so instead of "TCP, then TLS, then HTTP" you get one integrated exchange
  with parameters that no earlier layer contained.

Each layer is a chance to disagree with the User-Agent. A request that claims Firefox in
its header, presents a Firefox TLS hash, and then negotiates QUIC transport parameters no
Firefox build ever sends has told the truth twice and lied once, and once is enough.

## What a site actually sees in a QUIC handshake

When a browser opens an HTTP/3 connection, the very first QUIC packets carry a block of
**transport parameters**: the client's declared limits and preferences for the
connection. Among them are things like the maximum idle timeout, the initial flow-control
windows for streams and for the connection, the maximum number of concurrent streams, the
maximum UDP payload size, the active connection-ID limit, and several others.

Two things about that block are fingerprintable, and neither is about any single value:

- **The set that is present.** Different engines advertise different subsets of the
  optional parameters, and some send vendor-flavoured or experimental ones that others
  never do.
- **The order they appear in and the concrete values chosen.** The initial window sizes,
  the timeout, the stream limits are not random; a given browser build picks a
  characteristic set. The ordering of the parameters in the packet is itself part of the
  signature, exactly as extension order is for TLS.

On top of the transport parameters, the initial-packet construction and the QUIC version
negotiation add more. The result is a compact profile, observable in the first UDP
datagrams, that a server can compare against what a genuine build of the claimed browser
would send. It is the same idea as JA3, moved down onto QUIC, and it is young enough that
many automation stacks do not model it at all.

## Why a scripted HTTP client gets this wrong

A library that speaks HTTP/3 by hand chooses its own transport parameters, and it chooses
them like a library, not like a browser.

The defaults a general-purpose QUIC implementation ships with are tuned for that library.
Its idle timeout, its flow-control windows, its stream limits and the order it serializes
them in are whatever its authors picked, and they are stable across every program that
links it. So the moment such a client sets a Firefox User-Agent, it produces a decisive
contradiction: a header field promising one engine, sitting on top of a QUIC handshake
that belongs to a networking library. This is the HTTP/3 version of
[the User-Agent that claims one browser while the handshake belongs to another](tls-fingerprint-user-agent-mismatch.md),
and it cannot be papered over from JavaScript, because by the time any page script runs
the handshake is already on the wire.

Worse for the scripted approach: even matching the *values* is not enough, because the
parameter set and its order also have to match, and those are baked into how the client
serializes its handshake. Reproducing a real browser's QUIC profile by configuration means
reproducing an entire engine's behaviour by hand, and keeping it in step every time that
engine changes its defaults in a new release.

## Why a real Firefox engine matches its own User-Agent

invisible_playwright does not build a QUIC handshake to look like Firefox. It **is**
Firefox: a build patched at the C++ level for fingerprint consistency, driven by stock
Playwright. When a session negotiates HTTP/3, the transport parameters, their order and
the initial-packet construction come from the browser's own network stack, the same code
path a person's Firefox uses.

That inverts the scripted-client problem. The QUIC fingerprint is not engineered to match
the User-Agent; it matches because the thing sending it and the thing named in the header
are the same program. When the underlying build advances a version, its handshake advances
with it, and the User-Agent it reports advances too, so the two stay consistent without
anyone maintaining a table of parameter values. The layer a hand-rolled client is most
likely to contradict is the one this approach never has to think about.

There is a caveat that has nothing to do with the browser: **many proxies never let HTTP/3
happen at all.** QUIC runs over UDP, and a proxy that only forwards TCP, or that terminates
the connection and re-originates it as HTTP/1.1, changes what the site can even observe.
Sometimes that means no QUIC fingerprint is exposed because the connection silently falls
back to TCP; the choice of [SOCKS5 versus an HTTP proxy](socks5-vs-http-proxy-browser.md)
decides whether UDP survives the hop. That is a property of your network path, not of the
engine, and it is worth knowing which one you have before you reason about what a server
reads.

## Running it: launch and fetch over HTTP/3

The switch from plain Playwright is two lines, and nothing about HTTP/3 needs configuring.
The browser advertises and negotiates it the way a normal Firefox does; you just drive the
page.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # Ask the page which protocol the navigation actually used.
    proto = page.evaluate(
        "performance.getEntriesByType('navigation')[0].nextHopProtocol"
    )
    print("negotiated protocol:", proto)   # 'h3' once QUIC is in use
```

The `browser` object is a real Playwright `Browser`, so every method works exactly as
documented upstream. [`nextHopProtocol`](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceResourceTiming/nextHopProtocol) is a standard Web Performance value: `h3` means the
navigation went over HTTP/3, `h2` means it fell back to HTTP/2. If you are running through
a proxy, this one line is also how you confirm whether QUIC survived the hop or was
downgraded to TCP:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate(
        "performance.getEntriesByType('navigation')[0].nextHopProtocol"))
```

Pass a fixed `seed` and the rest of the identity is reproducible too, so a run that behaves
oddly at the network layer can be replayed exactly rather than guessed at.

## The honest caveat: what this does not fix

Matching the QUIC fingerprint to the User-Agent removes one specific contradiction, an
important one, and nothing more. It is worth being blunt about the boundary, because the
protocol layer is where overclaiming is easiest.

A genuine QUIC handshake does nothing about:

- **IP reputation.** A perfect HTTP/3 profile from a datacenter range is still from a
  datacenter range. The fingerprint and the address are separate questions, and
  [a clean fingerprint on a burned IP still loses](why-blocked-with-a-clean-fingerprint.md).
- **Per-account quotas and rate limits.** These are counted, not fingerprinted, and no
  transport parameter changes the count.
- **Behaviour and timing.** Request cadence, pointer motion, how fast a form is filled.
  A server watching those sees them regardless of how the packets were framed.
- **The proxy erasing the layer entirely.** If your path forces TCP, there is no QUIC
  fingerprint to match, for better or worse.

The accurate way to state it: invisible_playwright makes the transport, TLS and driver
layers read as a genuine Firefox, which is why it clears most fingerprint-based checks.
The IP, the pacing and the account budget are yours to supply, with a clean exit and human
timing. No layer of this makes a session undetectable, and any tool that tells you it does
is selling the one claim that is both false and legally reckless.

## Conclusion

QUIC added a fingerprint above the ones people already watch for, and it is the one a
scripted HTTP client is least equipped to fake, because faking it means reproducing an
entire browser's network behaviour by hand and maintaining it forever. Using an actual
Firefox engine sidesteps that: the handshake and the User-Agent match because they come
from the same program. That closes a real gap. It does not close the ones that were never
about the browser, and treating a matched transport fingerprint as a finished job is how a
clean session still gets blocked.

## Short answers to the questions that lead here

**Does QUIC have its own fingerprint, separate from TLS?** Yes. The QUIC transport
parameters, their order and the initial-packet construction form a signature distinct from
the JA3/JA4 TLS hash and from the HTTP/2 settings frame. A client can match one and
contradict another.

**Can a Python HTTP library fake a Firefox HTTP/3 handshake?** Only by reproducing the
whole engine's transport behaviour and its exact parameter set and order, and keeping it in
step across releases. In practice its library defaults contradict the Firefox User-Agent it
sets.

**How does invisible_playwright get the QUIC fingerprint right?** It does not construct one.
It runs a real Firefox build, so the HTTP/3 stack is the browser's own and matches the
User-Agent by construction.

**Does my proxy affect the QUIC fingerprint?** It can remove it. QUIC needs UDP; a proxy
that forwards only TCP or re-originates as HTTP/1.1 downgrades the connection, so no HTTP/3
fingerprint is exposed at all.

**How do I check which protocol a page actually used?** Read
`performance.getEntriesByType('navigation')[0].nextHopProtocol` from the page. `h3` means
HTTP/3 over QUIC, `h2` means it fell back.

**Does matching the QUIC fingerprint make me undetectable?** No. It fixes one
transport-layer contradiction. IP reputation, rate limits, quotas and behaviour are
separate and untouched by it.

## Sources

- The QUIC transport-parameter model and initial-packet handshake as defined by
  [RFC 9114 (HTTP/3)](https://datatracker.ietf.org/doc/html/rfc9114) and
  [RFC 9000 (QUIC)](https://datatracker.ietf.org/doc/html/rfc9000), read from the standards
  rather than from a single implementation, retrieved 2026-08-28.
- This project's network-layer parity checks, which compare a session's negotiated
  protocol and handshake against a stock build of the same Firefox version.
- The Web Performance
  [`nextHopProtocol`](https://developer.mozilla.org/en-US/docs/Web/API/PerformanceResourceTiming/nextHopProtocol)
  value, a standard browser API used above to confirm the negotiated protocol from inside
  the page, retrieved 2026-08-28.

**See also:** [the HTTP/2 fingerprint one layer down](http2-fingerprint-detection.md),
[the JA3/JA4 TLS fingerprint below that](ja3-ja4-tls-fingerprint.md), and
[why a clean fingerprint can still be blocked](why-blocked-with-a-clean-fingerprint.md)
when the IP or the behaviour is the real problem.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The QUIC layer is real and
young; the honest caveat below the demo is the part that keeps this useful.*
