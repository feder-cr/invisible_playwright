---
title: "Can Websites Detect Playwright?"
description: "Stock Playwright exposes a webdriver flag, protocol artifacts and mismatched OS fingerprint. Which signals catch your run, and what a patched Firefox fixes."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 20
---


# Can Websites Detect Playwright?

Yes, in principle. Stock Playwright leaves signals in the page that a script can read,
and any one of them is enough to tell "this is automated" from "this is a person". But
"in principle" is doing real work in that sentence. Whether a given site catches your
specific run depends entirely on which of those signals it bothers to check, and there
are only a handful of them.

This page enumerates the concrete tells a page can read, maps each to what a real-looking
browser does about it, and shows the two-line launch that removes the browser-level ones.
It also states plainly what that launch does not touch, because a page that promised you
undetectability would be lying to you.

## The three signals a page can actually read

Strip away the folklore and stock Playwright exposes three families of tell to in-page
JavaScript:

- **A driver flag.** `navigator.webdriver` is defined by the
  [WebDriver automation standard](https://www.w3.org/TR/webdriver2/), and a page reads it
  with one property access.
- **Protocol and automation artifacts.** Globals left in scope by a stealth layer, a
  built-in whose `toString()` no longer says `[native code]`, an event that arrives
  without the trusted bit a real click carries.
- **A fingerprint that contradicts itself.** The user agent claims one operating system
  while the GPU string, the font list, the audio device or the screen geometry describe a
  headless machine somewhere else.

Everything a page-level bot check does falls into one of those three. The rest of this
article takes them one at a time.

## Signal one: the webdriver flag

The cheapest check there is. Under standard automation, `navigator.webdriver` returns
`true`, and a single line of page script reads it.

The naive fix makes things worse. Setting the property to `false` from a script is itself
a tell, because a genuine browser that was never automated reports `undefined`, not
`false`. A value where there should be an absence is a signature of its own. The correct
behaviour is the one an untouched browser produces, and getting there from JavaScript
patching is [harder than it looks](navigator-webdriver-explained.md).

invisible_playwright drives a Firefox patched at the C++ level, so the flag reports the
real-browser value without a script ever assigning it. There is no override to catch,
because nothing overrode anything.

## Signal two: automation globals and protocol artifacts

Automation globals and protocol artifacts are what the act of driving the browser leaves
lying around, and they exist whether or not the driver flag above was ever touched.

JavaScript stealth plugins reach into built-ins to hide the first signal, and in doing so
they create the second: a redefined function whose `toString()` stops returning
`[native code]`, a property descriptor that a real engine never has, a global that only a
particular tooling stack defines. A tampering-oriented suite such as CreepJS does not ask
what you report, it asks whether anything you report contradicts anything else, and a
built-in that has been rewritten is exactly that kind of contradiction. This is
[the native-code check](tostring-native-code-detection.md), and it is the reason stacking
two disguises reads worse than one.

Because the browser is patched in the engine rather than in the page, there are no
injected globals to enumerate and no rewritten built-ins to catch with `toString()`. The
functions a page inspects are the genuine ones.

## Signal three: a fingerprint that does not match the claimed OS

A fingerprint that contradicts the claimed operating system is the family that catches most
server-side automation, and it has nothing to do with the driver at all.

A page reads the user agent, then reads the GPU renderer string, the installed fonts, the
audio device parameters and the screen geometry, and checks that they tell the same story.
A headless machine in a datacenter usually fails that check without any automation flag
being set: a software renderer under a claim of desktop hardware, a font set that belongs
to a different operating system, an audio stack answering with defaults, a screen
resolution no real display has. None of those are automation tells. They are
"this is a datacenter" tells, and they survive every page-level patch because they are not
in JavaScript's gift to change.

invisible_playwright derives a complete, internally consistent fingerprint from a seed:
GPU, canvas, audio, fonts and screen, roughly 400 fields, generated to agree with each
other and to describe a real Windows desktop rather than the host the code runs on. Same
seed, same machine, every run, which is what makes a failure reproducible enough to bisect.
This is the family that a real-browser fingerprint fixes and a property patch cannot, and
it is why the launch below passes most JS detection: the driver layer, the fingerprint and
the TLS handshake all read as a genuine Firefox.

## The launch that removes the browser-level tells

Switching from stock Playwright is two lines, and the object you get back is a real
Playwright `Browser`, so every method you already use works unchanged.

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the identity reproducible; drop it for a fresh one each run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # navigator.webdriver reads as a real browser; the fingerprint is consistent
    print(page.evaluate("navigator.webdriver"))
```

Add a proxy when you deploy, since the browser-level tells are only half the picture:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

The async API is identical, `from invisible_playwright.async_api import InvisiblePlaywright`
with `await` in front of the page calls. Nothing else about your Playwright code changes.

## What this does not fix, and why saying so matters

Here is the honest boundary, because a tool that removed only some of the signals and let
you believe it removed all of them would get you blocked with more confidence, not less.

The launch above addresses the three browser-level families: the driver flag, the
automation artifacts and the fingerprint, plus the TLS handshake, because the request is
made by a real Firefox rather than something impersonating one. What it does not touch:

- **IP reputation.** A consistent browser on a known datacenter address is still on a
  known datacenter address. You supply a clean exit.
- **Per-account quotas and rate limits.** A perfect fingerprint does not raise a limit the
  account already hit.
- **Behaviour and timing.** A burst of identical requests, a form filled in eighty
  milliseconds, a session with no pointer motion. Some sites do not fingerprint at all,
  they watch, and pacing is on you.

So the accurate claim is narrow and true: invisible_playwright makes the browser read as a
real one, which is why it passes most in-page detection. It does not make the session
invulnerable, and no software can, because half the signals are about the network and the
account rather than the browser. When a run still fails with all three browser families
clean, the [checklist for being detected on one site](playwright-detected-as-bot.md) works
the remaining suspects in the cheap-first order, and the network layer has
[its own page on the handshake](ja3-ja4-tls-fingerprint.md).

## Conclusion

Can websites detect Playwright? They can detect stock Playwright whenever they check for
one of three things: the webdriver flag, automation artifacts, or a fingerprint that
disagrees with the claimed OS. A patched Firefox driven by stock Playwright removes all
three at the engine level, which is why it clears most JavaScript detection. It does
nothing for your IP, your quotas or your pacing, so the honest answer to the title is that
it fixes the browser and leaves the rest to you. Verify it the way you would verify
anything here: [compare against a stock browser and run it more than once](how-to-test-bot-detection.md).

## Short answers to the questions that lead here

**Can a website tell I am using Playwright?** If it checks the webdriver flag, automation
artifacts or fingerprint consistency, yes for stock Playwright. A patched-engine browser
removes those browser-level tells.

**Does invisible_playwright make me undetectable?** No, and nothing does. It makes the
browser read as a real Firefox, which passes most in-page checks. Your IP, account limits
and behaviour are separate and still yours to handle.

**Is navigator.webdriver enough to catch me?** It is the cheapest check and stock
automation fails it. Setting it to `false` in a script is its own tell; a real browser
reports `undefined`.

**Why do I pass fingerprint tests and still get blocked?** Because those tests do not see
your address, your request rate or your timing, and a consistent browser on a datacenter
IP is still on a datacenter IP.

**Do stealth plugins help?** They fix the first signal and often create the second: a
rewritten built-in that a tampering check flags. Running one on top of a patched engine is
two disguises contradicting each other.

**How do I check what a site actually reads?** Open a detection suite in your automated
browser and a stock browser on the same machine, and diff the reports field by field
rather than reading the score.

## Sources

- The [W3C WebDriver specification](https://www.w3.org/TR/webdriver2/), which defines the
  `navigator.webdriver` property and the automation flag behind it.
- This project's real API surface, `InvisiblePlaywright(seed=...)` and the returned
  Playwright `Browser`, as documented in the quickstart and configuration pages.
- The public detection suites CreepJS, BotD, FingerprintJS, sannysoft and BrowserLeaks,
  each of which reads one or more of the three signal families described above.
- This project's release gates, which assert the presence of a real-browser signal rather
  than the absence of a wrong one, and separate browser tells from IP and behaviour.

**See also:** [the webdriver flag in detail](navigator-webdriver-explained.md),
[the checklist for a single blocking site](playwright-detected-as-bot.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The honest boundary in the
last section is the one I most wish more of these pages drew.*
