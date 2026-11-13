---
title: "Playwright Firefox: SEC_ERROR_UNKNOWN_ISSUER"
description: "SEC_ERROR_UNKNOWN_ISSUER is Firefox's own NSS certificate-trust error, not a Chromium string. Why curl works inside a Docker container and Firefox still fails, and the MOZILLA_PKIX_ERROR_MITM_DETECTED variant Firefox raises when it recognizes interception."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 27
---


# Playwright Firefox: SEC_ERROR_UNKNOWN_ISSUER

`SEC_ERROR_UNKNOWN_ISSUER` is what Firefox itself says when a certificate arrives
during a TLS handshake and Firefox cannot build a chain from it up to a root it
trusts. Sectigo's own support documentation states it plainly: the error means
"Firefox is unable to chain up to a trusted Certificate Authority based on
information provided to Firefox from the web site you're visiting." The chain is
broken, not necessarily the certificate at the end of it.

If you already know `net::ERR_CERT_AUTHORITY_INVALID` from Chromium, this is the
same underlying condition described by a different browser's own error stack, and
the distinction matters for anyone driving Firefox with Playwright.

## Not the same string as ERR_CERT_AUTHORITY_INVALID, and why it matters here

[`ERR_CERT_AUTHORITY_INVALID`](err-cert-authority-invalid-playwright.md), covered
separately on this site, is Chromium's own network-stack naming: `net::` prefix,
numeric code -202, defined in Chromium's source tree. `SEC_ERROR_UNKNOWN_ISSUER` is
not a Playwright string or a wrapper string, and it is not that Chromium string
ported over. It comes from NSS, the certificate library Firefox has always used,
surfaced through Firefox's own PSM (Personal Security Manager) error list. Two
browsers, two independent TLS stacks, two different names for the same category of
failure: an issuer the browser does not trust.

This is not a cosmetic detail for `invisible_playwright`. The wrapper drives a
[patched Firefox binary](does-playwright-support-firefox-stealth.md), not a
Chromium build, so the string a script actually sees on a broken certificate chain
is `SEC_ERROR_UNKNOWN_ISSUER` (or its MITM-specific sibling below), never the
Chromium one. Searching your logs for `ERR_CERT_AUTHORITY_INVALID` against this
project is searching for a string this engine does not produce.

## The realistic causes

**A Docker container missing CA certificates entirely.** Minimal base images, the
kind most CI and scraping containers start from, frequently ship without the
`ca-certificates` package installed at all, so nothing in the container's trust
store validates anything, legitimate or otherwise. The fix at the OS level is
routine: install `ca-certificates` and run `update-ca-certificates` on
Debian/Ubuntu-based images, or the CentOS-family equivalent against
`/etc/pki/ca-trust/source/anchors/`. That step alone often "fixes" `curl` inside
the same container while Firefox keeps failing, for the reason in the next section.

**A self-signed certificate on the target**, the ordinary local-development and
staging case: nothing signed it that any browser trusts by default, Firefox
included, because nothing was meant to.

**A proxy or corporate security product intercepting and re-signing TLS.** A device
between the browser and the destination that terminates the original connection and
presents its own certificate produces exactly this error unless its root CA is
separately installed and trusted. This is functionally identical, from Firefox's
point of view, to a hostile interception, because the mechanism is the same one
either way.

