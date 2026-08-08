---
title: "Wrap invisible_playwright in a FastAPI service"
description: "Build async FastAPI render service around invisible_playwright: browser alive by lifespan, semaphore bounds concurrency, cost of routing requests through one IP."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 90
---


# Wrap invisible_playwright in a FastAPI service

A common next step after the first script works is to put it behind an HTTP
endpoint, so other services can ask for a rendered page or a screenshot without
carrying a browser of their own. This page is a real service shape for that: an
async FastAPI app that keeps one browser alive across requests, bounds how many
pages render at once, and returns either the HTML or a PNG.

It also answers the question honestly. A shared render endpoint helps with the
part invisible_playwright is built for - the browser looks like a genuine
Firefox driven by a person, so the fingerprint, TLS and driver layers read as
real. It does nothing for the part you have just made worse: every caller now
leaves from the same address, which is its own detectable pattern. Both halves
are below.

## Why a long-lived browser, not one per request

Launching a browser is the expensive part. It downloads nothing after the first
run, but it still starts a process, reads the profile, and builds the identity
before the first page loads. Doing that on every HTTP request means every caller
pays that cost, and a burst of callers starts a burst of processes that compete
for the same CPU and memory.

The fix is the ordinary one for any expensive resource behind a web server:
create it once when the app starts, hand every request a page from it, and close
it once when the app stops. In FastAPI that lifecycle hook is the lifespan
context. One browser, many pages.

A single browser serving many pages is also the shape that matches how the
identity works. One launched browser is one seeded machine. If you want every
caller to share a stable, reproducible fingerprint, pass a fixed `seed` at
startup and every page inherits it. If you would rather each caller look like a
different machine, that is a different design - a small pool of browsers, or a
launch per identity - and it costs more, so decide it on purpose.

## The minimal render service

Here is the whole thing. It keeps one browser for the process lifetime, hands
each request a fresh page, and returns the rendered HTML.

```python
from contextlib import asynccontextmanager, AsyncExitStack

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from invisible_playwright.async_api import InvisiblePlaywright

state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # started once, when the process comes up
    async with AsyncExitStack() as stack:
        browser = await stack.enter_async_context(InvisiblePlaywright(seed=42))
        state["browser"] = browser
        yield
    # AsyncExitStack closes the browser here, once, on shutdown


app = FastAPI(lifespan=lifespan)


@app.get("/render", response_class=HTMLResponse)
async def render(url: str):
    browser = state["browser"]
    page = await browser.new_page()
    try:
        await page.goto(url, wait_until="networkidle")
        return await page.content()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await page.close()
```

