---
title: "Why an attached debugger makes automation detectable"
description: "A fingerprinting service reported developer_tools true with no devtools open. The cause was the automation framework's attached debugger, four separate leaks."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 3
---


# Why an attached debugger makes automation detectable

An attached JavaScript debugger makes automation detectable because it measurably
changes how fast the page runs, and that timing difference shows up even when every
property on the page looks normal. **Attaching a debugger, which is how automation
frameworks evaluate code and track execution, puts the JavaScript engine into debug
mode - and debug mode is slower in ways a page can time.**

That is what caught us. A commercial fingerprinting service was reporting
`developer_tools: true` on our sessions. No devtools window was open. Nothing was
inspecting anything. The browser looked, by every property-level check we could run,
completely normal. It was right anyway.

This page is what that means, the four separate leaks it turned out to be, and the one
that mattered most, which had nothing to do with timing at all.

## The mechanism

Playwright drives Firefox through a component that needs to evaluate code in the page,
track promises and manage execution contexts. The natural way to do that is the
engine's own `Debugger` API. [The same frame-tracking machinery has its own separate
failure mode against cross-origin iframes](cross-origin-iframe-unreachable.md), for a
different reason than anything covered here.

Attaching one calls `setIsDebuggee()` on every realm it touches, and that flag is not
cosmetic. It puts the JavaScript engine into debug mode:

- **The optimising JIT is disabled**, so JavaScript runs several times slower.
- A slow path runs **on every native function call**.
- A slow path runs **on every frame exit**.
- A slow path runs **on every exception**.

None of that is visible as a property. There is no `navigator.isBeingDebugged`. But it
is trivially visible as *time*.

Run 50,000 iterations of something and measure. A normal Firefox and a debug-mode
Firefox produce different distributions, not just different totals, and a model trained
on real browsers separates them easily. That is what the service was doing, and
`developer_tools: true` was its conclusion.

**The general point, which is bigger than this bug:** [a browser fingerprint](what-is-a-browser-fingerprint.md)
is not only the set of values a browser reports. It includes how the browser behaves, and performance is a
behaviour. You can spoof every string on the page and still be running measurably
differently from the thing you claim to be - [the same value-versus-output gap](renderer-string-vs-render.md)
that cost us a WebGL renderer flag before it cost us this one.

## Fix one: attach without entering debug mode

The debugger does not actually need debug mode for what an automation driver uses it
for. Evaluating code in a realm and holding references to values work without it; what
needs it is stepping, breakpoints and the hooks that fire on every call.

So the engine gained a flag. When it is set, the realm is never marked as a debuggee:
the JIT stays on, the slow paths stay off, and evaluation still works because that path
never consulted the flag in the first place.

Result: `developer_tools: false`, on every session we measured afterwards.

## Fix two: the stack traces said too much

Two separate leaks, both readable from ordinary JavaScript by anyone who throws an
error and reads [`Error.stack`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Error/stack).

**Frames belonging to the automation layer.** The driver's own code lived at
`chrome://juggler/...`, and when the stack was built those frames were in it. A page
that captures a stack sees frames from a source no ordinary browsing produces. The fix
is to skip frames whose source belongs to the automation layer while the stack string is
built.

**The name of evaluated code.** When the driver evaluated something, the engine labelled
it `debugger eval code`, with an introduction type of `debugger eval`. Both strings are
visible in stacks. They now say `eval code` and `eval`, which is what ordinary
evaluation is called.

Neither of these needs a fingerprinting library. `try { null.f() } catch (e) { e.stack }`
is the whole technique.

## Fix three: attached but invisible

Even with debug mode skipped, the debugger was still being registered in the realm's
internal list of debuggers, which meant `hasDebuggers()` was true.

That state is not readable from JavaScript. It did, however, keep two engine paths alive
that are not gated on debug mode: a notification on every major garbage collection, and
allocation sampling. So a page realm remained distinguishable from one that had never
been debugged, on an axis nobody was looking at yet.

