---
title: "Can the Gamepad API fingerprint or detect a bot?"
description: "How the Gamepad API works as a bot signal, why Firefox returns an empty getGamepads() array until a real gesture, and why matching that shape beats a stub."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 31
---


# Can the Gamepad API fingerprint or detect a bot?

Short version: it helps a detector cross-check your user agent, and it cannot detect
you on its own. The [Gamepad API](https://developer.mozilla.org/en-US/docs/Web/API/Gamepad_API) is a rarely-audited input surface, which is exactly
why it is worth getting right. Most stealth work pours effort into canvas, WebGL and
`navigator.webdriver`, and leaves the small input APIs answering in whatever way a
patch happened to leave them. A detector that has run out of the obvious checks reaches
for the unobvious ones, and an input API that reports the wrong shape for the browser
it claims to be is a cheap, quiet tell.

This page is what [`navigator.getGamepads()`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/getGamepads) actually returns, why an empty array is the
correct answer rather than a suspicious one, how the shape functions as a user-agent
consistency check, how to read it through invisible_playwright, and the honest limit of
what any of this fixes.

## What the Gamepad API actually exposes

The surface is small. `navigator.getGamepads()` is a function that returns a snapshot of
the game controllers the browser currently knows about, plus [`gamepadconnected` and
`gamepaddisconnected`](https://www.w3.org/TR/gamepad/) events that fire when one appears or leaves. That is nearly all of
it from a fingerprinting point of view: a function that is either present or not, and an
array whose contents and length are the interesting part.

Three properties of that surface matter for detection:

- **It requires a secure context.** On an insecure origin the API is not exposed the way
  it is on `https://`, so a page served over plain HTTP sees a different shape than one
  served over TLS. A test page on `https://example.com` sees the full surface.
- **It stays empty until real input arrives.** A browser does not report a connected
  controller just because one is plugged in. Firefox surfaces a gamepad only after it
  reports input following a genuine user gesture, which is a deliberate privacy gate: a
  page cannot silently enumerate your controllers on load.
- **Its shape differs by engine.** Before any controller is active, Firefox returns an
  empty array. Other engines pre-fill a fixed-length array with null entries. That
  difference is small, stable, and trivial to read from JavaScript.

So the API contributes almost nothing to a per-visitor fingerprint on its own. What it
contributes is a consistency signal, and consistency is where automated browsers get
caught. It sits in the same family as the other small, OS-backed surfaces covered in
[what data a website collects about your browser](what-data-websites-collect-about-your-browser.md).

## Why an empty array is the correct answer

An empty array is the correct answer: it is what a real, unmodified Firefox returns
whenever no controller is reporting input. The instinct when hardening a browser is
instead to make every surface look busy and populated; for the Gamepad API that instinct
is wrong, and acting on it is more detectable than doing nothing.

A real person driving a real Firefox, with no controller in use, produces an empty
result from `getGamepads()`. That is the overwhelmingly common case: most desktop
sessions have no gamepad reporting input. An empty array is not a suppressed signal or a
blocked probe; it is the honest, ordinary answer that the reference browser gives. The
[how-to-test-bot-detection method](how-to-test-bot-detection.md) warns that a blank
section is usually a failure rather than a pass, and this is the exception that proves
the rule: here the blank is what the stock browser returns, and a comparison against a
stock browser on the same machine confirms it.

The failure modes are the opposite of emptiness:

- **A stub that reports a connected controller.** A page that reads an active gamepad in
  a headless session with no gesture history has caught a browser inventing input that
  never happened. Real input is gated behind a user gesture; a fabricated pad is a claim
  the browser cannot back up.
- **A stub that returns the wrong-engine shape.** A fixed-length, null-filled array under
  a Firefox user agent is a direct contradiction: the string says Firefox, the array
  says a different engine.
- **An over-eager patch that changes the function's signature or throws.** Anything that
  makes `getGamepads` look overridden, non-native, or inconsistent with a fresh copy of
  the built-in is exactly what tampering detectors like [CreepJS](creepjs-explained.md)
  look for.

The real invisible_playwright build does none of these. It behaves as stock Firefox
does on this surface: secure-context gated, empty until a genuine gesture, engine-correct
in shape. Nothing extra is spoofed here, because the correct value is the one the
unmodified browser already produces. This is the same principle as
[maxTouchPoints reading 0 on a spoofed desktop](navigator-maxtouchpoints-pointer.md): the
honest, boring value is the one that matches, and inventing a livelier one only creates a
contradiction to be found.

## The shape is a user-agent cross-check

The reason this obscure API earns a place in a detector's toolbox is that it turns the
user-agent string into a testable claim.

A user agent is free text. Anything can send any string. What a detector wants is a
behaviour that is expensive to fake and that should agree with that string. The Gamepad
API is one such behaviour: given a user agent that names Firefox, the empty-array shape,
the secure-context gating, and the gesture requirement should all match what a real
Firefox of that version does. When they do not, the mismatch is decisive in the same way
a [TLS handshake that disagrees with the user agent](tls-fingerprint-user-agent-mismatch.md)
is decisive, and for the same reason: the string is cheap, the behaviour is not.

This is why "just set the user agent" does not survive contact with a careful detector.
Every value you assert by hand has to agree with every value you did not, and the small
input APIs are among the values people forget they did not set. The
[checklist for being detected on one site](playwright-detected-as-bot.md) starts with
exactly this class of mistake, because it is the most common and the most avoidable.

The practical consequence: a browser that matches stock Firefox on the Gamepad API is
not passing because the API was cleverly spoofed. It is passing because the engine
underneath is a real Firefox that answers the question the same way a real Firefox always
would. There is no stub to catch out, because there is no stub.

## Reading getGamepads() through invisible_playwright

The launch is the two-line change from stock Playwright; everything after it is the
Playwright API you already know. Here is the whole operation this page is about, reading
the surface from a secure context and confirming its shape.

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the whole identity reproducible, so this read is repeatable
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")   # a secure context, so the API is exposed

    report = page.evaluate("""() => {
        const pads = navigator.getGamepads();
        return {
            present:   typeof navigator.getGamepads === 'function',
            native:    /\\[native code\\]/.test(navigator.getGamepads.toString()),
            length:    pads.length,
            connected: Array.from(pads).filter(Boolean).length,
            secure:    window.isSecureContext,
        };
    }""")

    print(report)
    # {'present': True, 'native': True, 'length': 0,
    #  'connected': 0, 'secure': True}
```

The `browser` object is a real `playwright.sync_api.Browser`, so `new_page`, `goto` and
`evaluate` are the stock methods documented upstream. Nothing here is a wrapped subset.

The measurement worth making is the comparison, not the single read. Open the same
snippet against a stock Firefox on the same machine and diff the two `report` objects
field by field. On the honest build they match: `present` true, `native` true, `length`
0, `connected` 0, `secure` true. A page-level spoofing layer bolted on top would move at
least one of those (`native` flips to false when the function is a JavaScript override,
or `connected` reports a phantom pad), and that single differing field is the entire
finding. Reading it twice in one session should also return the identical result, since
nothing here randomises per call.

If you want to see the surface with a controller actually active, note that it will stay
empty until a genuine gesture produces input; a scripted `dispatchEvent` does not satisfy
the gesture gate, which is the whole point of the gate.

## What matching stock Firefox does not fix

This is the honest boundary, and it is the same boundary every page in this set draws.

invisible_playwright is built to look like a real browser driven by a real person, and
that is why it passes most detection checks: the fingerprint, the TLS handshake and the
driver layer all read as a genuine Firefox, and the Gamepad API is one more surface that
reads correct because the engine underneath is real. That covers the browser. It does not
cover everything a modern detector weighs.

Specifically, a perfect Gamepad API shape does nothing for:

- **IP reputation.** A datacenter address or a known, recycled proxy IP is flagged before
  a single JavaScript surface is read. You supply a clean exit; the browser cannot.
- **Per-account quotas and rate limits.** These are counted server-side against your
  account and your address. No browser property changes a counter.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, requests at a metronome's interval. You supply human pacing.

The Gamepad API also has **no effect on any of these** in either direction. It is a
consistency signal about the browser, and it lives entirely inside the browser. Getting
it right removes one way to be caught; it does not touch the network or the behaviour that
a challenge-free clean fingerprint still has to get past. That larger picture is the
subject of [why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

## Conclusion

The Gamepad API cannot detect a bot by itself, and it is not a rich fingerprint. What it
is, is a quiet cross-check on your user-agent claim: a real Firefox returns an empty array
until a genuine gesture, gated behind a secure context, in an engine-specific shape. The
correct move is to match that exactly, which invisible_playwright does by being a real
Firefox rather than by spoofing the surface. An over-eager stub that invents a connected
controller, returns the wrong-engine shape, or leaves the function looking overridden is
more detectable than the honest empty array it was trying to improve on. Match the stock
browser, supply a clean exit and human pacing yourself, and this surface becomes one less
thing to worry about.

## Short answers to the questions that lead here

**Can the Gamepad API detect a bot?** Not on its own. It is a consistency check on your
user agent, and it flags a browser only when the reported shape disagrees with the engine
the user agent claims.

**Why does getGamepads() return an empty array?** Because that is what a real Firefox
returns until a controller reports input after a genuine user gesture. The empty array is
the correct, common answer, not a suppressed one.

**Is an empty gamepad list suspicious?** No. Most desktop sessions have no active
controller, so an empty result matches the overwhelming majority of real users. A
fabricated connected pad is what looks wrong.

**Does the Gamepad API need HTTPS?** Yes. It requires a secure context, so a page on
plain HTTP sees a different surface than one on `https://`. Test on a secure origin.

**Does invisible_playwright spoof the Gamepad API?** No extra spoof is needed. The build
behaves as stock Firefox does on this surface, because the correct value is the one the
unmodified engine already produces.

**Will fixing this stop me being blocked?** Only for this one signal. It does nothing for
IP reputation, account quotas, rate limits or behaviour, which you supply with a clean
proxy and human pacing.

## Sources

- W3C, [Gamepad](https://www.w3.org/TR/gamepad/), retrieved 2026-08-28, for the
  `gamepadconnected` / `gamepaddisconnected` events and the specification of the API
  surface.
- MDN, [Gamepad API](https://developer.mozilla.org/en-US/docs/Web/API/Gamepad_API),
  retrieved 2026-08-28, for the overview of the surface.
- MDN, [`Navigator.getGamepads()`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/getGamepads),
  retrieved 2026-08-28, for the method signature and its return value.
- MDN, [Using the Gamepad API](https://developer.mozilla.org/en-US/docs/Web/API/Gamepad_API/Using_the_Gamepad_API),
  retrieved 2026-08-28, for the Firefox-specific note that a gamepad already connected
  when the page loads stays hidden until the user presses a button or moves an axis on it.
- Mozilla Hacks, [Securing Gamepad API](https://hacks.mozilla.org/2020/07/securing-gamepad-api/),
  retrieved 2026-08-28, for the Firefox 81 secure-context requirement and the empty-array
  behavior on an insecure origin.
- This project's fingerprint parity checks, which compare each browser surface against a
  stock Firefox on the same machine field by field rather than reading a verdict.

**See also:** [what data a website collects about your browser](what-data-websites-collect-about-your-browser.md)
for the full JS-accessible surface this API sits inside,
[maxTouchPoints and pointer consistency](navigator-maxtouchpoints-pointer.md) for the same
match-the-boring-value principle on another input surface, and
[the checklist for being detected on one site](playwright-detected-as-bot.md) for the order
to work in when a single site flags you.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The surface that catches an
automated browser is usually the one nobody thought to check.*
