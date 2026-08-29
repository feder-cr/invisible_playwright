---
title: "WebRTC IPv6 leak: why a proxy does not stop it"
description: "A SOCKS proxy carries only TCP, so an IPv6 host still emits a WebRTC candidate with its real global address, and the old disableIPv6 pref no longer stops it."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 11
---


# WebRTC IPv6 leak: why a proxy does not stop it

You routed everything through a proxy, the page shows the proxy's IPv4, and you
still got recognised across sessions. The address that gave you away was never on
the surface you were watching. It was an IPv6 host candidate emitted by WebRTC, on
a transport your SOCKS proxy never carried, and the pref everyone reaches for to
turn it off does nothing in current Firefox.

This page is why that happens, how to see it in your own setup, and what actually
removes it.

## The two transports, and which one your proxy carries

A [SOCKS5](https://datatracker.ietf.org/doc/html/rfc1928) proxy tunnels TCP. That is the whole contract. Your HTTP requests, your
TLS, your WebSocket traffic all ride TCP and all exit at the proxy, which is why
the page reads the proxy's address and why the server reflexive WebRTC candidate
(the one derived from a STUN round trip) shows the proxy's egress too.

WebRTC does not only report the address a STUN server sees. Before any of that, it
enumerates the host's own network interfaces and emits a [**host candidate**](https://datatracker.ietf.org/doc/html/rfc8445) for
each usable local address. That enumeration is local. It does not travel through
your proxy, because there is nothing to travel: the browser is reading its own
interface list off the operating system.

On an IPv4-only host behind a proxy this is harmless. The host candidate is a
private LAN address, and a real browser masks it as an `<uuid>.local` mDNS name
anyway, which is [the shape a genuine Firefox produces](webrtc-leak-proxy.md). But
a host with working IPv6 has a **global** IPv6 address on that interface, routable
from anywhere, unique to the machine. WebRTC emits it as a host candidate exactly
as it emits the LAN one, and now the page can read a stable global address that
your proxy never touched.

That is the leak. The IPv4 srflx candidate shows the proxy; the IPv6 host
candidate shows the machine. Both are in the same candidate list, and a detector
that reads the list sees both.

| ICE candidate | Where the address comes from | Rides TCP? | Does the proxy cover it? | What it reveals |
|---|---|---|---|---|
| `typ srflx` (server reflexive) | A STUN round trip | Yes | Yes | The proxy's egress IPv4 |
| `typ host`, IPv4 | Local interface enumeration | No | No | A private LAN address, masked as `<uuid>.local` |
| `typ host`, IPv6 | Local interface enumeration | No | No | The machine's global, routable IPv6 |

Only the first row travels a transport the proxy carries. The two `typ host` rows
are read straight off the operating system's interface list, which is why the
proxy never sees them and the IPv6 one exposes a stable global address.

## Why the standard "disable IPv6" pref does nothing now

Setting `media.peerconnection.ice.disableIPv6` does not stop the IPv6 WebRTC leak
in current Firefox, because the pref was disconnected from the code path that emits
host candidates. The advice you will find everywhere is to toggle
`media.peerconnection.ice.disableIPv6` in about:config, or to flip the matching
Playwright/Selenium preference. That advice is stale.

We went looking for where current Firefox reads that pref during ICE gathering.
It does not. A search across the WebRTC transport code finds the pref honoured only
in two unrelated places: a DNS resolver flag that suppresses AAAA lookups, and a
socket-level flag on the TCP path. Neither runs during the interface enumeration
that produces host candidates. The pref that is named as if it governs IPv6 in
WebRTC no longer reaches the code that emits IPv6 host candidates.

So setting it is not a smaller version of the fix. It is no version of the fix.
The candidate is generated on a code path the pref was disconnected from, and the
about:config toggle changes a value that nothing downstream reads. This is the same
class of trap covered in [testing for presence rather than absence](how-to-test-bot-detection.md):
a setting that looks like it is doing something, on a signal that never moves.

## What the leak looks like in a report

You do not need a detector to see it. Ask WebRTC for its own candidates and read
them. Here is a page that gathers candidates through the proxied browser and prints
them, using stock Playwright methods on the real `Browser` object:

```python
from invisible_playwright import InvisiblePlaywright

PROXY = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

GATHER = """
() => new Promise((resolve) => {
    const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
    const cands = [];
    pc.onicecandidate = (e) => {
        if (e.candidate === null) { resolve(cands); return; }
        cands.push(e.candidate.candidate);
    };
    pc.createDataChannel("x");
    pc.createOffer().then((o) => pc.setLocalDescription(o));
    setTimeout(() => resolve(cands), 4000);
})
"""

with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    for line in page.evaluate(GATHER):
        print(line)
```

Each printed line is one ICE candidate. The `typ host` lines are the local
interface addresses; the `typ srflx` lines are what STUN reported. On a leaking
setup you will see a `typ host` line carrying a colon-bearing global IPv6 address
next to the srflx line that carries the proxy's IPv4. That IPv6 string is the
machine, printed to the page.

Read the same list from a stock browser on the same host and compare the two, which
is the [only reliable way to tell a real value from a spoofed one](how-to-test-bot-detection.md).
If the IPv6 host line is present in both, it is your machine leaking, not the tool.

## What actually stops it, and how we proved it

Because the pref is disconnected, the only place to remove the candidate is the
gathering layer itself, below where the pref used to sit. The engine that
invisible_playwright drives filters IPv6 addresses out of the interface
enumeration before any candidate is built from them, on a code path shared by
Windows and Linux, so an IPv6-capable host gathers as if it were IPv4-only. The
synthetic IPv4 srflx that shows the proxy's egress is untouched; only the IPv6
host and the IPv6 srflx derived from it are dropped.

We did not take that on faith, because a signal that is simply absent could be
absent for the wrong reason. We ran it as a live A/B on one machine: with the
filter active, the candidate list came back as `{ host IPv4 as <uuid>.local,
srflx = proxy egress }` and nothing else. Turn the filter off and re-gather, and
the real global IPv6 host candidate reappears in the list. Turn it back on and it
is gone again. The candidate tracks the filter and only the filter, which is what
lets us say the filter is what removes it rather than some unrelated timing.

Run the code block above against the shipped engine and the IPv6 `typ host` line
is not there. The IPv4 srflx still is, still pointing at the proxy, so the surface
that should show the proxy shows the proxy and the surface that leaked the machine
shows nothing to leak. That last part matters: a WebRTC section that comes back
completely empty is itself a tell, so the goal is a candidate list that looks like
a real browser behind NAT, not a suppressed one.

## Keep it true when you rotate the exit

The IPv6 filter is a property of the engine, so it holds across every session and
every seed. The thing that does not hold for free is agreement between the proxy
and the rest of the identity. When you [rotate to a new exit](how-to-rotate-proxies-playwright.md),
the srflx candidate follows the new proxy automatically, but the timezone, locale
and geolocation only follow if you let them, and a browser whose WebRTC says one
country while its clock says another is its own signal. That coupling is covered in
[when the timezone does not match the proxy](timezone-proxy-mismatch.md), and it is
worth checking on every rotation rather than once at setup.

## Conclusion

The IPv6 WebRTC leak survives a proxy for a simple reason: the proxy carries TCP,
the leak is on interface enumeration that never uses TCP, and the pref named to
stop it was disconnected from that path in current Firefox. Setting
`media.peerconnection.ice.disableIPv6` changes nothing an ICE candidate is built
from. The fix has to live in the gathering layer, and it has to be verified by
watching the candidate appear and disappear as the filter flips, not by trusting
that an empty result means a solved one. Read your own candidate list, compare it
against a stock browser, and confirm the IPv6 host line is gone while the IPv4
srflx still shows the proxy.

## Short answers to the questions that lead here

**Does a SOCKS5 proxy stop a WebRTC IPv6 leak?** No. A SOCKS5 proxy carries TCP,
and the IPv6 host candidate comes from local interface enumeration that never
touches the proxy, so the machine's global IPv6 is still emitted.

**Does media.peerconnection.ice.disableIPv6 work?** Not for ICE candidate
gathering in current Firefox. The pref is still read for a DNS resolver flag and a
TCP socket flag, but nothing on the host-candidate path reads it, so setting it
does not remove the IPv6 candidate.

**Why does my IPv4 show the proxy but I still got tracked?** Because the srflx
(STUN-derived) candidate rides TCP and exits at the proxy, while the IPv6 host
candidate is your real machine address on a separate transport. One is disguised,
the other is not.

**How do I see the leak myself?** Open an `RTCPeerConnection`, collect
`onicecandidate` events, and print them. A `typ host` line with a colon-bearing
global IPv6 address is the leak. The code block above does exactly this.

**Is an empty WebRTC result the safe outcome?** No. A completely blocked or empty
candidate list is itself a tell. A real browser behind NAT emits a masked host
candidate and a server reflexive one, so the target is a realistic list, not a
silent one.

**Does this come back when I rotate proxies?** The IPv6 filter holds across
rotations, but timezone and locale agreement with the new exit does not follow
automatically, so recheck those each time you change the exit.

## Sources

- This project's WebRTC transport patches and the live A/B toggle described above,
  which made the real IPv6 host candidate appear and disappear on demand and so
  established that the filter, not timing, is what removes it.
- A read of the current Firefox WebRTC transport code confirming the legacy
  disableIPv6 pref is honoured only for DNS resolution and the TCP socket, and
  nowhere on the host-candidate gathering path.
- [RFC 1928, SOCKS Protocol Version 5](https://datatracker.ietf.org/doc/html/rfc1928),
  retrieved 2026-08-29, which defines the CONNECT command a SOCKS5 proxy uses to
  tunnel TCP and says nothing about the interface enumeration a host candidate comes
  from.
- [RFC 8445, Interactive Connectivity Establishment (ICE)](https://datatracker.ietf.org/doc/html/rfc8445),
  retrieved 2026-08-29, which defines the host candidate as a binding to a local
  interface address, gathered independently of any STUN round trip.
- [RFC 8828, WebRTC IP Address Handling Requirements](https://www.rfc-editor.org/rfc/rfc8828),
  retrieved 2026-08-29, which sets the default mode for exposing a host's private
  IPv4 and IPv6 addresses as ICE candidates.
- [The IETF mDNS ICE candidates draft](https://datatracker.ietf.org/doc/html/draft-ietf-mmusic-mdns-ice-candidates-03),
  retrieved 2026-08-29, which documents the `<uuid>.local` name masking applied to the
  IPv4 host candidate referenced above.

**See also:** [what a WebRTC leak through a proxy looks like end to end](webrtc-leak-proxy.md),
[how a synthetic ICE candidate is shaped to match a real browser](webrtc-ice-candidate-spoofing.md),
and [testing for the presence of the right signal instead of the absence of a wrong one](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The IPv6 filter on
this page exists because a live ICE test found a real global address leaking past a
proxy that was carrying everything else.*
