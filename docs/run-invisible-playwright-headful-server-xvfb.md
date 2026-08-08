---
title: "Run invisible_playwright headful on a server with Xvfb"
description: "Run a real headful Firefox window on a headless server with Xvfb when headless=True is enough, and why the fingerprint, not display mode, passes detection."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 87
---


# Run invisible_playwright headful on a server with Xvfb

Search for "headless gets detected" and the top answer is almost always the same:
install Xvfb, run a real headful window, and the giveaway disappears. It is a folk fix
built on a wrong premise. Headless mode is not the tell a serious stealth engine fixes;
the fingerprint is. So `headless=True` already passes the checks people install Xvfb to
escape, and Xvfb becomes an optional tool for a much narrower set of jobs.

This page separates the two things that folk fix confuses: the display mode you run in,
and the fingerprint the page reads. It shows the real API for both, the honest cases
where Xvfb earns its place, and the parts of a session that no display mode touches.

## Why headless is not the giveaway here

The idea that "headless is detectable" comes from a real history. Old headless Chrome
announced itself in a dozen ways, so switching to a headed window did remove those
specific tells. But what those tells had in common was not the window. It was the set of
things that tend to travel with a bare headless server: no GPU, no fonts, no audio
device, a screen size nobody has, an automation flag left set.

A page reading your browser does not have an `isHeadless` property to check. It reads the
fingerprint: the WebGL renderer, the font list, the audio stack, the screen, the driver
layer, the TLS handshake. If those read as a genuine Firefox on a real machine, the page
cannot tell whether a window was drawn on a monitor or into a memory buffer, because that
difference does not reach JavaScript.

invisible_playwright is built around that fact. It is a Firefox patched at the C++ level
so the fingerprint, the TLS handshake and the driver layer read as a real browser, and
the same seed reproduces the same machine every run. That is what passes the detectors,
in headless mode and headed mode alike. If you are still weighing the two, see
[headless versus headful and what actually differs](headless-vs-headful.md) and
[whether headless mode is detectable at all](is-playwright-headless-detectable.md).

## What Xvfb actually does

Xvfb (X Virtual FrameBuffer) is an X server that draws into memory instead of onto a
monitor. On a Linux box with no display hardware, a program that insists on a real window
has nowhere to open it and fails with an error about a missing `DISPLAY`. Xvfb hands that
program a virtual display so it renders into a buffer no one is looking at.

That is the whole trick. It does not change a single fingerprint value, it does not touch
the network layer, and it does not make a browser look more or less human. It only
answers the question "where do I draw this window" on a machine that would otherwise have
no answer.

## You may not need Xvfb at all: headless=True renders headed

Here is the part the folk fix misses. With this wrapper you usually do not manage Xvfb
yourself, because `headless=True` is not a bare headless render.

```python
from invisible_playwright import InvisiblePlaywright

# headless=True on Linux keeps Firefox in HEADED rendering mode and hides the
# window on a virtual display the wrapper spawns for you. Real rendering
# pipeline, coherent fingerprint, no monitor required.
with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

On Linux, `headless=True` spawns its own virtual display, points `DISPLAY` at it, and
keeps Firefox in headed mode so the rendering pipeline and the fingerprint stay coherent.
You get real headed rendering with no window and no monitor, and you did not write a line
of Xvfb plumbing. It does need the package installed once (the launch will stop and tell
you `sudo apt install xvfb` if it is missing).

So for the common case, the answer to "do I need Xvfb to avoid the headless tell" is: the
tell is already handled by the fingerprint, and the virtual display is already handled for
you. Reach for a manual Xvfb setup only when you specifically need the next section.

## When Xvfb is actually worth it: headless=False on a server

There is a real, narrower case: you want a genuine headful window, `headless=False`, on a
server with no monitor. Two honest reasons to want that:

- A browser extension that only works with a real, on-screen display attached.
- A page that genuinely behaves differently in a truly windowed session, which does
  happen for a small number of sites and is worth confirming by comparison rather than
  assuming.

For those, you run your whole Python process under an Xvfb display and pass
`headless=False`. The classic wrapper is `xvfb-run`, which starts an Xvfb, exports
`DISPLAY`, runs your command, and tears the display down afterward:

```bash
# scrape.py contains the Python below
xvfb-run -a --server-args="-screen 0 1920x1080x24" python scrape.py
```

```python
# scrape.py
from invisible_playwright import InvisiblePlaywright

