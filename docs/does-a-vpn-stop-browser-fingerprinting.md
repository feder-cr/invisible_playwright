---
title: "Does a VPN stop browser fingerprinting?"
description: "A VPN changes the IP a server sees, not the browser fingerprint. Why canvas, WebGL, fonts and timezone survive the tunnel, and what actually covers the fingerprint half."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 12
---


# Does a VPN stop browser fingerprinting?

No. A VPN changes one thing: the IP address the server sees. The browser
fingerprint - canvas, WebGL, installed fonts, the user agent, the timezone, the
hardware counters - is exactly what it was before you connected. Worse, a VPN can
make a session look more suspicious rather than less, because it introduces a second
story about where you are and gives a detector two stories to compare.

This page is what a VPN moves, what it leaves in place, the specific way it can
backfire, and what has to cover the half it cannot.

## What a VPN actually changes

A VPN routes your traffic through a server somewhere else and presents that server's
address as the origin of every request. From the server's point of view, you are in
the country the exit sits in, on the network the exit belongs to. That is genuinely
useful and it is the entire effect: one field, the source IP, is replaced.

Everything a detector reads from *inside* the page is untouched, because none of it
travels through the tunnel as a value the VPN can rewrite. The page runs JavaScript
on your actual machine and asks your actual browser questions. The tunnel carries the
answers without changing them.

So the honest split is: a VPN is the network half. The fingerprint half is a
different problem with a different owner.

## What a VPN leaves untouched

Open any fingerprinting report through a VPN and read the fields rather than the
verdict. The address at the top has changed. Nothing below it has:

- **[Canvas](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API) and
  [WebGL](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API).** The hash of a
  rendered image and the GPU vendor and renderer strings come from your graphics stack.
  A tunnel does not touch the graphics stack.
- **Fonts.** The installed-font list is read from the operating system. It still
  describes your real machine, and if that is a server it still describes a server.
- **User agent and platform.**
  [`navigator.userAgent`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/userAgent),
  [`navigator.platform`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/platform),
  [`navigator.oscpu`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/oscpu)
  report the browser you are actually running.
