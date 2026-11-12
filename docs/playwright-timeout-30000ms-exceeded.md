---
title: "TimeoutError: Timeout 30000ms Exceeded"
description: "Playwright's own TimeoutError is the client giving up on waiting for an action or navigation, not a network-layer failure. Why raising the number is usually the wrong first move, and how to read what was actually stuck."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 17
---


# TimeoutError: Timeout 30000ms Exceeded

`TimeoutError: Timeout 30000ms exceeded` is Playwright's own driver giving up on waiting
for something to finish, after its default 30-second budget ran out with no success. It
shows up as `locator.click: Timeout 30000ms exceeded`, `page.goto: Timeout 30000ms
exceeded`, and similar variants naming whichever call it was attached to. Playwright's
own API documentation states the default plainly: every action and navigation call
defaults to 30 seconds unless overridden with `timeout` or changed globally with
`set_default_timeout()` / `set_default_navigation_timeout()`.

This is not the browser refusing to load a page over the network. It is Playwright's
client library concluding that whatever it was waiting for did not happen inside the
window it was given, a distinction covered in depth on [`ERR_CONNECTION_TIMED_OUT`](err-connection-timed-out-playwright.md):
[a real, verified report](https://github.com/microsoft/playwright/issues/5062) shows
`net::ERR_TIMED_OUT` firing from the browser's own network stack even after this option
was raised to 180 seconds, because the two mechanisms are not the same lever. This page
is about the one Playwright itself controls.

## What is actually happening underneath

For an action like `click`, `fill`, or `check`, Playwright's own actionability system is
what this timeout is a budget for. Before performing the action, Playwright waits for a
set of conditions on the target element, and its own documentation is explicit about the
failure mode: "If the required checks do not pass within the given `timeout`, action
fails with the `TimeoutError`." The checks vary by action but commonly include whether
the element is attached to the DOM, visible, stable, able to receive pointer events at
the point Playwright would click, and enabled. For navigation, the timeout instead
bounds how long `goto()` waits for the condition you asked for, the default `load` event
or whatever `wait_until` you configured, to fire.

The practical result is that this error rarely tells you *what specifically* was wrong,
only that the wait ran out. [A real report](https://github.com/microsoft/playwright/issues/17275)
shows exactly this: the log reads `waiting for selector ".free >> nth=5"` with nothing
further, and the reporter had no way to tell whether the element never existed, never
became visible, or was blocked from receiving the click.

## A concrete case where every check passes and the click still never lands

The clearest illustration of why the timeout itself is rarely the real story is [a
documented, Firefox-specific report](https://github.com/microsoft/playwright/issues/23618):
Playwright's own logs showed the target element as visible, enabled, and stable, every
box checked, and the click still timed out. The cause was an open dropdown sitting above
the target in the page's own stacking context, intercepting the pointer event before it
could reach the element underneath. Playwright can confirm an element looks perfectly
clickable by every measure it checks and still never deliver the click, because something
else on the page is standing in the way. Chromium and WebKit did not reproduce it; the
interaction was specific to how Firefox handled that overlay. Raising the timeout does
not fix anything here: the dropdown is still there ninety seconds later exactly as it was
at second one.

## Why raising the number is usually the wrong first move

A longer timeout only postpones the same failure if the underlying condition was never
going to resolve on its own, which is the common case: an element covered by another one,
a selector matching nothing because of an earlier silent failure upstream, or a modal
that needs dismissing before the target becomes reachable at all. None of those improve
by waiting longer; they resolve, or they do not, and a bigger number just moves the same
red result further down the clock.

It has a real cost beyond wasted time, too. A test suite where every stuck action waits
out an inflated timeout before failing turns a fast, informative red into a slow one, and
in CI that budget is rarely free: a step that used to fail in 30 seconds and now fails in
120 eats into whatever overall job timeout the pipeline enforces, sometimes turning one
clear failure into a pipeline-level timeout that reports nothing useful at all.

The one case where raising it is the right move is when the condition genuinely just
needs more real time: a slow but honest server response, a heavy page that takes longer
than 30 seconds to reach `load` on a loaded CI runner. Even there, confirming that is
actually what is happening comes before reaching for the setting, because "this will
succeed given more time" and "this will never succeed" produce the identical message.

## How to tell the difference before touching the timeout

Read what Playwright was actually waiting for, rather than guessing from the final
exception. Playwright's own verbose action log names the specific actionability state it
is polling, for example printing a line like "waiting for element to be visible, enabled
and stable" while an action is pending, the detail the bare `TimeoutError` text strips
away.

```bash
# Prints Playwright's own action-level waiting log to stderr as it happens.
DEBUG=pw:api python your_script.py
```

A [saved trace](record-playwright-trace-debug-scraper.md) captures the same information
after the fact, including a screenshot at the moment of failure, which answers the
question the dropdown example above turns on: was something visually sitting on top of
the target when the click was attempted.

## Diagnostic checklist

1. **Read the verbose action log or a saved trace before changing any timeout value.**
   The specific actionability state Playwright was stuck on is the actual bug report;
   the `TimeoutError` text alone is not.
2. **Take a screenshot at the moment of failure.** An overlay, a modal, or a dropdown
   sitting over the target element is invisible in the log line but immediate in a
   screenshot.
3. **Confirm the selector matches exactly one element, and that it exists at all**,
   before assuming a visibility or stability problem; zero matches times out identically
   to an element that never becomes clickable.
4. **Ask whether the condition is genuinely time-bound or structurally stuck.** A slow
   response benefits from more time. A permanently obscured element does not, no matter
   the timeout.
5. **If the failure is instead a browser-level `net::` string**, you are looking at a
   different mechanism entirely; see [ERR_CONNECTION_TIMED_OUT in Playwright](err-connection-timed-out-playwright.md)
   for that separate case.

## The honest boundary

Playwright's own action and navigation timeout is a driver-level setting, unrelated to
how convincingly a browser's identity reads to a page. `invisible_playwright` does not
change this default, does not alter how actionability checks are evaluated, and does
not make a genuinely stuck condition resolve any faster. A stock Playwright browser and
this project's build hit the identical `TimeoutError` under the identical stuck
condition, because the actionability system this timeout bounds belongs to Playwright's
own client library, not to the engine underneath it.

## Short answers to the questions that lead here

**What does "Timeout 30000ms exceeded" mean?** Playwright's own client library waited
30 seconds, its default, for an action's actionability checks or a navigation's load
condition to succeed, and gave up. It is a driver-level timeout, not a network error.

**Should I just raise the timeout?** Usually not as the first move. If the underlying
condition, a missing element, an overlay blocking the click, a selector matching
nothing, was never going to resolve, a longer timeout only delays the identical
failure while making the suite slower.

**How do I find out what was actually stuck?** Run with `DEBUG=pw:api` or save a
[trace](record-playwright-trace-debug-scraper.md) and read the specific actionability
state Playwright was waiting on, plus a screenshot at the moment of failure.

**Can an element pass every actionability check and still cause this timeout?** Yes. A
documented, Firefox-specific case shows an element reported as visible, enabled, and
stable still timing out because an open dropdown was intercepting the click before it
reached the target.

**How is this different from net::ERR_CONNECTION_TIMED_OUT?** That error is the
browser's own network stack giving up on a connection attempt, on its own internal
ceiling, independent of this setting. This error is Playwright's client library giving
up on waiting for a call to succeed. Raising this timeout has no effect on that one.

## Sources

- Playwright's own [API reference for `browser_type.launch()` and related timeout
  parameters](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch),
  and the [test-timeouts documentation](https://playwright.dev/docs/test-timeouts), for
  the default 30-second action and navigation timeout, retrieved 2026-08-30.
- Playwright's own [Auto-waiting / actionability documentation](https://playwright.dev/python/docs/actionability),
  for the exact list of checks (visible, stable, receives events, enabled, editable) and
  the statement that a check not passing within `timeout` fails with `TimeoutError`,
  retrieved 2026-08-30.
- [microsoft/playwright#17275](https://github.com/microsoft/playwright/issues/17275), a
  real `locator.click: Timeout 30000ms exceeded` report where the log named only the
  selector being waited on, with no further diagnostic detail.
- [microsoft/playwright#23618](https://github.com/microsoft/playwright/issues/23618), a
  real, Firefox-specific report of an element passing every actionability check and
  still timing out because an open dropdown intercepted the click.
- [microsoft/playwright#5062](https://github.com/microsoft/playwright/issues/5062), for
  the confirmed independence of Playwright's own timeout setting from the browser's own
  network-level connection timeout, already covered in full on the
  `ERR_CONNECTION_TIMED_OUT` page.

**See also:** [net::ERR_CONNECTION_TIMED_OUT in Playwright](err-connection-timed-out-playwright.md)
for the separate, browser-network-layer timeout this page is most often confused with,
[recording a Playwright trace to debug a scraper](record-playwright-trace-debug-scraper.md)
for the tool that shows exactly what an action was waiting on, and ["Execution context
was destroyed"](execution-context-destroyed.md) for a different failure that can also
follow a stuck or racing wait.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. This timeout and the
actionability system it bounds are Playwright's own; a stuck check is the page, not the
engine's identity layer.*
