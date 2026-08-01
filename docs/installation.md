---
title: "Installation"
description: "Install the wrapper from PyPI, then fetch the patched Firefox binary separately - a one-time, sha256-verified download. Supported on Windows, Linux and macOS."
parent: "Documentation"
nav_order: 1
---


# Installation

Two steps, and they are separate on purpose: the Python package is small, the browser
is not.

```bash
pip install invisible-playwright
python -m invisible_playwright fetch      # one-time ~238 MB download (~544 MB unpacked), sha256-verified
```

`pip install` gets you the wrapper and its API. `fetch` downloads the patched Firefox
binary itself, verifies it against a sha256 shipped inside `invisible-core`, and caches
it. You only pay that cost once per engine version - later runs use the cached copy
without a network call.

## Supported platforms

**Windows x86_64**, **Linux x86_64 / arm64**, **macOS arm64 / x86_64**.

On macOS the app is ad-hoc signed, not notarized. If Gatekeeper complains the first
time it runs, clear the quarantine flag once:

```bash
xattr -dr com.apple.quarantine /path/to/cached/Firefox.app
```

`invisible-playwright path` prints the exact cached location if you need it.

## Where the engine is cached, and how to move it

By default the engine lives in a per-user cache directory. To put it somewhere else -
another drive, a shared location, a path your CI already caches between runs - set
`INVISIBLE_PLAYWRIGHT_CACHE_DIR` before the first `fetch`:

```bash
export INVISIBLE_PLAYWRIGHT_CACHE_DIR=/mnt/big/engines
```

If you already have a compatible binary on disk and want to skip the download
entirely, point `INVPW_BINARY_PATH` at it instead. The full list of environment
variables, including the ones for corporate networks and CI, is in
[Configuration](configuration.md).

## Verifying the install

```bash
invisible-playwright doctor    # checks every cached engine against its recorded seal
invisible-playwright version   # prints wrapper, core and engine versions
```

Both commands, and the rest of the CLI, are in the [CLI reference](cli-reference.md).

## Next

[Quickstart](quickstart.md) has the two-line change from plain Playwright, and the
sync and async examples.
