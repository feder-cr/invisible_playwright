---
title: "What are mouse-dynamics behavioural biometrics?"
description: "Mouse-dynamics biometrics score pointer velocity, curvature, acceleration and dwell across every event in a session. Why trusted events alone do not suffice."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 22
---


# What are mouse-dynamics behavioural biometrics?

Mouse-dynamics behavioural biometrics are the family of detectors that watch how a
pointer moves rather than what any one pointer event claims. They do not ask "was this
click real". They ask a statistical question over hundreds of events: does the shape of
this motion match the shape a human hand and arm produce.

This is the detection-science companion to
[the page on event trust](playwright-clicks-istrusted.md). That page is about a single
field on a single event. This page is about the distribution across the whole stream, and
about the reason a perfectly-formed event stream can still be scored as automated: the
two questions are different, and passing the first does not answer the second.

## What a mouse-dynamics classifier actually measures

A biometric classifier does not read one event and decide. It buffers the pointer trace
for a session and computes features over the whole sequence. The usual ones:

- **Velocity distribution.** Human pointer speed is not uniform. It rises, peaks before
  the target, and falls off as the hand corrects. The distribution has a characteristic
  spread and skew.
- **Acceleration and jerk.** The first and second derivatives of position. Human motion
  has a submovement structure - a large ballistic move followed by small corrective ones -
  that shows up as a specific acceleration signature near the target.
- **Curvature.** Real paths bow. They are not straight lines and they are not identical
  arcs. The amount and variability of curvature per move is a feature on its own.
- **Pause and dwell.** Where the pointer stops, for how long, before it clicks. Humans
  hesitate, overshoot, settle. The dwell-time distribution is one of the strongest
  single features in the literature.
- **Inter-event timing.** The interval between samples and the total time from first
  motion to click, taken as a distribution rather than a mean.

The key word in every line above is distribution. The classifier is fitting the spread
and correlation of these quantities against a model of human motion, then scoring how far
your session sits from it. No individual event is "wrong". The verdict comes from the
statistics of many events together.

## Why a physically-correct event stream can still fail

A stream can be correct in every field a page can inspect and still score as automated,
because the classifier measures the shape of the sequence, not the correctness of any one
event. That is the fact that surprises people, and the reason this page exists.

You can emit pointer events that are correct in every field a page can inspect - real
screen coordinates, the right button, the right modifier state, the
[trusted flag](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted) a genuine
input device sets - and still be scored as automated, because none of those fields is what
a biometric model measures. The model measures the shape of the sequence, and event-level
correctness says nothing about sequence-level shape.

Two concrete ways a technically-perfect stream fails the statistics:

- **Straight-line teleport clicks.** A driver that jumps the pointer from its last
  position to the target and clicks produces exactly two events with correct fields and a
  velocity distribution no arm can make: one instantaneous displacement, zero curvature,
  zero corrective submovement. Every field is valid. The trace is not human.
- **Metronome motion.** Even a curved path, if every session uses the same arc at the same
  cadence, collapses the variance a human produces. Real motion is noisy between
  repetitions. A generator that emits the identical curve every time has a distribution
  that is too tight, and "too consistent" is itself the anomaly.

This is the same lesson as
[the WebRTC false pass in the testing guide](how-to-test-bot-detection.md): a check that
looks at the wrong layer passes trivially. Correct event fields are necessary. They are
not sufficient, because the detector is one level up, looking at the distribution.

## What invisible_playwright supplies, and what it does not

invisible_playwright supplies the event-level half of the answer: **trusted,
operating-system-level pointer events** with real coordinates and real buttons, routed
along a Bezier-curve path rather than teleported. Be precise about the boundary, because
the honest version of the product claim lives exactly here.

That handles the necessary half: the events are indistinguishable from a physical device
at the field level, and the default motion is curved rather than a straight jump. This is
why the engine reads as a genuine Firefox at the fingerprint, TLS and driver layers, and
why it passes most detection checks - those layers are what most checks look at.

What it does **not** do is invent movement you never scripted. The path between "move the
pointer" and "click" is a policy, and the policy is the caller's. If your code issues one
`click()` and nothing else, the browser has one arc to work with; it cannot manufacture
the reading pauses, the scroll-then-hover, the overshoot-and-correct, or the
session-to-session variance that a human produces across a whole visit. The engine gives
you a human-shaped primitive. Whether the session as a whole has human-shaped statistics
is a function of how you drive it. See
[why Bezier curves are only the easy part](human-mouse-movement.md) for the full version
of this boundary.

## A runnable example, and where the policy is yours

The launch is two lines. The movement policy is the part you own.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # The engine arcs the pointer to the target on a Bezier curve and emits
    # trusted, OS-level pointer events. That is the necessary half.
    page.click("#submit")
