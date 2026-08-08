---
title: "Can you run invisible_playwright serverless?"
description: "Why invisible_playwright mostly does not fit serverless: size of patched-Firefox is the blocker. Containers and long-lived workers are the actual home."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 84
---


# Can you run invisible_playwright serverless?

Mostly no, and for a structural reason rather than a setting you have not found yet.
The blocker is not a config flag. It is that this tool launches a full, real browser,
and a serverless function is sized for code that does not.

This page is the mechanism behind that answer, the one place it does fit, and the
container or long-lived worker that is the honest recommendation instead.

## The short answer, and why it is structural

invisible_playwright is not a JavaScript shim that patches an existing browser. It ships
a patched Firefox, built at the C++ level, and drives it with stock Playwright. On first
use it downloads that binary (about 238 MB compressed, roughly 544 MB once unpacked, as
listed on the [installation](installation.md) page), verifies a sha256, and caches it.
A real launch then needs that unpacked tree on disk plus working memory for the browser
process itself.

A typical function-as-a-service runtime caps three things at once: deployment package
size, memory, and wall-clock execution time. All three fight a cold-start browser
launch. The download alone can exceed a package limit; the unpacked tree plus a running
Firefox can exceed the memory limit; and downloading and starting a browser per
invocation can exceed the time limit before your first `goto` returns.

So the answer is not "add a bigger timeout". The weight of a real browser is the thing
that does not fit, and no configuration removes it.

## What a function actually has to do to launch this

Walk through a cold invocation of a function that has never run before:

1. Fetch the engine. If it is not baked into the image, that is a ~238 MB download on
   the critical path, before any of your code runs.
2. Unpack and place it. Roughly 544 MB has to land somewhere writable and readable.
3. Verify it. A sha256 pass over the cached engine.
4. Launch Firefox and connect Playwright over its protocol.
5. Only now open a page and do the work you came for.

Steps 1 through 4 are pure overhead, and on a serverless platform they can repeat on
every cold start because the local disk and memory a function sees do not persist between
invocations by design. You are paying browser-startup cost per request instead of once.

## Where the weight goes: download, unpack, and resident memory

Three separate limits, three separate ways this fails:

- **Package or layer size.** Many function platforms cap the deployed artifact well
  under the unpacked engine size. Baking Firefox into the deployment can blow past the
  limit outright.
- **Ephemeral disk.** The engine has to be unpacked somewhere. A read-only or tiny
  scratch filesystem cannot hold it.
- **Memory.** A real Firefox rendering a real page has a real resident-set size. Add the
  unpacked binary in page cache and a modest function memory tier is gone.

The download can be moved off the critical path with a cache directory, and the wrapper
supports exactly that: `INVISIBLE_PLAYWRIGHT_CACHE_DIR` points the engine at a path your
platform already caches between runs, and `INVPW_BINARY_PATH` skips the download entirely
when a compatible binary is already on disk (see [Configuration](configuration.md)). That
helps a persistent worker a great deal. It helps a stateless function much less, because
the thing a function does not keep is exactly the local disk those variables point at.

## The recommendation: a container image or a long-lived worker

The honest home for this tool is somewhere the browser is unpacked once and reused across
many jobs:

- **A container image** with the engine already inside it, run on a service that keeps
  the container warm. The unpack happens at build time, not per request. This is common
  enough to have its own walkthrough in
  [how to use invisible_playwright in Docker](how-to-use-invisible-playwright-in-docker.md),
  and the fingerprint pitfalls specific to containers are in
  [running Playwright in Docker undetected](how-to-run-playwright-docker-undetected.md).
- **A long-lived worker or VM** that launches the browser once at startup and pulls jobs
  from a queue, reusing the same process across many pages.

Both share one property that a stateless function cannot: the ~238 MB download and the
~544 MB unpack are paid once, at startup or build, and every job after that reuses a warm
browser. That is the difference between browser cost per deployment and browser cost per
request.

Here is a minimal worker that launches once and reuses the browser across a list of jobs.
The `browser` returned is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every method is the
stock API:

```python
from invisible_playwright import InvisiblePlaywright

JOBS = [
    "https://example.com/a",
    "https://example.com/b",
    "https://example.com/c",
]

# seed=42 makes the identity reproducible, so a failing job replays exactly.
with InvisiblePlaywright(seed=42) as browser:   # binary unpacked once, reused below
    for url in JOBS:
        page = browser.new_page()
        page.goto(url)
        print(url, page.title())
        page.close()
```

