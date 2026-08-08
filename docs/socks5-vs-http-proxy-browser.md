---
title: "SOCKS5 vs HTTP proxy: what each does in the browser"
description: "SOCKS5 vs HTTP proxy in the browser: who authenticates and where. SOCKS auth and DNS run in the patched Firefox engine; HTTP auth runs in the Playwright driver."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 10
---


# SOCKS5 vs HTTP proxy: what each does in the browser

**The practical difference between a SOCKS5 and an HTTP proxy in the browser is who
authenticates and where, not speed.** With a `socks5://` URL the patched Firefox
engine opens the connection, speaks the authentication, and resolves DNS at the exit.
With an `http://` URL the Playwright driver authenticates one layer up, and the
engine's proxy settings are never touched.

Most comparisons of these two proxy types argue about speed or overhead. That is
the least interesting difference and almost never the one that decides whether your
automation works. The difference that matters is structural: for one of them the
patched browser engine makes the connection and authenticates itself, and for the
other the authentication happens one layer up, in the driver, before the engine is
involved at all.

This page is about that split. It is written for Playwright, it uses a real API, and
the behaviour described is what the shipped build actually does when you hand it each
kind of proxy URL.

## SOCKS5 vs HTTP proxy at a glance

Every row below is a property of the code path the URL scheme selects, not a tuning
option you set separately. The scheme is the only switch.

| Property | SOCKS proxy (`socks5://`, `socks4://`, `socks://`) | HTTP/HTTPS proxy (`http://`, `https://`) |
|---|---|---|
| Where auth happens | Connection layer, inside the SOCKS handshake | HTTP Basic auth, a `Proxy-Authorization` header |
| Who holds the credentials | The patched browser engine | The Playwright driver |
| Engine proxy prefs | Set directly (`network.proxy.type = 1`, host, port, version) | Left untouched |
| DNS resolution | Forced at the exit (`network.proxy.socks_remote_dns = true`) | Depends on the setup |
| Passed to Playwright | No proxy (the engine does the whole job) | The proxy dictionary, unchanged |
| Needed a source patch | Yes, to speak authenticated SOCKS5 | No, Basic auth was already handled |

## The one distinction that actually changes behaviour

Both proxy types move your traffic through an exit. The question that separates them
is: **who speaks the authentication handshake, and over which protocol?**

- A **SOCKS proxy** (`socks5://`, `socks4://`, `socks://`) authenticates at the
  connection layer, before any HTTP is spoken. The username and password are part of
  the SOCKS handshake itself.
- An **HTTP/HTTPS proxy** (`http://`, `https://`) authenticates with HTTP Basic auth, a
  [`Proxy-Authorization`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Proxy-Authorization)
  header the client sends after it opens the connection.

Those are different mechanisms living at different layers, and in this tool they are
handled by two different components. The scheme in the URL is the switch. Nothing
else you configure changes which path runs.

## What the wrapper does with each scheme

When you pass a proxy, the wrapper looks at the URL scheme and routes it one of two
ways.

For a `socks5://`, `socks4://` or `socks://` URL, the settings are written into the
patched engine directly: the proxy type is set to a manual SOCKS proxy
(`network.proxy.type = 1`), the host, port and SOCKS version are filled in, DNS is
forced to resolve at the exit rather than locally
(`network.proxy.socks_remote_dns = true`), and the credentials are handed to the
browser to speak as part of the SOCKS handshake. Playwright itself is then told there
is **no** proxy, because the engine is doing the whole job.

For an `http://` or `https://` URL, the opposite happens: the proxy dictionary is
passed through to Playwright unchanged, and Playwright negotiates HTTP Basic auth on
its own. That path never touches the engine's proxy preferences.

So the two schemes do not just differ in wire protocol. They differ in which piece of
software is holding the credentials. SOCKS traffic is authenticated by the browser
you are trying to make look real; HTTP proxy traffic is authenticated by the
automation driver in front of it.

## Why SOCKS5 authentication needed a source patch

SOCKS5 authentication needed a source patch because stock Firefox only speaks SOCKS5
**without** credentials: given a SOCKS5 proxy that requires a username and password, an
unmodified engine never performs the authenticated handshake, so any exit that demands
auth refuses the connection. Most commercial residential and mobile exits demand auth,
so on a stock engine, authenticated SOCKS5, which is the common case, simply did not
work.

This build carries a C++ patch to the proxy service so the engine reads a SOCKS
username and password and includes them in the SOCKS5 handshake. That is the only
reason returning control to the engine is a valid strategy for SOCKS at all: without
the patch there would be nothing on the engine side to authenticate with, and the
credentials would have to be smuggled in some other way. With the patch, an
authenticated `socks5://user:pass@exit:1080` is a first-class citizen, handled the
same way an unauthenticated one is.

HTTP proxies never needed this, because HTTP Basic proxy auth was already part of
what the driver layer handles. That asymmetry is the whole reason the two schemes take
two different code paths. For the SOCKS side specifically, see
[SOCKS5 proxy authentication in Playwright](playwright-socks5-proxy-authentication.md),
which covers the URL format and the failure you get when auth is silently dropped.

## Using each one, and confirming which path ran

The API is the same for both; only the scheme changes. The `browser` object is a real
Playwright `Browser`, so every method you already use works unchanged.

