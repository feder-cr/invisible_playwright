---
title: 'Playwright "Frame Was Detached" Error'
description: "Frame was detached means the iframe itself was removed from the page's frame tree, not that its execution context was replaced by a navigation. A framework remounting a widget instead of navigating it is the usual cause."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 18
---


# Playwright "Frame Was Detached" Error

"Frame was detached" means the frame object itself, usually an iframe, was removed from
the page entirely: Playwright's own `framedetached` event fired for it, and any
operation still scoped to that `Frame` object fails because there is no longer a frame
there to operate on, not merely a stale JavaScript context inside one that is still
present. It shows up in a few different verbatim shapes depending on what you were doing
when it happened: [`Frame has been detached.`](https://github.com/microsoft/playwright/issues/14404)
from a direct method call on a `Frame`, and `net::ERR_ABORTED; maybe frame was detached?`
from a navigation-scoped call that got interrupted by the frame disappearing mid-flight.

## Three similar-sounding errors, and which one this is

This project's docs already cover two closely related failures, and all three are easy to
conflate because they describe "the thing I was holding a reference to is gone." They are
not the same mechanism, and the fix for one does nothing for the others.
[**"Element is not attached to the DOM"**](playwright-element-not-attached-to-dom.md) is
the narrowest: a single element node was swapped out by a framework re-render, while the
document and frame around it never changed. [**"Execution context was destroyed"**](execution-context-destroyed.md)
is one level up: the frame is still present, but it navigated, so its JavaScript context
was torn down and replaced. The frame did not go anywhere; what you were evaluating
inside it did.

**"Frame was detached"** is a further level up from both. The frame itself, the actual
object Playwright's frame tree was tracking, no longer exists on the page: not navigated,
not swapped, removed. Playwright's own documentation is precise about the distinction:
`framedetached` "fired when the frame gets detached from the page," separate from
`framenavigated`, which fires "when the frame commits navigation to a different URL." A
frame can be detached only once, and once it has been, `frame.is_detached()` returns
`true` for the rest of that frame object's life.

## The realistic causes

**A framework remounting a widget under a new node instead of navigating it in place.**
The same React/Vue/Angular re-render behavior that swaps out a single element, described
in full on the element-detached page, does the identical thing to an entire iframe when
the widget it belongs to, a chat panel, a payment form, an inline authentication popup,
is keyed such that a state change tears down the old iframe and mounts a fresh one rather
than reusing it. From the outside the widget looks unchanged; the `Frame` object your
code was holding is gone underneath it.

**A top-level navigation tearing down every child frame it owned.** Navigating the main
page destroys not just its own execution context but every iframe nested inside it. Code
still holding a reference to one of those child frames, not to the page itself,
sees that frame detach as a side effect of a navigation that, from the page's own
perspective, looks unremarkable.

**A popup or a secondary tab being closed, programmatically or by the site itself.** The
frame tree entry for a closed page's main frame detaches the same way a removed iframe
does, and code that raced ahead to interact with it after the close reports the identical
error.

**A navigation inside the frame getting interrupted by the frame's own removal.** This is
the specific shape behind `net::ERR_ABORTED; maybe frame was detached?`, [seen across
several real reports](https://github.com/microsoft/playwright/issues/8145). The trailing
question mark is not decorative; it is Playwright's own code printing a best guess at the
cause, because an aborted navigation and one cut short by the frame disappearing can look
alike from the driver's side. [A real report against `browserContext.storageState()`](https://github.com/microsoft/playwright/issues/29402)
shows this string firing intermittently even when `is_detached()` had returned `false`
immediately beforehand: the frame was attached at the moment it was checked and detached
by the moment the next operation ran. Maintainers tracked a fix for this case to a
following release.

## Diagnostic checklist

1. **Check `page.frames` for what actually happened to the frame you expected.** A
   frame that detached is simply gone from that list; one that merely navigated is still
   present with a new URL. This one check tells you immediately which of the three
   related failures above you are looking at.
2. **Read the action right before the failure.** A click, a form submission, or any
   interaction that could plausibly trigger a framework re-render of the surrounding
   widget is the first suspect for a detached iframe, the same way it is for a detached
   element.
3. **If the error is `net::ERR_ABORTED; maybe frame was detached?`, treat the question
   mark literally.** Confirm the frame actually detached, via `page.on("framedetached")`
   or a post-hoc check of `page.frames`, not assuming the message's own guess is
   automatically correct.
4. **Never hold a `Frame` reference across an action that might cause the surrounding
   widget to remount.** Re-acquire it, or better, avoid holding one at all.

## The fix, and why it is structural rather than a wait

`frame.wait_for_selector()` before re-reading a frame reference narrows the race but does
not remove it, for the identical reason a longer wait does not fix [a detached
element](playwright-element-not-attached-to-dom.md#the-ordinary-fix-locators-re-query-handles-do-not):
a check passing and the next operation running are not the same instant, and a framework
can remount the iframe in that gap regardless of how long you waited first.

The structural fix is [`frame_locator()`](https://playwright.dev/python/docs/api/class-framelocator)
in place of a held `Frame` object. A `FrameLocator` does not resolve to a concrete frame
until the moment an action runs, the same lazy-resolution principle a plain `Locator`
applies to elements, so a remounted widget is simply found again on the next action.

```python
# Holding a Frame object is exactly what this error class is about.
frame = page.frame(name="payment-widget")
frame.click("#submit")  # fails if the widget remounted since the line above

# frame_locator() resolves the frame fresh at the moment of the action.
page.frame_locator("iframe[name=payment-widget]").locator("#submit").click()
```

If you must work with a `Frame` object directly, for example because you need
`frame.url` or `frame.title()` rather than an action, attach a `page.on("framedetached")`
listener before the operation you are worried about, so a detach is a known event
instead of a downstream exception with no context attached to it.

```python
page.on("framedetached", lambda f: print("frame detached:", f.url))
```

## The honest boundary

Frame detachment is a property of how the page's own JavaScript manages its DOM and its
iframes, decided entirely by the site's code, not by anything a browser's identity
layer touches. `invisible_playwright` does not change how or when a page detaches a
frame, and it does not paper over a widget that remounts instead of navigating. A stock
Playwright browser and this project's build hit the identical detachment under the
identical page behavior, because the frame tree this error concerns belongs to the
automation protocol tracking the page's own DOM, not to anything this project's stealth
patching reaches.

## Short answers to the questions that lead here

**What does "Frame was detached" mean?** The frame object itself, usually an iframe, was
removed from the page's frame tree entirely. Playwright's `framedetached` event fired
for it, and any operation still scoped to that `Frame` object now has nothing to act on.

**How is this different from "Execution context was destroyed"?** That error means the
frame is still present but navigated, so its JavaScript context was replaced. This error
means the frame itself is gone, not merely its context. Check `page.frames` to tell them
apart immediately.

**What does "net::ERR_ABORTED; maybe frame was detached?" mean specifically?** It is
Playwright's own guess at the cause of a navigation that got aborted, printed because an
aborted navigation and one cut short by the frame's own removal can look alike from the
driver's side. Confirm the detachment actually happened rather than trusting the guess.

**Does waiting longer before re-checking the frame fix this?** No, for the same reason
a longer wait does not fix a detached element: a check passing and the next operation
running are not the same instant, and a remount can happen in that gap regardless of how
long you waited first.

**What is the actual fix?** Use `frame_locator()` instead of holding a `Frame` object
directly. It resolves the frame fresh at the moment each action runs, so a remounted
widget is simply found again rather than causing a stale reference to fail.

## Sources

- Playwright's own [API reference for `Frame`](https://playwright.dev/python/docs/api/class-frame),
  for `frame.is_detached()`, the `framedetached` and `framenavigated` page events, and
  the documented behavior that a frame can be detached only once, retrieved 2026-08-30.
- Playwright's own [API reference for `FrameLocator`](https://playwright.dev/python/docs/api/class-framelocator),
  for the lazy per-action frame resolution this page recommends in place of a held
  `Frame` reference, retrieved 2026-08-30.
- [microsoft/playwright#14404](https://github.com/microsoft/playwright/issues/14404), a
  real report of `Uncaught frame.frameElement: Frame has been detached.` while clicking
  an element inside an iframe.
- [microsoft/playwright#8145](https://github.com/microsoft/playwright/issues/8145) and
  [microsoft/playwright-dotnet#2514](https://github.com/microsoft/playwright-dotnet/issues/2514),
  real reports of `net::ERR_ABORTED; maybe frame was detached?` during navigation.
- [microsoft/playwright#29402](https://github.com/microsoft/playwright/issues/29402), a
  real report of this error firing intermittently against `browserContext.storageState()`
  even when `is_detached()` had just returned `false`, tracked by maintainers to a fix in
  a following release.

**See also:** ["Execution context was destroyed", and when it means
detection](execution-context-destroyed.md) for the frame-still-present, context-replaced
sibling of this error, [Playwright "Element Is Not Attached to the DOM"](playwright-element-not-attached-to-dom.md)
for the single-node version of the identical underlying pattern, and [why content_frame()
returns None for a cross-origin iframe](cross-origin-iframe-unreachable.md) for a
different iframe failure, one where the frame is attached but unreachable rather than
detached.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Frame detachment is the
page's own DOM management; the automation protocol tracking it is Playwright's, and
nothing here touches this project's stealth layer.*
