---
title: "Headless vs headful: what is actually being detected"
description: "Headlessness is rarely what actually gets detected. What gets detected is the collection of signals that usually accompany it, and a headful browser on the same server still has almost all of them."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 4
---


# Headless vs headful: what is actually being detected

The usual advice is that headless gets caught and headful is safer, so run headful if
you can afford the resources. It points in roughly the right direction for the wrong
reason, and the wrong reason costs people a lot of time.

**Headlessness is rarely what gets detected.** What gets detected is the collection of
things that usually accompany it, and a headful browser on the same server has almost
all of them.

## What a page can actually observe

Run a fingerprint page in both modes on the same machine and diff the report. The
differences that show up are these, and none of them is a flag saying "headless":

- **No GPU**, so WebGL reports a software renderer.
  [What those strings mean](webgl-renderer-strings.md).
- **A small font set**, because the machine is a server.
  [Why installing more is not the fix](headless-fonts-differ.md).
- **No audio device**, so the audio values fall back to defaults.
  [The seven values](audiocontext-fingerprinting.md).
- **No speech voices**, because nothing installed a speech engine.
  [Which describes the operating system](speech-synthesis-voices.md).
- **A screen with no taskbar** and a default resolution.
  [The relationships that have to hold](screen-size-headless-tells.md).
- **Permission prompts that never had a human to answer them.**
  [The two answers that have to agree](permissions-api-consistency.md).

Now move that same server to headful mode. Every item on that list is still true. You
have changed the rendering path and not the machine.

That is why "switch to headful" sometimes fixes a block and often does not: it fixes
the rendering-path differences and nothing else.

## The rendering path really does differ, though

Two facts worth having, because they are concrete and checkable.

**In Chromium, headless has been a different binary.** Recent Playwright versions
default headed runs to Chrome and headless runs to a separate headless shell. A
different implementation composites and paints differently, which changes both what is
rendered and when state transitions are observable. Google has spent real effort making
the newer headless behave like the desktop browser, and the obvious tells from a few
years ago are largely gone, but "a different binary" is a meaningful sentence.

**In Firefox it is one binary with a flag**, which sounds better and is not free. The
flag still puts the browser on a different path: no widget tree, software-only
rendering, and timing that differs from a browser drawing to a real surface. Same
executable, different behaviour.

So in both engines the honest statement is: headless is not a property that is checked,
it is a mode that changes several things that are.

## The third option nobody mentions

There is a way to get the real rendering pipeline without a visible window, and it is
what this project does when you ask for `headless=True`.

The browser is launched **headed** and the window is hidden, by a different mechanism on
each platform:

- **Windows.** The browser cloaks its own windows through the compositor, so they render
  on the real GPU and never appear on screen, in the taskbar or in the switcher.
- **macOS.** The window is kept fully transparent with occlusion checks pinned, so the
  system does not stop drawing it.
- **Linux.** A private virtual display is started for the session and the browser is
  pointed at it, because X11 and Wayland have no per-window equivalent that keeps the
  GPU rendering.

The point of all three is the same: stay on the code path a visible browser uses.

Being straight about the limits, because they matter:

- On Linux this needs `Xvfb` present on the machine.
- A virtual display **does not conjure a GPU**. On a server with no graphics hardware
  the render is still software, and
  [the claim you make about the GPU still has to match the pixels](renderer-string-vs-render.md).
  This removes the headless code path, not the datacenter.
- The window exists, so the process uses more memory than a true headless run.

## This was latent for a while, and worth being honest about

The Windows and macOS hiding described above is not how this project's `headless=True`
worked from the start. For several releases, across two different Playwright versions,
`headless=True` on Windows rendered the browser window on the real desktop anyway -
visible, with a taskbar entry, indistinguishable from a headful run except that nobody
had asked for one. macOS raised outright. Only Linux, through a virtual display, ever
actually hid anything.

The cause was a scope mistake, not a missing feature: the hiding mechanism operated at
the *thread* level, on the assumption that a child process launched with no explicit
desktop inherits the calling thread's desktop. It does not - it inherits the parent
*process's* desktop, so the browser's own child processes stayed on the visible one
regardless of what the launching thread had been moved to. An automated test suite
that happened to spawn its own worker process with the desktop set explicitly at that
same process level passed throughout, which is exactly why this went unnoticed: the
thing validating the behaviour and the thing shipping it were not using the same
mechanism.

The fix is the compositor-level cloak described above, set on the window itself rather
than on any thread or process, which is also why it needs to live in the browser
binary: only the window's own owning process can set that attribute. Validated
afterward against a visible, headful window on the same machine: identical fingerprint
surface (no `visibilityState`, focus, canvas or WebGL tell), a real GPU-composited
screenshot, and a passing result on a commercial detector that specifically checks for
masked headless state. A per-platform automated check now asserts the underlying
window attribute directly - the cloak flag on Windows, the transparency and occlusion
state on macOS - rather than trusting a screenshot alone.

**The same fix closed a second, unrelated-looking bug for free.** An earlier hiding
approach had put the browser's main process on one virtual desktop and left its
sandboxed content processes on a different one by default. Ordinary page loads never
noticed. A page that triggered a cross-process navigation - handing the active tab from
one content process to another mid-session - did notice: the window being reparented
expected both processes on the same desktop, found them split across two, and the tab
crashed. Because the compositor-level cloak keeps every process on the single real
desktop and hides at the window level instead, that split stopped existing as a
possibility, and the crash went away as a side effect of fixing something else
entirely. It's a reminder that "which mechanism hides the window" and "which processes
can actually talk to each other" are not as separate as they look.

## How to find out which one is your problem

Do not reason about it. Measure it, in this order.

1. Run your target in headless on the server. Note what happens.
2. Run it headful on the same server, with a virtual display if needed. If the outcome
   is identical, the mode is not your problem and you can stop tuning it.
3. Run it on your laptop, headless. If that works and the server does not, the machine
   is the difference, not the mode.
4. Open a fingerprint page in both modes on the server and diff the two reports field by
   field. Whatever differs is the entire real-world difference between the modes for
   you.

Most people skip step four and spend a week on step two.

## Short answers to the questions that lead here

**Is headless mode detectable?** Modern headless is not detectable as a property. What
is detectable is the environment it usually runs in, plus rendering differences that
vary by engine.

**Is headful safer for scraping?** Somewhat, and much less than people expect. On the
same server it changes the rendering path and leaves every hardware tell in place.

**Does `--headless=new` fix it?** It closes the older, obvious Chromium differences. It
does nothing about the GPU, the fonts, the audio device or the screen.

**Why does my script work locally and fail in CI?** Almost always the machine rather
than the mode. Compare the fingerprint reports, not the modes.

**Do I need Xvfb?** On Linux, for a headed run without a desktop, yes. On Windows and
macOS this project hides the real window instead.

**Does running headful cost much more?** More memory and a display server on Linux. If
your target does not care about the mode, it buys nothing, which is why step one is
finding out whether it cares.

**See also:** [Playwright in Docker](playwright-docker-detection.md), which is the same
question asked about the container instead of the mode, and
[the checklist for being detected on one site](playwright-detected-as-bot.md), where
this sits at step three.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. `headless=True` here means headed and hidden, which
is a decision made specifically because the headless code path is observable.*
