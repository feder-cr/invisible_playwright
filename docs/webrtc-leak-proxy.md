---
title: "WebRTC leak with a proxy in Playwright and Selenium"
description: "The standard WebRTC leak fix, disabling WebRTC or forcing non-proxied UDP off, passes every leak test and fails at the only thing that matters. What actually leaks through a proxy, and a gate of ours that missed it."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 1
---


# WebRTC leak with a proxy in Playwright and Selenium

"My browser leaks my real IP while using a proxy." "Playwright leaks IP with proxy."
"How do I prevent WebRTC leaks in Selenium?" Every answer to those questions ends the
same way: disable WebRTC, or set
`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`.

That advice passes every WebRTC leak test and fails at the only thing that matters.

This page covers what actually leaks, why the standard fix creates a bigger problem
than it solves, and what we learned getting it wrong ourselves, including a release
gate of ours that printed green over a browser whose WebRTC was completely dead.

## What a real Firefox behind NAT emits

Start here, because it is the target and almost nobody states it.

Open a `RTCPeerConnection` on an ordinary consumer machine and you get, at minimum:

- one **host** candidate, whose address is not your LAN IP but a random
  `<uuid>.local` name
- one **srflx** (server reflexive) candidate, whose address is your **public** IP,
  learned from a STUN server

Two candidates. One meaningless, one your public address. That is the shape.

The `.local` part surprises people. Firefox sets
`media.peerconnection.ice.obfuscate_host_addresses` to `true` by default, so the LAN
address is replaced with an mDNS name. It has been the default for years.

## The three failure states, in increasing order of how bad they are

**1. You leak the LAN IP.** The host candidate is `192.168.x.x`. This is what leak
tests are built to catch, and it is the least interesting failure.

**2. You leak the real public IP while claiming another one.** You are on a proxy,
your HTTP traffic exits from one address, and the srflx candidate names a different
one. STUN runs over UDP; a SOCKS5 proxy carries TCP. So the browser asks a STUN
server directly, from the real interface, and the STUN server truthfully answers with
the real address. Two addresses for one session, and the contradiction is the signal,
not either address on its own.

**3. You emit nothing at all.** No candidates, or the connection never completes.
This is the one people create on purpose, and it is the worst of the three.

Almost nobody browses with WebRTC broken. It is on by default and it stays on,
because video calls are normal. A session with zero ICE candidates has told the page
something much more specific than an IP address: it has said *this browser has been
modified*. An unusual IP is a weak signal. A missing capability is a strong one.

## What about `disable_non_proxied_udp` and `media.peerconnection.enabled=false`?

These are the two answers you will find, and they are worth taking separately.

**`--force-webrtc-ip-handling-policy=disable_non_proxied_udp`** is a Chromium flag.
It stops WebRTC from using any network path that does not go through the proxy. Since
a SOCKS5 proxy carries TCP and STUN wants UDP, in practice this means no srflx
candidate is gathered. You have converted failure state 2 into failure state 3.
It does prevent the leak. It also produces a browser that cannot gather a normal
candidate set, which is a condition a real Chrome almost never ends up in.

**`media.peerconnection.enabled=false`** is the Firefox equivalent and it is blunter:
`RTCPeerConnection` stops existing. A page can detect that in one line, without
setting up a connection at all. Firefox has no equivalent of the Chromium handling
policy, which is part of why this preference gets recommended so often.

Both work as leak prevention. Neither is invisible, and which one you want depends
entirely on whether anything is looking. If you are protecting your own privacy while
browsing, disabling WebRTC is a reasonable trade and you should stop reading here. If
you are trying to look like an ordinary browser, you have swapped a leak for a
signature.

The third option is the harder one: let WebRTC run, and make the address it reports
be the address you want it to report.

## The incident: our gate was green and the feature was dead

We had a WebRTC gate. It asserted the sensible things: the host candidate does not
expose the LAN address, no IPv6 candidate appears. Both assertions passed, run after
run.

