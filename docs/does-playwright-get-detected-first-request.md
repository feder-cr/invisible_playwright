---
title: "Does Playwright Get Detected on the First Request?"
description: "Yes, detection can happen before JavaScript runs: the TLS handshake and HTTP/2 settings form a fingerprint a Playwright script can contradict on request zero."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 27
---


# Does Playwright Get Detected on the First Request?

Yes, it can, and the surprising part is that it can happen before a single line
of your page's JavaScript runs. People picture detection as something that
inspects `navigator.webdriver` or reads a canvas hash after the page loads. Those
are real, but they are late. The first thing a server sees from you is not a
DOM, it is a TLS handshake and the opening frames of an HTTP/2 connection, and
those carry a fingerprint of their own.

If that network fingerprint says one client while your `User-Agent` header says
Firefox, the two disagree on request zero, before any script has a chance to
tidy anything up. This page is about that gap, why a real patched browser does
not have it, and the honest limits of what "no gap" buys you.

## What the server sees before your JavaScript runs

A page load is not one event, it is a sequence, and the browser has already
described itself twice before your code executes.

First is the TLS handshake. The [ClientHello](https://datatracker.ietf.org/doc/html/rfc8446) message lists the cipher suites
the client offers, the extensions it sends, the elliptic curves it supports and
the order of all of it. That ordering is not random and it is not the same
across clients: a given browser build produces a characteristic shape, and
hashing that shape is what JA3 and JA4 do. [The full mechanics of how a
handshake becomes a fingerprint](ja3-ja4-tls-fingerprint.md) are a page of their
own, but the short version is that the network stack has a signature and the
signature is legible.

Second, once the encrypted connection is up, HTTP/2 opens with a `SETTINGS`
frame and a set of window and priority values. The exact numbers, and the order
they arrive in, differ between engines and between an engine and a generic HTTP
library that merely speaks the protocol. [HTTP/2 settings are a fingerprint in
the same way](http2-fingerprint-detection.md): distinctive, sent up front, and
impossible to edit from inside the page because the page does not exist yet.

Both of these are decided by whatever actually made the connection. Not by a
header you set. Not by a property you patched. The transport layer answers for
itself.

## Why the mismatch is the tell, not the fingerprint itself

A JA3 hash on its own is not incriminating. Plenty of legitimate clients have
unusual handshakes. What gets flagged is a contradiction: a handshake that
belongs to one client sitting underneath a `User-Agent` header that names a
different one.

This is the same failure that shows up everywhere else in fingerprinting. A
detector rarely asks "is this value rare". It asks "do two values that must
agree, agree". Here the two values are the network fingerprint and the claimed
browser. If a request presents a TLS signature that a generic HTTP client
produces, and the header says it is Firefox on Windows, that is not a subtle
statistical wobble. It is a flat contradiction, visible on the first packet,
with no page interaction required. [The mismatch between a TLS fingerprint and
the User-Agent](tls-fingerprint-user-agent-mismatch.md) is one of the cleanest
signals a server has, precisely because the two sources are independent and hard
to fake in lockstep.

This is why bolt-on stealth is fragile at the network layer. You can override
every JavaScript property you like, and the handshake still comes from whatever
library or headless build actually opened the socket. The disguise is applied
above the layer where the contradiction lives.

## Why a real patched Firefox has no gap here

invisible_playwright takes the approach that avoids the mismatch by construction:
it drives a real Firefox, patched at the C++ level, using stock Playwright. The
handshake is not spoofed to look like Firefox. It is Firefox's handshake, because
the process performing it is Firefox.

That distinction is the whole point. When the TLS `ClientHello` goes out, it is
emitted by a genuine Gecko network stack, so the JA3 and JA4 it produces are the
ones a real Firefox of that version produces. When HTTP/2 opens, the `SETTINGS`
frame carries the values that engine actually uses. The `User-Agent` header then
names Firefox and is telling the truth, so there is nothing for a request-zero
comparison to catch. There is no second client hiding under the first, because
there is only one client.

Switching to it is a two-line change, and the network behaviour comes along for
free because it was never something you were configuring:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # The TLS handshake and HTTP/2 frames on this request are a real
    # Firefox's, and the User-Agent header matches because it is not a spoof.
```

The `browser` here is a real Playwright `Browser`, so every method you already
use works unchanged. The `seed` fixes the in-page fingerprint (GPU, canvas,
audio, fonts) so a run is reproducible, but the network-layer identity does not
depend on the seed at all: it is the same genuine Firefox handshake on every
session, because that is what the process is.

If you want to confirm this rather than take it on faith, the honest method is a
comparison. Capture the handshake from a session driven this way and from a stock
Firefox on the same machine, and diff the JA3 or JA4. They should match, because
it is the same engine. That is the same "compare, do not read a verdict"
discipline that the [testing guide](how-to-test-bot-detection.md) argues for at
every layer.

## The honest caveat: a matching handshake is table stakes

Here is the part that a slogan would skip. A correct TLS and HTTP/2 fingerprint
means you do not fail the request-zero mismatch check. It does not mean you pass
request zero.

The server judges the handshake and the IP that sent it at the same instant. A
perfect Firefox `ClientHello` arriving from an address with a bad reputation, a
datacenter range, or an exit that a thousand other clients are using this minute,
is still a bad first impression. The network fingerprint being clean removes one
signal; it does nothing about the address underneath it, and the address is
evaluated on exactly the same first packet.

So the layer split is worth stating plainly. invisible_playwright is designed to
look like a real browser driven by a real person, and that is why it clears the
fingerprint, TLS and driver checks: those read as a genuine Firefox because it is
one. What it does not do, and cannot do, is supply the things that are not
browser properties:

- **IP reputation.** The engine sends the handshake; you choose what sends it.
  Route it through a clean, ideally residential exit. [Proxy configuration and
  the DNS handling that goes with it](configuration.md) is where that is set.
- **Rate limits and per-account quotas.** A real browser making a thousand
  identical requests a minute is still a thousand requests a minute.
- **Behaviour and timing.** After request zero, the session is judged on how it
  moves. Human pacing is your responsibility, and for agent workloads the pause
  shaped like model latency is [its own consideration](ai-browser-agents-stealth.md).

A matching handshake is necessary and not sufficient. It is the floor you have to
be standing on before anything else you do matters, not the ceiling.

## Conclusion

Detection on the first request is real, and it lives below JavaScript: the TLS
handshake and HTTP/2 settings describe the client before the page loads, and a
network fingerprint that contradicts the `User-Agent` is one of the earliest and
cleanest signals a server has. The durable answer to it is not a better spoof but
a real engine, which is why driving a genuine patched Firefox means the handshake
matches the header by construction. Pair that with a clean exit and human pacing,
and request zero stops being the place you get caught. Leave the exit dirty and it
still is, no matter how perfect the handshake.

## Short answers to the questions that lead here

**Can I be detected before my JavaScript runs?** Yes. The TLS handshake and the
HTTP/2 `SETTINGS` frame are sent before the page loads and both carry a
fingerprint, so a mismatch is visible on the first request.

**What is a JA3 or JA4 fingerprint?** A hash of the shape of the TLS
`ClientHello`: the cipher suites, extensions and curves and the order they appear
in. Different clients produce different shapes.

**Why does a matching handshake matter if the header already says Firefox?**
Because the header is trivially set and the handshake is not. When they disagree,
the handshake is believed and the header is treated as a lie.

**Can I fix the TLS fingerprint from JavaScript or a stealth plugin?** No. The
handshake happens below the page, before any script runs. The only real fix is
for the actual client to be the browser it claims to be.

**Does a real Firefox handshake mean I will not be blocked?** No. It clears the
network-fingerprint mismatch, but the IP that sent the handshake is judged at the
same moment, and reputation, rate limits and behaviour are all still yours to
handle.

**Does the seed change the network fingerprint?** No. The seed fixes the in-page
identity so runs are reproducible; the TLS and HTTP/2 fingerprint is the genuine
engine's and is the same regardless.

## Sources

- The TLS 1.3 and HTTP/2 specifications for what the `ClientHello` and `SETTINGS`
  frames actually contain, and the public JA3 and JA4 definitions for how those
  are hashed into a fingerprint.
- This project's own comparison method: capturing a handshake from a session and
  from a stock Firefox on the same machine and diffing the result, rather than
  trusting a verdict.

**See also:** [JA3 and JA4 TLS fingerprints](ja3-ja4-tls-fingerprint.md) for the
handshake mechanics, [the TLS-versus-User-Agent mismatch](tls-fingerprint-user-agent-mismatch.md)
for why the contradiction is decisive, and [HTTP/2 fingerprint detection](http2-fingerprint-detection.md)
for the layer just above it.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The handshake is
real because the browser is real; the exit and the pacing are still yours to get
right.*
