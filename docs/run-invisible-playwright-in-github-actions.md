---
title: "Run invisible_playwright in GitHub Actions CI"
description: "Run invisible_playwright on GitHub Actions: a working headless CI recipe caching the engine, and why a datacenter runner IP, not the fingerprint, is the limit."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 83
---


# Run invisible_playwright in GitHub Actions CI

The integration is undramatic, which is the good news. On a hosted runner the engine
downloads itself on first use, `headless=True` behaves, and stock Playwright code runs
unchanged. You can have a green job in ten minutes.

The honest part of this page is the part nobody puts in the README: the browser is not
the limiting factor on CI, the runner's address is. A real-browser fingerprint does not
change the network it comes from, and a hosted runner sits in a datacenter range that is
scored before a single fingerprint field is read. This page gives you the working recipe
and then spends most of its length on the thing the working recipe does not fix.

## What works out of the box, and what does not

What works: the fingerprint, the TLS handshake and the driver layer read as a genuine
Firefox, because it is one. Those are the checks invisible_playwright is built to pass,
and they pass the same on a runner as on a laptop, because they are properties of the
browser rather than the host.

What it does not touch, on CI or anywhere else: IP reputation, per-account quotas, rate
limits, and behaviour or timing. On a GitHub-hosted runner the first of those is the one
that bites, because the runner IP is shared datacenter space with a reputation you did
not build and cannot improve. The request is scored on that address before any script
reads a fingerprint. A perfect browser on a flagged IP is still on a flagged IP, and
[whether a site can tell a datacenter proxy from a residential one](can-websites-detect-a-datacenter-proxy-ip.md)
is the same question applied to the runner itself.

So the recipe below is genuinely useful and genuinely incomplete. It gets the browser
running correctly. Making the session survive is a second step, and it is a proxy, not a
browser flag.

## The minimal working recipe

Install the package, run a headless script. The engine is fetched and cached on first
use, so the first job is slower than the rest.

```yaml
name: scrape
on:
  workflow_dispatch:
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install invisible-playwright
      - name: Run
        run: python scrape.py
```

The script itself is the same two-line switch you would use locally, with
`headless=True` because a runner has no display:

```python
# scrape.py
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
    page.screenshot(path="out.png")
```

