---
title: "ERR_HTTP2_PROTOCOL_ERROR in Playwright: causes and how to diagnose it"
description: "ERR_HTTP2_PROTOCOL_ERROR in Playwright is Chromium's catch-all for an HTTP/2 framing violation, often a proxy mangling the tunnel or a TLS/ALPN mismatch. How to tell which, without guessing."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 28
---


# ERR_HTTP2_PROTOCOL_ERROR in Playwright: causes and how to diagnose it

`ERR_HTTP2_PROTOCOL_ERROR` means the client detected a violation of the HTTP/2 framing
rules and killed the connection itself, usually with an `RST_STREAM` or `GOAWAY` frame
carrying `PROTOCOL_ERROR`. It is not a timeout, not a DNS failure, and not on its own
proof a site blocked you. It is the browser's HTTP/2 stack refusing to keep talking
because something on the wire broke the rules it enforces.

The code does not say which rule broke, or on which side. That is by design: Chromium
developers have said on the public chromium-dev list that this one code covers roughly
eight distinct causes, and the only reliable way to separate them is a network log read
for the actual frame that triggered it. This page narrows that to what a Playwright
user actually hits: a proxy in the path, and the automation-specific reports in
Playwright's own issue tracker.

## What the string is, and which browser actually says it

`ERR_HTTP2_PROTOCOL_ERROR`, code -337 in Chromium's `net_error_list.h`, is a Chromium
net-stack string, thrown by `page.goto()` when Playwright drives Chromium. It is the
exact string almost every search result about this topic is describing.

Firefox does not surface this literal string. `invisible_playwright` drives a Firefox
patched at the C++ level with the networking stack untouched, so the same class of
HTTP/2 framing violation shows up under Firefox's own names: commonly
`NS_ERROR_NET_RESET` for a connection torn down mid-stream, or, when the server
negotiates HTTP/2 over a TLS cipher HTTP/2 blacklists,
`NS_ERROR_NET_INADEQUATE_SECURITY`. Same protocol, same class of break, different name,
because the two engines carry separate HTTP/2 implementations. Searched this exact
string while driving Firefox? Look for those two names in your logs instead.

## The causes worth checking behind a proxy

- **The proxy does not tunnel HTTP/2 cleanly.** A `CONNECT` tunnel should be a
  transparent byte pipe. A proxy that buffers the stream, re-chunks it, or terminates
  and re-opens TLS itself can corrupt the binary framing, and the client sees frames
  that do not parse.
- **ALPN disagrees with what actually gets spoken.** HTTP/2 is negotiated in the TLS
  handshake's ALPN extension. A proxy that terminates TLS and re-negotiates outbound can
  advertise `h2` to your client while speaking something else to the origin, so the
  session starts as HTTP/2 and cannot stay one. A proxy doing that also replaces the
  handshake the destination sees with its own, so the
  [JA3 and JA4 fingerprint](ja3-ja4-tls-fingerprint.md) arriving there is the proxy's,
  which matters separately from whether the session survives.
- **A server-side HTTP/2 config problem, unrelated to your proxy.** HTTP/2 enforces
  header rules HTTP/1.1 never did: no invalid bytes in a header value, a fixed
  pseudo-header order, framing from RFC 9113 a lenient HTTP/1.1 stack never checked. A
  working HTTP/1.1 endpoint can trip this the moment a header carries a character
  HTTP/1.1 tolerated. Diagnosable, but it belongs to the target, not your setup.
- **The "error" is not an error.** More than one report traces this exact code to a
  server sending `RST_STREAM` deliberately, as an anti-automation response rather than a
  protocol accident. The browser reports both cases identically: a stream closed with
  `PROTOCOL_ERROR` before delivering anything. If that is what happened, the thing
  worth reading is not the framing rules but
  [what an HTTP/2 connection reveals about the client that opened it](http2-fingerprint-detection.md),
  since the frame order and settings are themselves a fingerprint.

A smaller set of reports describe the error only in headless mode, disappearing
headful, proxy or no proxy. That is in Playwright's own tracker with no confirmed root
cause; treat it as something to test for, not an explanation to assume. It is also the
one shape on this page where
[the separate question of what headless changes](is-playwright-headless-detectable.md)
is worth a look, because a mode-dependent failure with no protocol explanation is the
signature of something reading the client and not the frames.

## How to actually find out which one it is

1. **Drop the proxy and retry.** Gone on a direct connection, the proxy is implicated.
   Survives, the fault is server-side or in ALPN, and no proxy change fixes it.
2. **Hit the same target and proxy with curl.** `curl -v --http2 -x <proxy> <url>`
   isolates the browser entirely; if curl reproduces the failure, Playwright is not the
   variable.
3. **Force HTTP/1.1 as a diagnostic, not a fix.** `curl --http1.1` through the same
   proxy, or a launch flag disabling HTTP/2, shows whether the target works once the
   stricter framing is out of the picture. Success there points at HTTP/2-specific
   strictness, not the network path itself.
4. **Read the actual frame, not just the code.** Chromium: `chrome://net-export`,
   reproduce, load the capture in the netlog viewer, look for
   `HTTP2_SESSION_RECV_INVALID_HEADER` or the equivalent event. Firefox: set
   `MOZ_LOG=nsHttp:5,nsSocketTransport:5` before launch and read the log for the frame
   that preceded the failure.
