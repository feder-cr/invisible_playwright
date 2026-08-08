---
title: "Migrating from Selenium to Playwright for stealth"
description: "Migrate from Selenium to Playwright: map driver.get and find_element to page.goto and locators, drop the WebDriver server that flips navigator.webdriver."
parent: "Comparisons"
nav_order: 27
---


# Migrating from Selenium to Playwright for stealth

If you are moving off Selenium because your scrape keeps getting the wrong page, it
helps to know what the move actually changes and what it does not. The short version:
switching frameworks removes one specific class of automation signal, the kind that
comes from the driver process itself. It does not touch your address, your request
rate, or the way you behave once the page loads.

This is a practical port. It maps the Selenium calls you already have to their
Playwright equivalents, explains the one architectural difference that matters for
detection, and shows where a patched browser fits on top. It answers the question in
the title honestly: the migration helps with the driver layer, and you still supply
the rest.

## What actually differs between the two frameworks

The visible difference is the API. The difference that matters for detection is the
process model underneath it.

Selenium drives a browser through a separate WebDriver server: `geckodriver` for
Firefox, `chromedriver` for Chrome. Your Python talks to that server over HTTP, and
the server talks to the browser over the WebDriver protocol. That extra process is
the whole point of Selenium's portability, and it is also the thing that announces
itself to the page.

Playwright has no external driver. It speaks its own protocol to the browser
directly, over a pipe, with nothing standing between your code and the engine. There
is no second server to start, to keep in version-sync, or to leave a marker in the
page. That is the durable architectural difference, and it is the same one covered in
[selenium-driverless vs invisible_playwright](vs-selenium-driverless.md) from the
other direction: driverless tools exist precisely because the driver is the leak.

## The driver process, and the signal it leaves behind

Here is the concrete mechanism, because "the driver leaks" is worth making exact.

When a page runs `navigator.webdriver`, a browser being driven through the WebDriver
protocol returns `true`. On Chrome, `chromedriver` additionally raises an
"automation-controlled" flag that surfaces in the browser's own UI hints and in
several derived signals. A one-line check reads either of these, and it is the first
thing any detector tries because it is free and definitive. Selenium sets both by
design: the protocol that makes it work is the protocol that sets the bit.

Bolt-on patches for this exist and age badly. The classic Selenium-side fix injects
JavaScript to redefine `navigator.webdriver`, which is exactly the overcorrection
worth avoiding: a genuine browser reports `undefined`, not `false`, so writing
`false` swaps one tell for another. The most cited helper for this pattern
[has not shipped a release in years](selenium-stealth-unmaintained.md), which is its
own risk on a moving target. The background on why the property behaves this way,
and why `false` is not the clean value, is in
[what navigator.webdriver really tells a site](navigator-webdriver-explained.md).

Playwright removes the source of that particular signal rather than papering over it:
no driver protocol, no bit to flip. invisible_playwright goes one step further and
pairs stock Playwright with a Firefox patched at the C++ level, so the browser
reports `navigator.webdriver=false` at the binary level, the way an ordinary Firefox
does, instead of through an injected script that a descriptor walk can catch. The
driver-layer tells are gone because the driver is gone and the engine tells the truth
about itself.

## Mapping Selenium calls to Playwright

Most Selenium scripts are a thin loop over four verbs: open a URL, find an element,
interact with it, read something back. Each maps cleanly.

| Selenium | Playwright | Note |
|---|---|---|
| `driver.get(url)` | `page.goto(url)` | `goto` waits for load by default |
| `driver.find_element(By.CSS_SELECTOR, s)` | `page.locator(s)` | locator is lazy, resolved on use |
| `element.click()` | `page.locator(s).click()` | auto-waits for actionability |
| `element.send_keys(text)` | `page.locator(s).fill(text)` | `fill` clears first; `type` for keystrokes |
| `element.text` | `page.locator(s).inner_text()` | |
| `driver.page_source` | `page.content()` | |
| `WebDriverWait(...).until(...)` | built into every action | explicit waits mostly disappear |
| `driver.quit()` | context manager exit | closes on block exit |

The Selenium version most people are porting:

```python
from selenium import webdriver
from selenium.webdriver.common.by import By

driver = webdriver.Firefox()
try:
    driver.get("https://example.com/login")
    driver.find_element(By.CSS_SELECTOR, "#user").send_keys("alice")
    driver.find_element(By.CSS_SELECTOR, "#pass").send_keys("secret")
    driver.find_element(By.CSS_SELECTOR, "#submit").click()
    print(driver.find_element(By.CSS_SELECTOR, ".result").text)
finally:
    driver.quit()
```

The direct Playwright port, no stealth layer yet:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.firefox.launch()
    page = browser.new_page()
    page.goto("https://example.com/login")
    page.locator("#user").fill("alice")
    page.locator("#pass").fill("secret")
    page.locator("#submit").click()
    print(page.locator(".result").inner_text())
```

Two things shrank. There is no driver object to start and quit, and the explicit
waits are gone because every Playwright action waits for the element to be actionable
on its own. The `WebDriverWait` boilerplate that fills a Selenium codebase mostly
just deletes.

## Adding the stealth layer with invisible_playwright

The port above removes the driver process, which removes the driver-layer signal. It
does not give you a realistic fingerprint: stock Playwright Firefox still looks like a
clean automated browser on the machine surfaces (GPU, fonts, audio, screen). To add
that, the launch is a two-line change from the port you just wrote, and nothing after
it changes:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/login")
    page.locator("#user").fill("alice")
    page.locator("#pass").fill("secret")
    page.locator("#submit").click()
    print(page.locator(".result").inner_text())
```

