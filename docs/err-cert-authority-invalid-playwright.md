---
title: "net::ERR_CERT_AUTHORITY_INVALID in Playwright"
description: "net::ERR_CERT_AUTHORITY_INVALID means the certificate chain does not trace to a root the browser trusts, often a proxy intercepting TLS. What ignoreHTTPSErrors actually disables, and the tradeoff of using it."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 35
---


# net::ERR_CERT_AUTHORITY_INVALID in Playwright

`net::ERR_CERT_AUTHORITY_INVALID` means the certificate presented during the TLS
handshake does not chain up to a certificate authority the browser trusts.
Chromium's own network error list defines it exactly: "the server responded with a
certificate that is signed by an authority we don't trust." Playwright drives real
browser engines, and those engines enforce this the same way they do for any human
using them, because certificate-chain validation is the mechanism that makes TLS mean
anything against a man-in-the-middle.

This is not the same failure as the adjacent
`ERR_CERT_COMMON_NAME_INVALID`, which fires when the chain is trusted but the
certificate's own name does not match the hostname you asked for. Authority-invalid
means the *issuer* is not trusted; common-name-invalid means the *subject* is wrong.
Read the exact code before assuming which problem you have.

## The causes

**A self-signed certificate on the target.** Common in local development and
staging environments: nothing signed it that any browser trusts by default, because
nothing was meant to.

**A proxy intercepting and re-signing TLS.** A proxy that terminates the original TLS
connection and re-establishes its own toward the destination has to present *some*
certificate to your browser for that connection, and unless its own root CA is
installed in the browser's trust store, that certificate is signed by an authority
nothing trusts. This is functionally identical, from the browser's point of view, to
a hostile machine-in-the-middle attack, because it is the same mechanism: someone
between you and the destination is terminating TLS you did not expect them to
terminate.

**An expired or misconfigured intermediate certificate.** A chain that used to
validate can stop validating the moment an intermediate CA cert expires or gets
revoked, with nothing about the leaf certificate itself having changed.

**Corporate or antivirus SSL inspection.** The same mechanism as the proxy case
above, run locally: security software that intercepts outbound HTTPS to scan it
presents its own re-signed certificate, and a browser Playwright drives on that
machine sees exactly the same untrusted-authority failure a hostile interception
would produce.

## What `ignoreHTTPSErrors` actually disables, and the tradeoff

Playwright's `ignoreHTTPSErrors` context or launch option is a real, documented
lever, and it does exactly what it says: it disables certificate-chain and hostname
validation for that browser context entirely. It is not a feature that trusts one
specific proxy's certificate and rejects everything else; it removes the check
altogether, for every connection that context makes for the rest of its life.

That is the tradeoff worth being explicit about, because "just add
`ignoreHTTPSErrors: True`" is the answer everywhere and it is rarely presented with
its actual cost. The entire reason certificate validation exists is to make TLS mean
"you are actually talking to who you think you are talking to." Turning it off to
accept your own proxy's interception certificate accepts every other invalid
certificate with it, including one from an attacker on a network you did not expect
to be adversarial. A context configured this way cannot tell the difference between
your trusted proxy's re-signed certificate and a hostile one sitting on public
Wi-Fi, because the check that would have told them apart is the check you disabled.

The narrower, safer alternative when the actual goal is trusting one specific proxy
is installing that proxy's own root CA certificate into the browser's trust store,
so only that one issuer becomes trusted, not every issuer becoming
irrelevant. Where that is not practical, the operationally honest habit is scoping
`ignoreHTTPSErrors` to sessions that are genuinely, deliberately behind a proxy you
control, never to a context whose traffic might also touch a network you do not
control, and treating a *new* certificate error appearing mid-session, on a proxy
that was previously validating fine, as a signal worth investigating rather than
suppressing further, since that is exactly the shape a captive portal or an
unexpected interception point produces.

## Diagnostic checklist

1. **Reproduce with `curl` against the same target and proxy.** `curl -v
   -x <proxy> <url>` prints the actual certificate chain rather than collapsing it
   into one exception. Read the `issuer` line specifically.
