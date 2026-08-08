---
title: "playwright-extra stealth plugins vs a patched browser"
description: "playwright-extra applies puppeteer-extra stealth plugins as injected JavaScript a detector reads back; a patched Firefox moves those goals into the engine."
parent: "Comparisons"
nav_order: 31
---


# playwright-extra stealth plugins vs a patched browser

`playwright-extra` and a patched browser both aim to make automation look human, from
two different layers: `playwright-extra` injects JavaScript into the page to override
what a script reads back; a patched browser changes the compiled engine itself, so
there is nothing injected to catch. The rest of this page works through why that
layer choice matters and what each one still leaves for you to handle.

This site already has a page on [playwright-stealth, the page-level init-script approach](vs-playwright-stealth.md).
`playwright-extra` is a different thing that people reach for at the same moment, so it
deserves its own page. It is not a single script you inject. It is a Node plugin
ecosystem: a small wrapper around Playwright that lets you register evasion plugins,
the best known of which is the stealth plugin ported from `puppeteer-extra`.

The mechanism underneath is the same one the page-level approach uses, and so is the
structural limit - covered next, along with the separate cost of the language it is
written in.

## What playwright-extra actually is

`playwright-extra` is a wrapper you put in front of Playwright in Node. You call
`chromium.use(StealthPlugin())` and each registered plugin gets a hook that runs when a
page is created. The stealth plugin then does its work as a bundle of smaller evasions,
each one redefining a property or a method a detector is likely to read:
[`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
the plugins array, `navigator.languages`, the WebGL vendor strings, and so on.

Every one of those evasions is delivered the same way: JavaScript injected into the
page before the site's own code runs. That is the important sentence, because it is
what decides whether the disguise holds. The plugin does not change what the browser
*is*. It changes what a script *sees* when it asks, by installing new values in the
page ahead of the question.

The appeal is genuine. It is a couple of lines on top of an existing Node Playwright
script, no browser to build, no binary to host, and it fixes the obvious property
checks immediately.

## Why an injected override can be read back

The limit is not a bug in the plugin. It is a property of doing the work in JavaScript,
in the same page the detector runs in. When your override and the detection code share
one runtime, the override is visible to that code by construction.

Three ways it shows, all of which detectors in the public suites already do:

- **Source of a function.** A native method printed with
  [`Function.prototype.toString`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Function/toString)
  returns `[native code]`. A replacement getter is an
  ordinary function whose source is the patch you wrote. Patch `toString` itself to
  hide that and the patched `toString` becomes the new anomaly, checkable the same way.
  [The general version of this race has its own page](tostring-native-code-detection.md).
- **Descriptor shape and order.** Redefining a property tends to make it an **own**
  property where the native one is **inherited**, and can change the order properties
  enumerate in. A detector that walks the descriptors of `navigator` compares that
  order and that shape against a known-good browser, and the injected ones do not line
  up.
- **Timing.** An injected getter that computes a plausible answer does measurable work
  where a native accessor returns a stored value. Read the same property a few thousand
  times and the distribution is a signal.

None of these require knowing which plugin you used. They ask a structural question -
"is this value native, and does it sit where a native one sits" - and an in-page
override answers no.

## The language boundary

There is a practical difference between the two layers that has nothing to do with
detection: the language you drive them from. The whole `puppeteer-extra` and
`playwright-extra` ecosystem is **Node**. The plugins
are npm packages, the hooks are JavaScript, and the API you drive is the Node Playwright
API. If your automation is written in Python, adopting it means either rewriting the
driving code in Node or standing up a Node service beside your Python one.

That is a real cost even before the detection question, and it is worth naming plainly
rather than pretending the choice is only about stealth. The
[maintenance story of the underlying plugin is worth reading too](puppeteer-extra-stealth-unmaintained.md)
before you build on it.

## What a patched browser does instead

`invisible_playwright` moves the same goals down a layer. The fingerprint - GPU strings,
audio, fonts, screen, the driver surface - is set in the compiled Firefox build and its
preferences, before any page loads. There is no JavaScript injected into the page to
carry the disguise, so there is no injected layer for `toString`, descriptor order or
timing to catch. What a script reads is what a native accessor returns, because it is
the native accessor.

It is driven from **Python**, with [stock Playwright](https://playwright.dev/python/docs/api/class-page). Switching from a plain Playwright
script is two lines, and every method after that is the upstream Playwright API,
unchanged:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # `page` is a real Playwright Page: every documented method works
    ua = page.evaluate("() => navigator.userAgent")
    webdriver = page.evaluate("() => navigator.webdriver")
    print(ua, webdriver)
```

The `seed=42` makes the identity reproducible: the same seed yields the same GPU, the
same canvas hash, the same audio context every run, which is what lets you replay a
failing run instead of guessing at it. Drop the seed and each session gets a distinct
fingerprint. Either way the values are the browser's own, not a getter you can print.

This is why it passes most in-page detection: the fingerprint, the driver layer and the
TLS handshake read as a genuine Firefox because they *are* one, rather than a real
browser wearing values pasted on at page load.

## What this does not fix, and you have to supply

A patched build changes the fingerprint. It does not touch anything outside the
browser, and it is a mistake to expect it to. On its own it adds:

- **No proxy rotation, and no IP reputation.** A perfect browser on a datacenter IP, or
  on a residential IP a thousand other people are using this minute, still loses. You
  supply a clean exit; see [how proxy and timezone are configured](configuration.md).
- **No captcha handling.** It does not solve or answer challenges.
- **No per-account quotas or rate limits.** How often one account or one address may
  act is not a browser property.
- **No behaviour or pacing.** A form filled in eighty milliseconds and a pointer that
  teleports are their own signal, whatever the fingerprint says. You supply human
  pacing.

The honest framing is that this handles the fingerprint, TLS and driver layer well, and
leaves the network, the account and the behaviour to you. Anyone selling more than that
is overselling.

## Conclusion

`playwright-extra` and `invisible_playwright` are aiming at the same goal from different
layers and different languages. The plugin ecosystem patches the page in Node, which is
quick to adopt and structurally visible to the code it is hiding from. The patched
browser sets the fingerprint in the engine and is driven from Python, so there is no
injected layer to read back - at the cost of shipping a real binary. Neither one fixes
your IP, your quota or your behaviour, and the tool that claims it does is the one to
distrust.

## Short answers to the questions that lead here

**Is playwright-extra the same as playwright-stealth?** Closely related. Both trace to
`puppeteer-extra`'s stealth work and both inject JavaScript into the page.
`playwright-extra` is the broader Node plugin ecosystem; `playwright-stealth` is the
narrower Python port. Same mechanism, same limit.

**Can a detector tell I am using a stealth plugin?** It does not need to know the
plugin. It can ask whether a value is native, using `Function.prototype.toString`,
descriptor order, or timing, and an injected override answers wrong to all three.

**Does playwright-extra work with Python?** The ecosystem is Node. Using it from Python
means running a Node process beside your code. `invisible_playwright` is driven from
Python with stock Playwright directly.

**If the browser is patched, why do I still get blocked?** Because the fingerprint is
one layer of several. A patched build does nothing for a bad IP, a hit quota, or
behaviour no human produces. Work [the per-site checklist](playwright-detected-as-bot.md)
in order.

**Which layer should I pick?** If you want it looking like a real browser rather than a
real browser wearing patches, and you are in Python, the engine layer. If you need a
two-line addition to an existing Node script and accept the visibility, the plugin.

**Does a patched browser guarantee I get through?** No, and nothing does. It handles the
fingerprint, TLS and driver layer. The proxy, the account and the pacing are yours.

## Sources

- The `playwright-extra` and `puppeteer-extra` projects, read from their own
  repositories and plugin source rather than from summaries, for how plugins register
  and inject.
- The public detection suites (CreepJS, BotD, FingerprintJS, sannysoft, BrowserLeaks)
  for the native-vs-override checks described above, each read from its own source.
- This project's own release gates, for the fingerprint, TLS and driver behaviour of
  the patched build.

**See also:** [playwright-stealth vs the engine layer](vs-playwright-stealth.md) for the
Python port of the same idea, [three ways to make Playwright undetected](playwright-stealth-levels.md)
for where each layer sits, and [the native-code detection race](tostring-native-code-detection.md)
for the check that reads injected overrides back.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It handles the
fingerprint, TLS and driver layer; the proxy, the account and the pacing are still
yours to get right.*
