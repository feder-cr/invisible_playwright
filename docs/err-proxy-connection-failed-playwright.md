---
title: "net::ERR_PROXY_CONNECTION_FAILED in Playwright"
description: "net::ERR_PROXY_CONNECTION_FAILED means the browser could not even open a socket to the proxy server, before any CONNECT tunnel is attempted. How it differs from ERR_TUNNEL_CONNECTION_FAILED, and the causes worth checking first."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 32
---


# net::ERR_PROXY_CONNECTION_FAILED in Playwright

`net::ERR_PROXY_CONNECTION_FAILED` means the browser could not open a connection to
the proxy server itself. Chromium's own network error list defines it precisely:
"Could not create a connection to the proxy server. An error occurred either in
resolving its name, or in connecting a socket to it." Nothing about the destination
site is involved yet, because the browser never got past the proxy.

## Not the same failure as ERR_TUNNEL_CONNECTION_FAILED

This is the distinction worth making explicit, because the two names look similar
and get confused constantly. [`ERR_TUNNEL_CONNECTION_FAILED`](err-tunnel-connection-failed-playwright.md)
fires when the browser *did* reach the proxy, sent an HTTP `CONNECT` request for a
specific HTTPS destination, and the proxy refused or failed that specific tunnel,
commonly over bad credentials or a destination it will not forward to.
`ERR_PROXY_CONNECTION_FAILED` fires earlier and more broadly: the browser never
managed to open a TCP socket to the proxy at all, whether the destination is HTTP or
HTTPS, whether authentication was ever going to be needed, before any `CONNECT`
exchange is possible. Chromium keeps them as two distinct codes for exactly
that reason: `-130` for `ERR_PROXY_CONNECTION_FAILED`, `-111` for
`ERR_TUNNEL_CONNECTION_FAILED` - one failure before any `CONNECT`, one during
it.

If you see `ERR_TUNNEL_CONNECTION_FAILED`, the proxy is reachable and rejected one
specific request. If you see `ERR_PROXY_CONNECTION_FAILED`, the proxy was never
reachable in the first place, or its own hostname never resolved. Treat them as two
different bugs; fixing credentials does nothing for the second one.

## The realistic causes

**The proxy address or port is wrong, or the proxy is down.** The most literal
reading of the error: nothing is listening where the client tried to connect.
Confirm host, port and that the proxy process is actually running and reachable from
the network the browser is on.

**The proxy's own hostname will not resolve.** Chromium's own definition names DNS
resolution of the proxy's name as one of the two failure paths this code covers. If
the proxy is configured by hostname rather than a bare IP, a broken or unreachable
resolver on the machine fails before a socket to the proxy is even attempted. See
[`net::ERR_NAME_NOT_RESOLVED` in Playwright](err-name-not-resolved-playwright.md) for
the sibling error when it is the *destination's* name that fails instead of the
proxy's own.

**A firewall blocks the specific port the proxy listens on.** Outbound rules that
allow normal web traffic on 443 but nothing else will drop a connection attempt to
an arbitrary proxy port silently, from the client's perspective indistinguishable
from the proxy being offline.

