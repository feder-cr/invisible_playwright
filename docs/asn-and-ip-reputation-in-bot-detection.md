---
title: "What is ASN and IP reputation in bot detection?"
description: "What ASN and IP reputation are: how they score requests before JavaScript runs, how reputation builds and decays, and why a clean browser on a burned IP loses."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 23
---


# What is ASN and IP reputation in bot detection?

An ASN identifies which network operator an address belongs to, and IP reputation is the
abuse history attached to that address; together they let a detector score a connection
before a single line of JavaScript runs. This is the layer a browser cannot touch, and it
is why a session with a flawless fingerprint can still be turned away at the door.

This page explains what an ASN is, how IP reputation gets built and how it decays, why a
perfect browser on a flagged block loses anyway, and where the browser's job ends and the
proxy's job begins.

## What an ASN actually is

An ASN, an [Autonomous System Number](https://datatracker.ietf.org/doc/html/rfc1930),
identifies the network operator that announces a block of IP addresses to the rest of the
internet. A residential internet provider has one or more ASNs. A cloud host has its own.
When a connection reaches a server, the server can look up the address and learn, in a
single query, which operator it belongs to and what kind of operator that is.

That last part is the point. ASNs carry a category. A range that belongs to a consumer
internet provider looks like a person at home. A range that belongs to a hosting company
or a cloud region looks like a server, because that is what almost everything in it is.
The lookup is cheap, it happens at the very first packet, and it does not care in the
slightest what browser you plan to run. A datacenter ASN is a strong prior on its own:
real customers rarely browse a shopping site from a cloud region, so a request from one
starts the session already suspected. This is covered in more depth in
[can websites detect a datacenter or proxy IP](can-websites-detect-a-datacenter-proxy-ip.md),
and the short version is that they can, directly, at the network layer.

## How IP reputation is built and how it decays

The ASN category is the coarse signal. Reputation is the fine one, and it is per-address
and per-block rather than per-operator.

A reputation service records what addresses have done. If a particular IP, or the /24
block around it, has recently been the source of credential-stuffing traffic, scraping
runs, spam, or a burst of failed logins, that history attaches to the address. It is
shared: the block was burned by whoever used it before you, and you inherit the score. A
single address can also burn itself inside one session, purely by volume. If one IP opens
several hundred sessions against the same endpoint in an hour, that velocity is itself the
signal, independent of how human each individual session looks.

Reputation decays, but slowly and unevenly. An address that sat quiet for a long stretch
recovers some standing. An address that is being actively abused right now does not, and
the decay resets every time the block is used again. This is why a cheap shared proxy
never really recovers: the moment its score drifts back toward neutral, the next user on
the same block drives it back down. You are not renting a clean address, you are renting a
position in a queue of people degrading it.

Three inputs, then, all read before the browser matters: the ASN's category, the block's
abuse history, and the live session count from that address.

## Why a perfect fingerprint on a burned block still loses

A perfect fingerprint on a burned block still loses because a detector scores the network
layer and the fingerprint layer independently, and a flawless browser can only zero out the
second one - it has no way to reach the first.

invisible_playwright is designed to look like a real browser driven by a real person. The
fingerprint is coherent, the TLS handshake belongs to a genuine Firefox, and the driver
layer does not announce automation. That is why it passes most detection checks: the parts
a browser controls, it controls well. What it cannot do is change the address the
connection arrives on. A coherent browser on a burned IP is still on a burned IP.

The two layers are scored independently and both have to be right. Picture a detector as
adding up evidence. The network layer contributes its verdict, a datacenter ASN or a block
with recent abuse, before any script runs. The fingerprint layer contributes its verdict
after. A perfect browser sets the second contribution to roughly zero, which is exactly
what you want, but it cannot make the first contribution negative. If the network layer
alone crosses the threshold, the session is challenged or blocked no matter how clean
everything downstream reads. This is the mechanism behind
[being blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md): nothing
is wrong with the browser, and the browser was never the thing that failed.

You can prove which layer you are hitting in about a minute. Open the same URL by hand,
from the same machine and the same exit, in a stock browser. If the manual visit is also
challenged, the browser is not your problem and no fingerprint change will help. If the
manual visit passes and the automated one does not, the difference is in the browser or
the behaviour, and that is a different investigation entirely.

## Where the browser ends and the proxy begins

The division of labour is clean, and being clear about it saves a lot of wasted effort.

The browser's job is everything the server reads after the connection is established: the
fingerprint fields, the TLS handshake, the absence of automation tells, the driver layer.
invisible_playwright handles those from one seed, reproducibly. Your job, the part the
browser cannot do for you, is three things it has no access to.

- **A clean exit.** An address on a consumer ASN, on a block without recent abuse, not
  already shared by a crowd. Around 90% of proxy addresses are public and therefore
  already known and scored before you send a byte through them. The clean fraction is the
  part worth paying for.
- **Human pacing.** Session count per address is a reputation input, so hundreds of
  sessions from one IP burns it regardless of browser quality. Spread the work across
  addresses and across time.
- **Behaviour and quotas.** Per-account limits, rate limits, and the timing of your
  actions are yours to supply. The browser looks human; whether it acts human is up to
  the code driving it.

One more thing that is genuinely the browser's job and is easy to mistake for a network
problem: the browser and the exit have to tell the same story. An exit in one country
behind a browser claiming another is a contradiction the detector reads directly, and it
is cheap to get right. invisible_playwright derives the timezone from the egress IP by
default, which is why
[a timezone that does not match the proxy](timezone-proxy-mismatch.md) has its own page.

## A runnable example

The browser side is two lines. You supply the proxy; the wrapper wires it, routes DNS
through it, and derives the timezone from its exit so the browser and the network agree.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# seed=42 makes the identity reproducible; the proxy is the exit you supply
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    # confirm the exit the browser is actually leaving from,
    # rather than assuming it
    page.goto("https://example.com/ip")
    print(page.inner_text("body"))
```

The `browser` object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every standard
method works exactly as documented upstream. Note what the example does and does not
claim: it pins the fingerprint so a failure is reproducible, and it reads back the exit so
you know which address your reputation is riding on. It does not make a bad address good.
If the printed IP sits on a datacenter ASN or a burned block, the cleanest browser in the
world is still leaving from there.

## Conclusion

ASN and IP reputation are the part of bot detection that runs before your browser gets a
turn. The ASN says what kind of network you are on, the reputation says what that address
and its neighbours have been doing, and the session count says how hard you are leaning on
it right now. All three are read at connection time, none of them are in a browser's
gift to change, and a perfect fingerprint sets its own layer to zero without touching
theirs.

So the honest framing is the useful one. invisible_playwright makes the browser layer look
like a real person's real browser, which is most of the battle and the part most tools get
wrong. The network layer is yours: a clean exit, human pacing, and quotas you respect. Get
both right and the session passes. Get the browser perfect on a burned block and it does
not, and now you know exactly why.

## Short answers to the questions that lead here

**What is an ASN in bot detection?** The number identifying the network operator that owns
the address you connect from. Detectors read it at the first packet to learn whether you
look like a home connection or a datacenter, before any JavaScript runs.

**Can a good fingerprint fix a bad IP?** No. The two are scored independently. A perfect
browser zeroes out its own layer but cannot make a burned or datacenter address look
clean, so the session can still be blocked on the network verdict alone.

**Why does my clean browser still get blocked?** Almost always the exit. Prove it by
opening the same URL by hand from the same machine and exit in a stock browser; if that is
challenged too, the browser was never the problem.

**How does an IP get a bad reputation?** From abuse history on the address or its block,
inherited from whoever used it before you, plus live session volume. Shared cheap proxies
never recover because the next user re-burns the block as it decays.

**Does invisible_playwright give me a clean IP?** No, and it does not claim to. It handles
the browser fingerprint, TLS and driver layers. You supply the proxy, the pacing and the
quotas; the browser cannot change the address it arrives on.

**How many sessions can one IP make?** Fewer than you would like, because session count is
itself a reputation input. Hundreds of sessions from one address burns it regardless of
how human each session looks. Spread across addresses and across time.

## Sources

- [RFC 1930](https://datatracker.ietf.org/doc/html/rfc1930), the IETF guidelines that
  define an Autonomous System as a routing policy unit and specify the ASN that identifies
  it.
- [Playwright's `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for what a launched `Browser` object exposes once the wrapper hands it back.
- This project's release and validation gates, which test the browser layer through a real
  proxy and confirm the exit address inside the browser rather than assuming it.
- The network-layer behaviour described here is read from public reputation and ASN-lookup
  mechanics, cross-checked against the browser layer this project controls.

**See also:** [can websites detect a datacenter or proxy IP](can-websites-detect-a-datacenter-proxy-ip.md)
for the network layer in detail, [browser trust scores explained](browser-trust-score-explained.md)
for how the fingerprint layer is scored separately, and
[why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md)
when every browser check comes back green.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The browser layer is the
part we can make perfect; the IP is the part only you can keep clean.*
