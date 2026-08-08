---
title: "navigator.vendor and productSub: the Firefox tells"
description: "navigator.vendor is empty and navigator.productSub is 20100101 on real Firefox. See why a Chromium spoof gets these engine-fixed constants wrong."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 20
---


# navigator.vendor and productSub: the Firefox tells

On a real Firefox, `navigator.vendor` is the empty string `""` and
`navigator.productSub` is the frozen literal `"20100101"`; on the Chromium family they are
`"Google Inc."` and `"20030107"`. Both are read-only brand constants baked into the engine,
so a page reads them to tell which engine it is really talking to - and a Chromium-based
tool wearing a Firefox user agent has to fake both by hand, in the wrong place on the
object, where a one-line check can see the disguise.

Most disguises fail on the values a tool sets by hand. This page is about two values
almost nobody sets by hand, because they look boring: `navigator.vendor` and
`navigator.productSub`. They are short, they are constant, and they are exactly the kind
of field a Chromium-based tool wearing a Firefox user agent forgets to reshape, or
reshapes in a way that leaves a mark.

They are worth understanding for a reason that goes past these two fields. They are the
clearest small example of a distinction this whole project is built on: the difference
between a value a browser reports and a capability it actually has. One you can lie about
in a line of JavaScript. The other the engine answers for you, natively, whether you ask
it to or not.

## The two brand constants, and what each engine actually reports

