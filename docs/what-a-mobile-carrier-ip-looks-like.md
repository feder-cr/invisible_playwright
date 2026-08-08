---
title: "What does a mobile carrier IP look like to a site?"
description: "How a site reads a mobile carrier IP: operator ASN, carrier-grade NAT sharing, and churn, and why a mobile exit pairs cleanly with a desktop browser fingerprint."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 24
---


# What does a mobile carrier IP look like to a site?

A mobile carrier IP does not look like a home broadband line, and sites know the
difference. It sits behind carrier-grade NAT, its ASN belongs to a mobile operator, it is
shared by many real subscribers at once, and it changes without meaning. Those three
signals - operator ASN, shared NAT, churn - are what a site reads when it decides how much
to trust the address, and they are read separately from anything your browser reports.

This page is what those signals actually are, why sites weight a mobile IP differently
from a static consumer line, the consistency trap that catches people who pair a mobile
exit with the wrong browser story, and the one honest caveat about what an address can and
cannot do for you.

## The three signals that mark a carrier IP

A site does not "see a phone". It sees an address, looks up what is publicly known about
that address, and reads three things off it.

**The ASN belongs to a mobile operator.** Every IP maps to an [Autonomous System
Number](https://www.iana.org/assignments/as-numbers/as-numbers.xhtml), and the registries
record who owns each block. A mobile operator's ranges are labelled as such. A lookup that
returns a mobile network is a different fact from one that returns a residential broadband
provider or a datacenter host, and sites treat the three categories differently before your
page has finished loading.

**It is shared by many subscribers at once.** [Carrier-grade NAT
(CGNAT)](https://datatracker.ietf.org/doc/html/rfc6598) puts hundreds or thousands of real
phones behind one public address at the same time. So a single mobile IP
carries a lot of unrelated, genuine traffic. A site that blocks it outright blocks real
customers, which is exactly why mobile ranges are handled with a lighter touch than a
datacenter range that only ever emits automated traffic.

**It changes without meaning.** A phone that moves between cells, drops to a new session,
or simply sits idle can be handed a different public IP with no user action and no
significance. This churn means a site cannot treat "same IP" as "same person" the way it
can for a static line, and it cannot treat "IP changed mid-session" as suspicious the way
it might elsewhere.

## Why a site weights it differently from a static line

**Operator ASN, shared NAT and meaningless churn together are why sites weight a mobile IP
differently from a static line.** A static consumer broadband line is roughly one household,
stable for months. If it starts behaving
oddly, an IP-level action is precise: it affects that one line. A mobile IP is the
opposite on all three counts, so the same IP-level action is blunt and the same "same
address" reasoning is unreliable.

The practical consequences you will actually observe:

- **A mobile IP absorbs more per-address activity before it looks abnormal**, because a lot
  of legitimate people genuinely share it.
- **IP churn during a session is normal**, so a mid-session address change is not by itself
  a red flag on a mobile exit the way it can be on a static one.
- **Reputation is coarse.** A mobile block is a range or an operator behaviour, not a
  precise line, so the signal a site derives from it is weaker and it leans harder on the
  browser and behaviour to make up the difference.

That last point is the one that matters for automation: because the IP tells the site
less, the browser story has to be clean, because it is carrying more of the weight.

## The consistency trap: a mobile exit with a desktop browser

Here is the part people get wrong. It feels like a mobile IP should be paired with a
mobile browser - a phone user agent, a small viewport, touch events. It should not, and
insisting on it creates the mismatch.

**A mobile exit with a desktop browser is a normal, common combination.** It is exactly
what tethering is: a laptop sharing a phone's connection. Millions of real desktop sessions
egress through a mobile operator every day. A desktop Firefox fingerprint arriving from a
mobile ASN is an ordinary thing a site sees constantly.

**A mobile exit claiming to be a phone, with a desktop viewport, is not normal.** The
moment you set a phone user agent, you have made a claim - "I am a phone" - and every value
that a real phone would carry now has to agree with it. A real phone has a small,
high-density screen, touch as the primary pointer, a mobile GPU string, a specific set of
fonts, and no window that a mouse arcs across. Set the user agent and forget the rest and
you have a phone that reports a 1920-wide desktop viewport and a desktop GPU, which is a
contradiction no real device produces. That is the [same class of self-inflicted mismatch
as any hand-set value that disagrees with the rest](playwright-detected-as-bot.md).

invisible-playwright presents a **Windows desktop** fingerprint by default: desktop GPU,
desktop screen, desktop fonts, mouse pointer, all cross-consistent and all derived from one
seed. Run that through a mobile exit and you get the tethering story, which is coherent.
The failure mode is only if you then bolt a phone user agent on top of a desktop machine.
Do not. If you genuinely need a phone identity, that is a whole-device decision, not a
header - see [Playwright Firefox mobile emulation](playwright-firefox-mobile-emulation.md)
for why a phone header without the phone machine is worse than no change at all.

## Running a desktop browser through a mobile exit

The code is the ordinary two-line launch. The mobile part is entirely in the proxy you
supply; the browser stays a coherent desktop.

```python
from invisible_playwright import InvisiblePlaywright

# A mobile-carrier exit is just a proxy endpoint. The browser presents a
# Windows desktop fingerprint; the exit ASN is a mobile operator. That pairing
# is a tethered laptop, which is a story sites see every day.
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/")
    # Confirm the exit the site actually sees, rather than assuming it.
    print(page.evaluate("() => fetch('https://example.com/ip').then(r => r.text())"))
```

Two things worth calling out. The `seed=42` keeps the desktop identity identical run after
run, so if a mobile exit behaves differently from a static one you can [replay the exact
same machine](quickstart.md) and isolate the address as the only variable. And the timezone
is auto-derived from the egress IP by default, so a mobile exit in another country moves
the browser's timezone with it rather than leaving a static-home timezone stranded on a
foreign address - which is [the mismatch that has its own
page](timezone-proxy-mismatch.md).

Do not set a phone user agent here. The browser is a desktop; let it stay one.

## The honest caveat: an address is not a reputation

invisible-playwright is built to look like a real Firefox driven by a real person, and that
is why it passes most detection: the fingerprint, the TLS handshake and the driver layer
read as a genuine desktop browser. A mobile exit does not change that story for the better
or the worse, as long as you keep the browser a desktop.

What a mobile IP does **not** do, and what no fingerprint layer does either:

- **It does not launder a bad reputation.** A mobile range that has already been abused
  carries that history. A clean browser on a burned exit still loses, the same way [a
  perfect fingerprint on a known datacenter IP
  loses](can-websites-detect-a-datacenter-proxy-ip.md).
- **It does not raise your per-account quota.** Rate limits and account-level throttling
  are counted against the account, not the address, and the coarse reputation of a shared
  mobile IP does not lift them.
- **It does not supply pacing.** A mobile exit that receives ten form submissions in ten
  seconds is emitting a velocity signal no browser realism can mask. Human pacing is
  something you provide.
- **It does not fix a self-inflicted claim.** If you tell the site you are a phone and then
  hand it a desktop, the address cannot rescue the contradiction.

The address is one input among several. invisible-playwright supplies a genuine desktop
browser; a clean, appropriately-shared exit and human timing are yours to supply.

## Conclusion

A mobile carrier IP is marked by three public facts: a mobile-operator ASN, heavy sharing
behind carrier-grade NAT, and churn that carries no meaning. Sites weight it differently
because those three make IP-level reasoning blunt and unreliable, which pushes more of the
trust decision onto the browser and the behaviour.

The trap is thinking a mobile exit demands a mobile browser. It does not. A desktop browser
on a mobile exit is a tethered laptop, a story sites see constantly. A phone header on a
desktop machine is a contradiction they see never. Keep invisible-playwright's desktop
identity coherent, supply a clean exit and human pacing, and the mobile IP is an asset
rather than a tell.

## Short answers to the questions that lead here

**How does a site know an IP is mobile?** It looks up the ASN in the public registries. A
mobile operator owns the block, and that ownership is recorded, so the lookup returns
"mobile network" before your page finishes loading.

**Should I use a mobile user agent with a mobile proxy?** No. A mobile exit with a desktop
browser is normal tethering. A phone user agent forces every other value - screen, pointer,
GPU, fonts - to also be a phone's, and if they are not you have built a contradiction.

**Why are mobile IPs treated more leniently than datacenter ones?** Because carrier-grade
NAT means one mobile IP carries the genuine traffic of many real subscribers, so blocking
it blocks real customers. A datacenter IP only ever emits automated traffic.

**Does a mobile IP make me undetectable?** No. It improves one input, IP reputation, and
only if that particular exit is clean. It does nothing for account quotas, rate limits,
timing, or a browser that contradicts itself.

**My IP changed mid-session on a mobile proxy. Is that a problem?** Usually not on a mobile
exit, because real phones do exactly that when they move between cells. The same change on a
static line would be more notable.

**Can invisible-playwright present a phone?** It presents a Windows desktop by default,
which is the right pairing for a mobile exit used as tethering. A true phone identity is a
whole-device decision, not a header - see the mobile emulation page before attempting it.

## Sources

- [IANA's Autonomous System Number
  registry](https://www.iana.org/assignments/as-numbers/as-numbers.xhtml), which is where
  the mobile-operator label a site reads actually lives, and [RFC 6598's Shared Address
  Space](https://datatracker.ietf.org/doc/html/rfc6598), the IETF allocation carrier-grade
  NAT deployments use.
- This project's configuration behaviour: the proxy dict, seed-reproducible identities, and
  the egress-derived timezone, each documented on its own page in this set.
- Our own release gates comparing a desktop fingerprint field by field against a stock
  desktop Firefox, which is how the desktop identity is kept coherent regardless of the
  exit it runs through.

**See also:** [why a datacenter IP still gets caught](can-websites-detect-a-datacenter-proxy-ip.md)
for the other end of the reputation scale, [when the timezone does not match the
proxy](timezone-proxy-mismatch.md) for what has to move with the exit, and [can two devices
share a browser fingerprint](can-two-devices-share-a-browser-fingerprint.md) for why the
machine, not the address, is what a fingerprint identifies.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It looks like a real desktop
browser, which is why it passes most checks; the exit and the pacing are still yours to
get right.*
