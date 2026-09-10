---
title: "Python browser automation without Selenium: the options"
description: "Playwright and Pyppeteer, or no browser at all when the data is in the HTML. What leaving Selenium buys you, and what it does not change all by itself."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 48
---

# Python browser automation without Selenium

Selenium is the default answer people reach for, and it is often not the
right one for a new project. This page is the actual landscape: what replaces
it, what each option buys you, and - the question worth asking first - whether
you need a browser at all.

## Ask this first: do you need a browser?

If the data you want is present in the page's initial HTML response - view
source and search for it - you do not need a browser. `requests` or `httpx`
fetches the page in milliseconds with no rendering overhead. If the site does
heavy anti-bot fingerprinting at the TLS/HTTP layer even for a plain request,
`curl_cffi` reproduces a real browser's TLS handshake shape from Python
without launching one.

If the content only appears after JavaScript runs - infinite scroll, a
React/Vue app, content loaded via a later fetch call - then yes, you need a
real browser engine, and the rest of this page is for you. If the block is
specifically at the TLS/HTTP handshake layer rather than in the rendered
content, [curl_cffi](vs-curl-cffi.md) is the narrower fix, covered further
down.

## The Python options, without Selenium

**Playwright for Python.** The most complete modern option: auto-waiting
built in, first-class async support, and one API across Chromium, Firefox and
WebKit. Microsoft maintains it, and it ships its own browser builds rather
than driving whatever is installed on the machine. This is the framework most
new Python browser-automation projects should default to.

**Pyppeteer.** A Python port of Puppeteer, driving Chromium over CDP directly
with no separate driver process, similar in spirit to how Playwright talks to
Chromium. It has a smaller community than Playwright and historically lags
behind Puppeteer's own releases, which is worth weighing before committing to
it for a long-lived project.

**Requests-HTML** and similar lightweight wrappers exist but are largely
unmaintained at this point and add a thin, dated layer over an embedded
Chromium; for anything beyond a quick script, either of the two above is a
better foundation.

## What you gain by leaving Selenium specifically

**No separate driver process.** Selenium talks to geckodriver or chromedriver
over the [W3C WebDriver](navigator-webdriver-explained.md) HTTP protocol -
Playwright and Pyppeteer talk to the browser's own protocol directly, which is
faster and has one fewer moving part to keep version-matched with the
browser.

**Auto-waiting by default.** Selenium waits for an element to exist in the
DOM; Playwright waits for it to be actionable - visible, stable, not covered,
enabled - before acting, which removes a large share of the flaky-test
problem Selenium code is known for. [Selenium vs Playwright: the actual
differences](difference-between-selenium-and-playwright.md) has the full
comparison if that is the deciding factor for you.

**What does not change automatically.** Neither Playwright nor Pyppeteer is
inherently less detectable than Selenium out of the box. Both set
`navigator.webdriver` under their respective automation protocols, both ship
recognizable automation-build browsers by default, and both are subject to
the same class of TLS/driver-level tells Selenium has. Switching frameworks
for code-quality reasons is a good move on its own merits; switching
frameworks *expecting it to solve a detection problem* usually does not,
because the underlying browser identity is the thing that has to change, not
the API wrapped around it.

## If detection is the actual reason you are leaving Selenium

Then the framework was never the real lever. What moves the needle is the
browser build underneath, however you drive it:

- **On Chromium**, Patchright patches Playwright's own Chromium driver to
  remove the CDP-level and WebDriver-level tells at the source rather than
  papering over them from JavaScript.
- **On Firefox**, this project - [invisible_playwright](https://github.com/feder-cr/invisible_playwright) -
  is the same idea taken further: a Firefox patched at the C++ source, with
  Playwright's own unmodified API on top, so the code you write is standard
  Playwright and the browser underneath is not a stock automation build.

Both approaches keep Selenium out of the picture entirely and address the
actual layer where the tells live.

## Short answers to the questions that lead here

**What replaces Selenium in Python?** Playwright, for most new projects.
Pyppeteer if you specifically want a Puppeteer-equivalent API and are
comfortable with a smaller, slower-moving community.

**Do I need a headless browser for web scraping in Python?** Only if the data
you need is not present in the raw HTML response. Check with `curl` or
`requests` first; a browser is the more expensive tool and should be reached
for when it is actually necessary.

**Is Playwright faster than Selenium?** Generally yes, mostly from removing
the separate driver process and from Playwright's own auto-waiting reducing
retries.

**Is curl_cffi a replacement for a browser?** No, it replaces the need for a
browser specifically when the block is at the TLS/HTTP fingerprint layer and
the content does not require JavaScript execution.

**Will switching from Selenium to Playwright stop me getting blocked?** Not
by itself. Both default to a recognizable automation build; the browser
identity is the layer that needs to change, separate from which framework
drives it.

**See also:**
[Selenium vs Playwright: the actual differences](difference-between-selenium-and-playwright.md),
[Migrating from Selenium to Playwright for stealth](migrate-selenium-to-playwright-stealth.md),
and [Python web scraping blocked? The TLS fingerprint reason](web-scraping-tls-fingerprint-requests-blocked.md)
for when you do not need a browser at all.

## Sources

- [Playwright for Python documentation](https://playwright.dev/python/docs/intro), retrieved 2026-09-10, for the API and browser-management model.
- [Pyppeteer's own repository](https://github.com/pyppeteer/pyppeteer), retrieved 2026-09-10, for its maintenance status and scope relative to upstream Puppeteer.
- [curl_cffi's own documentation](https://github.com/lexiforest/curl_cffi), retrieved 2026-09-10, for TLS-fingerprint impersonation without a browser engine.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The framework
question and the browser-identity question are kept separate on purpose,
because most "I switched frameworks and I'm still blocked" reports are the
second question wearing the first one's name.*
