---
title: "Can You Run Playwright Without Being Detected?"
description: "An honest answer for Playwright automation: what removing browser-level tells clears, why it looks like a real browser, and the three signals it does not fix."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 11
---


# Can You Run Playwright Without Being Detected?

Not in an absolute sense, and any tool that promises otherwise is lying to you.

What is achievable is narrower and more useful: you can remove the browser-level
tells so the fingerprint and the automation flags read like a real person's browser,
and that alone clears most JavaScript-based detection. The rest of what gets a session
flagged is not a browser problem at all, and no amount of browser work touches it.

This page draws that line precisely. It shows what "undetected" can honestly mean,
what a real-browser fingerprint clears, and the three signal classes that are detected
independently of the browser and that you, not the tool, have to supply an answer for.

## The honest answer: "looks like a real browser", not "invisible"

The useful reframing is to stop asking "can I be undetected" and start asking "does
this look like a real browser driven by a real person". That is a question you can
actually answer yes to, and it is the one that most detection is really asking.

A detector almost never has a single "is this a bot" bit. It has a pile of signals and
a score. Some of those signals live in the page, in JavaScript: the fingerprint, the
automation flags, whether any built-in has been tampered with. Some live below the
page: the network handshake, the exit address. Some live in what the session does over
time: pacing, motion, request velocity.

A tool that drives a genuine, unmodified-looking browser can make the first pile read
clean. It cannot make the other two piles read clean, because they are not properties
of the browser. Being honest about that split is the whole point of this page, and it
is why [passing every public suite still does not mean the session passes](how-to-test-bot-detection.md).

## What "detected" actually means: three layers of signal

Before touching code, it helps to name the three layers, because a fix for one does
nothing for the others.

