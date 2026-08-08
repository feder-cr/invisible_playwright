---
title: "TLS fingerprint vs User-Agent: the contradiction"
description: "A TLS fingerprint mismatch beats a spoofed User-Agent: the handshake is evidence the engine already produced, the header only a claim. Why the gap is decisive."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 13
---


# TLS fingerprint vs User-Agent: the contradiction

Most advice about TLS fingerprinting stops at "your handshake looks automated". That
frames the problem as one value being wrong in isolation, which is not usually what
gets a session blocked. The sharper failure is a disagreement: the User-Agent string
advertises one browser, and the TLS handshake that happened before that header was
ever sent was produced by a different one.

A string is a claim. A handshake is evidence. When the two contradict each other,
there is no innocent reading of it, and no amount of editing the string closes the
gap. This page is about that specific contradiction, why it is decisive, and a
measured instance of it from inside this project.

## Two layers, and only one of them is under JavaScript's control

A request carries two descriptions of the browser that made it, and they are produced
at completely different times by completely different code.

The **User-Agent** is a header. It is a string the browser chooses to send, and
anything driving the browser can overwrite it before the request goes out. You can set
it in one line. So can a scraper that is not a browser at all. Because it is trivially
writable, a detector treats it as an assertion to be checked, not as a fact.

The **TLS handshake** happens first, during connection setup, before any HTTP header
exists. The client sends a [ClientHello](https://datatracker.ietf.org/doc/html/rfc8446) listing the exact cipher suites it supports, in
its exact order, with its exact set of extensions and their exact order. That list is a
property of the network stack compiled into the engine. JavaScript never sees it and
cannot set it. Tools like [JA3 and JA4 hash that ClientHello](ja3-ja4-tls-fingerprint.md)
into a short fingerprint, and different browser families produce visibly different
fingerprints because they ship different TLS stacks.

So one description is a claim the request makes about itself, and the other is a
by-product the engine emitted without being asked. A detector that has both does not
need to decide whether either is "suspicious" on its own. It only has to check whether
they name the same browser.

## Why the mismatch is decisive, not just suspicious

A disagreement between the header and the handshake is decisive because, unlike a
single odd value, it has no innocent explanation. A single unusual value is
ambiguous. A rare cipher order might be a corporate proxy, an
old build, a niche operating system. Detectors that block on one odd value alone get
false positives, and they know it, so the good ones do not.

A contradiction is different. If the header says one engine and the handshake was
produced by another, exactly one of those is a lie, and the only thing that produces
that particular pairing is a tool overwriting the header on top of a stack it did not
match. There is no real browser configuration that emits browser A's ClientHello and
then announces itself as browser B. The pairing itself is the artifact.

This is the same logic that runs one layer up in the page. The strongest in-page
detectors do not ask "is this value weird", they ask "do two values that a real browser
derives from one source still agree". [Client Hints and the User-Agent string must tell
the same story](client-hints-sec-fetch.md) for the same reason the handshake and the
header must. The TLS case is just the earliest and least forgiving instance, because it
is settled before your automation code has run a single line.

That is also why [randomising the User-Agent makes things worse, not better](playwright-user-agent.md).
Rotating the string does not rotate the handshake. Every rotation that lands on a
browser family your TLS stack does not match manufactures a fresh contradiction on
every request.

## The catch is not a "bad" fingerprint - it is a disagreeing one

The catch is not that the fingerprint is "bad" on its own - it is that it disagrees
with the header the same request sends. The distinction matters because it changes
what a fix has to do.

If the problem were "our TLS fingerprint is bad", you could imagine papering over it. It
is not. The problem is that the handshake and the User-Agent disagree, and the only way
to make them agree is to make the engine's real handshake match the browser the header
claims to be. You cannot claim to be Firefox and then hand-tune your way out of a
ClientHello that is not Firefox's. You have to actually produce Firefox's ClientHello.

We measured this from the inside, and it cost us real blocks before we understood it.

A build of ours advertised itself as Firefox in every header and in every in-page
property, and it passed the JavaScript detectors cleanly. It still got a different,
degraded page than a stock Firefox on the same address. The manual visit worked; ours
did not; the machine and the IP were held constant. By elimination the discriminant was
the one surface no in-page test can see: the handshake.

Read on a public handshake-fingerprinting page, the ClientHello told the story. Our
build offered **17 cipher suites where the stock Firefox of the same version offered
16**. One extra cipher, enabled by a build-configuration default our fork's tree carried
past the point upstream had turned it off. That single difference moved the JA4 into a
value **no shipping Firefox produces**. The header said Firefox; the handshake said
something that had never existed. A handshake-level fingerprinter caught the pairing
immediately, and it was an edge tell that fired before a page ever loaded.

