---
title: "Why AI browser agents have their own timing signal"
description: "An LLM loop emits machine-regular action gaps and instant pointer jumps - a behavioral signal no fingerprint fixes. Where pacing and dwell time live in your code."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 10
---


# Why AI browser agents have their own timing signal

A stealth engine can make your browser read as a genuine Firefox down to the TLS
handshake, and an AI agent driving it can still stand out for a reason that has nothing
to do with the browser at all: the rhythm of the loop that controls it.

An LLM agent runs a think-act cycle. It looks at the page, decides on an action, emits
the action, waits for the result, and looks again. That cycle has a shape, and the shape
is measurable. The gaps between actions cluster around the model's latency, the actions
themselves land effectively instantly, and the pointer arrives at each target without a
human's approach, overshoot or correction. None of that is in the fingerprint. It is a
separate signal, orthogonal to every value a fingerprinting page reads, and a site can
check it on its own.

This page is about that signal: what produces it, why a clean fingerprint does not
touch it, and where the fix has to live, which is in your code and not in the engine.

## What the timing signal actually is

Behaviour is a fingerprint of a different kind. A fingerprinting page reads static
properties: the GPU string, the font list, the canvas hash. A behavioural check reads
the stream of events a session produces over time: when clicks happen relative to each
other, how a pointer travels between two points, how long a value takes to type, whether
anything is read before it is acted on.

A human session is full of small irregularities. There is a pause to read that varies
from a second to several. The pointer curves toward a target, arrives a little off, and
corrects. Typing has an uneven cadence with the occasional longer gap. Time is spent on
the page doing nothing measurable.

An agent loop, left to its own timing, produces almost none of that. The interesting
part is that this is true even when the engine underneath is perfect, which is what makes
it worth a page of its own.

## Why a perfect fingerprint does not touch it

invisible_playwright is designed to look like a real browser driven by a real person, and
that is why it passes most detection checks: the engine fingerprint, the TLS handshake and
the driver layer all read as a genuine Firefox rather than an automated one. That is a real
result and it is most of what people are blocked for.

It is also not everything. The same honesty that makes the product worth using means being
clear about the edges: on its own it does not fix IP reputation, per-account quotas, rate
limits, or behaviour. Those are supplied by you, a clean exit and human pacing among them.
The timing signal is squarely in that last category. The engine controls what the browser
*is*; it does not control the cadence at which your loop decides to act, because that cadence
is produced above the browser, in the code that calls it.

Put differently: a fingerprint is a photograph and behaviour is a motion study. Making the
photograph perfect does nothing to the motion study, and the two are read by different parts
of a detection system. This is the same reason the [testing method that catches false
passes](how-to-test-bot-detection.md) lists behaviour among the things no in-page suite
covers.

## What the loop leaks: gaps and pointer jumps

Two specifics, because they are the ones an agent produces without meaning to.

**Inter-action gaps that cluster.** Between one action and the next sits a wait for the
model to respond. That wait is not random the way a human pause is; it clusters around
whatever the model's latency happens to be, run after run. A distribution of gaps that is
tight and repeatable, rather than the broad, ragged spread a person produces, is itself the
tell. It does not matter that the individual number looks plausible. The *shape* of the
distribution is the signal.

**Actions that land instantly, and pointers that jump.** When an agent decides to click a
coordinate, the naive path is to dispatch the click at that coordinate with no travel and
no dwell. The pointer teleports from wherever it was to the target, and the action fires
with zero read time in front of it. A form filled field by field with no gaps, a click on
an element the instant it appears, a pointer that never passes through the space between two
targets: each is a motion no hand produces. The [detected-on-one-site checklist](playwright-detected-as-bot.md)
reaches this at step five, after the machine and the automation tells, because it is what is
left once the browser itself is clean.

invisible_playwright does smooth one piece of this for you: a click arcs the pointer to the
target on a Bezier curve rather than teleporting, so an individual click's *path* is
human-shaped. What it cannot smooth is the *cadence* between your actions and the dwell time
in front of them, because your loop, not the browser, decides when the next action happens.

## Pacing and pointer motion live in your code, not the engine

The consequence is a division of labour, and it is a clean one.

The engine owns the browser identity: the fingerprint, the TLS layer, the driver surface.
You own the rhythm: how long to dwell before acting, how much to vary the gap between
actions, whether to read the page before touching it. No engine can supply the rhythm for
you, because the rhythm is produced by the loop calling the engine, and that loop is yours.

Concretely, that means adding pacing and dwell to the code between your actions rather than
expecting the browser to add it underneath. Here is the minimal shape, using the real
two-line launch and standard Playwright methods on the returned browser:

