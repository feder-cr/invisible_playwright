---
title: "Why Playwright's bundled Firefox is easy to detect"
description: "Playwright's Firefox carries version markers no real release has. See the concrete tells, why editing the user agent does not fix them, and the fix that works."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 28
---


# Why Playwright's bundled Firefox is easy to detect

Playwright does not drive the Firefox you download from mozilla.org. It ships and
downloads its own Firefox, patched to speak the automation protocol Playwright needs.
That build works beautifully for testing your own pages. The problem is that it is not
a released Firefox, and it does not pretend to be one: it carries version markers and
runtime traits that a real Firefox never has, and those differences are stable, cheap
to read, and exactly what a fingerprinting page keys on.

This page shows the concrete tell in Playwright's own build, why setting a user agent
string does not remove it, and the one integration point that does: swapping the
bundled binary for a differently-patched real-version Firefox and driving it with the
same stock Playwright API. It closes with the honest limit of that swap, because a
browser that looks real does not fix an address that is not.

## The bundled build is not a shipped Firefox

When you install Playwright, it fetches a browser it calls "firefox" into
[its own cache](https://playwright.dev/python/docs/browsers), a documented step of
every install. That binary is compiled from Firefox source with a control protocol
added on top so Playwright can attach to it. It is a genuine Firefox engine. It is not
any Firefox that a human ever installed.

The gap shows up in several places at once:

- **The version string does not track the release channel.** Playwright pins its
  browser to a build that lags or leads the public Firefox on the same date, and the
  reported version can be one that was never handed to end users at all.
- **The build identifier is the automation vendor's, not Mozilla's.** A released
  Firefox carries a build ID and a set of internal markers that a page can read
  indirectly; the bundled build carries different ones.
- **Runtime traits differ.** How certain APIs behave, which surfaces are present, and
  the precise shape of a handful of values are all decided at compile time, and the
  automation build was compiled with different switches than the release.

None of this is a bug in Playwright. The build exists to run your test suite, and for
that it is correct. It only becomes a tell when you point it at a page whose job is to
notice that the browser in front of it is not the browser it claims to be.

## The concrete tell: version markers that no real user has

The version string is the simplest of these tells: a real Firefox reports one drawn
from millions of real installs, on the release cadence everyone else is on, while the
bundled automation build reports one tied to Playwright's own pin instead.

A detector does not need a secret list to use this. It already has the population
distribution of real Firefox versions, because it sees real traffic. A version that
sits off that distribution, or that pairs with a build marker no release carries, is a
single boolean: this client is not a stock browser. Combined with anything else - the
network handshake, one runtime trait - it stops being a guess.

This is the same failure mode described for Chromium builds in
[why Chromium is not Chrome](chromium-is-not-chrome.md): the engine is real, but the
distribution it was cut from is not the one real users run, and consistency checks
read the difference. It is also why
[the automation protocol itself is a surface](bidi-vs-cdp-detection.md) - the version
markers and the control channel are two faces of the same "this is a vendor build"
signal.

## Why a user agent string does not fix it

The reflex is to override the
[user agent](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/userAgent)
so it reports a normal release version. That edits one string. It does not edit the
build.

The version a page reads is not only the user agent header. It is also what the
browser reports through several JavaScript surfaces, what the build markers imply, and
how the version-dependent runtime traits actually behave. Change the header alone and
you have created a contradiction: the header says one release, and everything the page
can cross-check against it says another. As the
[checklist for being detected on one site](playwright-detected-as-bot.md) puts it, a
detector rarely asks whether a value is unusual - it asks whether two values that
should agree, do. Overriding the user agent guarantees they do not.

You cannot patch a compiled-in trait from JavaScript, and you cannot make a header
agree with a build it does not describe. The only way to report a real Firefox
version consistently is to be running a real Firefox version.

## The integration point: swap the binary, keep the API

invisible_playwright plugs in at exactly this point, and the plug is deliberately
narrow. Stock Playwright is designed to launch its bundled browser, but it will just
as happily attach to a different Firefox that speaks the same control protocol. So the
integration point is to hand Playwright a differently-patched, real-version Firefox
and let every line of your automation stay exactly as it was.

The launch is two lines different from plain Playwright, and nothing after it changes:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate("navigator.userAgent"))
    # reports a released Firefox, and the build markers behind it agree
```

The `browser` object is a real `playwright.sync_api.Browser`. Every method you know
works exactly as documented upstream; there is no wrapped subset to learn. The async
form is identical in shape:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    print(await page.evaluate("navigator.userAgent"))
```

