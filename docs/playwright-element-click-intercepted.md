---
title: "Playwright \"Subtree Intercepts Pointer Events\""
description: "Something else is sitting on top of the element you asked Playwright to click. Reaching for force:true fires blind at the same spot instead of finding out what is there, and that gap is itself a real-user difference."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 22
---


# Playwright "Subtree Intercepts Pointer Events"

```
Error: locator.click: <element> intercepts pointer events
```

Playwright checks, before every click, whether the element it is about to act on is
actually the thing that would receive the click - "the hit target of the pointer event
at the action point," in Playwright's own wording for the check. When something else
occupies that point instead, the click is refused, not sent somewhere silently
wrong. This is the "Receives Events" actionability check, and it exists for the same
reason strict mode does: a click that lands on the wrong thing should fail loudly, not
succeed against a target nobody asked for.

## What is actually sitting on top

The element failing this check is real and rendered where you expect it. Something else
is layered above it at the exact pixel Playwright would click, and a person moving a real
mouse to that spot would hit the same obstruction:

- **A sticky or fixed header** whose height was not accounted for, sitting over the top
  of the page's actual content at certain scroll positions.
- **A modal backdrop** left in the DOM, invisible or nearly so, after a dialog was
  supposed to close but did not fully tear down.
- **An animation or transition mid-flight** - a toast sliding in, a dropdown collapsing -
  occupying the space for the duration of the transition.
- **A cookie-consent or promotional overlay** that renders after the page's main content
  and sits above it until dismissed.

In every case the check is correct: the element you targeted is not what a click at that
point would actually hit.

## Why `{force: true}` is usually the wrong first move

`force: true` skips every actionability check, including this one, and sends the click
regardless of what is on top. It makes the error disappear and it does not make the
obstruction disappear. Two things follow from that, and the second one matters
specifically for a browser-automation project built around looking like a real user.

First, mechanically: `force` skips the check, not the browser. Playwright
still delivers a real click at your target's coordinates, the browser's
hit-testing still applies, and whatever sits on top at that point is what
receives it. You have not reached your element; you have clicked the overlay
blind. Whatever handler fires belongs to the obstruction - or to nothing -
while your script carries on believing the button was pressed, which is how a
forced click turns one visible failure into a silent wrong path.

Second, and this is the part worth internalizing instead of skipping past:
clicking blind into an overlay is itself a tell, on any
page instrumented to notice it. A cookie banner that gets clicked on its
backdrop instead of its buttons, a modal whose own dismiss logic never ran
before the flow continued underneath,
is exactly the kind of behavioral inconsistency a page can check for
independent of any browser fingerprint. `force: true` does not just risk clicking the
wrong thing; it risks producing an interaction shape no genuine user session would ever
generate.

## The right diagnostic sequence

**1. Screenshot at the moment of failure.** This settles what is actually on top faster
than reading DOM structure by hand:

```python
try:
    page.get_by_role("button", name="Submit").click()
except Exception:
    page.screenshot(path="what-is-on-top.png")
    raise
```

**2. Check if it is timing rather than layout.** If the obstruction is a transition or an
animation, waiting for it to finish is the actual fix, not forcing through it:

```python
locator = page.get_by_role("button", name="Submit")
locator.wait_for(state="visible")
# if a specific overlay is the known culprit, wait for it to detach instead of guessing
page.locator(".modal-backdrop").wait_for(state="hidden")
locator.click()
```

**3. Check if it is a leftover element rather than a timing issue.** A modal backdrop
that never tears down after its dialog closes is a bug in the page, not a race; the fix
there is closing the dialog properly in your script (clicking its own close control) or,
if the leftover element is truly cosmetic and inert, scrolling it out of the way rather
than clicking through it.

**4. Check if the interception is intentional on the page's part.** A cookie banner or a
promotional interstitial genuinely means the target is not supposed to be clickable yet.
Dismiss it the way a real visitor would - clicking its own accept or close button - rather
than reaching past it.