**Playwright's own `server: "per-context"` proxy mode misconfigured.** A concrete,
real bug: [microsoft/playwright#9437](https://github.com/microsoft/playwright/issues/9437)
reports launching the browser with `proxy={"server": "per-context"}` and then
creating a `browser.new_context()` with no proxy object supplied, expecting the
context to simply have no proxy. Instead, navigation throws
`net::ERR_PROXY_CONNECTION_FAILED`, because the browser was told a proxy would be
supplied per context and none was, leaving it trying to route through a proxy
configuration that does not resolve to anything real.

**A VPN or system-level proxy setting introduced an incompatible PAC file or
endpoint.** Documented in the wild as a side effect of VPN client updates that
rewrite system proxy configuration: Chromium picks up a PAC-resolved proxy endpoint
the VPN update introduced, and that endpoint is not actually reachable.

## What Firefox calls the same failure

`invisible_playwright` drives a patched Firefox rather than Chromium, and Firefox's
own networking layer does not use the `net::ERR_*` naming at all. The same class of
failure, an inability to reach the configured proxy, surfaces in Firefox as
`NS_ERROR_PROXY_CONNECTION_REFUSED`, tracked directly in Mozilla's own bug tracker
(for example, [Bugzilla 549299](https://bugzilla.mozilla.org/show_bug.cgi?id=549299),
titled "'Proxy server refusing connections' error when SOCKS proxy merely can't
connect," and [Bugzilla 493699](https://bugzilla.mozilla.org/show_bug.cgi?id=493699)
for the equivalent HTTPS case). If you are chasing this failure against Firefox
rather than a Chromium browser, search your logs for that name instead of the
Chromium string.

## Diagnostic checklist

Test reachability to the proxy itself before Playwright is anywhere in the picture.

```bash
# Confirms only that a socket can be opened to the proxy, nothing about the tunnel.
nc -vz proxy-host proxy-port

# Isolates DNS resolution of the proxy's own hostname specifically.
nslookup proxy-host
```

- `nc` hangs or refuses: the proxy is unreachable from this machine, full stop.
  Confirm the host and port are current, since a rotated proxy endpoint produces
  exactly this.
- `nslookup` fails on the proxy's own hostname: the failure is DNS, not a dead
  proxy process, and no amount of retrying the connection fixes a name that will not
  resolve.
- Both succeed, but Playwright still throws this error: recheck the proxy
  configuration Playwright actually received, particularly a `server:
  "per-context"` launch paired with a context created without its own proxy object,
  the shape of the real bug cited above.
- Runs fine from your own machine and fails only in a container or CI runner: test
  from inside that exact environment. A different egress path or a different, more
  restrictive firewall ruleset than your own machine's is common.

## The honest boundary

This is a network-reachability failure, resolved or not before any TLS handshake or
JavaScript-visible browser identity is involved. `invisible_playwright` hands the
proxy configuration straight to the patched engine; it does not open the connection
to the proxy on your behalf, retry a dead endpoint, or make an unreachable proxy
reachable. A stock Playwright Chromium, a stock Firefox, and this project's build
all fail identically against a proxy that cannot be reached, because the failure
happens before any browser-specific behavior begins.

## Short answers to the questions that lead here

**What does net::ERR_PROXY_CONNECTION_FAILED mean?** The browser could not open a
connection to the configured proxy server at all, either because its name would not
resolve or because a socket to it could not be opened. No destination site is
involved yet.

**How is this different from ERR_TUNNEL_CONNECTION_FAILED?** Tunnel failure means
the proxy was reached and refused one specific HTTPS `CONNECT` request, often over
credentials. This error means the proxy itself was never reachable in the first
place, before any such request could be sent.

**Could a Playwright configuration mistake cause this rather than a real network
problem?** Yes. A real, confirmed bug exists where a `server: "per-context"` proxy
launch paired with a context created without its own proxy object throws exactly
this error.

**What does Firefox call this instead of ERR_PROXY_CONNECTION_FAILED?**
`NS_ERROR_PROXY_CONNECTION_REFUSED`, tracked under that name in Mozilla's own
Bugzilla.

**Does invisible_playwright's stealth patching affect this error?** No. It is a
reachability failure to the proxy itself, resolved entirely below any
fingerprint or engine-identity layer this project touches.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for the exact definitions of `ERR_PROXY_CONNECTION_FAILED` (-130) and the
  distinct `ERR_TUNNEL_CONNECTION_FAILED` (-111), retrieved 2026-08-30.
- [microsoft/playwright#9437](https://github.com/microsoft/playwright/issues/9437),
  a real report of this exact error from a `server: "per-context"` proxy launch
  paired with a context created with no proxy object.
- Mozilla Bugzilla [549299](https://bugzilla.mozilla.org/show_bug.cgi?id=549299) and
  [493699](https://bugzilla.mozilla.org/show_bug.cgi?id=493699), for
  `NS_ERROR_PROXY_CONNECTION_REFUSED`, the Firefox-side name for the same class of
  failure.
- Chromium developer discussion groups on
  [ERR_PROXY_CONNECTION_FAILED](https://groups.google.com/a/chromium.org/g/chromium-discuss/c/NDmGCxeGSSk),
  for real-world reports of the error's causes outside of Playwright specifically.

**See also:** [ERR_TUNNEL_CONNECTION_FAILED in Playwright](err-tunnel-connection-failed-playwright.md)
for the later, tunnel-specific failure this page is most often confused with,
[ERR_SOCKS_CONNECTION_FAILED in Playwright](err-socks-connection-failed-playwright.md)
for the equivalent failure on a SOCKS5 proxy instead of an HTTP one, and
[ERR_NAME_NOT_RESOLVED in Playwright](err-name-not-resolved-playwright.md) for when
it is the destination's name, not the proxy's, that fails to resolve.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. The proxy address is handed straight to the
engine; failing to reach it is the network answering before the browser's identity
layer is ever involved.*