The fix was not a header edit and not a TLS "spoof". It was engine parity: bring the
offered cipher set back to exactly what current upstream Firefox ships, byte for byte,
so the ClientHello our build emits is indistinguishable from retail. After that the
JA3, the JA4 and the full handshake fingerprint were byte-identical to a stock Firefox
of the same version, and the contradiction was gone because there was nothing left to
contradict. The claim and the evidence now name the same browser because they are the
same browser.

## What this means for how you drive a browser

The practical conclusions follow directly from where the two layers live.

You cannot fix this from your automation script, because the handshake is decided by the
engine before your script runs. Setting `user_agent` in Playwright, adding headers, or
installing a page-level stealth plugin all operate above the handshake and cannot reach
it. If you send browser-shaped requests from an HTTP client that is not a browser, the
handshake gives you away no matter how carefully you copy the headers, which is
[why request libraries get blocked where a real browser is not](web-scraping-tls-fingerprint-requests-blocked.md).

The only durable answer is to send requests from a real browser engine whose handshake
already matches the identity it advertises, and then not to disturb the agreement from
above. That is the position this project takes: a Firefox patched at the C++ level,
driven by stock Playwright, whose ClientHello is the real Firefox ClientHello because it
is a real Firefox. You do not assemble a matching handshake; you inherit it.

In code, that means the default is already the aligned one, and your job is to avoid
reintroducing a contradiction by hand:

```python
from invisible_playwright import InvisiblePlaywright

# The engine's handshake and its User-Agent already name the same Firefox.
# Do not override the User-Agent to a different browser family - that is the
# one edit that manufactures the contradiction this page is about.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate("() => navigator.userAgent"))
```

The `seed` fixes the in-page fingerprint so a run is reproducible; it does not touch the
handshake, which is a property of the engine and identical across seeds. If you want to
confirm the alignment yourself, drive the browser to a public handshake-fingerprinting
page and to a stock Firefox of the same version, and diff the two ClientHellos:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    # Any public TLS-fingerprinting endpoint that echoes the parsed ClientHello.
    page.goto("https://example.com/your-tls-echo-endpoint")
    print(page.inner_text("body"))
    # Compare the ja3 / ja4 shown here against a stock Firefox of the same
    # version on the same machine. They should be byte-identical.
```

If those two match, the header and the handshake are telling one consistent story, and
the contradiction that this whole page is about cannot arise. If they differ, no
in-page change will help, because the disagreement lives below the page.

## Conclusion

The User-Agent is a claim and the TLS handshake is evidence, produced at different
times by different code, and only the claim is yours to write. A detector that holds
both does not have to judge either one in isolation; it only has to notice when they
name different browsers, and that pairing has no honest cause. We shipped that exact
contradiction once - one extra cipher suite turned our Firefox's handshake into
something no Firefox produces - and the only thing that closed it was making the engine
emit the real handshake, byte for byte. You match a fingerprint by being the thing that
has it, not by describing it.

## Short answers to the questions that lead here

**Can I fix a TLS fingerprint mismatch by changing the User-Agent?** No. Editing the
header changes the claim, not the handshake it disagrees with. The two only agree when
the engine's real ClientHello matches the browser the header names.

**Why does my request get blocked when the User-Agent looks perfect?** Because the
handshake sent before that header was produced by a different stack, and the detector
compared the two. A perfect string on the wrong handshake is a contradiction, not a
disguise.

**Can JavaScript read or set the TLS fingerprint?** No. The ClientHello is sent during
connection setup, before any page code runs. JavaScript never sees it and cannot change
it, which is why no page-level stealth plugin touches it.

**Is a "bad" TLS fingerprint the real problem?** Usually not on its own. The decisive
signal is disagreement with the User-Agent, because one odd value has innocent
explanations and a header-versus-handshake contradiction does not.

**Does rotating the User-Agent help?** It hurts. The handshake does not rotate with the
string, so every rotation onto a browser family your stack does not match creates a new
contradiction on every request.

**How do I check my own setup?** Drive your browser and a stock browser of the same
version to a public handshake-fingerprinting page on the same machine, and diff the JA3
and JA4. If they are not byte-identical, that gap is what a detector sees.

## Sources

- This project's release notes on the handshake divergence described above: a build that
  offered one cipher suite more than the stock Firefox of the same version, moving the
  JA4 to a value no shipping Firefox produces, closed by restoring exact cipher parity
  with current upstream.
- Public JA3 and JA4 fingerprinting pages, read by driving both this build and a stock
  Firefox to them on the same machine and comparing the parsed ClientHello field by
  field.

**See also:** [why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md),
[the headers that must agree with the User-Agent](client-hints-sec-fetch.md), and
[why request libraries get blocked where a real browser is not](web-scraping-tls-fingerprint-requests-blocked.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The cipher-parity block
above cost us real page loads before we found it, and the fix was making the engine
match, not editing a header.*
