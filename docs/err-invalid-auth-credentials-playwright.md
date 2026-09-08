---
title: "net::ERR_INVALID_AUTH_CREDENTIALS in Playwright"
description: "net::ERR_INVALID_AUTH_CREDENTIALS means an HTTP authentication exchange was attempted and failed to establish credentials, a distinct failure from a proxy tunnel refusing a 407 or a SOCKS5 credential being silently dropped."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 38
---


# net::ERR_INVALID_AUTH_CREDENTIALS in Playwright

`net::ERR_INVALID_AUTH_CREDENTIALS` means Chromium's network stack attempted an HTTP
authentication exchange and the credentials could not be established. Chromium's own
network error list defines it in exactly those words, code -338: "Credentials could not
be established during HTTP Authentication." `page.goto()` throws it when this happens on
navigation, and it is worth being precise about what that sentence does and does not
cover, because two other, very different proxy failures get confused with it constantly.

## What this error is not

It is not [`ERR_TUNNEL_CONNECTION_FAILED`](err-tunnel-connection-failed-playwright.md),
which fires when an HTTP proxy's `CONNECT` tunnel is refused outright, commonly on a
`407` the client never successfully answers. That failure happens before any
authentication scheme has actually been negotiated; the tunnel itself never opens.

It is also not the silent SOCKS5 credential drop covered on [the SOCKS5 authentication
page](playwright-socks5-proxy-authentication.md). That failure produces no exception at
all: the page loads, from the wrong address or not the way you expected, because
Playwright's documented `proxy` credentials are specified for HTTP proxies only and a
SOCKS5 username and password are quietly dropped on the way to the connection.

`ERR_INVALID_AUTH_CREDENTIALS` sits after both of those, in the exchange itself. A
challenge was issued, a client attempted to answer it with a scheme such as Basic,
Digest, NTLM, or Negotiate, and the exchange failed to produce credentials the server or
proxy would accept. It is loud, not silent, and it is thrown after negotiation started,
not before a tunnel opened.

## The adjacent codes worth knowing, because they narrow the diagnosis

Chromium's error list places several related, more specific failures right next to this
one, and which one you actually see narrows the cause: `ERR_MISSING_AUTH_CREDENTIALS`
(-341), "No Kerberos credentials were available during HTTP Authentication," the
GSSAPI-specific case of having nothing to offer for a Negotiate challenge instead of
offering something that gets rejected; `ERR_UNSUPPORTED_AUTH_SCHEME` (-339), "An HTTP
Authentication scheme was tried which is not supported on this machine," a platform gap
rather than wrong credentials; and `ERR_MALFORMED_IDENTITY` (-329), "The identity used
for authentication is invalid," a client-side formatting problem before the server ever
gets to accept or reject anything. If your log shows one of these instead, the fix is
different: a missing local credential store for the first, a scheme the machine simply
cannot perform for the second.

## Where this actually shows up

