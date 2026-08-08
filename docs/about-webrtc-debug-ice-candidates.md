---
title: "about:webrtc: read your real ICE candidates"
description: "Read your live ICE candidates in Firefox's built-in about:webrtc: tell a real LAN-IP leak from a masked .local host and a proxy-egress srflx line."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 17
---


# about:webrtc: read your real ICE candidates

A public leak site gives you a verdict and a couple of IP strings. Firefox ships a
page that gives you the raw material behind that verdict: every
[ICE candidate](https://datatracker.ietf.org/doc/html/rfc8445) the
browser gathered, its type, its address, its priority, and the timeline of when each
one arrived. It is `about:webrtc`, it is built in, it needs no extension, and it is
the first place to look after any change to a WebRTC or proxy setting.

This page is how to read it, what the three interesting candidate states look like
line by line, how to pair it with `about:config`, and how to reproduce the same read
from a Playwright script so a bad candidate fails a test instead of a real session.

## Why a leak site is not enough

A leak site aggregates. It runs the gathering for you, picks the addresses it thinks
matter, and prints something like "Local IP" and "Public IP" with a green or red
badge. That summary hides exactly the distinctions you need while debugging, because
three very different underlying states can round to the same badge:

- A host candidate carrying a raw `192.168.x.x` LAN address. This is a real leak, and
  it is *worse* than a stock browser, which hides that address by default.
- A host candidate carrying an obfuscated `<uuid>.local` name. This is the correct
  state, and it is what a stock Firefox produces.
- A server-reflexive (`srflx`) candidate whose address is your proxy egress, with a
  plausible NAT port and a priority in the range a real candidate uses.

A "No Leak" badge can be produced by the second and third states working correctly,
and it can also be produced by WebRTC being suppressed to the point that nothing is
gathered at all, which is [its own detectable signal](webrtc-leak-proxy.md). The
badge does not tell you which. The candidate list does.

## Opening about:webrtc and reading a session

Type `about:webrtc` into the address bar. The page is empty until a peer connection
exists, so open a page that creates one first, then come back to it. Each connection
gets a collapsible block; expand it and the two things worth reading are:

- **ICE Stats / the candidate table.** Every gathered candidate with its type
  (`host`, `srflx`, `relay`), its address, its port, its protocol and its priority.
- **The ICE gathering log / timeline.** Timestamped lines showing when gathering
  started, when each candidate was added, and when it finished. A candidate that
  arrives suspiciously early (time zero, no round trip) is as much a tell as a wrong
  address, and the timeline is the only view that shows arrival time.

You are reading for three things: that a host candidate exists and is masked, that a
server-reflexive candidate exists and points at the exit you expect, and that the
timeline looks like a browser talking to a STUN server rather than fabricating an
answer instantly.

## The three states a leak site collapses into one

Three candidate states, plus a suppressed section that gathers nothing, all round to
the same one-word leak-site badge. The table below is how each reads in the candidate
list; the lines underneath show the exact shape of each one.

| State | Candidate line | Leak-site badge | Verdict |
|---|---|---|---|
| Leak | `typ host` with a raw `192.168.x.x` LAN address | "Local IP" / red warning | Real leak, worse than stock |
| Correct host | `typ host` with a `<uuid>.local` name | "No Leak", local IP shown as "-" | Shipped, correct |
| Correct srflx | `typ srflx` with your proxy egress address | contributes to "No Leak" | Correct exit |
| Suppressed | nothing gathered at all | "No Leak" | Detectable tell, a FAIL |

Here is what each state looks like as a candidate line, so you can recognise it on
sight in the table.

**Leak (worst case).** The host candidate carries the machine's real LAN address:

```text
candidate:0 1 UDP 2122252543 192.168.1.24 51894 typ host
```

If you see a private `192.168.x.x`, `10.x.x.x` or `172.16-31.x.x` address on a `typ
host` line, WebRTC is handing out your internal network address. A stock browser does
not do this; it masks it. Seeing it means a masking setting is off, and you are now
more identifiable than a default install.

**Correct host.** The same candidate with the address replaced by a per-session mDNS
name:

```text
candidate:0 1 UDP 2122252543 3f1c9a7e-....local 51894 typ host
```

The `.local` name is what a stock Firefox emits by default, and a leak site reads it
as "No Leak" with the local IP shown as "-". This is the shipped state here: the
patched engine keeps host-address obfuscation on, so the host line is a `.local` name
and never a raw LAN IP.

**Correct srflx.** The server-reflexive candidate carries your public exit and a NAT
port:

```text
candidate:1 1 UDP 1686052863 203.0.113.45 49731 typ srflx raddr 0.0.0.0 rport 0
```

Behind a proxy, that address must be the proxy egress, not your real public IP and not
a private address. The port should sit in the ephemeral NAT range and the priority
should look like a real `srflx` priority rather than a round, hand-picked number.
Getting the [priority and foundation right, not just the address](webrtc-ice-candidate-spoofing.md),
is what separates a candidate that survives inspection from one that only survives a
leak badge.

## Pairing it with about:config

`about:webrtc` shows you the result; `about:config` shows you the switches that
produced it. Open a second tab on `about:config` and filter on two prefixes.

- **`media.peerconnection.*`** are the standard Firefox WebRTC prefs. The one that
  governs the host state above is `media.peerconnection.ice.obfuscate_host_addresses`:
  `true` gives you the `.local` name, `false` gives you the raw LAN IP. If
  `about:webrtc` shows a `192.168.x.x` host, this is the first pref to check.
- **`zoom.stealth.webrtc.*`** is where the product exposes its own WebRTC controls,
  including the egress the synthetic `srflx` is built from and the IPv6 host
  suppression. These are set per session by the launcher from the proxy you pass, so
  in normal use you read them here to confirm the wrapper resolved the right exit,
  rather than editing them by hand.

The workflow is: change one pref, reload the page that builds the connection, refresh
`about:webrtc`, and read the candidate that changed. One switch at a time keeps cause
and effect legible, which is the same discipline that makes a bisect a bisect.

## Driving the same session from Playwright

The manual read is for exploring. Once you know which candidate line matters, you want
it as an assertion that runs every session, and `InvisiblePlaywright` returns a real
Playwright `Browser`, so the same code that opens `about:webrtc` can also gather
candidates programmatically.

First, opening the built-in page under a seeded, proxied session so you can eyeball it:

```python
from invisible_playwright import InvisiblePlaywright

PROXY = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
    page = browser.new_page()
    # A peer connection has to exist before about:webrtc has anything to show.
    page.goto("https://example.com")
    page.evaluate("new RTCPeerConnection().createDataChannel('x')")

    inspector = browser.new_page()
    inspector.goto("about:webrtc")
    print("Expand the session to read candidates + the gathering timeline")
    inspector.wait_for_timeout(3000)
```

Then the same read as data, so a wrong candidate fails a check instead of a session.
This gathers the candidate strings the page itself would list and prints them:

```python
from invisible_playwright import InvisiblePlaywright

PROXY = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

GATHER = """
async () => {
  const pc = new RTCPeerConnection();
  pc.createDataChannel("probe");
  const lines = [];
  pc.onicecandidate = (e) => { if (e.candidate) lines.push(e.candidate.candidate); };
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((r) => setTimeout(r, 3000));
  pc.close();
  return lines;
}
"""

with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    candidates = page.evaluate(GATHER)

    host = [c for c in candidates if "typ host" in c]
    srflx = [c for c in candidates if "typ srflx" in c]

    for c in candidates:
        print(c)

    # Assert the positive shape, not the absence of a leak.
    assert host and all(".local" in c for c in host), "host must be an mDNS .local name"
    assert srflx, "a proxied session must still produce a server-reflexive candidate"
    assert not any(
        p in c for c in host for p in ("192.168.", "10.", "172.16.")
    ), "no raw LAN address on a host line"
```

Because the identity is seed-derived, `seed=42` gives the same session every run, so a
candidate that fails this on one run fails it on the next, and you can [replay the
exact browser that produced it](quickstart.md) instead of chasing a random draw.

## A before-and-after you can measure

The distinction is not theoretical. With host obfuscation off, a leak page reports the
host candidate as the real LAN address and shows the red "WebRTC exposes your Local IP"
warning; the same page on the shipped baseline reports "No Leak" with the local IP as
"-", because the host line is now a `<uuid>.local` name and the only routable address
on the page is the proxy egress on the `srflx` line. Same session, one pref, and the
difference is the whole point of reading candidates instead of badges.

Assert the presence of the right candidate, not the absence of a wrong one. A WebRTC
section that comes back empty passes every "no leak" test and is itself a signal, which
is the same false pass that a [verdict-based test hides and a field comparison
catches](how-to-test-bot-detection.md).

## Conclusion

`about:webrtc` turns WebRTC from a badge into a list you can read. It shows the host
candidate so you can confirm it is a `.local` name and not a raw LAN IP, the
server-reflexive candidate so you can confirm it is your proxy egress with a plausible
port and priority, and the gathering timeline so you can confirm the answers arrived
the way a browser produces them rather than instantly. Pair it with `about:config` to
see the switches behind each line, drive the same read from Playwright to make it a
test, and change one pref at a time so every difference has a cause.

## Short answers to the questions that lead here

**What is about:webrtc for?** It is Firefox's built-in inspector for live peer
connections: the full ICE candidate list, each candidate's type and address and
priority, and the gathering timeline. It shows the detail a leak site summarises away.

**Why is my host candidate a 192.168 address?** Host-address obfuscation is off. A
stock Firefox emits a `<uuid>.local` name instead, and a raw LAN address is a real
leak. Check `media.peerconnection.ice.obfuscate_host_addresses` in `about:config`.

**What should the srflx candidate show behind a proxy?** Your proxy egress address,
with a NAT-range port and a realistic priority. Not your real public IP, not a private
address, and not a suspiciously round priority value.

**A leak site says "No Leak" but I still get flagged. Why?** "No Leak" can mean the
candidates are correct, or it can mean WebRTC was suppressed and gathered nothing,
which is itself detectable. The candidate list tells you which; the badge does not.

**Can I read about:webrtc from Playwright?** You can navigate to it with `page.goto`,
and you can gather the same candidate strings programmatically with `page.evaluate`
over a real [`RTCPeerConnection`](https://developer.mozilla.org/en-US/docs/Web/API/RTCPeerConnection),
which turns the manual read into an assertion.

**Why does an empty WebRTC section count as a failure?** Because a real browser behind
NAT always emits at least a host and a server-reflexive candidate. Emitting nothing is
not neutral; it is a shape no ordinary browser has, so assert presence, not absence.

## Sources

- Firefox's built-in `about:webrtc` and `about:config` pages, read directly rather
  than through a third-party summary.
- This project's WebRTC release gates and their per-event diagnostics, including the
  incident where a negative-only check passed a fully blocked WebRTC section behind a
  proxy, and the host-obfuscation fix that moved the host line from a raw LAN IP to a
  `.local` name.

**See also:** [what actually leaks through a proxy](webrtc-leak-proxy.md),
[the srflx fields beyond the address](webrtc-ice-candidate-spoofing.md), and
[everything that has to agree with your exit IP](timezone-proxy-mismatch.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Every candidate line in
this page is one I have read off about:webrtc while chasing a leak that a badge missed.*
