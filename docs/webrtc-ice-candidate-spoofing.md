---
title: "WebRTC ICE candidate spoofing: the fields that give it away"
description: "Replacing the address in a server-reflexive candidate is the easy part. The priority, the foundation and the arrival time are all checkable, and we shipped all three wrong before getting them right."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 2
---


# WebRTC ICE candidate spoofing: the fields that give it away

Every guide about hiding your address in WebRTC stops at the address. Rewrite the
server-reflexive candidate to say the proxy's IP and you are done.

You are not done. A candidate is a structured record with half a dozen fields, it arrives
at a particular moment, and it sits in a set alongside other candidates that have to be
consistent with it. We got the address right immediately and then spent a long time
finding out that three other things were wrong.

This page is what has to be replaced, the two ways to do it, and the three fields that
betray a synthetic candidate: when it arrives, what priority it claims, and what
foundation it shares.

## What actually has to be replaced

A normal browser behind NAT produces two candidates that matter:

```
candidate:... typ host   <uuid>.local          the LAN address, masked
candidate:... typ srflx  203.0.113.7  raddr ...  the public address, from STUN
```

The **srflx** is the problem. STUN runs over UDP, a SOCKS proxy carries TCP, so the
browser asks a STUN server directly from the real interface and the server truthfully
answers with your real address. Your HTTP traffic exits one way and your WebRTC exits
another, and the two disagree.

The **host** candidate is not the problem, and this is where people overcorrect. Firefox
already masks it behind an mDNS `<uuid>.local` name by default, which is what real
browsers do. Replacing it with a fake LAN IP makes you *worse* than stock: we shipped
that once, and a public leak test reported "WebRTC exposes your Local IP" against our
browser and not against an ordinary Firefox. The correct move is to leave the default
alone.

[The wider version of this argument](webrtc-leak-proxy.md) is that suppression and
overcorrection are both louder than the leak.

## Two ways to produce the right address

**Swap after the fact.** Let the real STUN transaction complete, then replace the address
in the result before the candidate is emitted. The advantage is that everything else
about the candidate is genuine: the timing, the port, the priority, all produced by the
normal code path. The disadvantage is that it needs the STUN transaction to succeed,
which behind a proxy it often does not.

**Synthesise.** Build a candidate that never came from a STUN server. This works when
there is no reachable STUN at all, which is the common case behind a SOCKS proxy, and it
is where every field you do not think about becomes a thing you have to get right.

Both are needed in practice. The swap handles the case where STUN works; the synthetic
one covers the case where it does not.

## Tell one: arrival time

Our first synthetic candidate was emitted immediately.

A real server-reflexive candidate cannot appear instantly. It exists because a request
travelled to a STUN server and a response came back, so it arrives one network round trip
after gathering starts. A candidate that materialises with zero delay describes a STUN
server in the same room as you.

This is not a theoretical concern. Published detection advice for this exact technique
names round-trip timing as the way to spot a spoofed address, in both directions: a
candidate that arrives too fast never made the trip, and one that arrives too slow made
the trip from somewhere other than where it claims.

The fix is a delay that looks like a network. Ours emits on a timer at a realistic STUN
round trip rather than immediately.

## Tell two: priority

This is the one that would have caught us on any careful inspection, and it is a single
number.

ICE priority is not arbitrary. It is computed by a documented formula from the candidate
type, an interface preference and the component id:

```
priority = (type_preference << 24) | (local_preference << 16) | (256 - component_id)
```

For a server-reflexive UDP candidate the type preference is fixed by the specification.
The **local preference** is where implementations differ, and the one in Firefox's ICE
stack produces values in a narrow band, roughly 32256 to 32704.

Our synthetic candidate hardcoded it to `0xFFFF`, which is 65535.

That is not an unusual priority. It is a priority **no real candidate on that stack ever
has**, because the code that generates them cannot produce it. One integer, visible in
the SDP, and it says the candidate did not come from the browser.

The fix was to reconstruct the real formula and read the interface preference from the
genuine host candidate on the same interface, so the synthetic srflx inherits the number
a real one would have had. The values now sit in the same band as a stock browser's.

