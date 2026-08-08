---
title: "How to rotate proxies when scraping with Playwright"
description: "A tutorial on assigning a proxy per session in Playwright, SOCKS5 authentication, DNS through the proxy, and why the exit IP must match the timezone it claims."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 4
---


# How to rotate proxies when scraping with Playwright

To rotate proxies in Playwright, assign one proxy per browser session and keep it for
the life of that session, authenticate it on the path that carries its scheme, resolve
DNS through it, and derive the timezone, locale and WebRTC address from that session's
own exit IP so none of them contradicts the new address. Rotating the exit IP is the
easy half; making everything that must agree with the new IP actually agree with it is
the half that decides whether a rotated session gets flagged.

This is a tutorial: assign a proxy to a session, authenticate it, resolve DNS through
it, and decide when to rotate at all. The code is real and runnable against
`InvisiblePlaywright`, a real Playwright `Browser`.

The reason to read past the code samples is the last section. Rotating the exit IP is
the easy half. The half that actually gets sessions flagged is everything that has to
agree with the new IP once you have it, and generic proxy-rotation tutorials do not
mention it because their browser cannot fix it anyway.

## Per session, not per request

Rotate the proxy once per browser session, not once per request: give one browser
launch one proxy and hold it for the whole visit, then close the session and start the
next one on a different proxy. This is the [sticky-session pattern rather than a
per-request rotating pool](sticky-vs-rotating-proxy-sessions.md), and for a browser it
is the correct default.

The instinct coming from `requests` or `scrapy` is to rotate on every outgoing call: a
fresh IP per request, drawn from a pool, so no single address makes too many calls.
That instinct is wrong for a browser session, for a reason that has nothing to do with
rate limits.

A browser session is not a sequence of independent requests. It is one visit: cookies
set on the first response, a session token issued once, a `Referer` chain, a page that
expects the next request to come from the same place as the last one. Swap the IP
mid-session and you have a visitor whose cookies say "the person from Germany" and
whose next request arrives from Japan. That is not two requests looking less
suspicious. It is one identity contradicting itself, and it is cheaper to catch than a
single bad IP ever was.

Rotate the unit that actually corresponds to a visitor: one browser launch, one proxy,
for the life of that session. When the job is done, close it and start the next one on
a different proxy.

```python
from invisible_playwright import InvisiblePlaywright

proxies = [
    {"server": "socks5://gate1.example.com:1080", "username": "user1", "password": "pass1"},
    {"server": "socks5://gate2.example.com:1080", "username": "user2", "password": "pass2"},
    {"server": "socks5://gate3.example.com:1080", "username": "user3", "password": "pass3"},
]

for i, proxy in enumerate(proxies):
    with InvisiblePlaywright(seed=1000 + i, proxy=proxy) as browser:
        page = browser.new_page()
        page.goto("https://example.com/catalog")
        # ... do this session's share of the work, then let the block exit
```

Each iteration is a separate process, a separate seed, and a separate proxy. Each one
gets its own canvas hash, GPU string, font set and timezone, because those are derived
from the seed and the exit rather than left over from the previous session. That is
also the property a shared-browser, proxy-per-context setup does not have: every
context in one browser reports the same hardware regardless of which proxy it is
tunneled through, which is exactly what
[proxy per context does not isolate](playwright-proxy-per-context.md). If a target only
counts requests per address, contexts are fine and cheaper. If a target compares
sessions to each other, one process per identity is the pattern above, not contexts in
one process.

## When per-request rotation is the right call

There is a narrow case where rotating more often than per-session is correct: plain
HTTP calls with no session state, no login and no cookies to preserve, where each
request is genuinely independent (a public API poll, a stateless price check). Nothing
above applies there, because there is no visitor identity to contradict. The moment a
login, a cart or a multi-step flow is involved, you are back to one session, one proxy,
for its whole duration.

## SOCKS5 with a username and password

Most proxy pools that matter for scraping are SOCKS5 with per-account credentials, and
Playwright's own [`proxy` option](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch)
documents username and password for HTTP proxies only.
Passing them on a `socks5://` server does not raise an error; it just does not
authenticate, and the failure shows up later as requests that silently fall through or
never arrive. The full evidence and the workarounds that exist for plain Playwright are
in [Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).

With this package you do not need any of those workarounds. Pass the four fields and
the scheme decides the path:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

A `socks5://` or `socks4://` server is written into the browser's own proxy
preferences, credentials included, rather than handed to Playwright's proxy layer
where the credentials are silently dropped. `http://` and `https://` servers go to
Playwright directly, because that is the path where its credential support is real.
The endpoint needs an explicit port; a bare host with no port is refused rather than
launched unproxied.

| Proxy scheme | Where the credentials go | Remote DNS |
|---|---|---|
| `socks5://` / `socks4://` | Written into the browser's own proxy prefs, credentials included | On by default |
| `http://` / `https://` | Passed to Playwright's proxy layer, where its credential support is real | On by default |

The [difference between a SOCKS5 and an HTTP proxy for a browser](socks5-vs-http-proxy-browser.md)
is what decides which of those two paths a given proxy takes.

## DNS through the proxy, not around it

A SOCKS5 proxy tunnels the connection. It does not automatically tunnel the DNS lookup
that happens before the connection, and that split is easy to miss because nothing
about it fails loudly.

Resolve hostnames locally and only the traffic is proxied; your resolver, and anyone
watching it, sees the plain list of every hostname visited, unproxied. That gap is
[how a proxy leaks DNS even when the traffic is tunneled](does-a-proxy-leak-dns-doh-explained.md).
Resolve at the far end and the split disappears. `InvisiblePlaywright` routes DNS through the proxy by
default for `socks5`/`socks4`/`http`/`https` servers, so there is nothing to set for
the common case:

