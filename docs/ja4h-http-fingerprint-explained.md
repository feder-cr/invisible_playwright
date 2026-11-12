---
title: "JA4H: The HTTP-Header-Order Fingerprint, Explained"
description: "JA4H fingerprints an HTTP client from the request itself: method, header order, header count, cookies, and Accept-Language, folded into one string. What it measures, how it is built, and how it differs from JA4's TLS handshake and the HTTP/2 frame layer."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 39
---


# JA4H: The HTTP-Header-Order Fingerprint, Explained

JA4H fingerprints an HTTP client from the request itself, the method, the header names
and their order, how many headers there are, whether cookies and a referer are present,
and the first `Accept-Language` value, folded into one readable string. It is [JA4's own
sibling for the HTTP layer](https://blog.foxio.io/ja4%2B-network-fingerprinting), part of
the JA4+ suite published by FoxIO, and it answers a question neither the TLS handshake nor
the raw HTTP/2 frames can: what does this specific request, as composed, actually look
like, cookie by cookie and header by header.

This page is what goes into it, how the string is actually built field by field, how it
differs from the two layers already covered in this set, and what it means for a browser
that is real all the way down.

## Where it sits relative to what this set already covers

[JA3 and JA4](ja3-ja4-tls-fingerprint.md) fingerprint the TLS handshake: the ClientHello,
sent before a single byte of HTTP exists. [The HTTP/2 fingerprint](http2-fingerprint-detection.md)
covers the binary framing layer one step above that: the SETTINGS frame, the window
update, the priority tree, and the order of the four HTTP/2 pseudo-headers, `:method`,
`:authority`, `:scheme`, `:path`.

JA4H sits at a third, different layer, worth being precise about rather than treating as
"more of the same." The HTTP/2 fingerprint reads the protocol machinery, present only on
HTTP/2 connections and invisible to a plain HTTP/1.1 client. JA4H reads the **request
itself**: the ordinary header field names, in the order the client sent them, the method,
the HTTP version, and the cookies, none of which depend on which protocol version carried
them. FoxIO's own format encodes the HTTP version directly into the string precisely so
it works identically on HTTP/1.0, HTTP/1.1, or HTTP/2. Two clients can agree perfectly on
SETTINGS and pseudo-header order and still disagree completely on JA4H, because JA4H asks
about the headers a script or browser actually chose to send, not the wire format
carrying them.

## What goes into the string, field by field

A JA4H fingerprint has the shape `a_b_c_d`, the same locality-preserving structure every
JA4+ method uses. Based on FoxIO's own reference implementation, each segment is built
from a specific, deterministic rule:

**The `a` segment** is six fields packed together with no separator:

- **Method**, the first two lowercase letters of the HTTP method: `ge` for GET, `po` for
  POST, `he` for HEAD.
- **Version**, two digits: `10` for HTTP/1.0, `11` for HTTP/1.1, `20` for HTTP/2.
- **Cookie flag**, `c` if the request carries a `Cookie` header, `n` if it does not.
- **Referer flag**, `r` if the request carries a `Referer` header, `n` if it does not.
- **Header count**, a two-digit count of headers present, **excluding** `Cookie` and
  `Referer` themselves, capped at `99`.
- **Language**, the first value of `Accept-Language`, lowercased, with hyphens and
  semicolons stripped, truncated or padded to exactly four characters, or `0000` if the
  header is absent entirely.

**The `b` segment** is a truncated SHA-256 hash, the first twelve hex characters, of the
ordinary header names, joined in the **order they were sent**, excluding `Cookie` and
`Referer`. This is the header-order signal proper, and unlike JA4's TLS extension list, it
is not sorted first: order is exactly what this segment is measuring, so sorting it away
would defeat the point.

**The `c` segment** is the same truncated hash construction applied to the request's
cookie **names**, sorted alphabetically first. It is `0` repeated twelve times if there
are no cookies at all.

**The `d` segment** is the identical hash construction applied to the full cookie
`name=value` pairs, also sorted by name. Twelve zeros again when there are no cookies.

Put together, that reads as: what kind of request is this, in what shape (`a`), sent with
which headers in which order (`b`), carrying which cookies (`c`), set to which specific
values (`d`).

## Real examples, from malware traffic FoxIO has published

FoxIO's own reference table includes JA4H strings computed from real captured samples,
not synthetic ones, which is a useful way to see what the string distinguishes in
practice:

- `JA4H=ge11cn020000_9ed1ff1f7b03_cd8dafe26982`, the IcedID malware dropper: a `GET`,
  HTTP/1.1, with cookies, no referer, two headers, no `Accept-Language`.
- `JA4H=po10nn060000_cdb958d032b0`, Darkgate: a `POST`, HTTP/1.0, no cookies, no
  referer, six headers.
- `JA4H=ge11cn060000_4e59edc1297a_4da5efaf0cbd`, Cobalt Strike: a `GET`, HTTP/1.1, with
  cookies, no referer, six headers.

FoxIO's own blog post is direct about why these read as suspicious independent of any
signature or payload inspection: "The lack of an Accept-Language is a clear indication
that the application is not human interactive, ergo a bot." A four-zero language field is
not a coincidence in that sentence; it is the field doing exactly the job it was designed
for. None of the three examples above needed TLS at all: IcedID's dropper stage, per
FoxIO's own writeup, "doesn't use TLS" and communicates in plain HTTP, which is precisely
the case where JA3 and JA4 have nothing to say and JA4H is the only signal left on the
wire.

## Why the `c` and `d` split matters

The separation between cookie **names** (`c`) and cookie **values** (`d`) is deliberate.
FoxIO's own description draws the distinction cleanly: `JA4H_c` "fingerprints cookies and
remains consistent per website or application," while `JA4H_d` is "a fingerprint of the
user and will be different per user." The names a site sets are stable across visitors;
the values inside those cookies, session identifiers chief among them, are what makes
each visitor's `JA4H_d` distinct, and what makes a sudden change in `JA4H_d` mid-session,
with `JA4H_c` unchanged, worth a second look: the same cookie names now carrying a
different visitor's values inside a session that was supposed to stay one identity. That
is exactly the mechanism FoxIO's own material describes for spotting session hijacking.

## Why nothing inside the page can freely rewrite this

The base set of headers a real browser sends on a normal navigation, `Host`,
`User-Agent`, `Accept`, `Accept-Language`, `Accept-Encoding`, `Connection`, and the
`Sec-Fetch-*` family, is composed by the networking code inside the engine, in a fixed
relative order reflecting how that engine was built. Page-level JavaScript can add
**additional** headers, through `fetch()` options or Playwright's own
`extra_http_headers`, but that appends to the request; it does not reorder the engine's
own base set or change how many of them there are beforehand. The `b` segment of JA4H is
reading exactly that base composition, the same way [the HTTP/2 fingerprint](http2-fingerprint-detection.md#why-javascript-cannot-reach-this-layer)
reads the frame layer beneath it: a property of which networking stack assembled the
request, not a value a script decorated afterward.

This is why an HTTP client library gives itself away here just as cleanly as at the TLS
and HTTP/2 layers. A Python `requests` or `httpx` call can carry a user agent string
copied byte for byte from a real Firefox, and its header set, names, count, and order
will still belong to that library. `Accept-Encoding: gzip, deflate` with no
`Accept-Language` at all, in an order no browser produces, is a JA4H that says "scripting
library" regardless of what the `User-Agent` string claims.

A patched-but-real browser does not have this problem, for the same reason the TLS and
HTTP/2 layers do not: the engine composing the request is a genuine browser's own
networking code, so the header set is the browser's because it is the browser. There is
no template to keep synchronized with a moving target; the request is real because the
thing sending it is real.

## Checking your own

There is no single, universally trusted public endpoint that computes and returns JA4H
the way public JA3/JA4 checkers exist for the TLS layer; FoxIO's own tooling is built
around packet captures read with `tshark` rather than a hosted API. The practical check
is the same discipline this whole set recommends everywhere below the page: capture the
raw request your engine actually sent, from a small local server that logs headers
verbatim in the order received, and compare it field by field against the identical
request from a stock browser of the same version on the same machine, the header names
present and their count excluding `Cookie` and `Referer`, the header order itself since
JA4H's `b` segment does not sort it away, whether `Accept-Language` is present at all,
and the cookie names and values if any are carried.

If every one of those matches a stock browser byte for byte, the JA4H layer is consistent
with the identity the request claims. A library's header set will not match no matter how
carefully the other layers are dressed up, which is the point of this fingerprint sitting
beside JA4 rather than instead of it.

## Conclusion

JA4H is the HTTP-layer answer to a question JA3 and JA4 cannot address at all: what does
the actual request look like, not the handshake that opened the connection underneath it.
It reads the method, the header order and count, the language header, and the cookies,
all composed by the client's own networking code before any page-level script gets a say
in the base set. A real, unmodified browser engine produces a real browser's JA4H for
free; an HTTP library announces its own, and a missing `Accept-Language` is, in FoxIO's
own words, one of the plainest tells in the whole JA4+ family. Combined with JA4 and the
HTTP/2 frame layer beneath it, a site checking all three is asking the same underlying
question three times, at three different layers, and a client that gets one wrong while
dressing up the others is answering itself.

## Short answers to the questions that lead here

**Is JA4H the same as JA4?** No. JA4 fingerprints the TLS ClientHello, sent before any
HTTP exists. JA4H fingerprints the HTTP request itself: method, header order and count,
cookies, and Accept-Language. They are separate, complementary members of the same JA4+
family.

**Is JA4H the same as HTTP/2 fingerprinting?** No, though they sit close together. The
HTTP/2 fingerprint reads binary framing, present only on HTTP/2. JA4H reads the ordinary
header names, order, and cookies, and works identically on HTTP/1.1 and HTTP/2 because
the version is encoded directly into the string.

**Can I change my JA4H by setting extra_http_headers in Playwright?** You can add headers
that way, which extends the request, but you cannot reorder or remove the engine's own
base header set through it; that composition belongs to the networking code.

**What is the single biggest tell in JA4H?** A missing `Accept-Language` header. FoxIO's
own material names it directly as evidence the client is not human-interactive, because
every real browser sends one by default.

**Why do JA4H_c and JA4H_d matter separately?** `JA4H_c` hashes cookie names and stays
stable per site. `JA4H_d` hashes cookie values and is specific to the individual visitor,
which is why a change in `JA4H_d` mid-session, with `JA4H_c` unchanged, is a documented
signal for session hijacking.

**Does a real browser need to impersonate a JA4H string?** No, in the same sense a real
browser never needs to impersonate its own TLS handshake. If the engine composing the
request is genuinely the browser the user agent claims, the header set is that browser's
by construction, not by a table someone has to keep current.

## Sources

- FoxIO's own [JA4+ GitHub repository](https://github.com/FoxIO-LLC/ja4), specifically
  [`technical_details/JA4H.md`](https://github.com/FoxIO-LLC/ja4/blob/main/technical_details/JA4H.md)
  and the reference implementation [`python/ja4h.py`](https://github.com/FoxIO-LLC/ja4/blob/main/python/ja4h.py)
  and [`python/common.py`](https://github.com/FoxIO-LLC/ja4/blob/main/python/common.py),
  for the exact field construction, the `a_b_c_d` format, and the twelve-character
  truncated SHA-256 hash used for each segment, retrieved 2026-08-30.
- FoxIO's own [JA4+ Network Fingerprinting blog post](https://blog.foxio.io/ja4%2B-network-fingerprinting),
  for the description of `JA4H_ab`, `JA4H_c`, and `JA4H_d`, the missing-`Accept-Language`
  bot signal, the session-hijacking use of `JA4H_d`, and the IcedID example running over
  plain HTTP with no TLS, retrieved 2026-08-30.
- FoxIO's [JA4+ README](https://github.com/FoxIO-LLC/ja4), for the published example
  fingerprints from real IcedID, Darkgate, and Cobalt Strike samples quoted above, and the
  table of vendors, including Cloudflare, supporting the JA4+ family, retrieved 2026-08-30.
- [tls.peet.ws](https://tls.peet.ws/api/all), checked directly, confirming it reports
  `ja3`/`ja4` and HTTP/1 request data but does not compute `ja4h`, retrieved 2026-08-30.

**See also:** [JA3 and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md)
for the handshake layer directly beneath this one, [HTTP/2 fingerprint: the layer above
the TLS handshake](http2-fingerprint-detection.md) for the binary framing fingerprint
JA4H sits beside rather than on top of, and [why a plain requests scraper is blocked
before it sends a header](web-scraping-tls-fingerprint-requests-blocked.md) for what an
HTTP library gives away at every one of these layers at once.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The header-composition code
is one of the parts left deliberately untouched, so the JA4H a request carries is the
engine's own.*
