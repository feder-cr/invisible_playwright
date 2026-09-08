---
title: "net::ERR_EMPTY_RESPONSE in Playwright"
description: "net::ERR_EMPTY_RESPONSE means the server closed the connection without sending a single byte back, not even a status line. A misbehaving proxy, a crashed backend, and a deliberate anti-automation drop all produce it identically."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 36
---


# net::ERR_EMPTY_RESPONSE in Playwright

`net::ERR_EMPTY_RESPONSE` means the connection to the server was made, the request went
out, and the server closed the connection without sending anything back at all: no
status line, no headers, no body. Chromium's own network error list defines it exactly
that way, code -324, "The server closed the connection without sending any data."
`page.goto()` throws it after the TCP or TLS layer succeeded, which is the detail worth
holding onto before reading anything else on this page: this is not a reachability
failure and not a handshake failure. Something answered the socket, then said nothing.

## The realistic causes

**A crashed or overloaded backend.** A worker process dying mid-request, an
out-of-memory kill, or a request handler that raises before writing a single byte all
leave the reverse proxy or load balancer in front of it holding an open connection with
nothing to forward. The proxy closes the client connection instead of inventing a
response, and the browser reports exactly this.

**A proxy or load balancer timing out on the backend and giving up without answering.**
Distinct from a client-visible timeout: the proxy's own patience with the origin server
ran out, and instead of returning a proper `502` or `504` it drops the connection
silently. Whether a given proxy does this depends entirely on its configuration; some
always synthesize an error page, some do not.

**A server deliberately closing the connection instead of answering.** This is a
documented, ordinary feature of production web servers, not a theory about hostile
intent. nginx's own status code `444` exists specifically to close a connection without
sending any response, and the `reset_timedout_connection` directive, in nginx's own
words, applies to "connections closed with the non-standard code 444," releasing the
socket with a TCP reset, not a normal close. A server operator who wants to deny
a request with zero information leakage, whether the requester is a scanner, a bot, or
simply unwanted traffic, has this as a first-class, standard option: return nothing
rather than a status code that says "blocked." That is the one cause on this list
where the error is not a fault to repair but a decision made about your session, and
the question then moves off this page and onto
[why the script is being refused in the first place](why-does-my-playwright-script-get-blocked.md).

