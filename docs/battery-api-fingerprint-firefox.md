---
title: "Battery API fingerprint: does Firefox expose it?"
description: "Does desktop Firefox expose the Battery Status API? No, it was removed in 2017, so a battery object under a Firefox user agent gives away a fake browser."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 29
---


# Battery API fingerprint: does Firefox expose it?

Short version: on the desktop, no. Firefox removed the
[Battery Status API](https://developer.mozilla.org/en-US/docs/Web/API/Battery_Status_API)'s
`navigator.getBattery()` from web content in the 52 series back in 2017, on privacy
grounds, and it has never come back.
So a real desktop Firefox has no Battery Status API at all. `navigator.getBattery` is
`undefined`, and there is nothing to read.

That single fact turns the Battery API into a small but reliable consistency check. A
page that claims to be Firefox in its user agent, and then successfully obtains a
battery object, has not measured your battery. It has caught a browser that is lying
about what it is. This page explains why that happens, why the honest answer is to have
no Battery API rather than a convincing fake one, and how a genuine Firefox build gets
that for free.

## What the Battery Status API was, and why Firefox dropped it

The Battery Status API let a page call `navigator.getBattery()` and read the charge
level, whether the device was plugged in, and the seconds until full or empty. It was
meant for power-aware pages that could defer heavy work on a low battery.

The privacy problem showed up quickly. The charge level was reported with enough
precision, and it changed slowly enough, that the combination of level plus
charging state plus time-remaining behaved like a short-lived identifier: two page
loads seconds apart saw the same unusual value, and that value could be used to correlate
visitors even across contexts that were supposed to be isolated. Firefox responded by
removing the API from web content entirely in the
[52 series in 2017](https://developer.mozilla.org/en-US/docs/Mozilla/Firefox/Releases/52),
restricting it to chrome/privileged code only. Chromium kept a
reduced version. That divergence is the whole point for fingerprinting: the presence or
absence of `getBattery` is now a per-engine trait, not a per-device one.

## Why absence is the honest answer, and a fake battery is the tell

A fake battery reading is the riskier of the two outcomes, because an absence matches
every real Firefox automatically while a fake value has to survive every angle a
detector can check it from. There are two ways an automation stack can end up
"consistent" with a Firefox user agent on this surface.

The first is to actually be a Firefox build, in which case `navigator.getBattery` is
simply not there, because the engine never defined it. Nothing is spoofed. There is no
value to keep plausible over time, no charging animation to fake, no drain rate to model.
The gap in the API is real, so it survives every probe automatically.

The second is to run an engine that does expose the Battery API, usually a Chromium base,
and then bolt on a JavaScript layer that tries to look like Firefox. Now the stealth
layer has a decision to make. If it leaves `getBattery` in place, the page reads a battery
object from a claimed-Firefox browser, which no real Firefox produces, and that is a
direct contradiction.

If it deletes `getBattery` to hide the mismatch, it has to delete it
convincingly: the property has to be gone from the prototype, absent from a
`for..in` walk, missing from `Object.getOwnPropertyNames`, and it has to stay missing when
a detector fetches a clean copy of `Navigator.prototype` from a fresh iframe and compares.
A partial delete that patches the obvious access but leaves a trace elsewhere is worse than
leaving the API alone, because now there is both a Firefox user agent and evidence of
tampering. This is the pattern CreepJS is built to catch: it does not ask what you report,
it asks whether the shape of your environment is internally consistent and whether anything
looks deleted.

The general rule, which the Battery API illustrates cleanly, is that adding a fake signal
is almost always more dangerous than a real absence. An absence is one bit that matches
every other Firefox on earth. A fake is a value you now have to defend against every angle
a detector can approach it from.

## How the patched build inherits the gap natively

invisible_playwright drives a Firefox that is patched at the C++ level, not a different
engine wearing a Firefox user agent. Because the base is a real Firefox from the current
release line, it inherits the same missing Battery API that every stock desktop Firefox
has had since 2017. There is no shim reintroducing `getBattery` and no shim removing it,
because there was never anything to remove. The surface is correct for the same reason a
stock Firefox is correct: the engine simply does not implement that API for web content.

That is the recurring theme across the fingerprint surface. A field is most robust when it
is genuinely produced by a real browser rather than reconstructed on top of one. The same
logic covers the identity fields that a page reads far more often than the battery, such
as [what `navigator.vendor` and `navigator.productSub` report on Firefox](navigator-vendor-productsub-firefox.md),
which are wrong on a Chromium base pretending to be Firefox and correct on an actual
Firefox build.

## Check it yourself in two lines

Switching from stock Playwright is a two-line change, and after that every standard
Playwright method works unchanged. Here is the whole probe: launch, open a page, and ask
the browser whether it has a Battery API.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""() => ({
        userAgent: navigator.userAgent,
        hasGetBattery: typeof navigator.getBattery,
        onPrototype: 'getBattery' in Navigator.prototype,
    })""")

    print(report)
```

On this build you get a Firefox user agent, `hasGetBattery` equal to `"undefined"`, and
`onPrototype` equal to `False`. That is the correct, boring result: a Firefox with no
Battery API, exactly like the one a person runs. The `seed=42` argument makes the whole
identity reproducible, so if you are comparing this against a stock Firefox on the same
machine you can rerun the exact same fingerprint rather than a fresh random one each time.

The comparison is the part worth doing by hand. Open the same probe in a stock desktop
Firefox and you will see the identical three values. Open it in a plain Chromium and
`hasGetBattery` comes back `"function"`. That contrast, run against a real reference
browser rather than read as a verdict, is the method the
[testing guide](how-to-test-bot-detection.md) argues for on every surface.

## What a passing battery check does not buy you

This is where honesty matters, because it is easy to overread a green result.

Having no Battery API, correctly, tells a detector that this browser looks like a genuine
Firefox on this one narrow surface. It says nothing whatsoever about your network. The
Battery API cannot see your IP address, your exit country, or how many requests you have
made in the last minute. A browser can be a flawless Firefox on every fingerprint field
and still be turned away because the exit IP is a known datacenter range, because the
account behind the session hit a per-account quota, because the requests arrived faster
than a human hands could produce them, or because the pointer never moved before the click.

invisible_playwright is designed to look like a real browser driven by a real person, and
that is genuinely why it passes most fingerprint, TLS and driver-layer checks: the engine
is a real Firefox, so the traits it reports are the traits Firefox actually has, this API
absence among them. But it does not supply a clean IP, it does not pace your actions, and
it does not manage per-account limits. Those are yours to bring: a reputable exit, human
timing, and sane volume. The [configuration guide](configuration.md) covers the proxy and
timezone side of that, and the [detected-on-one-site checklist](playwright-detected-as-bot.md)
walks the causes in the order they are usually the real culprit, with the network exit near
the bottom of the list rather than the top.

## Conclusion

The Battery Status API is a clean example of a detection signal that is best handled by not
existing. Desktop Firefox removed it in 2017, so the honest state for anything claiming to
be Firefox is to have no `getBattery` at all. A stealth layer that adds a fake battery
object to a non-Firefox engine creates the exact contradiction it was trying to avoid, and
one that tries to delete the API instead has to delete it perfectly or leave a tampering
trace. A patched real-Firefox build sidesteps the whole dilemma by inheriting the gap
natively. It is a small surface, it costs nothing to get right, and getting it right is
worth exactly as much as it is: one consistent bit, and not one bit more about your IP or
your behaviour.

## Short answers to the questions that lead here

**Does Firefox have a Battery API?** Not on the desktop for web content. It was removed in
the 52 series in 2017 for privacy reasons, so `navigator.getBattery` is `undefined`.

**Is a battery reading under a Firefox user agent a red flag?** Yes. A real desktop Firefox
cannot return a battery object, so a page that gets one from a claimed-Firefox browser has
caught a fake.

**Does Chrome still expose it?** Chromium kept a reduced Battery API, which is why the
presence or absence of `getBattery` now works as a per-engine trait rather than a
per-device one.

**Should a stealth tool fake a battery to look complete?** No. Adding a fake signal is more
dangerous than a real absence, because the fake has to survive every angle a detector can
probe from, while an absence matches every other Firefox automatically.

**Does invisible_playwright spoof the Battery API?** No. It drives a real patched Firefox,
so the API is simply absent the same way it is absent in a stock Firefox. There is nothing
to spoof.

**If my battery check passes, am I undetectable?** No. This surface says nothing about your
IP reputation, per-account quotas, rate limits, or behaviour. A clean proxy and human pacing
are still on you.

## Sources

- Mozilla's own [Firefox 52 release notes](https://developer.mozilla.org/en-US/docs/Mozilla/Firefox/Releases/52),
  which record the Battery Status API becoming available only to chrome/privileged code
  in that release, 2017.
- This project's own fingerprint parity checks, which read `navigator.getBattery` against a
  stock desktop Firefox and confirm both report the same absence.

**See also:** [what data websites collect about your browser](what-data-websites-collect-about-your-browser.md),
[the navigator.vendor and productSub story on Firefox](navigator-vendor-productsub-firefox.md),
and [how navigator.webdriver actually reads](navigator-webdriver-explained.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The most robust fingerprint
field is the one a real browser genuinely produces, and sometimes the one it genuinely does
not.*