# headless=False opens a REAL headed window. On a display-less server this only
# works because the process is running under the xvfb-run display above.
with InvisiblePlaywright(seed=42, headless=False) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.screenshot(path="out.png")   # opening the PNG is the real check
```

The `browser` object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every standard
method works exactly as documented upstream. Nothing about driving the page changes
between the two display modes; only where the window is drawn changes. Reach for this
deliberately, for one of the reasons above, not as a reflexive stealth step. The stealth
comes from the patched engine either way.

## Installing Xvfb and a minimal container

Whether you let `headless=True` spawn the display or run `headless=False` under
`xvfb-run`, the one system package you need is the same:

```bash
sudo apt-get update
sudo apt-get install -y xvfb
```

In a container, install it in the image and you are done. There is nothing browser-side
to add, because the engine downloads itself on first use:

```dockerfile
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb \
 && rm -rf /var/lib/apt/lists/*
RUN pip install invisible-playwright
COPY scrape.py .
CMD ["python", "scrape.py"]
```

For everything else that comes up when packaging this into an image (cache location, the
one-time engine download, running as a non-root user), see
[running invisible_playwright in Docker](how-to-use-invisible-playwright-in-docker.md).

## What Xvfb does not fix

This is the honest half, and it is the same for every display mode. invisible_playwright
is designed to look like a real browser driven by a real person, which is why the
fingerprint, TLS and driver layer read as a genuine Firefox and clear most detection
checks. It does not, on its own, fix the parts of a session that are not a browser
property at all:

- **IP reputation.** A perfect browser on a datacenter address that a thousand other
  people share is still on that address. Supply a clean exit. See
  [why a clean fingerprint still gets blocked](why-blocked-with-a-clean-fingerprint.md).
- **Per-account quotas and rate limits.** No render mode raises a limit the account
  already hit.
- **Behaviour and timing.** Pointer motion, typing rhythm and pacing are yours to supply.
  The wrapper draws curved, paced mouse motion by default, but the overall rhythm of what
  you do is still up to you.

Xvfb changes exactly one of those: none. It answers "where is the window drawn", and
nothing more. Treat it as plumbing, and put your effort into the clean proxy and the
human pacing, which is where a session is actually won or lost. If your headless runs are
being blocked today, work
[the order in the headless-blocked checklist](how-to-scrape-headless-blocked.md) before
you assume the display mode is the cause.

## Conclusion

The "headless is the giveaway, so use Xvfb" advice inverts the real picture. Here the
fingerprint is what passes detection, `headless=True` already renders headed on a virtual
display the wrapper manages for you, and Xvfb is an optional tool for the narrow case of a
real `headless=False` window on a machine with no monitor, usually for an extension or a
page that behaves differently windowed. Install `xvfb`, decide between the two modes on
purpose, and remember that whichever you pick, the stealth is in the patched engine and
the session is still won by a clean IP and human pacing.

## Short answers to the questions that lead here

**Do I need Xvfb to avoid getting detected as headless?** No. The tell was never the
window, it was the fingerprint that tends to come with a bare server, and that is what the
engine fixes. `headless=True` passes the same checks a headful window would.

**Does invisible_playwright use Xvfb automatically?** On Linux, `headless=True` spawns its
own virtual display and keeps Firefox in headed rendering, so you do not manage Xvfb
yourself. It needs the `xvfb` package installed, and it will tell you if it is missing.

**When would I actually run headless=False on a server?** When you need a real on-screen
display: an extension that requires one, or a page you have confirmed behaves differently
in a truly windowed session. Run the process under `xvfb-run` and pass `headless=False`.

**Is headless=True or headless=False more stealthy?** Neither. The fingerprint is
identical across modes because it is generated the same way. Pick the mode for functional
reasons, not for stealth.

**If the browser passes every check, why am I still blocked?** Because Xvfb and the
fingerprint do not touch IP reputation, account quotas, rate limits or your timing. A
datacenter IP and robotic pacing fail with a perfect browser. Supply a clean exit and
human rhythm.

**How do I install Xvfb?** `sudo apt-get install -y xvfb`, or add it to your Docker image.
That single package covers both the automatic virtual display and manual `xvfb-run`.

## Sources

- The wrapper's own launch path, where `headless=True` spawns a virtual display and keeps
  Firefox in headed mode, and refuses with an install hint when the package is absent.
- The project's fingerprint, TLS and driver patches, which are what clear the detectors
  independently of the display mode, verified against the public detection suites
  (CreepJS, BotD, FingerprintJS, sannysoft, BrowserLeaks).
- Playwright's own [`Browser` class API](https://playwright.dev/python/docs/api/class-browser),
  which every method on the `browser` object above implements unchanged.

**See also:** [headless versus headful and what actually differs](headless-vs-headful.md),
[running it in Docker](how-to-use-invisible-playwright-in-docker.md), and
[can a website tell you are on a server](can-a-website-tell-you-are-on-a-server.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Xvfb is plumbing; the
stealth is in the engine, and the session is still won by a clean IP and human pacing.*