2. **Compare the issuer to what you expect.** If the certificate's issuer is your own
   proxy provider's name rather than a public CA, this confirms the proxy is
   terminating and re-signing TLS, the expected-but-unvalidated case, not a hostile
   interception.
3. **Test the same target with the proxy removed.** Validates cleanly without the
   proxy: the proxy is the one re-signing TLS, confirmed. Still fails: the
   destination's own certificate is broken, unrelated to the proxy at all.
4. **Check with `openssl s_client -connect host:443 -showcerts`** for the full chain
   and its dates, to rule out an expired intermediate specifically rather than an
   untrusted one.
5. **If the error appears suddenly on a proxy that was previously clean, stop and
   investigate before reaching for `ignoreHTTPSErrors`.** A working proxy's
   certificate does not change identity on its own; a new, different issuer showing
   up mid-project is the concrete signal the tradeoff above describes, not routine
   noise to suppress.

## The honest boundary

Certificate validation is a TLS-layer check, enforced by the browser engine the same
way for a real person and for Playwright driving it. `invisible_playwright` does not
alter certificate handling and does not make an untrusted chain trusted; the
`ignoreHTTPSErrors` lever is stock Playwright behavior, available identically on a
stock browser, and this project's engine-level realness has no bearing on whether a
given certificate validates. Realness answers what a page can observe about the
browser; it says nothing about who is actually on the other end of the TLS
handshake, which is precisely the question certificate validation exists to answer.

## Short answers to the questions that lead here

**What does net::ERR_CERT_AUTHORITY_INVALID mean?** The certificate presented during
the TLS handshake does not chain up to a certificate authority the browser trusts,
most often because it is self-signed or was re-signed by an intercepting proxy.

**Is this the same as ERR_CERT_COMMON_NAME_INVALID?** No. That error means the chain
is trusted but the certificate's name does not match the hostname requested. This
error means the issuer itself is not trusted, regardless of the name on the
certificate.

**What does ignoreHTTPSErrors actually turn off?** All certificate-chain and
hostname validation for that browser context, for every connection it makes, not
just the one proxy or certificate you intended to allow.

**Is there a safer fix than ignoreHTTPSErrors?** Installing the specific proxy's own
root CA certificate into the trust store validates only that one issuer, leaving
every other certificate's validation intact, unlike disabling the check outright.

**How do I know if a proxy is intercepting TLS versus the destination just having a
broken certificate?** Read the certificate's issuer with `curl -v` or `openssl
s_client`. An issuer matching your proxy provider confirms interception; test the
same target with the proxy removed to isolate it further.

**Does invisible_playwright fix or cause certificate validation errors?** Neither.
Certificate validation is standard TLS behavior the engine enforces the same way for
any browser; this project's stealth patching does not touch it, and `ignoreHTTPSErrors`
is stock Playwright, not something this project adds or modifies.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_CERT_AUTHORITY_INVALID` (-202) and the adjacent, distinct
  `ERR_CERT_COMMON_NAME_INVALID` (-200), retrieved 2026-08-30.
- [microsoft/playwright-python#346](https://github.com/microsoft/playwright-python/issues/346),
  a real, verbatim report of `net::ERR_CERT_AUTHORITY_INVALID` against a bare IP
  address destination.
- [microsoft/playwright#2814](https://github.com/microsoft/playwright/issues/2814),
  a user question on bypassing this exact error, illustrating how often
  `ignoreHTTPSErrors` is reached for without the tradeoff being discussed.
- Playwright's own [API reference](https://playwright.dev/python/docs/api/class-browser#browser-new-context),
  for the `ignoreHTTPSErrors` context option and its documented scope.

**See also:** [ERR_TUNNEL_CONNECTION_FAILED in Playwright](err-tunnel-connection-failed-playwright.md)
for a different proxy failure mode that happens before TLS is ever negotiated, [JA3
and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) for the
handshake layer just beneath certificate validation, and [does a proxy leak
DNS? DoH and DNS leaks explained](does-a-proxy-leak-dns-doh-explained.md) for another
case where a proxy's behavior is easy to mistake for the browser's own.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Certificate validation is stock TLS behavior,
untouched by this project's patching; `ignoreHTTPSErrors` removes that check
entirely rather than trusting one specific proxy, and that tradeoff is worth reading
before reaching for it.*