Behind a proxy, on real pages, WebRTC was returning **blocked**. Both the host and
the STUN section. No candidates at all, failure state 3, shipped.

The gate passed because a dead WebRTC leaks nothing. Every negative assertion is
trivially satisfied by a feature that does not run. We had written a test that could
not distinguish "working correctly" from "not working".

The fix to the test was to assert the positive shape: the page has to **complete**,
the host candidate has to be a `.local` name, the srflx has to be present and has to
equal the proxy's exit address. An empty result is now a failure.

This is the general rule, and it is the most useful thing on this page:

> A check that only asserts the absence of a bad signal will pass when the feature is
> broken. Assert the presence of the right signal instead.

The same shape shows up in [fonts](headless-fonts-differ.md), in [canvas](canvas-fingerprint-noise.md), in [audio](audiocontext-fingerprinting.md). Anything you can suppress will
pass a suppression-based test.

## A second gap, found days later: the test wasn't testing what shipped

Fixing the assertion closed one hole and opened up a more uncomfortable question:
what config had the passing test actually been running against?

The answer was that the test suite set the real-address handling, the derived public
IP, and the IPv6 filter itself, through explicit overrides, and called that combination
"the intended production config" in its own comments. A default session - the one an
actual user gets with no extra arguments - had none of those three switched on in the
wrapper's own shipped baseline. The gate had been green for every run because it never
ran the shipped defaults at all; it ran a configuration nobody's real session used.

That is a different failure from the one above, and worth naming separately: the first
was a test whose assertions couldn't tell working from broken; this one had assertions
that were fine, aimed at a target that wasn't the one being shipped. Both produce the
same outward symptom - a green gate over a feature real users don't actually get - for
opposite reasons. The fix was to make the shipped baseline match what the test already
assumed, not the other way around: derive the public IP from the proxy's own exit
address automatically, turn the real-address handling on by default, and only then
treat the gate as validating a real session instead of a hypothesis about one.

## `media.peerconnection.ice.disableIPv6` does nothing in current Firefox

This one is worth stating plainly, because the preference is still recommended in a
lot of places.

We searched for it in the ICE gathering path of the Firefox 151 source. It is not
read there. What exists is `RESOLVE_DISABLE_IPV6` for DNS and a `DISABLE_IPV6` flag
on TCP sockets. Neither is consulted when candidates are gathered.

The consequence, if your machine has a routable IPv6 address: WebRTC emits a host
candidate carrying that real global IPv6, and a SOCKS5 proxy does nothing about it
because it only carries TCP. Your IPv4 can be perfectly proxied while your IPv6 sits
in the SDP in plain text.

Set the preference, verify it changed nothing, and then go looking for an actual
fix. We ended up filtering IPv6 addresses in the gathering code itself and proving
causality with a toggle: flag on, no IPv6 candidate; flag off, the global IPv6 comes
straight back.

## The thing that actually broke gathering behind a proxy

This took the longest to find and it is not intuitive.

A page that has not been granted camera or microphone permission causes Firefox to
gather in a restricted mode: only the address of the **default route**, instead of
every local interface. That mode is a privacy feature and it is the normal case, since
most pages never ask for a camera.

Finding the default route works by opening a UDP socket "connected" to the remote
address of the document and reading back which local address the OS picked. Behind a
SOCKS proxy that remote host is not directly reachable, so the probe fails, so
gathering fails, so you get zero candidates on every proxied page.

That was the real cause of the blocked state. Not a missing configuration value, not
the STUN server, not the proxy credentials. A privacy path in the browser that
assumes direct connectivity, meeting a setup that does not have it.

Worth knowing even if you never touch browser source: **behind a proxy, WebRTC can
fail for reasons that have nothing to do with WebRTC**, and the error surfaces as
silence.

## If you synthesise a candidate, the details are the fingerprint

Suppose you decide to supply the srflx candidate yourself, so it names your proxy's
exit address. The address is the easy part. The candidate carries several other
fields, and each of them is checkable:

