---
title: "puppeteer-real-browser vs invisible_playwright"
description: "puppeteer-real-browser patches runtime JavaScript properties; invisible_playwright is Firefox with spoofs compiled into the binary. Compare where each helps."
parent: "Comparisons"
nav_order: 26
---


# puppeteer-real-browser vs invisible_playwright

Both projects exist to answer the same complaint: a real page and an automated
one come back different, and the automated one loses. They take opposite routes
to that goal, and the route is the whole story. This page is where each one puts
the realness, what that buys you, and the part neither of them fixes.

If you want the honest one-line version first: both make the browser look
genuine, which is why most fingerprint and driver-layer checks pass. Neither
supplies a clean address, a human pace, or per-account patience. You bring those.

## Where each one puts the disguise

`puppeteer-real-browser` launches a real Chrome from Node and layers evasions on
top of it in JavaScript. It drives an actual browser rather than a headless
shell, then applies stealth-plugin style property patches at runtime and offers
an optional captcha-solving helper. The realness is Chrome; the disguise is a set
of overrides written over the top of it after the browser is already running.

`invisible_playwright` goes the other way. The realness lives in a Firefox that
was patched at the C++ level and configured through standard `about:config`
prefs, and it is driven by stock Playwright from Python. There is no runtime
layer rewriting `navigator` properties per page, because the values a detector
reads are what the compiled build reports in the first place.

That is the durable architectural difference, and it is not a put-down of either
side: one disguises a real Chrome from the outside, the other ships a browser
that does not need disguising from the outside.

## Why the layer that applies the patch matters

A property that is patched at runtime can be asked how it was made. The classic
question is whether a function is native code: call `toString()` on an overridden
method and a naive patch returns a body, or a `Proxy` handler, or a stack frame
that a genuine built-in would never produce. Detectors walk descriptors, compare
a method against a clean copy pulled from a fresh iframe, and record any
disagreement. [Why toString and native-code checks catch runtime
patches](tostring-native-code-detection.md) is the long form of this, and it is
the failure mode that runtime evasion layers spend the most effort staying ahead
of.

When the value is compiled into the build instead, there is no override to
inspect: the method is the real one, the property is the real property, and the
consistency check that catches a runtime patch has nothing to compare against.
This is the same reason we keep a
[standing comparison against the JS stealth-plugin
approach](vs-playwright-stealth.md) rather than shipping one ourselves, and the
reason the upstream plugin ecosystem's maintenance state is
[worth reading before you build on it](puppeteer-extra-stealth-unmaintained.md).

None of this makes runtime patching useless. It makes it a moving target, where
a compiled build moves the same work to a place the page cannot query.

## The same task in invisible_playwright

