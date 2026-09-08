---
title: "net::ERR_SSL_PROTOCOL_ERROR in Playwright"
description: "net::ERR_SSL_PROTOCOL_ERROR means the TLS handshake itself broke, before any certificate was evaluated. Why ignoreHTTPSErrors does not fix it, and the HSTS and MITM-proxy causes that actually do."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 14
---


# net::ERR_SSL_PROTOCOL_ERROR in Playwright

`net::ERR_SSL_PROTOCOL_ERROR` means the TLS handshake itself failed at the protocol
level, before the browser ever got to the step of evaluating whether a certificate is
trustworthy. Chromium's own network error list defines it in five words, code -107, "An
SSL protocol error occurred." `page.goto()` throws it when the handshake cannot complete
at all: a cipher suite neither side will agree on, a TLS version mismatch, or bytes on
the wire that do not parse as a valid handshake message in the first place.

That distinction, handshake-level versus certificate-level, is the one thing worth
getting right before doing anything else, because the two failures look similar and the
usual fix for one does nothing for the other.

## Not the same failure as a certificate trust problem

[`ERR_CERT_AUTHORITY_INVALID`](err-cert-authority-invalid-playwright.md) means the
handshake completed, a certificate arrived, and the browser decided not to trust who
signed it. `ERR_SSL_PROTOCOL_ERROR` means the handshake never got that far: there is no
certificate to evaluate trust on yet, because the negotiation that would deliver one
broke first.

