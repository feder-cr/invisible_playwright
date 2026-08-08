---
title: "Comparisons"
description: "invisible_playwright against the closest alternatives at each layer - Camoufox, Patchright, nodriver, playwright-stealth - plus the engine question underneath all of them: Firefox or Chromium."
has_children: true
nav_order: 4
---

# Comparisons

Most comparisons in this space put tools side by side that are not actually
comparable: a page-level script against a patched binary, a Chromium tool against a
Firefox one. These pages try not to do that. Each one names the layer the comparison
actually happens at, states plainly where the other tool covers more, and does not
claim an advantage it could not verify against the other project's own source or
documentation.

Start with [Three ways to make Playwright undetected](playwright-stealth-levels.md)
if you want the map before the individual matchups - it's the frame every comparison
below sits inside.

## Concepts and the map

- [Three ways to make Playwright undetected](playwright-stealth-levels.md) - The three levels stealth tools work at - page, driver, engine - and what each reaches.
- [Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md) - No CDP surface and one identity, traded against Firefox's smaller traffic share.
- [Chromium is not Chrome, and detectors know the difference](chromium-is-not-chrome.md) - Chrome for Testing plays H.264 but fails a Widevine DRM check no patch closes.
- [WebDriver BiDi vs CDP: does the new protocol hide you](bidi-vs-cdp-detection.md) - BiDi standardizes the control wire, but automation leaks live in the page, not the protocol.
- [Do I need an anti-detect browser or just Playwright?](do-i-need-an-antidetect-browser.md) - What a paid anti-detect GUI adds over stock Playwright driving a patched Firefox.
- [Migrating from Puppeteer to Playwright for stealth](migrate-puppeteer-to-playwright-stealth.md) - The near one-to-one API mapping, and what changes for detection off the CDP stack.
- [Migrating from Selenium to Playwright for stealth](migrate-selenium-to-playwright-stealth.md) - Map driver.get and find_element to page.goto and locators, dropping the WebDriver server.

## Patched-engine, driver-patch and stealth-plugin tools

- [invisible_playwright vs Camoufox: two patched Firefoxes](vs-camoufox.md) - Both patch Firefox in C++; how each builds its fingerprint and whether a run replays.
- [invisible_playwright vs Patchright: driver vs engine](vs-patchright.md) - Patchright patches the Playwright driver on Chromium; this patches Firefox itself. Often you need both.
- [invisible_playwright vs rebrowser-patches: the same CDP fix](vs-rebrowser-patches.md) - Fixes the Runtime.enable CDP leak on Chromium; reaches neither Firefox nor fonts, GPU, canvas, audio.
- [undetected-playwright vs a patched Firefox binary](vs-undetected-playwright.md) - Hides Playwright's init-script and bindings tells; a patched Firefox closes the fingerprint layer.
- [invisible_playwright vs playwright-stealth: page vs engine](vs-playwright-stealth.md) - playwright-stealth patches the page in about four lines; this rebuilds the engine.
- [playwright-extra stealth plugins vs a patched browser](vs-playwright-extra-stealth.md) - Applies stealth plugins as injected JavaScript a detector reads back; a patched engine compiles them in.
- [invisible_playwright vs fingerprint-suite: injection vs engine](vs-fingerprint-suite.md) - fingerprint-suite injects a generated fingerprint into a page; this sets it in the engine.
- [invisible_playwright vs playwright-with-fingerprints](vs-playwright-with-fingerprints.md) - Injects remote-service fingerprints into a Windows-only, ageing Chromium build, and what that trade costs.
- [puppeteer-real-browser vs invisible_playwright](vs-puppeteer-real-browser.md) - Node plus real Chrome with runtime property patches vs Python plus Firefox with compiled spoofs.

## CDP and driverless Chrome tools

- [invisible_playwright vs nodriver and undetected-chromedriver](vs-nodriver.md) - Chrome-only tools, not Playwright forks; neither hides IP or fingerprint, by their own docs.
- [pydoll vs invisible_playwright: CDP without a driver](vs-pydoll.md) - pydoll drives Chrome over CDP with no chromedriver; this ships seed-consistent Firefox surfaces.
- [zendriver vs invisible_playwright: Chrome CDP vs Firefox](vs-zendriver.md) - zendriver drives Chrome over CDP; this drives a patched Firefox with cross-checked surfaces.
- [selenium-driverless vs invisible_playwright stealth](vs-selenium-driverless.md) - Drops chromedriver by driving Chrome over raw CDP but leaves rendering and TLS stock.
- [invisible_playwright vs SeleniumBase UC Mode](vs-seleniumbase-uc-mode.md) - UC Mode detaches chromedriver but never changes the engine, GPU, fonts, canvas or TLS.
- [undetected-chromedriver vs a patched Firefox browser](vs-undetected-chromedriver.md) - Strips the driver's cdc_ and webdriver leaks but ships a stock Chromium fingerprint.
- [invisible_playwright vs Ulixee Hero](vs-ulixee-hero.md) - Replayed emulation on stock Chromium vs a fingerprint decided natively in patched Firefox.

## Non-Playwright frameworks and HTTP-client libraries

- [botasaurus vs invisible_playwright: framework vs library](vs-botasaurus.md) - Batteries-included Chrome scraping framework vs a patched-Firefox engine driven with stock Playwright.
- [invisible_playwright vs Scrapling](vs-scrapling.md) - Scrapling is an adaptive parser, not a stealth engine; its ceiling is whichever browser it wraps.
- [scrapy-playwright vs a patched Firefox for stealth](vs-scrapy-playwright.md) - Runs stock Playwright inside Scrapy; point the handler at a patched Firefox and keep the scheduler.
- [invisible_playwright vs DrissionPage](vs-drission-page.md) - Fuses an HTTP session mode and a CDP browser mode, each failing a different check half.
- [curl_cffi vs invisible_playwright: TLS client vs browser](vs-curl-cffi.md) - Replays a browser's TLS and HTTP/2 at byte level but runs no JavaScript.
- [invisible_playwright vs hrequests](vs-hrequests.md) - A hybrid TLS-impersonating HTTP client plus a browser mode with injected fingerprints.
- [tls-client vs a real browser: when TLS is enough](vs-tls-client.md) - When JA3/HTTP2 socket spoofing suffices on JSON and HTML, and where JS forces a full browser.

## Unmaintained tools

- [puppeteer-extra-plugin-stealth: unmaintained since 2024](puppeteer-extra-stealth-unmaintained.md) - No real update since mid-2024; old patches still work but the checklist froze.
- [pyppeteer's own maintainer says to switch to Playwright](pyppeteer-unmaintained-playwright.md) - Its README calls it unmaintained and points to playwright-python; what switching does not fix.
- [selenium-stealth hasn't been updated since December 2021](selenium-stealth-unmaintained.md) - Last commit December 2021; what its property patches still do and what to check today.
- [Splash is unmaintained, and it was never a real browser](splash-unmaintained-qtwebkit.md) - A QtWebKit render service reporting an engine no real browser ships, which no spoof reaches.
