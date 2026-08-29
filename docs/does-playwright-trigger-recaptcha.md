---
title: "Does Playwright Trigger reCAPTCHA More Often?"
description: "Why Playwright triggers reCAPTCHA more often. How reCAPTCHA builds risk from fingerprint, IP, session and behavior, and which inputs a browser engine can control."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 13
---


# Does Playwright Trigger reCAPTCHA More Often?

Often, yes, but not for the reason most people assume. There is no single "this is
Playwright" flag that reCAPTCHA flips. What it does is build a risk score out of many
inputs, and a default Playwright session happens to push several of those inputs in the
same wrong direction at once.

That distinction matters, because it tells you which parts of the problem the browser can
fix and which parts it cannot. This page walks the inputs, shows which one a coherent
real-browser engine moves, and is honest about the ones that no browser setting touches.

## reCAPTCHA is a score, not a switch

The useful mental model is a risk score between "obviously a person" and "obviously
automated", with a threshold somewhere in the middle. Below the threshold you get a quiet
pass or an invisible check. Above it you get a challenge, and above that you get blocked
outright.

Nothing about that score is a boolean. You do not "fail the Playwright test" and you do
not "pass" it. You accumulate a number, and every input either adds to it or does not.
This is the same machinery described in
[how a browser trust score is actually assembled](browser-trust-score-explained.md): a
weighted sum, not a checklist, which is why fixing one obvious tell can leave you still
above the line.

So the right question is not "does Playwright get detected". It is "which inputs does a
default Playwright session push over the threshold, and which of those can I move".

## The inputs that feed the score

Four families of input matter here, and they are largely independent of each other.

- **Fingerprint coherence.** Not whether any single value is unusual, but whether the
  values agree. A user agent claiming one platform against APIs that behave like another,
  a GPU string that does not match how pixels actually render, a font set that does not
  belong to the claimed operating system. Detectors mostly check agreement, not novelty.
- **IP reputation.** The address and its ASN. A datacenter range, an exit that a thousand
  other sessions used this minute, a country that disagrees with the rest of the session.
- **Session and cookie age.** A brand-new context with no cookies and no history looks
  different from a browser someone has actually used. reCAPTCHA reads longevity and prior
  interaction as signal.
- **Interaction pattern.** Pointer motion, typing rhythm, the pause before a click. A form
  filled in eighty milliseconds and a pointer that teleports between coordinates both
  score against you.

A default Playwright launch tends to be bad at the first and the third for free: a fresh
automated context ships an inconsistent fingerprint and a historyless session in the same
breath. That is most of why the challenge rate is higher than a hand-driven browser's,
before you have touched the proxy at all.

## Which input the browser layer actually moves

Of those four, exactly one is fully inside the browser's gift: fingerprint coherence.

Stock Playwright driving stock Firefox or Chromium leaks a scattering of small
disagreements. Some are automation tells like
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver), a
standard property that reports true whenever the user agent is under automation control. More
are consistency gaps: a headless context with no real GPU, a screen size no display has, a codec
list or font set that does not match the claimed platform. Each is a small addition to the
score, and together they are the fingerprint contribution.

You can see this contribution directly rather than taking it on faith. Open the same
detector page in your automated browser and in a stock browser on the same machine, and
diff the reports field by field. The
[method for doing that comparison honestly](how-to-test-bot-detection.md) is worth reading
before you trust any single verdict, because a green score can also mean a broken feature
that leaks nothing.

The other three inputs are not browser properties at all, which is the honest half of this
page and the reason a "stealth browser" alone is never the whole answer.

## What invisible_playwright does, and a two-line example

invisible_playwright is a Firefox patched at the C++ level and driven by stock Playwright.
Its job on this specific problem is narrow and it is the fingerprint input: instead of a
scattering of disagreements, it presents one coherent real Firefox. GPU, audio, fonts,
screen and roughly 400 fields are derived together from a single seed, so they agree with
each other and with a genuine desktop Firefox rather than contradicting one another.

Switching from plain Playwright is two lines, and every Playwright method works unchanged
because the returned object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser):

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the identity reproducible: same GPU, fonts, canvas hash every run
with InvisiblePlaywright(seed=42, proxy={
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}) as browser:
    page = browser.new_page()
    page.goto("https://example.com/form")
    page.click("#submit")   # pointer arcs to the button on a Bezier curve
