---
title: "Does a proxy leak DNS? DoH and DNS leaks explained"
description: "How a SOCKS5 proxy can leak DNS when the host resolves names locally, where DNS-over-HTTPS fits, and how to verify that names exit where the connection does."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 21
---


# Does a proxy leak DNS? DoH and DNS leaks explained

A proxy moves your connection to a different exit. It does not automatically move the
step that happens before the connection: turning `example.com` into an IP address. If
that lookup is done by your own machine while the connection leaves through the proxy,
you have a browser whose traffic exits in one country and whose DNS queries exit from
your real network. That split is a DNS leak, and it is one of the most common ways a
proxied session still tells a consistent story about where you actually are.

This page explains where name resolution happens, the difference between remote and
local resolution over SOCKS5, where DNS-over-HTTPS fits, how to check which one you are
doing, and the one thing fixing the leak does not fix.

## Where DNS resolution actually happens

DNS resolution happens in one of two places: on your own host, using whatever resolver
your operating system is configured to use, or at the proxy exit, where the proxy does
the lookup on your behalf. Every connection to a hostname is really two steps: first a
name is resolved to an address, then a socket is opened to that address. People think of
a proxy as covering "the connection", but there are two connections in that sentence, and
a proxy does not necessarily cover both.

In detail, the resolver step can run in one of two places:

- **On your host**, using whatever DNS server your operating system is configured to
  use. That is usually your ISP's resolver, or one handed to you by your local network.
- **At the proxy exit**, where the proxy itself does the lookup on your behalf and only
  the connection details come back to you.

When the host resolves, the lookup packets leave from your real network before the proxy
is ever involved. A passive observer on your network, and the resolver you queried, both
see that you asked for `example.com` at a given second. Moments later the destination
site sees a connection arrive from the proxy's country. Correlating those two is not
hard, and it undoes the reason you added the proxy.

## Remote vs local resolution over SOCKS5

