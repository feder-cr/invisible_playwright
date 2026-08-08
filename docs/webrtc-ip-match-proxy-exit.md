---
title: "WebRTC IP that matches the proxy exit, by design"
description: "WebRTC IP matching the proxy exit: the server-reflexive candidate address equals the exit IP, the real NAT port is preserved, all from one shared lookup."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 15
---


# WebRTC IP that matches the proxy exit, by design

WebRTC is kept matching the proxy exit by discovering the exit address once, then
swapping it into the real [server-reflexive](https://developer.mozilla.org/en-US/docs/Web/API/RTCIceCandidate/type) (`srflx`) candidate in place while preserving
the actual NAT port. HTTP and WebRTC then report the same public address, and the
candidate still reads as a genuine router mapping rather than a fabricated one.

A browser behind a proxy has two ways to say where it is. The HTTP request exits
through the proxy, so the visible address is the proxy's. WebRTC asks a separate
question over a separate path, and if nobody has arranged otherwise it answers with
the machine's own public address. When those two disagree, the session is telling two
stories, and the disagreement is cheaper to detect than any single fingerprint field.

This page is about the arrangement that keeps them agreeing: how the exit address is
discovered once, how it is placed into the WebRTC candidate, and why the real network
port is left exactly as the machine found it instead of being made up.

## What "matching" actually means

Matching means the server-reflexive (`srflx`) candidate carries the same public address as
the HTTP exit, paired with a real, non-zero port next to it, not just the address on its own.
That candidate is what a real browser behind a home router produces: the router rewrites the
source address of an outbound packet to the household's public address and to some port the
router chose, and a [STUN](https://datatracker.ietf.org/doc/html/rfc5389) server on the far
side reports that rewritten pair back. The pair is the NAT mapping, and a real `srflx` is
always a public address with a plausible port next to it.

So matching is not just "show the proxy's address". It is: show the proxy's address in
a candidate that still looks like a NAT mapping. An address with no port, a port of
zero, or a port that never varies is its own tell, and a page that reads the candidate
string sees all of it. The goal is a `srflx` whose address equals the HTTP exit and
whose shape is indistinguishable from an ordinary router mapping. For the wider list of
what a leak check reads out of that candidate, see
[WebRTC through a proxy](webrtc-leak-proxy.md).

## One lookup, shared with the timezone

The exit address is not guessed and not read from the machine. It is discovered by
making a single round trip out through the same proxy the session uses, and reading back
the address the far side saw. That is the address the rest of the world will see for
this session, by definition, because it is the path the session's own traffic takes.

The same round trip also answers a second question. The timezone a session should claim
follows from where its traffic exits, so the exit address and the timezone are resolved
together from one lookup rather than two. There is no second network call, and no window
in which the WebRTC address and the timezone could be derived from different exits. If
you have read [why the timezone has to match the proxy](timezone-proxy-mismatch.md),
this is the other half of the same lookup: both surfaces are pinned to the one exit that
was actually measured.

You do not call any of this yourself. Passing a proxy is enough, and everything derived
from the exit is set up before the browser starts:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/")
    # The HTTP exit, the timezone, and the WebRTC srflx address
    # were all derived from one lookup through this proxy.
```

## Swap the address, keep the NAT port

What happens is a swap, not a fabrication. The machine still performs the real STUN
exchange and learns a real mapped pair. The address half of that pair is replaced, in
place, with the measured exit address. The port half is left exactly as the network
produced it. The candidate that reaches the page is therefore a genuine mapping with one
field corrected: the public address now equals the proxy exit, and the port is the real
one the network assigned, not a constant and not a zero.

The naive alternative would be to fabricate a whole candidate instead: pick the exit
address, attach some port, and hand it to the page. That produces a matching address and
an unconvincing mapping, because the invented port carries none of the structure a real
router mapping has.

That distinction is the whole point of the design. A port that is preserved from a real
exchange varies the way a real one does and pairs with the address the way a real one
does. A [hand-built candidate](webrtc-ice-candidate-spoofing.md) has to reproduce all of
that from nothing and usually does not. Keeping the real port means there is nothing to
reproduce, because it was never synthetic in the first place.

## Why the value travels as an environment variable first

The exit address is delivered through the environment first, with the preference as a
fallback, for a timing reason. A child process inherits its environment at the instant
it is started, so the value is present before the process has executed a single line.
The preference channel synchronises slightly later, after the parent has finished
propagating it. WebRTC gathering can begin early, and a value that is not there yet is a
value the candidate cannot use. Delivering it as inherited environment means it is
available from the first moment gathering could ask for it, with the preference behind it
so nothing depends on that timing being tight.

There are two channels that could have carried the exit address into the part of the
browser that builds the candidate: a preference, synchronised from the parent process
after the child is running, or the child's environment, fixed at the moment it is
launched. The environment wins because of when each one becomes available, not because
of anything else about the two.

You never set either channel by hand. The measured exit is written into both before
launch; an explicitly supplied value wins if you provide one, and with no proxy nothing
is injected at all, because the machine's own STUN answer is already the truth.

## Measuring it: read the srflx candidate through the proxy

The only test worth trusting here is the one that reads the candidate the page actually
receives, through the proxy the job uses, and confirms two things: the `srflx` address
equals the exit, and the port is real. Assert the presence of the right candidate rather
than the absence of a leak, because an empty gather passes every negative check while
proving nothing. That principle is the subject of
[how to test whether your browser is detected](how-to-test-bot-detection.md), and WebRTC
is where it bites hardest.

```python
import json
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

GATHER = """
() => new Promise((resolve) => {
  const pc = new RTCPeerConnection();
  const cands = [];
  pc.onicecandidate = (e) => {
    if (e.candidate) { cands.push(e.candidate.candidate); }
    else { resolve(cands); }        // null candidate == gathering complete
  };
  pc.createDataChannel("probe");
  pc.createOffer().then((o) => pc.setLocalDescription(o));
})
"""

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()

    # The exit address, read back through the same proxy the browser uses.
    exit_ip = page.goto("https://api.ipify.org?format=json") and \
        json.loads(page.locator("pre, body").inner_text())["ip"]

    page.goto("https://example.com/")
    candidates = page.evaluate(GATHER)

    srflx = [c for c in candidates if "typ srflx" in c]
    assert srflx, "no server-reflexive candidate: an empty gather is a FAIL, not a pass"

    for line in srflx:
        parts = line.split()
        addr, port = parts[4], int(parts[5])   # candidate ... <addr> <port> typ srflx
        assert addr == exit_ip, f"srflx {addr} does not match exit {exit_ip}"
        assert port != 0, "srflx port is zero: that is a fabricated mapping"
        print("srflx", addr, port, "matches exit and carries a real NAT port")
```

Run it at least ten times and from the machine that runs production, not from your
laptop. On the shipped build behind a proxy this reports a single `srflx` whose address
equals the exit and whose port is a real, non-zero value that changes between runs the
way a router's chosen port does. The host candidate alongside it is an `.local`
name rather than a LAN address, which is what a current browser emits and what a
[reader of the candidate list](playwright-detected-as-bot.md) expects to see.

## Conclusion

Keeping WebRTC aligned with the proxy is not a matter of hiding a value. It is a matter
of making one field of a real thing correct while leaving the rest of that real thing
alone. The exit is measured once, on the path the session actually uses, and that one
measurement pins both the WebRTC address and the timezone. The address is swapped into a
genuine STUN result with the network's own port kept intact, so the candidate reads as a
NAT mapping because it is one. And the value arrives through the channel that is ready
first, so gathering never has to proceed without it.

The test that confirms all of this is the same in shape as every other good stealth
test: read the positive signal the page receives, through production inputs, more than
once. A `srflx` that is present, matches the exit, and carries a real port is the pass.
Nothing arriving at all is the failure that a leak-only check would have called clean.

## Short answers to the questions that lead here

**Why does WebRTC show a different IP than my proxy?** Because WebRTC discovers the
machine's own public address over its own path unless something makes it use the exit
instead. The fix is to derive the candidate's address from the measured proxy exit, not
to disable WebRTC, since a browser that gathers nothing is itself unusual.

**Is the WebRTC port faked too?** No, and that is deliberate. The real STUN exchange runs
and the real network port is preserved; only the public address is swapped. A made-up or
zero port is a tell, so the mapping keeps the port the network actually assigned.

**How is the exit address found?** By one round trip out through the same proxy the
session uses, reading back the address the far side saw. That same lookup also fixes the
timezone, so both are derived from the one exit that was actually measured.

**Do I have to configure any of this?** No. Pass a proxy and the exit is measured and
applied before the browser launches. With no proxy nothing is injected, because the
machine's own STUN answer is already correct.

**How do I confirm it is working?** Read the ICE candidates through the proxy and check
that a server-reflexive candidate is present, its address equals the exit, and its port
is non-zero. Assert presence, not the absence of a leak, and repeat the run.

**Why can an empty WebRTC result still fail a check?** Because a leak test that only
looks for a wrong address is satisfied by no address at all. A blank or blocked gather
passes the negative check while looking nothing like a real browser behind NAT.

**See also:** [reading a WebRTC leak behind a proxy](webrtc-leak-proxy.md),
[how ICE candidates get spoofed](webrtc-ice-candidate-spoofing.md), and
[keeping the timezone matched to the exit](timezone-proxy-mismatch.md).

## Sources

- This project's WebRTC handling in the patched engine and its wrapper: the single
  exit-address lookup shared with timezone resolution, the in-place address swap that
  preserves the real NAT port, and the environment-first delivery that beats the
  preference sync at startup.
- The project's release gates for WebRTC, which assert the positive form of the
  candidate (present, matching the exit, real port) rather than the absence of a leak,
  after an earlier negative-only gate passed a browser that was gathering nothing.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The exit is measured once,
on the path the traffic takes, and everything else is kept honest around it.*
