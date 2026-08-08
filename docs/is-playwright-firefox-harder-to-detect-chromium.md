---
title: "Is Playwright Firefox Harder to Detect Than Chromium?"
description: "Firefox vs Chromium: the one structural difference in automation detection (CDP injection vs Juggler none) and where engine choice stops mattering."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 26
---


# Is Playwright Firefox Harder to Detect Than Chromium?

Short version: there is one structural reason Firefox starts from a slightly
cleaner position, it is smaller than most people hope, and it does nothing about
the signals that get sessions blocked in practice. This page is the real
difference, the part that is marketing, and where the two engines become equal
again.

## The one structural difference that is real

Chromium automation is controlled over the [Chrome DevTools Protocol
(CDP)](https://playwright.dev/docs/api/class-cdpsession). For
years, that control channel has left a document-level trace: property names
beginning with `cdc_` or `$cdc` attached to page objects. A detector does not
need to be clever to find those. It greps for a known string, and the string is
either present or it is not.

Playwright drives Firefox through a different mechanism, Juggler, which does not
inject any such page-visible variable. So that specific class of tell - a
concrete, document-level string a detector can match against - simply does not
exist on the Firefox side. It is not that Firefox hides it better. There is
nothing to hide.

That is a genuine starting advantage, and it is the honest core of the "Firefox
is harder to detect" claim. It is also where the honest version of the claim
ends. This is a deeper contrast if you want the mechanics:
[the cdc_ variable and why it exists](cdc-variable-explained.md), and
[how BiDi and CDP differ in what they expose](bidi-vs-cdp-detection.md).

## What a stock Playwright Firefox still tells the page

Removing one signal family does not make a browser look human. A plain,
unmodified Playwright Firefox still announces itself in ways that have nothing to
do with the control protocol:

- [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver)
  reports `true`. That is one property read, and it is set
  the same way regardless of engine. See
  [what navigator.webdriver actually proves](navigator-webdriver-explained.md).
- The fingerprint is raw. The GPU string, the canvas hash, the audio context,
  the installed fonts, the screen geometry - all of them are whatever the
  machine happens to be, which on a server is a machine that looks like a server.
- The TLS handshake is decided before any JavaScript runs, and no engine choice
  changes what a mismatched handshake says.

So the fair comparison is not "Firefox versus Chromium" in the abstract. It is
"which raw browser starts with fewer free tells", and the answer is Firefox by
exactly one family. The rest of the work is the same on both engines. There is a
fuller side-by-side in
[Firefox versus Chromium for anti-detect](firefox-vs-chromium-antidetect.md).

## Where invisible_playwright picks up from the Firefox baseline

invisible_playwright takes the Firefox starting position and closes the tells the
baseline leaves open. It is a Firefox patched at the C++ level, driven by stock
Playwright, so `navigator.webdriver` reads the way a real browser's does, and the
fingerprint surfaces (GPU, audio, fonts, screen, roughly 400 fields) are derived
from a seed instead of leaking the host. The point is not that it evades anything
by name. It is that the browser reads as a genuine Firefox because, at the layers
a page can inspect, it is one.

Switching from plain Playwright is a two-line change, and every Playwright method
you already use keeps working:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the whole identity reproducible run after run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # the page sees a genuine Firefox: no webdriver flag, a coherent fingerprint
    print(page.evaluate("navigator.webdriver"))   # -> None, not True
```

The returned `browser` is a real `playwright.sync_api.Browser`. There is no
wrapped subset of the API to learn. The async form is the same import from
`invisible_playwright.async_api` with `await` on the page calls.

If you want to confirm the difference for yourself rather than take it on trust,
open a public suite in both a stock Playwright Firefox and the patched one and
diff the reports field by field, the way
[testing whether your browser is detected](how-to-test-bot-detection.md)
describes. The webdriver flag and the raw-fingerprint fields are exactly where
the two diverge.

## The caveat that has to travel with every claim above

Engine choice removes one signal family. It does nothing for the signals that are
independent of the engine, and those are usually the ones that get a session
blocked:

- **IP reputation.** A coherent browser on a datacenter address, or on a
  residential address a thousand other people are using this minute, still loses.
  That is a property of the exit, not the browser. Bring a clean proxy.
- **Per-account quotas and rate limits.** These do not read the fingerprint at
  all. They count requests against an identity over time.
- **Behaviour and timing.** Pointer motion, typing rhythm, the pause between
  actions. invisible_playwright arcs the cursor on a Bezier curve, but pacing the
  work like a human is on you.

None of these care which engine you chose. Which is why the honest answer to the
title is "it helps with the browser layer, and the browser layer is not the whole
test". The
[checklist for when Playwright is detected on one site](playwright-detected-as-bot.md)
puts these in the order they actually bite, and the IP is seventh on it for a
reason.

## Conclusion

Is Playwright Firefox harder to detect than Chromium? By one concrete, structural
margin, yes: the CDP control channel has historically left a `cdc_` string in the
page and the Juggler channel Firefox uses does not, so a whole class of grep-able
tell is absent from the start. That is real, and it is worth having. It is also
one family of signals out of several. A stock Playwright Firefox still reports its
webdriver flag and a raw fingerprint, and neither engine does anything about the
IP, the quotas, or the way you move. invisible_playwright closes the browser-layer
gap so the session reads as a real Firefox driven by a real person; you supply the
clean exit and the human pacing. That division of labour is the whole answer.

## Short answers to the questions that lead here

**Is Firefox harder to detect than Chromium for automation?** By one signal
family. Chromium's CDP channel has historically left `cdc_` properties in the
page; Firefox's Juggler channel injects none, so that specific tell is absent.
Everything else is the same work on both engines.

**Does using Firefox make my automation undetectable?** No. It removes one tell.
A stock Playwright Firefox still reports `navigator.webdriver=true` and a raw
fingerprint, and no browser fixes IP reputation, quotas, or behaviour.

**What is the cdc_ variable?** A set of property names the Chrome DevTools
Protocol has attached to page objects, giving a detector a literal string to
match. Firefox's control mechanism does not add an equivalent.

**Does invisible_playwright work with normal Playwright code?** Yes. It returns a
real Playwright `Browser`, so every documented method works unchanged. The only
difference is the two-line launch.

**If the fingerprint is clean, why do I still get blocked?** Because the block is
probably not about the fingerprint. IP reputation, rate limits, and timing are
independent of the browser and are the usual cause once the browser layer is
clean.

**Should I switch from Chromium to Firefox just for this?** Only if the `cdc_`
family is actually your problem, which you can check by testing. For most blocks
the engine is not the deciding factor, and the fix is a cleaner exit and more
human behaviour.

## Sources

- Playwright's own [CDPSession documentation](https://playwright.dev/docs/api/class-cdpsession),
  which shows what raw Chrome DevTools Protocol access looks like from
  Playwright's side, and MDN's
  [`navigator.webdriver` reference](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
  read for what each exposes to a page rather than from third-party summaries.
- This project's release gates, which compare a patched build against a stock
  Firefox field by field, and confirm the webdriver flag and fingerprint surfaces
  are where the two diverge.

**See also:** [the cdc_ variable explained](cdc-variable-explained.md),
[Firefox versus Chromium for anti-detect](firefox-vs-chromium-antidetect.md), and
[what navigator.webdriver actually proves](navigator-webdriver-explained.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The one-family
advantage is real; the caveat that travels with it is the part worth remembering.*
