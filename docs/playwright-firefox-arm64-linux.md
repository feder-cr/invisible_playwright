---
title: "Running Playwright Firefox on ARM64 Linux"
description: "Playwright Firefox on ARM64 Linux: what actually ships per architecture, the emulation trap, memory limits on small boards, and how to verify at runtime."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 46
---


# Running Playwright Firefox on ARM64 Linux

ARM64 Linux runs Playwright's Firefox natively today, on real ARM servers and on boards like the Raspberry Pi, but "it works" depends on which binaries actually exist for your architecture, whether you are running native or emulated, and how much memory the device gives a browser process. Check the compatibility table for your Playwright version before building anything around it.

## Check what exists for your architecture before you design anything

The single most useful step before writing any code is confirming that the browser you plan to drive actually ships an arm64 build at the Playwright version you intend to pin. Playwright's own system requirements name the platforms in one line: "Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86-64 or arm64)", read on 5 September 2026. That sentence covers the distribution and the architecture but not the per-browser split, and the split is where arm64 surprises live, so the check that settles it is running the install for the browser you need on the machine you have and seeing whether a binary lands.

Two cheap checks before anything else: run `dpkg --print-architecture` or `uname -m` on the target host to know what you're actually building for, and try the install step in a throwaway container for that exact architecture, rather than trusting a result from your development laptop.

## x86_64 and arm64 are different downloads of the same version

A given Playwright version is not one universal binary. The install step detects the host architecture and fetches a build matched to it, and arm64 and x86_64 are separate downloads of the same version. Copying a browser cache from an x86_64 machine into an arm64 container does not give you a working browser: the files exist at the expected path and cannot run there, the failure covered in [Playwright "Executable Doesn't Exist" After Install](playwright-executable-doesnt-exist.md).

The safe pattern: run the install step, whether that's `playwright install` or this project's own automatic engine download, on the same architecture that will later launch the browser, never on a different machine with the output copied in afterward.

## The emulation trap

An x86_64 image will often start under emulation on an arm64 host, and the reverse is true too: Docker's `--platform` flag and QEMU user-mode emulation exist specifically to make a foreign-architecture binary runnable. It starts, but it also runs meaningfully slower than a native build, and the slowdown is not uniform across operations.

That matters for anything timing-sensitive. Page load timeouts, wait conditions tuned against a native run, and any check assuming a script finishes inside a fixed window all get less predictable under emulation. [A slow launch caused by an unbounded sequence of per-step timeouts](slow-browser-launch-timeout-budget.md) is a different problem, but the same instinct applies here: prefer the native build for your real architecture, and treat emulation as a fallback, not a default.

## Memory-constrained boards versus real ARM servers

"ARM" covers two very different machines here. A Raspberry Pi or similar board typically has a small, fixed amount of RAM, storage slower than a server SSD, and no active cooling, so sustained CPU load can throttle the whole board, not just slow the browser. A cloud arm64 server is a different animal: server-class memory, no thermal throttling, and none of a small board's SD-card I/O ceiling.

Both are "ARM64 Linux," and the architecture check above passes identically on both, so an architecture mismatch is not what bites you on a small board. Running several browser contexts concurrently is where the two diverge hardest: a board with a few gigabytes of RAM runs out of headroom for concurrent contexts far sooner than a server does, well before any CPU limit.

If you're scaling out concurrent work rather than running one context at a time, [Run invisible_playwright concurrently with asyncio](run-invisible-playwright-concurrently-asyncio.md) covers bounding concurrency with a semaphore, which matters more on a memory-constrained board than on a server with room to spare.

## Container base images for arm64

Most current Debian and Ubuntu base images are published as multi-architecture manifests, so `FROM debian:bookworm-slim` on an arm64 host pulls the arm64 variant automatically without you naming it. That's the simplest path: build on the architecture you intend to run on, and let the base image resolve itself. Docker's own documentation defines a multi-platform build as "a single build invocation that targets multiple different operating system or CPU architecture combinations", and warns that "emulation with QEMU can be much slower than native builds, especially for compute-heavy tasks" - which is the paragraph to remember before you decide to build arm64 images on an x86 laptop. The image below is a starting point to adapt rather than a recipe we have built for your application.