**A proxy-side rewrite or bypass rule interacting badly with routing.** [A real Playwright
report](https://github.com/microsoft/playwright/issues/20703) traces `ERR_EMPTY_RESPONSE`
to a `proxy.bypass` configuration combined with a manual host mapping for one domain; the
manual mapping worked and the proxy path did not, pointing at the proxy's own routing for
that hostname rather than the destination server.

**A dev-server or component-test harness racing its own startup.** [A component-testing
report](https://github.com/microsoft/playwright/issues/16424) shows `ERR_EMPTY_RESPONSE`
against `localhost` on a second test run specifically, which is a different shape from a
remote target: a local server that has not finished binding, or a build tool serving a
half-written response, produces the identical string with nothing remote involved at all.

## What Firefox calls this, and why the naming does not map one to one

`invisible_playwright` drives a patched Firefox, and Firefox's networking layer does not
use `net::ERR_*` strings. Here the mapping is worth doing carefully rather than by name
association, because Firefox's own error list, `ErrorList.py` in the Firefox source tree,
splits this differently than the name would suggest.

`NS_ERROR_NET_RESET`, error 20, is defined as "the connection was established, but no
data was ever received." That is the literal match for what Chromium's
`ERR_EMPTY_RESPONSE` describes: a connection that opened and then produced zero bytes.

`NS_ERROR_NET_EMPTY_RESPONSE`, error 36, despite the name, is defined narrower: "the
connection was established, but browser received an empty page with 4xx, 5xx error
response." That is a response that did carry a status line, an error one, with nothing
in the body, which is a different event from silence on the wire.

So a Firefox log reporting `NS_ERROR_NET_RESET` is the closer analog to what a Chromium
user means by `ERR_EMPTY_RESPONSE`, not the similarly-named
`NS_ERROR_NET_EMPTY_RESPONSE`. Read the Firefox error name literally rather than by
resemblance to the Chromium string you searched for.

## Diagnostic checklist

1. **Reproduce with `curl -v` against the same URL through the same proxy, outside
   Playwright entirely.** `curl` prints exactly what came back, including a connection
   that closes with zero bytes, and removes the browser as a variable.
2. **Remove the proxy and retry direct.** Gone without it, the proxy or the path to it is
   implicated, matching the shape in the routing-bypass report above. Persists, the origin
   server or something between you and it is next. If the proxy never opened the tunnel
   at all you would be reading
   [ERR_TUNNEL_CONNECTION_FAILED](err-tunnel-connection-failed-playwright.md), and if the
   socket was torn down after some bytes had moved,
   [ERR_CONNECTION_RESET](err-connection-reset-playwright.md): the three differ only in
   how far the exchange got.
3. **Check whether it is one target or many.** Consistent on one specific site with
   everything else loading normally is the shape of a deliberate drop; intermittent across
   unrelated targets points at your own proxy or network path.
4. **If the target is your own dev server or test harness, check it finished starting.**
   A `localhost` empty response, especially only on a second or later run, is often a race
   with the server's own startup or a build step, not a remote failure at all.
5. **Ask whether the same request succeeds from a different exit or network.** Success
   elsewhere with the identical request narrows the cause to something specific about the
   current IP or proxy, which is consistent with, though not proof of, a deliberate block.

## The honest boundary

An empty response is generated by whatever closed the connection: a backend process, a
proxy, a load balancer, or a server explicitly built to answer unwanted requests with
silence. `invisible_playwright` passes requests through the patched engine's own network
stack and does not intercept, retry, or paper over a connection that closes with nothing
in it. A stock Playwright Chromium, a stock Firefox, and this project's build all see the
identical empty response from the identical proxy or server, because nothing about a
browser's JavaScript-visible identity changes what happens at the TCP layer before a
single header exists.

## Short answers to the questions that lead here

**What does net::ERR_EMPTY_RESPONSE mean?** The connection to the server succeeded and
then the server closed it without sending any data back at all, not even a status line.

**Is this the same as a timeout?** No. A timeout is a connection attempt or a wait that
never resolves. This is a connection that resolved and then produced silence.

**Could this be a deliberate block rather than a bug?** Yes, and it is a documented,
ordinary server capability, not a hypothesis: closing a connection with no response is a
standard way to deny a request without revealing anything about why.

**Why does it only happen on my local dev server, not in production?** A race with the
server's own startup or a build step is common on `localhost`, distinct from a remote
target's behavior; check that the server has actually finished binding before Playwright
navigates to it.

**What does Firefox call this instead of ERR_EMPTY_RESPONSE?** `NS_ERROR_NET_RESET` is
the closer match, defined as a connection established with no data ever received. The
similarly-named `NS_ERROR_NET_EMPTY_RESPONSE` is a narrower case, an empty body on a 4xx
or 5xx status line, not silence on the wire.

**Can invisible_playwright prevent or fix an empty response?** No. It is generated by
whatever closed the connection, below any layer this project's stealth patching touches.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_EMPTY_RESPONSE` (-324), "The server closed the connection without sending any
  data," retrieved 2026-08-30.
- Mozilla's [`xpcom/base/ErrorList.py`](https://searchfox.org/mozilla-central/source/xpcom/base/ErrorList.py)
  (viewed via the Fossies mirror), for `NS_ERROR_NET_RESET` (20) and
  `NS_ERROR_NET_EMPTY_RESPONSE` (36) and their exact definitions, retrieved 2026-08-30.
- nginx's own documentation for [`reset_timedout_connection`](https://nginx.org/en/docs/http/ngx_http_core_module.html#reset_timedout_connection),
  confirming the non-standard status `444` closes a connection without sending a
  response, retrieved 2026-08-30.
- [microsoft/playwright#20703](https://github.com/microsoft/playwright/issues/20703), a
  real `ERR_EMPTY_RESPONSE` report traced to a `proxy.bypass` configuration for one
  domain.
- [microsoft/playwright#16424](https://github.com/microsoft/playwright/issues/16424), a
  component-test report of `ERR_EMPTY_RESPONSE` against `localhost` specifically on a
  second run.

**See also:** [ERR_CONNECTION_RESET in Playwright](err-connection-reset-playwright.md)
for the TCP-RST variant of a connection dying mid-request, [ERR_HTTP2_PROTOCOL_ERROR in
Playwright](err-http2-protocol-error-playwright.md) for a documented case of a server
resetting a stream deliberately as an anti-automation response, and [ERR_TUNNEL_CONNECTION_FAILED
in Playwright](err-tunnel-connection-failed-playwright.md) for the earlier failure of the
proxy tunnel itself never completing.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Silence on the wire is generated by whatever closed
the connection; the browser's identity layer has no part in it either way.*
