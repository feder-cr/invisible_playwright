---
title: "Running an AI browser agent headless on a server"
description: "Run AI browser agents headless on a server without datacenter tells. Engine covers GPU-less headless fingerprint; IP reputation stays yours to control."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 11
---


# Running an AI browser agent headless on a server

Your agent worked. You ran it on your laptop, it clicked through the flow, it read the
page it was supposed to read. Then you moved it to a server so it could run unattended,
and the same agent that sailed through yesterday now gets a challenge on the first
request.

Almost nobody who hits this changed one thing between the two runs. They changed two,
at the same time, and only noticed one of them.

## What actually changed between laptop and server

When you move an agent from a laptop to a server you usually flip two independent
switches in a single step:

- **The environment lost its display.** A server has no GPU, no attached monitor, no
  audio device, and usually a font set that belongs to a bare Linux box. A browser
  running there answers a fingerprinting page honestly, and the honest answers say
  "datacenter".
- **The network exit changed.** Your laptop went out through a residential connection.
  The server goes out through a datacenter address block whose reputation is already
  known and often already flagged.

These are two different problems with two different owners. The first is a property of
the browser and the machine it runs on. The second is a property of the route your
packets take, and no browser setting touches it. Diagnosing this as "detection got
harder" hides the fact that [a website can tell it is talking to a server](can-a-website-tell-you-are-on-a-server.md)
for reasons that have nothing to do with each other.

## The headless axis: the tells the engine covers

The first switch, the display-less environment, is the one a stealth engine can do
something about, and it is the reason invisible_playwright exists. The browser is a
Firefox patched at the C++ level, so the fingerprint it presents is a real Windows
Firefox regardless of what it is running on:

- A spoofed canvas and WebGL surface with a plausible GPU vendor and renderer, seed
  deterministic, instead of a software rasterizer string that announces there is no
  graphics card. The renderer string and the pixels it draws agree, which is
  [a mismatch a header-only spoof cannot hide](renderer-string-vs-render.md).
- Real Windows screen metrics and device pixel ratio instead of
  [a resolution and taskbar-free height that no real display has](screen-size-headless-tells.md).
- A bundled Windows font set that matches the platform the browser claims, so the font
  list does not read as a bare server.
- The driver-layer automation flags a stock automated browser leaves lying around are
  gone, and the TLS handshake is a genuine Firefox handshake because the browser is a
  genuine Firefox.

Crucially, all of that holds with the browser running
**[headless](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch)**.
Headless mode by itself is far less detectable than people think; what gets a server
caught is the empty GPU, the missing fonts, the impossible screen, and those are exactly
the axes the engine fills in. So the "GPU-less headless" half of your two-axis change is
covered by the tool. The other half is not, and no honest page will tell you otherwise.

## A minimal headless run on a server

Switching an existing Playwright agent over is a two-line change, and the object you get
back is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser) with every standard
method. Here is the whole thing, headless, through your proxy, with a fixed seed so a
failing run on the server is reproducible rather than a fresh random identity every time:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# runs headless on a server while presenting a real Windows Firefox fingerprint
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")          # mouse arcs to the button on a Bezier curve
    print(page.title())
```

`page`, `browser.new_page()`, `page.goto`, `page.click` are ordinary Playwright. There is
no wrapped subset to learn, so whatever agent loop you already have keeps working. The
timezone is auto-derived from the proxy's egress IP by default, so the browser clock
matches the exit country without you setting it. Pass an explicit `timezone="..."` only
if you want to override that.

The seed is the part that pays off on a server. Log it once and the exact same machine
comes back on the next run:

```python
sf = InvisiblePlaywright(proxy=proxy)
with sf as browser:
    print("seed =", sf.seed)   # write this to your job log
```

Now a failure at 3 a.m. is replayable at 9 a.m. from the same identity, instead of a
guess about which random draw the server happened to produce.

## Deploying it in a container

The engine downloads a patched Firefox on first use, verifies a checksum, and caches it.
On a server you want that cache to survive between runs rather than re-downloading each
time, so point it at a persisted path:

```dockerfile
FROM python:3.12-slim

RUN pip install invisible-playwright

# keep the cached engine on a mounted volume so it downloads once
ENV INVISIBLE_PLAYWRIGHT_CACHE_DIR=/engines

