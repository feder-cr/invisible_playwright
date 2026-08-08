---
title: "WebDriver BiDi vs CDP: does the new protocol hide you"
description: "WebDriver BiDi standardizes the automation control wire across browsers, but the leaks that expose automation live in the page, not on the protocol you speak."
parent: "Comparisons"
nav_order: 19
---


# WebDriver BiDi vs CDP: does the new protocol hide you

There is a hope, repeated often enough that it is worth answering directly, that
WebDriver BiDi is the fix for automation detection. The reasoning goes: the old
Chrome DevTools Protocol had a famous leak, everyone is moving to a standardized
bidirectional protocol, so the standardized one must close it.

It does not, and the reason is the useful part. The leak people are trying to
escape was never on the protocol wire. It is in the page. Changing which wire the
driver speaks over leaves the page-level artifacts exactly where they were.

## The protocol is the control channel, not the fingerprint

The protocol is the wire a driver speaks over, not the fingerprint a page reads,
so changing it moves nothing a detector looks at. CDP, BiDi and Firefox's own
Juggler are all
control channels: the out-of-process link a driver uses to say "open a page", "go
to this URL", "evaluate this script", "give me the console messages". They are
plumbing between your Python process and the browser process.

A detection script running inside the page cannot see that channel at all. It
cannot see whether the command that opened the tab arrived as CDP JSON, as a BiDi
message, or as a Juggler call. What it can see is whatever the browser does to the
page as a side effect of being driven: a property that reads differently, a global
that should not exist, a built-in prototype that was touched before the first
script ran, a loop that runs measurably slower because the engine is in a
different mode.

So the question "does BiDi hide me" is really two questions that get conflated. Is
the control channel itself observable? Mostly no, for any of the three. Does
switching the control channel change the in-page artifacts? Also no, because those
artifacts are produced by a different mechanism entirely.

## What CDP's Runtime.enable leak actually was

The CDP `Runtime.enable` leak was a page-visible side effect of how Chromium
automation manages JavaScript execution contexts, not a property of the CDP wire
itself. It is narrower than the folklore around it, so it is worth stating
exactly.

On Chromium, automation libraries call the CDP command `Runtime.enable` to manage
the execution contexts they need for evaluating JavaScript. That call has an
in-page side effect: it causes the browser to emit context-creation events, and a
page can provoke and observe the timing of that machinery. Ordinary browsing never
triggers it, so its footprint reads as automation. Two separate projects,
[Patchright](vs-patchright.md) and
[rebrowser-patches](vs-rebrowser-patches.md), independently converged on the same
fix: stop calling `Runtime.enable` automatically and create the needed contexts by
hand, with identifiers the page cannot correlate back to the session.

Notice what that fix is and is not. It is not "switch protocols". It is a change to
how the driver uses the protocol so that the observable side effect stops
happening in the page. The leak lived in the page; the fix lives at the driver.
The protocol name never entered into it.

## Firefox automation here rides Juggler out of band

This project does not patch that leak because it never had it. The Firefox we ship
is driven by stock Playwright over Juggler, an out-of-band control protocol that is
not CDP and has no `Runtime.enable` command to call. There is no automatic
context-enable step whose in-page timing a script can measure, because that step is
a CDP-shaped thing and this is not CDP.

That is an architecture difference, not a cleverness difference. The
`Runtime.enable` timing signal is specific to how CDP-driven Chromium manages
execution contexts. A driver that manages contexts differently, over a different
channel, does not emit that particular event and so cannot be caught by a probe
looking for it. The class of Chromium hand-patches that exist to suppress it has no
counterpart here for the same reason a Linux binary has no Windows registry to
clean.

```python
from invisible_playwright import InvisiblePlaywright

# A stock Playwright script. The control channel underneath is Juggler,
# not CDP, so there is no Runtime.enable side effect for a page to time.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # standard Playwright: the returned object is a real Browser
    print(page.title())
```

