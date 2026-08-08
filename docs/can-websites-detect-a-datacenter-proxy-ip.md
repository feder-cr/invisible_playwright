---
title: "Can websites detect a datacenter or proxy IP?"
description: "Yes, directly at the network layer. No fingerprint can hide the IP the connection arrives on. Why fingerprint and IP layers are independent and both must be right."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 16
---


# Can websites detect a datacenter or proxy IP?

Yes, directly. The server sees the address your connection arrives on before a
single line of your JavaScript runs, and it can look that address up: which
network owns it, whether it belongs to a hosting provider or a home broadband
line, and whether it has been abused recently. No browser fingerprint changes
that lookup, because the address is a property of the connection, not of the
page.

This is the honest boundary of any browser-side stealth. A tool like
invisible_playwright makes the browser look like a real person's, and that is
most of what public detection checks read. It does not, and cannot, change the
reputation of the IP you route through. Those are two independent layers, and
both have to be right.

## What the server reads before your page loads

Every request carries a source IP. From that one number the receiving server
derives, with no cooperation from you:

- **The ASN**, the [autonomous system number](https://datatracker.ietf.org/doc/html/rfc1930),
  which identifies the network that owns the address block. Hosting and cloud
  providers have their own ASNs, and they are trivially separable from
  consumer broadband and mobile carriers.
- **The address type**, datacenter versus residential versus mobile, which
  public and commercial datasets label directly.
- **Reputation**, meaning whether this address or its neighbours have recently
  sent traffic that looked automated or abusive. Shared and heavily recycled
  exits accumulate this quickly.

None of that is a browser property. It is decided at the network layer, the same
layer that carries the [TLS handshake no in-page test can see](how-to-test-bot-detection.md).
A perfect fingerprint sitting on an address the server already distrusts is a
consistent browser on a flagged IP, and the flag wins.

## Why the fingerprint cannot reach it

The fingerprint cannot reach the IP because the two are different kinds of
value: the fingerprint is data the browser volunteers to the page, and the IP
is decided by the network carrying the connection, which the page never
touches.

A fingerprint is everything the page can measure about the client from inside
the sandbox: the user agent, the GPU string, the canvas and audio hashes, the
font list, the screen, the language settings. All of it is data the browser
volunteers in response to JavaScript. That is exactly why it is spoofable, and
why matching it to a real Windows machine is worth doing.

The IP is the opposite kind of value. It is not something the browser reports;
it is where the packets came from. The browser cannot lie about it because the
browser is not the one telling the server. The network is. You can change which
IP you arrive on by routing through a different proxy, but you cannot make a
given IP read as something it is not, and no amount of in-page patching touches
it. This is the single most common misunderstanding about stealth: people expect
a good fingerprint to cover the address, and the two never meet.

## Both layers, independently, with the real API

Because the layers are independent, you supply the IP and the tool supplies the
browser. The proxy is passed at launch, and everything downstream is stock
Playwright:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/")
    print(page.title())
```

Two things are happening here, and they are separate. `seed=42` fixes the
browser half: the same GPU, canvas hash, audio context, fonts and screen every
run, so a failure is reproducible. `proxy=...` fixes the IP half: every request,
including DNS, leaves through that exit. The `browser` object is a real
Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser), so
`new_page`, `goto`, `click` and the rest behave exactly as documented upstream.

By default, with no `timezone=` argument, the browser timezone is derived from
the exit IP so the two stories agree. If you want to confirm the exit is what you
think it is, read it from inside the browser rather than assuming, and check that
[WebRTC is not reporting a second address next to the proxy's](how-to-check-proxy-ip-leak.md).

## Choosing the IP half so it does not undo the browser half

The fingerprint work is wasted if the address is one a server distrusts on sight.
Two rules follow directly from what the server reads:

- **Type matters more than count.** A residential or mobile exit shares an ASN
  and an address type with ordinary human traffic; a datacenter exit does not,
  no matter how many of them you rotate through. Rotation does not change the
  type.
- **Freshness matters as much as type.** A large share of cheaply available
  proxies are already public, which means their addresses are already on the
  lists servers consult, and they were flagged before you ever used them. The
  distinction between a clean exit and a burned one is not price, it is whether
  the address is already known. The tradeoffs between exit protocols are covered
  in [SOCKS5 versus HTTP proxies for a browser](socks5-vs-http-proxy-browser.md),
  and the reputation problem itself in
  [why scraping gets blocked even through a proxy](web-scraping-getting-blocked-proxies.md).

Get both halves right and the server sees a genuine Firefox arriving from an
address that looks like a person's. Get the browser right and the IP wrong, and
you have built a careful disguise and then signed it at the door.

## What this does and does not fix

To be explicit, because overclaiming here is both false and a real risk:

invisible_playwright is designed to look like a real browser driven by a real
person, and that is why it passes most fingerprint, TLS and driver-layer checks:
those layers read as a genuine Firefox. On its own it does not fix IP reputation,
per-account quotas, rate limits, or behaviour and timing. Those are yours to
supply: a clean exit for the address, human pacing for the behaviour, sane limits
for the account. There is no setting that makes an IP undetectable, because the
IP is not something the browser gets to describe.

## Conclusion

Can websites detect a datacenter or proxy IP? Yes, directly and early, at a layer
no fingerprint reaches. The browser fingerprint and the IP are independent
signals, read by different parts of the stack, and a detector that sees a perfect
browser on a flagged address believes the address. So match the browser, which is
what this tool does, and pair it with an exit whose type and reputation hold up,
which is what you bring. Neither half alone is a session; both right is.

## Short answers to the questions that lead here

**Can a browser fingerprint hide my IP?** No. The IP is where the connection
comes from, not something the browser reports, so no in-page value or spoof
changes it.

**Will a residential proxy alone get me through?** It fixes the address half. If
the browser still looks automated, a clean IP does not save it. Both layers have
to be right.

**How does a site know my IP is a datacenter?** From the ASN and address-type
datasets tied to the IP. Hosting networks have their own autonomous system
numbers and are labelled as such in public data.

**Does rotating IPs help?** It helps with rate limits and per-address velocity.
It does not change the type: rotating datacenter exits are still datacenter
exits.

**Can invisible_playwright change my ASN or reputation?** No, and nothing on the
browser side can. It handles the fingerprint, TLS and driver layers; you supply
the exit.

**Why do I pass every fingerprint test and still get blocked?** Because the
suites do not see your address, and a consistent browser on a distrusted IP is
still on a distrusted IP.

## Sources

- [RFC 1930](https://datatracker.ietf.org/doc/html/rfc1930), the IETF guidelines
  that define an Autonomous System as a routing policy unit and specify the ASN
  that identifies it.
- [Playwright's `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for what the `browser` object in the example above exposes once the proxy is
  wired in.
- This project's default behaviour: the proxy dict passed at launch routes every
  request and its DNS through the exit, and the browser timezone is derived from
  that exit unless an explicit zone is set.
- The [configuration notes](configuration.md) on proxy schemes and on why roughly
  nine in ten cheap proxies are already known before first use.
- The [testing method page](how-to-test-bot-detection.md), which lists IP
  reputation among the things no in-page suite covers.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
[how to check for a proxy IP leak](how-to-check-proxy-ip-leak.md), and
[when the timezone does not match the proxy](timezone-proxy-mismatch.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The browser half is
what the patch does; the IP half is the one honest thing it cannot do for you.*
