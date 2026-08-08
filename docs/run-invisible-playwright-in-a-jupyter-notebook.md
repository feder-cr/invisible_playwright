---
title: "Run invisible_playwright in a Jupyter notebook"
description: "Why the sync Playwright API raises 'cannot be run from an existing event loop' in a Jupyter kernel, and the await-in-a-cell fix for developing a stealth session interactively."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 91
---


# Run invisible_playwright in a Jupyter notebook

Run invisible_playwright in a notebook by importing from
`invisible_playwright.async_api` instead of the top-level package and awaiting each
call in a cell - the ordinary synchronous API raises because the Jupyter kernel
already runs its own asyncio event loop, and it refuses to start a second one inside
that. That one swap is the only thing that trips people up on the first try, and it is
not specific to this library.

A notebook is otherwise one of the better places to develop a browser session, because
you can launch once, then poke at the live page cell by cell and read a screenshot
after each step instead of rerunning a whole script. This page shows the error, the
fix, and how to keep a stealth session alive across cells so you can inspect it as you
build. It also answers the question honestly at the end: the notebook is a development
convenience and changes nothing about how detectable the session is.

## Why the sync API fails in a notebook

Here is why: the synchronous API tries to start its own event loop, and Jupyter's
kernel is already running one. Drop the ordinary two-line launch into a cell and it
raises immediately:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:   # raises in Jupyter
    page = browser.new_page()
    page.goto("https://example.com")
```

```
Error: It looks like you are using Playwright Sync API inside the asyncio loop.
Please use the Async API instead.
```

The synchronous Playwright API drives the browser by running its own event loop under
the hood. A Jupyter kernel already has one running to service the cell, and you cannot
start a second loop inside the first. This is the same reason the sync API fails inside
any `async def`, and it is why `asyncio.run(...)` in a cell gives you
`asyncio.run() cannot be called from a running event loop`.

You do not need `nest_asyncio`, and you do not need to spawn a thread. The loop you
need is already there. Use the async API and `await` it directly.

## The fix: the async API, awaited in a cell

Import from `invisible_playwright.async_api` and `await` the operations. Jupyter
supports top-level `await` and top-level `async with`, so a single cell reads almost
exactly like the sync version:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    print(await page.title())
```

