---
title: "What data does a website collect about your browser?"
description: "JS-accessible page surface - navigator, screen, canvas, WebGL, audio, fonts, WebRTC - plus TLS/HTTP2 fingerprint the server sees and which real engines normalise."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 11
---


# What data does a website collect about your browser?

Far more than most people expect, and most of it without asking. By the time a
page has finished its first paint it has usually read a few hundred values off
your browser, hashed several of them together, and compared the answers against
each other. The interesting detection question is almost never "what is this one
value" but "do these values agree", and that is the part a bolt-on disguise gets
wrong.

This page enumerates the concrete surface a site can read from JavaScript, adds
the passive fingerprint the server sees before any script runs, and marks which
of them a real engine normalises to real values and which one it does not touch
at all. If you want the reverse angle - how to prove any of this on your own
setup - [how to test whether your browser is detected](how-to-test-bot-detection.md)
is the companion page.

## The JavaScript surface, field by field

Everything here is readable by any script on the page, with no permission prompt
and usually in the first few milliseconds.

- **navigator**: user agent, `platform`, `oscpu`, `languages`,
  `hardwareConcurrency`, `deviceMemory`, `maxTouchPoints`, `vendor`,
  `productSub`, `buildID`. Individually dull, collectively a signature. The CPU
  and RAM pair is a favourite because a server often answers with round numbers
  no consumer laptop reports - see
  [hardwareConcurrency and deviceMemory](hardware-concurrency-device-memory.md).
- **screen metrics**: `width`, `height`, `availWidth`, `availHeight`,
  `colorDepth`, and `devicePixelRatio`. An `availHeight` equal to `height` means
  no taskbar, which means no desktop, which means a server.
- **canvas**: the page draws text and shapes to an offscreen canvas, reads the
  pixels back with
  [`getImageData()`](https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/getImageData),
  and hashes them. Tiny differences in the font rasteriser, the GPU
  and the anti-aliasing make the hash stable per machine and different across
  machines. It is read twice and compared; a hash that changes between two reads
  in one session is itself a tell.