SOCKS5, defined in [RFC 1928](https://datatracker.ietf.org/doc/html/rfc1928), is the
scheme where this actually has a choice, which is why it comes up here and not with plain
HTTP proxying. The protocol lets a client send the proxy either an **address** it has
already resolved, or a **hostname** for the proxy to resolve itself.

- **Local (host-side) resolution.** The client resolves `example.com` to an IP using the
  host's resolver, then tells the proxy "connect to this IP". The lookup left your
  network. This is the leak.
- **Remote (proxy-side) resolution.** The client tells the proxy "connect to this
  hostname", and the proxy resolves it at the exit. The lookup and the connection now
  leave from the same place.

The trap is that the leaking version is often the default in naive setups. A lot of
tooling resolves the hostname first out of habit and only then hands an address to the
proxy, so the connection is proxied and the DNS is not. Nothing errors. The page loads.
The exit IP looks right in a quick check. The lookup quietly went out your front door.
This is the same class of silent-default problem as
[SOCKS5 authentication that falls back to an unproxied connection](playwright-socks5-proxy-authentication.md)
instead of failing loudly. The difference between the two schemes, and why SOCKS5 is the
one that carries hostnames at all, is covered in
[SOCKS5 vs HTTP proxy for a browser](socks5-vs-http-proxy-browser.md).

invisible_playwright resolves DNS through the proxy by default, so names and connections
exit from the same place. There is no separate switch to remember: pass a SOCKS5 proxy
and the hostname goes to the exit, not to your local resolver.

## Where DNS-over-HTTPS fits

DNS-over-HTTPS (DoH), standardized in
[RFC 8484](https://datatracker.ietf.org/doc/html/rfc8484), encrypts the resolver query
and sends it over HTTPS to a chosen resolver. It is a related but different fix from
proxy-side resolution, and the two solve different problems.

Its purpose is confidentiality: your local network and your ISP can no longer read which
hostnames you are looking up, because the query looks like ordinary HTTPS traffic to some
server.

What DoH does **not** do on its own is change *where* the query exits. If the browser's
DoH request itself goes out your real network rather than through the proxy, the encrypted
lookup still originates from your real IP. The resolver you picked now knows the name, and
a determined observer still sees a lookup-shaped request leaving your network at the same
moment a proxied connection opens elsewhere. Encryption hides the *content* of the leak,
not the *fact* of it.

So DoH and proxy-side resolution answer different questions:

- **Proxy-side resolution** decides *where the lookup exits*. It closes the geographic
  split that this whole page is about.
- **DoH** decides *whether the lookup is readable in transit*. It is about privacy from
  the network path, not about exit location.

For a proxied automation session, the property you want is that the name resolves at the
exit. When resolution happens at the proxy, the exit's own resolver handles the name, and
the "which resolver, encrypted or not" question moves to the exit's side of the world,
where it belongs. Firefox exposes DoH through standard `network.trr.*` about:config
preferences if you want the encrypted-in-transit property on top, but it is not a
substitute for resolving at the exit.

## Launch invisible_playwright and resolve names at the exit

The whole point of the product here is that the correct behaviour is the default. You pass
a SOCKS5 proxy the ordinary way and the hostname is resolved at the exit, alongside the
connection.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# seed=42 makes the whole identity reproducible run to run
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

The `browser` object is a real Playwright `Browser`, so every method you already use works
unchanged. The only thing that differs from stock Playwright here is that the name in
`page.goto(...)` is resolved through the proxy rather than by your host, so the DNS query
and the HTTP connection both leave from `gate.example.com`'s exit and not from your
network. Async is the same, from `invisible_playwright.async_api`.

If you drive `firefox.launch()` yourself instead of using this class, the same proxy dict
has to reach the launch through `get_default_stealth_prefs(proxy=...)` so the SOCKS
preferences that route DNS are actually written. Skipping that is how a hand-rolled setup
ends up proxying the connection and leaking the lookup.

## How to verify names exit where the traffic does

Do not assume. The failure mode of a DNS leak is that everything looks fine, so the check
has to be positive: confirm the lookup exit, do not just confirm the absence of an error.

A minimal, honest check has two halves that must agree:

```bash
# 1. What does the world see your CONNECTION exit as?
#    Run this THROUGH the browser session, not with a bare curl on the host,
#    or you have measured the path you do not use.
```

Inside the session, read the exit the destination actually sees:

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/ip")   # any endpoint that echoes the caller IP
    print("connection exit:", page.inner_text("body"))
```

Then compare that against where your **DNS** appears to resolve from, using a resolver-echo
service that reports the address of the machine that queried it. If the connection exit and
the resolver-side address are in the same place, resolution is happening at the exit. If the
resolver address is your home ISP while the connection exit is the proxy's country, you have
found the leak. This is the same "measure the real path, through the proxy, not on localhost"
discipline described in [how to check a proxy for an IP leak](how-to-check-proxy-ip-leak.md),
and it belongs next to the WebRTC and timezone checks, because all three fail in the same
quiet way: a value that is individually plausible and disagrees with another value.

While you are checking exits, check the [timezone against the exit IP](timezone-proxy-mismatch.md)
too. A DNS leak and a timezone mismatch are the same category of tell: two surfaces that
should point at one location and do not.

## The honest caveat: this fixes the leak, not the reputation

Resolving names at the exit closes a specific, real gap: it stops your DNS from pointing
at your real network while your connection points somewhere else. That is worth doing, and
it is the default here for that reason.

It does not make the exit IP good. If the proxy's address is a known datacenter range, or
is already on shared blocklists, or is a country that does not match the rest of your
session, a perfectly consistent DNS path does not help. The name and the connection now
exit from the *same* place, but that place can still have a bad reputation. What decides
whether a datacenter exit is recognised as one is a separate question, covered in
[can websites detect a datacenter proxy IP](can-websites-detect-a-datacenter-proxy-ip.md).

The honest framing for the whole product is the same here as everywhere: invisible_playwright
is built to look like a real Firefox driven by a real person, which is why the fingerprint,
the TLS handshake and the driver layer read as genuine, and why it passes most detection
checks that inspect the browser. It does not supply a clean IP, per-account quotas, rate
limits, or human timing. You bring a reputable exit and sane pacing; the browser brings the
part that has to look real. A closed DNS leak is one clean surface among several, not a
finish line.

## Conclusion

A proxy can leak DNS whenever the host resolves names locally while the connection exits
elsewhere. Over SOCKS5 the fix is to hand the proxy a hostname to resolve at the exit rather
than an address you resolved at home, so names and connections leave from the same place.
DoH is a companion, not a replacement: it encrypts the lookup in transit but does not by
itself move where the lookup exits. Verify positively, comparing the connection exit against
the resolver-side address, and remember that closing the leak fixes location consistency,
not the reputation of the address you exit from.

## Short answers to the questions that lead here

**Does using a proxy hide my DNS?** Only if the proxy does the name resolution. If your
host resolves the name and then hands the proxy an IP, the lookup left your real network
and the connection did not, which is a DNS leak.

**What is the difference between remote and local DNS resolution?** Local means your machine
resolves the hostname before contacting the proxy; remote means the proxy resolves it at the
exit. SOCKS5 supports both, and only remote resolution keeps names and connections exiting
together.

**Does DNS-over-HTTPS stop a DNS leak?** Not by itself. DoH encrypts the query so the path
cannot read it, but if the encrypted query still leaves from your real network the leak of
location is unchanged. It answers "is my lookup readable", not "where does my lookup exit".

**Does invisible_playwright leak DNS?** No. It resolves DNS through the proxy by default, so
the name and the connection exit from the same place. You just pass a SOCKS5 proxy the normal
way.

**How do I check for a DNS leak?** Positively, through the browser session and not on
localhost. Compare the exit IP the destination sees against the address a resolver-echo
service reports; if they disagree in location, you have a leak.

**If my DNS no longer leaks, am I safe?** It fixes location consistency, not IP reputation.
A datacenter or blocklisted exit is still a datacenter or blocklisted exit after the leak is
closed.

## Sources

- The SOCKS5 protocol's two connect modes (address versus hostname), defined in
  [RFC 1928](https://datatracker.ietf.org/doc/html/rfc1928), which is the mechanism that
  makes proxy-side resolution possible at all.
- DNS Queries over HTTPS (DoH), standardized in
  [RFC 8484](https://datatracker.ietf.org/doc/html/rfc8484), which encrypts the resolver
  query in transit without changing where it exits.
- This project's proxy handling, which routes DNS through the proxy by default rather than
  through the host resolver, and refuses a proxy endpoint given without an explicit port
  rather than launching unproxied.
- The release gates behind the network pages in this set, including the exit-consistency and
  WebRTC checks, which all assert a present, correct signal rather than the absence of a
  wrong one.

**See also:** [how to check a proxy for an IP leak](how-to-check-proxy-ip-leak.md),
[SOCKS5 vs HTTP proxy for a browser](socks5-vs-http-proxy-browser.md), and
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The default that keeps DNS and
the connection exiting together is the whole point of this page; the reputation of the exit is
still yours to bring.*