The `browser` here is a real
[`playwright.sync_api.Browser`](https://playwright.dev/python/docs/api/class-browser),
so every Playwright method works exactly as documented upstream. Passing `seed=42`
fixes the identity: the same seed produces the same GPU, canvas hash, audio context
and screen on every run, which is what makes a CI failure reproducible instead of a
new random machine each time. Log `InvisiblePlaywright().seed` if you let it pick
one for you.

## Cache the engine so every job does not re-download it

The engine is a large asset. Left alone, each job downloads it again, which is slow and
occasionally rate-limited. Point the cache at a directory GitHub Actions preserves
between runs, and only the first run pays the download cost. The size per engine version
is on the [installation](installation.md) page.

```yaml
      - name: Cache the engine
        uses: actions/cache@v4
        with:
          path: ~/.cache/invisible-playwright
          key: invpw-engine-${{ runner.os }}

      - name: Run
        env:
          INVISIBLE_PLAYWRIGHT_CACHE_DIR: ~/.cache/invisible-playwright
        run: python scrape.py
```

`INVISIBLE_PLAYWRIGHT_CACHE_DIR` moves the cached engine to a path you control, and here
that path is one the cache action restores. On a corporate or rate-limited runner that
refuses anonymous GitHub downloads, `STEALTHFOX_GITHUB_TOKEN` lets the fetch authenticate;
both variables are documented in [configuration](configuration.md). If you already ship a
binary in the image, `INVPW_BINARY_PATH` skips the download entirely.

## The real limiting factor: the runner's IP

The runner's IP, not the browser, is what gets scored: run the job above with no
proxy and read the exit address from inside the browser, the same way you would
[check for a proxy IP leak](how-to-check-proxy-ip-leak.md), and it comes back as a
hosting provider's range. That is not a bug in the browser; it is the runner, and
this is the measurement that reframes the whole exercise.

The order in which a site decides matters. The connection and its TLS handshake arrive
first, and the source address is attached to both. A scoring system can raise a
challenge or serve a different page on the address alone, before any in-page fingerprint
script has run. So the fingerprint work invisible_playwright does, which is real and
which passes the public suites, happens after the point where a datacenter IP has
already lowered the score.

This is why a run that is flawless on your laptop gets challenged on CI with the identical
code. Your laptop is on a residential connection with an unremarkable reputation. The
runner is not. Nothing about the browser changed between the two, and nothing about the
browser can change the thing that did.

## Route the CI job through a residential proxy

The fix is to give the job a network that is not datacenter space. invisible_playwright
takes a proxy dict directly, so the change is a few lines and a couple of secrets, not a
new tool:

```python
# scrape.py
from invisible_playwright import InvisiblePlaywright
import os

proxy = {
    "server": os.environ["PROXY_SERVER"],
    "username": os.environ["PROXY_USER"],
    "password": os.environ["PROXY_PASS"],
}

with InvisiblePlaywright(seed=42, headless=True, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

Feed the credentials in as secrets rather than committing them:

```yaml
      - name: Run
        env:
          PROXY_SERVER: ${{ secrets.PROXY_SERVER }}
          PROXY_USER: ${{ secrets.PROXY_USER }}
          PROXY_PASS: ${{ secrets.PROXY_PASS }}
          INVISIBLE_PLAYWRIGHT_CACHE_DIR: ~/.cache/invisible-playwright
        run: python scrape.py
```

Two things worth knowing before you buy anything. First, most cheap proxy pools are
already public, so their addresses are on the same block lists as the runner, and you
have paid to move from one flagged IP to another; the clean fraction is the residential
IPs that are not already known. Second, once a proxy exits somewhere real, the browser
timezone and locale have to agree with that exit, or you have traded an IP problem for a
consistency problem, which is [why setting timezone_id and still getting flagged](timezone-proxy-mismatch.md)
has its own page. By default invisible_playwright derives the timezone from the egress
IP, so that part is handled unless you override it. If a single job hits many pages,
[rotating the exit across requests](how-to-rotate-proxies-playwright.md) spreads the
velocity that a single address would otherwise concentrate.

Even with all of that, the proxy fixes the network and only the network. Per-account
quotas, rate limits and interaction timing are still yours to supply: pace the job like a
person rather than a loop, and do not run a hundred requests a minute from one identity.

## Conclusion

invisible_playwright runs cleanly in GitHub Actions: install it, cache the engine, run
headless, and the browser presents as a genuine Firefox. That is real and it is most of
what fingerprint-based checks look at. The lesson the recipe teaches, though, is that the
hosted runner's datacenter IP is scored before the fingerprint is read, so the browser
being real does not make the network real. Pair the job with a residential proxy and give
it human pacing, and the parts invisible_playwright cannot see are covered by the parts
you supply. Skip that, and no amount of fingerprint fidelity changes the address the
request came from.

## Short answers to the questions that lead here

**Does invisible_playwright work in GitHub Actions?** Yes. The engine downloads and
caches on the runner, `headless=True` works, and stock Playwright code runs unchanged.

**Then why does my CI run get challenged when my laptop does not?** The runner sits in a
datacenter IP range with a poor reputation, and the address is scored before any
fingerprint is read. Your laptop is on a residential connection; the browser is identical
on both.

**Can a better fingerprint fix the datacenter IP?** No. The fingerprint is a property of
the browser, the IP is a property of the network, and the network is scored first. Route
the job through a residential proxy instead.

**How do I stop re-downloading the engine every job?** Cache the directory named by
`INVISIBLE_PLAYWRIGHT_CACHE_DIR` with actions/cache. Only the first run pays the download.

**Is a proxy enough on its own?** It fixes the network and nothing else. Per-account
quotas, rate limits and behaviour or timing are still yours to handle, and most cheap
proxy pools are already blocked anyway.

**Do I need a display or xvfb?** No. Run `headless=True`; a hosted runner has no display
and none is needed.

## Sources

- This project's own CI runs and the documented environment variables
  (`INVISIBLE_PLAYWRIGHT_CACHE_DIR`, `STEALTHFOX_GITHUB_TOKEN`, `INVPW_BINARY_PATH`) on
  the [configuration](configuration.md) page.
- The exit-address reading taken from inside a headless runner job, compared against the
  same script run from a residential connection.
- Playwright's own [`Browser` API reference](https://playwright.dev/python/docs/api/class-browser)
  for the methods and properties that `browser` exposes once launched.

**See also:** [how to check for a proxy IP leak](how-to-check-proxy-ip-leak.md),
[rotating proxies across requests](how-to-rotate-proxies-playwright.md), and
[whether sites can detect a datacenter proxy IP](can-websites-detect-a-datacenter-proxy-ip.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It makes the browser look
like a real person's, which is most of the battle and not all of it: on CI the runner's
IP is the part you still have to fix yourself.*
