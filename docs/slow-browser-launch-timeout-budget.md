---
title: "Slow browser launch: a per-request timeout is not a budget"
description: "One launch in six was randomly slow even though every network request had its own timeout. The fix is a total step budget, not a shorter per-request timeout."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 4
---


# Slow browser launch: a per-request timeout is not a budget

When a browser launch is randomly slow with no error, the usual cause is a
multi-step network operation where every individual call has its own timeout but
nothing bounds the sequence as a whole. The fix is a total budget for the step, not a
shorter per-request timeout.

This is a real case of that shape. An intermittent slow launch is one of the more
frustrating classes of bug, because every individual piece of code involved is
provably bounded and the whole still is not. Below is the symptom, the wrong question
the per-request timeout was answering, and the one-line fix once the right question
was asked.

## The symptom

Roughly one launch in six spent up to 35 seconds on a step that normally finishes in
under a second: resolving information from the network connection being used, before
the browser itself even starts. Every other launch was fast. Nothing about the slow
ones looked different from the outside - same machine, same code path, no error.

## Every piece involved already had a timeout

The step in question tries a short list of endpoints in sequence until one answers,
and every single request in that list already had a timeout set. Ten seconds each,
bounded, tested, working exactly as configured.

That's the trap. A timeout on each request answers "how long do I wait for this one
server to respond." It says nothing about "how long am I willing to wait for the step
as a whole to finish." With three endpoints in the list and a ten-second timeout on
each, the worst case was three sequential ten-second waits plus connection overhead -
correctly bounded per-request, and entirely unbounded as a caller-facing guarantee.
Every piece was well-behaved. Their sum was not something anyone had actually put a
number on.

## The fix is a budget, not a longer or shorter timeout

Tuning the per-request number doesn't touch the real problem, because the real
problem is that no single number existed for the thing that actually matters: how
long the caller launching the browser is willing to wait, total, no matter how many
endpoints the step ends up trying.

The fix adds exactly that: a budget for the whole step, separate from the per-request
timeout, with the per-request value now computed as whatever is smaller - the
original timeout, or whatever's left of the budget. A slow first endpoint eats into
the time available for the second instead of adding on top of it. The step now
finishes or gives up inside a single, known ceiling, regardless of how long the
endpoint list grows in the future.

The failure message changed with it: instead of a plain timeout error, it now states
how many endpoints were actually reached before the budget ran out. That distinction
matters operationally - a caller staring at a failure needs to know whether the
network path is broken or whether the deadline given to it was simply too tight for
the number of hops involved, and a bare timeout can't tell the two apart.

## The general shape, because it isn't specific to this code

Anywhere a step tries N things in sequence, each with its own timeout, and nothing
wraps the sequence itself: the worst case is the sum of the individual timeouts, not
any one of them. That worst case grows quietly every time an endpoint or a retry is
added to the list, without anyone touching a number that looks related to it. A
per-item timeout is a promise about one item. A budget is a promise about the caller's
actual patience, and only the second one is what a launch, a request pipeline, or any
multi-step network operation actually needs to bound.

The difference in one table:

| | Per-request timeout | Total step budget |
|---|---|---|
| What it bounds | one endpoint's wait | the whole step, end to end |
| Worst case | N x timeout | one fixed ceiling |
| When you add an endpoint | worst case grows silently | ceiling is unchanged |
| Answers | "how long for this server" | "how long the caller will wait" |

## What to check in your own setup

If a multi-step network operation is occasionally slow despite every individual call
having a timeout, add up the worst case by hand: number of steps times the largest
per-step timeout. If that number is larger than what you'd actually consider
acceptable, the fix is a shared budget across the steps, not a smaller per-step
timeout - a smaller per-step timeout only narrows the same unbounded sum, it doesn't
cap it.

## Short answers to the questions that lead here

**Why is my automation's launch occasionally much slower than usual, with no error?**
A common cause is a multi-step network resolution during launch (proxy egress lookup,
[timezone resolution from the proxy exit IP](offline-geoip-timezone-proxy.md), and
similar) where each step has its own timeout but nothing bounds the sequence as a
whole. The slow cases are every step in the list being tried in the worst order, not
a single broken request.

**What is a timeout budget?** A timeout budget is a single upper bound on how long an
entire multi-step operation may take, independent of how many sub-steps it runs. Each
sub-step still gets a timeout, but that per-step value is capped at whatever is left
of the budget, so the sum can never exceed the ceiling the caller actually cares about.

**Doesn't a shorter per-request timeout fix this?** It lowers the ceiling
proportionally but doesn't remove it, and it risks cutting off a slow-but-honest
server that would have answered given slightly more time. A budget bounds the total
without penalizing an individual slow-but-working endpoint as harshly.

**How do you test a fix like this?** Against the failure it's meant to prevent: revert
to the unbounded version and confirm the worst-case wait reappears, then confirm the
budgeted version can't exceed its ceiling no matter how many endpoints are added to
the list.

**See also:** [why a killed test runner leaks Firefox processes on Windows](orphaned-browser-process-windows.md),
another case in the automation layer's own reliability rather than anything the
target page does, and [a packaged build that launched fine but couldn't be driven](juggler-missing-packaged-build.md),
where the gap was a smoke test that never actually exercised the thing that broke.

## Sources

- This project's own diagnosis and fix for the launch-time geo resolution step, and
  its regression test suite, which locks in both the budget behaviour and the
  message distinguishing "endpoint unreachable" from "budget exhausted."

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
on a slow launch that took longer to correctly diagnose than to fix, because every
piece involved really was behaving exactly as configured.*
