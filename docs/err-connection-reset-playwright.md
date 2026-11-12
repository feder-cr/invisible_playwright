---
title: "net::ERR_CONNECTION_RESET in Playwright"
description: "net::ERR_CONNECTION_RESET means a TCP RST tore the connection down mid-request. Proxy-side causes versus server-side causes, told apart with the proxy removed, plus the Firefox name for the same failure."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 31
---


# net::ERR_CONNECTION_RESET in Playwright

`net::ERR_CONNECTION_RESET` means the TCP connection was torn down with a reset
packet, an RST, rather than a clean close. Something on the path decided the
connection should stop existing right now, instead of finishing whatever request or
response was in flight. Chromium's own network error list defines it plainly: "a
connection was reset (corresponding to a TCP RST)." `page.goto()` throws it when the
reset lands during navigation; it can also fire mid-session on a page that had
already loaded.

The string tells you a reset happened. It does not tell you which end sent it, which
is the actual question worth answering before touching any code.

## Proxy-side causes

**The proxy killed the connection on its own terms.** A connection-time limit, a
per-request timeout tighter than the origin server's response time, or a plan-level
cap on concurrent or total connections can all end with the proxy sending the RST
itself rather than passing through whatever the origin was doing. This looks
identical, from the browser's side, to the origin resetting the connection.

**The proxy process itself restarted or was killed mid-request.** Every connection it
was holding open ends with a reset at that instant, which reads as a sudden, brief
burst of this error across whatever was in flight at the time, then nothing.

**A middlebox on the proxy's own path reset it.** A proxy is itself a client to the
origin, and its own connection can be reset by something between the proxy and the
destination, independent of anything wrong with your request.

## Server-side and network-path causes

**The origin server reset the connection deliberately.** A server-side rate limit,
a WAF rule, or an anti-automation response can close a connection with an RST
instead of returning a normal HTTP response, in which case the reset itself is the
site's answer, not an accident.

**A firewall or IPS on the path injected the reset.** This is a documented behavior of
network security appliances: sending a spoofed RST to both ends kills a connection
without either endpoint needing to cooperate, and it can happen anywhere between the
browser and the origin, proxy included.

**Antivirus or endpoint security software on the machine running Playwright.**
Reported directly in Playwright's own issue tracker: connection resets that trace
back to local security software inspecting or interfering with outbound TLS
connections, not to anything remote.

## What Firefox calls the same failure

`net::ERR_CONNECTION_RESET` is Chromium's net-stack naming. `invisible_playwright`
drives a patched Firefox with its own, untouched networking stack, and Firefox
reports the identical failure, a connection torn down by a TCP RST, as
`NS_ERROR_NET_RESET`. This is the same Firefox name already covered on [the
ERR_HTTP2_PROTOCOL_ERROR page](err-http2-protocol-error-playwright.md#what-the-string-is-and-which-browser-actually-says-it)
for the HTTP/2-specific variant of the same underlying event; searching this exact
Chromium string while driving Firefox is the wrong search, look for
`NS_ERROR_NET_RESET` in your logs instead.

## Diagnostic checklist

1. **Remove the proxy and retry against the same target directly.** Gone without a
   proxy, the proxy or the path to it is implicated. Still resets, the origin or a
   network device between you and it is the more likely cause.
2. **Reproduce with `curl` outside Playwright entirely.** `curl -v -x <proxy>
   <url>` isolates whether the browser is even a variable. A real Playwright report
   traced tests passing locally and failing consistently in a CI runner
   ([microsoft/playwright#16749](https://github.com/microsoft/playwright/issues/16749)),
   which is exactly the shape a path-dependent reset produces and `curl` from inside
   that same environment would have shown directly.
3. **Check whether it is consistent or intermittent.** A reset on every single
   attempt against one target points at something structural, a firewall rule, a
   deliberate server-side block. An intermittent reset under load points more at a
   proxy connection cap or limit being hit.
4. **Check local security software before assuming the network is at fault.**
   [microsoft/playwright-python#815](https://github.com/microsoft/playwright-python/issues/815)
   and related reports describe this exact string on headless Linux/CI machines with
   no proxy involved at all, worth ruling out before chasing a network explanation
   for a local one.
5. **Ask your proxy provider directly about connection or rate limits**, since a
   limit enforced by closing the connection produces this exact string with nothing
   in your own logs distinguishing it from a hostile reset.

## The honest boundary

A TCP RST happens below the TLS handshake and well below anything a browser's
JavaScript-visible identity touches. `invisible_playwright` passes the proxy option
straight to the patched engine and does not retry, absorb, or paper over a reset;
a stock Playwright browser, a stock Firefox, and this project's build all see the
identical reset from the identical proxy or server, because the reset is generated
on the wire, not answered by anything the browser claims to be.

## Short answers to the questions that lead here

**What does net::ERR_CONNECTION_RESET mean?** The TCP connection was torn down by a
reset packet mid-request, rather than closed cleanly. The string does not say which
side sent the reset.

**Is this always the proxy's fault?** No. It can come from the proxy itself, a
deliberate reset from the origin server, a firewall or security appliance on the
path, or local antivirus/security software with no remote cause at all.

**How do I tell a proxy-side reset from a server-side one?** Remove the proxy and
retry directly. If the reset disappears, the proxy or the path to it caused it; if it
persists, look at the origin or a device between you and it.

**Why does it only happen in CI, not locally?** A different network path, a
different egress IP subject to a different rule, or a runner-specific firewall or
proxy configuration are the usual explanations; reproduce with `curl` from inside the
CI environment itself rather than guessing from your own machine.

**What does Firefox call this instead of ERR_CONNECTION_RESET?** `NS_ERROR_NET_RESET`.
Same underlying TCP-level event, Chromium and Firefox just name it differently.

**Can invisible_playwright's stealth patching cause or fix this?** Neither. The
reset happens at the TCP layer, entirely below any fingerprint or JavaScript-visible
identity surface this project touches.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_CONNECTION_RESET` (-101), retrieved 2026-08-30.
- [microsoft/playwright-python#815](https://github.com/microsoft/playwright-python/issues/815),
  a real report of this exact string on headless Ubuntu Chromium with no proxy
  involved.
- [microsoft/playwright#16749](https://github.com/microsoft/playwright/issues/16749),
  tests passing locally and failing with `net::ERR_CONNECTION_RESET` /
  `NS_ERROR_NET_RESET` consistently inside an Azure DevOps CI runner.
- [microsoft/playwright#8746](https://github.com/microsoft/playwright/issues/8746),
  the same string reported under headless Chromium on a CentOS runner.
- Mozilla's [HTTP logging documentation](https://firefox-source-docs.mozilla.org/networking/http/logging.html),
  for capturing the `NS_ERROR_NET_RESET` name on the Firefox side.

**See also:** [ERR_HTTP2_PROTOCOL_ERROR in Playwright](err-http2-protocol-error-playwright.md)
for the HTTP/2-specific framing variant of a connection dying mid-stream,
[ERR_TUNNEL_CONNECTION_FAILED in Playwright](err-tunnel-connection-failed-playwright.md)
for a proxy failure that happens before any connection to the destination exists at
all, and [ERR_PROXY_CONNECTION_FAILED in Playwright](err-proxy-connection-failed-playwright.md)
for the earlier failure of not reaching the proxy in the first place.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. A TCP reset is generated on the wire, by the
proxy, the origin, or something between them; the browser's identity layer has no
part in it either way.*
