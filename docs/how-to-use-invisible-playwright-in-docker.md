---
title: "How to use invisible_playwright in Docker"
description: "Install invisible-playwright in Docker, fetch the patched Firefox binary at build time, run headless, and verify the fingerprint survives the container."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 7
---


# How to use invisible_playwright in Docker

To run `invisible_playwright` in Docker, start from a maintained Playwright
base image for the OS-level shared libraries, `pip install
invisible-playwright`, fetch the patched Firefox engine at build time, and
run the container with `--shm-size=1gb`. The fingerprint itself needs
nothing container-specific: the same seed produces the same machine on a
desktop or inside a container.

This is a setup tutorial, not the detection theory. If you have not read
[why a container that starts perfectly can still get a different page than
your laptop](playwright-docker-detection.md), read that first - it explains
the six things a stock container gives away (GPU, fonts, audio, voices,
screen, core/memory pairing) that no flag on this page fixes. What this page
covers is narrower: get `invisible-playwright` installed, its patched engine
fetched, and a session actually running inside a container, with the two
steps everyone trips on the first time - the binary download and the shared
memory size - called out explicitly.

## The base image

`invisible_playwright` does not use the browsers a Playwright base image
bundles - it drives its own patched Firefox binary, fetched separately. What
the image still buys you is the OS-level shared libraries a Linux browser
needs to start at all (X11, GTK, NSS, ALSA and the rest), which is exactly
the part [installation](installation.md) does not enumerate because it
depends on your base distribution. Starting from a maintained Playwright
image is the pragmatic way to get that layer right without hand-listing
packages that may not match your distro:

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app
```

If you already maintain your own slim base image and know which shared
libraries a browser needs on it, that works too - nothing here depends on
the rest of that image's contents. The point of this base is only the
libraries, not the browsers it ships alongside them.

## Installing invisible-playwright

Installing `invisible-playwright` in a container is the same two-step
install as on a desktop: `pip install invisible-playwright` for the
package, then a separate `fetch` step to download the patched engine. See
[installation](installation.md) for why the two steps are kept separate:

```dockerfile
RUN pip install --no-cache-dir invisible-playwright
```

Nothing container-specific here yet. The wheel is small; the part that
needs a build-time decision is the next step.

## Fetching the patched engine at build time

`fetch` downloads the patched Firefox binary, verifies it against the
sha256 shipped inside `invisible-core`, and caches it - a one-time,
~238 MB download that unpacks to ~544 MB. Do this **at build time**, not on
container start, so the image is ready to run immediately and a cold start
never depends on the network:

```dockerfile
RUN python -m invisible_playwright fetch
```

`python -m invisible_playwright` is identical to the installed
`invisible-playwright` command and needs nothing on `PATH`, which matters
in a container where the venv's bin directory is not always where you
expect it - see the [CLI reference](cli-reference.md) for both forms.

Two environment variables are worth setting explicitly in a Dockerfile
rather than discovering you need them from a failed build:

```dockerfile
# A CI runner or a corporate network that rate-limits or blocks anonymous
# GitHub downloads needs a token for the fetch step to succeed:
ENV STEALTHFOX_GITHUB_TOKEN=""

# Point the cache somewhere your CI already persists between builds, so a
# rebuild does not re-download 238 MB every time:
ENV INVISIBLE_PLAYWRIGHT_CACHE_DIR=/opt/invisible-playwright-cache
```

Both are covered in full in [Configuration](configuration.md). If your
build runner mounts a persistent volume, point
`INVISIBLE_PLAYWRIGHT_CACHE_DIR` at it and later builds skip the download
entirely - `fetch` checks the cached tree against the seal first and only
downloads if it does not match, so a warm cache makes the step nearly free.

The three environment variables that matter in a container build:

| Environment variable | What it does | Documented in |
|---|---|---|
| `STEALTHFOX_GITHUB_TOKEN` | Authenticates the engine download when a CI runner or corporate network rate-limits or blocks anonymous GitHub downloads | [Configuration](configuration.md) |
| `INVISIBLE_PLAYWRIGHT_CACHE_DIR` | Points the binary cache at a path the build persists between runs, so a rebuild skips the ~238 MB download | [Configuration](configuration.md) |
| `INVPW_BINARY_PATH` | Uses a binary already baked into the image and skips the `fetch` step entirely | [Configuration](configuration.md) |

## Running headless in the container

No container-specific API: pass `headless=True` the same way you would on
a desktop. The one thing every container hits regardless of which browser it runs is
shared memory: containers default `/dev/shm` to 64 MB, and a browser will
use more than that. Fix it with the run flag, not by disabling shared
memory usage, because the flags that disable it change how the browser
behaves rather than giving it room:

```bash
docker run --shm-size=1gb my-invisible-playwright-image
```

Put the actual script in the image and run it as the default command:

```dockerfile
COPY probe.py .
CMD ["python", "probe.py"]
```

```python
# probe.py
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.screenshot(path="/app/out.png")
    print("navigator.userAgent =", page.evaluate("navigator.userAgent"))
    print("hardwareConcurrency =", page.evaluate("navigator.hardwareConcurrency"))
    print("webgl renderer =", page.evaluate("""
        () => {
            const gl = document.createElement("canvas").getContext("webgl");
            const info = gl.getExtension("WEBGL_debug_renderer_info");
            return gl.getParameter(info.UNMASKED_RENDERER_WEBGL);
        }
    """))