**A proxy or an internal server negotiating NTLM or Negotiate/Kerberos rather than plain
Basic.** [A real report](https://github.com/microsoft/playwright/issues/12890) shows
`ERR_INVALID_AUTH_CREDENTIALS` against a VPN-gated internal hostname, a shape consistent
with integrated Windows authentication attempting a scheme exchange that never completes
outside the environment it expects. Chromium's own documentation on [HTTP authentication](https://www.chromium.org/developers/design-documents/http-authentication/)
lists Negotiate as able to fall back to either Kerberos or NTLM, and that negotiation has
its own failure modes independent of whether the password itself is correct.

**Playwright's documented `proxy` credentials reaching an infrastructure layer that does
not forward them the way a direct launch does.** [A real report](https://github.com/microsoft/playwright/issues/35679)
shows the identical `server`/`username`/`password` configuration working on a direct
Playwright launch and failing with this error once routed through a Selenium Grid node.

**Proxy credentials configured but never transmitted at all, which reads as this error
family even though nothing was rejected.** [A separate report](https://github.com/microsoft/playwright/issues/32567)
shows a proxy's own access log recording no `Proxy-Authorization` header at all despite
credentials being set on the `proxy` object, a "never sent" failure rather than "sent and
rejected," worth ruling out before assuming the password itself is wrong.

**Basic auth combined with a proxy in the same session.** [A related report](https://github.com/microsoft/playwright/issues/21703)
shows `http_credentials` for an origin server's Basic-auth challenge failing specifically
when a proxy is also configured; embedding the credentials directly in the URL was the
workaround that worked where the documented [`http_credentials` context option](http-basic-auth-playwright-http-credentials.md)
did not.

## Diagnostic checklist

1. **Identify which layer is challenging you: the proxy, or the destination site.** A
   proxy issuing the challenge means this is proxy authentication; the destination server
   issuing it means `http_credentials` on the context is the right tool, not the `proxy`
   option's credentials.
2. **Confirm the credentials are actually being sent**, not merely configured. Capture
   traffic or check the target's own access log for a `Proxy-Authorization` or
   `Authorization` header on the failing request, the way the report above distinguishes
   "never sent" from "sent and rejected."
3. **Test the same credentials with `curl` against the same endpoint outside Playwright.**
   `curl -v --proxy-user user:pass -x http://proxy:port https://example.com` isolates
   whether the credentials themselves are valid before touching browser configuration at
   all.
4. **Check what authentication scheme is actually being negotiated.** NTLM and
   Negotiate/Kerberos have their own environment-dependent failure modes, distinct from a
   simple wrong password on Basic or Digest; a scheme requiring domain-joined credentials
   will not succeed from a machine outside that domain no matter how correct they look.
5. **If a proxy and `http_credentials` are both configured, test with one removed at a
   time**, since a documented interaction exists between the two.

## What Firefox reports instead

`invisible_playwright` drives a patched Firefox, and Firefox's networking layer has no
single equivalent to Chromium's generic `ERR_INVALID_AUTH_CREDENTIALS`. What exists
instead, in Firefox's own error list, is narrower and split by context:
`NS_ERROR_PROXY_AUTHENTICATION_FAILED`, defined as covering the case where a `407` from a
proxy cannot be easily propagated back to the caller, and `NS_ERROR_PROXY_UNAUTHORIZED`
for the proxy's `401`-equivalent status internally. There is no distinct Firefox
constant for a failed credential exchange against the destination server the way
Chromium's code covers it; a rejected Basic or Digest exchange there generally surfaces
as a repeated challenge rather than a single named failure. If you are chasing this
failure against Firefox specifically, look for the proxy-side names above rather than
searching for a literal Chromium string that Firefox does not produce.

## The honest boundary

Credential negotiation happens inside the HTTP and proxy-authentication layers,
independent of anything a browser reports about itself to a page. `invisible_playwright`
passes the `proxy` and `http_credentials` options straight through to the patched engine;
it does not retry a failed exchange, guess at a working scheme, or make a rejected
credential accepted. A stock Playwright browser and this project's build fail an
authentication exchange identically given the identical credentials and the identical
challenge, because nothing about the fingerprint layer this project touches is involved
in whether a password is correct.

## Short answers to the questions that lead here

**What does net::ERR_INVALID_AUTH_CREDENTIALS mean?** An HTTP authentication exchange,
Basic, Digest, NTLM, or Negotiate, was attempted and failed to establish credentials the
challenger would accept. It fires after negotiation starts, not before.

**How is this different from ERR_TUNNEL_CONNECTION_FAILED?** That error means an HTTP
proxy's CONNECT tunnel was refused, often on an unanswered 407, before any authentication
scheme actually ran. This error means a scheme was attempted and its exchange failed.

**Why does my SOCKS5 proxy fail silently instead of throwing this?** Playwright's proxy
credential fields are documented for HTTP proxies only. A SOCKS5 username and password
are dropped with no exception at all, a different and quieter failure covered on the
SOCKS5 authentication page.

**Could my credentials be correct and this still happens?** Yes. A scheme mismatch, an
NTLM or Kerberos negotiation that cannot complete outside its expected environment, or an
infrastructure layer that never actually transmits the credentials you configured can all
produce this error with a genuinely correct password.

**Is proxy authentication the same option as http_credentials?** No. `proxy.username`
and `proxy.password` authenticate to the proxy itself; `http_credentials` on the context
answers a `401` challenge from the destination site. Confusing the two is a documented
source of this failure.

## Sources

- Chromium's [`net/base/net_error_list.h`](https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h),
  for `ERR_INVALID_AUTH_CREDENTIALS` (-338) and the adjacent `ERR_MISSING_AUTH_CREDENTIALS`
  (-341), `ERR_UNSUPPORTED_AUTH_SCHEME` (-339), and `ERR_MALFORMED_IDENTITY` (-329),
  retrieved 2026-08-30.
- Mozilla's [`xpcom/base/ErrorList.py`](https://searchfox.org/mozilla-central/source/xpcom/base/ErrorList.py)
  (viewed via the Fossies mirror), for `NS_ERROR_PROXY_AUTHENTICATION_FAILED` and
  `NS_ERROR_PROXY_UNAUTHORIZED`, retrieved 2026-08-30.
- Chromium's own [HTTP authentication design document](https://www.chromium.org/developers/design-documents/http-authentication/),
  for the supported schemes (Basic, Digest, NTLM, Negotiate) and how Negotiate falls back
  to Kerberos or NTLM, retrieved 2026-08-30.
- [microsoft/playwright#12890](https://github.com/microsoft/playwright/issues/12890),
  `ERR_INVALID_AUTH_CREDENTIALS` against a VPN-gated internal hostname.
- [microsoft/playwright#35679](https://github.com/microsoft/playwright/issues/35679),
  identical proxy credentials working on a direct launch and failing through a Selenium
  Grid node.
- [microsoft/playwright#32567](https://github.com/microsoft/playwright/issues/32567), a
  proxy's own access log showing no authentication header received despite credentials
  being configured.
- [microsoft/playwright#21703](https://github.com/microsoft/playwright/issues/21703),
  `http_credentials` failing specifically when a proxy is also configured on the same
  context.

**See also:** [ERR_TUNNEL_CONNECTION_FAILED in Playwright](err-tunnel-connection-failed-playwright.md)
for the earlier tunnel-refusal failure this one is most often confused with, [SOCKS5
proxy authentication in Playwright](playwright-socks5-proxy-authentication.md) for the
silent, unrelated failure on the SOCKS5 path, and [handling HTTP basic auth in
Playwright](http-basic-auth-playwright-http-credentials.md) for the `http_credentials`
option this page distinguishes from proxy credentials.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Both credential options pass straight to the engine;
a rejected exchange is the challenger's answer, not a spoof this project could add.*
