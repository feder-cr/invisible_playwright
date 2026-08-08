---
title: "IPv6 vs IPv4: which does your proxy expose?"
description: "On dual-stack hosts, browsers reach sites over IPv6 while SOCKS proxies carry IPv4, so sites log your machine's address at the transport layer, not the proxy."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 25
---


# IPv6 vs IPv4: which does your proxy expose?

You set a proxy, an IP-echo page shows the proxy's IPv4, and you assume every request
exits there. On a machine that has working IPv6 that assumption has a hole in it, and
the hole is not in JavaScript or in WebRTC. It is one layer lower, in which network
your browser actually opened the TCP connection over.

A SOCKS proxy carries one address family per connection. If the proxy is reached over
IPv4 and a connection to a site slips out of the tunnel, a dual-stack host will make
that direct connection over IPv6 by preference, and the site logs your real global
IPv6 address. This is the same failure family as the
[WebRTC IPv6 leak](webrtc-ipv6-leak-proxy.md), but at the transport layer rather than
through ICE candidates, so a WebRTC-only check will never see it. This page is why it
happens, how to see it in your own setup, how to force the browser onto one family,
and what pinning the family does and does not buy you.

## Two answers to "which IP", and only one is on your screen

"Which IP does the site see" has two answers that usually agree and occasionally do
not.

- The **application answer**: what a page reads back when it asks an echo endpoint for
  your address. This is the one every leak tutorial checks, and behind a working proxy
  it shows the proxy.
- The **transport answer**: the source address of the actual TCP connection the socket
  opened. This is the one the site's own access log records, before any page runs.

When every connection rides the proxy, both answers are the proxy and there is nothing
to discuss. The gap opens when a connection is made outside the tunnel: the transport
answer becomes your host's address while the application answer, read from some other
already-proxied request, can still say the proxy. You are then looking at a screen that
says "proxy" while a log somewhere says "you".

## Why a dual-stack host reaches for IPv6 first

