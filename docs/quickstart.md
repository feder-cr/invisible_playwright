---
title: "Quickstart"
description: "Switching from plain Playwright is a two-line change, sync or async, with zero API changes after that. Every session gets a distinct fingerprint by default, or a reproducible one if you pass a seed."
parent: "Documentation"
nav_order: 2
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Documentation",
      "item": "https://feder-cr.github.io/invisible_playwright/documentation.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Quickstart"
    }
  ]
}
</script>

# Quickstart

**100% Playwright-compatible** - sync and async, every method, zero API changes
beyond how the browser is launched. If you already have Playwright code, switching is
two lines:

```diff
- from playwright.sync_api import sync_playwright
- with sync_playwright() as p:
-     browser = p.firefox.launch()
+ from invisible_playwright import InvisiblePlaywright
+ with InvisiblePlaywright() as browser:
```

Every session gets a distinct fingerprint (GPU, audio, fonts, screen, roughly 400
fields) and Bezier-curve mouse motion, with no further configuration required.

## Sync

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(proxy={"server": "socks5://...", "username": "u", "password": "p"}) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

## Async

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(proxy={"server": "socks5://...", "username": "u", "password": "p"}) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    await page.click("#submit")
```

The `browser` object returned is a real `playwright.sync_api.Browser` /
`playwright.async_api.Browser` - every Playwright method works exactly as documented
upstream. There is no wrapped subset of the API to learn.

## Logging the seed to replay a run

Every session is generated from a seed, whether you pass one or not. Log it and you
can reproduce the exact same identity later:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)
    # ...
```

## Reproducible fingerprint

Pass a seed explicitly and every field it implies - GPU, canvas hash, audio context,
fonts, screen - comes back identical, run after run:

```python
with InvisiblePlaywright(seed=42) as browser:
    ...   # same GPU, same canvas hash, same audio context, every run
```

This is the difference between debugging a failure and guessing at one: same seed,
same browser, so a failing run can be replayed exactly rather than hoping the next
random draw reproduces it.

## Next

[Configuration](configuration.md) covers proxies, timezone and environment
variables. [Pinning fingerprint fields](pinning.md) covers forcing specific values -
a GPU model, a screen size - while leaving the rest seed-derived.
