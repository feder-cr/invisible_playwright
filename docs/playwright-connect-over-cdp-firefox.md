---
title: "Playwright connect_over_cdp does not work with Firefox"
description: "connect_over_cdp is Chromium-only: Firefox ships no DevTools Protocol. Here is the Juggler-based connect() path that replaces it, with runnable Playwright code."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 16
---


# Playwright connect_over_cdp does not work with Firefox

You reached for `browser_type.connect_over_cdp(...)`, pointed it at a Firefox
instance, and got nothing to connect to. Most pages that answer this bury the
reason under a workaround. The reason is the whole answer: connecting to Firefox
over the DevTools Protocol is not hard, it is **structurally impossible**, because
Firefox ships no DevTools Protocol surface for anything to attach to.

This page is why the method is Chromium-only, what actually carries automation to
Firefox instead, the `connect()` path that replaces `connect_over_cdp`, and why with
this project you usually do not need either.

## connect_over_cdp is a Chromium-only entry point

[`connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
does exactly one thing: it speaks the Chrome DevTools Protocol to an already-running
browser that is listening on a CDP endpoint. Playwright's own reference for the method
states plainly that "connecting over the Chrome DevTools Protocol is only supported for
Chromium-based browsers." It is a real method on every browser type in Playwright's
Python API, including `firefox`, so calling it does not raise "no such attribute". It
raises because there is no endpoint on the other end.

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # Chromium: attaches to a browser already listening with --remote-debugging-port
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    # Firefox: there is no CDP port to point this at, so there is nothing to attach to
```

The Chromium line works because Chromium can be told to open a DevTools endpoint. The
Firefox line has no equivalent to point at. The DevTools Protocol is a shipped, first
class part of Chromium; it is not part of Firefox at all.

## Why there is no DevTools endpoint to connect to

Playwright does not drive Firefox through the DevTools Protocol the way it drives
Chromium. It drives it through **Juggler**, a separate automation protocol built for
Firefox, carried over a pipe rather than a CDP socket. There is no CDP layer sitting
underneath waiting to be exposed, so no flag turns one on.

This is not a gap that a future release closes. It is a design difference between the
two engines, and for automation it cuts in Firefox's favour: a protocol that was never
shipped as a public browser interface is also a protocol with no shipped artefacts for a
detector to look for. [The structural version of that argument is here](firefox-vs-chromium-antidetect.md).

Juggler is not a loose, best-effort protocol either. Its command schema is validated
**closed-world**: a payload carrying any field the schema was not told to expect is
rejected outright, at the moment the call is made. That strictness is invisible until a
client and server disagree, at which point [a routine Playwright upgrade can take out 97
of 133 end-to-end tests while the browser still launches and loads pages](playwright-protocol-drift.md).
The point for this page is narrower: the wire Firefox listens on is Juggler, not CDP, so
the connection primitive that reaches it is not `connect_over_cdp`.

## The path that replaces it: connect() over a Juggler endpoint

The remote-connection method for Firefox is
[`connect()`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect),
not `connect_over_cdp()`. It attaches to a **browser server** that publishes a `ws://`
endpoint speaking Juggler; Playwright's own reference describes `connect()` as attaching
"to an existing browser instance created via `BrowserType.launchServer`" and does not
restrict it to Chromium the way `connect_over_cdp` is restricted.

You start that server with Playwright's own tooling. The `launchServer()` call in the
Node API, or the `playwright launch-server --browser firefox` CLI it backs, both boot a
Firefox and print a `ws://` URL. That URL is a Juggler websocket endpoint, not a CDP one,
and you hand it to `connect()` from any client, Python included:

```python
from playwright.sync_api import sync_playwright

# WS is printed by a browser server, e.g.
#   playwright launch-server --browser firefox
# It is a Juggler ws:// endpoint, not an http:// CDP endpoint.
WS = "ws://127.0.0.1:5000/<guid>"

with sync_playwright() as p:
    browser = p.firefox.connect(WS)      # NOT connect_over_cdp
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
    browser.close()
```

The mapping to remember, if you are porting Chromium code:

| Chromium | Firefox |
|---|---|
| `chromium.connect_over_cdp(http_url)` | `firefox.connect(ws_url)` |
| `--remote-debugging-port` opens a CDP endpoint | `launch-server` publishes a Juggler ws endpoint |
| DevTools Protocol over a socket | Juggler over a pipe / websocket |

There is no line in that table where a CDP URL reaches Firefox, because no such URL
exists.

## With invisible_playwright you rarely need connect at all

With invisible_playwright you usually need neither `connect()` nor `connect_over_cdp`:
the launcher hands you a real, fully configured `Browser` object directly, in-process, and
it is a genuine Playwright `Browser` with every standard method. People reach for
`connect_over_cdp` because they assume attaching to a separately launched browser is the
only way to get one configured the way they want. With this project it is not.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())          # every standard Playwright method works unchanged
```

The `browser` returned is a `playwright.sync_api.Browser`, so `new_page`, `new_context`,
`page.goto`, `page.click` and the rest behave exactly as documented upstream. No CDP
endpoint, no server to boot, no websocket to wire up. The seed makes the whole thing
reproducible: `seed=42` yields the same GPU, canvas hash, audio context, fonts and screen
on every run, which is the difference between replaying a failure and guessing at it.

The async surface is identical:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    print(await page.title())
```

