---
title: "WebRTC has no ICE candidates behind a proxy"
description: "An empty WebRTC ICE candidate list behind a proxy reads as tampering. Why residential proxies drop the UDP that STUN needs, and how the fix keeps it non-empty."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 14
---


# WebRTC has no ICE candidates behind a proxy

The short version: a residential proxy tunnels TCP and drops UDP, STUN needs UDP, so
the gathering that should produce a public candidate produces nothing. A real Firefox
behind a home router never comes back empty. So "empty" does not read as "private", it
reads as "manipulated".

You route the browser through a residential proxy, open a detection page, and the
WebRTC section comes back empty. No local candidate, no server reflexive candidate,
nothing. It feels like a win, because nothing leaked. It is not a win. An empty
candidate list is one of the loudest signals a fingerprinting page can read, and this
page is about why the emptiness happens and why it is the tell.

## The empty list is the tell, not the leak

Most WebRTC advice is written around the opposite failure: the browser exposing your
real local address next to the proxy's. That is a real problem and it has [its own
page](webrtc-leak-proxy.md). But the failure people hit behind a working proxy is the
mirror image, and it is worse because it looks like success.

A browser that has gathered [ICE candidates](https://www.rfc-editor.org/rfc/rfc8445)
emits at least two kinds. A host candidate,
which is the machine's own address (or a masked `.local` form of it), and a server
reflexive candidate, the `srflx`, which is the public address a STUN server saw the
traffic arrive from. Behind NAT, which is every home connection, a real Firefox always
produces a srflx, because that is the entire point of STUN: to discover the public side
of the NAT.

So a detector does not need to inspect the candidates to be suspicious. It counts them.
Zero candidates under a desktop user agent, on a page that just asked for them, is a
state a normal browser on a normal network does not reach. A model does not have to know
why the list is empty to score the emptiness. This is the general rule that shows up
across every surface: [a suppressed signal is itself a
signal](how-to-test-bot-detection.md), and asserting the absence of a leak is not the
same as asserting the presence of a real value.

## Why a residential proxy makes STUN return nothing

STUN, the [Session Traversal Utilities for NAT protocol](https://www.rfc-editor.org/rfc/rfc8489),
works over UDP. It sends a small datagram to a STUN server and reads back the
public address and port the server observed. That round trip is how the srflx candidate
gets its value.

A SOCKS5 residential proxy, of the kind sold as a pool of home IP addresses, almost
always carries TCP and refuses UDP. Even the SOCKS5 [UDP ASSOCIATE command](https://www.rfc-editor.org/rfc/rfc1928), which
the protocol defines precisely for this, is typically unimplemented at the exit. So when the browser tries to
send its STUN datagram through the tunnel, the datagram has nowhere to go. The STUN
transaction times out. No public address comes back, so no srflx candidate is formed.

This is not a browser bug and it is not something a JavaScript patch can fix, because
the packet genuinely cannot traverse the tunnel. It is a property of the transport you
chose. If you want to understand why UDP and TCP behave so differently through these
tunnels, [SOCKS5 versus HTTP proxying](socks5-vs-http-proxy-browser.md) covers what each
one actually forwards.

The consequence for stealth is direct. Do nothing, and the proxy silently strips your
one guaranteed public candidate, and the list that remains is either short or empty in a
way a real network never produces.

## The second cause, which is subtler: the default-route probe

There is a second way to reach zero candidates, and it fires even before STUN is
attempted. It is worth knowing because it explains an empty list on pages that never
prompted for any device.

A page that has not been granted camera or microphone permission does not get the full
candidate set. To limit what an untrusted page can learn about your interfaces, the
browser switches into a reduced mode, one of the [WebRTC IP address handling
modes](https://www.rfc-editor.org/rfc/rfc8828), in which it only offers the address of the
default route: the single interface your traffic would leave by. Deciding which interface that
is requires a quick internal probe. The browser opens a UDP socket and "connects" it to
the remote address of the document, not to send anything, but to ask the operating
system which local address that route would use.

Behind a SOCKS proxy, that remote is not reachable directly. The probe is UDP, it is
aimed at an address the proxy has interposed itself in front of, and it fails. In the
reduced mode, a failed default-route probe is fatal to gathering: it returns a failure,
and the whole candidate list comes back empty. So on an ordinary page, one with no media
prompt at all, the very step that is supposed to pick a safe address to advertise
collapses into advertising nothing.

That is the part that makes the emptiness so easy to ship by accident. It is not the
exotic case of a page requesting your webcam. It is the default case, and it is exactly
the case a detection page exercises.

## What a real Firefox emits, and what the fix guarantees

The reference is a stock Firefox behind a home router. Open a WebRTC probe in one, on a
normal connection, and you get a host candidate and a server reflexive candidate whose
address is your public IP. Non-empty, with a srflx present. That is the shape a detector
expects, and matching it is the goal.

The three states a detector can observe, side by side:

| WebRTC state | Candidate count | Server reflexive (srflx) | Reads as |
|---|---|---|---|
| Stock Firefox behind a home NAT | Non-empty | Present, carries the public IP | Normal |
| Untouched engine via a SOCKS5 proxy | Short or empty | Missing (UDP dropped) | Manipulated |
| This project's fix via the same proxy | Non-empty | Present, carries the proxy egress IP | Normal |

The fix in this project targets both failure paths so the observed shape matches that
reference through a proxy:

- When the default-route probe fails, gathering does not abort. It falls back to the
  local addresses that were already collected and filtered, so the reduced mode still
  has something valid to work from instead of returning empty.
- The host addresses are masked into the `.local` form, so the local network is not
  exposed even though the list is non-empty.
- A server reflexive candidate is synthesized from the proxy's own egress address, the
  address the browser is genuinely reaching the internet from, so the srflx a real NAT
  would have produced is present and correct rather than missing. The realness of that
  candidate, its priority and foundation matching what a genuine gather produces, is
  covered in [ICE candidate spoofing](webrtc-ice-candidate-spoofing.md).

The measured result, through a SOCKS5 residential exit: the host candidate reads as
`.local`, the server reflexive candidate carries the proxy egress IP, the public IP the
page reports equals that egress, and [CreepJS](creepjs-explained.md) shows the WebRTC
panel complete and unblocked rather than recording a suppressed probe. Zero of the
"empty list" and "blocked" states that the untouched paths produce. None of this changes
the behaviour without a proxy, where the real gather runs normally.

## Reproduce it and read the candidates yourself

The only way to trust any of this is to read the candidate list on the path you deploy
on, through the proxy you deploy with, not on localhost. Here is the probe. It creates a
peer connection, waits for gathering to complete, and returns the raw candidate lines so
you can count them and inspect the addresses.

```python
from invisible_playwright import InvisiblePlaywright

GATHER_ICE = """
() => new Promise((resolve) => {
    const pc = new RTCPeerConnection();
    const lines = [];
    pc.onicecandidate = (e) => {
        if (e.candidate) {
            lines.push(e.candidate.candidate);
        } else {
            resolve(lines);   // null candidate marks gathering complete
        }
    };
    pc.createDataChannel("probe");
    pc.createOffer().then((offer) => pc.setLocalDescription(offer));
    setTimeout(() => resolve(lines), 5000);  // do not hang if it stalls
});
"""

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    candidates = page.evaluate(GATHER_ICE)

    print("candidate count:", len(candidates))
    has_srflx = any(" typ srflx " in c for c in candidates)
    print("has server reflexive:", has_srflx)
    for line in candidates:
        print(" ", line)

    assert candidates, "empty candidate list: the proxy dropped STUN and nothing filled the gap"
    assert has_srflx, "no srflx: a real browser behind NAT always emits one"
```

Note that the assertions are written in the positive form. They fail on an empty list
and on a missing srflx, because those are the states a detector reads as manipulation.
A test that only checked "my real IP did not appear" would pass on a completely empty
list, which is the false green that started this whole investigation.

Run it more than once, and compare against the same probe in a stock Firefox on the
same machine, since the interesting differences are the ones between the two reports
rather than the raw numbers in either. If you need to confirm the exit address the
proxy is actually giving you before trusting the srflx value, and to get authentication
right for a SOCKS5 pool, see [SOCKS5 proxy
authentication](playwright-socks5-proxy-authentication.md).

A minimal install to run the snippet:

```bash
pip install invisible-playwright
```

## Conclusion

An empty WebRTC candidate list is not the safe outcome it looks like. It is a state a
real browser on a real network does not reach, and a detection page can score the
emptiness without ever inspecting a single candidate. The root cause is the transport:
a residential proxy carries TCP and drops the UDP that STUN depends on, so the one
public candidate a NAT would guarantee never forms, and on ordinary pages the
default-route probe fails the same way before STUN is even tried.

The fix is not to hide WebRTC but to make it produce the shape a genuine browser
produces through the same proxy: a masked host candidate, and a server reflexive
candidate carrying the true egress address. Look real, which means non-empty and
consistent, rather than merely quiet. And whichever tool you use, read the candidate
list on the deployed path instead of trusting that no leak means all is well.

## Short answers to the questions that lead here

**Why does WebRTC return no ICE candidates behind my proxy?** Because a residential
SOCKS5 proxy carries TCP and drops UDP, STUN needs UDP to discover your public address,
so the server reflexive candidate never forms and the list comes back short or empty.

**Is an empty candidate list safe because nothing leaked?** No. It is a tell. A real
Firefox behind NAT always emits at least one server reflexive candidate, so zero
candidates is a state normal browsers do not reach, and detectors read it as
manipulation.

**Why is the list empty even on a page that never asked for my camera?** Pages without
media permission use a reduced mode that only advertises the default-route address, and
the probe that finds that route is itself UDP. Behind SOCKS it fails and gathering
returns empty.

**Can I fix this from JavaScript?** No. The STUN datagram genuinely cannot traverse a
UDP-blocking tunnel, and the default-route probe genuinely fails. It has to be handled
below the page, in the engine, by falling back and synthesizing the candidate the proxy
prevented.

**What should the candidates look like through a working proxy?** A host candidate in
the masked `.local` form and a server reflexive candidate whose address equals the
proxy egress IP, with the public IP the page reports matching that same egress.

**How do I test it without fooling myself?** Assert the list is non-empty and that a
srflx is present, run it through the real proxy rather than localhost, repeat it, and
diff against a stock Firefox on the same machine.

## Sources

- This project's WebRTC release gate and its per-event gathering diagnostics, including
  the run where a negative-only assertion passed on a browser that was returning nothing
  behind a proxy.
- Measurements through a SOCKS5 residential exit against public detection suites,
  comparing candidate count, host form and server reflexive address before and after the
  fix, against a stock Firefox reference on the same machine.
- [RFC 8489, Session Traversal Utilities for NAT (STUN)](https://www.rfc-editor.org/rfc/rfc8489)
  - STUN runs over UDP to discover the public address behind a NAT.
- [RFC 1928, SOCKS Protocol Version 5](https://www.rfc-editor.org/rfc/rfc1928) - defines the
  UDP ASSOCIATE command that a residential exit typically leaves unimplemented.
- [RFC 8445, Interactive Connectivity Establishment (ICE)](https://www.rfc-editor.org/rfc/rfc8445)
  - the candidate types, including the server reflexive candidate a NAT produces.
- [RFC 8828, WebRTC IP Address Handling Requirements](https://www.rfc-editor.org/rfc/rfc8828)
  - the reduced default-route modes a page without media permission runs in.

**See also:** [WebRTC leaks through a proxy](webrtc-leak-proxy.md) for the opposite
failure of exposing your real address, [ICE candidate spoofing](webrtc-ice-candidate-spoofing.md)
for how the synthetic candidate is made to look real, and [testing whether your browser
is detected](how-to-test-bot-detection.md) for the compare-against-stock method used
throughout.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-list false
green on this page cost us a release gate that looked green for weeks.*
