---
title: "Sticky vs rotating proxy sessions: which to use"
description: "Sticky vs rotating proxy sessions explained in browser terms: how each maps to one launch and one exit, when to keep an IP for a whole visit, and what neither one fixes."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 20
---


# Sticky vs rotating proxy sessions: which to use

Most proxy providers sell the same pool two ways: a sticky endpoint that keeps
one exit IP for the life of a session, and a rotating endpoint that hands you a
new exit on some schedule or on every request. The question that brings people
here is which one to point a browser at, and the honest answer is that for a
browser driving a real visit it is almost always sticky, for a reason that has
nothing to do with which is "better" and everything to do with what a browser
session already assumes.

This page defines both in browser terms, maps each to the way this tool runs one
launch against one proxy, and then says plainly what neither choice fixes, so a
sticky IP is not mistaken for a clean one.

## What sticky and rotating actually mean

Two endpoints, two different promises about the exit address.

- **Sticky** gives you one exit IP and holds it for the whole session, usually
  for a fixed window (a few minutes to an hour, depending on the provider). Every
  request in that window leaves from the same address.
- **Rotating** gives you a fresh exit per unit of work. Depending on the endpoint
  that unit is one request, or one short window, so consecutive requests can leave
  from different addresses in different cities.

Rotating is built for a workload made of many small independent fetches, where
each request is its own event and no request needs to remember the last one. A
browser is the opposite of that workload.

## Why a browser visit is one identity, not many requests

A page load is not one request. It is a document, then dozens of subresources,
XHR/fetch calls, and often a WebSocket, all issued by the same tab and all tied
together by state the site handed you on the way in.

Three pieces of that state assume the requests keep leaving from the same place:

- **Cookies.** The [session cookie](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies)
  a site sets on the first response is presented on every request after it. The
  site issued that cookie to whoever arrived on the first IP.
- **A session token.** Anything the site minted for your visit - a CSRF token, an
  auth token, a server-side session id - is bound to the context it was created in.
- **The Referer chain.** Each in-site navigation carries the [previous
  URL](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referer),
  so the requests form an ordered path through the site rather than a set of
  unrelated hits.

Cookies, a session token, and a Referer chain all assume the same exit. That is
why the natural unit here is one launch, one proxy, for the life of a session.
Keep the exit fixed and the identity stays internally consistent: the IP that
holds the cookies is the IP that got them.

Rotate the exit in the middle of that visit and you hand the site one visitor
whose IP contradicts its own cookies. The session cookie says this browser
arrived from address A a minute ago; the request carrying it now leaves from
address B, a different network in a different city. Nothing about the browser
changed, but the story the requests tell stopped making sense, and that
contradiction is a cheaper signal for a site to read than any in-page fingerprint.

## How this maps to one launch, one proxy

In invisible_playwright a proxy is set once, at launch, and it stays put for the
life of the browser. That is the sticky model expressed in code: pass a sticky
endpoint from your provider and the whole visit leaves from one exit.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://sticky.gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# one launch, one exit, for the whole visit
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/login")
    page.click("#submit")            # same IP that received the session cookie
    page.goto("https://example.com/account")   # Referer chain and cookies stay consistent
```

The `browser` here is a real Playwright `Browser`, so every method works as
documented; the only thing the wrapper decided for you is that the exit does not
move mid-session. The `seed` pins the fingerprint so the identity is reproducible
across runs, which is a separate axis from the IP but the same principle: hold the
visitor still so a failure is something you can replay.

If your workload genuinely is many independent visits rather than one continuous
one, rotation still has a place - you just apply it between launches, not inside
one. Give each visit its own launch with its own exit:

```python
seeds_and_exits = [
    (11, "socks5://user:pass@a.gate.example.com:1080"),
    (12, "socks5://user:pass@b.gate.example.com:1080"),
]

for seed, server in seeds_and_exits:
    with InvisiblePlaywright(seed=seed, proxy={"server": server}) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        # this visit is complete and self-contained before the next exit is used
