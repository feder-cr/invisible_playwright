---
title: "Playwright in Docker: it runs, and still gets blocked"
description: "Playwright runs in Docker but gets blocked? The container describes a datacenter machine - no GPU, few fonts, no audio, a default screen - not automation flags."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 7
---


# Playwright in Docker: it runs, and still gets blocked

**A Playwright container that starts and renders perfectly can still get blocked,
because it describes a machine no real person owns.** WebGL reports no GPU, the font
set is tiny, there is no audio device and no speech voices, the screen is a default
nobody has, and the core and memory counts come in pairs nobody buys. Every answer is
consistent with "datacenter", and none of it changes when you fix `navigator.webdriver`.

Almost every page about Playwright and Docker is about making it start: install the
system dependencies, add the fonts, use the official image. Those pages are correct and
they solve a different problem from this one.

This is about the container that starts perfectly, renders perfectly, and gets a
different page than your laptop does. Nothing is broken. The container is simply
describing a machine that is not a person's, in about six places at once.

## What the container says about itself

Work through what a page can read, and a stock container answers every one of these
the same way:

**No GPU.** WebGL reports a software renderer, `llvmpipe` or `SwiftShader` on Linux, a
basic render driver on Windows. It is the single loudest item and no page-level patch
touches it, because it is not a lie you told, it is the truth about where you are.
There is [a longer page on what those strings mean](webgl-renderer-strings.md).

**A tiny font set.** A stripped image has DejaVu and Liberation and very little else.
[Why that is a comparison and not a count](headless-fonts-differ.md) matters here,
because the usual advice is to install more fonts, and more is not the fix. Matching
the platform you claim is.

**No audio device.** Sample rate, output latency and channel count fall back to
defaults that say there is no sound card.
[What the seven audio values are](audiocontext-fingerprinting.md).

**No speech voices.** `speechSynthesis.getVoices()` returns an empty array, because
nothing in a base image installs a speech engine.
[Which is a statement about the operating system](speech-synthesis-voices.md).

**A screen nobody has.** Default resolutions, an available height equal to the full
height because there is no taskbar, a device pixel ratio of 1.
[The relationships that have to hold](screen-size-headless-tells.md).

**Core and memory counts that come in pairs nobody buys.** A container limited to two
cores reporting eight gigabytes, or the reverse.
[Which the machine reports directly](hardware-concurrency-device-memory.md).

Six answers, all consistent with each other and all consistent with
[a browser running on a server](can-a-website-tell-you-are-on-a-server.md). None of them
is about automation. This is why fixing `navigator.webdriver` changes nothing here.

## The official image is a cohort, not a disguise

The official Playwright Docker image fixes reliability, not detection. Using the
maintained image is the right call, and it is also true that everyone else following
that universal advice has the same font set, the same library versions and the same
defaults. A font list that exactly matches a widely used CI image is not anonymous, it
is a label, and that is the consequence nobody mentions.

That is not an argument against using it. It is an argument for knowing what it does
and does not buy: it buys you a browser that starts and renders consistently, and it
does not buy you a machine that looks like somebody's desk.

## `/dev/shm`, and why it is a different problem

The other thing everyone hits in Docker is the browser crashing or hanging under load,
which is usually shared memory. Containers default to a 64 MB `/dev/shm`, and a
browser will use more than that.

```bash
docker run --shm-size=1gb ...
```

Fix it that way rather than by disabling the shared memory usage, because the flags
that disable it change how the browser behaves rather than giving it room.

This is a stability problem, not a detection one. It is in this page because it is
always in the same search, and because the two get conflated: a browser that crashes
under load looks like a browser that is being blocked, and the fix is completely
different. Establish which one you have before you start changing your fingerprint.

## What actually helps

In the order that gives the most back per unit of effort:

1. **Decide what platform you are claiming**, and make the font set, the screen values,
   the audio and the voice list all agree with it. Consistency is the thing being
   checked, not any single value.
2. **Deal with the GPU honestly.** Either give the machine one, or accept that the
   render is software and do not claim a discrete card on top of it, which is
   [a contradiction that is worse than the original problem](renderer-string-vs-render.md).
3. **Do not install fonts until you know which ones.** A larger set that belongs to the
   wrong platform is a stronger signal than a small set.
4. **Separate the layers before debugging.** Run
   [the checklist](playwright-detected-as-bot.md) in order. Most container problems are
   step three, and most people start at step seven and buy a proxy.

## What this project does about it

The engine carries its own font set rather than reading the machine's, so a container
and a desktop produce the same font list, and it is the list that belongs to the claimed
platform. The screen values, the audio values and the voice list come from the same
seeded profile, so they describe one machine rather than six unrelated defaults.

What it does not fix, and this is the honest limit: the GPU. A container with no
graphics hardware still renders in software, and while the strings can say otherwise,
the pixels are drawn by the rasteriser that is actually there. That gap is written up
in its own page because it is the one thing on this list that a browser cannot solve.

## Short answers to the questions that lead here

**Why does Playwright work locally but get blocked in Docker?** Because the container
answers differently about the machine: no GPU, few fonts, no audio device, a default
screen. None of those change when the automation flags do.

**Does the official Playwright Docker image avoid detection?** It avoids the startup
problems. Its font set and defaults are shared by everyone who uses it, which is the
opposite of blending in.

**Should I install more fonts in my container?** Only the ones belonging to the platform
you claim. Adding a large mixed set makes the mismatch clearer.

**What does `--shm-size` fix?** Crashes and hangs under load. It has nothing to do with
detection, and conflating the two sends people to rewrite a fingerprint when they needed
a flag.

**Can I get a real GPU in a container?** Sometimes, with hardware passthrough on a host
that has one. It is the only real fix for the software renderer, and it is not available
everywhere.

**Is headless the problem?** Rarely by itself. What gets detected is what usually
accompanies it, and every item on that list is also true of a headful browser in the
same container.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
which is the order to work in, [three ways to make Playwright undetected](playwright-stealth-levels.md),
which explains why none of the page-level tools reach any of this, and
[why the same code works locally but fails in the cloud](why-playwright-works-locally-fails-in-cloud.md),
which is this same gap on a bigger host.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The font set travels with the engine here, which is
the only reason a container and a desktop can agree.*
