---
title: "botasaurus vs invisible_playwright: framework vs library"
description: "botasaurus is a batteries-included Chrome framework; invisible_playwright is patched-Firefox plus fingerprint with stock Playwright. Which fits your stack."
parent: "Comparisons"
nav_order: 22
---


# botasaurus vs invisible_playwright: framework vs library

botasaurus is a batteries-included scraping framework built around Chrome;
invisible_playwright is a patched-Firefox engine plus a seed-reproducible
fingerprint that you drive with the automation library you already know. That is
the whole difference: a framework that decides your setup for you, versus an
engine you drive with your own.

The two get compared because both promise a browser that reads as real, but they
sit at different layers of the stack, and picking between them is mostly a
question about how much of your scraping setup you want to own. The rest of
this page is what that difference means in practice.

## What botasaurus is

botasaurus is an actively maintained open-source scraping framework in Python. Read
from its own repository, it bundles a great deal into one package: an anti-detect
driver built around Chrome, humanized actions, request-level scraping for when you
do not need a browser at all, response caching, parallelism across many tabs or
processes, and a decorator-based programming model that wraps your scraping function
and hands it a ready driver.

The selling point is that it is batteries-included. You write a function, annotate
it, and the framework supplies the browser, the retries, the cache and the
concurrency around it. If you want a setup handed to you rather than assembled,
that is the appeal, and it is a real one.

The trade that comes with it is the one every framework makes: you adopt its driver,
its decorators and its execution model. Your orchestration lives inside its shape.
And the realness it offers is whatever its Chrome-based anti-detect driver achieves,
patched at the JavaScript and driver layer rather than in the engine itself.

## What invisible_playwright is

invisible_playwright is not a framework. It is a Firefox patched at the C++ level
and a seed-reproducible fingerprint, which you drive with stock Playwright. There is
no scraping setup in the box: no caching, no decorators, no built-in parallelism
model. You bring those, or you bring the ones you already have.

What it gives you instead is the browser. The realness comes from the engine
matching a real Windows Firefox down at the level a detector actually reads: the TLS
handshake is a genuine Firefox handshake, `navigator.webdriver` reports what a clean
browser reports rather than a patched value, and the GPU, audio, fonts and screen
are internally consistent because they are derived together from one seed. That is
[why the automation-flag layer is mostly not your problem here](navigator-webdriver-explained.md):
it is answered by the engine, not painted on top of it.