```

Mount a volume for `/app` (or `docker cp` afterward) if you want the
screenshot on the host to look at - and open it, a grep of the log output
is not the same thing as looking at the rendered page.

## Verifying the fingerprint survived the container

The point of a seed is that the same seed produces the same machine
everywhere, and a container is the environment where that claim gets
tested hardest, because it is also the environment most likely to leak its
own defaults through. Run `probe.py` once on your desktop and once inside
the container, both with `seed=42`, and diff the three printed lines. They
should match exactly: same user agent, same core count, same WebGL
renderer string. Nothing about being inside a container changes any of
them, because none of the three is read from the host - the renderer
string, the font list behind it, the audio device and the screen values
all come from the seeded profile rather than from what the container
actually has, which is the gap [the container detection
page](playwright-docker-detection.md) describes as the one thing a stock
setup cannot fix without help.

A fingerprinting test page such as [CreepJS](creepjs-explained.md) or
[BrowserLeaks](browserleaks-explained.md) will show the same thing from
the outside: the same seed renders the same panel whether the browser
sits on your laptop or inside this container, because the font set
travels with the engine rather than being read off the box it happens to
be running on.

The one thing this does not fix, and will not: a container with no
graphics hardware still renders in software. The renderer string is
correct, the pixels behind it are still drawn by whatever rasterizer is
actually present. That is a hardware question, not something any browser
patch reaches, and it is covered honestly rather than glossed over in the
page linked above.

## Conclusion

A working `invisible-playwright` container is three ordinary Docker
decisions and one that is not: a base image for the OS-level libraries
(ordinary), installing the package (ordinary), sizing `/dev/shm`
(ordinary), and fetching the patched engine at build time so the image
starts cold with no network dependency (the one that is specific to this
project). The fingerprint itself needs nothing container-specific at
all - the seed produces the same machine whether it is sampled on a
desktop or inside a container, which is the property the verification
step above exists to prove rather than assume.

## Short answers to the questions that lead here

**Do I need to install fonts in the Dockerfile?** No. The engine carries
its own bundled font set rather than reading the container's, so you get
the same font list on a desktop and inside a container without installing
anything extra. See [installation](installation.md) for what the wheel
does and does not include.

**Where does the patched Firefox binary come from in a container?** It is
downloaded by `python -m invisible_playwright fetch` and cached, the same
one-time, sha256-verified step as on a desktop, just run at build time so
the image does not need network access on start. See the [CLI
reference](cli-reference.md).

**Why does the browser hang or crash under load in my container?** Almost
always the default 64 MB `/dev/shm`. Run the container with
`--shm-size=1gb` rather than disabling shared memory usage, which changes
browser behaviour instead of giving it room.

**Does running in Docker break the fingerprint?** No, as long as the
values come from the seeded profile rather than the host. That is what
`invisible-playwright` provides; a plain Playwright browser in the same
container would answer with the container's own defaults instead, which
[the detection page](playwright-docker-detection.md) walks through field
by field.

**Can I skip the download and bring my own binary into the image?** Yes,
set `INVPW_BINARY_PATH` to a binary already baked into the image and the
fetch step is skipped entirely. Covered in
[Configuration](configuration.md).

**Do I need a real GPU for this to work in a container?** Not for the
fingerprint - the renderer string is seed-derived either way. The pixels
behind it are still software-rendered without hardware passthrough, which
is a rendering-quality question, not a detection one.

## Sources

- [Playwright in Docker: it runs, and still gets blocked](playwright-docker-detection.md),
  the detection-level page this tutorial complements.
- [Installation](installation.md) and the [CLI reference](cli-reference.md)
  for the exact `fetch` behaviour and environment variables used above.
- [Configuration](configuration.md) for `INVISIBLE_PLAYWRIGHT_CACHE_DIR`,
  `INVPW_BINARY_PATH` and `STEALTHFOX_GITHUB_TOKEN`.
- Playwright documentation, [Docker](https://playwright.dev/python/docs/docker), for the
  base image and its version tags, retrieved 2026-08-28.
- Docker Engine reference, [`docker run`](https://docs.docker.com/engine/reference/run/),
  for the 64 MB default size of `/dev/shm` and the `--shm-size` flag, retrieved 2026-08-28.
- [CreepJS](https://github.com/abrahamjuliot/creepjs) and
  [BrowserLeaks](https://browserleaks.com/), the fingerprinting test pages named above,
  retrieved 2026-08-28.

**See also:** [How to scrape without getting
blocked](how-to-scrape-without-getting-blocked.md) for the wider order of
operations, [Quickstart](quickstart.md) for the two-line change from plain
Playwright, and [Pinning fingerprint fields](pinning.md) if the container
needs to reproduce one specific machine rather than any seeded one.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level and driven by stock Playwright. The
engine fetch and the font bundle are the two things this page leans on
that a plain Playwright container does not have.*