1. **The browser layer.** The fingerprint (GPU, canvas, audio, fonts, screen), the
   automation flags ([`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
   and friends), and internal consistency
   (does the platform you claim agree with the fonts you have). This is in JavaScript's
   reach, and it is the layer a stealth engine addresses.
2. **The network layer.** The TLS handshake, the HTTP/2 settings frame, header order,
   and the reputation of the exit address. Decided before a line of page script runs,
   and [invisible to every in-page test](ja3-ja4-tls-fingerprint.md).
3. **The behaviour layer.** Pointer motion, typing rhythm, how fast a form is filled,
   how many requests per minute from one account, and the timing shape of an
   automated agent.

invisible_playwright is built to solve layer 1 well, and it drives a real Firefox
binary, which means layer 1's network handshake is a genuine Firefox handshake rather
than an impersonation. What it deliberately does not do is manufacture a clean IP, a
per-account quota, or human pacing. Those are yours to bring.

## What invisible_playwright removes: the browser layer

The product is a Firefox patched at the C++ level, driven by stock Playwright, with a
fingerprint derived from a seed so it is real-browser-shaped and reproducible. Every
session gets a coherent identity (roughly 400 fields that agree with each other)
instead of the default automation profile that detectors are trained on.

Switching from plain Playwright is a two-line change, and the object you get back is a
real Playwright `Browser`, so every method works exactly as documented upstream:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the identity reproducible; drop it for a fresh one each run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # navigator.webdriver reads undefined, the fingerprint is internally
    # consistent, and the TLS handshake is a real Firefox handshake
    print(page.evaluate("navigator.webdriver"))   # -> None
```

Add a proxy and let the browser timezone follow the exit automatically, which is how
you keep the browser and the network from telling two different stories:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/login")
    page.fill("#user", "someone")
    page.click("#submit")   # the pointer arcs to the button on a curve
```

The measurable result of layer 1 being clean: open the same fingerprinting page in
this browser and in a stock Firefox on the same machine, diff the reports field by
field, and the difference collapses to per-session noise (the IP and the random canvas
jitter a real browser also has). The JavaScript detectors read it as a genuine Firefox
because, at the layer they can see, it is one. That is why it passes most of them. It
is also the ceiling of what the browser layer can do.

## The three signal classes it deliberately does not touch

This is the part most "undetectable" claims skip. These three are detected
independently of the browser, the tool does not address them by design, and a clean
fingerprint on top of any one of them still loses.

- **IP reputation.** A perfect browser on a datacenter range, or on a residential exit
  a thousand other people are sharing this minute, is still on a flagged address, and
  that is decided before your script runs. Around 90 percent of cheap proxies are
  public and already known. The fix is a clean exit, which is your input, not the
  browser's output. [WebRTC through a proxy](webrtc-leak-proxy.md) is the related
  browser-visible surface, but the reputation itself is not a browser property.
- **Per-account rate limits and quotas.** If one account does in an hour what a person
  does in a week, no fingerprint hides it, because the signal is the account's own
  history, not the browser's identity. Spreading work across accounts and staying under
  human volume is an application-design decision the tool cannot make for you.
- **Behaviour and timing.** Uniform keystroke intervals, a form filled in eighty
  milliseconds, a pointer that teleports, a request cadence with no human jitter, or
  [the pause shaped exactly like model latency in an agent loop](ai-browser-agents-stealth.md).
  The engine gives you curved mouse motion for clicks, but the overall rhythm of the
  session is driven by your code.

None of these is a gap or a bug. They are outside the browser, so they are outside what
a browser-fingerprint tool can honestly claim. Pairing a real-browser fingerprint with
a clean exit and human pacing is the actual recipe; the tool is one of those three
parts.

## How to check what is left over

Because the browser layer is handled, your testing effort should go to the two layers
that are not. The method that catches the most:

- **Prove it is the browser before blaming it.** Open the target by hand from the same
  machine and network. If the manual visit also gets the wrong page, this is the IP or
  the account, and no browser change helps. This ordering is the whole point of the
  [detected-on-one-site checklist](playwright-detected-as-bot.md).
- **Compare against a stock browser, do not read a score.** Diff the fingerprint fields
  against a real Firefox on the same machine. Anything that matches is not your problem
  whatever the verdict says; anything that differs, other than the address, is a
  candidate.
- **Assert presence, not absence.** A blocked or empty signal is itself a tell and a
  failure, not a pass. [Why a green verdict can mean the feature is broken](how-to-test-bot-detection.md)
  is the trap here.
- **Watch the score, not just the pass line.** A [trust or confidence score](browser-trust-score-explained.md)
  tells you more than a binary verdict, and it is where residual behaviour and IP
  signals show up even when the fingerprint is clean.

Run any of this at least ten times, through the proxy you actually deploy with, on the
machine you actually deploy on.

## Conclusion

Can you run Playwright without being detected? You can make it look like a real browser
driven by a real person, and that clears most JavaScript-based detection, which is what
invisible_playwright is built to do: a patched Firefox, a seed-consistent real-browser
fingerprint, and a genuine handshake because the browser is genuine. What it does not
do, on purpose, is fix your IP reputation, your per-account quotas, or your behaviour
and timing. Those are detected independently, and you supply the answers: a clean exit
and human pacing. Treat "undetectable" as a slogan and you will overclaim; treat it as
"looks like a real browser, plus the two layers I own", and you will actually pass.

## Short answers to the questions that lead here

**Can Playwright be truly undetectable?** No, and any tool that says so is lying. You
can remove the browser-level tells so the fingerprint and flags look like a real
person's browser, which clears most JavaScript detection. IP, account limits and
behaviour are detected separately.

**Why does invisible_playwright pass most checks then?** Because at the layer those
checks can see, it is a real Firefox: real fingerprint, no automation flags, a genuine
handshake. Most public detection is a JavaScript check, and that is exactly the layer
it addresses.

**If my fingerprint is clean, why am I still blocked?** Almost always the IP, the
account's own request history, or the behaviour. Those are not browser properties, so a
clean browser does not touch them.

**Does it fix my proxy or my IP reputation?** No. It deliberately does not touch the
exit address. A perfect browser on a known-bad IP still loses, and you supply the clean
exit.

**Does it make my automation behave like a human?** It gives you curved mouse motion
for clicks, but the pacing, typing rhythm and request volume come from your code. That
is the behaviour layer, and it is yours.

**Is a passing sannysoft or CreepJS result enough to be safe?** No. It means the
browser layer is internally consistent and does not announce automation. The network
and behaviour layers are not in those tests at all.

## Sources

- This project's release gates, which compare the browser's fingerprint field by field
  against a stock Firefox on the same machine and treat a suppressed signal as a
  failure rather than a pass.
- The public detection suites named across this documentation set (sannysoft, CreepJS,
  BotD, FingerprintJS, BrowserLeaks), each read from its own source rather than its
  rendered verdict.

**See also:** [how to test whether your browser is detected](how-to-test-bot-detection.md),
[the checklist for being detected on one site](playwright-detected-as-bot.md), and
[what a browser trust score actually measures](browser-trust-score-explained.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It is built to look like
a real browser, which is why it passes most checks and why it will never claim to make
you invisible.*
