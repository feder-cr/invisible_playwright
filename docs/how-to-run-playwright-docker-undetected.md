---
title: "How to run Playwright in Docker without getting detected"
description: "A step-by-step tutorial for running Playwright in Docker with a real GPU, font set and screen, instead of the six machine tells a bare container gives away."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 6
---


# How to run Playwright in Docker without getting detected

To run Playwright in Docker without getting detected, you have to fix what the
container reveals about its machine, not just get the browser to start. A slim image
gives away a missing GPU, a tiny font set, no audio device and a default screen -
facts about the hardware that stay true even when `navigator.webdriver` is clean. This
tutorial fixes those in the browser engine itself, then verifies the result with a
field-by-field diff against a real desktop, so the container and the desktop describe
the same seeded machine.

Most Docker-and-Playwright tutorials solve one problem: getting the browser to start.
Install the system libraries, add some fonts, use the official image, done. That
problem is real and those tutorials are correct. It is also a different problem from
the one in this title.

This is a tutorial for the container that starts fine, renders fine, and still gets a
different page than your laptop does. Nothing crashes. The container is just
describing a machine that is not a person's, and it says so in about six places at
once. We covered the theory of why in
[Playwright in Docker: it runs, and still gets blocked](playwright-docker-detection.md).
This page is the practice: a Dockerfile, the specific things a slim image is missing,
and the code that fixes each one.

## What a stock container gives away, concretely

A stock container gives away four facts about its machine at once, none of them about
automation: a missing GPU, a tiny font set, no audio device, and a screen nobody has.
Before writing a Dockerfile, know what you are fighting. A `python:3.11-slim` or
`mcr.microsoft.com/playwright` image answers every one of these the same way, and a
page can read all of them without asking permission:

- **No GPU.** WebGL falls back to a software rasterizer and says so by name -
  `llvmpipe` on Linux, a "Basic Render Driver" on Windows. See
  [WebGL renderer strings](webgl-renderer-strings.md) for the exact shape of that
  string and why it is the loudest tell on the list.
- **A tiny font set.** A slim image ships DejaVu, Liberation, and not much else, no
  matter what platform the user agent claims. See
  [why headless browsers render different fonts](headless-fonts-differ.md) for the
  three causes, none of which is "fonts are missing."
- **No audio device**, so `AudioContext` values fall back to defaults that say there
  is no sound card. See
  [AudioContext fingerprinting](audiocontext-fingerprinting.md) for the values a page
  checks and why adding noise to them made sessions easier to catch, not harder.
- **A screen nobody has** - `availHeight` equal to `height` because there is no
  taskbar, a device pixel ratio of exactly 1. See
  [screen size and viewport tells](screen-size-headless-tells.md) and
  [the devicePixelRatio pref](devicepixelratio-firefox-pref.md) for the exact
  combinations that never occur on a real machine.

None of this is about automation. `navigator.webdriver` can be perfectly clean and
every one of these four still fires, because they are facts about the machine, not
about the driver. That is also why the fix is not a stealth plugin: a plugin patches
JavaScript properties, and a font set is not a JavaScript property, it is an output of
the operating system's rendering pipeline.

## A Dockerfile that starts

Here is a minimal, working setup. It uses the wrapper directly, so nothing here is
Chromium-specific plumbing you have to translate.

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the patched engine into the image at build time, not at container
# start. This is a one-time ~238 MB download (~544 MB unpacked), sha256-verified
# against the seal shipped inside invisible-core, so a later `docker run` never
# needs network access to get a browser.
RUN python -m invisible_playwright fetch

COPY script.py .
CMD ["python", "script.py"]
```

```
# requirements.txt
invisible-playwright
```

That builds and runs. It also, by itself, still describes a machine with no GPU, a
tiny font set and no audio device, for the reason above: none of those come from
`pip install`, they come from the base image. The next two sections are what actually
changes that.

## The part a Dockerfile cannot fix by itself

You could install more fonts here, or set `--use-gl=swiftshader` flags, or reach for a
stealth plugin that patches `navigator.webdriver`. Every one of those addresses a
different layer than the one that is actually visible:

- Installing fonts fixes an empty font list, but a font list has to match the
  *platform you claim*, not just be non-empty - a bigger, mismatched set is a
  stronger tell than a small honest one.
- Patching WebGL's `getParameter` in JavaScript can make the renderer string say
  NVIDIA. It does not change what draws the pixels, so the string and the render then
  disagree, which is
  [a worse contradiction than an honest software renderer](renderer-string-vs-render.md).
- `navigator.webdriver` was never the problem here. It is an automation tell, and a
  server describing itself as a server is a machine tell. They need different fixes,
  which is the whole argument of
  [headless vs headful](headless-vs-headful.md): switch a server to headful and it is
  still a server.

The reason this project patches the engine instead of the page is exactly this list:
a value set from JavaScript is one property, checkable once and inconsistent with
everything around it. A value produced by the engine itself - the font rasterizer,
the reported GPU, the audio device defaults - is consistent by construction, because
nothing downstream had to be told to agree with it.

## Running the container with a real fingerprint

`script.py`, unchanged from what you would write outside Docker:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.screenshot(path="/app/out.png")
    print(page.title())
```

`browser` is a real Playwright `Browser`; every method works exactly as documented
upstream, container or not. `headless=True` here means headed and hidden rather than
a stripped rendering mode - the engine stays on the same code path a visible window
uses, which matters more inside a container than outside one, since a container is
already missing enough real hardware without the browser giving up the normal
rendering path too.

Build and run it with room for shared memory, which is unrelated to detection but
breaks under load if you skip it:

```bash
docker build -t invisible-scraper .
docker run --shm-size=1gb invisible-scraper
```