If you genuinely need an out-of-process browser to attach to (a long-lived server, a
different host), the `connect()` path in the previous section is the one to use. For the
common case, the context manager removes the reason people went looking for
`connect_over_cdp` in the first place.

## What "just launch it normally" buys you over attaching

Attaching over a protocol has a cost that the in-process launch avoids: the connection
method itself is part of what a page can measure, not just what it lets you do.

An automation protocol is a surface. Attaching a debugger to a page realm, historically,
did four separately observable things to Firefox: it disabled the optimising JIT so the
page ran measurably slower, it dropped the driver's own frames into `Error.stack`, it
labelled evaluated code as debugger evaluation, and it warmed a serialisation helper in
the page's own realm before the page's first script ran. [All four are written up, and
all four are fixed here](debugger-timing-detection.md). The lesson that carries over is
that how you connect is not cosmetic: the connection path is part of what a page can
measure. A launcher that owns the whole engine can close those; a thin attach-over-a-port
model inherits whatever the endpoint exposes.

So the honest framing is not "connect_over_cdp is missing a feature". It is that the CDP
attach model does not exist for Firefox, the Juggler `connect()` model does, and the
in-process launch is the one that lets the engine be patched underneath you rather than
observed through a socket.

## Conclusion

`connect_over_cdp` does not work with Firefox because Firefox ships no DevTools Protocol
for it to reach. Playwright drives Firefox through Juggler, a closed-world protocol over a
pipe, and the remote-connection method that matches it is `connect()` to a `ws://`
endpoint from a browser server, not `connect_over_cdp()` to a CDP URL. With
invisible_playwright the shortest path is neither: the launcher returns a real Playwright
`Browser` in-process, seed-reproducible, with the full API intact. Reach for `connect()`
only when you truly need a browser in another process.

## Short answers to the questions that lead here

**Does connect_over_cdp work with Firefox?** No. It speaks the Chrome DevTools Protocol,
and Firefox exposes no CDP endpoint for it to attach to. The method exists on the Firefox
type but has nothing to connect to.

**Why is connect_over_cdp Chromium-only?** Because the DevTools Protocol is a shipped part
of Chromium and is not part of Firefox at all. Playwright drives Firefox through Juggler
instead.

**How do I attach to a running Firefox with Playwright?** Start a browser server
(`playwright launch-server --browser firefox`, or `launchServer()` in the Node API), take
the `ws://` endpoint it prints, and pass it to `firefox.connect(ws_url)`, not
`connect_over_cdp`.

**What is Juggler?** Firefox's own automation protocol, carried over a pipe, that
Playwright uses in place of the DevTools Protocol. Its schema rejects any undeclared
field at runtime, which is why a client and server that disagree fail sharply.

**Do I need connect_over_cdp to configure the browser?** No. With invisible_playwright the
launcher returns a fully configured, real Playwright `Browser` in-process, so there is no
separate endpoint to attach to for the common case.

**Is there any CDP flag I can enable in Firefox to make this work?** No. There is no CDP
layer underneath to switch on. Use the Juggler `connect()` path instead.

## Sources

- Playwright's own [`BrowserType` API reference](https://playwright.dev/python/docs/api/class-browsertype),
  where [`connect_over_cdp`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
  and [`connect`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect)
  are documented as distinct methods, the former stated as Chromium-only.
- This project's Juggler protocol contract, including the closed-world schema validation
  and the driver-layer artefacts documented on the pages linked above.

**See also:** [Firefox or Chromium for anti-detect automation](firefox-vs-chromium-antidetect.md)
for why no-CDP is a structural property rather than a missing feature,
[why a Playwright upgrade broke 97 of 133 tests](playwright-protocol-drift.md) for how
strict the Juggler contract is, and [the two-line switch from plain Playwright](quickstart.md)
for the in-process launch.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. connect_over_cdp is the
question we get most from people porting Chromium code, and the answer is that Firefox
never had the endpoint to begin with.*
