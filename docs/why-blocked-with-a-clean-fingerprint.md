---
title: "Why am I blocked with a clean fingerprint?"
description: "You pass CreepJS, BotD and sannysoft and still get blocked. The fingerprint is only one of four independent layers - here is how to isolate which one is failing."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 8
---


# Why am I blocked with a clean fingerprint?

You checked the thing everyone tells you to check. The fingerprint reads as a real
Firefox: CreepJS finds nothing contradictory, BotD returns human, sannysoft is all
green, the TLS handshake matches the browser you claim to be. And the session still
gets a challenge, a short body, or the wrong page.

This is the most common frustration with automation stealth, and it has a specific
cause: a clean fingerprint is one of four independent layers, and the other three do
not care how real your browser looks. This page is how to find which layer is actually
blocking you, so you stop re-tuning a fingerprint that was never the problem.

## The four layers, and why fixing one does nothing for the others

A detector is not one gate. It is several, scored separately and combined at the end.
invisible_playwright is built to pass two of them and cannot touch the other two,
because the other two are not browser properties at all.

- **Fingerprint and driver.** What the browser reports about itself: GPU, canvas,
  audio, fonts, screen, and the automation tells like
  [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver).
  Plus the TLS handshake, which is decided before any page loads. This is the layer the
  product owns. A patched Firefox driven by stock Playwright reads as a genuine Firefox
  because it is one.
- **IP reputation.** Whether your exit address is a datacenter range, a known proxy, or
  an address a thousand other automated clients are using this minute. Not a property of
  the browser. You supply this, with a clean proxy.
- **Rate and volume.** How many requests you make and how fast. A perfect browser making
  forty requests a second is not a human, and nothing in the fingerprint changes that.
  You supply this, with pacing.
- **Session and account.** Cookie history, a warmed session, a logged-in identity that
  is not brand new hitting a first-request quota. You supply this, by reusing sessions
  instead of starting cold every time.

The reason fixing the fingerprint again does nothing is that these four are scored
independently. A flawless score on the first layer cannot raise the other three. If the
block is coming from the IP, you can regenerate identities all day and see no change,
because you keep improving the one number that was already fine.

## Start from a browser that removes the first layer from the equation

The point of a real-browser engine is that it takes the fingerprint and driver layer
off the table, so when you debug you are debugging the other three instead of chasing
ghosts in the first. Switching from plain Playwright is a two-line change, and the
returned object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser) with every method
intact:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 pins the identity: same GPU, canvas, audio, fonts and screen every run,
# so a failing run is reproducible rather than a different browser each time
with InvisiblePlaywright(seed=42, proxy={
    "server": "socks5://gate.example.com:1080",
    "username": "u",
    "password": "p",
}) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listings")
    print(page.title())