Because the returned object is a real [Playwright `Browser`](https://playwright.dev/python/docs/api/class-browser),
every method works exactly as documented upstream. You compose it into whatever
stack you like.

## Framework versus engine: the durable difference

The honest, verifiable difference is architectural, not a scoreboard.

botasaurus wraps Chrome and gives you a setup. invisible_playwright replaces the
engine and gives you nothing but the engine. A framework is convenient precisely
because it decides things for you; a library is flexible precisely because it does
not. Neither is better in the abstract.

Where it bites is the ceiling on realness. A page-level or driver-level anti-detect
layer is bounded by what JavaScript and the driver can reach, and some of the most
decisive signals sit below that line. The TLS handshake is decided before any script
runs. A software WebGL renderer on a server announces the datacenter no matter what
the property returns. When the disguise is applied inside the browser rather than
being the browser, those layers are outside its reach. That is the same reason the
comparison against [a most-starred stealth wrapper](vs-scrapling.md) and against
[a mode that hides the WebDriver channel without touching the engine](vs-seleniumbase-uc-mode.md)
lands on the same point: the stealth ceiling is whichever engine sits underneath.

There is also a language-of-engine difference worth stating plainly. botasaurus is
Chrome. invisible_playwright is Firefox. If your target audience is overwhelmingly
one engine, the browser that matches it blends in better, and that is a per-job call
rather than a universal one.

## A runnable example

The two-line launch, then a normal Playwright navigation and read. Pass a seed and
the identity is reproducible run after run, which is what makes a failing run
debuggable instead of a guess.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
    print(page.inner_text("body"))
```

Add a proxy the same way plain Playwright does, and the browser timezone follows the
exit IP unless you pin it:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")
    for row in page.query_selector_all(".item"):
        print(row.inner_text())
```

That is the whole surface. There is no wrapped subset of the API to learn, so any
caching, retry or parallelism you want you add with the ordinary Python tools you
would use around Playwright anyway. If you need to force a specific field, for
instance a GPU model or a screen size, while leaving the rest seed-derived,
[pinning fingerprint fields](pinning.md) covers it.

## The caveat that applies to both

This is the part any honest comparison has to say out loud: neither tool fixes the
things that are not browser properties.

invisible_playwright is designed to look like a real browser driven by a real person,
and that is why it passes most detection checks that read the fingerprint, the TLS
layer and the driver. It does not, on its own, fix IP reputation, per-account quotas,
rate limits, or behaviour and timing. A perfect browser on a datacenter IP that a
thousand other people are using this minute is still on that IP. A perfect browser
that fills a form in eighty milliseconds still moves like a machine.

You supply those: a clean exit, human pacing, sane per-account volume. The same is
true of any Chrome-based framework, botasaurus included, and any tool that tells you
otherwise is overclaiming. There is no undetectable browser, and the realistic goal
is to remove the browser and driver from the list of things that give you away, so
that what remains is the network and the behaviour, which you control. The method for
proving you actually got there is in
[how to test whether your browser is detected](how-to-test-bot-detection.md):
compare against a stock browser field by field, and treat a blank or suppressed
signal as a failure rather than a pass.

## Which one to reach for

Reach for botasaurus when you want a scraping setup handed to you, you are happy on
Chrome, and its caching, decorators and concurrency model match how you want to work.
The value is that you write less scaffolding.

Reach for invisible_playwright when you already have orchestration, or want to own it,
and what you need is a browser that reads as a genuine Windows Firefox at the engine
level while you drive it with stock Playwright. The value is the realness floor and
the freedom to compose it into any stack. If you are already deciding between custom
browsers inside a larger crawler, the same reasoning appears in
[bringing a stealth browser to a crawler](crawl4ai-stealth-custom-browser.md).

They are not strictly either-or. Nothing stops you from keeping a framework's
convenience for the easy targets and reaching for a real engine for the hard ones.

## Conclusion

The comparison is framework versus library, and it resolves on ownership. botasaurus
decides your setup and drives Chrome; invisible_playwright decides nothing but the
browser and lets you drive a real Firefox with stock Playwright. The engine-level
realness is the reason it clears most fingerprint, TLS and driver checks, and the
honest boundary is that it clears none of the network or behaviour signals for you.
Pick the layer that matches how much of the stack you want to own, and supply the
clean exit and the human pacing either way.

## Short answers to the questions that lead here

**Is botasaurus better than invisible_playwright?** They are different layers. One is
a Chrome scraping framework, the other is a Firefox engine plus fingerprint you drive
yourself. Better depends on whether you want a setup or a browser.

**Can I use invisible_playwright like a framework?** No, and that is deliberate. It
gives you a real Playwright `Browser` and nothing else, so you add caching, retries
and parallelism with your own tools.

**Which passes more detection checks?** invisible_playwright fixes signals that live
below JavaScript, such as the TLS handshake, because it is the engine rather than a
patch inside it. But no browser fixes IP reputation or behaviour, so "more checks" is
not "all checks".

**Does either one make me undetectable?** No. Both leave IP reputation, rate limits,
per-account quotas and timing to you. Any claim of guaranteed evasion is false and a
legal and reputational risk.

**botasaurus is Chrome and invisible_playwright is Firefox, does that matter?** It can.
If your target audience is overwhelmingly one engine, matching it blends in better.
That is a per-job call.

**Do I have to pick just one?** No. Keep a framework's convenience for easy targets
and reach for a real engine for the hard ones.

## Sources

- The [botasaurus](https://github.com/omkarcloud/botasaurus) repository and README,
  retrieved 2026-08-28, for its framework scope: anti-detect Chrome driver,
  humanized actions, caching, and parallelism.
- [Playwright's `Browser` class documentation](https://playwright.dev/python/docs/api/class-browser),
  retrieved 2026-08-28, for what the returned object exposes once you switch from
  botasaurus to stock Playwright.
- This project's quickstart and configuration docs for the invisible_playwright API
  used in the examples above.
- This project's own release gates and testing notes for the realness boundary: the
  engine-level signals it fixes and the network and behaviour signals it does not.

**See also:** [invisible_playwright vs Scrapling](vs-scrapling.md),
[invisible_playwright vs SeleniumBase UC Mode](vs-seleniumbase-uc-mode.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It is a browser and a
fingerprint, not a scraping framework, and it still needs a clean exit and human
pacing to matter.*