The object `InvisiblePlaywright` returns is a real Playwright `Browser`. Every method
you used above is the upstream method, documented in the upstream docs, so the body of
the script is identical to plain Playwright. The `seed=42` makes the whole identity
reproducible: same seed, same GPU, canvas hash, audio context, fonts and screen, run
after run, which is what turns a flaky failure into one you can replay. Omit the seed
and each session gets a distinct identity instead.

An async codebase changes only the import and the `await` keywords:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com/login")
    await page.locator("#user").fill("alice")
```

For real work you pass a proxy the same way you would in Playwright, and the browser
derives its timezone from the exit address so the two stories agree; the schemes and
the DNS handling are in [Configuration](configuration.md). Whether you run this
patched engine alone or layer a page-level script on top is a real decision with a
wrong answer (two spoofers contradict each other), covered in
[the stealth levels page](playwright-stealth-levels.md).

## What the switch does not fix

The switch does nothing for your IP reputation, request rate, session history, or
behavioral timing - it only closes the driver-layer signal. This is the honest half,
and skipping it is how people migrate and stay blocked.

Dropping the WebDriver server removes the automation signals that live in the driver
layer: `navigator.webdriver`, the automation-controlled flag, the leftover globals a
driver injects. A patched engine adds a realistic fingerprint and a genuine TLS
handshake on top. That is a real and useful chunk of what detectors look at, and it is
why a well-configured session reads as an ordinary Firefox across the fingerprint, the
TLS layer and the driver layer.

It is not the whole of what they look at. The migration does nothing about:

- **IP reputation.** A datacenter address, or a proxy IP already on a list, fails on
  the network before any browser property is read. A perfect browser on a known IP
  still loses, so you supply a clean exit.
- **Request rate and per-account quotas.** Hitting one endpoint faster than a person
  could is a velocity signal no fingerprint hides. You supply human pacing.
- **Session history.** A brand-new session with no cookies and no prior visits looks
  new, whatever the browser reports.
- **Behaviour and timing.** Pointer motion, typing rhythm, and the shape of your
  pauses are watched by sites that fingerprint nothing at all.

The rule to carry out of this page: the framework switch removes the driver-layer
tells, not your address, your rate, or your history, which are the usual reasons a
scrape gets blocked in the first place. Fix those with the tools that fix those, and
let the browser handle the browser.

## Conclusion

Porting Selenium to Playwright is a mechanical job with a real payoff: the four verbs
map one-to-one, the explicit waits mostly delete, and the separate WebDriver server
that flipped `navigator.webdriver` to `true` goes away entirely. Adding
invisible_playwright on top is a two-line change to the launch and gives you a
reproducible, realistic Firefox that reports the driver bit as `false` at the binary
level.

What it buys you is that a session reads as a real browser driven by a real person
across the fingerprint, TLS and driver layers, which is most of the detection surface
and the reason it passes most checks. What it does not buy you is a clean IP, a
sensible request rate, or a plausible history. Supply those yourself and the migration
does its job; skip them and you will have ported your code and kept your block.

## Short answers to the questions that lead here

**Will switching from Selenium to Playwright stop me getting detected?** It removes
the driver-layer tells, which is a real class of signal, and with a patched engine it
adds a realistic fingerprint and TLS handshake. It does not fix your IP, your rate, or
your behaviour, so it helps with some blocks and not others.

**Why does Selenium set navigator.webdriver to true?** Because it drives the browser
through the WebDriver protocol via a separate server (`geckodriver` or
`chromedriver`), and that protocol sets the bit by design. Playwright uses its own
protocol with no external driver, so there is no bit to set.

**Can I just patch navigator.webdriver in Selenium instead?** You can, and it ages
badly. A real browser reports `undefined`, not `false`, so an injected `false` is its
own tell, and the popular helper for this has not shipped in years.

**How much of my Selenium code has to change?** The launch and teardown, and the
find/interact calls map one-to-one. Most `WebDriverWait` boilerplate deletes because
Playwright actions auto-wait.

**Is invisible_playwright a different API I have to learn?** No. It returns a real
Playwright `Browser`, so every method is the upstream method. Only the launch line
differs from stock Playwright.

**I ported everything and still get blocked. Why?** Most likely the part the migration
does not touch: the exit IP, the request rate, or the behaviour. Work those in the
order a [detection checklist](playwright-detected-as-bot.md) suggests.

## Sources

- The Selenium WebDriver protocol and its `geckodriver` / `chromedriver` server model,
  and Playwright's driverless protocol, each read from their own project documentation
  rather than from a summary.
- This project's own fingerprint and TLS gates, which is where the claim that the
  driver layer, fingerprint and handshake read as a genuine Firefox is measured, and
  where the limits of that (IP, rate, behaviour) are stated plainly.

**See also:** [selenium-driverless vs invisible_playwright](vs-selenium-driverless.md)
for the same driver argument from the driverless side,
[what navigator.webdriver really tells a site](navigator-webdriver-explained.md) for
the mechanism, and [the one-site detection checklist](playwright-detected-as-bot.md)
for what to check once the framework is not the problem.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The migration removes
the driver, not the address; I have shipped both mistakes.*
