---
title: "Does Playwright Set navigator.webdriver to True?"
description: "Stock Playwright reports navigator.webdriver as true, an old automation tell. Why patching it is detectable, and what a native false looks like to detectors."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 21
---


# Does Playwright Set navigator.webdriver to True?

Short version: yes. A browser launched by stock Playwright reports
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver) `=== true`, and it has done so for years. That single boolean is
one of the oldest and cheapest automation tells there is, which is exactly why so many
people try to patch it and why the patch usually makes things worse.

This page is what the flag is, why setting it to `false` from JavaScript is itself a
signal, what a native `false` looks like to a detector, and the honest limit of what
clearing it buys you.

## The one-line answer, and the check that proves it

The flag is specified. The [WebDriver standard](https://www.w3.org/TR/webdriver2/) says a browser under automation control
must expose `navigator.webdriver` as `true`, so Playwright, Selenium and any other
WebDriver-based driver all set it. Read it yourself:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate("() => navigator.webdriver"))
```

On a plain Playwright launch of the same engine that prints `True`. The value above
comes back `False`, and the rest of this page is about why the difference between those
two lines is not one character of JavaScript.

A clean, human-driven browser reports `false` here. Note that this is different from the
Chromium story, where an untouched browser reports `undefined` and a hand-set `false` is
itself an anomaly. On Firefox the honest value is a plain `false`, which is what this
engine returns.

## Why that boolean is such an old tell

It costs a detector nothing. One property read, no timing, no fingerprint math, no
iframe. It is the first line of almost every detection script ever written, and it
catches the enormous number of scrapers that never touch it at all.

Because it is so cheap, it is also the first thing every serious tool fixes, which means
that by itself it separates almost nobody from almost nobody. It matters for the reverse
reason: leaving it `true` marks you instantly, so it is table stakes rather than an
advantage. The interesting question is not whether the flag reads `false`, it is
*how* it came to read `false`, because that is where the second tell lives.

## Why a JavaScript patch of the flag is itself detectable

The obvious fix is to overwrite the property from a script that runs before the page:

```js
// the classic, and the trap
Object.defineProperty(navigator, "webdriver", { get: () => false });
```

This does make `navigator.webdriver` return `false`. It also leaves two fresh
fingerprints that are easier to check than the original flag.

**The getter is no longer native code.** In a real browser the property is backed by an
internal getter implemented in C++. Ask that getter to describe itself and it says so:

```js
Object.getOwnPropertyDescriptor(Navigator.prototype, "webdriver")
  .get.toString();
// real browser:  "function get webdriver() { [native code] }"
// JS override:   "() => false"       <-- the whole source, in plain sight
```

Every function in JavaScript carries its own source, and
[`Function.prototype.toString`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Function/toString) hands it back. A native builtin answers with the
`[native code]` placeholder; your arrow function answers with its actual body. So the
override that was supposed to hide automation has instead written the word `false` into
a place a detector can read directly. This is the general ceiling on page-level stealth,
and it is worth understanding on its own terms in
[the note on toString and the native-code check](tostring-native-code-detection.md).

**The property moved to the wrong object.** On a real Firefox the `webdriver` accessor
lives on `Navigator.prototype`, not on the `navigator` instance. `defineProperty` on
`navigator` puts an own property on the instance, so `navigator.hasOwnProperty("webdriver")`
flips from `false` to `true` and the descriptor now sits one rung lower on the prototype
chain than it should. A detector that walks the chain, which the tampering-focused suites
do by design, sees the shape change even when the value is correct.

The pattern is the one that runs through
[the whole checklist for being flagged on a single site](playwright-detected-as-bot.md):
a value fixed in isolation contradicts a value nobody thought to fix. Setting the boolean
right while leaving the getter's source and its location wrong is two new tells in
exchange for one old one.

## What a native false looks like to a detector

The way out is not a better shim, it is to not have a shim. This engine is a Firefox
patched at the C++ level, so `navigator.webdriver` is `false` because the browser's own
internals return `false`, the same code path a normal Firefox uses. There is no
JavaScript getter to inspect and no instance property to trip over:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    report = page.evaluate("""() => {
        const desc = Object.getOwnPropertyDescriptor(
            Navigator.prototype, "webdriver");
        return {
            value: navigator.webdriver,
            ownProp: navigator.hasOwnProperty("webdriver"),
            getterSource: desc && desc.get.toString(),
        };
    }""")
    print(report)
    # {'value': False,
    #  'ownProp': False,
    #  'getterSource': 'function get webdriver() { [native code] }'}
```

