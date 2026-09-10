---
title: "How to check a proxy's status before you rely on it"
description: "Liveness, latency, exit location and protocol behaviour are four different checks, and confusing them is how a working proxy gets blamed for what is actually a browser or site problem. The order to check them in."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 40
---

# How to check a proxy's status before you rely on it

"Is my proxy working" is actually four separate questions, and treating it as
one is how people spend an hour debugging a browser fingerprint when the
proxy was down the whole time, or blame a working proxy for a site's own
block.

## The four checks, in the order to run them

**1. Is it alive at all?** Before touching a browser: `curl` through it and see
if you get a response.

```bash
curl -x socks5h://user:pass@host:port https://ifconfig.me
```

`socks5h` (not `socks5`) resolves DNS through the proxy rather than locally,
which matters for the leak question below. An HTTP proxy uses
`curl -x http://user:pass@host:port ...` instead. If this hangs or errors, stop
here - nothing downstream will work and no browser-level fix changes it.

**2. Where does it say it is exiting from?** The `ifconfig.me` response gives
you the exit IP. Check it against a geolocation lookup and against what you
expected to pay for. A proxy sold as "US residential" that resolves to a
datacenter ASN in a different country is not doing what you bought it for,
and this is worth catching before you spend time on anything else.

**3. Is it fast enough for what you are doing?** `curl -x ... -w "%{time_total}\n" -o /dev/null -s https://example.com`
gives you a round-trip number. A proxy that is technically alive but takes
eight seconds per request will make an automated flow look nothing like a
human's browsing rhythm, independent of any fingerprint concern.

**4. Does it hold up through an actual browser session, not just curl?** This
is the check people skip, and it is where a proxy that passes checks 1-3 can
still fail in practice. `curl` and a browser are different clients: a browser
opens more connections, resolves DNS differently depending on configuration,
and can leak the real address through WebRTC regardless of what the HTTP
proxy is doing correctly.
[How to check if a proxy leaks your real IP](how-to-check-proxy-ip-leak.md)
is the browser-level version of this check, and it is not optional if the
proxy's whole purpose is to hide the real address - a proxy that passes
`curl` but leaks over WebRTC has not actually done its job.

## What "proxy status" checks cannot tell you

None of the four checks above say anything about whether a *site* will let
you through. A perfectly healthy, correctly-exiting, fast proxy still gets
challenged if the browser behind it reports as automated, or if the exit
address has been used by enough other traffic to earn a bad reputation on
that specific site's own reputation database. Conflating "my proxy is up" with
"the site accepts my proxy's traffic" is a different confusion in the other
direction from the one above, and it sends people rotating proxies to fix a
browser-fingerprint problem.

## A quick reference for interpreting results

| Symptom | Likely cause | Where to look |
|---|---|---|
| curl through the proxy hangs or errors | Proxy is down or credentials are wrong | Check 1 |
| curl works, exit IP is wrong country/ASN | Wrong proxy config or provider issue | Check 2 |
| curl works, browser session times out | Browser-specific connection handling, or WebRTC leaking real address and site rejecting the mismatch | Check 4 |
| Everything above passes, site still blocks | Fingerprint, rate, or behavioural detection - not the proxy | See below |

If you have run all four checks and the proxy is confirmed healthy and
non-leaking, and you are still being challenged, the proxy has done its job
and the remaining problem is elsewhere.
[Why does my Playwright script get blocked?](why-does-my-playwright-script-get-blocked.md)
is the next page to read, and it starts from exactly this point: address
confirmed fine, now what.

## Short answers to the questions that lead here

**How do I know if my proxy is working?** Run `curl` through it against an IP
echo service before touching a browser. If that fails, nothing above it will
work either.

**Why does my proxy work in curl but not in my browser?** Different clients
handle DNS and WebRTC differently. A working `curl` test does not rule out a
WebRTC leak or a browser-specific connection issue.

**How often should I check a proxy's status?** Before every automated session
that depends on it mattering, if the proxy is shared or rotating - a proxy
that worked an hour ago can have changed reputation or gone down since.

**Does a slow proxy get detected more?** Indirectly: unusually slow, uniform
latency is itself a signal in aggregate behavioural analysis, separate from
whatever the proxy's address reputation is.

**Is a datacenter proxy always worse than residential?** Not universally - it
depends entirely on whether the specific site treats datacenter ranges
differently, which varies by site.

**See also:**
[How to check if a proxy leaks your real IP](how-to-check-proxy-ip-leak.md),
[Setting a proxy per Playwright context](set-geolocation-permissions-per-playwright-context.md),
and [WebRTC leak with a proxy](webrtc-leak-proxy.md).

## Sources

- [Playwright's documented proxy configuration](https://playwright.dev/python/docs/network#http-proxy), retrieved 2026-09-10, for how proxy credentials and per-context proxies are set.
- This project's own release gates, including the positive-form WebRTC check referenced in check 4, which replaced an earlier negative-only assertion that a fully blocked WebRTC connection could pass by mistake.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The table above
exists because "the proxy isn't working" and "the site is blocking me" get
treated as the same sentence far more often than they are the same problem.*
