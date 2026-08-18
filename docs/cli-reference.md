---
title: "CLI reference"
description: "The invisible-playwright command line: two commands, fetch and version, and what each one covers."
parent: "Documentation"
nav_order: 5
---


# CLI reference

The installed command is `invisible-playwright`, with a hyphen. `python -m
invisible_playwright` works identically and needs nothing on `PATH`.

```bash
invisible-playwright fetch    # download the engine if missing, check every cached
                              # one against the seal, print the path
invisible-playwright version  # wrapper, core and engine versions, and where the
                              # engine is cached
```

## `fetch`

Makes the engine present and correct, then prints where it is.

It checks every cached engine tree against the seal shipped inside
`invisible-core` **before** downloading anything. That ordering is the point: a
tree that no longer matches the seal is the case worth catching, and it is
invisible to a plain "download if missing" that only looks at whether a file is
there. Anything that does not match is reported on stderr and left alone; the
sealed engine is fetched and verified against its sha256.

The last line on stdout is the absolute path, by itself, so this is the scripting
form:

```bash
FIREFOX="$(invisible-playwright fetch)"
```

which is better than asking for a path separately, because it guarantees the
thing it names actually exists and matches the seal.

Running it when everything is already correct is cheap and does nothing.

## `version`

```
invisible_playwright 0.6.0
invisible_core       18.12.0   (declared: ==18.12.0)
engine               firefox-18  Firefox 151.0  build 20260724001949
seal                 f294a96ae4ec  [.../invisible_core/seal.json]
cache                /home/you/.cache/invisible-playwright
```

This is the output to paste into a bug report. Two lines are worth knowing about:

- if the installer's record disagrees with the core that will actually run, a
  `STALE RECORD` line appears between them. That is the state pip and `pip check`
  both call healthy, because both read the record and neither reads the files.
- `cache` is where the engine trees live. Deleting a directory there is how you
  reclaim the space; there is no subcommand for it, on purpose - see below.

## What happened to the other four

The command line used to have six entries: `fetch`, `fetch --force`, `path`,
`version`, `clear-cache`, `doctor`. Each was a step somebody had to know to take,
in a package whose whole promise is that the browser handles itself. None of the
behaviour was lost:

| gone | where it went |
|---|---|
| `doctor` | `fetch` does it on every run. It was the thing most worth doing and the thing least likely to be typed, which is the worst combination a subcommand can have. |
| `fetch --force` | unnecessary once every run verifies. A tree is replaced because it does not match the seal, not because a flag was passed. |
| `path` | `fetch` prints it as its last line, and unlike `path` it guarantees the tree is there and correct. |
| `clear-cache` | deliberately not folded in. The cache root belongs to `invisible_core`, not to this package, so pruning "trees no seal points at" would delete an engine this package did not put there. `version` prints the location. |

The `tag` argument went with them. The seal decides which engine a given build
runs, and the engine check refuses anything else, so a tag on the command line
could only ever name something that would then be rejected.

## See also

[Installation](installation.md) for the initial `fetch`, and
[Configuration](configuration.md) for the environment variables that change where
the engine is cached or which binary gets used.
