---
title: "Puppeteer scraping examples, and what happens when you run them against a real defense"
description: "Six standard Puppeteer patterns - basic extraction, waiting for content, pagination, screenshots, form submission, intercepting requests - shown as code, then run against an actual detector to show what changes and what does not."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 170
---

# Puppeteer scraping examples, and what happens when you run them against a real defense

Every Puppeteer tutorial shows the same six patterns. They are worth having in
one place, and worth being honest about: none of them, run as written, holds
up against a site that is actively checking for automation. This page shows
the patterns, then shows what a real check does to them.

## The six standard patterns

**Basic extraction.**

```js
const browser = await puppeteer.launch();
const page = await browser.newPage();
await page.goto('https://example.com');
const title = await page.$eval('h1', el => el.textContent);
await browser.close();
```

**Waiting for dynamic content.**

```js
await page.goto('https://example.com/listings');
await page.waitForSelector('.listing-card');
const items = await page.$$eval('.listing-card', cards =>
  cards.map(c => c.querySelector('.title')?.textContent)
);
```

**Pagination.**

```js
let allItems = [];
while (true) {
  const items = await page.$$eval('.item', els => els.map(e => e.textContent));
  allItems = allItems.concat(items);
  const next = await page.$('.next-page:not(.disabled)');
  if (!next) break;
  await next.click();
  await page.waitForNetworkIdle();
}
```

**Screenshots for debugging or archival.**

```js
await page.screenshot({ path: 'page.png', fullPage: true });
```

**Filling and submitting a form.**

```js
await page.type('#search-input', 'query text');
await page.click('#search-button');
await page.waitForNavigation();
```

**Intercepting network requests to read API responses directly.**

```js
page.on('response', async (response) => {
  if (response.url().includes('/api/items')) {
    const data = await response.json();
    console.log(data);
  }
});
await page.goto('https://example.com');
```

## What a real check does to all six

Run any of the above against `bot.sannysoft.com` or a similar public detector,
and the pattern itself is not what fails - the *browser underneath* it is.
Every one of these scripts launches Puppeteer's default bundled Chromium,
which:

- Sets `navigator.webdriver` under the CDP automation layer Puppeteer speaks.
- Ships as a specific, versioned automation build distinguishable from a
  retail Chrome install on request.
- Produces a WebGL/canvas fingerprint tied to whatever GPU (or lack of one) the
  host machine has, unrelated to the code pattern used.

None of the six patterns above caused any of that. The `$eval`, the
`waitForSelector`, the pagination loop - all correct, idiomatic Puppeteer.
The tells live one layer down, in what browser Puppeteer launched and how it
is being driven - the same [CDP-visible](bidi-vs-cdp-detection.md) automation
layer, not in which selector method you called.

## The two fixes, and where each one lives

**Fix the pattern-level mistakes first, because they are real.** A fixed
`waitForTimeout(2000)` instead of `waitForSelector` produces flaky, slow
scrapes that themselves look unusual in aggregate. Skipping
`waitForNetworkIdle()` before reading paginated content produces truncated
results that have nothing to do with detection. These are worth fixing
regardless of any anti-bot concern.

**Fix the browser identity separately, because the code above cannot touch
it.** If the site is specifically checking automation signals, no amount of
selector correctness changes `navigator.webdriver`, the CDP fingerprint, or
the build identity. That is a browser-engine question, not a scraping-pattern
question, and it needs a different browser underneath the same code:
[Patchright](vs-patchright.md) for a patched Chromium driver on
Puppeteer/Playwright's Chrome side, or this project's patched Firefox if you
can move off Chromium entirely.
[Migrating from Puppeteer to Playwright for stealth](migrate-puppeteer-to-playwright-stealth.md)
is the practical path from the code above toward that fix, keeping the same
scraping logic and changing only the engine underneath it.

## Short answers to the questions that lead here

**Why does my working Puppeteer script get blocked on some sites and not
others?** The sites that block it are checking automation-layer signals your
scraping logic does not touch. Sites that do not check simply never notice.

**Is Puppeteer detectable by default?** Yes, in the sense that its default
bundled Chromium reports as an automated build under inspection, the same way
any framework's default browser does.

**Do I need Puppeteer-extra-plugin-stealth?** It patches several of the same
known Chrome-side tells that Patchright addresses at the driver level; check
its current maintenance status before depending on it for anything important,
since stealth plugins of this shape tend to lag behind detector updates.

**Can these examples be adapted to Playwright directly?** Yes - the concepts
(`waitForSelector`, request interception, pagination loops) map closely, and
Playwright's own auto-waiting removes the need for some of the explicit waits
shown above.

**Will intercepting API responses avoid detection better than reading the
DOM?** It avoids some rendering-related overhead and can be faster, but it is
still the same browser and the same automation layer making the request - it
does not change what the browser reports about itself.

**See also:**
[Migrating from Puppeteer to Playwright for stealth](migrate-puppeteer-to-playwright-stealth.md),
[puppeteer-real-browser vs invisible_playwright](vs-puppeteer-real-browser.md),
and [how to capture XHR/API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md)
for the request-interception pattern done in Playwright's API.

## Sources

- [Puppeteer's own API documentation](https://pptr.dev/), retrieved 2026-09-10, for the methods used in the examples above.
- This project's own detector gates, for the claim that browser-layer signals, not scraping-pattern code, are what a real check reads first.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The code on this page is ordinary Puppeteer, shown honestly next to what it
does and does not survive, rather than presented as a stealth technique it
never was.*
