---
title: "Quickstart"
description: "Two-line switch from Playwright with zero API changes to learn. Sessions get distinct fingerprints by default; pass a seed for reproducible ones instead."
parent: "Documentation"
nav_order: 2
---


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

The synchronous API, for scripts that already call `sync_playwright()` directly:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(proxy={"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

## Async

The same shape under `asyncio`, for code that already calls `async_playwright()`:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(proxy={"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}) as browser:
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

## Short answers to the questions that lead here

**Do I need to change my Playwright code to use invisible_playwright?** No, beyond
the two-line switch above. Every method - sync and async - is the same one you
already call on `playwright.sync_api.Browser` / `playwright.async_api.Browser`;
there is no wrapped subset of the API to learn.

**Does every session get a different fingerprint automatically?** Yes. GPU, audio,
fonts, screen and the rest of the roughly 400 fields are generated fresh per session
with no configuration required, and each gets Bezier-curve mouse motion by default too.

**How do I make a fingerprint reproducible across runs instead of random?** Pass
`seed=` explicitly, or log `sf.seed` from a seedless run and reuse that value next
time. Either way, every field the seed implies comes back identical.

**Is the `browser` object a real Playwright object, or a wrapper around one?** It is
the real thing - `playwright.sync_api.Browser` or `playwright.async_api.Browser` -
so anything documented upstream for that object works unchanged here.

**Where do I set a proxy, force the timezone, or fix one fingerprint field while
leaving the rest random?** [Configuration](configuration.md) covers proxies,
timezone and environment variables; [Pinning fingerprint fields](pinning.md) covers
forcing individual values like a GPU model or screen size.

**See also:** [Installation](installation.md) for downloading the patched Firefox
binary this class drives, [Configuration](configuration.md) for proxy and timezone
options, [Pinning fingerprint fields](pinning.md) for forcing specific values while
the rest stays seed-derived, and
[giving an agent a reproducible browser identity](reproducible-agent-browser-identity-seed.md)
for the full seed-reuse pattern this page introduces.

## Next

[Configuration](configuration.md) covers proxies, timezone and environment
variables. [Pinning fingerprint fields](pinning.md) covers forcing specific values -
a GPU model, a screen size - while leaving the rest seed-derived.
