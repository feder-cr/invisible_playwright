---
title: "ERR_TUNNEL_CONNECTION_FAILED in Playwright: what it means and how to fix it"
description: "net::ERR_TUNNEL_CONNECTION_FAILED is the browser failing to open an HTTP CONNECT tunnel through your proxy. The real causes, ruled out one by one, and the curl test that isolates them before Playwright is even involved."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 29
---


# ERR_TUNNEL_CONNECTION_FAILED in Playwright: what it means and how to fix it

`net::ERR_TUNNEL_CONNECTION_FAILED` means the browser tried to open an HTTP CONNECT
tunnel through your proxy for an HTTPS destination, and the proxy would not complete
it. `page.goto()` throws it, the navigation never gets a response, and the error tells
you almost nothing about which of the several things that can go wrong actually did.
This page is that list, and how to tell them apart before you touch Playwright at all.

## What "tunnel" actually refers to

For plain HTTP, a proxy can just forward the request. For HTTPS, the browser needs an
end-to-end encrypted connection to the destination, so it asks the proxy to open a raw
TCP tunnel first: an `HTTP CONNECT host:443` request, answered with `200 Connection
established` if the proxy agrees. Only then does the actual TLS handshake happen,
inside that tunnel. `ERR_TUNNEL_CONNECTION_FAILED` fires when the CONNECT exchange
itself fails, before any TLS or HTTP traffic to the destination exists.

Chromium's own error list defines it plainly: "a tunnel connection through the
proxy could not be established." (The name is Chromium's; Firefox's network
stack spells the same family of failures as `NS_ERROR_*` codes, and this page
uses the Chromium name because it is the one people search for.) The adjacent code, `ERR_PROXY_CONNECTION_FAILED`, is earlier and
different: it means the browser could not even open a TCP connection to the proxy
itself. `ERR_TUNNEL_CONNECTION_FAILED` means the browser did reach the proxy, and the
CONNECT for this specific destination is what failed. That distinction rules out "the
proxy is completely dead" as the cause before you look any further.

## The realistic causes, in the order to check them

