---
title: "navigator.webdriver is not the tell you think it is"
description: "navigator.webdriver is a specified property, not a leak. Patching it alone buys almost nothing: what it tells a detector, and where the real fix has to live."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 1
---

# navigator.webdriver is not the tell you think it is

[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver) is a real, specified browser property: a conforming
browser must report it as `true` while under automation control and otherwise
leave it `undefined`, never `false`. It is the cheapest signal in any
fingerprint, which is why it is the one everybody checks first, and passing it
alone - by hiding, patching, or deleting it - stops a detector on essentially
nothing else it looks at.

If you have ever automated a browser and been shown a different page than a human
gets, the first thing you found was almost certainly `navigator.webdriver`. It is
the famous one. It is in every tutorial. And patching it, on its own, buys you
close to nothing.

Here is why, and what the property actually tells a detector.

## navigator.webdriver is a standard, not a leak

`navigator.webdriver` is specified. It is not something that slipped out by
accident: the [WebDriver spec](https://www.w3.org/TR/webdriver2/) requires a conforming browser to expose it as `true`
when the session is under automation control. It exists so that a page can know,
which means a page checking it is using it exactly as intended.

Start from that. You are not defeating a bug. You are
contradicting a value the browser is required to publish about itself.

## Why setting navigator.webdriver to false is worse than leaving it true

Setting `navigator.webdriver` to `false` is worse than leaving it `true`, because a
clean browser never reports `false` in the first place - it swaps one honest
signal for a value no real browser sends, and adds two more tells in the
process. The usual first attempt looks like this:

```js
Object.defineProperty(navigator, 'webdriver', { get: () => false });
```

Three things just happened, and only one of them was the thing you wanted.

**One.** In a normal browser that is not automated, the property is not `false`. It
is `undefined` in most builds, because the attribute is only present when the flag
is set. A detector that has ever looked at a real browser knows the difference
between "the answer is no" and "there is no answer". Reporting `false` is a
distinct value from what a clean browser reports, so you have swapped one signal
for another.

**Two.** You left fingerprints on the object itself. The property is now an own
property of the `navigator` instance instead of living on `Navigator.prototype`
where it belongs. That is one line to check:

```js
Object.getOwnPropertyNames(navigator).includes('webdriver')       // should be false
Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver') // should exist
```

**Three.** Your getter is a JavaScript function you wrote, and functions carry
their source. [`Function.prototype.toString`](tostring-native-code-detection.md)
on a native getter returns `function get webdriver() { [native code] }`. On
yours it returns whatever you typed. Every serious stealth layer patches
`toString` to hide this, and then the patch to `toString` is itself detectable,
and so on down. This is the part people underestimate: each layer of the
disguise is a new surface.

## The timing problem, which is why "it works locally" happens

A page-level patch only works if it runs before the page's own scripts read
`navigator.webdriver` - miss that window and the page has already read the
original value, which has nothing to do with what value you set and everything
to do with when your code runs. Automation frameworks expose an "on new document"
or "init script" hook for exactly this, and it is not the same thing as running
code after `goto` returns.

Two consequences people meet in this order:

**It works when you test it by hand and fails in the crawler.** By the time you open
a console and check `navigator.webdriver`, your patch has long since run. The page's
own script ran earlier, read the real value, and may have already sent it.

**A page that keeps a reference wins permanently.** If the first line of a page's
script does `const w = navigator.webdriver` or captures the descriptor, your later
redefinition changes nothing for that reference. You cannot un-read a value that was
already read.

This is also why headed and headless runs can differ for the same code: they do not
generally change injection order, but they do change timing, and a race you win on a
warm local machine you can lose on a loaded CI box.

A value decided inside the engine has no race, because there is no moment before
which it was something else.

## What a real audit checks instead

A real audit checks a whole fingerprint, not one boolean: descriptor hygiene,
cross-surface consistency, rendering output, and behaviour. Open any of the public
fingerprinting test suites and look at what they collect -
[CreepJS](creepjs-explained.md), [BotD](botd-explained.md), [sannysoft](sannysoft-explained.md), fpscanner - and none of them
stop at `navigator.webdriver`. They build a picture and then check whether the
picture is internally consistent.

A rough map of what that picture contains:

- **Descriptor hygiene.** Which properties are own vs inherited, whether getters
  are native, whether anything has been redefined. Cheap to check, expensive to
  fake completely.
- **Consistency across surfaces.** The user agent string says one platform. So do
  [`navigator.platform`](navigator-platform-oscpu-consistency.md), the
  [Client Hints](client-hints-sec-fetch.md), the fonts that are actually installed, the
  [WebGL renderer string](webgl-renderer-strings.md), the timezone, the
  language list, and [the way the audio stack rounds floating point](audiocontext-fingerprinting.md).
  Any one of those can be spoofed. Making all of them agree, on a machine that
  is not actually that machine, is the real work.
- **Rendering.** Canvas and WebGL output, and whether the reported GPU is a GPU a
  human would plausibly have. A software rasterizer string is a strong signal that
  the browser is running on a server with no graphics hardware, and it is a signal
  no amount of property patching hides.
- **Behaviour.** Whether the pointer travels or teleports, whether keystrokes have
  human interval distributions, whether the page was scrolled at all.

Notice that `navigator.webdriver` is one item in the first bucket. It is the
cheapest check a detector can run, which is why it is the one everybody knows, and
also why passing it proves nothing.

## The three places you can fix this

There are exactly three levels at which a stealth tool can operate, and they have
different failure modes.

**In the page, with JavaScript.** Inject a script before the page's own code and
redefine things. This is what the classic stealth plugins do. It is easy to adopt
and it works against naive checks. Its ceiling is the one described above: every
override is an object that can be inspected, and you are in a patching race
against the detector inside the same runtime you are trying to lie to.

**In the automation driver.** Patch the driver so that it stops announcing itself:
remove the flags that set `webdriver` in the first place, stop injecting the
bindings that automation frameworks leave in the page's global object. Patchright
is the well-known example on the Chromium side. This removes a whole class of
tells at the source rather than papering over them, and the property genuinely
becomes `undefined` and not `false`, because nothing set it. What it does not
change is anything about the machine underneath.

**In the browser engine, before it ships.** Change the values in the C++ source
and rebuild. [Camoufox](vs-camoufox.md) does this for Firefox, and so does the project I maintain,
`invisible_playwright`. At this level there is no override to detect, because
there is no override: the engine simply reports the value you compiled in, through
the same native code path a normal build uses. `Function.prototype.toString` says
`[native code]` because it is native code.

The tradeoff is honest and worth stating: you now have a browser build to maintain
against upstream, the binary is large, and you are locked to whichever engine you
patched. That is a real cost and anyone telling you otherwise is selling
something.

## The part that actually decides it

Whichever level you pick, the thing that gets you caught is rarely a single
property. It is disagreement.

A user agent claiming Windows, on a machine whose fonts are the Linux default set.
A timezone from one continent and an IP from another. A GPU string from a laptop
and a screen resolution that no laptop ships with. A canvas that renders
identically to ten thousand other sessions because everybody is using the same
spoofing library with the same default seed.

This is why the useful mental model is not "hide the bot flags" but "be a specific
machine, consistently, for the whole session". In practice that means the
fingerprint should be generated once from a seed and then every surface should
derive from that same seed, so the parts cannot contradict each other. It also
means a re-run with the same seed gives you the same machine, which is the only
way to A/B test anything in this space without the noise swamping the signal.

## If you take one thing away

`navigator.webdriver` is a smoke alarm, not the fire. If your automation is being
detected and you have already patched it, the answer is almost never a better
patch for that property. It is somewhere in the consistency of everything else,
and the fastest way to find it is to run one of the open test suites against your
setup and read the whole report rather than the headline verdict.

## Short answers to the questions that lead here

**How do I remove `navigator.webdriver`?** You cannot remove it from the page. You can
delete the override so the property is genuinely `undefined`, which is what a real
browser reports, but that has to happen where the browser decides, not in a script the
page can read.

**Does `Object.defineProperty(navigator, 'webdriver', {get: () => false})` work?** It
changes the value and creates two new tells: the property moves from the prototype to
the instance, and `false` is not what a real browser says. Real browsers say
`undefined`.

**What should `navigator.webdriver` be?** `undefined` in a normal browser.
`true` under automation. `false` almost nowhere, which is why setting it to `false` is
worse than leaving it alone.

**Is `--disable-blink-features=AutomationControlled` enough?** It hides this one flag
in Chromium. It says nothing about the twenty other things a detector reads, and it is
itself a recognisable launch configuration.

**Why do I get detected even with `navigator.webdriver` undefined?** Because it is the
cheapest check, not the important one. Everything else on your machine is still
answering honestly.

## Sources

- [MDN, `Navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
  retrieved 2026-08-28, for the specified behavior discussed throughout this page.
- The [WebDriver specification](https://www.w3.org/TR/webdriver2/#interface), retrieved
  2026-08-28, whose interface section defines the webdriver-active flag and requires a
  conforming user agent to expose `navigator.webdriver` as `true` under automation control.
- [MDN, `Function.prototype.toString()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Function/toString),
  retrieved 2026-08-28, for the native-code string a builtin getter returns.
- [CreepJS](https://github.com/abrahamjuliot/creepjs), retrieved 2026-08-28, one of the
  public fingerprinting suites named on this page.
- [BotD](https://github.com/fingerprintjs/BotD), retrieved 2026-08-28, another of the
  named public suites.
- [bot.sannysoft.com](https://bot.sannysoft.com/), retrieved 2026-08-28, the older test
  page named alongside the others.
- [fpscanner](https://github.com/antoinevastel/fpscanner), retrieved 2026-08-28, the
  fourth suite named on this page.
- [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright), retrieved 2026-08-28,
  the named example of a driver-level fix on the Chromium side.
- [Camoufox](https://github.com/daijro/camoufox), retrieved 2026-08-28, the named example
  of an engine-level Firefox patch this page contrasts with `invisible_playwright`.

**See also:** [the three levels a stealth tool can work at](playwright-stealth-levels.md), [what CreepJS does to catch an override](creepjs-explained.md), [the ChromeDriver `cdc_` variable](cdc-variable-explained.md) for the same problem in a different place, and [whether stock Playwright sets navigator.webdriver to true](does-playwright-set-navigator-webdriver.md).

---

*I maintain `invisible_playwright`, an MIT-licensed Firefox patched at the C++
level and driven by stock Playwright. It is on PyPI. I am not neutral about the
third approach, and the tradeoffs above are the ones I actually live with.*
