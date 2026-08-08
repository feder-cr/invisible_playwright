---
title: "Playwright SOCKS5 proxy with authentication"
description: "Playwright documents SOCKS5 proxy credentials for HTTP proxies only. Why a username and password on a socks5:// server fails silently, and the routes that work."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 4
---


# Playwright SOCKS5 proxy with authentication

The short answer is that Playwright's `proxy` option takes a `socks5://` server and a
username and password, and the credentials are documented for HTTP proxies only. Most
guides tell you to pass all four fields together and stop there. This page is the
evidence, what happens when you do it anyway, and the routes that work.

## What Playwright's own documentation says

Two statements from the same [API reference](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch), and they are not about the same thing.

On the `server` field:

> Proxy to be used for all requests. HTTP and SOCKS proxies are supported, for example
> `http://myproxy.com:3128` or `socks5://myproxy.com:3128`.

On the credentials:

> `username` ... Optional username to use if **HTTP proxy** requires authentication.
>
> `password` ... Optional password to use if **HTTP proxy** requires authentication.

So the transport list and the authentication list are different lists. SOCKS is in the
first and not in the second.

The corroborating evidence is upstream:
[microsoft/playwright#10567, "Support socks5 proxy with authentication"](https://github.com/microsoft/playwright/issues/10567),
opened in November 2021 and **still open**, with 49 thumbs-up and the label
`P3-collecting-feedback`. A feature request that has been open for five years is not a
feature that quietly works.

Checked 2026-07-27, against Playwright's [`docs/src/api/params.md`](https://github.com/microsoft/playwright/blob/main/docs/src/api/params.md) and the live issue.

## What happens when you pass them anyway

Passing SOCKS5 credentials to Playwright fails silently. Nothing raises, the browser
starts, and the username and password are dropped on the SOCKS path, so the failure
surfaces later as requests that never arrive or arrive from the wrong address.

```python
browser = p.chromium.launch(proxy={
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
})
```

That silent acceptance is the whole problem, and it is why the wrong advice spreads: the
API accepts the arguments, the browser starts, and the failure shows up later as
requests that do not arrive, or that arrive from the wrong address, depending on how
the proxy reacts to an unauthenticated handshake.

If you are debugging this, do not test with a page load. Test by checking the exit
address the proxy reports, and compare it against your own. A silent fall-through to
the direct connection looks exactly like success until you look at the IP.

## The routes that do work

**Run a local relay.** The most common answer in practice. A small local proxy listens
without authentication, and authenticates upstream on your behalf. Playwright then
points at `socks5://127.0.0.1:<port>` with no credentials, which is a configuration it
does support. It costs you a process to supervise and a port, and it works on every
engine.

**Use an HTTP proxy instead**, if your provider offers one. The credentials are
supported there, which is what the documentation says; the
[trade-offs between a SOCKS5 and an HTTP proxy in the browser](socks5-vs-http-proxy-browser.md)
are their own topic.

**Configure the browser directly**, if the browser is Firefox. This is the route this
project takes, and it is worth explaining because the mechanism is not obvious.

| Route | Works on | What it costs | Carries SOCKS5 credentials |
|---|---|---|---|
| Local relay that authenticates upstream | Every engine | A process and a port to supervise | Yes, at the relay |
| HTTP proxy instead of SOCKS5 | Every engine | Needs an HTTP endpoint from your provider | Yes, on the documented path |
| Firefox preferences directly | Firefox only | A patched proxy service (this project) | Yes, via the two added prefs |

## Doing it in Firefox preferences

Firefox has its own proxy configuration, independent of whatever the automation layer
passes, and it can be set through `firefox_user_prefs` at launch:

```python
prefs = {
    "network.proxy.type": 1,
    "network.proxy.socks": "gate.example.com",
    "network.proxy.socks_port": 1080,
    "network.proxy.socks_version": 5,
    "network.proxy.socks_remote_dns": True,
}
```

That much is stock Firefox and it gives you an unauthenticated SOCKS5 connection.

The part that is missing from a stock build is the credentials. Firefox's proxy service
reads the host, the port, the version and the DNS setting, and there is no supported
preference on that path for a SOCKS username and password. This project adds two,
`network.proxy.socks_username` and `network.proxy.socks_password`, and patches the
proxy service to read them, which is why a SOCKS5 proxy with authentication works here
without a relay in front of it.

If you are using this package, none of the above is something you write. Pass the proxy
and the scheme decides the path:

```python
with InvisiblePlaywright(proxy={
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}) as browser:
    ...
```

A `socks5://` or `socks4://` server is translated into those preferences and goes
through the engine. An `http://` or `https://` server is handed to Playwright, because
that is the path where its own credential support is real.

## The DNS half, which almost nobody mentions

`network.proxy.socks_remote_dns` decides where hostnames are resolved.

With it off, your machine resolves every hostname locally and only then sends the
connection through the proxy. The traffic is proxied. The DNS queries are not. Anyone
watching your resolver, which includes your ISP and your own network, has the full list
of everything you visited, and the sites themselves can sometimes see the mismatch
between the resolver's location and the connection's.

Turn it on, and resolution happens at the far end, which is the entire point of using
the proxy. The equivalent when you are using Python's `requests` is the `socks5h`
scheme rather than `socks5`, where the `h` is exactly this.

It defaults to off. If you configure a SOCKS proxy by hand and skip this line, you have
a leak that no leak test aimed at the browser will show you, which is the same blind spot [a WebRTC check has](webrtc-leak-proxy.md).

## Short answers to the questions that lead here

**Does Playwright support SOCKS5 with a username and password?** The `server` field
accepts `socks5://`, and the credential fields are documented for HTTP proxies. The
feature request for SOCKS authentication has been open since 2021.

**Why does it not throw an error?** Because the arguments are valid arguments. Nothing
about the call is malformed; the credentials simply are not carried on that path.

**How do I check whether my proxy is actually being used?** Read the exit address from
inside the browser and compare it with your own. Do not infer it from a page loading,
because a fall-through to a direct connection also loads pages.

**Is there a workaround?** A local relay that authenticates upstream is the usual one
and it works on every engine. Firefox can also be configured directly through
preferences, which is what this project does.

**Does SOCKS4 support authentication?** Not in the username and password sense.
SOCKS4a has a user field with no password, so if your provider requires real
credentials you want SOCKS5 or HTTP.

**Why does my DNS leak even though the proxy works?** Because SOCKS proxies resolve
names locally unless you tell them not to. Set `network.proxy.socks_remote_dns` to true,
or use `socks5h` outside the browser.

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md),
which is the other half of getting a proxied session to agree with itself, and
[WebRTC leak with a proxy](webrtc-leak-proxy.md), which is the leak that survives a
correctly configured proxy.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The two preference names above are ours, added
because the stock proxy path has nowhere to put a SOCKS password.*