- **Priority** is computed by a documented formula from the candidate type, the
  interface preference and the component id. Our first attempt hardcoded the local
  preference to `0xFFFF`. Real candidates on this stack fall in a range around
  32256 to 32704, so `65535` is a value no genuine candidate ever has. One number,
  and it was enough on its own.
- **Foundation** is an identifier shared by candidates of the same type from the same
  base. Ours drifted between runs, taking a different value each time and sometimes
  colliding with the TCP host candidate's. A real one is stable.
- **The `raddr` and `rport` fields**, and the relationship between a candidate and the
  base it was derived from.

A synthesised candidate is not one value to get right. It is a small structure that
has to be internally consistent and consistent with the other candidates in the same
SDP. This is the same lesson as every other fingerprint surface, arriving in a
different costume.
[The full mechanics of replacing a candidate correctly](webrtc-ice-candidate-spoofing.md),
including the arrival-timing tell that catches a candidate injected too fast, are
their own page.

## How to check your own setup

Run a WebRTC test page through the proxy you actually deploy with. Not localhost:
localhost bypasses the proxy and tests nothing. [BrowserLeaks](https://browserleaks.com/webrtc)
lists the full candidate set, and [CreepJS](https://abrahamjuliot.github.io/creepjs/)
shows what a real detector extracts from it.

What you want to see:

- The page **completes**. A stuck or empty result is a failure, not a pass.
- Host candidate is a `<uuid>.local` name. Not a LAN IP, and not absent.
- An srflx candidate exists, and its address equals the exit address of your proxy.
- No IPv6 candidate, if your proxy is IPv4.
- The same page in a stock Firefox, side by side, differing only in the IP.

That last one is the check that catches everything else. If your browser's output
differs from a normal Firefox in any field except the address, that field is the
problem.

## Where this project stands

The engine emits an srflx candidate carrying the proxy's exit address, with priority
and foundation rebuilt to match the shape the real code produces, and the host
candidate is left as the mDNS `.local` name Firefox produces by default. IPv6
candidates are filtered in the gathering path when a proxy is configured. The exit
address is resolved at launch by the wrapper, in the same lookup that resolves the
timezone, so the two cannot disagree.

Nothing here is turned off. The goal throughout is a browser that emits what a real
one emits, from an address you chose.

## Short answers to the questions that lead here

**Does a SOCKS5 proxy stop a WebRTC leak?** No. SOCKS5 carries TCP, STUN uses UDP, so
the STUN request leaves from the real interface and the answer names the real address.
The proxy is working correctly; WebRTC is simply not going through it.

**Does an HTTP proxy stop it?** No, for the same reason.

**Does Playwright's `proxy=` option stop it?** No. It configures the browser's HTTP
path. ICE gathering is a separate mechanism.

**Why does my IP leak in Firefox but not in Chrome, or the other way round?** The two
engines gather candidates differently and expose different controls. Chromium has a
handling policy flag; Firefox does not. Test the engine you actually ship.

**Is `media.peerconnection.ice.disableIPv6` the fix for an IPv6 leak?** Not in current
Firefox. See the section above: the preference is not read during ICE gathering.

**Will a VPN fix it?** Usually yes, because a VPN moves the whole interface rather
than just the browser's TCP traffic, so STUN leaves through the tunnel too. That is
the actual difference between a VPN and a proxy here.

**Is it enough to check a leak test page?** Only if the page completes and shows a
full candidate set. A leak test that reports "no leak" over a browser with WebRTC
disabled is reporting the absence of a feature, not the presence of protection.

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md),
the other half of "the browser and the network disagree",
[what privacy.resistFingerprinting really does](resist-fingerprinting.md),
another case where the protective setting is itself visible, and
[the checklist for being detected on one site](playwright-detected-as-bot.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The dead-gate story is ours, and the rule it produced now applies to every gate we
run.*
