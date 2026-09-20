---
title: "Playwright headless vs headed: what detectors see"
description: "Headless vs headful: headlessness is rarely what gets detected. It is the hardware and rendering signals around it, and a headful server keeps almost all of them."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 4
---


# Playwright headless vs headed: what detectors see

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
default headed runs to Chrome and headless runs to
[a separate headless shell](https://playwright.dev/docs/browsers#chromium-headless-shell).
A different implementation composites and paints differently, which changes both what is
rendered and when state transitions are observable. Google has spent real effort making
the newer headless behave like the desktop browser, and the obvious tells from a few
years ago are largely gone, but "a different binary" is a meaningful sentence.

**In Firefox it is one binary with a flag**, which sounds better and is not free. Passing
[`-headless`](https://hacks.mozilla.org/2017/12/using-headless-mode-in-firefox/) still
puts the browser on a different path: no widget tree, software-only rendering, and
timing that differs from a browser drawing to a real surface. Same executable,
different behaviour.

So in both engines the honest statement is: headless is not a property that is checked,
it is a mode that changes several things that are.

## The third option nobody mentions

There is a way to get the real rendering pipeline without a visible window, and it is
what this project does when you ask for `headless=True`.

The browser is launched **headed** and the window is hidden, by a different mechanism on
each platform:

- **Windows.** A fresh desktop object is created for the session and the browser process
  is created *on* it, so the whole tree - launcher, parent, GPU and content processes -
  lives on a desktop nobody switches to. It renders on the real GPU and never appears on
  screen, in the taskbar or in the switcher. The browser binary is not involved in the
  hiding at all.
- **Linux.** A private virtual display is started for the session and the browser is
  pointed at it, because X11 and Wayland have no per-window equivalent that keeps the
  GPU rendering.

The point of both is the same: stay on the code path a visible browser uses, and hide
the screen rather than the window.

Being straight about the limits, because they matter:

- On Linux this needs `Xvfb` present on the machine.
- A virtual display **does not conjure a GPU**. On a server with no graphics hardware
  the render is still software, and
  [the claim you make about the GPU still has to match the pixels](renderer-string-vs-render.md).
  This removes the headless code path, not the datacenter.
- The window exists, so the process uses more memory than a true headless run.

## This was latent for a while, and worth being honest about

This project's `headless=True` did not always hide the window on Windows and macOS the
way it does now. For several releases, across two different Playwright versions,
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

The first fix was a compositor-level cloak, set on the window itself from inside the
browser binary, because only the window's own owning process can set that attribute.
It worked, and it was validated against a visible, headful window on the same machine:
identical fingerprint surface, a real GPU-composited screenshot, a passing result on a
commercial detector that specifically checks for masked headless state.

It was replaced anyway, for a reason that has nothing to do with whether it worked: it
was a patch inside the browser on a surface that has nothing to do with fingerprinting,
and the fewer of those a modified browser carries, the less there is to diverge from
the original. The current mechanism puts the hiding back where the operating system
already offers it. A desktop object is created per session and the browser *process*
is created on it - the process, not the thread, which is exactly the scope the latent
bug above had got wrong. Every child inherits it. Nothing in the browser knows it is
hidden, because from where it stands it is not: it has a desktop, a compositor and a
GPU like any other window. Validated the same way, same seed hidden against headed:
focus, visibility, inner and outer size, screen position, screen geometry, pixel ratio
and the WebGL renderer string all identical, one window on the session's desktop and
none on the interactive one.

**Two things a separate desktop changes, and why they are handled rather than hoped
away.** A browser's process sandbox assumes its parent lives on the interactive
desktop. Its GPU process cannot parent the compositor window across desktops under the
default sandbox level, and its content processes above a certain level are placed on
the sandbox's own window station. Left alone, the first means a software renderer under
a hardware renderer string, and the second means a tab that dies on the first
cross-process navigation - handing the active tab from one content process to another
mid-session - because the window being reparented expects both processes on the same
desktop and finds them split across two. Both were measured, and both are closed by
two sandbox settings the session applies only when it actually created a desktop:
never from the platform name, always from the fact. An automated check asserts the
result directly - a headed window found on the interactive desktop as the control, then
a hidden one absent from it and present on its own, still rendering - rather than
trusting a screenshot alone.

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

## Conclusion

Headless is not a property a detector checks. It is a mode that changes a handful of
properties detectors do check, and on the same machine a headful browser keeps nearly
all of them: the GPU, the font set, the audio device, the screen, the permission
history. None of that comes from the rendering path, which is why switching modes
without switching machines fixes so little. What actually closes the gap is keeping the
real, headed rendering pipeline and hiding the window instead of skipping it, on
whichever platform you are running. Diff a fingerprint page across both modes on your
own machine before you tune anything; whatever field moves is the entire real
difference for your case.

## Short answers to the questions that lead here

**Is headless mode detectable?** Modern headless is not detectable as a property. What
is detectable is the environment it usually runs in, plus rendering differences that
vary by engine.

**Is headful safer for scraping?** Somewhat, and much less than people expect. On the
same server it changes the rendering path and leaves every hardware tell in place.

**Does `--headless=new` fix it?** It closes the older, obvious Chromium differences. It
does nothing about the GPU, the fonts, the audio device or the screen.

**Why does my script work locally and fail in CI?** Almost always the machine rather
than the mode. Compare the fingerprint reports, not the modes. The full version of this
is [why a Playwright script works locally and fails in the cloud](why-playwright-works-locally-fails-in-cloud.md).

**Do I need Xvfb?** On Linux, for a headed run without a desktop, yes; see
[running headful on a Linux server with Xvfb](run-invisible-playwright-headful-server-xvfb.md).
On Windows and macOS this project hides the real window instead.

**Does running headful cost much more?** More memory and a display server on Linux. If
your target does not care about the mode, it buys nothing, which is why step one is
finding out whether it cares.

## Sources

- Playwright's own documentation on
  [the Chromium headless shell](https://playwright.dev/docs/browsers#chromium-headless-shell),
  retrieved 2026-08-29, for headed runs defaulting to Chrome and headless runs defaulting to
  a separate binary.
- Chrome for Developers' own documentation on
  [Chrome's Headless mode](https://developer.chrome.com/docs/chromium/headless), retrieved
  2026-08-28, for the unification of headless and headful on the same codebase and the
  retirement of the old, separately-implemented headless.
- Mozilla's own documentation on
  [using headless mode in Firefox](https://hacks.mozilla.org/2017/12/using-headless-mode-in-firefox/),
  retrieved 2026-08-29, for the single Firefox binary launched with the `-headless` flag
  rather than a second, separately-built implementation.
- This project's own fix history for the Windows/macOS hiding mechanism and the
  cross-process desktop-split bug it also closed, verified against a visible headful run
  on the same machine and against a per-platform check on the underlying window attribute.

**See also:** [Playwright in Docker](playwright-docker-detection.md), which is the same
question asked about the container instead of the mode, and
[the checklist for being detected on one site](playwright-detected-as-bot.md), where
this sits at step three.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. `headless=True` here means headed and hidden, which
is a decision made specifically because the headless code path is observable.*
