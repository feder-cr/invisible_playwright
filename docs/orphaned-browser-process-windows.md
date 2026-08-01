---
title: "Why a killed test runner leaves Firefox processes behind on Windows"
description: "A clean exit never leaked a browser process. A killed test runner did, every time, because the code path that was supposed to clean up never ran at all - and the first fix for it solved a problem that didn't exist."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 10
---


# Why a killed test runner leaves Firefox processes behind on Windows

A bug report said orphaned Firefox processes were piling up on a Windows CI box, and
pointed at "any path where teardown doesn't run cleanly - a timeout, an exception out
of the `with` block, a killed test runner." That description covers several genuinely
different code paths, and it turned out only one of them actually leaks. Chasing the
wrong one first is the useful part of this story.

## The first fix solved a problem that didn't exist

The initial theory: something in the exception path skips cleanup. The fix that
theory produces is a careful one - stamp a token into the browser's environment when
it launches, reap anything carrying that token in a `finally` block.

Before trusting it, the same scenario was run as a controlled comparison: four
launches, interleaved, half with the new cleanup path and half without, each one
ended by raising out of the `with` block rather than exiting normally. Result:
**zero survivors in both arms.** The exception path was never the leak. Playwright's
own `__exit__` handles it correctly whether or not any extra cleanup code is there
to help.

The fix was real code, reviewed, tested, and aimed at a path that had never been
broken.

## The path that actually leaks doesn't run any cleanup code at all

The original report's own detail said what was actually happening, in the part that
is easy to read past: several of the runs that showed survivors had **timed out**,
meaning the test runner process itself was killed from outside. Reproduced directly -
launch a session, send an external kill signal to the runner process, then count
what's left running: multiple survivors, consistently, across repeated attempts.

The reason no amount of code inside the wrapper can fix this: when the parent process
is killed, `__exit__` never executes. There is no exception to catch, no `finally`
block that runs, no teardown code path at all, because the process that would have
run it is already gone. A fix that lives inside `_teardown()` cannot address a
scenario where `_teardown()` is never called.

## The fix has to live in the kernel, because that's the only thing still there

If nothing inside the process tree survives to clean itself up, the mechanism that
does the cleaning has to be something that survives independently of that tree. On
Windows, that's a **job object**: browser processes are bound to a job created with
`KILL_ON_JOB_CLOSE`. The wrapper process holds the only handle to that job. When it
ends, whether by a clean return, an exception, or being killed outright, the
operating system closes the handle and the job's own termination kicks in - every
process still in it goes down, without anything in user code needing to run first.
Processes spawned later by the browser (its own content processes) join the same job
automatically, so nothing new has to be tracked as the session goes on.

Measured after the fix, same external-kill probe: zero survivors.

## Killing by proof, not by guesswork

A second design question sits underneath the fix: once you're cleaning up
after a killed process, how do you know which processes are actually yours?

The tempting shortcut - "anything named firefox.exe that showed up after we
started" - eventually kills a browser that belongs to a different, healthy,
concurrent session, which is a worse failure than leaving one of your own leaked
processes running: a leaked process is recoverable, a wrongly-killed one belonging
to someone else usually isn't.

The actual identification is exact rather than heuristic. Each session generates a
random token and stamps it into its own browser's environment at launch; child
processes inherit environment variables from their parent by default, so every
content process carries it too. Cleanup only ever touches a process that can be read
and positively shown to carry that exact token. A process whose environment can't be
read is left alone rather than assumed to be a match.

## A known limit, and a test that passed for the wrong reason

This entire mechanism is Windows-specific, which is also exactly where the bug is:
on Linux, the process Playwright launches directly is the browser, and Playwright's
own handle already covers it. There's nothing analogous to add there.

One test written for the identification logic is worth mentioning on its own,
because it passed the first time for a reason that had nothing to do with what it
claimed to check. The test asserted that a process with an empty token never
matches. It passed - with the matching guard entirely removed. The reason: a process
that never had the environment variable set at all reads back as `None`, and `None`
is not equal to an empty string either way the guard is written, so the test could
not tell "correctly rejected" apart from "the check never ran." It was rewritten
around a process whose token is deliberately set to the empty string, which is the
only input that actually forces the comparison to happen and can therefore fail.

## What to check in your own setup

If you see orphaned browser processes on Windows specifically, and not on Linux, the
timing question below settles it in one step:

1. Kill your own automation's parent process (task manager, or an external `taskkill`)
   mid-session, deliberately, in a throwaway run.
2. Immediately list processes matching your browser's binary name.
3. If any remain, the leak is happening on a path where no in-process cleanup code
   ever runs, no matter how it's written - the fix has to bind the browser to a
   kernel-level construct that survives the parent's own death, not to a `finally`
   block anywhere in your code.

## Short answers to the questions that lead here

**Does `try/finally` around a Playwright session prevent orphaned processes?**
Only for the paths where your process is still alive to run the `finally` block at
all. It does nothing for a kill signal sent to the runner itself.

**Why does this only happen on Windows?** On Linux, the process your code launches
directly is the browser. On Windows, closing that gap needs an OS-level construct -
a job object - because nothing in user space survives a killed parent to do the
cleanup.

**Is `psutil` actually required, or optional?** It has to be a hard dependency here.
An optional reaper is absent exactly on the runs that need it, and absent silently,
which is the worst way for a safety mechanism to fail.

**How do you avoid killing someone else's browser process by mistake?** By matching
an exact, per-session random token stamped into the environment rather than by name
or start time. Anything that can't be positively confirmed is left alone.

**See also:** [why an attached debugger makes automation detectable](debugger-timing-detection.md),
another case in the automation layer itself rather than anything the target page does,
[how to test whether your setup is actually working](how-to-test-bot-detection.md),
for the same discipline of measuring a claim before trusting it that caught the first,
unnecessary fix here, and [why one launch in six was randomly slow](slow-browser-launch-timeout-budget.md),
another reliability bug in the same layer with the same lesson: every piece involved
can be individually correct and the whole still isn't bounded.

## Sources

- This project's own diagnosis, the controlled comparison that ruled out the
  exception path, the job-object fix, and the regression test suite that locks the
  killed-runner case in.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on a leak whose real fix only showed up after the
first, plausible-looking one was measured and found to be fixing nothing.*