```python
import random
import time
from invisible_playwright import InvisiblePlaywright


def dwell(low=0.6, high=2.4):
    """A varied pause, the kind reading and deciding actually produces."""
    time.sleep(random.uniform(low, high))


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    dwell(1.2, 3.0)                 # read before acting, not act on arrival
    page.click("#open")             # the engine arcs the pointer on a Bezier curve

    dwell()
    for ch in "a search phrase":    # per-key rhythm, not one instant fill
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.04, 0.19))

    dwell()
    page.click("#submit")
```

The `browser` returned by `InvisiblePlaywright` is a real Playwright `Browser`, so every
method is the one you already know; the `seed=42` makes the identity reproducible so a run
that gets flagged can be replayed exactly. The only thing added above the plain automation is
the pacing, and that is the point: it has to be added, because it is the part the engine
cannot produce for you.

A note on doing it well. Uniform delays are their own tell. `time.sleep(1.0)` between every
action just moves the cluster from "instant" to "exactly one second", and a tight cluster at
one second is as machine-regular as a tight cluster at zero. Vary the pauses, make them
depend on what the step is doing (a long read before a decision, a short gap between two
keystrokes), and avoid the same number twice. The goal is the ragged, context-dependent
spread a person produces, not a constant with a label on it.

## Measuring your own rhythm before a site does

You can read this signal off your own agent the same way a site would, which is the honest
way to know whether your pacing is doing anything. Record a timestamp at each action, then
look at the distribution of the gaps.

```python
import time

timestamps = []


def mark():
    timestamps.append(time.monotonic())


# call mark() immediately before each page action, then afterward:
gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
print("count:", len(gaps))
print("min / max:", round(min(gaps), 3), round(max(gaps), 3))
print("mean:", round(sum(gaps) / len(gaps), 3))
```

If the min and max are close together, or the mean sits right on top of your model's
typical response time, your loop is emitting the clustered distribution described above and
the pacing is not varied enough yet. A human trace has a wide spread and a long tail. This is
the same instinct as reading the fingerprint report field by field rather than trusting a
verdict: measure the actual stream, do not assume the shape. For the related case of an agent
whose pauses line up with tool latency in a devtools context, see
[timing detection through the debugger](debugger-timing-detection.md).

## Conclusion

The engine fingerprint and the agent's rhythm are two different signals, read by two
different parts of a detection system, and a stealth browser addresses the first and not the
second. invisible_playwright makes the browser read as a genuine person's Firefox, which is
why it clears most checks; the cadence of your think-act loop is produced above the browser,
so pacing, dwell time and read-before-act have to live in your own code. Add them
deliberately, vary them so they are not merely a slower constant, and measure your own trace
the way a site would. That is the honest division of labour, and it is the one that holds up.

## Short answers to the questions that lead here

**Does a stealth browser hide that I am an AI agent?** It hides that the browser is
automated. It does not hide the rhythm of your control loop, which is a separate signal you
produce above the browser.

**What is the timing signal, exactly?** The gaps between your actions cluster around the
model's latency instead of spreading like a human's pauses, and actions land instantly with
pointers that jump straight to targets. Both are visible without reading any fingerprint.

**Can invisible_playwright fix my pacing for me?** No, and no engine can. It smooths an
individual click's pointer path on a Bezier curve, but the cadence between actions is decided
by your loop, so pacing and dwell have to live in your code.

**Do I just add a fixed sleep between actions?** That helps with "instant" but creates a new
tell: a tight cluster at one second is as regular as a tight cluster at zero. Vary the pauses
and make them depend on the step.

**Will fixing the timing get me past everything?** No. Behaviour is one signal among several.
A clean rhythm on a bad IP, over quota, or against a rate limit still fails; those are yours
to supply too, a clean exit and sane request volume among them.

**How do I know if my rhythm looks robotic?** Record a timestamp at each action and look at
the spread of the gaps. If min and max are close, or the mean sits on your model's response
time, the distribution is too tight.

## Sources

- This project's own testing notes, which list behaviour and the pause shaped like model
  latency among the things no in-page suite covers.
- Standard Playwright's documented [`Browser`](https://playwright.dev/python/docs/api/class-browser),
  [`Page`](https://playwright.dev/python/docs/api/class-page) and
  [`Keyboard`](https://playwright.dev/python/docs/api/class-keyboard) methods, used unchanged
  on the browser this wrapper returns.
- The behavioural step of this set's detected-on-one-site checklist, which reaches pointer
  motion and typing cadence after the browser itself is clean.

**See also:** [what fits and what does not for AI browser agents](ai-browser-agents-stealth.md),
[why a CDP-driven agent has a tell of its own](browser-use-detection.md), and
[testing bot detection without a false pass](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine makes the browser
real; the rhythm is yours to write, and this page is the part I keep having to add back into
my own agent loops.*