The `browser` object is a real
[`playwright.async_api.Browser`](https://playwright.dev/python/docs/api/class-browser),
so `new_page`, `goto`, `content` and every other method behave exactly as documented upstream.
There is no wrapped subset to learn - the only thing that differs from plain
Playwright is the two-line launch. Run it with any ASGI server, for example
`uvicorn app:app`, and `GET /render?url=https://example.com` returns the page as
the browser saw it.

Closing the page in `finally` matters. A page you forget to close is memory the
browser holds until it shuts down, and a service that leaks a page per request
is a service that falls over on the day it gets busy.

## Bound the concurrency with a semaphore

A render service that just launches a page per request has a quiet flaw:
nothing limits how many pages open at once.
Ten callers arriving together open ten pages together, a hundred open a hundred,
and each page is real memory and a real share of one CPU. A single browser does
not protect you from this - it just means all those pages crowd into one
process.

An `asyncio.Semaphore` fixes it in a few lines. It lets a fixed number of
renders proceed and makes the rest wait their turn instead of piling on.

```python
import asyncio
from contextlib import asynccontextmanager, AsyncExitStack

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from invisible_playwright.async_api import InvisiblePlaywright

MAX_CONCURRENT_RENDERS = 4
state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        browser = await stack.enter_async_context(InvisiblePlaywright(seed=42))
        state["browser"] = browser
        state["gate"] = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)
        yield


app = FastAPI(lifespan=lifespan)


@app.get("/render", response_class=HTMLResponse)
async def render(url: str):
    async with state["gate"]:               # waits if 4 renders are already busy
        page = await state["browser"].new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            return await page.content()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        finally:
            await page.close()
```

Pick the limit from the machine, not from a wish. Each concurrent page wants
memory and a slice of CPU, so a small server holds a handful, not fifty. Set it
too high and every render slows down together under load; set it sensibly and
callers queue for a moment instead of the whole box thrashing. If you want to
fail fast rather than queue forever, wrap the acquire in `asyncio.wait_for` and
return a `503` when the wait runs out.

## Returning a screenshot instead of HTML

A render service built the same way returns a PNG with one different call:
swap `page.content()` for `page.screenshot()`. A screenshot endpoint is
useful precisely because a text log tells you what your extractor found, while
the image tells you what the page actually rendered - the distinction that
[reading the screenshot instead of the log](how-to-test-bot-detection.md) is
built on.

```python
from fastapi import Response


@app.get("/shot")
async def shot(url: str, full_page: bool = False):
    async with state["gate"]:
        page = await state["browser"].new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            png = await page.screenshot(full_page=full_page)
            return Response(content=png, media_type="image/png")
        finally:
            await page.close()
```

[`page.screenshot`](https://playwright.dev/python/docs/api/class-page#page-screenshot)
is stock Playwright, so `full_page=True` captures past the
fold exactly as it does everywhere else - the mechanics and the pitfalls of the
tall capture are in
[how to take full-page screenshots](how-to-take-full-page-screenshots-playwright.md).

## The honest cost: one server, one IP

This is the part a service shape makes easy to forget, so it gets its own
section.

The moment you put one browser behind one endpoint on one host, every caller
that hits `/render` leaves the internet from the same address. The fingerprint
is still real and still per-session - each page is a genuine Firefox - but you
have concentrated all of that traffic behind a single IP, and volume from one
address is a signal no browser realism touches. A hundred callers behind your
service look, from the far end, like a hundred requests from one machine, which
is a pattern in its own right regardless of how convincing each request is.

invisible_playwright is designed to look like a real browser driven by a real
person, and that is why it clears most fingerprint, TLS and driver-layer checks.
It does not, on its own, fix IP reputation, per-account quotas, rate limits, or
the timing of your requests. Those are yours to supply. For a render service the
practical shape is:

- Give the service a clean egress, not the datacenter IP the host boots with.
  Route the browser through a proxy per request or per identity - the
  [proxy rotation patterns](how-to-rotate-proxies-playwright.md) page covers
  doing this without relaunching the browser every time, and
  [Configuration](configuration.md) covers the proxy dict and how the timezone
  follows the exit.
- Pace the work. A semaphore bounds concurrency on your side; it does nothing
  about how fast one destination sees requests arrive. Space them.
- Remember what no in-page realism can help with, the machine-and-behaviour
  tells that [the detection checklist](playwright-detected-as-bot.md) works
  through in order. A shared endpoint does not add any of that, and it can
  subtract from it by centralising the address.

None of this is a reason not to build the service. It is a reason to build the
egress and the pacing alongside it, rather than shipping the render endpoint and
discovering the address problem in production.

## Conclusion

A FastAPI render service around invisible_playwright is a small amount of code:
a lifespan that starts one browser and stops it once, a semaphore that keeps a
burst of callers from spawning unbounded pages, and an endpoint that returns
HTML or a PNG using stock Playwright methods. The browser realism comes for
free with the two-line launch.

What does not come for free is the network shape you have just created. One
endpoint means one address, and one address carrying everyone's traffic is a
detectable pattern that a perfect fingerprint does not undo. Build the clean
egress and the pacing as part of the service, not as a fix later.

## Short answers to the questions that lead here

**Should I launch a browser per request?** No. Launching is the expensive part,
so start one browser in the FastAPI lifespan and hand each request a fresh page
from it. Close the page when the request ends.

**How do I keep one browser alive across requests?** Enter the
`InvisiblePlaywright` async context in the lifespan startup and let it exit on
shutdown, keeping the returned `Browser` in app state. An `AsyncExitStack` does
the enter-and-later-exit cleanly.

**How do I stop a traffic burst from spawning too many pages?** Guard the render
with an `asyncio.Semaphore` sized to the machine. Callers past the limit wait
their turn instead of piling more pages onto one browser.

**Does one shared endpoint make me undetectable?** No. Each session's
fingerprint is real, but every caller now leaves from the server's single IP,
and volume from one address is its own signal. Realism does not fix reputation
or rate.

**Do I still need a proxy if the browser looks real?** Yes for most real
targets. The browser layer clears fingerprint and driver checks; the IP,
per-account limits and pacing are separate and yours to supply.

**Can I return a screenshot instead of HTML?** Yes. Swap `page.content()` for
`page.screenshot()` and return the bytes as `image/png`. It is stock Playwright,
`full_page=True` and all.

## Sources

- This project's Quickstart and Configuration pages for the launch, the async
  entry point, the proxy dict and the auto-derived timezone.
- FastAPI's lifespan documentation and Python's `asyncio.Semaphore` and
  `contextlib.AsyncExitStack`, all standard library and framework API.
- This set's own testing notes on why an address and its rate survive a perfect
  fingerprint, and why the screenshot beats the log.

**See also:** [how to rotate proxies without relaunching](how-to-rotate-proxies-playwright.md),
[the detection checklist in the order that matters](playwright-detected-as-bot.md),
and [how to test whether the result is actually detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The service shape
is easy; the single-IP cost is the part people ship without noticing.*