- **WebGL**: the renderer and vendor strings, plus dozens of numeric limits read
  via [`getParameter()`](https://developer.mozilla.org/en-US/docs/Web/API/WebGLRenderingContext/getParameter)
  (`MAX_TEXTURE_SIZE`, shader precision ranges, and so on). A software renderer
  string, or a plausible string whose numbers do not match a real card, both
  read as datacenter.
- **[AudioContext](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)**:
  the browser renders a short audio buffer through an
  oscillator and a compressor and hashes the floating-point output. Sample rate,
  latency and channel count trace back to a real audio device, which a bare
  container does not have, which answers with tell-tale default values.
- **installed fonts**: a script measures text width for hundreds of font names
  and infers which ones are present. A Linux font set under a Windows user agent
  is a one-line contradiction - see
  [detecting installed fonts from JavaScript](detect-installed-fonts-javascript.md).
- **timezone and locale**:
  [`Intl.DateTimeFormat().resolvedOptions().timeZone`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat)
  and the accept-languages list, both cross-checked against the exit IP's
  country.
- **WebRTC ICE candidates**: opening a peer connection surfaces network
  candidates, which can leak a real local or public address next to a proxy's,
  or, if suppressed clumsily, come back empty - and empty is
  [its own signal](webrtc-leak-proxy.md).

## The passive fingerprint the server sees without any script

Two surfaces are collected before a single line of JavaScript executes, purely
from how the connection is made:

- **[The TLS handshake](https://datatracker.ietf.org/doc/html/rfc8446).** The
  ordered list of cipher suites, extensions and curves your client offers is
  distinctive per implementation. A handshake that is not Firefox's, arriving
  with a user agent that says Firefox, is a decisive mismatch that no
  page-level patch can reach.
- **[HTTP/2 settings and header order](https://datatracker.ietf.org/doc/html/rfc9113#section-6.5.2).**
  The settings frame values and the exact order of request headers differ
  between real browsers and most scripting stacks.

You cannot fix either of these from inside the page. Either the request is made
by a real browser engine, or it is made by something impersonating the handshake
too. This is the single most common reason a browser that passes every in-page
suite still gets a different page - it is item six on the
[detected-on-one-site checklist](playwright-detected-as-bot.md) for exactly that
reason.

## Why agreement matters more than any single value

A detector rarely asks whether one value is unusual. It asks whether two values
that must agree, do. A user agent claiming Windows against a Linux font list. A
canvas hash that changes between two reads. A pinned timezone against an IP on
another continent. Each of these is cheap to check and expensive to fake,
because faking one field means also faking every field correlated with it.

This is where a bolt-on stealth plugin loses. It answers a handful of questions
in JavaScript while the engine underneath keeps answering the rest honestly, and
the two disguises contradict each other. A tool that patches `navigator` but
leaves the real WebGL renderer, or spoofs the renderer string while the pixels
are still drawn by a software rasteriser, produces a mismatch that neither the
original nor the patch produced alone. The auditing suite
[CreepJS records exactly this kind of internal contradiction](creepjs-explained.md),
and it records a blocked probe as a lie by name.

## How invisible_playwright normalises the browser surface

invisible_playwright takes a different route: instead of patching values on top
of a browser, it ships a Firefox patched at the C++ level and drives it with
stock Playwright. Every surface listed above - navigator, screen, canvas, WebGL,
AudioContext, fonts, timezone, WebRTC - is answered by one real engine, and all
of them are derived from a single seed, so they agree with each other by
construction rather than by a lookup table that has to be kept consistent by
hand. The TLS and HTTP/2 fingerprint read as a genuine Firefox because the
request is made by a genuine Firefox.

Reading that surface is a two-line switch from plain Playwright, and the object
you get back is a real Playwright `Browser` with every standard method:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes every surface reproducible: same GPU, canvas hash,
# audio context, fonts and screen on every run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    surface = page.evaluate("""() => ({
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        languages: navigator.languages,
        cores: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        screen: [screen.width, screen.height, screen.availHeight],
        dpr: window.devicePixelRatio,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    })""")

    print(surface)
```

Every field here comes back with a real-browser value, and because the seed is
fixed the whole set is identical on the next run - which is what lets you replay
a failing session exactly instead of guessing. Pass a proxy and the timezone and
locale follow the exit automatically:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

## The honest limit: what a fingerprint cannot touch

Making every browser-side surface real and consistent is why this build passes
most detection checks - the fingerprint, the TLS layer and the driver layer all
read as a genuine Firefox. It is not the whole story, and any tool that tells you
it is, is selling you something.

Two things sit outside the browser entirely:

- **The IP address and its reputation.** This is collected at the network layer,
  before your page loads, and no browser-side value controls it. A perfect
  browser on a datacenter range, or on a public proxy IP that is already on a
  blocklist, still loses. You supply a clean exit; the engine cannot.
- **Behaviour, pacing and account limits.** A pointer that teleports, keystrokes
  at a uniform interval, a form filled in eighty milliseconds, per-account
  quotas and rate limits - none of these are fingerprint surfaces. They are
  yours to get right with human pacing and sane request volumes.

So the honest framing: invisible_playwright makes the browser look like a real
browser driven by a real person, which handles the fingerprint, TLS and driver
layers. It does not handle your address or your behaviour, and pairing it with a
clean proxy and human-shaped pacing is the part that is on you.

## Conclusion

A website reads a large, script-accessible surface - navigator, screen, canvas,
WebGL, AudioContext, fonts, timezone, WebRTC - and adds the passive TLS and
HTTP/2 fingerprint it sees without any script at all. The detection value is in
whether those all agree. A real engine seeded from one value makes them agree by
construction, which is why the fingerprint and network layers pass. The two
things it deliberately does not touch, your IP reputation and your behaviour, are
the two things you still have to bring.

## Short answers to the questions that lead here

**What can a website see about my browser without permission?** The whole
navigator and screen object, canvas and WebGL hashes, an AudioContext hash, your
installed font list, timezone and locale, and WebRTC candidates - plus the TLS
and HTTP/2 fingerprint the server reads before any script runs.

**Can a site read my real IP even through a proxy?** At the network layer it
reads whatever address the connection exits from. WebRTC can also leak a second
address inside the page if the browser is not careful, which is a separate
surface from the server-observed one.

**Does invisible_playwright make me undetectable?** No, and no tool should claim
that. It makes the browser fingerprint, TLS and driver layers read as a real
Firefox, which passes most checks. It does not fix IP reputation, rate limits or
behaviour.

**Why do I pass every fingerprint test and still get blocked?** Because the
public suites do not see the TLS handshake, your address, or your behaviour. A
consistent browser on a bad IP is still on a bad IP.

**Is one weird value enough to flag me?** Usually not on its own. What flags you
is two values that should agree and do not, which is why patching a single field
tends to make things worse.

**Do I still need a proxy if the fingerprint is perfect?** Yes. The IP is
collected outside the browser and no fingerprint controls it. A clean exit is a
separate requirement you supply yourself.

## Sources

- The browser surfaces named above, each with its own page in this set, read
  from their own APIs rather than from a detector's rendered verdict.
- This project's release gates, which compare each surface against a stock
  Firefox on the same machine field by field, and which separate IP and session
  noise from the fingerprint delta.

**See also:** [how to test whether your browser is detected](how-to-test-bot-detection.md)
to prove any of this on your own setup, and
[the checklist for being detected on one site](playwright-detected-as-bot.md) for
the order to work in once a test tells you something.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The surfaces on
this page are the ones our own gates diff against a stock browser, and the IP
caveat is the one they cannot close.*