Containers default to a 64 MB `/dev/shm`, and a browser under real load wants more
than that. Fix it with the flag above rather than by disabling shared memory usage,
because the flags that disable it change how the browser behaves instead of giving it
room. A browser that crashes under load looks like a browser that got blocked, and the
two need completely different fixes - establish which one you have before touching
your fingerprint.

## Verifying it, container against desktop

A single clean-looking run does not prove anything, and the domain is not
deterministic. The check that actually catches a mismatch is a diff, not a verdict:
run the same seed inside the container and on a normal desktop, and compare field by
field.

```python
from invisible_playwright import InvisiblePlaywright

FP_SCRIPT = """() => {
    const gl = document.createElement('canvas').getContext('webgl');
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    return {
        renderer: gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL),
        vendor: gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
        availHeight: screen.availHeight,
        height: screen.height,
        dpr: window.devicePixelRatio,
    };
}"""

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    report = page.evaluate(FP_SCRIPT)
    assert "Basic Render Driver" not in report["renderer"], "software rasterizer leaked through"
    assert "llvmpipe" not in report["renderer"], "software rasterizer leaked through"
    assert report["availHeight"] < report["height"], "no taskbar reported, this is a headless tell"
    print(report)
```

Run that script both inside the built image and outside it on your own machine, with
the same seed, and the two reports should agree - GPU string, font set, screen
relationships, all of it - because the fingerprint comes from the seeded profile and
the bundled engine, not from whatever hardware happens to be underneath. That
agreement is the actual claim being tested here, not the assert statements: a seed is
supposed to produce the same machine everywhere it runs, container included. Run it
at least ten times before trusting a single green result, and open the PNG from
`page.screenshot()` rather than only reading the printed dict - a screenshot shows
what actually rendered, including whatever the extractor above did not think to ask
for.

## What this does and does not fix

Worth being precise about, because the honest limit matters more than the pitch.

The engine bundles its own font stack and ignores the container's fontconfig
entirely, so the font list a page measures is the same list on a bare `python:slim`
image as on a desktop, and it is the list that belongs to the platform being claimed.
The screen values, the audio defaults and the GPU fingerprint all come from the same
seeded profile, so they describe one coherent machine instead of six unrelated
container defaults.

What it does not fix: an actual GPU. If the container has no graphics hardware, WebGL
still renders in software underneath, whatever the reported strings say, because a
canvas hash and a render are outputs, not values anyone can just declare. Hardware
passthrough on a host that has a GPU is the only real fix for that, and it is not
available on every deployment target. That gap is the same one described in
[WebGL renderer strings](webgl-renderer-strings.md), and no amount of patching
anywhere else closes it, because it is not a lie being told - it is the truth about
where the container runs.

## Conclusion

A container that starts and renders is not the same as a container that looks like
somebody's desktop, and the gap between the two is six specific, checkable things:
GPU, fonts, audio, voices, screen, and the core/memory pairing. `docker build` and
`--shm-size` solve the startup and stability problems, which are real and worth
solving first. They do nothing for the six above, because those come from the base
image, not from a flag. Fixing them from the page - more fonts, an overridden
`getParameter` - trades one tell for a worse one, a contradiction between what is
claimed and what is rendered. Fixing them in the engine, so the container and a
desktop describe the same seeded machine, is the only version of this that survives a
field-by-field diff instead of a single verdict.

## Short answers to the questions that lead here

**Why does my Playwright script work locally but get blocked in Docker?** Because the
container answers differently about the machine it runs on: no GPU, a small font set,
no audio device, a default screen. None of those change when `navigator.webdriver` is
patched, because none of them are automation tells.

**Does the official Playwright Docker image fix this?** It fixes the browser starting
and rendering reliably. Its font set and defaults are shared by everyone who uses that
image, which is consistency, not disguise.

**Should I install more fonts in my Dockerfile?** Only the ones belonging to the
platform you are claiming, and only if you can match the set closely. A larger, mixed
set is a stronger mismatch than a small honest one.

**What does `--shm-size` actually fix?** Crashes and hangs under load from a
default 64 MB `/dev/shm`. It is a stability fix, unrelated to detection, and
conflating the two sends people to rewrite a fingerprint when a flag would have done.

**Can I get a real GPU inside a container?** Sometimes, with hardware passthrough on a
host that has one. It is the only fix for a software renderer, and it is not available
on every deployment target.

**Is baking the engine into the image at build time necessary?** No, but it means
`docker run` never needs network access to fetch a browser, and the sha256 check
against the seal happens once, at build time, instead of on every container start.

**See also:** [Playwright in Docker: it runs, and still gets blocked](playwright-docker-detection.md)
for the deeper explanation of why each of the six tells exists, [how to scrape without
getting blocked](how-to-scrape-without-getting-blocked.md) for where this fits in the
larger order of things to fix, and
[how to test whether your browser is detected](how-to-test-bot-detection.md) for the
comparison method used in the verification section above.

## Sources

- [Playwright in Docker: it runs, and still gets blocked](playwright-docker-detection.md),
  for the six-tell breakdown this tutorial builds a Dockerfile against.
- [WebGL renderer strings](webgl-renderer-strings.md) and
  [why headless browsers render different fonts](headless-fonts-differ.md), for the
  two loudest machine tells and why installing fonts or overriding `getParameter`
  does not close them.
- [Headless vs headful](headless-vs-headful.md), for why switching a server to
  headful leaves every one of these tells in place.
- This project's own installation and configuration docs, for the exact download
  size, the sha256 verification, and the environment variables used to bake or
  relocate the cached engine.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The font stack and the GPU fingerprint travel with the
engine into the container, which is the only reason `docker build` and a desktop can
agree on what machine they are.*
