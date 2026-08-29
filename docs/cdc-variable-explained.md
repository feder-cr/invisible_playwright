---
title: "The ChromeDriver `cdc_` variable, and why renaming it fails"
description: "The cdc_ variable ChromeDriver leaves on the page is a one-line Selenium test. Renaming it in the binary raises the bar but does not remove the tell."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 2
---


# The ChromeDriver `cdc_` variable, and why renaming it fails

**The `cdc_` variable is a property that ChromeDriver injects into every page it
controls, and its presence is a one-line test for Selenium automation. Renaming the
string in the binary hides it from a check that greps for the exact prefix, but not
from one that looks for the pattern, because the properties are still on the page.**

If you have automated Chrome with Selenium and been detected, you have probably met
this variable. This is what it is, why renaming it in the binary does not remove it,
and what that generalises to.

The check looks like this:

```js
Object.getOwnPropertyNames(window).find(k => k.startsWith('cdc_'))
```

It is a one-line test, it is in every bot-detection script, and understanding why it
works explains a lot about how this whole category of tell comes to exist.

## What the variable is

ChromeDriver, the binary Selenium talks to, needs to run its own JavaScript inside
the page: that is how it finds elements, reads text, and executes your `execute_script`
calls. To do that it keeps some state on the page's own objects, under names derived
from a fixed string compiled into the binary. Historically they look like this:

```
cdc_adoQpoasnfa76pfcZLmcfl_Array
cdc_adoQpoasnfa76pfcZLmcfl_Promise
cdc_adoQpoasnfa76pfcZLmcfl_Symbol
```

The prefix is stable across installations because it is a literal in the executable,
which is exactly what makes it a reliable signal. A page that finds one of those
property names is not guessing: nothing else puts them there.

Note what this is not. It is not a flag someone forgot to remove, and it is not the
same class of thing as `navigator.webdriver`. It is working state that the tool needs
in order to function.

## Why the usual fix is a rename

Some guides describe a manual version of the fix: edit the compiled ChromeDriver binary
directly and replace the `cdc_` literal with a different random-looking string of the
same length, since it is an in-place patch of a compiled file and the replacement has to
fit exactly where the original sat.

That defeats the check at the top of this page, because the check greps for `cdc_`. It
does not defeat a slightly better one. The variables are still there, still a set of
related names appearing together under a common stem, still matching a shape no page
author would produce. A detector that looks for *the pattern* instead of the prefix,
several own properties on `window` sharing a random-looking stem and typed suffixes,
finds them again under the new name.

[`undetected-chromedriver`](vs-undetected-chromedriver.md) goes further than a rename.
Its patcher finds the exact statement that assigns the `cdc_` properties inside the
compiled driver binary and overwrites it in place with a fixed, harmless statement of the
same byte length, so the assignment never runs and no `cdc_`-prefixed property lands on
`window` at all. That closes the specific check this page opened with. It does not close
the general one: the same fixed replacement ships in every copy of the patched driver,
and the browser it drives is still a stock, unpatched Chromium underneath, reporting
whatever canvas, font and WebGL values the host machine actually has.

So: a rename raises the bar without removing the surface, and overwriting the assignment
outright removes one named check without touching the fact that a driver is attached at
all. That distinction is the whole point of this page, and it applies far beyond this one
variable.

## The general shape of the problem

Any automation tool that needs to run code in the page needs somewhere to keep state.
The options are:

1. **Put it on page objects under a known name.** Easy, and detectable by name.
2. **Put it on page objects under a randomised name.** Harder, and detectable by
   shape.
3. **Do not put it on page objects at all.** Keep the state on the driver side of the
   boundary, or in an execution context the page cannot enumerate.

Option three is the only one that removes the surface instead of obscuring it, and
it is a design decision made long before anyone runs a detection test. It is also why
["which stealth plugin should I use"](playwright-stealth-levels.md) is often the wrong
question: no plugin can move state out of the page after the fact.

## Where Firefox differs, and where it does not

Firefox's automation does not use ChromeDriver, so `cdc_` does not exist there. That
is a fact about the protocol, not a virtue: Firefox's automation surfaces are
simply different ones.

