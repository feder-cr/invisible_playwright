---
title: "Playwright \"Executable Doesn't Exist\" After Install"
description: "The most common first-run Playwright error, and its Docker, CI and serverless variants: a missing playwright install step, an OS mismatch, or missing system libraries the binary needs to start."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 24
---


# Playwright "Executable Doesn't Exist" After Install

```
Error: browserType.launch: Executable doesn't exist at /root/.cache/ms-playwright/chromium-1140/chrome-linux/chrome
```

Playwright ships as two separate pieces that people reasonably expect to be one:
installing the `playwright` package gives you the client library, and the actual browser
binaries are a second download, fetched into a cache directory by a separate command.
"Executable doesn't exist" almost always means that second step never ran, ran for a
different platform than the one launching the browser, or ran and then had its output
deleted or never persisted. The message even names the exact path it looked for, which
is the fastest way to tell the three apart.

## Cause 1: the browsers were never downloaded

The library and the binaries are decoupled on purpose, so `pip install playwright` or
`npm install playwright` alone leaves you with a client and no browser to drive:

```bash
playwright install          # every browser Playwright supports
playwright install chromium # just one, faster for CI
playwright install firefox
```

This is overwhelmingly the first-run case, and it is what the error message is pointing
at directly: the path it names is exactly where `playwright install` would have put the
binary, and it is not there because that command never ran on this machine.

**How to confirm it:** check whether `playwright install` appears anywhere in your setup
script, Dockerfile, or CI workflow. If it does not, that is the fix, full stop.

This project splits the same way and for the same reason, with its own command for the
step: [the engine is fetched separately from the package](installation.md), so a fresh
environment that installed one and not the other lands on this error from the other
direction.

## Cause 2: a Docker or cross-platform build produced binaries for the wrong OS

A subtler variant: the install step did run, but it ran on a different operating system
than the one that later tries to launch the browser. Downloading browsers on your
development machine and copying that cache into a Linux container, or building a Docker
image on one architecture and running it on another, produces a cache directory that
exists, has files in it, and does not have an executable that runs on the target
platform. The error looks identical because from Playwright's point of view the file at
the expected path is simply not there in a runnable form for this OS.

**How to confirm it:** run `playwright install` as an explicit step inside the same
Dockerfile or CI job that later launches the browser, not assuming a cache
copied in from elsewhere is compatible. Playwright's official images run this step
during the image build specifically so the browsers match the container's own platform.
Once it launches, a container introduces a second and unrelated set of problems that
have nothing to do with paths:
[what an image is missing that a desktop has](how-to-use-invisible-playwright-in-docker.md)
is the next thing to read, not this page.

## Cause 3: missing system dependencies, which is a different error wearing the same clothes

On a fresh Linux CI runner or a minimal container image, the browser binary itself may
be present at the exact path Playwright expects and still fail to start, because the
shared libraries it links against are not installed. This produces a launch failure that
gets reported and searched for alongside "Executable doesn't exist," even though the
underlying gap is missing OS packages, not a missing binary.

```bash
playwright install --with-deps chromium   # binary + the system libraries it needs
```

Playwright's own documentation recommends exactly this combined form for CI and
container environments, specifically because installing the browser and installing its
dependencies are two separate concerns that both need to happen before a launch will
succeed.

**How to confirm it:** if the binary file demonstrably exists at the path named in the
error (check with a plain file listing) and it still will not launch, the failure is
dependencies, not a missing download, and `playwright install-deps` (or the combined
`--with-deps` form) is the fix rather than re-running the browser download.

## Cause 4: the cache directory is wiped or redirected between install and launch

CI systems and serverless platforms frequently run each step in a fresh, ephemeral
filesystem, or with a home directory that differs between the build stage and the run
stage. If `playwright install` runs in one stage and the launch happens in a different
stage, container, or function invocation that does not share the same
`~/.cache/ms-playwright` (Linux), `~/Library/Caches/ms-playwright` (macOS), or
`%USERPROFILE%\AppData\Local\ms-playwright` (Windows) path, the install's output never
reaches the process that needs it, and the error is identical to the browsers never
having been installed at all.

**How to confirm it:** check whether the install step and the launch step share a
filesystem, or whether a serverless platform resets the filesystem between invocations.
Serverless environments in particular often need the browser cache baked into the
deployment package or pulled from a persistent location at cold start, rather than
downloaded fresh on every invocation.