Switching from stock Playwright is two lines, and every Playwright method you
already use works unchanged, because the object you get back is a real
[Playwright `Browser`](https://playwright.dev/python/docs/api/class-browser).

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

The `seed=42` is the piece worth dwelling on. Every surface a detector reads -
GPU, canvas hash, audio context, fonts, screen, roughly four hundred fields - is
derived from that one seed, so the same seed gives the same machine every run.
That turns a flaky "sometimes blocked" into something you can replay exactly,
which is the difference between a bisect and a guess. Omit the seed and you get a
distinct identity per session; log `sf.seed` to recover it later.

Add a proxy and a timezone the same way, through the constructor:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(proxy=proxy, timezone="America/New_York") as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

Left to itself the timezone is auto-derived from the exit IP, so the browser's
clock and the address it comes from tell the same story without you pinning
anything.

## The honest caveat: what a real-looking browser does not fix

This is the part that decides real outcomes, and it is identical for both
projects, because it lives outside the browser entirely.

A browser that reads as genuine still does not override:

- **IP reputation.** A perfect fingerprint on a datacenter range, or on a
  residential IP a thousand other automated sessions are using this minute, is
  still on a bad address. Around [90% of proxies are public](configuration.md), so their IPs
  are already known and blocked before you send a single request. The fingerprint work cannot see the
  address, let alone launder it.
- **Per-account quotas and rate limits.** These are counted server-side against
  your account and your address. Looking like a real browser does not raise the
  ceiling; it just means you hit it wearing a better outfit.
- **Behaviour and timing.** A pointer that teleports, keystrokes at a metronome
  interval, a form filled in eighty milliseconds, a session with no scroll - none
  of that is a browser property. `invisible_playwright` moves the cursor on a
  Bezier curve, which handles the crudest version, but the pacing of a real task
  is yours to supply.

So the honest answer to "will this get me through" is: it removes the
fingerprint, TLS and driver-layer reasons you were flagged, which is most of the
common ones, and it leaves the address and the behaviour to you. A suppressed or
blocked signal is itself a tell, so
[test for the presence of the right signal, not the absence of a wrong
one](how-to-test-bot-detection.md), and compare against a stock browser rather
than reading a verdict.

## Choosing between them

Pick on the constraints you actually have, not on which disguise sounds cleverer.

Reach for `puppeteer-real-browser` if your stack is Node and Puppeteer, you want
Chrome specifically, and a built-in captcha-solving helper is something you would
otherwise wire up yourself. It keeps you inside one ecosystem and one language.

Reach for `invisible_playwright` if you are in Python, you want the realness in
the engine rather than in a runtime layer a page can interrogate, and
reproducibility matters - the seed makes a failing run replayable, and stock
Playwright means there is no new API to learn. It is Firefox, not Chrome, which
is a real difference if a target expects one specific engine.

Neither choice changes the caveat above. Both are a browser layer. The proxy and
the pacing sit on top of whichever you pick.

## Conclusion

The comparison is not "which one evades more", because neither evades on its own -
they make the browser honest-looking and hand the rest back to you.
`puppeteer-real-browser` disguises a real Chrome from JavaScript at runtime;
`invisible_playwright` ships a Firefox with the spoofs compiled in and drives it
with stock Playwright from Python. The runtime-versus-compiled split is the
durable difference, and it decides only the fingerprint and driver layer. It
passes most detection because it looks like a real browser driven by a real
person. It does not, and no browser layer can, fix a burned IP, a spent quota, or
a robotic rhythm.

## Short answers to the questions that lead here

**What is the difference between puppeteer-real-browser and
invisible_playwright?** One is Node driving real Chrome with runtime
stealth-plugin property patches; the other is Python driving a Firefox with the
spoofs compiled into the binary. Runtime overrides versus a compiled build is the
core split.

**Which one is more undetectable?** Neither is undetectable, and any tool that
tells you it is, is selling something. Both remove the fingerprint and
driver-layer tells; both leave the IP, the quota and the behaviour to you.

**Does invisible_playwright use a stealth plugin?** No. The values live in the
compiled Firefox and its prefs, so there is no runtime override for a
`toString()` or native-code check to catch.

**Can I keep my existing Playwright code?** Yes. The launch is two lines and the
returned object is a real Playwright `Browser`, so every method works as
documented upstream.

**Will either one solve captchas?** `puppeteer-real-browser` ships an optional
captcha helper; `invisible_playwright` does not bundle one. A reCAPTCHA that
scores you on behaviour and IP is not a browser-layer problem either way.

**It looks like a real browser but I still get blocked. Why?** Because the block
is probably not the browser. Check the address, the request rate against your
account, and the interaction timing, in that order.

## Sources

- `puppeteer-real-browser`'s own GitHub repository, [ZFC-Digital/puppeteer-real-browser](https://github.com/ZFC-Digital/puppeteer-real-browser), read 2026-08-29, for how it
  launches Chrome and applies runtime evasions, rather than from second-hand
  summaries.
- This project's own quickstart and configuration pages for the API shown above,
  and its release gates for the caveat that a browser layer does not touch the
  address or the behaviour.

**See also:** [why toString and native-code checks catch runtime
patches](tostring-native-code-detection.md), the [standing comparison with the JS
stealth-plugin approach](vs-playwright-stealth.md), and [how to test whether your
browser is actually detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed in the
example is real; the promise that a good fingerprint fixes a bad IP is not, and
this page exists partly to say so.*
