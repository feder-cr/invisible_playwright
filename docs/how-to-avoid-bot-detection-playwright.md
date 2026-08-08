---
title: "How to avoid bot detection with Playwright"
description: "A proactive guide to avoiding Playwright bot detection: navigator.webdriver patches are level one, a patched engine reaches the rest, with runnable code."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 8
---

# How to avoid bot detection with Playwright

**Avoiding bot detection in Playwright means fixing the right layer before you launch
a session, not patching properties after a site has already blocked you.** Detection
is decided at a layer most stealth work never reaches. Patching
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
and its neighbours is real, it is worth doing, and it is also the cheapest thing a
detector checks. The durable tells are not values you can redefine from a page
script. They are outputs a real machine produces and a script cannot: what the
engine actually renders, what it can actually decrypt, how a pointer actually moves.
Building toward that from the start, rather than patching properties after a block
shows up, is the difference this page is about.

This page is written for before you launch a session, not after one gets blocked. If
you already have a specific site turning you away, [the reactive
checklist](playwright-detected-as-bot.md) is the faster read. If you want the full
five-layer map of why sites block automation at all, [that overview is
here](how-to-scrape-without-getting-blocked.md). This page sits next to both: it is
the proactive version, the decisions to make and the code to write before the first
request goes out, so you spend effort on the layer that actually decides the outcome
instead of the layer that is easiest to patch.

## Decide which layer of Playwright bot detection you are fixing

Stealth advice reads as one undifferentiated pile of tips, and that is the first
mistake to avoid. It operates at three distinct levels, and each one has a different
ceiling:

1. **The page.** A script runs before the site's own code and redefines properties:
   `navigator.webdriver`, the plugin list, permission queries.
2. **The driver.** The automation layer itself stops setting the flags and leaving
   the bindings that give it away, so there is nothing left on the page to redefine.
3. **The engine.** The browser's own C++ source reports different values, compiled
   in, through the same code path a normal build uses.

[The full breakdown of what each level can and cannot reach is
here](playwright-stealth-levels.md). The proactive move is to pick a level
deliberately, before you write a line of scraping code, rather than discovering the
ceiling of level 1 three weeks into a project when a site starts checking something
a page script cannot touch.

## Start from what a level-1 patch actually buys you