**5. Only reach for `force: true` once you have confirmed, by screenshot, that the
obstruction is something Playwright is wrong about** - a transparent hit-testing layer
with no real visual presence, for instance - and even then, treat it as a documented
exception, not a default.

## Diagnostic checklist

1. Screenshot on failure before changing any code.
2. Identify the intercepting element from the error message or the screenshot.
3. Determine whether it is permanent (a fixed header, a stuck backdrop) or transient (an
   animation, a transition).
4. For transient obstructions, wait for the specific element's state rather than adding a
   blind sleep.
5. For permanent obstructions, fix the interaction to go through the obstruction properly
   (dismiss it, scroll past it) rather than around the check.
6. Reserve `force: true` for a confirmed case of Playwright being wrong about hit-testing,
   and say so in a comment when you use it.

## What invisible_playwright does and does not touch here

Actionability checks, hit-testing and page layout are Playwright's and the target page's
territory. This project changes what the browser reports about its identity at the
engine level; it does not change which element sits on top of which pixel, and a fixed
header intercepts a click identically on a patched Firefox and a stock one. Where this
does connect to the project's actual subject is indirect: a click that could not have
happened through a real cursor is a behavioral tell independent of fingerprint, which is
the reason to fix the obstruction rather than force through it, not something the engine
patch can paper over.

## Conclusion

"Subtree intercepts pointer events" means Playwright checked what a click at that point
would actually hit and found something other than your target. The fix is finding out
what that something is and either waiting for it to move, dismissing it properly, or
routing around it, in that order. Forcing the click through skips the diagnosis and can
produce an interaction no real pointer at that screen position could have produced,
which is a cost worth knowing about before reaching for the flag by default.

## Short answers to the questions that lead here

**What does "subtree intercepts pointer events" mean?** An element other than your
target occupies the exact point Playwright would click, so the click is refused rather
than sent to whichever one it guesses you meant.

**Is `force: true` ever the right fix?** Only once you have confirmed, by screenshot,
that the intercepting layer has no real visual presence and Playwright's hit-testing is
simply wrong about it. As a default reflex, no.

**Why does this only happen sometimes on the same page?** Usually because the obstruction
is a transition or animation that occupies the space only briefly, so the outcome depends
on the exact timing of your click relative to it.

**Does waiting longer fix it?** Waiting for the specific obstructing element's state
(hidden, detached) fixes it. A blind longer timeout only sometimes helps and does nothing
for a permanently stuck overlay.

**Can a click that bypasses hit-testing actually hurt me?** It can land on a handler that
assumes a real pointer event, or produce a state no real user session reaches, which is
itself distinguishable from ordinary use independent of any browser fingerprint.

**How do I find out what element is intercepting?** The error message usually names it
directly; a screenshot at the moment of failure confirms it visually when the message
alone is not enough.

## Sources

- Playwright's own documentation, [Actionability](https://playwright.dev/python/docs/actionability),
  for the "Receives Events" check definition quoted above and the full list of checks
  performed before a click.
- Microsoft Playwright issue [#10641](https://github.com/microsoft/playwright/issues/10641),
  a meta-issue tracking multiple reports of this check firing after it was introduced in
  version 1.17.0.
- Microsoft Playwright issue [#12298](https://github.com/microsoft/playwright/issues/12298),
  a report of the check firing against a component with a scheduled rotation animation
  sitting over the target.
- Microsoft Playwright issue [#14011](https://github.com/microsoft/playwright/issues/14011),
  a question thread on the retry behavior of the click action while this check is failing.

**See also:** [Execution context was destroyed, and when it means detection](execution-context-destroyed.md),
for another case where the browser's behavior is correct and the target page is the
variable, and [How to test bot detection without a false pass](how-to-test-bot-detection.md),
for the broader habit of treating a suppressed or bypassed signal as a finding rather
than a convenience.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Hit-testing is the
browser's own layout engine at work, real on a patched build exactly as it is on a stock
one.*
