---
title: "selenium-driverless vs invisible_playwright stealth"
description: "selenium-driverless drives Chrome over CDP, closes driver-detection gap but keeps rendering/TLS stock. Compare to patched binary, seed-reproducible approach."
parent: "Comparisons"
nav_order: 25
---


# selenium-driverless vs invisible_playwright stealth

selenium-driverless is a real, established Python library with a substantial user
base. It keeps a
Selenium-style API but connects to a Chromium-based browser directly over the Chrome
DevTools Protocol, so there is no separate `chromedriver` process sitting between your
code and the browser. That single decision removes a whole class of tell, and it is worth
understanding precisely before comparing it to anything.

This page is about the one thing that design choice fixes, the two surfaces it does not
touch, how a patched-binary approach differs, and the caveat that applies equally to both.

## The gap selenium-driverless closes

selenium-driverless removes the middleman between your code and the browser. It speaks CDP
straight to the browser, so there is no `chromedriver` process to fingerprint and
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
is not forced on for you. That is a genuine improvement, and it is the same category of fix
that other modern tools make in their own way: [SeleniumBase UC Mode detaches the driver
during sensitive actions](vs-seleniumbase-uc-mode.md) rather than removing it, and stock
Playwright already avoids the Selenium driver model entirely. The driver layer, in 2026, is
[mostly a solved and mostly not-your-problem tell](navigator-webdriver-explained.md).

Classic Selenium, by contrast, launches a browser and talks to it through a driver binary
(`chromedriver` for Chrome). That binary is a process on the machine, and its presence and
behaviour have historically been detectable: it flips `navigator.webdriver` to `true`, it
leaves recognisable command-and-control patterns, and on some builds it is visible as a
distinct automation channel.

## What it leaves as stock Chrome

Closing the driver gap does nothing to the two surfaces that a serious detector reads
first, because neither one lives in the driver.

The **rendering surface** is stock Chrome: the WebGL renderer string and the pixels it
actually draws, the canvas hash, the audio context, the installed font list, the screen
geometry. On a real desktop with a GPU these look fine. On a headless server they announce
a datacenter, and a Selenium-style API over CDP changes none of it, because these values
come from the engine and the host, not from how you connect to them. This is the same
reason [Chromium and Firefox present different anti-detect trade-offs at the engine
level](firefox-vs-chromium-antidetect.md): the disguise you can apply is bounded by the
browser you are disguising.

The **network surface** is stock Chrome too. The TLS handshake and the HTTP/2 settings are
decided by the browser before any page loads, and they say "Chrome" whatever your user
agent claims. [No in-page trick reaches the handshake](ja3-ja4-tls-fingerprint.md); either
the browser really produces that fingerprint or it does not.

So selenium-driverless answers "is a driver process present" with "no", and leaves "does
the engine and the network read as a real desktop browser" exactly where stock Chrome
leaves it.

## What invisible_playwright does instead

invisible_playwright takes the other half of the problem as its starting point. It ships a
**Firefox patched at the C++ level** and drives it with stock Playwright, so the driver
question is handled the way Playwright already handles it, and the effort goes into the
engine and the identity.

The identity is **reproducible from a seed**. The same seed yields the same visitor
identity across every surface at once: the GPU string and the pixels it draws agree, the
canvas hash is stable, the audio context, the font list and the screen geometry are all
drawn from the same seed and are consistent with each other. That last word is the point.
[Detectors rarely flag an unusual value; they flag two values that should agree and do
not](playwright-detected-as-bot.md), and a per-surface random draw is exactly how you
produce those contradictions. One seed, one coherent machine.

Because the patch is in the engine rather than a page-level script, the rendering surface
is not a stock browser wearing a mask, and the TLS handshake is a real Firefox handshake
rather than a claim in a header. That is why it passes most fingerprint, TLS and
driver-layer checks: it is built to look like a real browser driven by a real person, not
to defeat a specific check.

## The same operation, in invisible_playwright

Switching from plain Playwright is a two-line change, and the object you get back is a real
Playwright `Browser` with every standard method. Here is a launch plus a navigation and a
click through a proxy:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