In a real worker the `for` loop is a queue consumer, and the `with` block stays open for
the life of the process. The launch cost is amortized across every job the worker ever
handles, which is the whole point of not being serverless.

If you must fetch the engine ahead of time in an image build, do it as an explicit build
step so the download never lands on a request path:

```dockerfile
FROM python:3.12-slim
RUN pip install invisible-playwright
# Unpack the engine at BUILD time, not per request.
RUN python -m invisible_playwright fetch
# ... your worker entrypoint ...
```

## What the stealth actually does for you here, and what it does not

invisible_playwright is designed to look like a real browser driven by a real person, and
that is why it passes most detection checks: the fingerprint, the TLS handshake and the
driver layer read as a genuine Firefox rather than as automation. That property is the
same whether the browser runs in a function, a container, or a VM - where it runs does not
change what it looks like, which matters here because "serverless" and "undetected" get
searched together and are different problems.

What it does not do, on its own and in any environment:

- **IP reputation.** A genuine-looking browser on a known datacenter address is still on a
  known datacenter address. You supply a clean exit.
- **Per-account quotas and rate limits.** These are counted server-side against your
  account or key, not read off the browser.
- **Behaviour and timing.** Pacing, pointer motion and the rhythm of a session are yours
  to shape. An automated cadence is visible no matter how real the fingerprint is.

Serverless makes the last one worse in a specific way: fan-out. A hundred functions firing
at once, from a small pool of platform egress addresses, in a burst no human pace explains,
is a velocity signal you created by the deployment shape rather than by the browser. A warm
worker pulling jobs at a human interval is easier to keep honest. None of that is a claim of
evasion; it is the list of things you still have to get right yourself.

## Conclusion

Can you run invisible_playwright serverless? Technically sometimes, on a generous
container-backed function tier with the engine baked into the image and kept warm.
Practically, a plain stateless function that downloads and launches a full Firefox per
invocation will be slow and fragile even where it fits, because the browser weight is
structural and no setting removes it.

The tool's job is to make the browser look real, and it does that anywhere it runs. The
deployment's job is to give that browser a warm home, a clean exit and a human pace. Put
the browser in a container image or a long-lived worker, pay the unpack once, and reuse it.

## Short answers to the questions that lead here

**Can I deploy invisible_playwright to a serverless function?** Usually not on a plain
stateless function. The patched Firefox is about 238 MB to download and roughly 544 MB
unpacked, which fights typical size, memory and time limits. A container image or a warm
worker is the real home.

**Why is the browser so heavy?** Because it is a real, full Firefox patched at the C++
level, not a JavaScript patch over an existing browser. That weight is why the fingerprint
and TLS read as genuine, and it is also why it does not fit a tiny function.

**Can I just make the function timeout longer?** No. Size and memory limits fail
independently of time, and a per-request download and launch is overhead you pay on every
cold start regardless of the timeout.

**Does running it in a container make it undetectable?** No tool is undetectable. A
container is the right place to unpack the browser once and keep it warm, but it does not
fix your IP reputation, your account quotas, or your timing. You supply those.

**How do I avoid downloading Firefox on every invocation?** Bake it into the image at
build time, or point `INVPW_BINARY_PATH` at a binary already on disk. On a stateless
function neither fully helps, because the local disk does not persist between calls.

**What is the smallest thing that works?** A single long-lived worker: launch once inside
a `with InvisiblePlaywright(...) as browser:` block, then consume a job queue inside that
block so the browser is reused for the life of the process.

## Sources

- This project's [installation](installation.md) and [configuration](configuration.md)
  pages, for the measured engine download and unpacked sizes and the cache and binary-path
  environment variables.
- The container walkthroughs in this set, linked above, for the build-time unpack pattern.
- Playwright's own [`Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for the stock API this wrapper hands back unchanged.
- General serverless platform limits on package size, memory and execution time, which are
  documented per platform and are what the browser weight runs into.

**See also:** [how to use invisible_playwright in Docker](how-to-use-invisible-playwright-in-docker.md)
for the container build, [running Playwright in Docker undetected](how-to-run-playwright-docker-undetected.md)
for the fingerprint tells a headless container adds, and
[what a datacenter container gives away](playwright-docker-detection.md) for the machine
signals no stealth layer touches.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The weight that keeps it out
of a small function is the same weight that makes it look real.*