Both of these are [read-only properties fixed by the HTML standard](https://html.spec.whatwg.org/multipage/system-state.html#dom-navigator-vendor)
itself. Neither is derived from your machine, your OS or your locale. They are baked into
the engine, and they differ by engine in a way that has been stable for years:

| Property | Gecko (Firefox) | Blink (Chromium family) |
|---|---|---|
| `navigator.vendor` | `""` (empty string) | `"Google Inc."` |
| `navigator.productSub` | `"20100101"` | `"20030107"` |
| `navigator.product` | `"Gecko"` | `"Gecko"` (frozen for all) |

Read that first row again. On a real Firefox, `navigator.vendor` is the **empty string**.
Not absent, not `"Mozilla"`, not some plausible-looking company name - empty. And
`navigator.productSub` is the frozen literal `"20100101"`, the same four-then-four digits
that Firefox has reported for a decade regardless of the actual build.

On the Chromium side both fields carry Blink's constants: `"Google Inc."` and
`"20030107"`. A browser reports its engine's values here, natively, through the same code
path a normal build uses. There is no per-session computation and nothing to configure.

## Why a Chromium tool cannot fully reshape them

Now put a Chromium-based automation tool in front of a page, set the user agent to claim
Firefox, and look at what the page can still read.

`navigator.vendor` on that build is `"Google Inc."` until something overrides it. So the
tool overrides it, the same way every stealth layer overrides `navigator.webdriver`:

```js
Object.defineProperty(navigator, 'vendor', { get: () => '' });
Object.defineProperty(navigator, 'productSub', { get: () => '20100101' });
```

And the same three problems land that
[land when you patch webdriver this way](navigator-webdriver-explained.md):

- The property is now an **own property** of the `navigator` instance instead of living on
  `Navigator.prototype` where the real one lives. One line checks that.
- The getter is a JavaScript function you wrote, so `Function.prototype.toString` on it
  returns your source instead of `function get vendor() { [native code] }`. Hiding that
  needs another patch, which is itself detectable, and so on down.
- You have to get **both** fields right and keep them consistent with everything else that
  claims Firefox, in the same session, natively enough to survive descriptor inspection.
  That last requirement is the one that does not have a JavaScript answer.

A detector does not need to know the "correct" Firefox values to catch this. It only needs
to notice that a property claiming to be a native engine constant is answered by a
non-native getter sitting in the wrong place on the object. That is a
[descriptor-hygiene check](how-to-test-bot-detection.md), and it is cheap.

## The consistency trap: productSub agrees with the user agent for free

Here is the part that turns a boring constant into a live contradiction.

The Firefox user agent string contains the token `Gecko/20100101`. Look at a real one:

```
Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0
```

That `20100101` is the **same literal** as `navigator.productSub`. On a real Firefox they
agree because they come from the same engine, and there is no way for them to disagree -
you did not set either one. A Chromium tool spoofing a Firefox user agent has now written
`Gecko/20100101` into the user agent by hand, and it has to independently make
`navigator.productSub` return `20100101` by hand too, and make `navigator.vendor` return
empty, and keep the whole set in sync every time it touches any of them.

Two hand-maintained copies of the same fact are a contradiction waiting to happen, and
[spoofing the user agent is what creates the demand for all of them](playwright-user-agent.md).
The moment one copy drifts - a productSub left at Blink's `20030107` under a user agent
that says `Gecko/20100101` - the session is not merely unusual, it is provably lying, and
the check is one comparison rather than a model.

## Capability versus value, the distinction the whole project rests on

This is the same shape as the sharpest argument for choosing the engine in the first place.

A missing DRM capability
[cannot be faked from JavaScript](chromium-is-not-chrome.md) because a page can ask the
build to do the thing and read the real answer, not a string the build reports about
itself. `navigator.vendor` and `navigator.productSub` are the low-stakes version of the
same idea: on a real Firefox they are decided by the engine and delivered through native
code, so there is no override to detect because there is no override.

The clearest cousin is `navigator.webdriver`. On this build, in an ordinary session, it
comes back `false` - and it does so not because a patch wrote `false` into a property, but
because the remote-automation services that would flip it to `true` are simply not
attached. The value follows a capability that is absent. Nobody set it. That is why it
survives a descriptor audit that the JavaScript override above fails: the answer is native
because the condition behind it is real.

`vendor` and `productSub` are the same story with the machinery removed. A real Firefox
reports the empty string and `20100101` for free, natively, because it is Firefox. A
patched Firefox driven by stock Playwright is Firefox in exactly the way these checks can
test, so it inherits the correct answers without a line of spoofing code and without a
getter anyone can catch out of place.

## Reading them yourself with invisible_playwright

Do not take the table on faith - read the fields off the browser you actually run, and
compare them against a stock Firefox on the same machine. That comparison is the method
that [catches what a verdict misses](how-to-test-bot-detection.md).

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    identity = page.evaluate("""() => ({
        vendor:     navigator.vendor,
        productSub: navigator.productSub,
        product:    navigator.product,
        webdriver:  navigator.webdriver,
        ua:         navigator.userAgent,
    })""")

    for key, value in identity.items():
        print(f"{key:11} {value!r}")
```

On the patched Firefox this prints the native Gecko answers:

```
vendor      ''
productSub  '20100101'
product     'Gecko'
webdriver   False
ua          'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0'
```

`vendor` is empty, `productSub` matches the `Gecko/20100101` token in the user agent, and
nothing about the way those two fields answer differs from a stock Firefox. The seed only
fixes the parts that vary between machines - the GPU, the audio stack, the fonts, the
screen - so the run is reproducible; these brand constants are the same on every seed
because the engine, not the seed, decides them.

To confirm the descriptor is native rather than an override sitting on the instance, check
where it lives and what its getter reports:

```python
    hygiene = page.evaluate("""() => ({
        own_vendor:  Object.getOwnPropertyNames(navigator).includes('vendor'),
        proto_desc:  !!Object.getOwnPropertyDescriptor(Navigator.prototype, 'vendor'),
        getter_src:  Object.getOwnPropertyDescriptor(
                        Navigator.prototype, 'vendor').get.toString(),
    })""")
    print(hygiene)
    # own_vendor  -> False   (not an own property of the instance)
    # proto_desc  -> True    (it lives on Navigator.prototype)
    # getter_src  -> 'function get vendor() { [native code] }'
```

An override-based disguise fails the first two of those and gives itself away in the third.
A real Firefox passes them because there is nothing there to fail. Run it against a stock
Firefox as well and diff the two: the fields should be identical, which is the whole point.

## Conclusion

`navigator.vendor` and `navigator.productSub` are small, dull, and constant, which is
exactly why they are useful to a detector - they are the fields a disguise forgets, or
reshapes with a getter that a one-line check can see is not native. The empty vendor string
and the frozen `20100101` are not something a good stealth tool computes well. They are
something a real Firefox reports for free, natively, in agreement with the user agent it
already carries, because the engine and not a patch is what answers.

That is the same reason a compiled-in capability beats a written-in value everywhere else
in this subject. The cheapest way to report Firefox's brand constants correctly is to
actually be Firefox where the check can reach.

## Short answers to the questions that lead here

**What is navigator.vendor in Firefox?** The empty string. Not `"Mozilla"`, not a company
name - `""`. On the Chromium family it is `"Google Inc."`, and on WebKit it is a different
string again, so the field alone tells a page which engine it is talking to.

**What is navigator.productSub in Firefox?** The frozen literal `"20100101"`, the same four
digits that appear as the `Gecko/20100101` token in the user agent. On Chromium it is
`"20030107"`.

**Can I just override them in JavaScript to look like Firefox?** You can set the values,
but the override moves the property off `Navigator.prototype` onto the instance and gives
it a non-native getter, both of which a descriptor check reads in one line. You have
swapped a wrong value for a detectable disguise.

**Why does productSub matter if it never changes?** Because it has to agree with the
`Gecko/20100101` token in the user agent, and a spoof maintains those two copies by hand.
When they drift the session is not unusual, it is provably inconsistent.

**How is this like navigator.webdriver?** Both are decided by the engine, not by a written
value. `webdriver` is `false` in a normal session because the automation services are not
attached, not because a patch set it; `vendor` and `productSub` are native constants for
the same reason. There is no override to catch.

**How do I check my own browser?** Read all three fields plus the user agent off the
browser your automation launches, then read the same fields off a stock Firefox on the same
machine and diff them. Anything that differs is a candidate; anything that matches is not
your problem.

## Sources

- [The HTML standard's `NavigatorID` definitions](https://html.spec.whatwg.org/multipage/system-state.html#dom-navigator-vendor)
  for `navigator.vendor`, `navigator.productSub` and `navigator.product`, including the
  frozen per-engine values each is specified to report.
- MDN's reference pages for [`navigator.vendor`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/vendor)
  and [`navigator.productSub`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/productSub),
  which document the same empty-string and `20100101` values on Firefox.
- Direct reads taken from a patched Firefox driven by stock Playwright and from a stock
  Firefox on the same machine, compared field by field.
- This project's own notes on capability-versus-value tells, of which these two fields are
  the smallest concrete instance.

**See also:** [navigator.buildID, another engine-fixed Firefox tell](navigator-buildid-firefox-tell.md),
[why navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
[why you should not set the user agent](playwright-user-agent.md), and
[Chromium is not Chrome](chromium-is-not-chrome.md) for the capability-versus-value
argument in its sharpest form.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. These two fields cost me
nothing to get right, which is the entire argument.*