None of that code mentions the protocol, which is the point: the wire is an
implementation detail of the driver, and the same script would work if it changed.

## What WebDriver BiDi standardizes, and what it leaves untouched

WebDriver BiDi is a genuinely good thing and worth being precise about. It is a
[W3C effort](https://www.w3.org/TR/webdriver-bidi/) to give every browser one
bidirectional automation protocol, so that a driver can subscribe to events and
issue commands the same way across engines, instead of CDP on one family and
something bespoke on another. Firefox, Chromium and others implement it.
Playwright itself has been building BiDi support. If you have ever wanted "one
protocol, every browser", this is that.

What it standardizes is the shape of the control channel: the commands, the
events, the subscription model. What it does not do, because it is not what a
control protocol is for, is change what the browser exposes to the page. BiDi has
no clause that turns off [`navigator.webdriver`](does-playwright-set-navigator-webdriver.md), no clause about whether a helper
runs in the page's realm before the page's own scripts, no clause about whether the
JavaScript engine is in a mode that runs loops slower. Those are engine and driver
behaviors. A standardized wire carries them just as faithfully as a proprietary one.

The mental model that trips people up is that CDP and BiDi are alternatives on the
same axis as "detected" and "not detected". They are not. Protocol choice and
detectability are orthogonal. You can be trivially detectable over BiDi and hard to
detect over Juggler, or the reverse, and the protocol name predicts neither.

## The leaks that survive any protocol change

If the wire is not where the tells are, where are they? Two families, both in the
page, both indifferent to which control channel drove the browser.

The first is execution-context timing. Attaching a driver to the JavaScript engine
can put realms into a debug-adjacent mode where the optimizing JIT is off and slow
paths run on every call, and that is measurable from the page as time, with no
property to read. It is
[why a fingerprinting service can report developer tools with none open](debugger-timing-detection.md).
Whether the driver spoke CDP, BiDi or Juggler to attach makes no difference to the
resulting distribution; what matters is whether the engine was left in that mode.
This project's fix was to attach without entering debug mode at all, so the JIT
stays on. That is an engine change, and it would be exactly as necessary under BiDi.

The second is injected globals and touched built-ins. If a driver runs a helper in
the page's own realm before the page loads, and that helper touches `JSON`,
`Date.prototype` or `Array.prototype`, then the page starts life with built-ins
that something else handled first, and that is observable. In a controlled
experiment that was the difference between a session being blocked and not. The fix
is to never touch the page realm until the page's own first script has run. Again:
a driver behavior, carried identically over any wire. A page reading a leftover
global does not know or care how the global got injected.

There is also a separate, wire-adjacent failure mode worth naming so it is not
confused with the above: the driver and browser can drift out of agreement about
the protocol's own field set, which
[breaks the client rather than exposing it to the page](playwright-protocol-drift.md).
That is a maintenance surface every automation stack has, standardized protocol or
not, and it is orthogonal to detection too.

## How to choose, and what to actually measure

The practical upshot is that "which protocol" is not a stealth decision. Pick the
protocol for engineering reasons: BiDi for cross-browser uniformity and a stable
standard, CDP where you need a Chromium-specific capability it exposes, Juggler if
you are on this project's Firefox because that is what it speaks. The three differ
on engineering properties, but not on the axis people ask about here:

| Protocol | Cross-browser standard | `Runtime.enable`-style context leak | Control wire visible to the page | Protocol choice changes detectability |
|---|---|---|---|---|
| CDP | No (Chromium family) | Yes, until hand-patched at the driver | No | No |
| WebDriver BiDi | Yes (W3C, multi-engine) | No such standardized clause | No | No |
| Juggler | No (Firefox, this project) | No `Runtime.enable` command exists | No | No |

Then measure the
things that actually decide detection, none of which the protocol choice sets.

The measurement that separates the real question from the imagined one is timing
and page state against a stock browser on the same machine, not a protocol audit:

```python
from invisible_playwright import InvisiblePlaywright

PROBE = """
() => {
  // 1. any global that a normal browser would not have?
  const suspects = Object.getOwnPropertyNames(window)
    .filter(n => /puppet|driver|automation|cdc_|selenium/i.test(n));
  // 2. rough JIT-sanity number; compare the RATIO to a stock browser
  const t = performance.now();
  for (let i = 0; i < 5e6; i++) Math.sqrt(i);
  const loopMs = performance.now() - t;
  // 3. did anything claim a user gesture nobody made?
  const ua = navigator.userActivation || {};
  return { suspects, loopMs, isActive: ua.isActive, webdriver: navigator.webdriver };
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate(PROBE))
```

Run the same probe in a stock browser and diff the results. The absolute loop
number means nothing on its own; the ratio between the two browsers is the JIT
signal. A non-empty `suspects` list is an injected-global leak. `webdriver` should
read as it does on a clean browser. Every one of those is a page-level property,
and every one of them would report the same value whether the tab was opened over
CDP, BiDi or Juggler.

## Conclusion

WebDriver BiDi is worth adopting for what it is: one standard control protocol
across browsers, which is a real convenience and a real cleanup. It is not a
stealth upgrade, because it operates on the wrong axis. The `Runtime.enable` leak
that made CDP notorious was a page-visible side effect of how one protocol managed
contexts, fixed at the driver, not by changing wires. The leaks that remain,
execution-context timing and injected globals, live in the page and the engine, and
a standardized wire carries them as faithfully as a proprietary one. This project's
Firefox never had the CDP leak because it does not speak CDP, and it closes the
in-page leaks in the engine and driver, which is the layer where they actually are.
Choose the protocol for engineering reasons and measure detection where it lives.

## Short answers to the questions that lead here

**Does switching from CDP to WebDriver BiDi make me undetectable?** No. The
protocol is the control channel; the tells that flag automation are in the page and
the engine, and they read the same over any wire.

**Was the CDP Runtime.enable leak a protocol problem?** It was a page-visible side
effect of how CDP-driven Chromium managed execution contexts. The fix changes how
the driver uses the protocol, not which protocol it is.

**Does this project have the Runtime.enable leak?** No. Its Firefox is driven over
Juggler out of band, which has no `Runtime.enable` command, so the signal that
several Chromium projects hand-patch never exists here to begin with.

**Is WebDriver BiDi bad, then?** Not at all. It is a solid cross-browser standard
worth adopting for uniformity and stability. It just is not a detection fix,
because detectability and protocol choice are orthogonal.

**What actually gives automation away if not the protocol?** Execution-context
timing when the engine is left in a debug-adjacent mode, globals or touched
built-in prototypes injected into the page realm, and behavior. All page-level or
engine-level, none of them on the wire.

**How do I test my own setup?** Run a timing-and-globals probe in your automated
browser and in a stock browser on the same machine, and diff them. The ratio, not
the protocol name, is the signal.

## Sources

- This project's own debugger and execution-context fixes, described in
  [why an attached debugger makes automation detectable](debugger-timing-detection.md),
  for the in-page timing and injected-global leaks that survive a protocol change.
- The [Patchright](vs-patchright.md) and [rebrowser-patches](vs-rebrowser-patches.md)
  comparisons on this site, read from each project's own repository, for what the
  CDP `Runtime.enable` fix is and is not.
- The [WebDriver BiDi specification](https://www.w3.org/TR/webdriver-bidi/)'s own
  scope, for what a standardized control protocol standardizes and what it leaves
  to the engine.

**See also:** [why an attached debugger makes automation detectable](debugger-timing-detection.md)
for the page-level leaks in detail, [invisible_playwright vs rebrowser-patches](vs-rebrowser-patches.md)
for the CDP fix arrived at twice, and [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the compare-against-stock method this page leans on.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The protocol under
the driver is an implementation detail; the leaks were never on the wire.*
