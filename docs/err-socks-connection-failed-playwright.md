---
title: "ERR_SOCKS_CONNECTION_FAILED in Playwright: what it means and how to fix it"
description: "net::ERR_SOCKS_CONNECTION_FAILED is the browser's SOCKS5 handshake to your proxy failing, not the same failure as an HTTP CONNECT tunnel. The real causes and a checklist to isolate which one you have."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 30
---


# ERR_SOCKS_CONNECTION_FAILED in Playwright: what it means and how to fix it

`net::ERR_SOCKS_CONNECTION_FAILED` means the browser tried to speak the SOCKS5
handshake to your configured proxy and it did not complete. `page.goto()` throws it
before any request to the destination site exists, because the failure happens one
layer below HTTP, in the connection to the proxy itself. This page covers the specific
causes, in the order worth checking them, and how it differs from the adjacent tunnel
error.

## What the string actually says

The string comes from Chromium's network stack. Its own source defines error `-120`,
`ERR_SOCKS_CONNECTION_FAILED`, as "failed establishing a connection to the SOCKS proxy
server for a target host." The adjacent code, `-121`,
`ERR_SOCKS_CONNECTION_HOST_UNREACHABLE`, is a distinct, later failure: the SOCKS
handshake succeeded and the proxy reported the destination, not itself, unreachable.
`ERR_SOCKS_CONNECTION_FAILED` covers everything that goes wrong before or during the
handshake: a refused connection request, rejected authentication, or an unrecognized
version.

It is also not the same problem as
[ERR_TUNNEL_CONNECTION_FAILED](err-tunnel-connection-failed-playwright.md), which is an
HTTP CONNECT exchange with an `http://` proxy going wrong. This is a SOCKS5 handshake
with a `socks5://` proxy going wrong, a different protocol with a different set of
causes.

## Where this shows up against Playwright