- **Hardware counters.**
  [`hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency),
  [`deviceMemory`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/deviceMemory),
  the audio device sample rate, the screen dimensions and device pixel ratio all come
  from the box the browser runs on.
- **Timezone.**
  [`Intl.DateTimeFormat().resolvedOptions().timeZone`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat)
  reads your system clock's zone, which is set by your operating system, not by your
  exit.

A commercial system hashes many of these together into a stable identifier. That
identifier is stable *across* IP changes by design, which means switching VPN exits
does not make you a new visitor. It makes you the same visitor from a new address,
which is its own pattern.

## When a VPN makes you more suspicious, not less

This is the part most guides skip. A VPN does not just fail to help the fingerprint
layer. It can actively contradict itself.

**The timezone that does not move.** Your exit is in one country; your system clock
is still in another. A detector reads the exit's geolocation and the browser's
reported timezone and sees two different places. A plain browser with no VPN at least
tells one consistent story. A VPN with an unaligned clock tells two, and disagreement
between values that should agree is precisely
[what timezone-versus-proxy checks look for](timezone-proxy-mismatch.md).

**The address that leaks past the tunnel.**
[WebRTC](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API) can enumerate
network candidates directly and, depending on configuration, surface an address the
VPN was supposed to hide, printed right next to the exit. That is the strongest
possible contradiction: the browser volunteering the very thing the tunnel exists to
conceal. It also fails the other way, returning nothing at all, which is itself a
signal that something is suppressing the API. Getting these to agree is a surface of
its own, covered in
[making the WebRTC address match the exit](webrtc-ip-match-proxy-exit.md).

**The reputation the exit already carries.** A shared VPN endpoint has been used by
thousands of other people, and a busy exit accumulates a reputation that has nothing
to do with your fingerprint. A perfectly ordinary browser on a known-bad exit is
detected on the exit alone.

None of these three is a fingerprint fix. Two of them are new ways to fail that you
did not have before you connected.

## The fingerprint half: what invisible_playwright controls

invisible_playwright is a Firefox patched at the C++ level and driven by stock
Playwright. It is built to look like a real browser driven by a real person, which is
why the fingerprint, the TLS handshake and the driver layer read as a genuine
Firefox rather than as automation. It owns the half a VPN cannot reach: it derives a
complete, internally consistent identity - GPU, canvas, audio, fonts, screen, roughly
400 fields - from a single seed, so the values agree with each other instead of being
a plausible field sitting next to a contradicting one.

Switching from plain Playwright is a two-line change, and the browser it returns is a
real Playwright `Browser` with every standard method:

```python
from invisible_playwright import InvisiblePlaywright

# proxy = the network half; the fingerprint half is handled by the engine
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # navigator.userAgent, canvas hash, WebGL strings, fonts and timezone
    # all describe one coherent machine, and the timezone follows the exit
```

Pass a `seed` and every field it implies comes back identical run after run, so a
session you want to reproduce is reproducible. Omit it and each session gets a
distinct identity. Either way, the fingerprint the page reads is a coherent whole
rather than your real machine wearing a new IP.

## Aligning timezone and geolocation to the exit

The specific failure a VPN introduces - a clock that contradicts the exit - is the
one the engine closes automatically. By default the browser timezone is derived from
the egress IP: the proxy exit if a proxy is set, otherwise the host's own public
address. The clock follows the exit instead of staying pinned to the host.

```python
# default: timezone auto-derived from the egress IP, so the clock matches the exit
with InvisiblePlaywright(proxy=proxy) as browser:
    ...

# an explicit IANA zone always wins, when you need to force one
with InvisiblePlaywright(proxy=proxy, timezone="America/New_York") as browser:
    ...
```

The geolocation and locale surfaces are aligned to the same exit rather than left to
disagree. When the network path cannot reach an external lookup, the derivation still
resolves from a bundled dataset rather than falling back to the host's zone, which is
[why the timezone stays correct offline](offline-geoip-timezone-proxy.md). The result
is that the tunnel and the browser tell one story, which is what a VPN on its own
cannot arrange.

## Measuring the difference

The claim is checkable, and checking it is the point. Run a fingerprinting report
twice through the same exit: once with a stock Firefox pointed at the VPN, once with
invisible_playwright pointed at the same proxy, and diff the two reports field by
field rather than reading the scores.

With the stock browser, the address at the top matches the exit and the rows below it
describe your real machine, including a timezone that may sit on a different continent
from the address. With the engine, the address matches the exit *and* the rows below
it describe one coherent machine whose clock, locale and geolocation sit in the same
place as the exit. The delta is every fingerprint field plus the timezone alignment -
which is exactly the half the VPN left untouched in the first report.

Run it at least ten times and space the runs out. This domain is non-deterministic,
and a single green run is not a result.

## What invisible_playwright does not do

The honest caveat, because overclaiming is both false and a liability. The engine
covers the fingerprint, the TLS layer and the driver tells. It does not supply the
network half and it cannot repair it:

- **IP reputation.** A poor-reputation exit is detected on its own, whatever the
  fingerprint looks like. You supply a clean proxy; the engine does not launder a
  dirty one.
- **Per-account quotas and rate limits.** These are counted server-side against your
  account or your address, not read from the browser. Pacing is yours to manage.
- **Behaviour and timing.** Pointer motion is generated on a Bezier curve, but the
  rhythm of a whole session - how fast you move between actions, whether an agent
  pauses in a shape that looks like model latency - is yours to shape.

The framing that holds up: a VPN is the network half and invisible_playwright is the
fingerprint half. You need both, and a clean exit and human pacing on top of both.

## Conclusion

A VPN answers exactly one question a detector asks - where is this request coming
from - and leaves every other question answered by your real machine. On a bad day it
adds two contradictions, a stranded timezone and a WebRTC leak, that a plain browser
would not have produced. It is a necessary piece and not a sufficient one.

Cover the fingerprint layer with something built to look like a real browser, align
the clock and geolocation to the exit so the two halves agree, put a clean IP under
it, and pace the session like a person. A VPN does one of those four. Treat it as one
of four, not as the answer, and it does its job.

## Short answers to the questions that lead here

**Does a VPN change my browser fingerprint?** No. It changes the source IP and
nothing the page reads from inside the browser: canvas, WebGL, fonts, user agent,
timezone and hardware are unchanged.

**Can a VPN make detection worse?** Yes. If your system timezone does not match the
exit, or WebRTC leaks an address past the tunnel, you have handed a detector a
contradiction it would not otherwise have.

**Is a VPN the same as a proxy here?** For this purpose, close enough: both replace
the visible IP and neither touches the fingerprint. The engine takes a proxy directly
and aligns the browser's timezone to that exit.

**Will a VPN hide that I am automating?** No. Automation tells and the fingerprint
live in the browser, not the network. The address is the one thing the tunnel moves.

**Do I still need a good IP if my fingerprint is perfect?** Yes. A coherent browser
on a known-bad exit is detected on the exit alone. The two halves are independent.

**Does invisible_playwright include a VPN or proxy?** No. It handles the fingerprint,
TLS and driver layers and aligns the timezone to whatever exit you pass. You supply
the clean IP.

## Sources

- This project's release gates, including the WebRTC gate whose negative-only checks
  once passed on a feature that was returning nothing, and the timezone derivation
  that resolves the zone from the egress address.
- The public fingerprinting suites - CreepJS, BotD, FingerprintJS, sannysoft and
  BrowserLeaks - read field by field before and after a VPN, which is where the
  "one field changed, the rest did not" observation comes from.
- [MDN: `Navigator.userAgent`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/userAgent),
  [`Navigator.platform`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/platform)
  and [`Navigator.oscpu`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/oscpu),
  the browser-reported properties a VPN cannot rewrite.
- [MDN: `Navigator.hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency),
  [`Navigator.deviceMemory`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/deviceMemory)
  and [`Intl.DateTimeFormat`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat),
  the hardware and clock surfaces read straight from the host.
- [MDN: the Canvas API](https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API),
  [the WebGL API](https://developer.mozilla.org/en-US/docs/Web/API/WebGL_API) and
  [the WebRTC API](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API), the
  graphics and networking surfaces a tunnel does not reach.

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md)
for the contradiction a VPN introduces, and
[what no in-page test can see about the TLS handshake](ja3-ja4-tls-fingerprint.md) for
the network layer neither a VPN nor a fingerprint fix reaches from JavaScript.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The fingerprint half is
what it controls; the clean IP and the human pacing are still yours to supply.*