**Firefox keeping its own certificate store separate from the operating system's.**
This is the specific reason "curl works, Firefox doesn't" is the single most common
shape this bug takes inside a container. Firefox has historically shipped and
trusted its own NSS-based root store rather than reading the OS bundle the way
`curl` and most system tools do. Mozilla's own support documentation describes the
bridge for this: on Windows, macOS and Android, Firefox by default searches the
operating system's certificate store for third-party roots an administrator or a
program has added. Linux is conspicuously absent from that list, which lines up
with two real Playwright bug reports.
[microsoft/playwright#7611](https://github.com/microsoft/playwright/issues/7611)
reports that inside the official Playwright Docker image, "curl -v inside of Docker
works correctly and picks up the custom certificates, but it seems that the
Browsers don't," with both Chromium and Firefox failing and `SEC_ERROR_UNKNOWN_ISSUER`
named explicitly. [microsoft/playwright#33596](https://github.com/microsoft/playwright/issues/33596),
filed against Playwright on Ubuntu, shows the same split for Firefox specifically: a
certificate created with `mkcert`, `curl` reporting `SSL certificate verify ok`, and
Firefox throwing `page.goto: SEC_ERROR_UNKNOWN_ISSUER` on the identical host.
`update-ca-certificates` updates the container's system trust store; it does not
put that root into Firefox's own NSS database or flip the pref that would make
Firefox read the system store on Linux. An open feature request,
[microsoft/playwright#35815](https://github.com/microsoft/playwright/issues/35815),
tracks the sharper version of this gap: Firefox 137 tightened private-CA handling
further and now expects a `policies.json` enterprise-roots configuration Playwright
has no supported way to inject.

## The MOZILLA_PKIX_ERROR_MITM_DETECTED variant

Firefox does not always show plain `SEC_ERROR_UNKNOWN_ISSUER` when something is
actively intercepting traffic. Since Firefox 67, it can instead show
`MOZILLA_PKIX_ERROR_MITM_DETECTED`, and the difference is not cosmetic; it reflects
an actual second check Firefox ran. [Mozilla's own bug
tracker](https://bugzilla.mozilla.org/show_bug.cgi?id=1529643) documents the
mechanism: on hitting a certificate error, Firefox fires a background "priming"
request to a Mozilla-operated detection endpoint and compares the issuer it gets
back against the issuer on the page that just failed. A match means whatever is
intercepting the browser's own connections is broad enough to also be intercepting
that background request, which is a much stronger signal than one bad certificate
on one site, and Firefox can go on to auto-enable the same
`security.enterprise_roots.enabled` preference described above if a further pref
allows it to. In practice this means: a corporate or antivirus TLS-inspection
product that intercepts everything commonly produces the MITM-specific error, while
a single misconfigured server, or a self-signed certificate on one test host, stays
plain `SEC_ERROR_UNKNOWN_ISSUER`. Appuals' own troubleshooting writeup for this
error lists the usual real-world triggers seen in the wild: security suites doing
HTTPS scanning or filtering (Avast, Bitdefender, Kaspersky, ESET are the commonly
named ones), and, more rarely, a VPN or proxy product doing the same thing.

## Diagnostic checklist

1. **Confirm with `curl` or `openssl s_client -connect host:443 -showcerts` from
   inside the exact same container or environment.** If those validate cleanly
   while Firefox fails, you have just reproduced the "curl works, Firefox doesn't"
   shape directly, and the cause is almost always Firefox's separate trust store,
   not a broken certificate.
2. **Check whether `ca-certificates` is actually installed and updated in the
   image**, distinct from whether Firefox picks it up. Both steps are required and
   neither substitutes for the other.
3. **Test with the proxy or security product removed from the path entirely.**
   Clears: the intercepting layer is the cause, not the destination's own
   certificate.
4. **Read the exact error string, not just "certificate error."**
   `MOZILLA_PKIX_ERROR_MITM_DETECTED` specifically means Firefox's own background
   check matched the intercepting issuer against your failing page's issuer; plain
   `SEC_ERROR_UNKNOWN_ISSUER` means it did not run that check or did not get a
   match, which is common for a one-off self-signed host.
5. **If a private CA is genuinely trusted and Firefox still refuses it**, check
   whether the fix requires `security.enterprise_roots.enabled` or, on newer
   Firefox versions, an enterprise `policies.json`, rather than assuming the
   OS-level `update-ca-certificates` step was sufficient.

## The honest boundary

Certificate validation runs inside NSS, the same TLS and PKI library this project's
patched Firefox uses unmodified. `invisible_playwright` does not alter certificate
handling, does not add a private CA to the trust store on your behalf, and does not
make an untrusted chain trusted. A clean fingerprint answers what a page can
observe about the browser; it says nothing about whether the certificate presented
during the handshake actually chains to a root anyone should trust, which is
precisely the question this error exists to raise. Fixing it is a certificate and
trust-store problem, solved the same way for a stock Firefox and for this one.

## Short answers to the questions that lead here

**What does SEC_ERROR_UNKNOWN_ISSUER mean?** Firefox could not build a chain of
trust from the certificate a site presented up to a root certificate authority it
trusts. It is Firefox's own NSS-based error, not a Chromium string.

**Is this the same as net::ERR_CERT_AUTHORITY_INVALID?** It is the same category of
failure, an untrusted issuer, reported by a different browser's own TLS stack.
Chromium calls it `ERR_CERT_AUTHORITY_INVALID`; Firefox calls it
`SEC_ERROR_UNKNOWN_ISSUER`. `invisible_playwright` drives Firefox, so this is the
string that actually appears.

**Why does curl work inside my Docker container while Firefox still fails?** `curl`
reads the system CA bundle you just updated with `update-ca-certificates`. Firefox
historically keeps its own separate NSS trust store and, on Linux, does not read
the OS store by default the way it does on Windows and macOS, so the same fix that
repairs `curl` often does nothing for Firefox.

**What does MOZILLA_PKIX_ERROR_MITM_DETECTED add over the plain error?** It means
Firefox ran a background check against its own detection endpoint and found the
same intercepting issuer there, confirming broad interception rather than one bad
certificate on one site.

**Does invisible_playwright fix or cause this error?** Neither. NSS enforces
certificate validation the same way for any Firefox, patched or stock. This
project's fingerprint work sits above this layer entirely and does not touch trust
decisions.

## Sources

- Sectigo's own [Firefox error code: SEC_ERROR_UNKNOWN_ISSUER](https://support.sectigo.com/articles/Knowledge/Firefox-error-code-sec-error-unknown-issuer-1527076112539)
  knowledge base article, for the definition and the missing-intermediate-chain
  cause, retrieved 2026-08-30.
- Appuals' [Fix: MOZILLA_PKIX_ERROR_MITM_DETECTED Error on
  Firefox](https://appuals.com/mozilla-pkix-error-mitm-detected/), for the named
  real-world triggers (antivirus HTTPS scanning, VPN/proxy interception), retrieved
  2026-08-30.
- Mozilla's own bug tracker,
  [bug 1529643, "On certificate error pages, trigger an internal canary request to
  detect MitM"](https://bugzilla.mozilla.org/show_bug.cgi?id=1529643), for the
  priming-request mechanism that distinguishes the MITM-specific error, shipped in
  Firefox 67, retrieved 2026-08-30.
- [microsoft/playwright#7611](https://github.com/microsoft/playwright/issues/7611),
  a real report of `curl` validating a custom certificate inside the official
  Playwright Docker image while both Chromium and Firefox fail with
  `SEC_ERROR_UNKNOWN_ISSUER`.
- [microsoft/playwright#33596](https://github.com/microsoft/playwright/issues/33596),
  a real report of Firefox on Ubuntu throwing `SEC_ERROR_UNKNOWN_ISSUER` on a
  `mkcert`-issued certificate that `curl` verifies successfully on the same host.
- [microsoft/playwright#35815](https://github.com/microsoft/playwright/issues/35815),
  an open feature request describing Firefox 137's tightened private-CA
  requirements and the lack of a supported `policies.json` path in Playwright.

**See also:** [ERR_CERT_AUTHORITY_INVALID in Playwright](err-cert-authority-invalid-playwright.md)
for the Chromium-flavored version of this same failure category and the
`ignoreHTTPSErrors` tradeoff, [ERR_SSL_PROTOCOL_ERROR in Playwright](err-ssl-protocol-error-playwright.md)
for the handshake-level failure this one is often confused with, and
[how to use invisible_playwright in Docker](how-to-use-invisible-playwright-in-docker.md)
for the rest of what changes about running this project inside a container.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. Certificate trust is NSS's job, untouched by
this project's patching; a Docker image that fixes curl and still fails Firefox is
reading two different trust stores, not fighting two different bugs.*