COPY agent.py /app/agent.py
WORKDIR /app
CMD ["python", "agent.py"]
```

```bash
docker run --rm -v engine-cache:/engines your-agent-image
```

Because the browser is headless already, there is no `xvfb`, no virtual display, no
window manager to install. That is the whole point of the headless axis being handled at
the engine level. If you are weighing a function-as-a-service target instead of a
container, the download-and-unpack weight is the deciding factor, and
[whether invisible_playwright fits serverless](can-you-run-invisible-playwright-serverless.md)
walks through where it does and does not.

## The IP axis: what the engine does not fix

Here is the honest half. A perfect Windows Firefox fingerprint on a datacenter IP is
still on a datacenter IP. The browser layer and the network layer are read separately by
the systems you are up against, and the tool only owns the first one. It does nothing
about:

- **IP reputation.** [Whether a site can tell your exit is a datacenter address](can-websites-detect-a-datacenter-proxy-ip.md)
  is decided before the page loads, by the address block and its history, not by anything
  the browser reports. A clean residential-quality exit is a separate purchase and a
  separate decision.
- **Per-account quotas and rate limits.** Volume from one account or one address is a
  count, and no fingerprint changes a count.
- **Behaviour and timing.** An agent that fills a form in eighty milliseconds, or pauses
  in a shape that looks like model latency before every action, is legible as automation
  no matter how real the browser is. Pacing is yours to supply, and
  [what stealth does and does not do for AI agents](ai-browser-agents-stealth.md) covers
  the behavioural layer specifically.

The engine covers the fingerprint, TLS and driver layer, which is why it passes most
detection checks. It is not an evasion guarantee, and there is no such thing. You supply
a clean exit and human pacing; it supplies a browser that reads as a real person's real
Firefox.

## Conclusion

The laptop-to-server surprise is almost always two changes wearing one costume: a headless,
GPU-less environment and a datacenter exit, moved together. Separate them. The headless
environment is handled for you, headless and all, by a browser that presents genuine
Windows Firefox metrics instead of the empty-GPU tells a stock automated browser leaks.
The datacenter exit is a different problem with a different fix, and pretending the
browser solves it is how people burn three days on the wrong axis. Cover the first with
the tool, cover the second with a clean proxy and sane pacing, and the agent that worked
on your laptop works on the server for the reason it worked in the first place.

## Short answers to the questions that lead here

**My agent passes locally and fails on the server. What changed?** Two things at once:
the server has no GPU, fonts or real screen, and it exits through a datacenter IP. The
engine covers the first; the second is a separate fix.

**Does headless mode get my agent detected?** Much less than the things that come with a
server. Headless by itself is weak signal; the empty GPU, missing fonts and impossible
screen size are the strong ones, and those are exactly what the engine fills in.

**Does invisible_playwright run headless?** Yes, and it still presents a real Windows
Firefox fingerprint while headless, which is the whole reason it is worth running on a
server rather than a stock automated browser.

**Will this fix my datacenter IP being blocked?** No. The browser layer and the IP
reputation layer are independent. A real fingerprint on a flagged address still loses;
you need a clean exit for that half.

**Do I need a virtual display or xvfb on the server?** No. The browser is headless
already, so there is no attached-display requirement to fake.

**Is any of this guaranteed to get through?** No, and treat any tool that claims to be
as a warning sign. This handles the fingerprint, TLS and driver layer, which is most of
what gets an agent caught; behaviour, rate limits and IP reputation are still yours.

## Sources

- This project's real API, from the [Quickstart](quickstart.md) and
  [Configuration](configuration.md) pages: the two-line launch, the seed, proxy schemes,
  the engine cache directory variable.
- This project's own troubleshooting notes on why a clean browser on a datacenter address
  still fails, and on the machine-versus-automation split that separates the two axes
  above.
- Playwright's own [`Browser` class reference](https://playwright.dev/python/docs/api/class-browser)
  and [`launch()` reference](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch)
  for what the returned object is and what the `headless` option actually controls.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
[how to test bot detection without a false pass](how-to-test-bot-detection.md), and
[whether a website can tell you are on a server](can-a-website-tell-you-are-on-a-server.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The two-axis mistake in the
first section is one I made moving my own jobs off a laptop.*
