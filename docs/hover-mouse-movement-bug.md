---
title: "Why humanized mouse movement can fail on hover()"
description: "A Bezier curve measured on page.mouse.move() and a Bezier curve measured on page.hover() are not the same feature. On the call almost every script actually makes, the humanization can collapse to three events - a teleport with two extra samples on it."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 8
---


# Why humanized mouse movement can fail on hover()

Every guide to human-like mouse movement measures the same thing: draw a curve, add
jitter, check that the shape looks plausible. [The curve is the easy half](human-mouse-movement.md).
This page is about a failure mode one level up, found by measuring the feature on the
call scripts actually make rather than the one it's easiest to test with, and it
turned out the humanization had been validating itself against the wrong call the
whole time.

## The measurement that changed everything

`page.mouse.move()` and `page.hover()` both move the pointer. They are not the same
call underneath. `hover()` runs a hit-target check first - it needs to confirm the
element is actually there before it decides where the pointer should end up - and
that check itself moves the pointer, before the humanized path ever starts.

Driving the identical 570px displacement two ways, interleaved, same page, same
settings, made the gap impossible to miss:

| | `mouse.move()` direct | via `hover()` |
|---|---|---|
| events received | 108 (close to the ~97 the model predicts) | **3** |

The humanization was working exactly as designed - on the one call almost no real
script makes. On `hover()`, and by the same path `click()`, `dblclick()`, `check()` -
the entire way automation actually touches a page - it was emitting three events.
That is a teleport with two extra samples decorating it, not a human path.

## Why the hit-target check eats the movement

The mechanism is specific rather than mysterious. A hit-target check has to move the
pointer onto the target before it can confirm the target is really there. If the
humanized path is generated *inside* that same step, the check consumes the distance
first, and the path that runs afterward has almost nothing left to cover. The stroke
was never broken; it was just measuring a trip that had already ended.

This is worth generalising past mouse movement specifically: a feature validated
against a call your own test harness uses, rather than the call real code paths take,
can pass every internal check and still do almost nothing in production.

## The fix, and why it needed no new browser build

The fix moves path generation up a layer, so it completes before the hit-target
check's window opens rather than inside it - the same 570px stroke now produces double
digits of events through `hover()` instead of three, because the check has a real path
to intercept partway through rather than a pointer already sitting on the target.

Whether this needs a new binary comes down to one thing: do the events a script-driven
mouse move dispatches carry the same fields the engine's own generator would have
used? Checked in source before writing a line of the fix - the dispatch path takes an
explicit pressure value and an explicit input-source flag, and a plain
`page.mouse.move()` call reaches that exact path with the same arguments a generated
waypoint would carry. Nothing about trust, pressure or input source is lost by moving
generation up a layer, which is what made this shippable as an ordinary package update
instead of a new engine release.

## Two bugs that were hiding under "it looks like a curve"

Fixed alongside the layer change, because a good macro fix does not repair what the
underlying stroke gets wrong.

**Biased jitter is a measurable, systematic tell.** Noise added to a path has to be
zero-mean - equally likely up or down - or every path drawn by the same code carries
the same directional lean, which a large enough sample recovers trivially. Measured
before the fix: a consistent **+0.4976px** mean vertical bias, present on every single
path sampled. After centering the noise distribution correctly: **+0.00039px**, with
the confidence interval containing zero. The difference between "biased" and
"unbiased" here was one line, and it had been shipping the wrong one.

**Duplicate positions, measured where a detector actually reads them.** The right
place to count events is a listener on the page, not the path your own code planned -
those can disagree, and only one of them is what gets checked. Counted that way: the
duplicate-position rate dropped from **0.706** to **0.069** on the same displacement,
an order of magnitude, once measurement moved from the planned path to the wire.

## What still isn't solved, stated plainly

Not every problem here has a fix yet, and the honest thing is to say which one.

A cursor that only ever moves in a perfect curve directly toward the next click, and
sits motionless the rest of the time, has a shape at the macro level regardless of how
good any single stroke is: idle time with a suspiciously narrow distribution, and every
movement terminating on something clickable. Neither is specific to any one
implementation - it's a property of driving a page by clicking things in sequence
rather than by moving a mouse around the way a person actually does, including toward
things that are not buttons. Closing that gap is a different, larger piece of work
than fixing a single stroke, and it is not done.

There's a second cost worth naming for anyone building something like this: constants
shared by every install of the same trajectory code - the same easing curve, the same
knot layout, the same jitter distribution - are not just a generic "this looks
automated" signal. They're a fingerprint that links every account running that code to
every other one, which is a worse failure for a fleet than being flagged as automation
in the first place. A shape that varies per session, derived from something private to
that session, does not have that property; a shared constant always does.

## Short answers to the questions that lead here

**Why does my humanized mouse movement not seem to do anything?** Check which call
you're actually using. If it's `hover()`, `click()`, or anything that runs a
hit-target check first, verify the humanized path runs before that check consumes the
pointer's movement, not inside it.

**Is a Bezier curve enough for human-like mouse movement?** It's necessary, not
sufficient. The shape of one stroke and the fields on its events are both checkable
independently of each other, and neither one fixes the other.

**Does jitter need to be zero-mean?** Yes. A consistent directional bias, even a
fraction of a pixel, is measurable across enough samples and is itself a signal that
something generated the noise rather than a hand.

**Where should I measure event counts, planned path or page listener?** The page
listener. They can disagree, and the listener is what any real detection code would
also be reading.

**See also:** [Bezier curves are the easy half](human-mouse-movement.md), for the
pointer-event-field argument this page's fix depends on; and
[why an attached debugger makes automation detectable](debugger-timing-detection.md),
for another case where the automation layer itself, not the fingerprint, was the
actual problem.

## Sources

- This project's own cursor-generation rework and its before/after measurements,
  including the interleaved A/B methodology (same binary, same page, arms differing
  only by which engine generated the path) and the reproduction against a hover
  target that requires a scroll first.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on the mouse-movement bug that had been passing
its own tests by testing the wrong call.*