```

That is rotation done at the boundary where it does not break anything: a new
identity gets a new IP, and no single visit ever spans two exits. For rotating an
exit across many parallel jobs, [the per-run rotation
pattern](how-to-rotate-proxies-playwright.md) covers pool handling and retry, and
if you want each visit fully walled off from the others,
[one browser context per session](isolate-identities-browser-context-per-session.md)
keeps cookies and storage from bleeding between them.

## What neither sticky nor rotating fixes

This is the part that gets skipped, and it is the part that decides whether the
session actually gets through. Sticky versus rotating only decides whether the
identity stays internally consistent. It does not touch any of the following:

- **IP reputation.** A sticky IP that is a known datacenter address, or an exit a
  thousand other automated sessions are using this minute, is a bad IP that stays
  bad for your whole visit. Consistency is not cleanliness. Whether the address is
  one a site already distrusts is [a separate question with its own
  page](can-websites-detect-a-datacenter-proxy-ip.md).
- **Rate limits and per-account quotas.** These are counted per IP or per account,
  not per session style. Holding one sticky IP can actually make a rate limit
  easier for a site to enforce, because every request is attributed to the same
  address.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, a perfectly uniform request cadence - none of that is about the
  exit IP. You supply human pacing, or you do not.
- **Timezone and language agreement.** The exit's country still has to match the
  browser's timezone and locale, or the two tell different stories. This tool
  auto-derives the timezone from the egress IP for exactly that reason; the failure
  mode when they disagree has [its own writeup](timezone-proxy-mismatch.md).

Here is the honest framing for the whole product, not just this page.
invisible_playwright is a Firefox patched at the C++ level and driven by stock
Playwright, designed to look like a real browser driven by a real person. That is
why it passes most detection checks: the fingerprint, the TLS handshake, and the
driver layer read as a genuine Firefox rather than an automated one. It does not,
on its own, fix IP reputation, per-account quotas, rate limits, or behaviour. Those
you supply - a clean exit and human pacing - and sticky sessions are simply how you
keep the browser's own story consistent while you do.

## Conclusion

For a browser driving a real visit, use sticky: one launch, one exit, for the life
of the session, because the cookies, the session token, and the Referer chain all
already assume the same address. Rotating mid-session does not make you more
anonymous, it makes you incoherent, because it hands the site an IP that
contradicts the cookies it just issued. Reserve rotation for the boundary between
independent visits, where a new identity gets a new exit and no single visit spans
two. And remember what neither choice buys you: a consistent identity is still on
whatever IP you gave it, still subject to that IP's reputation and the site's rate
limits, and still only as human as its behaviour. Sticky keeps the story straight;
the clean proxy and the pacing are yours to bring.

## Short answers to the questions that lead here

**Sticky or rotating for browser automation?** Sticky, almost always. A browser
visit is one identity made of many requests that share cookies and a session token,
and those assume one exit. Rotate between visits, not inside one.

**Does a rotating proxy make me harder to detect?** Not inside a session. Changing
the exit mid-visit hands the site an IP that disagrees with the cookies it already
set, which is easier to spot than a stable IP, not harder.

**Will a sticky session get me past rate limits?** No. Rate limits and quotas are
counted per IP or per account. A stable IP can make them easier to enforce, since
every request is attributed to the same address.

**Does a sticky IP mean a clean IP?** No. Sticky only means the address does not
change. Whether it is a datacenter range or an already-flagged exit is a separate
property you have to check.

**How do I rotate IPs with invisible_playwright?** Give each visit its own launch
with its own proxy, so a new identity gets a new exit and no single visit spans two.
The per-run rotation page covers the pool and retry mechanics.

**Why does my session drop when the proxy rotates?** Because the session cookie and
token were bound to the first exit. When the address changes under them, the site
sees a request whose IP no longer matches the identity it issued, and it can reset
or challenge the session.

## Sources

- This project's proxy model, in which one proxy is set at launch and held for the
  life of the browser, and the reasoning that a browser visit is one identity rather
  than a set of independent requests.
- The product's default behaviour: fingerprint and TLS read as a genuine Firefox,
  while IP reputation, quotas, rate limits and behaviour are supplied by the caller -
  the honest boundary this page is built around.
- MDN, [Using HTTP cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Cookies) -
  how a server-issued session cookie is presented back on later requests.
- MDN, [Referer header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referer) -
  how the header carries the previous page's address on each navigation.

**See also:** [rotating proxies per run](how-to-rotate-proxies-playwright.md) for
doing rotation at the right boundary, [proxy per browser context](playwright-proxy-per-context.md)
for when different tabs really do need different exits, and [detecting a datacenter
proxy IP](can-websites-detect-a-datacenter-proxy-ip.md) for the reputation question a
sticky IP does not answer.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It looks like a real
browser driven by a real person, which is why it passes most checks - and why the
clean proxy and the human pacing are still yours to bring.*
