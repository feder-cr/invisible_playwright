---
title: "Why Playwright Locators Never Throw StaleElementReferenceException"
description: "Selenium's WebElement holds a live reference that goes stale the moment the DOM changes under it. Playwright's Locator re-resolves the element on every action, so the exception class does not exist."
parent: "Comparisons"
nav_order: 37
---


# Why Playwright Locators Never Throw StaleElementReferenceException

Playwright locators do not throw `StaleElementReferenceException` because there is no
Playwright equivalent of the object that goes stale. Selenium's `WebElement` is a
handle to one specific DOM node, found once and reused; the moment that node is
removed or replaced, the handle is pointing at nothing. Playwright's `Locator` is not
a handle to a node at all. It is a saved instruction for finding one, re-run every
time you act on it, so there is no stored reference left to invalidate.

If you have spent time in a Selenium codebase, you have hit this exception, and you
have probably also hit the folklore fix for it: wrap the call in a retry, catch the
exception, re-find the element, try again. That fix works because it is
re-implementing, by hand, the thing Playwright does automatically. This page is about
why the bug exists in one architecture and cannot exist in the other, not just that
one framework happens to handle it better.

## What StaleElementReferenceException actually is

Selenium's own documentation is direct about it. The Java client's class description
for the exception reads:

> "Indicates that a reference to an element is now 'stale' - the element no longer
> appears on the DOM of the page."

Underneath the Selenium API, this maps to a WebDriver protocol-level error. The W3C
WebDriver specification defines the underlying condition this way, in the section
covering element references:

> "Every DOM element is represented in WebDriver by a unique identifying reference,
> known as a web element... When an element is no longer attached to the DOM, i.e., it
> has been removed from the document or the document has changed, it is said to be
> stale."