```

The reproducible seed is what makes the fingerprint input debuggable: if a run draws a
challenge, replay the exact same identity and change one variable at a time, rather than
guessing whether the site changed or the machine did.

What this buys you is a lower fingerprint contribution to the score. The TLS handshake and
driver layer also read as a genuine Firefox rather than as something impersonating one,
which closes the network-layer tells covered in
[JA3 and JA4 TLS fingerprinting](ja3-ja4-tls-fingerprint.md). It does not touch the other
three inputs, and it does not "solve" the score. There is no such thing.

## The inputs the browser cannot move

A coherent fingerprint on a bad address is still on a bad address. These are the inputs you
have to supply yourself, and no browser setting substitutes for them:

- **IP reputation.** Around ninety percent of proxies are already known and blocked before
  you use them. A perfect browser on a listed datacenter IP still scores high. This is the
  first thing to rule out, and the [Docker and datacenter side of the
  problem](how-to-run-playwright-docker-undetected.md) is where most server-side sessions
  actually lose.
- **Session age.** A brand-new context with zero cookies reads as fresh every time. Warming
  a session, reusing storage state, and not throwing away every cookie between runs all move
  this input, and none of them are the browser's job.
- **Behaviour and timing.** Mechanical pacing scores against you regardless of how real the
  browser looks. Human-shaped delays and real pointer motion are things you pace from your
  own code. invisible_playwright supplies Bezier-curve mouse motion, but the rhythm of when
  you act is yours.

If your fingerprint is coherent and you are still challenged, the score is coming from one
of these three, and buying a better browser will not change it.

## Conclusion

Playwright meets reCAPTCHA challenges more often because a default automated session pushes
several score inputs the wrong way at once, chiefly an inconsistent fingerprint and a fresh,
historyless session. A real-browser engine like invisible_playwright moves the fingerprint
input, and the TLS and driver layers with it, which is a real and measurable reduction in
one contribution to the score. It does not, and cannot, fix IP reputation, session age, or
your timing. Treat reCAPTCHA as a sum you lower from several directions, not a switch one
tool flips, and pair a coherent browser with a clean exit and human pacing.

## Short answers to the questions that lead here

**Does Playwright itself get flagged by reCAPTCHA?** Not as a single flag. A default
Playwright session raises the risk score mainly through an inconsistent fingerprint and a
brand-new session with no history, which together read as automation.

**Will a stealth browser stop reCAPTCHA challenges?** It lowers one input, the fingerprint,
and the TLS layer with it. It does nothing for your IP, your session age, or your timing, so
on its own it does not stop challenges.

**Can any browser guarantee a pass?** No. reCAPTCHA produces a score from inputs the browser
does not control, so no browser setting "solves" it. Anyone claiming otherwise is selling
something.

**Why do I still get challenged with a perfect fingerprint?** Because the score has other
inputs. A datacenter IP, a cookieless session, or mechanical timing can each push you over
the threshold on their own.

**Does a reproducible seed help here?** Yes, for debugging. The same seed gives the same
machine every run, so a challenge can be replayed exactly instead of chasing a new random
identity each time.

**Is it the proxy or the browser?** Test it: open the same page by hand from the same
machine and network. If the manual visit is also challenged, it is the exit, and the
[detection checklist](playwright-detected-as-bot.md) works that split in order.

## Sources

- Google's own reCAPTCHA v3 documentation, [reCAPTCHA v3](https://developers.google.com/recaptcha/docs/v3),
  retrieved 2026-08-29, for the score-and-threshold behavior this page treats as its working
  model throughout.
- This project's fingerprint generation, which derives roughly 400 fields from one seed so
  they agree with each other rather than contradicting.
- The comparison method in the testing guide linked above, used to read the fingerprint
  contribution to a score directly rather than trusting a verdict.
- Public detector suites ([CreepJS](https://github.com/abrahamjuliot/creepjs),
  [BotD](https://github.com/fingerprintjs/BotD),
  [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs),
  [sannysoft](https://bot.sannysoft.com/), [BrowserLeaks](https://browserleaks.com/)), each
  read from its own source, retrieved 2026-08-28, for what a coherence check actually
  inspects.

**See also:** [how a browser trust score is assembled](browser-trust-score-explained.md),
[why a fresh browser scores low on reCAPTCHA v3](recaptcha-v3-score.md),
[the checklist for being detected on one site](playwright-detected-as-bot.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It moves the fingerprint
input to a reCAPTCHA score; the clean exit and the human pacing are still yours to bring.*