```dockerfile
FROM --platform=linux/arm64 debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip ca-certificates
RUN pip install invisible-playwright
```

The `--platform` flag pins the build explicitly rather than relying on the Docker daemon's default, which matters if you build on one machine and deploy to another. For a real GPU, font set, or screen presented to the browser instead of a bare container's defaults, see [How to run Playwright in Docker without getting detected](how-to-run-playwright-docker-undetected.md); it applies the same way on arm64, only the base image and engine differ by architecture.

## Verifying at runtime which architecture you actually got

Do not trust the tag on your base image alone; confirm what actually launched.

```
file /path/to/firefox/firefox-bin
uname -m
```

`file` reports the real architecture of the binary regardless of what image tag you built from, which is the check that matters if a multi-stage build or a copied cache is in the picture. The same idea applies to a binary Playwright resolved for you:

```python
import subprocess
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = p.firefox.executable_path
    result = subprocess.run(["file", exe], capture_output=True, text=True, check=True)
    print(exe)
    print(result.stdout.strip())
```

One caveat: Python's own `platform.machine()` inside your automation does not prove emulation is absent. It reports the interpreter's own architecture; under QEMU-based emulation, an x86_64 interpreter correctly reports x86_64 for itself while the host does the emulation invisibly. Check the host's real architecture independently instead, your cloud provider's listed instance type, or `uname -m` run outside any emulated container.

## What this project ships, and does not

This project currently builds and publishes engines for Linux on both x86_64 and arm64, and for Windows on x86_64. There is no macOS build; macOS support was discontinued after the engine build for it ended, so a macOS host has no engine to download here regardless of its own CPU architecture.

## Short answers to the questions that lead here

**Does Playwright work on ARM64 Linux, including a Raspberry Pi?**
Yes, for browsers with native arm64 builds, especially Chromium and Firefox. A Raspberry Pi can run them, but its limited RAM and lack of active cooling become the practical ceiling long before any architecture problem does.

**Can I just run an x86_64 image on an ARM server instead?**
It usually starts under emulation, but runs slower and less predictably, which matters for anything timing-sensitive. Prefer a native arm64 build whenever you have the choice.

**How do I know if my browser is running natively or under emulation?**
Run `file` on the resolved browser binary to see its real architecture, and compare it to the host's own `uname -m` or your cloud provider's listed instance type, rather than trusting the image tag alone.

**Does invisible_playwright support ARM64?**
Yes, on Linux: separate engine builds for x86_64 and arm64, alongside a Windows x86_64 build. There is currently no macOS build.

**Is WebKit's ARM64 Linux support as mature as Chromium and Firefox?**
Playwright's own system requirements name Debian 12 and 13 and Ubuntu 22.04, 24.04 and 26.04 on "x86-64 or arm64" without splitting the list per browser, so confirm the browser you need actually downloads on your distribution before you build around it.

**See also:** [How to run Playwright in Docker without getting detected](how-to-run-playwright-docker-undetected.md), [Slow browser launch: a per-request timeout is not a budget](slow-browser-launch-timeout-budget.md), and [Run invisible_playwright concurrently with asyncio](run-invisible-playwright-concurrently-asyncio.md).

## Sources

- Playwright, Installation and system requirements, https://playwright.dev/python/docs/intro - "Debian 12 / 13, Ubuntu 22.04 / 24.04 / 26.04 (x86-64 or arm64)", plus the Windows and macOS lines quoted here. Read 5 September 2026.
- Docker, Multi-platform builds, https://docs.docker.com/build/building/multi-platform/ - a multi-platform build is "a single build invocation that targets multiple different operating system or CPU architecture combinations", and "emulation with QEMU can be much slower than native builds, especially for compute-heavy tasks". Read 5 September 2026.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright), which ships and tests its own engine on both Linux architectures, not only the one most laptops happen to have.*