## Diagnostic checklist

1. **Read the exact path in the error.** It tells you which browser and which cache
   location Playwright looked in, which narrows the search immediately.
2. **Check whether that path exists at all.** An empty or absent cache directory points
   at causes 1, 2, or 4. A binary that exists but will not run points at cause 3.
3. **Confirm the install step and the launch step run in the same environment**, same
   OS, same filesystem, same user's cache directory.
4. **In Docker, run `playwright install` inside the Dockerfile**, targeting the
   container's own platform, rather than copying a host-downloaded cache into the image.
5. **On a fresh Linux CI runner, use `playwright install --with-deps`** rather than
   `install` alone, so missing system libraries are ruled out in the same step.
6. **On serverless platforms, confirm the browser cache survives between the build and
   the invocation**, since some platforms reset the filesystem on every cold start.

## What invisible_playwright does and does not touch here

This wrapper downloads and caches its own patched Firefox engine on first use, verified
against a checksum shipped in `invisible-core`, which sidesteps the "did `playwright
install` run" question specifically for the engine this project ships. It does not
change how Playwright itself resolves executables for any other browser, and the same
environment mismatches above (wrong OS in a Docker build, a filesystem that resets
between CI stages, missing system libraries) apply identically to any browser process
launched in that environment, patched or stock. Pointing `INVPW_BINARY_PATH` at a binary
you already manage yourself skips the automatic download entirely if your deployment
already solves this problem another way.

## Conclusion

"Executable doesn't exist" is Playwright telling you it looked for a browser binary at a
specific path and found nothing runnable there, and the exact path in the message is the
fastest route to the cause: never downloaded, downloaded for the wrong platform,
downloaded but missing the system libraries it needs to start, or downloaded somewhere
that did not survive to the process that needed it. Confirm which of the four before
changing anything, since the fix for each is different and none of them is guessing.

## Short answers to the questions that lead here

**What does "Executable doesn't exist" mean?** Playwright looked for a browser binary at
a specific cache path and did not find a runnable one there. The message names the exact
path it checked.

**Does `npm install playwright` or `pip install playwright` include the browsers?**
No. The package is the client library; `playwright install` is a separate, required step
that downloads the actual browser binaries.

**Why does this happen in Docker specifically?** Usually because the browser cache was
downloaded on a different OS than the container runs, or the `playwright install` step
was left out of the Dockerfile entirely. Run the install inside the same build that
produces the final image.

**I can see the binary file exists. Why does it still fail?** That points at cause 3,
missing system dependencies rather than a missing binary. Use `playwright install
--with-deps` instead of re-running a plain install.

**Why does it work locally but fail on CI or serverless?** The install step and the
launch step may not share a filesystem, or a serverless platform may reset the
filesystem between invocations, so the browsers downloaded during setup never reach the
process that runs them.

**Does invisible_playwright need `playwright install` too?** No for its own bundled
engine, which it downloads and caches itself on first use. Yes if your code also drives
any other Playwright browser directly in the same project.

## Sources

- Playwright's own documentation, [Browsers](https://playwright.dev/python/docs/browsers),
  for the cache directory locations per OS and the recommended `playwright install
  --with-deps` form for CI and container environments.
- Microsoft Playwright issue [#5767](https://github.com/microsoft/playwright/issues/5767),
  an early report of "Executable doesn't exist" from a CI environment missing the
  install step.
- Microsoft Playwright issue [#33673](https://github.com/microsoft/playwright/issues/33673),
  the same error from a Linux chromium binary path, illustrating the exact-path pattern
  the message follows.
- Microsoft Playwright issue [#30371](https://github.com/microsoft/playwright/issues/30371),
  a report of the error appearing after a Playwright version upgrade changed the expected
  binary revision.

**See also:** [Playwright in Docker: it runs, and still gets blocked](playwright-docker-detection.md),
for the separate problem of a container that launches fine and still describes a machine
no real person owns, and [Slow browser launch: a per-request timeout is not a budget](slow-browser-launch-timeout-budget.md),
another launch-time reliability issue with its own distinct cause.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. This wrapper manages its
own engine download so this specific error class does not apply to it directly, but the
same platform and filesystem mismatches apply to any browser process in the same
environment.*