SOCKS5, authenticated:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # DNS for example.com was resolved at the exit, not locally,
    # because socks_remote_dns is forced on for SOCKS proxies.
```

HTTP, authenticated:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "http://gate.example.com:8080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # Here Playwright negotiated HTTP Basic proxy auth itself;
    # the engine's SOCKS preferences were never set.
```

Do not assume the proxy took effect. Confirm the exit address from inside the browser,
because a test that passes on localhost has measured the path you do not use:

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    # any endpoint that echoes the caller's IP; replace with your own
    page.goto("https://example.com/ip")
    print(page.inner_text("body"))
```

A quick measurement worth doing once, so the abstract distinction becomes concrete:
drive the same exit through both a `socks5://` URL and an `http://` URL from the same
credentials, and read the exit IP inside the page each time. The address is identical,
as it must be, because it is the same exit. What differs is not visible in that
number: with SOCKS the resolution of `example.com` happened at the exit and the auth
was spoken by the engine, and with HTTP the driver authenticated to the proxy first.
The exit IP being equal is the point. It tells you both paths reach the same place;
which layer authenticated is a property of your setup, not of the exit.

## Which one to choose

For browser automation that has to look like a real user, prefer **SOCKS5** when the
exit offers it. Two reasons, both structural rather than about speed:

- The connection and its authentication are handled by the engine you spent effort
  making realistic, in one place, at the layer below HTTP. There is no second party
  inserting proxy headers into the conversation.
- Remote DNS is forced on, so hostnames resolve at the exit. A local resolution is a
  classic story mismatch: the browser claims to be somewhere the DNS lookup says it is
  not. See [when the timezone does not match the proxy](timezone-proxy-mismatch.md) for
  the general shape of that failure and everything else that has to agree.

Reach for an **HTTP/HTTPS proxy** when that is all the exit gives you, or when you
specifically want the driver-level path, for example because a piece of tooling around
you already speaks HTTP proxying. It works, it authenticates, and for most sites it is
indistinguishable at the destination. Just know that you are on a different code path,
and that path does not exercise the engine's proxy handling at all.

Whichever you pick, the exit reputation and the WebRTC surface matter more than the
scheme. A [WebRTC path that leaks or comes back empty through a proxy](webrtc-leak-proxy.md)
will undo a perfectly authenticated tunnel, and it fails in both directions.

## Conclusion

SOCKS5 versus HTTP is not a speed contest. It is a question of who authenticates and
where. In this build a SOCKS URL is driven entirely by the patched engine, credentials
and DNS included, which is only possible because the engine was patched to speak
authenticated SOCKS5 in the first place; an HTTP URL is handled by the Playwright
driver one layer up and never touches those engine preferences. Pick SOCKS when you
can, confirm the exit from inside the browser rather than assuming it, and remember
that the scheme decides the mechanism, not just the wire format.

## Short answers to the questions that lead here

**Is SOCKS5 more anonymous than an HTTP proxy?** Not inherently. Both send your
traffic through an exit. The practical difference here is that SOCKS auth and DNS are
handled by the browser engine itself, while HTTP proxy auth is handled by the driver
above it.

**Does authenticated SOCKS5 work in Firefox?** In stock Firefox, no: it supports
SOCKS5 only without credentials. This build carries a source patch so the engine reads
a SOCKS username and password and includes them in the handshake.

**Why does my SOCKS5 proxy with a password fail in a normal browser?** Because
unmodified Firefox performs the SOCKS5 handshake without credentials, so any exit that
requires auth refuses the connection.

**Will the two schemes give me a different exit IP?** No. Through the same exit the IP
is the same. What differs is which layer authenticated and where DNS resolved, not the
address the site sees.

**Do I need to set anything besides the URL to switch between them?** No. The scheme in
the proxy URL is the only switch. A `socks5://` URL takes the engine path, an
`http://` URL takes the driver path, automatically.

**Does an HTTP proxy leak DNS?** It depends on the setup, but for the SOCKS path DNS is
forced to resolve at the exit. Confirm your real exit and resolver from inside the
browser rather than trusting the scheme alone.

## Sources

- This project's proxy dispatch, which routes a proxy by its URL scheme to either the
  patched engine or the Playwright driver, read from its own implementation.
- The engine's SOCKS proxy handling, patched so an authenticated SOCKS5 handshake is
  performed with a username and password, described in this project's patch notes.
- Firefox's own proxy preferences (`network.proxy.type`, `network.proxy.socks_remote_dns`),
  which are standard `about:config` settings.
- The SOCKS protocol itself: SOCKS version 5 is defined in
  [RFC 1928](https://www.rfc-editor.org/rfc/rfc1928), and its username/password
  authentication method, the one the patch performs, is defined separately in
  [RFC 1929](https://www.rfc-editor.org/rfc/rfc1929).
- The HTTP side's `Proxy-Authorization` header and the Basic authentication scheme it
  carries are documented on
  [MDN's Proxy-Authorization reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Proxy-Authorization).

**See also:** [SOCKS5 proxy authentication in Playwright](playwright-socks5-proxy-authentication.md),
[rotating proxies across runs](how-to-rotate-proxies-playwright.md), and
[a different proxy per browser context](playwright-proxy-per-context.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The SOCKS5
authentication described here exists because the stock engine did not do it, and the
patch is the reason the scheme can be handed back to the engine at all.*
