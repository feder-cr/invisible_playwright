---
title: "Firefox launches but Playwright can't drive it: packaging gap"
description: "A custom Firefox build launches and screenshots fine, yet Playwright fails at launch with TargetClosedError. The real cause is a packaging gap, not the driver."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 11
---


# Firefox launches but Playwright can't drive it: packaging gap

A Firefox build that launches, renders a page, and takes a screenshot from the command
line looks like a working build. Handing that same binary to Playwright can still fail
immediately, with an error that has nothing to do with the page and everything to do
with one folder never making it into the release.

## The symptom

`firefox --headless --screenshot` on the built binary works: a real screenshot comes
back, the process exits cleanly. Every manual and CI smoke test built around that
command passes.

Point Playwright at the exact same binary and the session dies at launch, before
anything resembling a page load, with two errors:

```
console.error: "unrecognized command line flag" "-juggler-pipe"
Error: Failed to load chrome://juggler/content/components/Juggler.js
```

followed by Playwright reporting [`TargetClosedError`](playwright-targetclosederror-causes.md).
The browser process exits or never reaches a drivable state, and no amount of
retrying, changing launch arguments, or bumping the Playwright version changes the
outcome.

## Why the existing gates didn't catch it

Every gate built around `--version` or a standalone screenshot is, by construction,
testing that Firefox itself works. Firefox itself does work. None of those gates
ever ask Playwright to connect, because they don't need to for what they're checking.

The actual automation path is a separate component entirely: Firefox's Playwright
automation layer runs as its own set of files, registered through the browser's
chrome manifest system, loaded on demand when a driver actually connects and asks for
it. A smoke test that never asks for it will never notice it's missing.

## What was actually missing

The automation layer's files exist in a developer build's output directory as loose
files, registered by their own manifest, which is in turn referenced by the top-level
chrome manifest that ships with the browser.

The step that assembles an actual **packaged** release - the archive or installer a
user downloads, as opposed to a developer's raw build output directory - works from
an explicit manifest listing exactly which files to include. That manifest listed the
browser's separate remote-protocol component, guarded by the same build flag the
automation layer is guarded by, but never listed the automation layer's own files
alongside it. The result: every properly gated, flag-enabled build still produced a
packaged release with an entirely empty automation-layer directory, while the same
build's raw developer output had the files sitting right there.

**That's the whole bug.** Not a missing feature flag, not a build failure, not
anything wrong with the code that makes the automation layer work - a packaging
manifest that assembles the shipped archive from a fixed file list, and one directory
that should have been on that list and wasn't.

## Why it stayed invisible for as long as it did

Local development and day-to-day testing runs directly against the developer build's
own output directory, where the files were always present, because packaging never
entered the picture. The divergence only exists between that output directory and
whatever a packaging step separately assembles from a file list - and nothing in the
usual local workflow ever compares the two.

Every release gate that used `--version` or a screenshot to confirm "the build
works" was, without anyone deciding this deliberately, only ever exercising the
developer-output path. The packaged artifact - the only thing an actual user ever
receives - went untested by every gate that existed at the time.

## The fix, and the more important part that came with it

The immediate fix is one manifest entry: list the automation layer's files and its
own manifest alongside the component it sits next to, under the same build-flag
guard. Re-running the packaging step without touching anything else confirms it:
the packaged archive now contains the automation layer's files, and the resulting
binary drives correctly.

The part worth generalizing is the gate that came with it, because the fix alone
doesn't prevent a repeat. The new gate launches each actual packaged release
artifact - not the developer output directory, the thing that will actually ship -
and drives it: real launch, real page, a script round-trip, checking that automation
actually connects. A build missing the automation layer fails this gate immediately
and loudly, on the exact artifact a user would receive, rather than on a proxy for it.

## What to check in your own setup

If a custom or patched browser build works from the command line but a driver like
Playwright can't connect to it specifically:

1. Confirm the failure is packaging-specific by testing the developer build's own
   raw output directory directly, before any packaging step runs. If the driver
   connects there and not to the packaged artifact, the two are diverging somewhere
   in between.
2. Check what the packaging manifest actually includes, not what the build produces.
   A build flag being enabled only guarantees the files exist somewhere in the build
   tree, not that the packaging step decided to ship them.
3. Any gate built around a standalone launch or version check cannot, by
   construction, catch this class of bug. It has to actually connect the driver to
   the specific artifact being shipped.

## Short answers to the questions that lead here

**Why does Playwright fail to launch a custom Firefox build that works fine
manually?** The most likely cause, if the failure is specifically about connecting
rather than about the page, is that the browser's automation-layer component didn't
make it into the packaged build a user actually runs, even though it exists in the
developer's own build output.

**What does `Failed to load chrome://juggler/content/components/Juggler.js` mean?**
The browser is trying to load its Playwright automation component and the file
genuinely isn't present in that build's packaged chrome registry.

**Why didn't our release gates catch this?** Because they tested that the browser
launches and renders, which a packaging bug like this one leaves completely intact -
the failure is specific to the one component a launch-only or screenshot-only gate
never exercises.

**Does this only break Playwright, or would any driver hit the same failure?** Any
driver that connects through the browser's automation layer would fail the same way,
because the missing piece is the layer itself, not anything specific to one client
library - Playwright is simply the driver that surfaced it first.

**See also:** [why one launch in six was randomly slow](slow-browser-launch-timeout-budget.md)
and [why a killed test runner leaks Firefox processes on Windows](orphaned-browser-process-windows.md),
two more cases where every obviously-relevant check passed and the actual gap was
somewhere the existing gates never looked.

## Sources

- This project's own diagnosis of the packaging manifest gap, the fix, and the
  drive-based release gate that now runs against the actual shipped artifact rather
  than a developer build output directory.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
on a build that passed every gate it had and still couldn't be driven, because none
of those gates had ever actually tried.*