The fix registers the debugger everywhere it is needed for the driver to work and not in
the list that flips that flag. The realm is now indistinguishable from a never-debugged
one.

This is the sort of thing that is easy to dismiss as theoretical. We fixed it because
the previous three had all been theoretical right up until something measured them.

## Fix four, and this is the one that mattered

The others were about how the engine behaved. This one was about something the driver
did to the page, and it was the load-bearing fix.

Every execution context, when created, immediately ran a small helper in **the page's own
realm**, so that serialisation would be fast later. It ran before any of the page's
scripts.

That helper touched `JSON`, `Date.prototype` and `Array.prototype`. In the page's realm.
Before the page existed.

A page that has been automated therefore started with built-in prototypes that had been
touched by something else first, and that is observable. In a controlled experiment,
this was the difference between being blocked and not being blocked by a commercial
protection stack, with everything else held constant.

The fix is unglamorous: build the helper lazily, on first use, which is always after the
page has loaded and always in response to something the client asked for. The page realm
is now untouched until the page's own first script runs.

If you take one thing from this page, take that shape:

> The most expensive leak was not a value the browser reported. It was code that ran in
> the page's world before the page did.

## The related one: every evaluate was a user gesture

While looking at the same file we found that evaluating script was announcing itself
another way. Before running your code, the driver was telling the browser that a user
gesture was in progress, presumably so that gesture-gated APIs would work from
automation.

The side effect is that [`navigator.userActivation`](https://developer.mozilla.org/en-US/docs/Web/API/UserActivation)`.isActive` and `hasBeenActive` became
true as a result of `page.evaluate()`. A page that reads user activation therefore saw a
user gesture that no user made, at a moment no user could have made one. The same theme
runs through [synthetic clicks and the `isTrusted` flag](playwright-clicks-istrusted.md):
automation announcing an interaction that no human performed.

It is now off by default, and available behind a preference for the cases that genuinely
need gesture-gated APIs.

## What to check in your own setup

```js
// 1. does the automation layer appear in stacks?
try { null.f() } catch (e) { console.log(e.stack) }

// 2. is anything claiming a user gesture I did not make?
console.log(navigator.userActivation.isActive, navigator.userActivation.hasBeenActive);

// 3. rough timing sanity, compared against a normal browser on the same machine
const t = performance.now();
for (let i = 0; i < 5e6; i++) Math.sqrt(i);
console.log(performance.now() - t);
```

For the third one the absolute number means nothing. Run it in your automated browser
and in a normal browser on the same machine and compare the ratio. A large gap is the
JIT being off, and no property-level fix addresses it.

## Short answers to the questions that lead here

**Why does a fingerprinting service report devtools when nothing is open?** Because an
attached debugger changes engine behaviour, and that is measurable through timing even
with no window and no property to read.

**Can a page detect that the JIT is disabled?** Not directly, and it does not need to.
It measures how long things take and compares against what a normal browser does.

**Is `Error.stack` really used for detection?** Yes, and it is one of the cheapest
techniques available. It costs one thrown error, and it exposes both the source of every
frame and the label the engine gave to evaluated code.

**Does `page.evaluate()` leave traces?** It can. Ours announced a user gesture, and it
warmed a helper in the page realm before the page loaded. Both were fixed, and both
would have been invisible to any property-level audit.

**Does this affect Chromium-based automation?** The specific fixes are Firefox-side. The
category is not: any driver that attaches to the JavaScript engine or runs code in the
page's world before the page has the same class of problem.

**How would I have found this myself?** By comparing against a stock browser on the same
machine, on timing and on stack contents, rather than on values. Every one of these was
invisible in a property dump.

**See also:** [Function.prototype.toString and the native code check](tostring-native-code-detection.md),
for the property-level half of the same story, and
[three ways to make Playwright undetected](playwright-stealth-levels.md), where the
driver layer is the one this page is about.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. All four fixes are in this project's engine and
driver, and the fourth one was found by controlled experiment after the first three did
not fully close it.*