The `browser` object is a real `playwright.async_api.Browser`. Playwright documents
[both a sync and an async Python API](https://playwright.dev/python/docs/library) as
first-class, and recommends the async one for any project that already runs an
asyncio event loop - which is exactly the notebook's situation. Every method on the
async side is the same one documented upstream, just with `await` in front of it.
There is no wrapped subset to learn, and nothing about the API changes because you are
in a notebook rather than a script.

That single cell opens the browser, does its work, and closes it when the `async with`
block ends. For interactive development you usually want the opposite: a browser that
stays open while you type the next cell.

## Keeping the session alive across cells

To inspect a page step by step, enter the context manually in one cell and hold the
handle. The async context-manager protocol is public, so `__aenter__` and `__aexit__`
are fair game:

```python
# Cell 1 - launch and keep the handle
from invisible_playwright.async_api import InvisiblePlaywright

sf = InvisiblePlaywright(seed=42)
browser = await sf.__aenter__()
page = await browser.new_page()
await page.goto("https://example.com")
```

```python
# Cell 2 - the browser is still open; interact and read back
await page.click("#submit")          # mouse arcs to the button on a Bezier curve
print(await page.inner_text("body"))
```

```python
# Cell 3 - screenshot the current DOM into the notebook
from IPython.display import Image
await page.screenshot(path="step.png")
Image("step.png")
```

```python
# Last cell - close it when you are done
await sf.__aexit__(None, None, None)
```

Rendering the screenshot inline after each step is the reason a notebook is worth the
setup: you see what the page actually painted, not only what your extractor pulled out,
and the two are not always the same. For a page taller than the viewport, the
[full-page screenshot notes](how-to-take-full-page-screenshots-playwright.md) cover
`full_page=True` and the layout traps that come with it; if a shot comes back as
[speckled noise rather than the page](playwright-screenshot-returns-noise.md), that
page explains why.

Because the launch above passes `seed=42`, the GPU, canvas hash, audio context, fonts
and screen are identical on every rerun of the notebook. That is what makes a notebook
a place to debug rather than guess: a failing step replays exactly instead of arriving
with a fresh random machine each time. [Configuration](configuration.md) covers the
proxy, timezone and environment variables you would add for a real exit.

## What the notebook does not change about detection

This is the honest part, and it matters. Running in Jupyter is a development
convenience and nothing more. The session is the same patched Firefox driven by stock
Playwright whether it launches from a cell or a cron job, so detection is identical to
any other host.

invisible_playwright is built to look like a real Firefox driven by a real person,
which is why it passes most in-page checks: the fingerprint, the TLS handshake and the
driver layer read as a genuine browser rather than an automated one. On its own that
does not fix the things a browser cannot control:

- **IP reputation.** A consistent browser on a known datacenter address still loses.
  The notebook does nothing here; a clean exit is yours to supply.
- **Rate limits and per-account quotas.** These are counted server-side and no browser
  property hides them.
- **Behaviour and timing.** Clicking the instant a cell runs, with no pause between
  actions, is a pattern. Interactive development can make this worse, because you tend
  to fire cells back to back. Human pacing is on you.

So the accurate claim is narrow: a notebook helps you develop and inspect a session
that looks real, and it helps with the browser layer. It does not make a session
undetectable, and no honest tool would say it does. The
[detection checklist](playwright-detected-as-bot.md) is the order to work through when a
session is flagged, and most of that order is not about the browser at all.

## Conclusion

The only Jupyter-specific hurdle is the running event loop, and the fix is to use the
async API and `await` it in a cell - no `asyncio.run`, no `nest_asyncio`, no worker
thread, because the loop the sync API was trying to create already exists. From there,
hold the browser handle across cells and screenshot the DOM as you go, and you have an
interactive workbench for a seed-reproducible stealth session. What that session looks
like to a detector is unchanged from any other host, so pair it with a clean exit and
human pacing exactly as you would in production.

## Short answers to the questions that lead here

**Why do I get "cannot be run from an existing event loop" in Jupyter?** Because the
kernel already runs an asyncio loop and the sync Playwright API needs to start its own,
which is not allowed inside a running one. Use the async API instead.

**Do I need nest_asyncio to run Playwright in a notebook?** No. `nest_asyncio` patches
the loop to allow re-entry, but you do not need it here: switch to the async API and
`await` the calls directly, since the loop is already there.

**How do I keep the browser open between cells?** Enter the context manually with
`browser = await sf.__aenter__()` in one cell, use `page` in later cells, and call
`await sf.__aexit__(None, None, None)` when you are done, instead of wrapping everything
in one `async with`.

**Can I show a screenshot inline in the notebook?** Yes. `await page.screenshot(path=...)`
then display the file with `IPython.display.Image`. Reading the pixels beats trusting
the log.

**Is a session run from a notebook easier to detect?** No. Detection is identical to any
other host; the browser is the same patched Firefox. The notebook is a development
convenience, not a stealth feature.

**Does this make my scraping undetectable?** No. It makes the browser layer look real,
which passes most in-page checks. IP reputation, rate limits and timing are separate and
still yours to handle.

## Sources

- This project's async and sync APIs as documented in the [quickstart](quickstart.md),
  and the async context-manager protocol used above.
- The upstream Playwright error text raised when the sync API is started inside a
  running asyncio loop, reproduced in a Jupyter kernel.
- Playwright's own documentation on the
  [sync and async Python API](https://playwright.dev/python/docs/library), which
  recommends the async API for any project already running an asyncio event loop.

**See also:** [Configuration](configuration.md) for proxy and timezone setup, the
[detection checklist](playwright-detected-as-bot.md) for when a session is flagged, and
[how to take full-page screenshots](how-to-take-full-page-screenshots-playwright.md) for
capturing more than the viewport.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The event-loop error is
the first thing every notebook user hits, and the fix is one import line.*
