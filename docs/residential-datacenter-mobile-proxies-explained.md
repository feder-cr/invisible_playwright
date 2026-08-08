---
title: "Residential vs datacenter vs mobile proxies explained"
description: "What residential, datacenter and mobile proxies look like to a server: ASN, reverse DNS, shared exits. Browser fingerprint does not upgrade the IP class."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 19
---


# Residential vs datacenter vs mobile proxies explained

There are two completely separate things a site reads about your session, and they are
easy to confuse - especially when the question is which of residential, datacenter or
mobile proxies to use. One is the browser: the fingerprint, the driver layer, the TLS
handshake. The other is the exit address: who owns the IP, what its reverse DNS says,
how many other people are behind it right now. A tool can make the first one look like a
real person on a real Windows machine and change nothing about the second.

This page is about the second thing. It explains what each proxy class actually looks
like to a server, and then draws the honest line that most guides skip:
invisible_playwright makes the browser coherent, it does not turn a datacenter IP into a
residential one. The IP class is a property of the proxy you bought, not of the browser
you drove through it.

## What a site actually reads from an IP

When a request arrives, a site has a handful of cheap lookups it can do on the source
address alone, before it runs a single line of your JavaScript.

- **The ASN and its owner.** Every IP belongs to an
  [Autonomous System](https://datatracker.ietf.org/doc/html/rfc1930), and the AS owner
  is public. A block registered to a hosting company reads as a hosting company. A block
  registered to a consumer broadband provider reads as consumer broadband. This lookup
  costs nothing and is the first thing a reputation service does.
- **The reverse DNS (rDNS).** Many IPs resolve backwards to a hostname, and the pattern
  is a tell in itself. Server ranges often resolve to machine-generated names full of the
  IP octets and a hosting brand. Consumer lines resolve to names that look like an ISP's
  subscriber pool, or do not resolve at all.
- **How many others share it.** A single address emitting requests for hundreds of
  unrelated sessions in a minute is a shared gateway, and that velocity is visible from
  the server side without any browser cooperation.
- **Prior reputation.** Address ranges accumulate history. A range that has already been
  seen doing bulk automation carries that history to your session, whoever you are.

None of these four are browser properties. You cannot patch them from inside the page,
because the site reads them from the network before the page exists. That is the whole
reason the proxy class matters on its own.

## Datacenter proxies: cheap, fast, and labelled

A datacenter proxy exits from a server range. The ASN belongs to a hosting or cloud
provider, the rDNS usually looks machine-generated, and a single address is frequently
shared across many customers at once.

That combination is trivially classifiable. A site does not need to prove the request is
automated; it can price the risk on the ASN alone and serve the address a harder path -
more challenges, tighter rate limits, a thinner page. As the configuration notes put it,
[around 90% of proxies are already known and blocked before you ever use them](configuration.md),
and public datacenter pools are the bulk of that 90%.

Datacenter IPs are not useless. They are cheap, fast and stable, and for a site that does
not weigh the ASN heavily they are perfectly fine. What they are not is invisible: the
address announces where it lives, and no amount of browser realness edits that field.
This is exactly the case covered in
[can websites detect a datacenter proxy IP](can-websites-detect-a-datacenter-proxy-ip.md),
which is worth reading if the datacenter question is the one that brought you here.

## Residential proxies: consumer ranges, higher cost

A residential proxy exits from an address registered to a consumer broadband provider.
The ASN reads as an ISP, the rDNS looks like a subscriber line, and on the ASN and
reverse-DNS checks a site runs before your page loads, the exit is indistinguishable
from a household on that ISP.

That is why residential IPs survive the ASN filter that datacenter IPs fail. They are
also more expensive, usually slower, and often billed by the gigabyte rather than the
month, because the supply is genuine consumer connections rather than rented rack space.

Two honest caveats. First, a residential exit is frequently still a shared gateway: many
sessions leave through one address, so the velocity signal from the previous section can
still fire even though the ASN is clean. Second, not all residential supply is clean
supply. A residential range that has already been burned by bulk automation carries that
reputation regardless of the ASN label. The configuration page recommends filtering for
[residential IPs that are not already on the known lists](configuration.md) for exactly
this reason - the class alone is not the whole story, the reputation within the class is.

## Mobile proxies: carrier NAT, and why they read as human

A mobile proxy exits through a cellular carrier. The ASN belongs to a mobile network
operator, and the defining property is carrier-grade NAT: the operator puts a large
number of real subscribers behind a small pool of public addresses.

That NAT is what makes mobile exits expensive to distrust. Blocking one address can mean
blocking thousands of genuine phone users who happen to share it, so a site pays a real
cost for a false positive on a mobile ASN. The addresses also rotate on the carrier's own
schedule as devices move between towers, so the churn is normal traffic rather than a
tell.

The trade is cost and speed. Mobile bandwidth is the priciest of the three and typically
the slowest, and the same shared-gateway reality applies: the address is genuinely
human-looking, but you are one of many behind it.

## Where invisible_playwright helps, and where the IP takes over

Here is the line, stated plainly. invisible_playwright is built to look like a real
Firefox driven by a real person. The fingerprint is coherent across roughly 400 fields,
the driver layer does not announce automation, and the TLS handshake is a genuine
Firefox handshake because the browser is a real, patched Firefox. That is why it clears
most detection that reads the browser: the fingerprint, the automation flags, the
handshake all read as genuine.

What it does not do is change the IP class. The wrapper routes your session through
whatever proxy you hand it, faithfully, but the ASN and the reverse DNS the site reads
are properties of that proxy, not of the browser. A perfectly coherent Windows
fingerprint arriving from a datacenter ASN still reads as a datacenter ASN. The browser
being real does not relabel the address.

The launch is the same two-line change whichever class you use - the only thing that
changes is the `proxy` dict:

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
    print(page.text_content("body"))
```

The `browser` here is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every standard
method works unchanged. Swap the `server` value for a datacenter, residential or mobile
endpoint and the browser is byte-for-byte identical; only the class of address that
arrives at `example.com` differs. Passing `seed=42` fixes the fingerprint so that when you compare
two proxy classes, the browser is held constant and the IP is the only variable - which
is the only honest way to attribute a block to the exit rather than the browser.

By default the browser timezone is auto-derived from the egress IP, so the exit and the
clock agree without you setting anything. If you pin a zone by hand it has to match the
exit, or you have manufactured the contradiction described in
[when the timezone does not match the proxy](timezone-proxy-mismatch.md). The
authentication and DNS details for the SOCKS endpoint above are in
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).

So the division of labour is: the tool supplies a coherent browser, and you supply a
clean exit and human pacing. Neither substitutes for the other. If the fingerprint is
perfect and the address is a burned datacenter range, the session still loses, which is
the whole subject of
[why you might still be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

## Choosing a class for the job

There is no single right answer, only a match between what a target weighs and what you
are willing to pay.

- If the target barely weighs the ASN, a datacenter proxy is cheapest and fastest, and
  the browser coherence is doing most of the work.
- If the target filters on ASN, you need residential or mobile supply, and within that a
  clean, less-shared range beats a cheap heavily-shared one.
- If the target is unusually strict and the value per session is high, mobile is the
  hardest exit to distrust and the most expensive to run.

Whichever you pick, keep one address from carrying a whole campaign's velocity: rotating
the exit spreads the per-IP request rate, and
[how to rotate proxies with Playwright](how-to-rotate-proxies-playwright.md) covers doing
that without breaking the session-to-exit coherence the rest of this page depends on.

## Conclusion

A proxy class is not a quality setting, it is a description of who owns the address and
who else is behind it. Datacenter exits announce a server range, residential exits look
like a household, mobile exits hide behind carrier NAT, and those labels are read from
the network before your page runs.

invisible_playwright makes the browser side genuinely coherent, which is why it clears
the checks that read the browser. It does not, and cannot, edit the IP class you chose.
Pair a real-looking browser with a clean exit and human pacing and you have both halves;
supply only one and the other half is where the session fails.

## Short answers to the questions that lead here

**Does invisible_playwright turn a datacenter IP into a residential one?** No. It makes
the browser coherent. The ASN and reverse DNS a site reads come from the proxy you passed,
not from the browser, so a datacenter exit still reads as datacenter.

**What is the actual difference a site sees between the three classes?** The ASN owner
and the reverse DNS pattern: a hosting company, a consumer ISP, or a mobile carrier. Plus
how many other sessions share the same address at once.

**Which proxy class should I use?** The cheapest one the target tolerates. Datacenter if
it barely weighs the ASN, residential if it filters on ASN, mobile if it is strict and
the session is worth the cost.

**Why do I still get blocked with a clean fingerprint and a good proxy?** Usually shared
exits and velocity, a burned range within an otherwise clean class, a timezone that does
not match the exit, or behaviour and rate limits the browser cannot fix.

**Is a residential proxy always safe?** No. Residential supply can still be a shared
gateway and can carry prior reputation. The class clears the ASN filter; it does not
guarantee the specific range is unseen.

**Can I just set a Windows user agent and use a cheap proxy?** That fixes nothing the ASN
lookup reads, and a hand-set user agent creates new contradictions of its own. The exit
class is a separate problem from the browser string.

## Sources

- [RFC 1930](https://datatracker.ietf.org/doc/html/rfc1930), the IETF guidelines that
  define an Autonomous System as a routing policy unit and specify the ASN that
  identifies it - the basis for the ASN lookup described above.
- [Playwright's `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for what the launched `Browser` object exposes in the example above.
- This project's configuration notes on proxy schemes, egress-derived timezone, and the
  proportion of public proxy IPs already known and blocked.
- Public IP-classification behaviour: reverse DNS conventions for server versus consumer
  ranges, and carrier-grade NAT on mobile networks - each read from how the lookups work
  rather than from any vendor's claims.
- The project's own experience that a coherent fingerprint on a poorly-chosen exit still
  loses, which is the reason this page exists.

**See also:** [can websites detect a datacenter proxy IP](can-websites-detect-a-datacenter-proxy-ip.md),
[how to rotate proxies with Playwright](how-to-rotate-proxies-playwright.md), and
[why you might still be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It makes the browser look
like a real person's; the exit address is still yours to choose well.*