```

Pinning the seed matters for this specific problem. If every run draws a new
fingerprint, a failing run tells you nothing, because you cannot separate the site
changing from the machine changing. With a fixed seed the browser is a constant, so any
difference in outcome has to come from one of the other three layers. That is the whole
diagnostic move: hold the layer you already trust still, and vary the others one at a
time.

## Isolate the layer: change one input, retry, repeat

Do not tune. Isolate. Change exactly one thing between runs and watch whether the block
moves. Because the domain is non-deterministic, treat each configuration as at least ten
runs, not one, and compare rates rather than single outcomes.

**First, prove it is detection and not the IP.** Open the same URL by hand from the same
machine and network that runs the automation. If the manual visit also gets the wrong
page, the browser is not the problem and neither is your code:
[work the detection checklist from the top](playwright-detected-as-bot.md), whose step
zero is exactly this.

**Then change only the IP.** Keep the seed fixed, swap the proxy exit, retry. If the
block clears, it was IP reputation, and no fingerprint work would ever have fixed it.
Around ninety percent of public proxy IPs are already known and blocked before you send
a single request, so a fresh residential exit is often the entire fix.

**Then change only the rate.** Keep the seed and the IP, slow down. Add real gaps
between requests and between actions inside a page. If the block clears when you pace it,
you were producing a velocity signal no human could:
[per-scraper rate limiting](how-to-rate-limit-your-scraper-playwright.md) is the
structural version of this, and
[backing off correctly when 403 and 429 start mid-run](how-to-handle-403-429-backoff-mid-scrape-playwright.md)
is what keeps a rate problem from cascading into a ban.

**Then warm a session.** Keep everything, but do not arrive cold at the protected page.
Load a neutral entry page first, let cookies set, reuse that context. If the block clears
once there is history, it was the session or account layer: a brand-new client with no
cookies hitting a quota-limited endpoint on its first request.

By the time you have varied IP, rate and session with the fingerprint held constant, the
layer doing the blocking has named itself. You have measured it instead of guessing.

## An honest measurement, and the honest caveat

To show the shape of this concretely rather than as a slogan: on the public suites, a
seeded session reads as a genuine desktop Firefox. CreepJS finds no lie in the
descriptors, BotD returns a human verdict, and the reported GPU, audio and font set are
internally consistent with a Windows machine and with each other. Those are real results
and they are why most detection checks pass.

The caveat is the entire point of this page. That same session, on a flagged datacenter
IP, still loses. That same session, making superhuman request volume, still loses. That
same session, brand new with no cookie history against a first-request quota, still
loses. invisible_playwright handles the fingerprint and driver layer, and handles it
well enough that you can stop thinking about it. It does not fix IP reputation, rate, or
session history, and any tool that claims to make you undetectable everywhere is claiming
something no browser property can deliver. A real browser driven like a real person
passes most checks. It does not pass a check that is not about the browser.

## Do not re-tune a fingerprint that is already real

The trap this page exists to break is the loop where a block sends you back to the
fingerprint every time, because that is the layer you know how to change. If the seeded
browser already reads as real, changing it again is motion without progress, and it can
make things worse: a spoof that overcorrects can differ from a real browser in the other
direction. When the fingerprint is confirmed clean,
[confirm it with a stock-browser comparison rather than a verdict](how-to-test-bot-detection.md),
then leave it alone and move to the IP, the rate, and the session. Those are where a
clean-fingerprint block almost always lives.

## Conclusion

Being blocked with a clean fingerprint is not a contradiction, it is information: the
signal doing the blocking is one of the three you have not addressed yet. Hold the
browser constant with a fixed seed, then change the IP alone, the rate alone, and the
session alone, ten runs each, until the block moves. The layer that moves it is the layer
to fix. The fingerprint, once it reads as real, is the one thing on the list you can stop
touching.

## Short answers to the questions that lead here

**Why am I blocked when CreepJS and BotD both pass?** Because those suites only score the
fingerprint and driver layer. IP reputation, request rate, and session history are scored
separately and none of them care that your browser looks real.

**Does a clean fingerprint mean I will not be detected?** No. It means one of four layers
is clean. A real browser on a flagged IP, or making superhuman request volume, is still
detectable on layers the fingerprint never touches.

**How do I tell which layer is blocking me?** Hold the seed fixed and change one input at
a time: the proxy IP, then the request rate, then whether the session is warmed. The
change that clears the block names the layer.

**Is it the proxy or my code?** Open the same URL by hand from the machine that runs the
automation. If the manual visit fails too, it is the IP or the network, not your browser
or your code.

**Why does re-tuning the fingerprint never help?** Because if it already reads as real,
you are improving a score that was already fine while the actual blocking signal, on
another layer, stays exactly where it was.

**Can invisible_playwright make me undetectable?** No, and no honest tool can. It makes
the browser and driver read as a genuine Firefox, which passes most checks. You still
supply a clean IP, human pacing, and session reuse for the layers a browser cannot change.

## Sources

- This project's own public-suite gates, which confirm the fingerprint and driver layer
  reads as a genuine Firefox, and which by construction say nothing about the IP, rate,
  or session layers.
- The detection-checklist and rate-limiting notes in this documentation set, linked
  throughout, each written from a specific failure on one of the four layers.
- MDN, [`Navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver) -
  the specification for the automation flag referenced above.
- Playwright, [`Browser` class reference](https://playwright.dev/python/docs/api/class-browser) -
  the API the returned object implements unchanged.

**See also:** [the full detection checklist in working order](playwright-detected-as-bot.md),
[how to test whether the browser itself is the problem](how-to-test-bot-detection.md), and
[rate limiting your scraper so volume stops being the signal](how-to-rate-limit-your-scraper-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The four-layer split is the
model I reach for every time a clean browser still gets the wrong page.*