5. **Swap the exit and repeat.** A different proxy exit clearing the error on the same
   target points at the previous exit's HTTP/2 handling, not the target.
6. **Note first request versus concurrency.** A proxy mishandling multiplexed streams on
   one tunnel can pass a single quiet request and fail only once several run in
   parallel.

## What invisible_playwright does and does not touch here

Its TLS and HTTP/2 stacks are the engine's own, left unmodified so the handshake and
the frames read as genuine Firefox rather than an approximation. The product is not the
source of this error on its own build, and it cannot fix it either: a proxy that
mangles an HTTP/2 tunnel mangles it the same way whether the browser behind it has a
carefully built fingerprint or a stock Playwright default. A cleaner identity changes
what a page reports about itself, not how a middlebox handles binary framing three
layers below the page.

## Conclusion

`ERR_HTTP2_PROTOCOL_ERROR` is a catch-all for "the HTTP/2 rules were broken," with no
hint in the string about who broke them. Reproduce without the proxy to place the fault
on one side of it, confirm with curl outside the browser entirely, and read the actual
frame log before assuming a cause. Most of the fixes that matter here live in the proxy
or the server configuration, not in the browser driving the request.

## Short answers to the questions that lead here

**What does ERR_HTTP2_PROTOCOL_ERROR mean?** The client's HTTP/2 stack detected a
framing or stream-level rule violation and closed the connection itself. It does not
say which rule, or which side, broke it.

**Is this always the proxy's fault?** No. It shows up from a proxy mangling an HTTP/2
tunnel, a TLS/ALPN mismatch, a server-side HTTP/2 config problem, and a server
deliberately resetting the stream as a block. Testing with the proxy removed tells you
which fastest.

**Will forcing HTTP/1.1 fix it?** It is a diagnostic, not a fix. Success on HTTP/1.1
means the break is specific to HTTP/2's stricter framing or header rules, which tells
you where to keep looking rather than closing the case.

**Does invisible_playwright's stealth patching cause or fix this?** Neither. Its TLS
and HTTP/2 stacks are the engine's own and are untouched, so this is a network or
server-configuration problem independent of the browser identity layer.

**Why does Firefox not show this exact error string?** `ERR_HTTP2_PROTOCOL_ERROR` is
Chromium's net-stack naming. Firefox reports the same family under its own names,
commonly `NS_ERROR_NET_RESET`, or `NS_ERROR_NET_INADEQUATE_SECURITY` for the
TLS-cipher variant.

**Could this be bot detection rather than a real protocol bug?** In documented cases,
yes: a server sends RST_STREAM deliberately, and the client reports it identically to
an accidental one. Succeeds from a different exit with no other change? Suspect a
deliberate reset over a genuine framing bug.

## Sources

- Chromium's [`net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  defining `ERR_HTTP2_PROTOCOL_ERROR` as code -337, "There is an HTTP/2 protocol
  error," alongside related codes in the same family
  (`ERR_HTTP2_SERVER_REFUSED_STREAM`, `ERR_HTTP2_STREAM_CLOSED`,
  `ERR_HTTP2_COMPRESSION_ERROR`).
- The [chromium-dev discussion on ERR_HTTP2_PROTOCOL_ERROR](https://groups.google.com/a/chromium.org/g/chromium-dev/c/VSzBnCgvQgc),
  where a Chromium developer diagnoses a concrete case, an invalid character in a
  response header name, and recommends a NetLog capture over guessing from the code.
- [The case of HTTP2 protocol error and chromium net-log](https://blog.nuvotex.de/http2-protoerr-net-log/),
  a netlog-based diagnosis of a malformed `strict-transport-security` header value that
  HTTP/1.1 tolerated and HTTP/2's stricter parsing rejected.
- Microsoft Playwright issues [#27600](https://github.com/microsoft/playwright/issues/27600)
  and [#31240](https://github.com/microsoft/playwright/issues/31240), user reports of
  `ERR_HTTP2_PROTOCOL_ERROR` under Playwright, one labeled by maintainers as an upstream
  Chromium issue after disabling HTTP/2 did not resolve it.
- A [changedetection.io discussion](https://github.com/dgtlmoon/changedetection.io/discussions/2051)
  tracing repeated `ERR_HTTP2_PROTOCOL_ERROR` reports to sites actively resetting the
  connection as an anti-automation response rather than a genuine protocol fault.
- Mozilla's [HTTP logging documentation](https://firefox-source-docs.mozilla.org/networking/http/logging.html),
  for the `MOZ_LOG=nsHttp:5,nsSocketTransport:5` capture used on Firefox.

**See also:** [HTTP/2 fingerprint: the layer above the TLS handshake](http2-fingerprint-detection.md)
for what this same protocol layer reveals about the client, [SOCKS5 vs HTTP proxy: what
each does in the browser](socks5-vs-http-proxy-browser.md) for which layer authenticates
and where, and [why a plain requests scraper is blocked before it sends a
header](web-scraping-tls-fingerprint-requests-blocked.md) for the TLS layer just beneath
this one.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The HTTP/2 stack is one
of the parts left deliberately untouched, so an error at this layer is a proxy or
server problem to solve, not a spoof to add.*