**Wrong or expired proxy credentials.** An HTTP proxy that requires authentication
answers the CONNECT with `407 Proxy Authentication Required`, not `401`, which is the
origin server's code, not the proxy's. If the client retries with bad credentials or
does not retry at all, the tunnel never completes and the browser surfaces
`ERR_TUNNEL_CONNECTION_FAILED` instead of forwarding the 407 to your code. A
[changedetection.io issue](https://github.com/dgtlmoon/changedetection.io/issues/2173)
shows exactly this pair in the same log: a 407, then the tunnel error immediately
after.

**The proxy is refusing connections from this specific machine.** Not dead in general,
refusing this caller: an IP allowlist that does not cover the machine's current egress
address is a common version of this, and it explains a proxy that works from a laptop
and fails from a container or a CI runner with a different outbound IP. A
[Playwright issue](https://github.com/microsoft/playwright/issues/34252) reports
exactly that shape: identical proxy config, working on Windows locally, failing with
this error from inside a Kubernetes pod.

**The proxy does not tunnel the target port.** Many proxy servers only allow CONNECT on
a small set of ports, 443 chief among them, and reject anything outside that list
outright. Worth ruling out first if the destination is on a non-standard port.

**A scheme or transport mismatch in how the proxy is configured.** An
[Apify Crawlee issue](https://github.com/apify/crawlee/issues/3369) traced a tunnel
failure to how a browser pool built the proxy URL for an HTTPS proxy versus how
Playwright's own direct launch built it: same credentials, same host, different wiring,
one worked and one did not. The exact string the proxy option receives matters more
than it looks like it should.

**A firewall between the machine and the proxy's port.** Outbound rules that allow 443
to the open internet but block an arbitrary proxy port are easy to miss, especially
after a rotated port.

## How this meets Playwright's proxy option

Playwright's `proxy` argument (`server`, `username`, `password`) is handed to the
browser at launch, and for an `http://` or `https://` proxy server, the browser owns
the whole CONNECT exchange, credentials included. That is a real, documented
authentication path, unlike SOCKS5: see
[Playwright's proxy credentials failing silently instead of loudly](playwright-socks5-proxy-authentication.md)
for that different, quieter failure. A SOCKS5 proxy with a username and password drops
the credentials with no error at all, and the page loads from the wrong address or not
at all. An HTTP or HTTPS proxy with bad credentials throws this exact exception,
loudly, at `page.goto()`. Seeing `ERR_TUNNEL_CONNECTION_FAILED` already tells you the
credentials were sent and read; the question is why the far end rejected the exchange,
not whether it received one.

## The diagnostic checklist

Test the proxy before Playwright is anywhere in the picture. `curl` speaks the same
CONNECT method the browser does, and prints the exchange instead of hiding it behind
one exception name.

```bash
# Same host, port and credentials you pass to Playwright's proxy option.
curl -v -x http://username:password@proxy-host:proxy-port https://example.com
```

Read the `-v` output for the `CONNECT` line and the status that follows it:

- `407` on the CONNECT: credentials are wrong or expired. Confirm the exact strings
  going into Playwright's `proxy` dict match what the provider issued, and that
  nothing else in your code is also setting a proxy and overriding it.
- Connection refused or a hang with no response: the proxy is unreachable from this
  machine, or a firewall is dropping the port. Run the same `curl` from inside the
  actual environment that will run the browser, since a container or a CI runner can
  have a different egress path than the machine you are typing the command on.
- Curl succeeds and the page loads: the proxy and credentials are fine, and the
  failure is specific to Playwright's launch configuration. Recheck the `server`
  string for a scheme mismatch, `http://` versus `https://`, or credentials embedded
  in the URL alongside separate `username`/`password` fields, the exact shape the
  Crawlee issue above traced its failure to.
- Curl works on 443 but the real target sits on a different port: test that port
  specifically, since some proxies tunnel 443 only.

## The honest boundary

This is a proxy and network-layer failure, not a fingerprint problem, and no amount of
browser-identity work touches it. `invisible_playwright` passes the proxy option
straight through to the patched Firefox engine; it does not open the CONNECT tunnel
itself, retry it, or paper over a rejection. A stock Playwright browser, a stock
Firefox, and this project's binary all fail the same CONNECT the same way, because the
proxy is what says no, not the browser driving it. If curl outside Playwright also
fails against the same proxy, that confirms it.

## Short answers to the questions that lead here

**What does ERR_TUNNEL_CONNECTION_FAILED mean in Playwright?** The browser sent an HTTP
CONNECT to your proxy for an HTTPS destination and the proxy did not complete it. The
cause is almost always credentials, reachability, or a port the proxy will not tunnel,
not a Playwright defect.

**Is this a bug in Playwright or in this project's Firefox build?** No. It is a
network-layer response from the proxy, reproducible with plain `curl` and no browser
involved at all.

**Why does it work on my laptop and fail in Docker or CI?** The egress IP is usually
different, and some proxies allowlist by IP. Run the `curl -x` test from inside the
container or runner, not from your own machine, before assuming the code changed.

**How do I tell a credentials problem from a dead proxy?** Read the CONNECT status in
`curl -v`. A `407` is credentials; a refused connection or a timeout with no response
is reachability, not authentication.

**Does this ever relate to SOCKS5 instead of HTTP proxies?** The failure mode there is
different: SOCKS5 credentials that Playwright cannot carry are dropped silently with no
exception. See
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md)
for that separate, quieter failure.

**Can a stealth or antidetect browser fix ERR_TUNNEL_CONNECTION_FAILED?** No, and treat
a claim that it can as a sign the claim is not honest. The tunnel is negotiated between
the browser's network stack and the proxy, before any fingerprint surface is involved.

## Sources

- Chromium's own network error list,
  [`net/base/net_error_list.h`](https://github.com/chromium/chromium/blob/main/net/base/net_error_list.h),
  for the exact definitions of `ERR_TUNNEL_CONNECTION_FAILED` and the adjacent, earlier
  `ERR_PROXY_CONNECTION_FAILED`, retrieved 2026-08-30.
- [microsoft/playwright#34252](https://github.com/microsoft/playwright/issues/34252),
  the same proxy configuration working from a local machine and failing from inside a
  container, with a `407` visible in the log.
- [apify/crawlee#3369](https://github.com/apify/crawlee/issues/3369), an HTTPS proxy
  tunnel failing through a browser pool while the identical credentials worked through
  a direct Playwright launch, traced to how the proxy URL was built.
- [dgtlmoon/changedetection.io#2173](https://github.com/dgtlmoon/changedetection.io/issues/2173),
  a `407 Proxy Authentication Required` followed immediately by
  `net::ERR_TUNNEL_CONNECTION_FAILED` in the same Playwright session.

**See also:** [Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md)
for the separate, silent failure mode on the SOCKS5 path, [SOCKS5 vs HTTP proxy: what
each does in the browser](socks5-vs-http-proxy-browser.md) for why the two transports
fail so differently, and [web scraping keeps getting blocked with good proxies](web-scraping-getting-blocked-proxies.md)
for what a working proxy still does not fix.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. The proxy is passed straight through; a failed
CONNECT tunnel is the network answering, not the browser.*
