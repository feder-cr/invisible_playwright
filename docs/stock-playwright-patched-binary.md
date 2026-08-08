---
title: "Stock Playwright, patched Firefox: how they connect"
description: "Join unmodified Playwright to a patched Firefox binary via prefs and environment contract, so Playwright upgrades stay clean and the fingerprint persists."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 30
---


# Stock Playwright, patched Firefox: how they connect

invisible_playwright joins unmodified Playwright to a patched Firefox binary: you keep
stock Playwright exactly as it ships from upstream, and the stealth lives one layer down,
inside the patched binary and the preferences it reads. It does not fork or patch
Playwright at all.

Most stealth tooling in this space instead works by editing the automation library: a
fork of Playwright, a monkeypatch that rewrites methods at import time, or a page-level
script that overrides browser APIs after the page loads. All three couple your disguise
to a specific version of the driver, and all three break the day you upgrade it.

This page is about the seam that avoids that: what connects the unmodified driver to the
patched engine, why that choice keeps upgrades clean, and the one thing it deliberately
does not do for you.

## What "stock Playwright" actually means here

Stock means the Playwright you already installed from PyPI, unmodified. The classes,
the methods, the async and sync flavours, the return types are all the upstream ones.
When you launch through invisible_playwright, the `browser` object you get back is a
real [`playwright.sync_api.Browser`](https://playwright.dev/python/docs/api/class-browser)
(or its async twin), and every method on it behaves exactly as the upstream
documentation says.

There is no wrapped subset to learn and no shimmed method that behaves differently from
the documented one. If a snippet works against plain Playwright, it works here after a
two-line change to how the browser is launched, and nothing after that line changes.
The distinction between a page and a context, for one example, is
[the ordinary Playwright distinction](playwright-new-page-vs-new-context.md), not a
reinvented one.

## The integration seam: a launch path and a prefs contract

The seam is a launch path plus a preferences contract: three things happen before
Playwright ever launches anything, and then Playwright drives the resulting binary
exactly like any other Firefox.

When you construct `InvisiblePlaywright`, three things happen before Playwright is ever
asked to launch. First, the patched Firefox binary is located, downloading and caching
itself on first use if you have not pointed at one already. Second, a seed is turned
into a full browser identity: GPU, audio, fonts, screen, and roughly four hundred other
fields, all correlated so they agree with each other. Third, that identity is written
out as a set of Firefox preferences and environment values that the patched binary knows
how to read.

Then stock Playwright is handed a launch path pointing at that binary, plus the prefs and
environment that carry the identity. Playwright launches it the way it launches any
Firefox, speaks its normal protocol to it, and drives it with its normal API. The
stealth is not in anything Playwright does. It is in the binary Playwright launched and
the preferences that binary read on startup.

That contract is a set of documented, public-shaped Firefox preferences plus a few
environment values. It is a stable interface between two pieces: the automation library
on one side, the engine on the other. Neither side reaches into the other. Playwright
does not know the browser is patched, and the browser does not care which version of
Playwright launched it, because they meet only at that prefs and launch-path boundary.

If you ever drive
[`firefox.launch()`](https://playwright.dev/python/docs/api/class-browsertype) yourself
instead of using the class, the same contract is available directly through Playwright's
own `executable_path`, `firefox_user_prefs` and `env` launch options, which is why
[Configuration](configuration.md) shows
passing the proxy dict into the preferences helper. And when a preference you expect does
not seem to take effect, the fix is usually in that contract rather than in Python, which
is what [firefox prefs not applying](firefox-prefs-not-applying.md) walks through.

## A runnable example: the two-line launch and one navigation

The switch from plain Playwright is two lines, and everything after is stock API. Pass a
seed and the identity is reproducible across runs.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
    page.click("#submit")   # ordinary Playwright, mouse arcs on a Bezier curve
```

The async form is the same shape against the async API:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    print(await page.title())
```

`browser`, `page`, `new_page`, `goto`, `title`, `click`: all of that is upstream
Playwright, unchanged. The only invisible_playwright-specific thing on the screen is the
constructor. Everything the fingerprint does happened before `new_page` was called, in
the binary the constructor launched.

## Why a Playwright upgrade does not break the fingerprint

An upgrade does not break the fingerprint because the identity lives in Firefox
preferences read at startup, not in Python code a new Playwright version might touch.
That is the payoff of putting the stealth in the binary rather than in Python overrides.

A page-level override script has to run inside the page and win a race against the site's
own code, and it depends on the exact API surface the driver exposes. A monkeypatch
depends on the internal shape of the library it is rewriting. Change the library under
either of them and the disguise can silently stop matching, which is the failure mode
behind a lot of "it worked last month" reports.

Because the identity here is delivered as browser preferences that the engine reads at
startup, none of it lives in a place a Playwright upgrade touches. Upgrade Playwright and
the same binary launches with the same prefs and produces the same fingerprint. The one
coupling that does exist is between the driver and the browser's own remote protocol,
which upstream keeps stable within a range and which this project tracks deliberately, as
[protocol drift between Playwright and the binary](playwright-protocol-drift.md)
describes. That is a narrow, monitored seam, not the broad and fragile one a fork or a
monkeypatch signs you up for.

## The honest caveat: identity is not the whole session

Identity is not the whole session: this integration owns the browser's fingerprint and
nothing about your IP reputation, quotas, or behaviour. Being blunt about that boundary
is the point of this section, because the seam above is about browser identity and
nothing else.

What the integration delivers is a browser that reads as a genuine Firefox driven by a
real person: the fingerprint is internally consistent, the TLS handshake is a real
Firefox handshake because the engine is a real Firefox, and the driver layer does not
announce automation. That is why it passes most in-browser detection, the fingerprint,
tampering and driver checks that public suites like CreepJS, BotD, FingerprintJS,
sannysoft and BrowserLeaks measure.

It does not, and cannot on its own, fix the parts of a session that are not the browser:

- **IP reputation.** A perfect browser on a datacenter address or a widely-shared exit is
  still on a bad address. You supply a clean proxy.
- **Per-account quotas and rate limits.** These are counted server-side against your
  account or your address, and no browser property changes the count.
- **Behaviour and timing.** Pointer motion is humanised for you, but pacing, order of
  actions, and the rhythm of a whole session are yours to get right.

The realistic claim is that this makes the browser look real, which handles one large and
common category of blocks and leaves the other categories to you. Anyone promising it
"bypasses everything" is selling something that does not exist. The
[troubleshooting checklist for being detected on one site](playwright-detected-as-bot.md)
exists precisely because the fix is often in one of those other categories, not in the
fingerprint.

## Conclusion

The design in one sentence: unmodified Playwright meets a patched Firefox at a
preferences and launch-path contract, so the stealth lives in the engine and the driver
stays stock. That is what keeps a Playwright upgrade from touching your fingerprint, and
it is why the integration is two lines rather than a fork you have to maintain.

It also draws a clean line around what the tool owns. It owns browser identity, and it
does that well enough to pass most in-browser detection. It does not own your address,
your quotas, or your pacing, and pretending otherwise would only cost you a debugging
afternoon spent in the wrong layer.

## Short answers to the questions that lead here

**Does invisible_playwright fork or patch Playwright?** No. You install stock Playwright
and stock invisible_playwright; the browser object returned is a real Playwright
`Browser` with every method unchanged. The stealth is in the binary and its preferences,
not in the Python driver.

**Will upgrading Playwright break the fingerprint?** No. The identity is delivered as
browser preferences the engine reads at startup, which a Playwright upgrade does not
touch. The only version coupling is the driver-to-browser remote protocol, which is
tracked deliberately.

**How does stock Playwright know to launch the patched browser?** The constructor hands
Playwright a launch path to the patched Firefox binary plus the prefs and environment
that carry the seeded identity. Playwright launches it as it would any Firefox.

**Does this make me undetectable?** No, and treat any tool that says so with suspicion.
It makes the browser read as a real one, which handles fingerprint, TLS and driver
checks. It does not fix IP reputation, account quotas, or behaviour.

**Do I still need a proxy?** For most real targets, yes. A consistent browser on a known
or datacenter IP still loses on the address alone, which the browser layer cannot change.

**Can I use the normal Playwright API for contexts, downloads, and the rest?** Yes. It is
the real upstream API with no wrapped subset, so anything documented for Playwright Firefox
works as written.

## Sources

- This project's own [Quickstart](quickstart.md) and [Configuration](configuration.md)
  pages, which describe the two-line launch, the returned real `Browser` object, and the
  environment contract.
- Playwright's own [Browser](https://playwright.dev/python/docs/api/class-browser) and
  [BrowserType](https://playwright.dev/python/docs/api/class-browsertype) API
  documentation, which describe the object this integration returns unchanged and the
  `executable_path` / `firefox_user_prefs` / `env` launch options the contract relies on.
- The release gates that measure the fingerprint, TLS and driver layers against public
  detection suites, run through a proxy and compared field by field against a stock
  Firefox.

**See also:** [Configuration](configuration.md) for proxies, timezone and the environment
variables, and [protocol drift between Playwright and the binary](playwright-protocol-drift.md)
for the one seam an upgrade can affect.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The two-line launch is the
whole surface area; everything else in this page is what sits behind that line.*
