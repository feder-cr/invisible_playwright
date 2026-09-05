---
title: "First Firefox launch fails on Windows, then works"
description: "A freshly extracted Firefox can fail its very first launch on Windows and then succeed every time after. What we measured, why, and how to handle it."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 29
---


# First Firefox launch fails on Windows, then works

A freshly extracted Firefox directory can fail to launch on Windows on its very first attempt and then succeed every single time after, with no code change and no reinstall. We measured this directly: the failure tracks the executable's file path, not the directory's contents, and a Windows registry key tied to that path is the strongest lead we found.

## The symptom

Extract a Firefox build to a brand new path on Windows and launch it. Sometimes the launch fails immediately, with no window, no error dialog, and nothing useful beyond a bare launch timeout. Try again at the exact same path with the exact same binary, no changes at all, and it works. Not just once: every later launch at that same path succeeded, every time, in every test we ran.

The rate is not "every first launch fails." Most first launches at a new path succeed cleanly. When one does fail, though, it fails fast, in under a second, and the very next attempt at the identical path always worked in our testing.

## What we ruled out

Our first instinct was that the extracted directory itself was incomplete on that one run. It wasn't: we took a directory that had already launched successfully many times, copied it whole to a new path we'd never used, and its first launch there failed the same way, with byte-for-byte identical contents to a working copy elsewhere. We also ruled out the per-launch profile Playwright creates, clearing every cache we could find, and re-downloading the build from scratch. None of it made any measurable difference. Whatever was failing lived outside both the directory and the profile.

## What the registry key points to

Firefox's startup on Windows goes through a launcher stage before the browser process starts, keeping state outside the installation directory. We found a registry key, `HKCU\Software\Mozilla\Firefox\Launcher`, holding timestamped entries keyed by the executable's own file path. A path never launched before had no entry; the same path, launched again, did.

That correlation matches what we observed: new path, no entry, occasional immediate failure; same path again, entry present, no failure. Two halves of this are worth keeping apart. The launcher stage itself is documented by Mozilla: Firefox on Windows bootstraps through a launcher process that, unlike every other process in the browser, "is not launched by the parent process, but rather launches it", and it exists so that protections like DLL blocklisting are in place before the browser's main thread runs. Mozilla treats launcher failures as a real enough category to ship a telemetry ping for them. The registry key and its per-executable timestamps, on the other hand, are our own observation: we found them, we can read them, and we have not found Mozilla documentation that describes them, so treat that half as a measured correlation rather than an architectural claim.

## The process that dies is the launcher, not the browser

The detail worth carrying into your own debugging: the process that exits is the launcher, not the browser you're trying to drive. It exits within a fraction of a second and writes no log anywhere we looked. "The browser printed nothing" is the wrong way to describe it, because the browser was never created. From Playwright's side, a dead launcher looks exactly like a plain launch timeout, with none of the specifics that would point at what actually went wrong.

## How to recognise this specific failure

- It only happens on the first launch at a path you have never used before, never on a path you have already launched successfully.
- It fails fast, typically well under a second, not after waiting out a timeout.
- There is no log file and no window, even briefly.
- Retrying the exact same launch, same path, same everything, succeeded the second time in every case we saw.

If your failure doesn't match this shape, slow rather than instant, or the same path failing on every attempt, you're looking at something else. [Slow browser launch: a per-request timeout is not a budget](slow-browser-launch-timeout-budget.md) and [Playwright "Executable Doesn't Exist" After Install](playwright-executable-doesnt-exist.md) cover failure modes that look similar but have different causes.

## Checking the registry key yourself

```
reg query "HKCU\Software\Mozilla\Firefox\Launcher"
```

Run it before and after a launch at a new path. A new entry appearing is the signal to look for; it won't tell you why the launcher failed the first time, only that the path is now on record.

## Practical handling: a warm-up launch and one retry

Two things worked for us. Right after extracting a build to a new path, launch it once and close it immediately, before any real automation depends on it, so the launcher can record the path while nothing is watching the clock.

```python
from playwright.sync_api import sync_playwright

def launch_with_warmup(playwright, executable_path, **launch_kwargs):
    # First launch at a brand new path: throwaway, just to let the
    # launcher record state for this executable. Close it immediately.
    warmup = playwright.firefox.launch(executable_path=executable_path, **launch_kwargs)
    warmup.close()
    return playwright.firefox.launch(executable_path=executable_path, **launch_kwargs)
```

Second, treat the very first real launch of any new install path as worth exactly one retry, no more. The second attempt succeeded every time we tested it, so a loop with backoff solves a problem you don't have and only hides a genuinely broken install behind repeated waiting.

```python
def launch_retry_once(playwright, executable_path, **launch_kwargs):
    try:
        return playwright.firefox.launch(executable_path=executable_path, **launch_kwargs)
    except Exception:
        return playwright.firefox.launch(executable_path=executable_path, **launch_kwargs)
```

## What remains unexplained

We have a correlation, not a proven mechanism. We don't know why an absent registry entry makes the launcher exit instead of simply creating the entry itself, the way a first-run step normally would. We also haven't ruled out a competing, more mundane explanation: antivirus or SmartScreen scanning a freshly written executable can delay its first execution at a new path, independent of anything Firefox's own code does.

Treat the warm-up launch and single retry as a practical workaround, not a fix for a confirmed cause. If what you're chasing turns out to be a preferences problem instead, [Firefox preferences that silently do nothing](firefox-prefs-not-applying.md) covers a different failure that can look similar from the outside.

## Short answers to the questions that lead here

**Why does Firefox fail to launch the first time but work the second time?**
In our testing, first launches at a brand new path occasionally fail within a fraction of a second, while the second launch at the same path always succeeded. The failure correlates with a Windows registry key that has no entry yet for a path never launched before.

**Is this a Firefox bug?**
We haven't proven that. What we have is a measured correlation between a missing registry entry and an occasional fast failure on first launch, not a confirmed root cause.

**Does this happen only on Windows?**
Yes, as far as we tested. The registry key we found, under `HKCU\Software\Mozilla\Firefox\Launcher`, is Windows-specific, and we have no equivalent finding for Linux.

**How do I avoid hitting this in automation?**
Launch once and close immediately after extracting a build to a new path, before any real task depends on it, and give the first real launch of any new path exactly one retry.

**Does clearing the cache or profile fix it?**
No. Neither made any difference in our testing; the failure tracked the executable's path, not the profile or any cache we cleared.

**See also:** [Slow browser launch: a per-request timeout is not a budget](slow-browser-launch-timeout-budget.md), [Playwright "Executable Doesn't Exist" After Install](playwright-executable-doesnt-exist.md), and [Firefox preferences that silently do nothing](firefox-prefs-not-applying.md).

## Sources

- This project's own testing, September 2026: repeated extraction of a shipped Firefox build to new Windows paths, timing the process exit, and inspecting `HKCU\Software\Mozilla\Firefox\Launcher` before and after each launch.
- Mozilla, Gecko Processes, https://firefox-source-docs.mozilla.org/ipc/processes.html - the launcher process on Windows, the quoted line about it launching the parent rather than being launched by it, and its role in DLL blocklisting. Read 5 September 2026.
- Mozilla, Launcher Process Failure ping, https://firefox-source-docs.mozilla.org/toolkit/components/telemetry/data/launcher-process-failure-ping.html - the telemetry Firefox sends when the launcher cannot start the browser. Read 5 September 2026; it documents the ping's fields and says nothing about registry state, which is why the registry half above stays labelled as our own observation.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright), on a failure that only ever showed up once per path and never explained itself in its own words.*