```python
with InvisiblePlaywright(proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")   # hostname resolved through the proxy, not locally
```

If you are driving `firefox.launch()` yourself instead of this class, the same proxy
dict has to go through `get_default_stealth_prefs(proxy=...)` so the resolver
preference is written; the mechanics of what actually gets set, and why the leak
produces no visible symptom, are covered in
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).

## The gotcha generic tutorials miss: the exit has to match the story

Every tutorial above this point is about getting the IP to change. None of them say
what has to change alongside it, and that is the part that gets a rotated session
flagged where a static one was not.

An IP is a claim about where a session is. So are the browser's timezone, its locale,
and the address WebRTC reports. A detector does not need any single one of those to
look wrong on its own; it only needs two of them to disagree, and rotating the proxy
without touching the rest is precisely how you manufacture that disagreement. Swap the
exit from Germany to Japan and keep a timezone that was resolved once, at your host's
own location or at the previous proxy's, and the new session is a browser in one
country insisting it is in another. That combination is
[covered field by field](timezone-proxy-mismatch.md): timezone offset, `Intl` output,
`navigator.language`, the `Accept-Language` header and the locale all have to name the
same country as the IP, and they are fed by different mechanisms, so fixing one does
not fix the rest.

The structural fix is to stop treating the timezone as something you configure once
and start treating it as a consequence of whichever proxy is active for that session.
`InvisiblePlaywright` does this by default: leave `timezone` unset and it is derived
from the actual egress IP of that session's proxy, at launch, every time you rotate.

```python
# default: each session's timezone follows its own proxy's exit, automatically
for proxy in proxies:
    with InvisiblePlaywright(proxy=proxy) as browser:
        ...   # this session's timezone, locale and WebRTC address all match this proxy
```

Pin an explicit IANA zone only when you already know the proxy's country with more
confidence than an IP-geolocation lookup can offer, and want to force it rather than
derive it:

```python
with InvisiblePlaywright(proxy=proxy, timezone="America/New_York") as browser:
    ...
```

The same lookup that resolves the timezone also feeds the locale and the WebRTC public
address in this project, so the three cannot drift apart the way they do when a
rotation script sets the proxy in one place and the timezone in another, if it sets it
at all.

WebRTC is the other half of this and it fails in both directions.
[A SOCKS5 proxy carries TCP; STUN wants UDP](webrtc-leak-proxy.md), so on a browser that
does not handle this deliberately, the WebRTC candidate can name your real address
right next to the proxied one in the same session, which is a direct contradiction
rather than a subtle one. The instinctive fix, disabling WebRTC outright, trades that
for a worse tell: a session with zero ICE candidates is a browser announcing that
something intercepted it, which is its own signal and arguably a stronger one. The
goal when rotating proxies is not to make WebRTC silent, it is to make it agree with
the rest of the session, exit address included.

## Conclusion

Rotate at the level of a visitor, one browser launch and one proxy per session, not per
request, unless the calls are genuinely stateless. Get SOCKS5 authentication onto the
path that actually carries it rather than the Playwright field that silently drops it,
and route DNS through the proxy rather than around it. None of that is the part that
gets a rotation script flagged. The part that does is leaving the timezone, locale and
WebRTC address behind when the IP moves: derive them from each session's own exit
instead of configuring them once, and a rotated session tells one consistent story
instead of several contradicting ones.

## Short answers to the questions that lead here

**Should I rotate the proxy on every request?** No, unless the requests are genuinely
stateless with no cookies or login involved. A browser session is one visit; changing
its exit mid-visit contradicts the cookies and tokens already issued to it.

**Why doesn't my SOCKS5 username and password work in Playwright?** Playwright's
`proxy` credentials are documented for HTTP proxies only. A `socks5://` server accepts
the fields without error and simply does not authenticate with them.

**Do I need to set the timezone myself when I rotate proxies?** Only if you want to
force a specific zone. The default behavior here is to derive it from each session's
own proxy exit at launch, so it never needs to be set by hand to stay correct.

**Is a proxy-per-context browser the same as rotating proxies per session?** No. Every
context in one browser process shares the same canvas, GPU, fonts and audio profile;
only the address differs. That is one machine appearing from several countries, not
several sessions.

**Does disabling WebRTC solve the leak when rotating proxies?** It solves the address
leak and creates a different signal: a browser with zero ICE candidates. The session
still disagrees with itself, just about a different thing.

**Does my DNS actually go through the new proxy after I rotate?** Only if remote
resolution is turned on for that connection. Otherwise every rotation still resolves
hostnames locally, which a page cannot see but a resolver-level observer can.

**See also:** [what a proxy per context does and does not isolate](playwright-proxy-per-context.md),
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md),
and [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for where proxy rotation sits among the other layers a block can come from.

## Sources

- Playwright's [`proxy` API reference](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch),
  whose credential fields are documented for HTTP proxies and not for `socks5://` servers.
- [Playwright timezone does not match the proxy IP](timezone-proxy-mismatch.md) and
  [WebRTC leak with a proxy](webrtc-leak-proxy.md), both grounding the exit-consistency
  section above.
- This project's own default of resolving timezone, locale and WebRTC exit address from
  one lookup per launch, so a rotated session cannot have them disagree.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level and driven by stock Playwright. The IP is the part
every rotation tutorial covers; the timezone it leaves behind is the part that gets
sessions caught.*