`navigator.webdriver` is the property everyone reaches for first, and it is worth
doing correctly rather than skipping. The naive version looks like this:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.firefox.launch()
    page = browser.new_page()
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
    """)
    page.goto("https://example.com")
```

This is a real fix for a real, cheap check, and on a naive detector it works. It is
also the ceiling of level 1, for reasons worth knowing before you rely on it:

- A clean, non-automated browser reports `undefined` for this property, not `false`.
  Setting it to `false` is a value no real browser sends.
- The property is now an own property on the `navigator` instance instead of an
  inherited one on the prototype, which is a one-line check.
- The getter you wrote is a JavaScript function with its own source, and
  `Function.prototype.toString` on it does not read `[native code]`.

None of that is a reason to skip the patch. It is a reason not to stop there.
[The full mechanics, including the timing race between your init script and the
page's own code, are written up here](navigator-webdriver-explained.md).

## Know that level 1 cannot reach the machine or the handshake

Even a perfect page-level patch leaves three things exactly as they were, because
none of them are JavaScript properties:

- **The machine.** The GPU string, the installed fonts, the audio device, the
  screen. A server reports server defaults regardless of what the page claims.
- **The handshake.** TLS and HTTP/2 are negotiated before any page-level script has
  had a chance to run.
- **The behaviour.** How the pointer moves, how a form gets filled, whether the page
  is ever scrolled.

This is the argument for moving down a level rather than writing a longer init
script. A property can be redefined by anyone with a console. An output has to be
produced by something that actually has the capability being tested.

## The capability check a Playwright property patch cannot pass

A capability check asks the browser to do something rather than report something,
which is why a page-level patch cannot pass one. Here is a concrete version you can
run yourself against whatever Playwright launches today.

Playwright's managed Chromium, as of the 1.57 default, ships Chrome's codecs. It
does not ship Chrome's DRM module. Ask a live page to negotiate a protected session
through the [Encrypted Media Extensions
API](https://developer.mozilla.org/en-US/docs/Web/API/Encrypted_Media_Extensions_API)
and read the answer directly:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://example.com")
    result = page.evaluate("""
        () => navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{
            initDataTypes: ['cenc'],
            videoCapabilities: [{ contentType: 'video/mp4; codecs="avc1.42E01E"' }],
        }]).then(() => 'yes').catch(() => 'no')
    """)
    print("widevine:", result)
```

On a default managed Chromium this prints `no`. Setting the user agent to claim
Chrome does not change the answer, because the missing piece is a DRM module, not a
string. [The full argument, including why this is a capability rather than a
value and what closes it, is here](chromium-is-not-chrome.md). No `add_init_script`
reaches this, because there is no property to redefine. The module either exists in
the binary or it does not.

A patched Firefox has no equivalent split to exploit or defend. There is no
widely-used stripped Firefox that automation runs and real visitors do not, so a
capability check like this one is not a live front for the engine this project
patches. That structural difference, and its real cost (Firefox is a smaller share
of traffic to begin with), [is covered in full
here](firefox-vs-chromium-antidetect.md).

## The two-line Playwright switch, and what it buys at each level

`invisible_playwright` patches the C++ source of Firefox itself and ships the
compiled binary, driven by stock Playwright with no wrapped subset of its API. The
switch from plain Playwright is two lines:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

`browser` here is a real `playwright.sync_api.Browser`. Every Playwright method
after that line works exactly as documented upstream, including `page.evaluate`, so
the Widevine check above runs unchanged against it if you want to see the
difference for yourself: a build with no proprietary-versus-open split has nothing
for that particular check to catch, because there is no Firefox that ships the
codecs and a separate Firefox that automation runs.

What the `seed` buys, specifically: roughly 400 fingerprint fields (GPU, canvas,
audio context, fonts, screen) come back **identical on every run** with the same
seed, and a fresh, distinct identity on every run without one. That determinism is
what turns "it worked yesterday" into something you can actually bisect when a site
starts blocking - change one thing at a time against a fixed identity instead of
guessing against a new random machine every launch.

## Give the behaviour a machine to match

A consistent fingerprint sitting under a pointer that teleports between coordinates
is still going to look automated to anything watching behaviour rather than
properties. `page.click` above does not send the cursor in a straight line or teleport
it to the target; it moves it along a Bezier curve first, because a form filled in
zero simulated motion is its own tell regardless of how good the fingerprint under it
is.

Two things follow from that, worth deciding before a target ever sees your session
rather than after:

- Do not layer a second stealth plugin on top of an engine-level fingerprint.
  Two systems answering the same questions about the machine will not always agree
  with each other, and the disagreement is easier to find than either alone.
- Space requests out. A perfectly disguised session making requests at machine
  speed produces a velocity signal that no per-request fingerprint hides.

## A short pre-launch checklist for Playwright bot detection

Before pointing any of this at a target you have not tested against yet:

1. Pick the level deliberately (page, driver, or engine) based on what your target
   actually needs, using [the full comparison](playwright-stealth-levels.md).
2. Log the seed for every session you run against a real target, so a later failure
   is something you can replay rather than something you have to guess at again.
3. Run one of the public suites and [read the whole report, not just the
   verdict](how-to-test-bot-detection.md), before you assume a level is enough.
4. Compare against a stock browser on the same machine, field by field, rather than
   trusting a single score.
5. Decide up front whether your target cares about codec or DRM capability checks;
   if it does, [know which engines have that gap and which do not](chromium-is-not-chrome.md)
   before you pick one.

## Conclusion

Avoiding bot detection proactively means deciding, before the first request, which
layer you are actually defending: the page, the driver, or the engine. A page-level
patch is fast and worth doing, and its ceiling is real: it cannot touch the machine,
the handshake, or a capability check that asks the browser to do something rather
than report something. The durable fixes live one or two levels down, in a driver
that never sets the tell in the first place or an engine that has nothing to lie
about because it is reporting values compiled into the binary. Pick the level your
target actually requires, prove it with a capability check rather than a property
read, and keep the identity seeded so a failure is something you can reproduce
instead of something you have to re-guess.

## Short answers to the questions that lead here

**What is the single most effective way to avoid bot detection in Playwright?**
There is no single fix, because detection is layered. Patch the obvious properties,
then check whether your target reads the machine, the handshake, or behaviour before
assuming you are done.

**Is patching `navigator.webdriver` enough?** No. It defeats the cheapest check a
detector runs and does nothing for the GPU, fonts, TLS handshake, or a capability
check like DRM negotiation, none of which are JavaScript properties.

**Do I need a stealth plugin, a patched driver, or a patched browser?** Depends on
what your target checks. A plugin fixes obvious property reads. A patched driver
removes the automation flags at the source. A patched engine is the only one of the
three that reaches the machine and capability checks a page script cannot see.

**Can I prove a Chromium-based tool is lying about being Chrome?** Yes, in one line:
ask it to negotiate a Widevine session. A missing DRM module is missing code, and no
user agent string changes the answer.

**Does a random fingerprint every run help or hurt?** It helps you blend in and
hurts you the moment something breaks, because you cannot tell whether the site
changed or your fingerprint did. A seeded, reproducible identity turns a failure
into something you can bisect.

**Should I combine a stealth plugin with a stealth browser?** No. Two layers
answering the same questions about the same browser will disagree eventually, and
the disagreement is its own signal.

## Sources

- [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
  the specified property behind the level-1 patch above.
- [The Encrypted Media Extensions
  API](https://developer.mozilla.org/en-US/docs/Web/API/Encrypted_Media_Extensions_API),
  for the `requestMediaKeySystemAccess` capability check above.
- [Three ways to make Playwright undetected](playwright-stealth-levels.md) and
  [why navigator.webdriver is not the tell people think it is](navigator-webdriver-explained.md),
  for the level-1 mechanics above.
- [Chromium is not Chrome](chromium-is-not-chrome.md), for the Widevine capability
  check and the direct tests it was run against, dated 2026-07-30 in that page's own
  sources.
- [Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md),
  for the structural argument and its honest cost.

**See also:** [How to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for the full five-layer map this page assumes, [the reactive troubleshooting
checklist](playwright-detected-as-bot.md) for when a specific site has already
turned you away, and [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the method to run before you trust any of the above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level and driven by stock Playwright. The Widevine check
above is one we run against our own binary before every release, not just a claim
about someone else's.*