**The general lesson**, and it is worth more than the specific number: when you fabricate
a record, every field with a computed value is a field somebody can recompute.

## Tell three: foundation

The foundation is an identifier shared by candidates of the same type from the same base
address, used to group them for the connectivity checks.

Ours appended a new foundation index each time, so across runs it drifted, taking a
different value on each launch, and it sometimes collided with the foundation of the
TCP host candidate, which is a grouping no real browser produces.

Two separate problems in one field: a value that moves when it should be stable, and a
value that groups things that do not belong together.

The fix reuses the existing server-reflexive foundation on the same base, matching on
the same criteria the real code matches on. It is now stable across runs and distinct
from the host candidates, which is what a stock browser produces.

### The general shape of both fixes

Notice what fixing these looked like: not inventing plausible values, but **finding the
function the browser already uses and calling it with the right inputs**. A fabricated
record is credible exactly to the degree that it was produced by the same code as a real
one.

## The failure nobody expects: gathering dies behind a proxy

The most expensive bug in this area was not a wrong value. It was no values at all.

Firefox restricts ICE gathering to the address of the **default route** when the page has
no camera or microphone permission, which is the normal case for almost every page. That
restriction is a privacy feature.

Finding the default route works by opening a UDP socket "connected" to the document's
remote address and reading back which local address the operating system chose. Behind a
SOCKS proxy that remote host is not directly reachable, the probe fails, gathering fails,
and the browser produces **zero candidates** on every proxied page.

So the browser was not leaking. It was silent, which is
[a stronger signal than the leak](webrtc-leak-proxy.md), and our gate reported success the
whole time because it only asserted that nothing leaked.

The fix falls back to the local addresses that survived filtering when the default-route
probe fails, rather than aborting. It only runs on the failure path, so the direct case is
untouched.

## Conclusion

Faking a WebRTC address is a small change with a long tail. The address is one field.
The priority is computed, the foundation is shared, the timing is observable, and the
whole set has to hold together with the host candidate and with each other.

The approach that worked, after three attempts that did not, is to stop fabricating and
start reusing: take the values from the code that produces the real ones, and only replace
the single thing that has to change.

And check the positive case. A synthetic candidate with perfect fields is worth nothing if
gathering silently produced nothing at all, which is exactly what we shipped while a
green gate watched.

## Short answers to the questions that lead here

**Why does WebRTC leak my real IP behind a SOCKS proxy?** Because STUN uses UDP and SOCKS
carries TCP, so the STUN request leaves from your real interface.

**Should I replace the host candidate too?** No. Firefox already masks it as an mDNS
`.local` name, and replacing that with a fake LAN address is worse than the default.

**What gives away a synthetic candidate?** Its priority if the value is outside the range
the real code produces, its foundation if it drifts or collides, and its arrival time if
it appears with no round trip.

**How is ICE priority calculated?** From the candidate type, a local preference and the
component id, combined by a documented formula. The local preference is
implementation-specific and lives in a narrow band.

**Can I just disable WebRTC?** You can, and a browser with no ICE candidates at all is a
more specific thing to be than one with an unusual address.

**How do I test this properly?** Assert the positive shape: the page completes, the host
candidate is a `.local` name, and the srflx address equals your proxy's exit. An empty
result is a failure. [The method](how-to-test-bot-detection.md).

**See also:** [WebRTC leak with a proxy](webrtc-leak-proxy.md) for the problem this
solves, [when the timezone does not match the proxy](timezone-proxy-mismatch.md) for the
other half of making a proxied session agree with itself, and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

## Sources

- The ICE specification's priority formula, and the local-preference range produced by
  the ICE implementation Firefox uses.
- Published detection guidance for this technique, which names round-trip timing and
  candidate ordering as the ways to spot a fabricated address.
- This project's own ICE work: the post-STUN address swap, the synthetic fallback with its
  timer, the priority and foundation reconstruction, and the default-route fallback. Every
  wrong value described above was one we shipped.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Three of the four problems on this page were found by
comparing our own output against a stock Firefox, field by field, which is the only method
that finds this class of thing.*