Pass a proxy the same way, and the browser timezone auto-derives from the exit IP unless
you pin it:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

The `seed=42` is the difference that matters for debugging: log it or pin it, and a failing
run is reproducible instead of a fresh random machine every time. If you need one specific
field to be a fixed value while the rest stay seed-derived, that is what
[pinning individual fingerprint fields](pinning.md) is for.

There is no wrapped subset of the API to learn. If you have Playwright code, the body is
unchanged; only the launch line differs.

## The honest caveat, which applies to both

Neither approach touches three things, and pretending otherwise would be the fast way to
get blocked while feeling safe.

- **Egress-IP reputation.** A coherent browser on a datacenter IP is still on a datacenter
  IP. A patched engine does not change your address, and a cheap proxy is often already on
  a block list before you send a single request. You supply a clean exit.
- **Per-account quotas and rate limits.** These are counted server-side against your
  account and your address, not read from the browser. No fingerprint work moves them.
- **Behaviour and timing.** A pointer that teleports, keystrokes at a metronome interval, a
  form filled in eighty milliseconds. invisible_playwright gives you Bezier-curve mouse
  motion, but pacing, dwell and the overall session shape are yours to get right.

So the honest framing is: invisible_playwright fixes the engine, the fingerprint and the
network surface, and selenium-driverless fixes the driver surface within stock Chrome.
Neither one fixes your address, your quota or your behaviour. The
[method for testing which of these is actually biting you](how-to-test-bot-detection.md) is
worth more than any single tool, because it tells you whether you have a browser problem or
one of the three the browser cannot solve.

## Conclusion

selenium-driverless makes a good, specific decision: drop the `chromedriver` binary by
speaking CDP directly, and the driver-detection gap closes with it. What it does not do,
and does not claim to, is change the engine underneath, so the rendering and TLS surfaces
stay stock Chrome. invisible_playwright starts from the other end, a patched Firefox with a
seed-reproducible identity that is consistent across every surface, which is why it reads
as a genuine browser to most fingerprint, TLS and driver checks. Both leave IP reputation,
quotas and behaviour to you, and the tool that admits that is the one to trust.

## Short answers to the questions that lead here

**Does selenium-driverless hide the fact that I am automating?** It removes the
`chromedriver` process and does not force `navigator.webdriver` on, which closes the driver
tell. It does not change that you are driving stock Chrome, so the engine and TLS surfaces
are unchanged.

**Is it undetectable?** No, and nothing honest claims to be. It fixes one surface. A
detector that reads the rendering or the handshake, or that watches your behaviour and your
IP, still has plenty to look at.

**How is invisible_playwright different?** It patches the Firefox engine and derives the
whole identity from a seed, so rendering and TLS are a real browser's rather than stock
Chrome's, and the same seed reproduces the same machine every run.

**Will either one get me past a block on its own?** Not if the block is your IP, your
account quota or your timing. Both fix the browser; you still supply a clean exit and
human-like pacing.

**Can I keep my existing Playwright code?** Yes. The launch line changes and the returned
object is a real Playwright `Browser`, so the rest of your script is unchanged.

**Why does the seed matter for stealth rather than just testing?** Because a coherent
identity is the point. One seed makes every surface agree with every other, and agreement
is what detectors actually check.

## Sources

- [selenium-driverless](https://github.com/kaliiiiiiiiii/Selenium-Driverless) project
  documentation and source, retrieved 2026-08-29, for the CDP-direct connection and
  Selenium-style API described above, rather than from a third-party summary.
- This project's own quickstart, configuration and release gates for the invisible_playwright
  API and the seed-reproducible fingerprint behaviour.

**See also:** [invisible_playwright vs SeleniumBase UC Mode](vs-seleniumbase-uc-mode.md)
for a driver-detached Chromium approach, [what navigator.webdriver actually
proves](navigator-webdriver-explained.md), and [the engine-level trade-offs between Firefox
and Chromium](firefox-vs-chromium-antidetect.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It passes most detection
because it is built to look like a real browser driven by a real person, and it still needs
a clean exit and human pacing from you.*
