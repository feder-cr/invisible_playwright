---
title: "crawl4ai stealth mode and custom browser engines"
description: "crawl4ai accepts browser_type firefox but has no executable_path, so you get Playwright's managed build. Where the adapter seam is and what stealth misses."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 3
---


# crawl4ai stealth mode and custom browser engines

crawl4ai cannot launch a custom (patched) browser binary today, because its
`BrowserConfig` has no `executable_path` field, so `browser_type="firefox"` always gets
Playwright's own managed Firefox build. crawl4ai ships two anti-detection features and a
browser abstraction, which makes it look like a patched engine should drop straight in.
It does not, and the reason is that single missing field rather than an architectural one.

This page is what its configuration actually exposes, what its stealth mode reaches,
where the adapter seam is, the workaround that works today, and the part that no
configuration changes.

## What BrowserConfig exposes, and the field that is missing

`BrowserConfig` exposes the browser choice, headless mode, a user data directory, a proxy
and extra arguments, but not the path to a binary. Read from
`crawl4ai/async_configs.py`:

| Field | Default |
|---|---|
| `browser_type` | `"chromium"` |
| `chrome_channel` / `channel` | `"chromium"` |
| `headless` | `True` |
| `user_data_dir` | `None` |
| `proxy_config` | `None` |
| `extra_args` | `None` |

`browser_type` genuinely accepts `"firefox"`, and Firefox is referenced throughout the
project, so this is not a Chromium-only tool at the configuration level.

What is **not** there is `executable_path`. There is no field that says which binary to
launch, so `browser_type="firefox"` gets you Playwright's own managed Firefox build and
nothing else. A patched engine has no supported way in.

`extra_args` does not close the gap either: it passes command-line arguments to a browser
that has already been chosen, not the choice of browser.

## What crawl4ai's stealth mode actually reaches

crawl4ai's stealth mode reaches only page-level JavaScript, and on Firefox it is largely
inert because its evasion modules target Chromium APIs. There are two separate features
here, and it is worth knowing which is which.

**Stealth mode** applies `playwright-stealth`, which is the page-level approach: scripts
injected into the document that redefine properties before the page's own code runs.
crawl4ai's own documentation is straight about the limitation, and it is the same one
that library states itself: **the evasion modules target Chromium APIs and do not support
Firefox or WebKit.**

So on `browser_type="firefox"` the stealth layer is largely inert, which is a genuinely
important thing to know before choosing that combination.

**The undetected browser adapter** is the heavier option, aimed at sites that look at
more than `navigator.webdriver`.

Both operate above the browser.
[The three levels page](playwright-stealth-levels.md) explains what that layer can and
cannot reach; the short version is that everything the machine reports about itself is
untouched by either.

## Where the seam actually is

The extension point exists and it is not `BrowserConfig`. It is on the crawler strategy:

```python
undetected_adapter = UndetectedAdapter()
crawler_strategy = AsyncPlaywrightCrawlerStrategy(
    browser_config=browser_config,
    browser_adapter=undetected_adapter,
)
```

`browser_adapter` takes an adapter object, and the project ships `PlaywrightAdapter` and
`UndetectedAdapter`. The documentation describes the pattern as allowing switching
between browser implementations, and then gives no guidance on writing your own.

That is where a different engine would belong: an adapter that owns the launch and hands
back a browser, which is exactly the shape
[scrapy-playwright's browser provider takes](integrations/scrapy-playwright.md) and the
reason that integration is clean. Here the seam is present but undocumented, so anything
built on it is building against a private interface.

**The one-field change that would fix it properly** is `executable_path` on
`BrowserConfig`, passed through to `launch()`. That is a small, well-scoped upstream
change, and until it exists there is no supported route.

## The workaround that works today, with its cost

Playwright resolves its managed browsers from a directory, and that directory is
configurable through [`PLAYWRIGHT_BROWSERS_PATH`](https://playwright.dev/docs/browsers#managing-browser-binaries).
Put a build where Playwright expects to find one, and `p.firefox.launch()` uses it, with
no crawl4ai change at all.

Be clear-eyed about what that is:

- It depends on Playwright's internal directory layout, which is version-stamped and not
  a public contract.
- It applies process-wide, so everything else in that process gets the substituted
  browser too.
- It will break on a Playwright upgrade, silently, by falling back to the managed build.

It works, and it is a workaround rather than an integration. If you use it, pin your
Playwright version and check the engine at runtime rather than assuming.

```python
# after launch, confirm you got the engine you meant to get
ua = await page.evaluate("() => navigator.userAgent")
```

## What no configuration changes

Everything the machine reports, which on a server is a consistent set of answers all
saying the same thing:

- **No GPU**, so WebGL reports a software renderer.
  [What those strings mean](webgl-renderer-strings.md).
- **A container font set** under whatever platform you claim.
  [Why more fonts is not the fix](headless-fonts-differ.md).
- **No audio device**, and no speech voices.
  [The audio values](audiocontext-fingerprinting.md) and
  [the voice list](speech-synthesis-voices.md).
- **A screen with no taskbar.**
  [The relationships that have to hold](screen-size-headless-tells.md).
- **The TLS handshake**, decided before any page-level layer exists.
  [Which nothing in JavaScript can reach](ja3-ja4-tls-fingerprint.md).

If crawl4ai works on your laptop and fails in a container, this list is where to look
first, and no stealth setting in the library addresses any of it.
[The container walkthrough](playwright-docker-detection.md) goes through it in order.

## Conclusion

crawl4ai is closer to supporting a patched engine than most tools its size: it already
abstracts the browser behind an adapter and already accepts Firefox as a browser type.
What it lacks is the one field that says which binary to run.

Until that exists, the honest options are a private adapter, a process-wide directory
substitution with the caveats above, or accepting the managed build and spending the
effort on the machine instead, which is where most container-based failures actually
originate.

## Short answers to the questions that lead here

**Does crawl4ai support a custom browser binary?** Not through `BrowserConfig`. There is
no `executable_path` field.

**Can crawl4ai use Firefox?** Yes, `browser_type="firefox"`, with Playwright's own
managed build.

**Does crawl4ai's stealth mode work with Firefox?** Largely not. It applies
`playwright-stealth`, whose evasion modules target Chromium APIs.

**What is the undetected browser adapter?** A heavier built-in adapter aimed at stronger
detection, passed as `browser_adapter` to the crawler strategy. It still operates above
the browser.

**Why does crawl4ai work locally and fail in Docker?** Because the container answers
differently about the machine, and none of the library's settings change that.

**Is there any way to use a patched engine today?** A process-wide
`PLAYWRIGHT_BROWSERS_PATH` substitution, with the fragility described above, or a custom
adapter written against an undocumented interface.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
for what the page level can reach,
[scrapy-playwright](integrations/scrapy-playwright.md), for what a documented provider
seam looks like, and
[Playwright in Docker](playwright-docker-detection.md).

## Sources

- `crawl4ai/async_configs.py`, for the `BrowserConfig` fields listed above, read
  2026-07-27.
- crawl4ai's own documentation for stealth mode and the undetected browser adapter,
  including its statement that the underlying evasion modules do not support Firefox.
- Playwright's own [browser-management
  documentation](https://playwright.dev/docs/browsers#managing-browser-binaries), for
  the `PLAYWRIGHT_BROWSERS_PATH` behaviour the workaround depends on.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. We would rather document the missing field than
publish an integration guide for a seam that is not there.*