All three answers match a browser nobody automated: the value is `false`, the property
is not an own property of the instance, and the getter still reports `[native code]`.
There is nothing here for a descriptor walk or a `toString` probe to catch, because the
thing they look for was never added. The same reasoning, at more length and with the
history of the flag, is in
[why navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md).

## The honest caveat: necessary, not sufficient

Clearing this flag correctly is table stakes, and table stakes are not a win. A `false`
`navigator.webdriver` is one boolean among dozens of signals, and it says nothing about
the surfaces that actually get datacenter automation caught:

- **The IP.** A perfectly clean browser on a known or datacenter address still loses,
  and no browser property changes that. You supply the exit.
- **Rate and volume.** Per-account quotas, request velocity and how many sessions share
  one address are counted server-side, where the driver flag is invisible.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, keystrokes at a metronome interval. This engine draws mouse motion on a
  Bezier curve, but the pacing of your actions is yours to make human.
- **The rest of the fingerprint.** GPU, fonts, audio device, screen, the TLS handshake.
  These read as a genuine Firefox here, which is the whole design, but they are a system
  you keep consistent, not a switch you flip once.

invisible_playwright is built to look like a real browser driven by a real person, which
is why it passes most detection checks at the fingerprint, TLS and driver layers. It does
not, and cannot, fix a bad IP, a blown quota or robotic timing. The
[method for actually testing which layer is failing](how-to-test-bot-detection.md)
is worth more than any single flag reading, because it tells you whether the thing you
fixed was ever the thing catching you.

## Conclusion

Stock Playwright sets `navigator.webdriver` to `true` by specification. Patching it from
JavaScript trades one obvious tell for two subtler ones: a getter whose source reads
`false` under `toString`, and a property that has migrated onto the wrong object. A
native `false`, returned by the browser's own C++ path, leaves neither, which is why this
engine returns `false` without a shim to catch. Treat that as necessary and never as
sufficient: it clears the driver layer, and the IP, the pacing and the behaviour are
still yours to get right.

## Short answers to the questions that lead here

**Does Playwright set navigator.webdriver to true?** Yes, on every WebDriver-based
launch, because the standard requires an automated browser to report `true`.

**Can I just set navigator.webdriver to false in a script?** You can, and the value will
read `false`, but the override's getter no longer reports `[native code]` and the
property lands on the wrong object, so you have added two tells to remove one.

**How do detectors catch the JavaScript override?** By reading the getter's source with
`Function.prototype.toString`, and by walking the prototype chain to see that the
property moved onto the instance.

**What should a clean Firefox report?** A plain `false`. That is different from Chromium,
where an untouched browser reports `undefined` and a hand-set `false` is itself odd.

**Does invisible_playwright fake the value?** No. It returns `false` from the browser's
own native code path, so there is no JavaScript getter to inspect and no own property to
find.

**If navigator.webdriver is false, am I undetectable?** No. It is one boolean among many
signals. It does nothing for your IP reputation, your rate limits or your behaviour, all
of which you still have to supply.

## Sources

- The WebDriver specification, which defines `navigator.webdriver` and requires an
  automated user agent to expose it as `true`.
- `Function.prototype.toString`, whose `[native code]` behaviour for builtins is the
  standard mechanism behind the getter-source check.
- This project's own driver-layer checks, comparing a native `false` against a
  JavaScript override on the same engine.

**See also:** [Function.prototype.toString and the native-code check](tostring-native-code-detection.md),
[why navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
and [the checklist for being detected on one site](playwright-detected-as-bot.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The flag reads false
because the browser's own code returns false, not because a script overwrote it.*