The spec is also explicit about the two events that invalidate a reference: a
navigation ("Upon navigation, all web element references to the previous document will
be discarded along with the document") and a DOM removal ("When a document node is
removed from the DOM, its web element reference will be invalidated"). A `WebElement`
in Selenium is a thin wrapper around exactly that reference. Find it once, and you are
holding a UUID that is only valid as long as the specific node it names stays attached
to the specific document it was found in.

## Why it happens: a reference bound to a moment, not a query

The mechanism is straightforward once you see it, and it is the reason the bug is not
a bug in the ordinary sense - it is the reference model working as designed, just not
as most people expect it to behave.

```python
from selenium import webdriver
from selenium.webdriver.common.by import By

driver = webdriver.Firefox()
driver.get("https://example.com/app")

button = driver.find_element(By.CSS_SELECTOR, "#submit")   # reference bound NOW

# something re-renders the page: a framework redraw, a partial reload,
# an AJAX response replacing the container - the DOM node #submit pointed
# to is destroyed and a new one is created in its place, even with the
# same selector matching it

button.click()   # StaleElementReferenceException: the OLD node is gone
```

`find_element` does not return "the button, described"; it returns a reference to the
one DOM node that matched at that instant. If a JavaScript framework re-renders the
container and produces a structurally identical but literally different node, the
selector would still match something today, but the object in your hand does not know
that. It is still pointing at the corpse of the old node. The new one, that a fresh
`find_element` call would return, is invisible to a reference taken before it existed.

## The Selenium-side fixes, and why they are still racing something

The community answer to this is well known: wrap interactions in retry logic that
catches the exception and re-finds the element.

```python
from selenium.common.exceptions import StaleElementReferenceException

for attempt in range(3):
    try:
        driver.find_element(By.CSS_SELECTOR, "#submit").click()
        break
    except StaleElementReferenceException:
        continue   # re-find on the next loop
```

This works, and it is also exactly what most experienced Selenium codebases end up
doing everywhere, by hand, on every interaction that might race a re-render. It is a
correct workaround for an architectural fact: the framework hands you a snapshot
reference and expects you to notice when the snapshot expired. `WebDriverWait` with an
`expected_conditions` check reduces how often you hit it, by waiting for a stability
signal before you grab the reference, but it does not remove the class of bug, because
the race between "you found it" and "you act on it" still exists in the time between
those two lines.

## Playwright's fix: never hold the reference at all

Playwright's `Locator` does not store a reference to a DOM node. Playwright's own
documentation on locators states this as the central design idea:

> "Locators are the central piece of Playwright's auto-waiting and retry-ability. In a
> nutshell, locators represent a way to find element(s) on the page at any moment...
> Every time a locator is used for an action, an up-to-date DOM element is located in
> the page."

The distinction is spelled out even more directly in Playwright's notes on
`ElementHandle`, the one Playwright API that *does* behave like Selenium's reference
model and that Playwright explicitly steers you away from:

> "The difference between the Locator and ElementHandle is that the ElementHandle
> points to a particular element, while Locator captures the logic of how to retrieve
> an element... If that element changes text or is used by React to render an entirely
> different component, handle is still pointing to that very DOM element. This can
> lead to unexpected behaviors."

And, plainly:

> "The use of ElementHandle is discouraged, use Locator objects and web-first
> assertions instead."

A `page.locator("#submit")` call does not search the page at all. It builds a small
object that knows *how* to search the page, and defers the actual search to the moment
you call `.click()`, `.fill()`, or any other action. Rerun that action twice, and the
DOM is queried twice, fresh each time:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/app")

    submit = page.locator("#submit")   # a query, not a reference: nothing found yet

    # a re-render happens here for any reason - the old node is gone,
    # a new #submit exists in its place

    submit.click()   # resolves NOW, against the CURRENT DOM. Finds the new node.
```

There is no stored UUID to go stale, because there is nothing stored except the
selector string and the retry/timeout configuration around it. Every action is
"find the thing, then act on it," fused into a single call, which removes the gap
where the world could change out from under a saved reference. This is also why
Playwright's auto-waiting exists as one mechanism rather than two: the same act of
re-resolving on every call is what lets Playwright retry a `.click()` for several
hundred milliseconds while a spinner clears, instead of needing a separate
`WebDriverWait` step before the interaction.

## What this does not fix

Locators remove one specific bug class; they do not make timing free. Two things
worth knowing before assuming "it just works" covers everything:

- **Locator strictness throws a different error on purpose.** If a selector matches
  more than one element, Playwright raises rather than silently acting on the first
  match. That is not staleness, it is Playwright refusing an ambiguous target -
  narrow the selector instead of treating it as the same class of failure.
- **A full navigation still destroys the JavaScript execution context**, and a
  `Locator` does not save you from evaluating code or reading a value against a
  document that has already gone. [The "Execution context was destroyed" error](execution-context-destroyed.md)
  is Playwright's version of a timing race, just at a different layer than element
  references, and the fix is the same shape as it always is: wait for the navigation
  event before continuing, and re-query afterward. The general discipline for picking
  the right wait signal in the first place is in
  [how to wait for content to load in Playwright](how-to-wait-for-page-load-playwright.md).

So the honest claim is narrow and accurate: the specific failure mode of "I found an
element, the page changed, my old reference is now garbage" cannot happen with
Playwright's `Locator`, because there is no old reference sitting around to become
garbage. Broader timing problems in a dynamic page are a separate topic, with their own
fixes.

## Conclusion

`StaleElementReferenceException` is not a Selenium defect to be patched around. It is
the direct, documented consequence of a reference model where finding an element and
acting on it are two separate steps, and anything can happen to the DOM in between.
Playwright's `Locator` collapses those two steps into one and re-runs the "find" half
on every single action, so the object you hold is never a snapshot of a moment that has
passed. Porting a Selenium codebase to Playwright does not require porting the retry
loops that work around this. The loops become unnecessary, not smaller.

## Short answers to the questions that lead here

**Can a Playwright Locator go stale?** No. A `Locator` does not hold a reference to a
DOM node; it holds a query that gets re-run every time you call an action on it, so
there is no stored reference to invalidate.

**Does Playwright have anything like StaleElementReferenceException?** Not for
locators. `ElementHandle`, an older and now-discouraged Playwright API, does hold a
fixed reference and can behave unexpectedly if the node it points to changes, which is
exactly why Playwright's own docs recommend `Locator` over it.

**Why did my Selenium script fail with StaleElementReferenceException after a click?**
Almost certainly the click triggered a DOM update, an AJAX-driven redraw, or a
navigation that replaced or removed the node your `WebElement` pointed to. The fix is
to find the element again after the change, not to reuse the old reference.

**Does retrying the find_element call fix it in Selenium?** Yes, functionally. A
catch-and-retry loop around `find_element` reimplements, per call site, what
Playwright's `Locator` does automatically on every action.

**Is this a stealth or fingerprinting concern?** No, it is a pure automation-reliability
difference, not a detection surface. It affects whether your script crashes on a
dynamic page, not whether a site can tell a script is present.

**Does this mean my Playwright script can never fail on timing?** No. Locators remove
the reference-staleness bug specifically. A full page navigation destroying the
execution context, or a selector matching more than one element, are different failure
modes with their own fixes.

## Sources

- Selenium's own Java API documentation, [`StaleElementReferenceException`](https://www.selenium.dev/selenium/docs/api/java/org/openqa/selenium/StaleElementReferenceException.html),
  retrieved 2026-08-30, for the exact class description quoted above.
- The W3C WebDriver specification's [stale element reference error](https://developer.mozilla.org/en-US/docs/Web/WebDriver/Reference/Errors/StaleElementReference)
  as documented on MDN, retrieved 2026-08-30, for the definition of a web element
  reference and the two events (navigation, DOM removal) that invalidate one.
- Playwright's own [Locators guide](https://playwright.dev/docs/locators), retrieved
  2026-08-30, for the "up-to-date DOM element is located" quote.
- Playwright's own [`ElementHandle` API reference](https://playwright.dev/python/docs/api/class-elementhandle),
  retrieved 2026-08-30, for the discouragement note and the Locator-versus-ElementHandle
  comparison quoted above.

**See also:** [Migrating from Selenium to Playwright for stealth](migrate-selenium-to-playwright-stealth.md)
for the rest of the port, including the driver-layer signal this page does not cover;
["Execution context was destroyed", and when it means detection](execution-context-destroyed.md)
for the timing failure Locators do not fix; and
[how to wait for content to load in Playwright](how-to-wait-for-page-load-playwright.md)
for picking the right wait signal in a dynamic page.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. None of this is a stealth
feature. It is just one fewer class of bug to carry over from a Selenium codebase.*
