---
title: "Drag and drop elements in Playwright with drag_to"
description: "Drag and drop with drag_to in Playwright. Why pointer events are trusted on patched Firefox, and the limit: programmatic paths are not human mouse motion."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 40
---


# Drag and drop elements in Playwright with drag_to

Dragging one element onto another is one of those interactions that looks trivial
and then quietly fails. A hand-dispatched `dragstart` event runs the site's handler,
the element does not move, and nothing tells you why. Playwright has a proper helper
for this, and on a patched browser the events it fires are indistinguishable from a
real pointer at the trust level. That last part is worth being precise about, because
it is true in a way that matters and false in a way that also matters.

This page is the two ways to drag in Playwright, why the events come back trusted, a
runnable example, and the one honest limit you should know before you point this at
anything that scores the motion itself.

## The two ways to drag in Playwright

Playwright gives you two documented calls, and they are the same operation at
different altitudes.

[`locator.drag_to(target)`](https://playwright.dev/python/docs/api/class-locator#locator-drag-to)
is the locator-first form. You already have a source locator, you hand it a target
locator, and Playwright performs the full sequence: hover the source, press the mouse
button, move to the target, release.

[`page.drag_and_drop(source, target)`](https://playwright.dev/python/docs/api/class-page#page-drag-and-drop)
is the selector form. You pass two selector strings and it resolves both, then runs
the identical hover-press-move-release sequence. Use it when you do not already hold
the locators.

Both do the real thing. Neither one dispatches a synthetic `DragEvent` into the DOM
and hopes the handler accepts it, which is the approach that fails silently on sites
that check the event is real. They drive the actual pointer through the browser's
input path, so the page sees a genuine press, a genuine move and a genuine release.

## Why the events come back trusted

Every DOM event carries an [`isTrusted`](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
flag. The browser sets it to `true` for input it generated from a real device and
`false` for anything a script dispatched with `dispatchEvent`. A site that guards a drag handler with `if (!event.isTrusted)
return;` throws away scripted drags and keeps human ones, and there is no way to set
`isTrusted` from JavaScript. It is read-only by design.

invisible_playwright drives a Firefox that has been patched at the C++ level, and
Playwright's input commands travel through the browser's native input path rather than
through a JavaScript shim. The press, the moves and the release that `drag_to`
produces arrive as engine-generated events, so `isTrusted` reads `true` on every one
of them. From the page's point of view the drag came from a pointer, because at the
level the flag measures, it did. This is the same mechanism that makes
[Playwright clicks report isTrusted=true](playwright-clicks-istrusted.md) on this
build rather than the `false` a page-level automation layer is stuck with.

That is the part that is genuinely solved. The next section is the part that is not.

## A runnable example

Switching from stock Playwright is a two-line change, and every method you already
know keeps working. Here is a drag using the locator form:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/board")

    source = page.locator("#card-1")
    target = page.locator("#column-done")

    source.drag_to(target)   # hover, press, move, release - all trusted
```

The selector form is the same operation without holding the locators first:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/board")

    page.drag_and_drop("#card-1", "#column-done")
```

The `seed=42` is what makes a failure reproducible: the same seed gives the same GPU,
canvas, fonts and screen every run, so a drag that misbehaves can be replayed exactly
instead of chased across random identities. Confirm the drop actually landed by
asserting on the DOM afterwards, not by trusting the call returned:

```python
    from playwright.sync_api import expect
    expect(page.locator("#column-done")).to_contain_text("card 1")
```

If you need finer control - a pause mid-drag, an intermediate waypoint, a hover to
trigger a drop zone highlight - drop to the
[low-level mouse API](https://playwright.dev/python/docs/api/class-mouse)
(`page.mouse.move`, `page.mouse.down`, `page.mouse.up`) and build the sequence
yourself. Everything on the returned `browser` is the stock Playwright API, so this is
the ordinary manual-drag recipe, unchanged.

## The honest limit: a straight line is not a human drag

Here is the caveat, stated plainly, because skipping it would be the dishonest version
of this page.

`drag_to` and `drag_and_drop` move the pointer from source to target on a straight
programmatic path. The events are trusted, but the trajectory is a machine's: a
direct interpolation from A to B, evenly spaced, with none of the overshoot, curve,
micro-correction or variable speed a hand produces. For a functional drag - reordering
a list, moving a card between columns, sorting a table - this is completely fine. The
handler cares that a real pointer pressed, moved and released over the right elements,
and it got exactly that.

It is not fine for a challenge that scores the *shape* of the movement. A slider
puzzle or a drag-to-verify widget records the path itself - the velocity profile, the
acceleration, whether the line is arrow-straight - and a perfectly straight, uniformly
timed drag reads as a machine even though every event on it carries `isTrusted=true`.
The trust flag and the motion curve are two independent signals. `drag_to` gives you
the first for free and does nothing for the second.

For those cases the built-in helper is the wrong tool. You want humanized pointer
motion driving the same low-level mouse calls, which is a
[behaviour problem rather than a trust problem](human-mouse-movement.md): a good
[Bezier path from a cursor library](ghost-cursor-human-mouse.md) fed through
`page.mouse.*`. Note that even invisible_playwright's own Bezier cursor is applied on
click and move, and the same subtlety that makes
[hover collapse to a near-teleport](hover-mouse-movement-bug.md) applies here - the
convenience helper does not route through it. Know which of the two problems you have
before you reach for a fix.

## What drag_to does not fix

Trusted events are a browser property, and a drag is one interaction inside a session.
Neither one touches the things that get a session blocked for reasons that have nothing
to do with the drag.

- **IP reputation.** A trusted drag from a datacenter address that a scoring system
  already knows is still from that address. Supply a clean exit; see
  [configuration](configuration.md) for how the proxy and the auto-derived timezone
  fit together.
- **Per-account quotas and rate limits.** No pointer event resets a counter. If an
  account or an address is over its allowance, a flawless drag does not move it back
  under.
- **Behaviour and timing.** The drag being trusted says nothing about the fifty
  milliseconds before it or the pace of the session around it. A page filled and
  submitted in eighty milliseconds is a timing signal regardless of every event on it
  being real.
- **Motion scoring**, as above - a separate signal from the trust flag.

invisible_playwright is built to look like a real browser driven by a real person, and
that is why the fingerprint, TLS and driver layers read as a genuine Firefox and why
the drag events are trusted. It does not, on its own, fix your address, your quotas or
your pacing. Those you supply: a clean proxy and human timing. When a clean fingerprint
still gets blocked, [the reason is usually one of those](why-blocked-with-a-clean-fingerprint.md),
not the browser.

## Conclusion

For a functional drag - reorder, move, sort - `locator.drag_to(target)` or
`page.drag_and_drop(source, target)` is the right call, and on a patched Firefox the
hover-press-move-release it produces arrives with `isTrusted=true`, so handlers that
throw away scripted drags accept it. That solves the trust half of the problem
completely.

It does not solve the motion half. The built-in path is straight and evenly timed, and
anything that scores the trajectory sees a machine. For those, drive humanized motion
through the low-level mouse API instead. Know which problem you have, and the two-line
switch handles the common case with nothing else to learn.

## Short answers to the questions that lead here

**How do I drag and drop in Playwright?** `locator.drag_to(target)` if you have the
locators, or `page.drag_and_drop(source, target)` with selector strings. Both run the
full hover-press-move-release.

**Are Playwright drag events real or synthetic?** On a patched Firefox they are real:
the input goes through the browser's native input path, so every event carries
`isTrusted=true`. A hand-dispatched `DragEvent` would be `false`.

**Why does my manual dragstart event do nothing?** Because a script-dispatched event
has `isTrusted=false`, and any handler that checks the flag ignores it. Use `drag_to`,
which drives the actual pointer.

**Will drag_to pass a slider or drag-to-verify challenge?** Not reliably. The events
are trusted, but the path is straight and uniform, and a challenge that scores the
motion curve sees a machine. That needs humanized pointer motion, not the built-in
helper.

**How do I add pauses or waypoints to a drag?** Drop to `page.mouse.move`,
`page.mouse.down` and `page.mouse.up` and build the sequence yourself. The returned
browser is the full stock Playwright API.

**Does a trusted drag mean I will not get blocked?** No. It fixes the event trust
level only. Your IP reputation, account quotas, rate limits and session timing are
separate, and you supply the clean proxy and human pacing.

## Sources

- Playwright's documented [`locator.drag_to`](https://playwright.dev/python/docs/api/class-locator#locator-drag-to)
  and [`page.drag_and_drop`](https://playwright.dev/python/docs/api/class-page#page-drag-and-drop),
  and the low-level [`page.mouse`](https://playwright.dev/python/docs/api/class-mouse) API.
- The [WHATWG DOM specification for `Event.isTrusted`](https://dom.spec.whatwg.org/#dom-event-istrusted)
  and [MDN's `Event.isTrusted` reference](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted):
  read-only, true only for user-agent-generated events.
- This project's input path, where Playwright's press, move and release commands are
  delivered as engine-generated events so the `isTrusted` flag reads true.
- This project's own notes on where pointer-motion realism ends, linked throughout.

**See also:** [do automated clicks report isTrusted=true](playwright-clicks-istrusted.md),
[human-like mouse movement past the Bezier curve](human-mouse-movement.md), and
[uploading files, another interaction that needs a trusted event](how-to-upload-files-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The trusted-events half
is solved; the straight-line-motion half is honestly not, and this page says which is
which.*
