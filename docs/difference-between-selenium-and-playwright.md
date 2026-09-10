---
title: "Selenium vs Playwright: the actual differences"
description: "Protocol, driver architecture, auto-waiting and browser support, compared from each project's own documentation - and the one difference neither project's docs will tell you: what each one looks like to a defended site."
parent: "Comparisons"
nav_order: 39
---

# Selenium vs Playwright: the actual differences

Both drive a real browser. The differences that matter are in how they talk to
it, not in what either one can technically click, and most comparisons stop
before the difference that a defended site actually reads.

## Protocol

**Selenium** speaks the W3C WebDriver protocol: your test code sends an HTTP
request to a driver process (geckodriver, chromedriver), which translates it
into browser-specific automation calls. That extra hop is a JSON server in the
middle of every action.

**Playwright** speaks each browser's own remote-debugging protocol directly -
CDP for Chromium, its own protocol for Firefox and WebKit - with no
intermediate driver process. One fewer moving part, and it is also why
Playwright ships its own browser builds rather than driving whatever happens
to be installed.

The practical consequence: Playwright is generally faster and has fewer version
mismatches to manage, because there is no separate driver binary to keep in
sync with the browser. Selenium's WebDriver layer is a published spec, which
is why so many languages and tools speak it; Playwright's protocols are not a
public spec, which is why Playwright itself has to ship the client libraries.

## Auto-waiting

Selenium waits for an element to exist. Playwright waits for it to be
**actionable** - visible, stable, not covered by another element, not
disabled - before acting on it. This single design choice removes a large
share of the flaky-test problem Selenium suites are known for, where a test
clicks an element that technically exists but has not finished animating into
place.

You can build the same waiting logic into a Selenium suite by hand. Playwright
builds it into every action by default.

## Browser support and architecture

Selenium supports whatever has a WebDriver implementation: Chrome, Firefox,
Edge, Safari, and older browsers nobody ships anymore. Playwright supports
Chromium, Firefox and WebKit, using patched builds it maintains itself rather
than the retail installs on your machine.

That difference cuts both ways. Selenium can drive the exact browser your users
have, retail build and all. Playwright's browsers are a known, pinned,
Playwright-maintained artifact - consistent across machines, and also a
specific automation build with its own fingerprint, discussed below.

## Language and ecosystem

Selenium has bindings older and wider than Playwright's: every mainstream
language, decades of Stack Overflow answers, and a WebDriver spec that other
tools (Appium, for mobile) build on. Playwright covers JavaScript/TypeScript,
Python, Java and .NET officially, younger, with fewer historical workarounds
needed because the API was designed after auto-waiting was already a known
problem.

## The difference neither project's own docs foreground

Both are legitimate testing tools built for testing, and neither one was
designed against adversarial detection. But they end up in different places on
that axis anyway, for structural reasons:

**[`navigator.webdriver`](navigator-webdriver-explained.md) is `true` under
both by default**, because it is part of the WebDriver spec both implement,
not a Selenium-specific flag.

**Selenium via geckodriver or chromedriver adds an extra network hop and
process**, which shows up in timing and in a few CDP/WebDriver-specific
properties a determined detector can check for.

**Playwright's own browser builds [carry properties that differ from a retail
install](chromium-is-not-chrome.md)** - build flags, missing OS integration
pieces, a UA string that can disagree with what the build actually is -
because they were built for automation, not for the browser vendor's own
release pipeline.

Neither framework is "worse" here; both are automation tooling used the way
automation tooling looks, which is a separate question from which one has the
nicer API. This project exists because of that gap:
[invisible_playwright](https://github.com/feder-cr/invisible_playwright) is
Playwright's own API, unchanged, driving a Firefox patched at the C++ source
rather than the stock build - same protocol and auto-waiting Playwright always
had, different browser underneath. It does not touch Selenium's side of this
at all.
[Migrating from Selenium to Playwright for stealth](migrate-selenium-to-playwright-stealth.md)
is the practical guide if the browser, not the API, is your actual problem.

## Which to pick

- **An existing large Selenium suite, no detection problem:** stay. Rewriting a
  working suite for architecture purity is rarely worth it.
- **A new suite, no detection problem:** Playwright, for auto-waiting alone.
- **Automation is being recognized and you need the retail browser exactly as
  your users have it:** Selenium can drive it directly; Playwright's patched
  builds are a different thing, unless you point it at a real engine as this
  project does.
- **Automation is being recognized and you can accept a modified engine:**
  Playwright with a patched build.

## Short answers to the questions that lead here

**Is Playwright faster than Selenium?** Generally yes, from the missing driver
hop and less flakiness from auto-waiting, not from any raw execution
advantage.

**Is Playwright replacing Selenium?** For new projects, largely, but Selenium's
installed base and WebDriver's status as a real spec keep it relevant,
especially where Appium and mobile matter.

**Does Selenium still get updates?** Yes, actively maintained, including 4.x's
own move toward Chrome DevTools Protocol features alongside WebDriver.

**Which one is more detectable?** Neither is inherently worse; both default to
`navigator.webdriver = true` and both are recognizable automation stacks
unless something changes the underlying browser.

**Can I use Playwright's API with Selenium's driver model?** No, they are
separate protocols end to end. Patchright and similar projects patch
Playwright's own driver instead of switching protocols.

**See also:**
[selenium-driverless vs invisible_playwright](vs-selenium-driverless.md) for the
driver-removal approach from Selenium's side, and
[Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md)
for the engine question once you have picked a framework.

## Sources

- [Selenium's own documentation on WebDriver and its components](https://www.selenium.dev/documentation/overview/components/), retrieved 2026-09-10.
- [Playwright's documentation on browsers and why it ships its own builds](https://playwright.dev/python/docs/browsers), retrieved 2026-09-10.
- [MDN, `Navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver) and the [WebDriver specification](https://www.w3.org/TR/webdriver2/#interface), for the flag both frameworks inherit.
- This project's own fingerprint gates, for the claim that a patched engine changes what the browser reports versus what Playwright's stock builds report.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. This page compares
frameworks, not engines - the engine question is a separate page, linked above,
because conflating them is the most common mistake in this comparison.*
