---
title: "CLI reference"
description: "The invisible-playwright command line: fetch, path, version, clear-cache and doctor - what each one does and when you actually need it."
parent: "Documentation"
nav_order: 5
---


# CLI reference

The installed command is `invisible-playwright`, with a hyphen. `python -m
invisible_playwright` works identically and needs nothing on `PATH`.

```bash
invisible-playwright fetch          # download the engine if missing
invisible-playwright fetch --force  # re-download even if cached
invisible-playwright path           # absolute path to the cached engine (downloads it if absent)
invisible-playwright version        # wrapper, core and engine versions
invisible-playwright clear-cache    # remove cached engine trees
invisible-playwright doctor         # check every cached engine against the seal
```

## `fetch`

Downloads the patched Firefox binary if it is not already cached, verifying it
against the sha256 shipped inside `invisible-core`. This is the one-time step covered
in [Installation](installation.md). `fetch --force` re-downloads even if a cached
copy already passes verification - useful if you suspect a corrupted cache without
wanting to hunt for the cache directory yourself.

## `path`

Prints the absolute path to the cached engine binary, downloading it first if it is
not present. Useful for pointing another tool - a debugger, a separate script - at
the exact binary this wrapper would launch.

## `version`

Prints three version numbers together: the wrapper (`invisible-playwright`), the core
(`invisible-core`), and the engine (the Firefox build itself). All three are meant to
move together; if you ever see them disagree in a way that looks wrong, `doctor` is
the next command to run.

## `clear-cache`

Removes cached engine trees. Use it to reclaim disk space, or as a clean-slate step
before `fetch --force` if `doctor` reports a problem `fetch --force` alone did not fix.

## `doctor`

Checks every cached engine against its recorded seal - the same integrity check
`fetch` does at download time, run again against what is already on disk. Run this
first if a session fails to launch and the error does not point at your own code.

## See also

[Installation](installation.md) for the initial `fetch`, and
[Configuration](configuration.md) for the environment variables that change where
the engine is cached or which binary gets used.
