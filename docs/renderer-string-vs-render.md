---
title: "Your renderer string says NVIDIA. Your pixels say software."
description: "A detection flag chased in the wrong direction for a while: you can spoof what a browser says about its GPU, and you cannot spoof what the GPU actually draws. The story of finding that out the hard way."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 4
---


# Your renderer string says NVIDIA. Your pixels say software.

A story about a detection flag we chased in the wrong direction for a while, and the
general lesson underneath it, which is the most expensive one in this area:

**you can spoof what the browser says about the GPU, and you cannot spoof what the
GPU draws.**

## What happened

A commercial fingerprinting service was returning `tampering = True` on our realness
gate. The seed under test had an NVIDIA GTX 980 persona, so the first hypothesis was
obvious: something about that persona is wrong, probably the renderer string bucket.

That hypothesis was wrong, and three measurements killed it.

**A seed sweep.** Ten seeds on the same build, spanning all three vendor buckets:
four NVIDIA, three AMD, three Intel. Ten out of ten flagged. Not persona-specific,
then. Whatever it was applied to every identity equally.

**An A/B across two builds.** The previous engine release and the current one, same
rate. Not a regression introduced by the newer build.

**The same build on a different operating system.** On Windows, the same two seeds
came back `tampering = False`, with the underlying model score around 0.15 to 0.17,
well under the threshold that raises the flag.

Same code, same personas, same detector. Clean on one host, flagged on the other.

## The actual cause

The flag only appeared when the browser ran **headless on Linux**, where there is no
GPU and rendering falls to a software rasterizer.

The renderer string still claimed a GTX 980, because that is what the profile said to
claim. The pixels coming out of the canvas and WebGL pipeline were produced by a
software rasterizer, which does not draw the same thing a real GTX 980 draws.
Antialiasing edges differ, floating-point rounding differs, and the small
inconsistencies are exactly what a model trained on real hardware output learns to
notice.

So the tell was not "this fingerprint is unusual". It was "the hardware this browser
claims and the hardware that drew this image are not the same hardware".

## Why this is the expensive class of problem

Everything else in a fingerprint is a value you can set. This one is an *output*.

A renderer string is a string. A canvas hash is the result of running a rasteriser
over a shape, and to change it consistently you have to change the rasteriser or the
machine. That is why:

- Spoofing the string alone is not enough, and is arguably worse than leaving it
  honest, because it creates a claim the pixels contradict.
- The mismatch is invisible in every check that only reads values. Our own
  release gates read values for a long time before this surfaced, which is [why they now assert presence rather than absence](how-to-test-bot-detection.md).
- It is worst exactly where automation usually runs: servers, containers and CI, none
  of which have a GPU.

If you deploy browser automation to a cloud machine and it behaves differently from
your laptop, this is high on the list of reasons, and it will not show up in any
property-level audit.

## What the options actually are

There is no clean fix, and pretending otherwise would be dishonest. There are three
imperfect ones:

1. **Give the machine a real GPU.** A cloud instance with hardware acceleration, and
   a browser configured to use it. Expensive, and not available everywhere.
2. **Make the claim match the reality.** If the render is software, do not claim a
   discrete card. This is unattractive, because a software renderer is itself a
   strong server signal, but a consistent weak story beats a contradictory strong
   one.
3. **Tune the software path so its output is closer to the claimed hardware.** The
   most work, the least certain, and the direction with the most headroom.

The one thing that does not work is claiming a better GPU harder.

## The other half of the story, which is about testing

While chasing the above, the same reports carried a second flag: `high_activity`.

It was not the product. It was us, running the same detector demo repeatedly through
the same small set of exit addresses over an afternoon. The service was measuring
velocity from that address and telling us so.

Two things follow, and they cost more time than the actual bug:

- **A test harness can manufacture the signal it is measuring.** If you hammer a
  scoring endpoint from one address, you are creating the condition you are trying to
  observe.
- **Read every flag before believing the one you were looking for.** We nearly
  attributed a velocity artefact to the browser build.

Space the runs out, rotate the exit, and check whether the flag moves with the
browser or with the harness.

## What to take from it

If you are debugging something like this, the order that would have saved us time:

1. **Sweep identities before blaming one.** Ten seeds took an hour and eliminated the
   entire first hypothesis.
2. **Change one axis at a time**, and include the host operating system as an axis.
   The answer here was the host, which was not on the list of suspects at all.
3. **Read all the flags, not the one you came for.**
4. **Ask whether the signal is a value or an output.** Values you can set. Outputs
   you have to actually produce, and that is a different and much harder job.

## Short answers to the questions that lead here

**Why am I flagged as tampering when every value looks right?** Because one of the
signals is not a value, it is an output. A renderer string can be set. The pixels a
rasterizer draws cannot be, and the two can contradict each other.

**Does this only happen on Linux?** It happens wherever there is no GPU, which is most
servers and containers. It showed up for us on headless Linux and came back clean on
Windows with a real GPU.

**Will a better GPU string fix it?** No. Claiming a better card harder makes the
contradiction larger, not smaller.

**What are my options?** A machine with a real GPU, a claim that matches the software
render, or work on making the software output resemble the claimed hardware. All three
are imperfect and the first is the only easy one.

**How do I test for it?** Sweep several identities before blaming one, change one axis
at a time, and include the host operating system as an axis. That is what took us from
the wrong answer to the right one.

**See also:** [WebGL renderer strings](webgl-renderer-strings.md), for what those
strings encode and the software-rasterizer entry we shipped in our own persona pool,
and [why headless renders different fonts](headless-fonts-differ.md), which is the
same value-versus-output problem on a different surface.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The measurements above are ours: ten seeds across three vendor buckets, two engine
builds, and two host operating systems. The wrong first hypothesis is ours too.*
