---
title: "Selenium detected by Cloudflare"
description: "The specific properties a Cloudflare challenge reads that Selenium leaves in their default, unmodified state - and why patching them one at a time from JavaScript is a losing game against a managed challenge."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 30
---

# Selenium detected by Cloudflare

A Selenium session hitting a Cloudflare-managed challenge - the interstitial,
or a Turnstile widget that never resolves - is one of the most common reports
in browser automation, and it has a specific, well-documented cause rather than
a mysterious one.

## What Cloudflare is actually reading

A managed challenge scores the session from several layers before anything you
did on the page is relevant:

- **The network path** - address type and reputation.
- **The TLS/HTTP handshake shape** - properties that differ between an
  automated client and a real browser's network stack.
- **What the browser reports about itself** - and this is where Selenium's
  defaults are specifically weak.

## Where Selenium's defaults fail this test

**[`navigator.webdriver`](navigator-webdriver-explained.md) is `true`.**
Selenium implements the W3C WebDriver spec, and that flag exists in the spec
precisely to announce automation. It is set by default in every unmodified
Selenium session, in every browser Selenium drives.

**geckodriver/chromedriver add an observable layer.** The separate driver
process that Selenium talks to over WebDriver's HTTP+JSON protocol introduces
[CDP-visible](bidi-vs-cdp-detection.md) and WebDriver-visible properties that a
stock retail browser session never produces, because a retail browser is
never being driven by an external process at all.

**Default launch flags are recognizable.** `--enable-automation` and similar
flags were historically set by default in automated Chrome sessions and are
directly checkable. Firefox under Selenium sets its own automation-related
preferences the same way.

**No history, no cookies, a fresh profile every run.** Separate from the
protocol-level tells: a session with zero browsing history behind it is itself
a signal a scoring system can weigh, independent of anything WebDriver-specific.

## Why patching one property at a time does not work

The common first fix is a JavaScript injection that overrides
`navigator.webdriver` to return `false`. This addresses exactly one of the
signals above and does two things wrong at once: it leaves the CDP-level and
driver-process-level tells untouched, and the override itself is detectable,
because a property that is normally a native getter now shows up with a
JavaScript-defined one if a script checks `Object.getOwnPropertyDescriptor`
or the function's own `toString()`.

A managed challenge that scores dozens of signals is not defeated by
patching one property from the page after the fact. The properties need to be
correct at the source - the browser build and the driver - not overridden
after the browser already exists.

## What actually changes the outcome

**Warm the session first.** A profile with real history and cookies, from a
prior real session, is a stronger signal than any single property fix.
`--user-data-dir` pointed at a persistent profile is the mechanism; it does
not remove the WebDriver flag, but it removes the "session with zero history"
signal.

**Address and pacing, before the browser.** If the request originates from a
datacenter address or arrives in an inhuman rhythm, no browser-side change
matters. This is usually the larger share of the score.

**A driver that does not announce itself.** [Patchright](vs-patchright.md)
removes the Chromium-side WebDriver and CDP-level tells at the driver layer
rather than patching properties from JavaScript, which is the correct place
to fix a protocol-level signal. On the Firefox side, this project does the same thing
architecturally: the properties Cloudflare checks are set correctly inside the
patched engine itself, not injected after the browser launches, which is why
they survive a `toString()` or descriptor check that a JavaScript override
does not.

## Short answers to the questions that lead here

**Does Selenium set navigator.webdriver to true?** Yes, by default, because it
is part of the W3C WebDriver spec Selenium implements.

**Can I just override navigator.webdriver with JavaScript?** You can, and it
fixes one property while leaving the CDP/driver-level and behavioural signals
untouched, and the override itself is checkable.

**Does using undetected-chromedriver fix this for Selenium?** It addresses
several of the same-family Chrome-side tells specifically. It does not touch
Firefox, and it does not change address or pacing signals.

**Will a proxy fix a Cloudflare challenge on Selenium?** If the address was
the trigger, often. If the trigger is the WebDriver flag or driver-level
signals, a proxy changes none of that.

**Is there a Selenium option that removes the automation flag?** Selenium
itself does not remove the WebDriver-mandated flag; that would mean not
implementing the spec it exists to implement. The fix lives at the driver or
engine layer, not in Selenium's own API surface.

**See also:**
[Migrating from Selenium to Playwright for stealth](migrate-selenium-to-playwright-stealth.md),
[selenium-stealth: is it still maintained](selenium-stealth-unmaintained.md),
and [what navigator.webdriver really tells a site](navigator-webdriver-explained.md)
for the flag's full mechanism.

## Sources

- [MDN, `Navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver) and the [WebDriver specification](https://www.w3.org/TR/webdriver2/#interface), retrieved 2026-09-10, for the flag's spec-mandated behaviour under Selenium.
- [Selenium's own documentation](https://www.selenium.dev/documentation/), retrieved 2026-09-10, for the driver-process architecture described above.
- Cloudflare's public documentation on managed challenges, for what a challenge scores beyond a single JavaScript property.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright, not Selenium. The
architectural argument here - fix it at the engine, not with a JavaScript patch
- is the same rule this project follows for its own patches.*