What Firefox does still do by default is set `navigator.webdriver` to `true` when a
session is under automation control, because the
[WebDriver specification](https://www.w3.org/TR/webdriver2/) requires it: the
property reflects a "webdriver-active" flag the spec sets whenever the user agent
is under remote control. So a stock Playwright Firefox is trivially detectable too,
just by a different two-line check, and anyone claiming Firefox is inherently
undetected is selling something.

The useful difference is architectural, not moral. Firefox's automation
protocol runs in privileged code with its own execution contexts, so the state a
driver needs does not have to live where the page can enumerate it. Whether a given
tool takes advantage of that is a separate question from whether the engine allows
it.

## What to actually check

If you are debugging a detection problem on a Chromium-based stack, check in this
order:

1. `Object.getOwnPropertyNames(window)` and the same for `document`, looking for
   groups of related unfamiliar names, not one specific prefix.
2. `navigator.webdriver`, remembering that `false` is not the same answer as
   `undefined`, and a clean browser gives the latter.
3. Whether anything you loaded has patched a built-in: print
   `Function.prototype.toString.call(navigator.__lookupGetter__('webdriver'))` and
   see whether it says `[native code]`.
4. The non-automation signals, which is usually where the real problem is by the time
   you have got this far: the GPU string, the font set, the timezone against the IP.

Number four catches more sessions than one to three combined, and it is the one
nobody checks first.

## Conclusion

The `cdc_` variable is not an oversight. It is working state a driver needs to run its
own code inside the page, which is why a naive fix does not make it disappear. A rename
defeats a check that matches a prefix, but not one that matches a shape, because the
properties are still sitting on `window` under a different label. Overwriting the
assignment outright, which is what a tool like undetected-chromedriver actually does,
closes that specific hole without changing anything else a driver or the stock browser
underneath still reveals. The lesson generalises past this one variable: any tool that
keeps state on page objects is one enumeration away from being found, and the only fix
that removes the surface instead of raising the bar is keeping that state off the page in
the first place.

## Short answers to the questions that lead here

**What is the `cdc_` variable?** A property ChromeDriver injects into `window`, whose
name starts with `cdc_` followed by a random-looking string. Its presence is a one-line
check for Selenium automation.

**Does patching it out in the binary work?** A rename defeats a check that greps for the
exact prefix, but not one looking for the *pattern*, several unexpected own properties on
`window` sharing a stem. Overwriting the assignment outright, which is what
undetected-chromedriver actually does, removes the property entirely, but the driver and
the stock browser underneath are otherwise unchanged.

**How do I find it?** Enumerate `window`'s own properties and look for entries no normal
page has.

**Does Playwright have an equivalent?** Not this one, because it does not use
ChromeDriver, and not a comparable global either - checked directly, enumerating
every own property on `window` in a live Playwright-driven Chromium session finds
nothing resembling `window.__playwright` or any similarly-named global. Some
online guides describe one anyway; it is worth checking a claim like that yourself
before repeating it, the same way this page checks `cdc_` by opening a real
session rather than trusting a description of one. That does not mean Playwright
sessions have no artefacts at all - [each automation stack has its own, and each
one is a function whose source can be printed](tostring-native-code-detection.md) -
only that this specific one does not appear to be real.

**What is the real fix?** Do not add the properties at all, which means the automation
layer has to be built differently rather than patched afterwards.

## Sources

- Chrome for Developers, [What is ChromeDriver?](https://developer.chrome.com/docs/chromedriver),
  retrieved 2026-08-28, for ChromeDriver's role as the binary Selenium talks to and the
  driver-side state it needs to run its own JavaScript in the page.
- Chromium source, [call_function.js](https://chromium.googlesource.com/chromium/src/+/main/chrome/test/chromedriver/js/call_function.js),
  retrieved 2026-08-29, for the `cdc_adoQpoasnfa76pfcZLmcfl_` prefix and the `_Array`,
  `_Promise` and `_Symbol` suffixes quoted earlier on this page.
- [undetected-chromedriver's GitHub repository](https://github.com/ultrafunkamsterdam/undetected-chromedriver),
  retrieved 2026-08-29, for its documented approach of downloading and patching the
  ChromeDriver binary.
- [undetected-chromedriver's `patcher.py`](https://github.com/ultrafunkamsterdam/undetected-chromedriver/blob/master/undetected_chromedriver/patcher.py),
  retrieved 2026-08-29, for the `patch_exe` function that locates the injected `cdc_`
  assignment inside the compiled binary and overwrites it with a fixed placeholder of
  the same byte length.
- Selenium, [WebDriver documentation](https://www.selenium.dev/documentation/webdriver/),
  retrieved 2026-08-28, for the driver-to-browser relationship ChromeDriver implements.
- The [WebDriver specification](https://www.w3.org/TR/webdriver2/), retrieved 2026-08-28,
  which requires a conforming browser to set `navigator.webdriver` to `true` under
  automation control.
- This project's own sessions, enumerating every own property on `window` in a live
  Playwright-driven Chromium session, for the claim that Playwright has no comparable
  `cdc_`-style global.

**See also:** [why setting `navigator.webdriver` to false is worse than leaving it alone](navigator-webdriver-explained.md), [the three levels a stealth tool can work at](playwright-stealth-levels.md), since where the state lives is a level-two decision, and [selenium-stealth's actual maintenance status](selenium-stealth-unmaintained.md), for the popular package that patches properties next to this one.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Firefox-based, so `cdc_` was never our problem,
but the general shape of it is everybody's.*
