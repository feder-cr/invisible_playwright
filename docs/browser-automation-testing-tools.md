---
title: "Browser automation testing tools, and where each one actually fits"
description: "Playwright, Selenium, Cypress and Puppeteer solve overlapping but different problems. A field guide by what you are actually testing - cross-browser regression, a single-browser app, API-adjacent flows, or real-world scraping - rather than a feature checklist."
parent: "Comparisons"
nav_order: 41
---

# Browser automation testing tools, and where each one actually fits

Four names come up constantly and get compared as if they were interchangeable:
Playwright, Selenium, Cypress and Puppeteer. They overlap enough to confuse
and differ enough that picking by feature-list length is how people end up
fighting the wrong tool for a year.

## The four, by what they are actually built for

**Playwright.** General-purpose, cross-browser (Chromium, Firefox, WebKit),
built by Microsoft with auto-waiting and multi-language support (JS/TS,
Python, Java, .NET). The current default recommendation for new
browser-automation projects that need real cross-browser coverage.

**Selenium.** The oldest and most widely deployed, via the W3C WebDriver
standard. Broadest language support, largest historical ecosystem, and the
one that can drive an actual retail browser install rather than a
framework-maintained build - relevant if you specifically need to test
against exactly what your users have installed.

**Cypress.** Built specifically for testing web applications from inside the
browser, running in the same run-loop as your app rather than driving it from
outside. Excellent developer experience and debugging for single-page apps,
historically Chromium-only in practice (with newer versions adding limited
Firefox and WebKit support), and architecturally not built for the kind of
cross-origin, multi-tab scraping work Playwright and Selenium both handle
natively.

**Puppeteer.** Chromium/Chrome only, Node.js only, maintained by the Chrome
team. If your target is specifically Chrome and your stack is Node, it is a
direct, well-supported option; it does not cover Firefox or WebKit at all.

## Choosing by what you are actually testing

| Your situation | Reach for |
|---|---|
| Cross-browser regression suite, multiple languages on the team | Playwright |
| Testing a Chrome-only internal app, Node stack already in place | Puppeteer |
| Fast-feedback testing of a single-page app you are actively developing | Cypress |
| Must drive an actual retail-installed browser, not a framework build | Selenium |
| Large legacy Selenium suite that works | Selenium, don't rewrite for its own sake |
| Real-world scraping across multiple sites, need Firefox specifically | Playwright |

## The axis none of these four are built around

All four of these are testing tools, built to verify that **your own
application** behaves correctly under known, cooperative conditions. None of
them was designed against a page actively trying to distinguish automation
from a human - because for their primary use case, the page being tested is
yours, and it has no reason to fight you.

That changes completely when the target is someone else's site and it is
actively fingerprinting. Every one of these four, used with its default
browser build, presents recognizable automation signals: `navigator.webdriver`
under WebDriver-based tools, CDP-visible properties under CDP-driven ones,
and browser builds that differ from a retail install in ways a determined
detector checks for. Picking Playwright over Selenium, or Puppeteer over
Cypress, does not change this axis at all - it is orthogonal to which testing
tool you picked.

## Where this project sits

[invisible_playwright](https://github.com/feder-cr/invisible_playwright) is
not a fifth entry on the list above competing for the same job. It answers a
different question: given that you have already picked Playwright's API
(unchanged, same code you would write against any Playwright browser), what
if the Firefox underneath it was patched at the C++ source rather than
Playwright's stock automation build? It is the engine layer beneath a testing
tool, not a replacement for one.
[Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md)
covers why Firefox was the base worth patching, and
[patchright vs invisible_playwright](vs-patchright.md) covers the equivalent
idea applied to Chromium instead.

## Short answers to the questions that lead here

**Is Playwright better than Selenium?** For new cross-browser projects,
usually yes - faster, auto-waiting, fewer moving parts. Selenium's breadth of
language support and ability to drive a real retail browser remain real
advantages in specific cases.

**Is Cypress a replacement for Selenium or Playwright?** Not a direct one.
Cypress is purpose-built for testing an application you control from inside
the browser's run-loop, and is a weaker fit for cross-origin scraping or
multi-tab flows that Selenium and Playwright handle natively.

**Does Puppeteer support Firefox?** Puppeteer has had experimental Firefox
support at various points; it is not the primary, well-maintained target the
way Chrome is. If Firefox specifically matters, Playwright is the
better-supported cross-browser option.

**Which tool is hardest to detect?** None of the four is built with detection
resistance as a design goal, and the default automation build under any of
them is recognizable. That is a separate axis from which testing tool fits
your testing needs.

**Can I use these for scraping instead of testing?** Yes, and Playwright and
Selenium in particular are commonly used this way - but recognize that you
have moved outside each tool's primary design intent, onto an axis (adversarial
detection) none of them was built to address.

**See also:**
[Selenium vs Playwright: the actual differences](difference-between-selenium-and-playwright.md),
[Migrating from Puppeteer to Playwright for stealth](migrate-puppeteer-to-playwright-stealth.md),
and [Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md).

## Sources

- [Playwright's own documentation](https://playwright.dev/), [Selenium's documentation](https://www.selenium.dev/documentation/), [Cypress's documentation](https://docs.cypress.io/), and [Puppeteer's documentation](https://pptr.dev/), all retrieved 2026-09-10, for each project's stated scope and browser support.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
which is not a competitor to any of the four tools above - it sits underneath
one of them. The page is organized around picking the right testing tool
first, because that decision is independent of the engine question it exists
to answer.*