A host with both a routable IPv4 and a global IPv6 address is called dual-stack.
Modern connection logic on such a host does not pick randomly. It follows an
address-selection order (the "happy eyeballs" behaviour,
[RFC 8305](https://datatracker.ietf.org/doc/html/rfc8305)) that prefers IPv6
and only falls back to IPv4 when the v6 path fails or is slow.

That preference is exactly what you want on a normal machine and exactly what works
against you here. The moment any connection is allowed to go direct, the browser's
first choice is your global IPv6 address, which is routable from anywhere, stable, and
unique to the machine. It is not a private LAN address that needs masking. It is a
public identifier of the host, and it went out on a transport your IPv4 proxy was never
part of.

## How an IPv6 connection escapes an IPv4-only proxy

A correctly configured SOCKS5 proxy tunnels every TCP connection, so in the normal case
nothing escapes. The escapes are specific and each one is a configuration seam rather
than a mystery.

- **A proxy-bypass entry.** No-proxy lists (for `localhost`, a LAN range, or a named
  host) tell the browser to connect directly for anything that matches. A direct
  connection on a dual-stack host takes IPv6 first. If a target ever matches a bypass
  rule you forgot was there, that request leaves on your real address.
- **Local DNS that returns an AAAA record.** With remote DNS turned off, the host
  resolves names itself. A dual-stack site answers with an AAAA (IPv6) record, and the
  browser tries to open an IPv6 socket to it. An IPv4-only proxy has no v6 destination
  to hand off to, so the stack can fall back to a direct connection. This is why the
  public pref `network.proxy.socks_remote_dns` matters: with remote DNS on, the browser
  hands the proxy a hostname and never resolves an AAAA locally to chase.
- **Driving the raw browser without the proxy prefs.** If you launch Firefox yourself
  and skip the SOCKS preferences, only part of the traffic is proxied and the rest is
  direct, over IPv6 by preference.

The common thread: the proxy protects the connections that go through it, and an IPv6
leak is a connection that did not. None of these is visible to an in-page IP check that
happens to have ridden the tunnel, which is what makes the transport answer worth
measuring on its own.

## How this differs from, and compounds with, the WebRTC IPv6 leak

The [WebRTC IPv6 leak](webrtc-ipv6-leak-proxy.md) and this one rhyme, and telling them
apart is the point.

- **WebRTC IPv6** is not a connection at all. WebRTC enumerates the host's network
  interfaces locally and emits your global IPv6 as an ICE *candidate*. Nothing was
  dialed; the address was read off the interface list and printed to the page. A SOCKS
  proxy cannot stop it because there is no TCP to tunnel, and the pref
  `media.peerconnection.ice.disableIPv6` no longer reaches the code that emits it.
- **Transport IPv6** is a real connection. A socket opened over IPv6 outside the proxy,
  logged by the site as an ordinary request from your machine.

They come from different layers, they leak on different signals, and they need
different fixes. They also compound: a host with global IPv6 is the precondition for
both. Fix the interface enumeration and the transport can still leak; fix the transport
and WebRTC can still enumerate. A single global IPv6 address on the machine feeds two
independent channels, which is why a real check reads
[every surface, not just WebRTC](how-to-check-proxy-ip-leak.md).

## Force the browser onto the proxy's address family

The cleanest fix is to remove the choice. If your proxy carries IPv4, deny the browser
IPv6 for outbound name resolution so no connection can prefer a v6 path in the first
place.

- **Route DNS through the proxy.** invisible_playwright does this by default: DNS is
  resolved at the proxy, not locally, so the browser never gets a local AAAA answer to
  chase onto a direct IPv6 socket. That default closes the most common seam without you
  touching anything.
- **Disable AAAA resolution at the browser.** The public Firefox pref
  `network.dns.disableIPv6` set to `true` makes Firefox stop doing IPv6 name lookups
  host-wide, so every connection is IPv4 and matches an IPv4-only proxy. Note this is a
  *different* pref from the WebRTC one above and it actually takes effect on the
  transport path. If you drive `firefox.launch()` yourself, set it in
  `firefox_user_prefs` alongside the SOCKS preferences from
  `get_default_stealth_prefs(proxy=...)`.
- **Or match the proxy to the host.** The other honest answer is a dual-stack proxy: if
  the exit carries IPv6 too, an IPv6 connection exits at the proxy and there is nothing
  to leak. Forcing IPv4 is the fix when the proxy is v4-only; a v6-capable exit is the
  fix when you would rather keep IPv6.

Pick removal of the choice (force v4) or coverage of both (dual-stack exit). What you
do not want is the middle state: an IPv6-capable host and a v4-only proxy with nothing
holding the browser to v4.

## Measure which family your proxy actually exposes

Do not assume, read it. The launch is a two-line change from stock Playwright, and
every method after it is standard Playwright. Install it first:

```bash
pip install invisible-playwright
```

Then ask an IP-echo endpoint, from inside the proxied browser, which address it saw,
and check the family:

```python
from invisible_playwright import InvisiblePlaywright

PROXY = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
PROXY_EXIT = "203.0.113.7"   # the IPv4 you expect the proxy to present

with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
    page = browser.new_page()
    page.goto("https://example.com")   # a real remote page, through the proxy

    # The transport answer: what address the endpoint actually logged for us.
    seen = page.evaluate(
        "() => fetch('https://example.com/ip').then(r => r.text())"  # your own IP echo
    ).strip()

    family = "IPv6" if ":" in seen else "IPv4"
    print("address the site logged:", seen, "(" + family + ")")

    # A colon on a v4-only proxy means the connection left the tunnel on the
    # host's own IPv6. Equal-to-proxy IPv4 is the state you want.
    assert seen == PROXY_EXIT, f"LEAK: site saw {seen} ({family}), not the proxy exit"
    print("confirmed: every request family matches the proxy")
```

`seed=42` makes the run reproducible, so a leaking run can be replayed exactly rather
than guessed at. Run it more than once and from the machine that runs production, not
your laptop, because a home network and a datacenter host have different IPv6 stories.
If the address comes back with a colon and it is not your proxy's own IPv6, a
connection escaped, and the previous section is how you close it. For the full
multi-surface version that also confirms WebRTC, DNS and timezone, see
[how to check if a proxy leaks your real IP](how-to-check-proxy-ip-leak.md).

## What pinning the address family fixes, and what it does not

Forcing the family fixes one specific thing: it stops your real IPv6 from being the
transport answer while you were watching the application answer. That is worth doing,
and it is invisible to the checks most people run.

It is worth being just as clear about what it does not do. invisible_playwright is
built to look like a real Firefox driven by a real person, which is why the
fingerprint, the TLS handshake and the driver layer read as genuine and pass most
detection checks on their own. None of that, and none of the IPv6 handling on this
page, changes the *reputation* of the address you do exit on. A proxy whose IP is
already on a blocklist, an exit shared by a thousand other clients this minute, a
per-account quota, a rate limit, or a request cadence no human produces will all fail a
session whose address family is perfectly pinned. Those you supply: a clean exit and
human pacing. This page keeps the address honest; it does not make a bad address good.
Which family your proxy exposes is one seam among several, and it sits inside
[the wider question of why a clean fingerprint can still be blocked](how-to-test-bot-detection.md).

## Conclusion

The address on your screen is the application answer. The address in the site's log is
the transport answer, and on a dual-stack host with an IPv4-only proxy the two can
disagree, because a connection that escaped the tunnel takes IPv6 by preference and
carries your real global address. It is the WebRTC IPv6 leak's cousin one layer down,
and a WebRTC check cannot see it. Route DNS through the proxy, pin the browser to the
proxy's family or use a dual-stack exit, and then measure the transport answer instead
of trusting the one the page hands you. Do that and "the proxy's IP" starts meaning the
address every connection actually used.

## Short answers to the questions that lead here

**Does a proxy make all my traffic use one IP?** Only the connections that go through
it. On a dual-stack host, a connection that escapes the tunnel (a bypass rule, a local
AAAA lookup, an unproxied launch) goes direct over IPv6 and carries your real address.

**My IP-echo page shows the proxy. Am I safe?** Not necessarily. That is the
application answer, read from a request that rode the tunnel. The site's access log
records the transport answer, which can be a different family if another connection
went direct.

**How do I force IPv4 through the proxy?** Route DNS through the proxy so no AAAA is
resolved locally (the default here), and set the public pref
`network.dns.disableIPv6` to `true` so Firefox stops doing IPv6 name lookups entirely.

**Is this the same as the WebRTC IPv6 leak?** Same address, different layer. WebRTC
reads your IPv6 off the interface list without opening a connection; this is a real TCP
connection made outside the proxy. Fixing one does not fix the other.

**Does `media.peerconnection.ice.disableIPv6` help here?** No. That pref is about
WebRTC candidate gathering and no longer reaches even that path. For transport, the
pref that takes effect is `network.dns.disableIPv6`.

**If I pin the family, will the site stop blocking me?** Not on its own. It keeps your
real IPv6 out of the log, but it does nothing for IP reputation, shared exits, quotas,
rate limits or behaviour. Those need a clean proxy and human pacing, which you supply.

## Sources

- This project's proxy handling, which resolves DNS at the proxy by default so a local
  AAAA record cannot steer a connection onto the host's IPv6, and the transport-answer
  measurement above, read from inside the proxied browser rather than assumed.
- A read of standard Firefox proxy and DNS preferences (`network.dns.disableIPv6`,
  `network.proxy.socks_remote_dns`) and the
  [RFC 8305](https://datatracker.ietf.org/doc/html/rfc8305) address-selection behaviour
  that makes a dual-stack host prefer IPv6, distinct from the WebRTC ICE path.

**See also:** [why a proxy does not stop a WebRTC IPv6 leak](webrtc-ipv6-leak-proxy.md)
for the interface-enumeration cousin of this leak,
[how to check if a proxy leaks your real IP](how-to-check-proxy-ip-leak.md) for the
positive-form multi-surface version, and
[SOCKS5 versus HTTP proxy](socks5-vs-http-proxy-browser.md) for which schemes carry DNS
through the tunnel in the first place.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The transport-answer
check on this page exists because "the page shows the proxy" and "the log shows the
proxy" are not the same sentence on a dual-stack host.*