This matters concretely because of what does and does not fix each one.
`ignoreHTTPSErrors`, Playwright's documented lever for accepting an untrusted
certificate, [operates entirely on certificate-chain and hostname validation](err-cert-authority-invalid-playwright.md#what-ignorehttpserrors-actually-disables-and-the-tradeoff).
It has nothing to disable when the handshake itself is what is broken. [A real
report](https://github.com/microsoft/playwright/issues/23771) shows exactly this: `net::ERR_SSL_PROTOCOL_ERROR`
against a local HTTPS server, with `ignoreHTTPSErrors: true` already set and changing
nothing. That is not a bug in the option; it is the option correctly having no bearing
on a failure that happens a step earlier than anything it controls.

## The realistic causes

**A cipher suite or TLS version neither side supports.** The client offers a set of
ciphers and protocol versions in its ClientHello; if the server's own configuration has
no overlap with what was offered, at whatever versions each side is willing to speak,
the handshake fails outright instead of falling back gracefully. This is common on
older or misconfigured servers, and on modern servers being reached by an outdated or
deliberately restricted client.

**A middlebox performing TLS interception badly.** Corporate SSL-inspection appliances,
some antivirus products, and some proxies terminate the original TLS connection and open
a new one to the destination, presenting their own certificate to the client. Done
correctly the result is a certificate trust question, `ERR_CERT_AUTHORITY_INVALID`'s
territory. Done badly, commonly a middlebox that does not support a TLS version or
extension the real destination uses, the re-negotiated handshake can break outright
instead. Cloudflare's own troubleshooting documentation for this exact error names
"ISP/network interference (DPI, SSL interception proxies, content filters, CGNAT)"
directly among its causes, alongside "TLS 1.3 support issues with middleboxes."

**A stale HSTS entry pinning the browser to HTTPS after the server's TLS configuration
changed underneath it.** HTTP Strict Transport Security tells a browser to remember that a
domain must never be reached over plain HTTP again. That memory has no opinion on whether
the HTTPS side is currently healthy; it only removes the fallback option. A certificate
rotation or server migration that leaves HTTPS broken for that host reports the protocol
failure directly, with no fallback the way a browser without that memory could still
reach.

**A port or scheme mismatch.** Requesting `https://` against a port actually serving
plain HTTP, or the reverse, produces bytes that do not parse as the protocol either side
expected, which surfaces as a protocol-level failure, not a clean rejection.

**HTTP/3 or QUIC negotiation failing where HTTP/2 or HTTP/1.1 would have worked.**
Cloudflare's own documentation names HTTP/3 incompatibility as a distinct cause
alongside the cipher and TLS-version cases above, worth testing for independently by
forcing an older protocol.

## Diagnostic checklist

1. **Reproduce with `openssl s_client -connect host:443`.** This performs the raw
   handshake outside any browser entirely; a failure here confirms the problem is the
   server or the network path, not Playwright or the engine driving it.
2. **Test with any middlebox removed from the path.** Corporate network, VPN, or
   antivirus SSL inspection removed and the handshake completes: the interception layer
   is implicated. Persists with all of those removed: the server's own TLS configuration
   is the more likely cause.
3. **Force specific TLS versions with curl as a diagnostic.** `curl -v --tlsv1.2
   --tlsv1.3` against the same host isolates a version-specific incompatibility.
4. **Check for a stale HSTS entry if the domain used to work over HTTPS and no longer
   does.** Chromium exposes `chrome://net-internals/#hsts` to inspect and delete a
   domain's stored HSTS state directly.
5. **Do not reach for `ignoreHTTPSErrors` first.** It has no effect on a handshake that
   never produced a certificate to evaluate; testing it and seeing no change, the way
   the real report above did, is itself useful confirmation the failure is
   protocol-level, not trust-level.

## What Firefox reports instead

`invisible_playwright` drives a patched Firefox, and Firefox does not collapse
handshake failures into one Chromium-style code. Its TLS stack is NSS, and NSS reports
handshake problems through its own family of named errors, `SSL_ERROR_*` for
protocol-level failures such as an unsupported version or no overlapping cipher, and
`SEC_ERROR_*` for the certificate-trust side that `ERR_CERT_AUTHORITY_INVALID` covers on
Chromium. A middlebox terminating and mis-re-negotiating TLS in front of Firefox commonly
surfaces as `PR_CONNECT_RESET_ERROR` at the NSPR layer, the connection torn down mid
handshake, rather than a named SSL error at all. A related, narrower Firefox-specific
case is already documented on [the ERR_HTTP2_PROTOCOL_ERROR page](err-http2-protocol-error-playwright.md#what-the-string-is-and-which-browser-actually-says-it):
`NS_ERROR_NET_INADEQUATE_SECURITY`, thrown specifically when a server negotiates HTTP/2
over a cipher suite HTTP/2 itself blacklists, which is a protocol-level TLS failure with
its own distinct name rather than a generic one. If you are chasing this failure against
Firefox rather than Chromium, search logs for the `SSL_ERROR_` and `SEC_ERROR_` families
and for `PR_CONNECT_RESET_ERROR`, not for the Chromium string itself.

## The honest boundary

A broken TLS handshake is negotiated between the client's TLS library and the server,
below any layer a browser's JavaScript-visible identity touches. `invisible_playwright`
leaves the TLS stack untouched specifically so the handshake reads as a genuine Firefox
handshake rather than an approximation, which is the entire subject of [why a TLS
fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md). It does not retry a broken
negotiation, work around a middlebox's bad re-encryption, or clear a stale HSTS entry on
your behalf. A stock Playwright browser, a stock Firefox, and this project's build all
fail an incompatible handshake identically, because nothing about a cleaner fingerprint
changes what a TLS library can negotiate with a server it fundamentally cannot agree
with.

## Short answers to the questions that lead here

**What does net::ERR_SSL_PROTOCOL_ERROR mean?** The TLS handshake itself failed, a
cipher or version mismatch or malformed handshake data, before any certificate was ever
evaluated for trust.

**Does ignoreHTTPSErrors fix this?** No, and a real report confirms it: the option
disables certificate-chain validation, which never runs if the handshake producing a
certificate never completed in the first place.

**How is this different from ERR_CERT_AUTHORITY_INVALID?** That error means a
certificate arrived and was not trusted. This error means the handshake broke before any
certificate arrived at all. They have different causes and different fixes.

**Can a stale HSTS entry really cause this?** Yes. HSTS removes the option to fall back
to plain HTTP for a domain; if that domain's HTTPS configuration later breaks, the
browser has no fallback left and reports the protocol failure directly.

**What does Firefox call this instead of ERR_SSL_PROTOCOL_ERROR?** Firefox's NSS stack
reports handshake failures through its own `SSL_ERROR_*` family rather than one generic
code, and a middlebox tearing down the connection mid-handshake commonly surfaces as
`PR_CONNECT_RESET_ERROR` instead of a named SSL error at all.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_SSL_PROTOCOL_ERROR` (-107), retrieved 2026-08-30.
- Cloudflare's own [Troubleshoot ERR_SSL_PROTOCOL_ERROR](https://developers.cloudflare.com/ssl/troubleshooting/err-ssl-protocol-error/)
  documentation, for the named causes including SSL-interception proxies, TLS 1.3
  middlebox incompatibility, and HTTP/3 negotiation failures, retrieved 2026-08-30.
- [microsoft/playwright#23771](https://github.com/microsoft/playwright/issues/23771),
  a real report of `net::ERR_SSL_PROTOCOL_ERROR` against a local HTTPS server with
  `ignoreHTTPSErrors: true` already set and having no effect.

**See also:** [ERR_CERT_AUTHORITY_INVALID in Playwright](err-cert-authority-invalid-playwright.md)
for the certificate-trust failure this page is most often confused with, [JA3 and JA4:
why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) for the handshake
layer this error occurs inside of, and [ERR_HTTP2_PROTOCOL_ERROR in Playwright](err-http2-protocol-error-playwright.md)
for the related, narrower Firefox-specific TLS-cipher failure this page's Firefox section
draws on.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The TLS stack is untouched on purpose; a broken
handshake is the network or the server, not something a fingerprint patch could reach
either way.*