The pattern repeats across real reports: a SOCKS5 proxy passed to `page.goto()` throws
this string instead of loading anything.
[microsoft/playwright#19661](https://github.com/microsoft/playwright/issues/19661)
reports it from `browserServer` plus `page.goto` against a SOCKS5 endpoint, and
[puppeteer/puppeteer#10788](https://github.com/puppeteer/puppeteer/issues/10788) shows
the same string from a different Chromium-driving tool, with authentication attempted
through `page.authenticate()`, the call for HTTP Basic auth, not a SOCKS5 credential
path.
[Playwright has the identical gap](playwright-socks5-proxy-authentication.md) on its
own `proxy` option, but the two failures look nothing alike: a dropped SOCKS5
credential there is silent, and the page loads from the wrong address with no
exception. Here the proxy rejects the connection outright, before any page exists. Not
every report even has a real proxy behind it: a misrouted local debugger setting can
throw the same string with no SOCKS5 server involved.

## The realistic causes

**The SOCKS server is unreachable.** Wrong host, wrong port, the exit rotated, or a
firewall between the machine and that port. This shows up as working one day and gone
the next, with nothing in the code changed.

**Authentication negotiation failed.** SOCKS5's handshake starts with the client
offering authentication methods and the server picking one;
[RFC 1928](https://datatracker.ietf.org/doc/html/rfc1928) defines `X'FF'` as the
server's answer when none are acceptable, closing the connection right there. A
username and password are a separate sub-negotiation in
[RFC 1929](https://datatracker.ietf.org/doc/html/rfc1929), and wrong credentials fail
it just as hard. Neither produces a 401 or 407; there is no HTTP yet for either code to
exist.

**The wrong SOCKS version.** SOCKS4 and SOCKS5 are different protocols on the wire, not
a version flag inside one. A proxy provisioned for SOCKS4 or SOCKS4a does not
understand a SOCKS5 greeting, and the reverse fails too. The
[Firefox SOCKS5 auth page](playwright-socks5-proxy-authentication.md) covers the
`network.proxy.socks_version` preference this project sets to `5`.

**An HTTP-only proxy handed a `socks5://` URL.** A server that only speaks HTTP CONNECT
does not parse SOCKS5's binary greeting as anything meaningful, and hangs, resets, or
answers with bytes that are not a valid SOCKS5 reply. See
[SOCKS5 vs HTTP proxy: what each does in the browser](socks5-vs-http-proxy-browser.md)
for why the two schemes take entirely different code paths.

**A DNS resolution mismatch at the handshake step.** A SOCKS5 connection request names
its destination as one of three address types in RFC 1928: IPv4, a domain name, or
IPv6. Resolve locally and send a raw IP where the exit expects a domain name, and the
proxy can answer with reply code `0x08`, "address type not supported", landing in this
error rather than as a quieter DNS leak.

## What this looks like on this project's engine

This project drives a patched Firefox, which does not print Chromium's `net::ERR_*`
strings. Its own SOCKS5 layer, `nsSOCKSIOLayer.cpp` in Mozilla's source tree, maps the
same RFC 1928 reply codes onto NSPR errors instead: general failure, ruleset rejection,
connection refused, and an unsupported command all become `PR_CONNECT_REFUSED_ERROR`;
network unreachable becomes `PR_NETWORK_UNREACHABLE_ERROR`; host unreachable and an
unsupported address type both become `PR_BAD_ADDRESS_ERROR`; and the `X'FF'`
no-acceptable-method case also becomes `PR_CONNECT_REFUSED_ERROR`. Same protocol, same
failure conditions, different name on the way out. If you are running this project's
Firefox rather than a Chromium browser, the causes above still apply; only the label on
the failure differs.

## Diagnostic checklist

Isolate the proxy with `curl` before Playwright is anywhere in the picture, since it
prints the handshake outcome instead of collapsing it into one exception name.

```bash
curl -v --socks5 user:pass@proxy-host:proxy-port https://example.com
curl -v --socks5-hostname user:pass@proxy-host:proxy-port https://example.com
```

- Connection refused or a timeout: the proxy is unreachable from this machine. Confirm
  host, port, and that nothing rotated recently.
- An authentication failure or a stall after the greeting: credentials or the offered
  method are wrong, not reachability.
- `--socks5` fails but `--socks5-hostname` succeeds, or the reverse: the address-type
  mismatch above, and Playwright's proxy handling needs the matching DNS mode.
- `curl -x http://proxy-host:proxy-port https://example.com` succeeds where every
  SOCKS5 attempt fails: the endpoint is an HTTP proxy, whatever the provider called it.

## The honest boundary

This is a proxy and network-layer failure. It happens before the browser's fingerprint
surface is involved, so no amount of identity work reaches it. A correctly
authenticated SOCKS5 handshake looks identical whether the browser behind it is stock
Playwright, stock Firefox, or this project's build; the proxy is the party answering,
not the browser asking.

## Short answers to the questions that lead here

**What does ERR_SOCKS_CONNECTION_FAILED mean?** The browser's SOCKS5 handshake to your
configured proxy did not complete, thrown before any request to the destination site
exists.

**Is this the same as ERR_TUNNEL_CONNECTION_FAILED?** No. That one is an HTTP CONNECT
tunnel through an `http://` proxy failing; this is a SOCKS5 handshake through a
`socks5://` proxy failing.

**Why does my SOCKS5 proxy work in curl but fail in Playwright?** Compare `--socks5`
against `--socks5-hostname`. A difference means a DNS-mode or address-type mismatch,
and Playwright's configuration needs to match what the exit expects.

**Does this happen with Firefox, or only Chromium browsers?** The exact string is
Chromium's. A patched Firefox reports the same handshake failure as an NSPR error such
as `PR_CONNECT_REFUSED_ERROR`. The causes are identical; only the label differs.

**Can bad SOCKS5 credentials cause this?** Yes, loudly, if the server rejects the
method or the credentials during the handshake. A silently dropped credential is a
different, quieter problem covered on the
[SOCKS5 authentication page](playwright-socks5-proxy-authentication.md).

## Sources

- Chromium's [`net/base/net_error_list.h`](https://github.com/chromium/chromium/blob/main/net/base/net_error_list.h),
  for `ERR_SOCKS_CONNECTION_FAILED` (-120) and `ERR_SOCKS_CONNECTION_HOST_UNREACHABLE`
  (-121), retrieved 2026-08-30.
- [RFC 1928, SOCKS Protocol Version 5](https://datatracker.ietf.org/doc/html/rfc1928),
  for the `X'FF'` response, reply codes `0x01`-`0x08`, and the three address types.
- [RFC 1929, Username/Password Authentication for SOCKS V5](https://datatracker.ietf.org/doc/html/rfc1929),
  for the credential sub-negotiation.
- [microsoft/playwright#19661](https://github.com/microsoft/playwright/issues/19661)
  and [microsoft/playwright#21762](https://github.com/microsoft/playwright/issues/21762),
  two real reports of this string, one against a real SOCKS5 endpoint, one from a
  misrouted local debugger proxy.
- [puppeteer/puppeteer#10788](https://github.com/puppeteer/puppeteer/issues/10788), the
  same string with authentication attempted through `page.authenticate()`.
- [FlareSolverr/FlareSolverr#1394](https://github.com/FlareSolverr/FlareSolverr/issues/1394),
  a SOCKS5 proxy that worked and then stopped after a restart.
- Mozilla's [`nsSOCKSIOLayer.cpp`](https://hg.mozilla.org/mozilla-central/file/tip/netwerk/socket/nsSOCKSIOLayer.cpp),
  for the SOCKS5-to-NSPR error mapping this project's engine performs, retrieved
  2026-08-30.

**See also:** [ERR_TUNNEL_CONNECTION_FAILED in Playwright](err-tunnel-connection-failed-playwright.md),
[SOCKS5 proxy authentication in Playwright](playwright-socks5-proxy-authentication.md),
and [SOCKS5 vs HTTP proxy: what each does in the browser](socks5-vs-http-proxy-browser.md).

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. The proxy is handed straight to the engine's own
SOCKS5 handling; a refused handshake is the proxy answering, not the browser failing to
look real.*