```

That single call already beats teleport-clicking: the pointer travels a curved path and
the click carries real event fields. But one call is one arc. To give a biometric model a
session-level distribution to score, the pacing and the intermediate motion have to come
from you:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # Move to a couple of unrelated points first, with uneven dwell between
    # them - a human does not go straight to the one element that matters.
    page.mouse.move(240, 180)
    page.wait_for_timeout(420)
    page.mouse.move(610, 350)
    page.wait_for_timeout(280)

    # Hover the target, settle, then click. The pause before the click is the
    # dwell-time feature the classifier weights most heavily.
    page.hover("#submit")
    page.wait_for_timeout(300)
    page.click("#submit")
```

Every method here is stock Playwright on a real `Browser` object -
[`mouse.move`](https://playwright.dev/python/docs/api/class-mouse), `hover`,
`wait_for_timeout`, `click` all behave exactly as documented upstream. The `seed=42`
argument fixes the fingerprint so a failing run is reproducible, which is what lets you
bisect a behaviour problem instead of guessing. The point of the second example is not
that these exact numbers are human - they are an illustration. The point is that the
variance, the pauses and the intermediate targets are decisions in your code, and a
biometric model scores the distribution those decisions produce. If you need paths with
more built-in irregularity than a single arc gives, see
[the ghost-cursor style human paths](ghost-cursor-human-mouse.md), and note that
[`hover()` can quietly defeat a humanized path](hover-mouse-movement-bug.md) if the target
is already under the pointer.

## What this does not fix on its own

A trusted, curved, well-paced pointer stream fixes only the behaviour layer - it does
nothing for IP reputation, account-level rate limits or request timing, which are scored
separately. The honest caveat, stated plainly: a biometric pass is not a session pass.

Those are the layers a mouse cannot touch:

- **IP reputation.** A datacenter or already-flagged exit loses regardless of how human the
  motion is. Supply a clean residential proxy.
- **Per-account quotas and rate limits.** A hundred human-shaped sessions from one account
  in an hour is a volume signal, not a motion signal.
- **Timing and pacing at the request level.** Requests fired faster than a person could
  read the page is its own tell, separate from pointer dynamics.
- **The distribution you never scripted.** As above: the engine supplies the primitive,
  not the whole behavioural policy of the visit.

invisible_playwright is designed to look like a real browser driven by a real person, and
that is exactly why it clears the fingerprint, TLS and driver checks that most detection
relies on. It is not an evasion guarantee and no honest tool is one. When a session still
fails with a clean fingerprint, the cause is usually one of the layers above, which is the
subject of [why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

## Conclusion

Mouse-dynamics behavioural biometrics score the shape of pointer motion across many
events - velocity, acceleration, curvature and dwell, taken as distributions - not the
correctness of any single event. That is why event trust is necessary but not sufficient:
you can be perfect at the field level and still sit far from the human model at the
distribution level. invisible_playwright gives you the necessary half, trusted OS-level
events on a curved path, and leaves the movement policy where it belongs, with the caller.
Pair it with human pacing, a clean exit, and quotas that a person could plausibly hit, and
the behaviour layer stops being the thing that gives you away.

## Short answers to the questions that lead here

**What do mouse-dynamics biometrics actually measure?** The distribution of pointer
velocity, acceleration, curvature and pause/dwell across a whole session, not any single
event field.

**My clicks are trusted and I still get flagged. Why?** Because trust is an event-level
property and biometrics are a sequence-level one. A trusted stream with a non-human
velocity or dwell distribution still scores as automated.

**Does invisible_playwright pass behavioural biometrics?** It supplies the necessary
part - trusted OS-level events on a Bezier-curve path - which reads as a genuine browser at
the fingerprint and driver layers. Whether the session as a whole has human statistics
depends on how you pace and vary your driving.

**Isn't a Bezier curve enough to look human?** It is the easy half. A single identical arc
every time has too little variance, and "too consistent" is itself an anomaly. Real motion
is noisy between repetitions.

**Will a clean proxy fix a behaviour flag?** No. A proxy fixes IP reputation, which is a
different layer. Behaviour and network are scored separately and both have to hold.

**What can the browser not do for me here?** It cannot manufacture movement you never
scripted - the reading pauses, overshoot, scrolling and session-to-session variance are
your policy, not the engine's.

## Sources

- The public literature on mouse-dynamics as a behavioural biometric, which consistently
  finds dwell time, velocity profile and curvature to be the high-weight features.
- This project's own event model and cursor engine, which emit trusted OS-level pointer
  events on a Bezier path, and the boundary documented across the mouse-movement pages in
  this set: the primitive is the engine's, the movement policy is the caller's.
- The testing notes in this set on why a check at the wrong layer passes trivially, the
  same failure mode as scoring event fields when the detector reads distributions.

**See also:** [Human-like mouse movement: Bezier curves are the easy part](human-mouse-movement.md),
[Playwright isTrusted: are automated clicks real?](playwright-clicks-istrusted.md), and
[AI browser agents and stealth](ai-browser-agents-stealth.md) for the pause shaped like
model latency.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine gives you a
human-shaped primitive; the distribution across a whole session is still yours to script.*