Because the engine is a real-version Firefox rather than the automation vendor's
build, the version markers, the build identifiers and the version-dependent runtime
traits all describe the same released browser, and they agree with each other instead
of contradicting a hand-set header. The seed makes that identity reproducible: the
same seed produces the same browser every run, which is what turns a flaky failure
into one you can replay. Pinning specific fields while leaving the rest seed-derived is
covered in [pinning fingerprint fields](pinning.md).

## What the swap does not do

The swap removes a specific class of tell: the ones that come from running the
automation vendor's build instead of a shipped one. That is the fingerprint layer, the
version markers, and the runtime traits, and it is why a session driven this way reads
as a genuine Firefox to the checks that inspect the browser itself.

It does not touch the things that were never about the browser:

- **IP reputation.** A real-looking browser on a known datacenter address still loses.
  The address is not a browser property. See
  [test through the proxy you deploy with](how-to-test-bot-detection.md) for why the
  exit has to be part of the test.
- **Per-account quotas and rate limits.** These are counted server-side, per account
  or per address, and no fingerprint changes a count.
- **Behaviour and timing.** A pointer that teleports, a form filled in eighty
  milliseconds, or, for agents, [a pause shaped like model latency](ai-browser-agents-stealth.md),
  are all read from what you do, not from what the browser is.
- **The network handshake in isolation.** invisible_playwright is a real Firefox, so
  its handshake is a real Firefox handshake, but the topic is worth understanding on
  its own terms: [the TLS fingerprint no in-page test can see](ja3-ja4-tls-fingerprint.md).

The honest framing is that the swap makes the browser look like a real browser driven
by a real person, which is why it passes most checks that inspect the browser. You
still supply the clean exit, the human pacing, and the account that is within its
limits. The tool removes one specific, avoidable tell; it does not remove the reasons a
session can legitimately be turned away.

## Conclusion

Playwright's bundled Firefox is easy to detect for a boring reason: it is not the
Firefox anyone ships, and it does not hide that. The version markers and runtime traits
of an automation vendor's build are stable, readable, and off the distribution of real
traffic, and no amount of header-editing reconciles a string with the build behind it.

The fix is not to patch the symptom but to change the engine: drive a real-version
Firefox with the same stock Playwright API, so the markers and traits describe a
browser real users actually run. Do that and the browser layer stops being your
problem. Then spend your effort where it still matters - the address, the pace, and the
account - because those were never the browser's to fix.

## Short answers to the questions that lead here

**Does Playwright use a real Firefox?** It uses a real Firefox engine, patched by the
automation vendor and pinned to its own version. It is a genuine engine but not a build
any end user installs, and its version markers differ from a shipped release.

**Can I just set the user agent to a normal version?** No. That edits one string while
the build markers and version-dependent traits still describe the vendor build, which
creates a contradiction a detector reads more easily than the original.

**What exactly gives the bundled build away?** Version markers and build identifiers
that do not match a released Firefox, plus compile-time runtime traits, all readable
from the page and cross-checkable against each other.

**How does invisible_playwright change this?** It hands stock Playwright a
differently-patched, real-version Firefox to drive instead of the bundled binary, so
the markers and traits describe a released browser. Your Playwright code does not
change.

**Will swapping the binary make me undetectable?** No, and nothing does. It removes the
tells that come from running an automation vendor's build. It does not fix IP
reputation, quotas, rate limits, or behaviour, which you still supply.

**Do I have to rewrite my Playwright code?** No. The launch is two lines different and
the returned object is a real Playwright `Browser`, so every method works as
documented upstream.

## Sources

- [Playwright's own browser-download behaviour](https://playwright.dev/python/docs/browsers):
  it fetches and pins its own Firefox build into its cache rather than using an
  installed release.
- This project's fingerprint parity work, which compares a released Firefox against the
  driven browser field by field, including the version markers and build identifiers
  that separate a shipped build from an automation vendor's build.
- The project's release gates, which measure realness as the presence of the right
  signal rather than the absence of a wrong one.

**See also:** [why Chromium is not Chrome](chromium-is-not-chrome.md) for the same gap
in the other engine, [the automation-protocol surface](bidi-vs-cdp-detection.md) for
the control channel behind these markers, and
[the checklist for being detected on one site](playwright-detected-as-bot.md) for the
order to work the rest in.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The bundled build is
correct for testing your own pages; it is only a tell when the page in front of it is
trying to notice.*
