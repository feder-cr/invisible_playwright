---
title: 'Playwright "Page Crashed" Error'
description: "Playwright's page-crash error hides two very different failures: a content process the OS killed for memory use, and an actual Firefox engine bug. How to tell them apart, and what each one implies for a long-running job."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 25
---


# Playwright "Page Crashed" Error

A page crash and an out-of-memory kill produce the same event in Playwright:
[`page.on('crash')`](https://playwright.dev/python/docs/api/class-page#page-event-crash)
fires, and the next call you make throws. Playwright's own docs describe the event as
what happens "when a page crashes... for example because of over-allocating memory,"
which is honest but incomplete: over-allocating memory and an actual engine bug are not
the same failure, they do not have the same fix, and treating them as one problem is how
a memory-sizing issue gets misdiagnosed as a Firefox regression, or the other way around.

This page is about telling the two apart, using what each one leaves behind, and what
each implies for a job that runs browsers for hours rather than minutes.

## Two failures, one event

Firefox is multi-process: a parent process and one or more content processes, one per
site instance under Fission. `page.on('crash')` fires when a content process, the one
actually rendering your page, goes away. It fires for exactly the same reason whether
the process died because the operating system killed it, or because the process itself
hit a bug and terminated on its own. Playwright cannot tell you which; it only knows the
process it was talking to no longer exists.

**Cause A: the content process was killed for using too much memory.** On Linux this is
the kernel's OOM killer, invoked when the system is critically low on memory and has to
free some by force. Mozilla's own bug tracker has multiple confirmed reports of exactly
this pattern: Firefox running headless can continuously allocate memory until "sometimes
get killed by OOM condition," and a Docker container with a small memory ceiling produces
a "reproducible crash." A related and Linux-specific variant shows up only in containers:
Firefox uses `shm_open()` against `/dev/shm` for shared memory, that mount defaults to a
tiny 64MB inside most container runtimes, and Mozilla's own bug tracker documents that
shared-memory allocations "can SIGBUS if there's not enough space in `/dev/shm`." Windows
has its own version of the same pressure, the working set and page file, with the same
outcome: the OS decides the process cannot have more memory and ends it.

**Cause B: the engine itself crashed.** A real bug, a segfault, an assertion failure, a
null dereference somewhere in Firefox's own code, unrelated to how much memory was
available. Firefox's crash reporter, built on Breakpad, catches signals like this and
writes a minidump before the process exits, stored, per
[Mozilla's own crash reporter documentation](https://firefox-source-docs.mozilla.org/toolkit/crashreporter/crashreporter/),
in a `minidumps` subdirectory of the profile.

## The one diagnostic that actually separates them

This is the concrete, checkable difference, and it comes from something more basic than
either browser or Playwright: what a process can and cannot do when it receives `SIGKILL`.
An OOM kill on Linux is delivered as `SIGKILL`, the one POSIX signal no process can catch,
block, or handle, by definition. A process killed with `SIGKILL` gets no chance to run
its own crash handler, which means Firefox's Breakpad-based reporter never runs and
**no minidump is written**. An actual engine bug, a segfault or an assertion failure,
raises a signal Breakpad *can* catch, so it does catch it, and a minidump appears.

That gives you a checklist item that needs no guessing:

- **No minidump in the profile's `minidumps` directory after the crash:** the process
  was killed from outside, almost always memory pressure. Look at the OS, not the code.
- **A minidump exists:** the process crashed on its own. Look at the code, not the
  memory graph.

## Diagnostic checklist

Work through these in order; the first one that gives you a clear answer is usually the
whole diagnosis.

1. **Attach a crash listener before you need it.** `page.on('crash', lambda p:
   print("crashed:", p.url))`. Without it, all you ever see is the downstream
   `TargetClosedError`, and [that error alone tells you nothing about which of several
   causes produced it](playwright-targetclosederror-causes.md).
2. **Check for a minidump.** Absent means killed externally; present means an engine bug,
   and the dump is the artifact to actually debug from.
3. **Check the OS log for an OOM kill.** On Linux, `dmesg` or `journalctl -k` after the
   crash; the kernel logs which process it killed and why. On Windows, the Event Viewer's
   System log around the crash timestamp.
4. **If you're in a container, check `/dev/shm` size** (`df -h /dev/shm`). The 64MB
   container default is too small for a real browser process; raise it, commonly to 2GB
   for headroom, rather than treating the resulting SIGBUS as a browser bug.
5. **Graph RSS memory across the run, not just at the moment of death.** A steady climb
   that ends at the crash is a resource-sizing problem. A crash with memory flat and
   unremarkable right up to the event is not; that is cause B, and no amount of extra
   headroom fixes it.
6. **Note whether the same input reproduces it.** Cause A depends on accumulated state,
   how many pages and contexts ran before it, how long the process had been alive, so the
   same single page rarely reproduces it in isolation. Cause B is usually tied to a
   specific page or a specific operation and reproduces on that input alone, independent
   of how long the browser had been running.

## What each one implies for a long-running job

The two causes point at different parts of your system, and fixing the wrong one wastes
real time.

**If it's memory pressure (cause A):** this is a capacity-planning problem, not a Firefox
defect. The usual sources are unclosed pages or contexts accumulating across a long-lived
browser instance, too much concurrency for the memory actually available, or, per the
Mozilla headless-mode reports above, memory that simply climbs over a long enough session
regardless of what your script does. The fix is operational: recycle the browser or
context on a schedule rather than running one instance indefinitely, cap concurrent
pages to what the box's memory actually supports, close every page and context your
script opens, and, in a container, give `/dev/shm` real headroom. None of this is
something a stealth patch touches, and nothing about running a patched engine changes
how much memory a page needs to render.

**If it's an engine bug (cause B):** capacity is not the issue and adding memory will not
help. This needs isolating the specific page or operation that triggers it, from the
minidump if you have symbols to read it with, or failing that from bisecting which step
in your script precedes the crash. It is a genuine "this build has a bug on this input"
problem, worth reporting with the reproduction, not a sizing knob to turn.

## Where a reproducible identity actually helps here

`invisible_playwright` does not fix page crashes, and nothing about the engine-level
patches this project makes touches memory management or crash handling; that would be a
correctness claim this page is not making. What a fixed seed does buy you is the same
thing it buys for [`TargetClosedError` in
general](playwright-targetclosederror-causes.md): a crash that happens on a specific page
under a specific identity replays with that identity pinned, so cause B's "does the same
input reproduce it" check in the checklist above is actually answerable instead of a
guess.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("crash", lambda p: print("content process crashed:", p.url))
    page.goto("https://example.com")
```

Run it again with `seed=42` and you get the identical browser identity; if the crash
follows the input rather than the identity, that already tells you something about which
cause you are looking at.

## Conclusion

`page.on('crash')` is one event standing in for two unrelated failures. An OS-level
memory kill leaves no minidump because `SIGKILL` cannot be caught, and it points at
capacity: unbounded concurrency, unclosed pages, a container's `/dev/shm` limit, or
memory that simply grows across a long session. An actual engine crash leaves a minidump
because Breakpad caught a signal it can handle, and it points at a specific input, not at
how much memory was available. Check for the minidump first; it is the fastest fork in
the diagnosis, and it comes from how Unix signals work, not from anything Playwright or
Firefox added on top.

## Short answers to the questions that lead here

**What does "Page crashed" mean in Playwright?** The content process Playwright was
talking to is gone. Playwright's `crash` event fires either way; it does not distinguish
an OS-level kill from an actual engine bug.

**How do I tell an OOM kill from a real Firefox crash?** Check for a minidump in the
profile's `minidumps` directory. `SIGKILL`, which is how OOM kills are delivered, cannot
be caught by any process, so no minidump is written. A minidump's presence means the
engine crashed on its own and caught the signal that caused it.

**Why does this only happen in Docker?** Usually `/dev/shm`. Container runtimes default
it to 64MB, Firefox uses it for shared memory via `shm_open()`, and Mozilla's own bug
tracker documents the resulting SIGBUS when it runs out. Raising the container's shared
memory allocation is the fix, not a code change.

**Will more RAM fix a page crash?** Only if the cause is memory pressure. If a minidump
was written, the process crashed on its own regardless of memory available, and more RAM
will not change that.

**Does invisible_playwright prevent page crashes?** No, and this page does not claim it
does. It is engine-level Firefox realness; it does not change how Firefox manages memory
or handles a genuine engine bug. A fixed seed makes a crashing run reproducible, which
helps you diagnose it, not avoid it.

**Should I retry automatically after a page crash?** Only after you know which cause you
hit. Retrying an OOM-driven crash without addressing the memory pattern just delays the
next one; retrying a genuine engine bug on the same input will usually reproduce it again.

**How long should a browser instance run before I recycle it?** There is no universal
number; it depends on your memory ceiling and how much your script accumulates per page.
Graph RSS across a real run first, per the checklist above, rather than picking a
duration blind.

## Sources

- Playwright's own [`page.on('crash')` event documentation](https://playwright.dev/python/docs/api/class-page#page-event-crash),
  retrieved 2026-08-30, for the event's definition and stated cause.
- Mozilla, [Firefox Source Docs: Crash Reporter](https://firefox-source-docs.mozilla.org/toolkit/crashreporter/crashreporter/),
  retrieved 2026-08-30, for the Breakpad-based minidump mechanism and its storage
  location in the profile.
- Mozilla Bugzilla, [bug 1587698, Firefox continuously allocates memory, which results
  in an OOM crash when headless is enabled](https://bugzilla.mozilla.org/show_bug.cgi?id=1587698),
  retrieved 2026-08-30.
- Mozilla Bugzilla, [bug 1464690, Reproducible crash when running in memory-constrained
  Docker container](https://bugzilla.mozilla.org/show_bug.cgi?id=1464690), retrieved
  2026-08-30.
- Mozilla Bugzilla, [bug 1245239, Shared memory allocations can SIGBUS if there's not
  enough space in /dev/shm](https://bugzilla.mozilla.org/show_bug.cgi?id=1245239),
  retrieved 2026-08-30.
- POSIX `signal(7)` semantics for `SIGKILL` (uncatchable, unblockable, and unhandleable
  by any process), general Unix signal-handling behavior underlying the minidump-absence
  diagnostic above.

**See also:** [Playwright TargetClosedError: the causes and the fixes](playwright-targetclosederror-causes.md),
for the three Firefox/Juggler causes behind the more generic close error, including a
different content-process crash pattern seen on Windows; and
["Execution context was destroyed"](execution-context-destroyed.md), for the usually
benign navigation-timing error that a crash listener helps you avoid confusing this with.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. Memory management and
crash handling are inherited from Firefox unchanged; nothing here is a stealth feature,
and this page does not claim otherwise.*
