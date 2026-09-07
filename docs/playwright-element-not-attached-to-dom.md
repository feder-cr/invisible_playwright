---
title: "Playwright \"Element Is Not Attached to the DOM\""
description: "Playwright's auto-waiting confirms an element exists before acting, then a framework re-render swaps it out in the gap before the action fires. The fix is a locator, not a handle."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 21
---


# Playwright "Element Is Not Attached to the DOM"

```
Error: locator.click: Element is not attached to the DOM
```

This looks like it should be impossible. Playwright auto-waits: it confirms an element
is visible, stable and enabled before acting on it. The error means the element passed
every one of those checks and was still gone by the time the action actually fired,
which happens because a check passing and an action firing are not the same instant, and
a modern framework can replace a node in the narrow gap between them.

## The mechanism: not attached is not "gone", it is "swapped"

"Element is not attached to the DOM" is a different failure from a page that navigated
out from under you. Navigating destroys the whole document and every context
[belonging to it](execution-context-destroyed.md). This error usually fires on a page
that never navigated at all: the document is the same document, the DOM is still there,
and the specific node your code was holding a reference to has simply been removed and
replaced with a new one that looks the same.

React, Vue and Angular all do this constantly and by design. A state update re-renders a
component, and depending on how that component is keyed, the framework can tear down the
existing DOM node and mount a fresh one instead of mutating the node in place, even when
the visible result is pixel-identical. Your old reference is now a detached node: it
still exists as an object, it is still "visible" in the sense that its properties read
back fine, and it is no longer part of the document tree that would receive a click.

## Why auto-waiting does not prevent this

Playwright's actionability checks run, confirm the element is ready, and then the action
executes. If a re-render lands inside that window - after the last check, before the
dispatched event - the element that passed the checks is not the element that receives
the action. Auto-waiting protects against slow-loading and not-yet-visible elements. It
does not protect against an element that was fine and then got replaced, because from
Playwright's point of view nothing was wrong until the instant it tried to act.

This is also why the failure is intermittent in a way that looks like flakiness: it
depends on whether a re-render happens to land in that specific window on that specific
run, which is a race against the framework's own render schedule, not against the
network.

## The ordinary fix: locators re-query, handles do not

The Locator API exists specifically for this. A `Locator` stores how to find an element,
not a reference to the element itself, so every action re-resolves it against the live
DOM at the moment the action runs instead of trusting whatever it found earlier:

```python
# fragile: element_handle is a snapshot, and a re-render invalidates it
handle = page.query_selector("button.submit")
some_state_changing_call()
handle.click()          # may throw "not attached" if a re-render swapped the node

# robust: the locator re-queries the live DOM at click time
locator = page.locator("button.submit")
some_state_changing_call()
locator.click()          # resolves fresh, right before acting
```

If your code is already using locators end to end and still hits this, the gap is
usually a held reference smuggled in some other way: a variable captured from an earlier
`page.locator(...).element_handle()` call, a list of handles gathered once and iterated
later, or a custom wait that reads a property off a handle and then acts on the same
handle afterward. Any point where you go from a `Locator` to an `ElementHandle` and hold
onto the handle reintroduces the exact staleness a `Locator` was built to avoid.

## When it is not a framework re-render

Two other patterns produce the identical message and deserve a quick check before you
assume React or Vue is the culprit:

- **A conditional element that unmounts on its own schedule** - a toast, a tooltip, an
  autocomplete dropdown - closing itself between when you found it and when you acted on
  it. The fix is the same (a locator, acted on immediately), but the underlying cause is
  a timed UI element in place of a state re-render.
- **A list re-sorted or re-filtered between query and action**, where the node you found
  is removed as part of an actual data change instead of a cosmetic re-render. Here the
  right fix is often to re-scope the locator after the action that triggers the sort, not
  just to swap a handle for a locator.

## Diagnostic checklist

1. **Confirm you are using a `Locator`, not a stored `ElementHandle`.** This alone
   resolves most reports.
2. **Search your code for `.element_handle()` or `element_handle=True`.** Anywhere a
   handle is extracted and held past the next state-changing call, it can go stale.
3. **Open a trace and step through the action.** Playwright's trace viewer shows the DOM
   at each step, so you can see the re-render happen rather than infer it.
4. **Check whether the target is a framework component with a changing `key`.** A list
   item whose key depends on an index rather than a stable identifier is remounted, not
   updated, on almost every re-render.
5. **Check whether the element is conditionally rendered on a timer** (a toast, a
   dropdown) rather than tied to application state at all.

## What invisible_playwright does and does not touch here

The application's own rendering behavior, and Playwright's Locator versus ElementHandle
distinction, are both entirely above the engine layer this project patches. A React
re-render replaces a DOM node identically whether the browser underneath is a stock
download or a Firefox patched for fingerprint realness; nothing about the patch changes
when or how a framework mounts and unmounts components.

## Conclusion

"Element is not attached to the DOM" almost always means a framework replaced a node in
the gap between Playwright confirming it was ready and Playwright acting on it, not that
Playwright's auto-waiting failed. Locators close that gap by re-resolving at the moment
of the action; a stored `ElementHandle`, however it was obtained, cannot, because it
points at a specific node that the framework has already thrown away.

## Short answers to the questions that lead here

**Why does this happen even though Playwright auto-waits?** Auto-waiting confirms an
element is ready before acting, but a framework re-render can replace the node in the
gap between that confirmation and the action itself. The check and the act are not the
same instant.

**Does switching from `page.$()` to `page.locator()` fix it?** In most reports, yes,
because a `Locator` re-queries the live DOM immediately before acting instead of trusting
a reference gathered earlier.

**Is this the same as "Execution context was destroyed"?** No. That error follows an
actual navigation replacing the whole document. This one usually fires on a document that
never navigated at all; only one node inside it was swapped.

**Why is it intermittent?** Because it depends on whether a re-render happens to land in
the narrow window between Playwright's last check and the dispatched action, which
varies run to run with the framework's own render timing.

**I already use locators everywhere. Why do I still see this?** Look for a place a
`Locator` was converted to an `ElementHandle` and held across an `await` - that handle is
exactly as stale as the old `page.$()` pattern.

**Does this mean my app has a bug?** Not necessarily. Tearing down and remounting a
component on state change is normal and often intentional framework behavior; it only
becomes a test problem when a stale reference is held across it.

## Sources

- Microsoft Playwright issue [#6244](https://github.com/microsoft/playwright/issues/6244),
  the original question thread on what "Element is not attached to the DOM" means and
  when it fires.
- Microsoft Playwright issue [#10477](https://github.com/microsoft/playwright/issues/10477),
  a report of the same error from `page.uncheck()` on a re-rendering control.
- Microsoft Playwright issue [#29735](https://github.com/microsoft/playwright/issues/29735),
  a WebKit and mobile Safari-specific case of the same failure family.
- Playwright's own documentation, [Actionability](https://playwright.dev/python/docs/actionability),
  for what the auto-waiting checks confirm and, by omission, what they do not protect
  against once an action is already dispatched.

**See also:** [Execution context was destroyed, and when it means detection](execution-context-destroyed.md),
the sibling failure caused by a real navigation rather than an in-place re-render, and
[Playwright Strict Mode Violation: Resolved to N Elements](playwright-strict-mode-violation.md),
another case where the fix is a properly scoped locator rather than a workaround around
the check.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A framework's own render
cycle produces this error identically underneath a patched engine or a stock one.*
