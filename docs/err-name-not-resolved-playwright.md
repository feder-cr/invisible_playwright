---
title: "net::ERR_NAME_NOT_RESOLVED in Playwright"
description: "net::ERR_NAME_NOT_RESOLVED means DNS failed before the proxy ever got a chance, or a SOCKS5 proxy is resolving hostnames on the wrong side. The Firefox preference that decides which, and how to confirm it."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 34
---


# net::ERR_NAME_NOT_RESOLVED in Playwright

`net::ERR_NAME_NOT_RESOLVED` means DNS resolution of a hostname failed outright.
Chromium's own network error list defines it in five words: "the host name could not
be resolved." `page.goto()` throws it before any connection to a destination or a
proxy is attempted for that name, because without an address there is nothing to
connect to yet.

The two questions that actually matter are which hostname failed to resolve, the
destination site's or the proxy's own, and, if a SOCKS5 proxy is involved, which side
was supposed to be doing the resolving in the first place.

## The causes

**A genuine DNS failure with no proxy involved.** A typo'd domain, a domain that has
actually stopped resolving, or a local resolver that is down or misconfigured. The
most literal reading of the error, and worth ruling out first with a plain DNS
lookup outside the browser entirely.

**A container or CI runner with no working resolver.** [microsoft/playwright#6771](https://github.com/microsoft/playwright/issues/6771)
reports exactly this shape: intermittent `net::ERR_NAME_NOT_RESOLVED` against an
internal domain, more frequent on an older Playwright version, in an automated CI
environment. Environments with restricted or misconfigured DNS egress produce this
failure with no code change involved at all.

**A SOCKS5 proxy resolving on the wrong side.** SOCKS5, unlike SOCKS4, supports
sending a raw hostname to the proxy and letting the proxy resolve it at the exit,
which is the entire reason a SOCKS5 proxy exists for someone who wants their DNS to
come from the exit's network rather than their own. If the client resolves the
destination hostname locally before ever contacting the proxy, and that local
resolver cannot see the hostname, in an isolated or restricted network the proxy
itself would have had no trouble with, the lookup fails before the proxy is ever
consulted, and the browser reports exactly this error. Mozilla's own bug tracker
carries a two-decade record of this exact class of problem under SOCKS5, [Bugzilla
134105](https://bugzilla.mozilla.org/show_bug.cgi?id=134105), "SOCKS5: DNS lookups
(host resolving) should occur on proxy, not client side," still marked resolved
incomplete after competing patches over many years never fully landed.

**The proxy's own hostname, not the destination's, is what fails.** If the proxy
itself is configured by hostname and that name will not resolve, the failure can
present at this same layer before a destination lookup is ever reached. See
[`net::ERR_PROXY_CONNECTION_FAILED` in Playwright](err-proxy-connection-failed-playwright.md)
for the closely related failure this shades into once the proxy's own name is the
one that will not resolve.

## The Firefox preference that decides the SOCKS5 case

Firefox's own proxy handling exposes exactly one preference deciding where SOCKS5
DNS resolution happens: `network.proxy.socks_remote_dns`. With it off, which is
Firefox's own stock default, every hostname resolves locally before the SOCKS5
connection is even opened. With it on, resolution happens at the proxy's end, the
behavior anyone reaching for SOCKS5 specifically usually wants. `curl`'s equivalent
distinction is the `socks5` versus `socks5h` scheme, the `h` meaning exactly this:
resolve at the proxy, not locally.

`invisible_playwright`'s own proxy wiring sets this preference to `true`
automatically whenever a `socks5://` server is passed through the documented `proxy=`
argument, [covered in full on the SOCKS5 authentication page](playwright-socks5-proxy-authentication.md#the-dns-half-which-almost-nobody-mentions).
That path is not where this error tends to originate. It shows up when a SOCKS5
proxy is configured by hand through raw `firefox_user_prefs`, bypassing that wiring,
with `network.proxy.socks_remote_dns` left at its stock, off default: the hostname
resolves against your own local or restricted network instead of the proxy's, and
that lookup fails in exactly the isolated-network shape the Mozilla bug above
describes.

## Diagnostic checklist

1. **Resolve the failing hostname directly, outside the browser.** `nslookup
   <hostname>` or `dig <hostname>` from the same machine, with no proxy involved.
   Fails there too: this is a genuine DNS problem, not a proxy-routing one.
2. **If a SOCKS5 proxy is involved, compare local versus remote resolution with
   `curl`.** `curl -v --socks5 user:pass@host:port <url>` resolves locally; `curl -v
   --socks5-hostname user:pass@host:port <url>` resolves at the proxy. One working and
   the other failing localizes the problem precisely to which side is expected to
   resolve the name.
3. **Confirm `network.proxy.socks_remote_dns` if you configured SOCKS5 by hand.** If
   you are not going through this project's documented `proxy=` argument, check the
   preference was actually set; its stock default is off.
4. **In a container or CI runner, test DNS from inside that exact environment.**
   [microsoft/playwright#6771](https://github.com/microsoft/playwright/issues/6771)
   shows this class of failure being environment-specific and intermittent rather
   than reproducible from a developer's own machine.
5. **Rule out a typo or a genuinely dead domain last**, since it is the least
   interesting cause and the easiest to confirm with a single lookup.

## The honest boundary

DNS resolution is a network-layer question, answered identically regardless of how
real the browser's fingerprint looks to a remote site. `invisible_playwright` passes
proxy configuration to the patched engine and, for SOCKS5 specifically, forces
remote resolution through the documented path; it does not retry a failed lookup,
guess at an address, or make an unreachable resolver reachable. A stock Playwright
browser and this project's build fail an unresolvable hostname identically, because
neither one is answering the failure at the identity layer.

## Short answers to the questions that lead here

**What does net::ERR_NAME_NOT_RESOLVED mean?** DNS resolution of a hostname failed
outright, before any connection to that name, proxy or destination, could even be
attempted.

**Why would this happen with a working proxy?** A SOCKS5 proxy resolving the
destination hostname locally instead of at the exit is the classic case: the local
resolver cannot see a hostname the proxy's own network could have resolved fine.

**What controls whether Firefox resolves locally or at the proxy?**
`network.proxy.socks_remote_dns`. Off by default in stock Firefox; this project sets
it to `true` automatically when a `socks5://` server goes through its documented
`proxy=` argument.

**How do I confirm which side is failing to resolve?** Compare `curl --socks5`
(local resolution) against `curl --socks5-hostname` (remote resolution) through the
same proxy and target. A difference between the two localizes the cause precisely.

**Does this ever mean the proxy itself is unreachable, not the destination?** Yes, if
the proxy is configured by hostname and that name fails to resolve. See
[ERR_PROXY_CONNECTION_FAILED](err-proxy-connection-failed-playwright.md) for that
adjacent case.

**Can invisible_playwright fix a DNS resolution failure?** No. It forces correct
remote resolution for SOCKS5 proxies configured through its own documented path, but
a genuinely dead domain, a broken local resolver, or a restricted CI network's DNS
egress are outside anything a browser's identity layer touches.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_NAME_NOT_RESOLVED` (-105), retrieved 2026-08-30.
- [microsoft/playwright#6771](https://github.com/microsoft/playwright/issues/6771),
  an intermittent report of this exact error against an internal domain from CI.
- Mozilla Bugzilla [134105](https://bugzilla.mozilla.org/show_bug.cgi?id=134105),
  "SOCKS5: DNS lookups (host resolving) should occur on proxy, not client side,"
  for the two-decade history of this exact failure class under SOCKS5.
- Mozilla's [Firefox enterprise policy reference for `Proxy`](https://firefox-admin-docs.mozilla.org/reference/policies/proxy/),
  documenting `network.proxy.socks_remote_dns` as "use proxy DNS when using SOCKS
  v5."
- The `requests` library's [documentation on SOCKS proxies](https://requests.readthedocs.io/en/latest/user/advanced/#socks),
  for the `socks5` versus `socks5h` scheme distinction referenced above.

**See also:** [Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md)
for the full DNS-resolution mechanics this page draws on, [does a proxy leak DNS?
DoH and DNS leaks explained](does-a-proxy-leak-dns-doh-explained.md) for the quieter
version of the same misconfiguration that leaks instead of failing outright, and
[ERR_PROXY_CONNECTION_FAILED in Playwright](err-proxy-connection-failed-playwright.md)
for when it is the proxy's own name, not the destination's, that will not resolve.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. DNS resolution happens below any fingerprint
this project touches; when a SOCKS5 proxy is involved, `network.proxy.socks_remote_dns`
decides which network's resolver actually gets asked.*
