---
title: "Playwright Strict Mode Violation: Resolved to N Elements"
description: "Playwright refuses to guess which of several matching elements you meant. The fix is a more specific locator, not disabling strict mode or reaching for .first()."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 20
---


# Playwright Strict Mode Violation: Resolved to N Elements

```
Error: locator.click: strict mode violation: locator('button') resolved to 3 elements
```

Playwright's locators are strict by design: "all operations on locators that imply some
target DOM element will throw an exception if more than one element matches," in
Playwright's own words. This is not a bug in your selector and it is not flakiness. It
is Playwright refusing to guess on your behalf, and the fix that actually holds up is
making the locator name the one element you meant, not forcing Playwright to pick one
for you.

The error fires the moment the count is wrong, ahead of the actionability retries that
handle a genuinely slow-loading element, which is why it reads as immediate rather than
as a timeout.

## Why strict mode exists at all

Before locators, Playwright and its predecessors would happily click "the first match"
when a selector found several elements, silently. That is a worse failure mode than an
exception: a test that clicks the wrong button because two elements matched the same
selector still passes, and the mistake surfaces later, if at all, as a result nobody
can explain. Strict mode converts a silent wrong click into a loud, immediate one.

That trade is deliberate and it is why the fix is never "make the error go away." Making
it go away without narrowing the locator restores exactly the ambiguity strict mode
exists to catch.

## The two ordinary causes

**A locator that matches more than you meant it to.** A generic tag or role selector -
`page.get_by_role("button")`, `page.locator("a")`, text that also appears in a footer or
a navigation bar - matches every element on the page that fits the description, not the
one your test cares about. This is the majority of reports: a selector written against
a mental picture of one section of the page actually reaches the whole document.

**A framework rendering more copies than you can see.** A React `Fragment`, a hidden
duplicate kept in the DOM for a transition, or a list item rendered twice during a
re-render can put two elements in the accessibility tree that look identical to a
locator even though only one is visible to a person. The count is still real; it is the
premise that "there's obviously only one of these" that was wrong.

## What actually fixes it, in order of preference

**Scope to the nearest stable container.** Chain the locator through a parent that is
itself unique - a row, a card, a labeled section, an ARIA landmark - so the match happens
inside that subtree instead of across the whole page:

```python
# too broad: matches every "Delete" button on the page
page.get_by_role("button", name="Delete").click()

# scoped: matches the one inside this specific row
row = page.get_by_role("row", name="Invoice #4471")
row.get_by_role("button", name="Delete").click()
```

**Filter on something that actually distinguishes the element.** `.filter(has_text=...)`
or `.filter(has=...)` narrows a locator by content or by a nested element, which is
different from picking a position - it says what makes this one the right one.

**Use a locator that names the element directly.** A `data-testid`, an
[accessible name](https://playwright.dev/python/docs/locators#locate-by-role), or a
label is worth adding to the page under test specifically because it removes the
ambiguity at the source rather than working around it downstream.

## What not to reach for first

**`.first()`, `.last()`, `.nth()`.** Playwright's own documentation calls these "not
recommended" for exactly this case, because a positional pick is fragile: it is correct
only as long as the page's element order does not change, and nothing announces when it
does. Reordering a list, adding a new row above the one you meant, or a responsive layout
that renders elements in a different sequence on a smaller viewport all silently break a
`.first()` that happened to work. A list that rebuilds itself between the pick and the
click produces
[the not-attached error instead](playwright-element-not-attached-to-dom.md), which is
the same fragility arriving as a different message.

**`{force: true}` or `strict_selectors=False`.** Neither narrows the match; both just
suppress the check, and `force` has
[its own way of going wrong on a click that is merely covered](playwright-element-click-intercepted.md):
it delivers the click at the coordinates regardless of what is on top of them. `strict_selectors=False` in particular reproduces the exact failure
mode strict mode was built to prevent: Playwright picks one of the matches for you, and
which one it picks is not something the error message ever told you to verify. If the
count is ever wrong again after the page changes, nothing will tell you.

The honest reading of both of these: they make the error disappear without answering
the question the error asked, which is "which element did you actually mean."

## Diagnostic checklist

1. **Read the count.** "Resolved to 2 elements" and "resolved to 6 elements" point at
   different bugs; six is a rendered list, two is usually a hidden duplicate or a
   footer/header collision.
2. **Print what actually matched.** `locator.count()` and a loop over
   `locator.nth(i).text_content()` or `all_text_contents()` shows you the real matches,
   not the one you assumed.
3. **Check the DOM, not just the rendered page.** Open dev tools and search for the
   selector; a duplicate that is hidden with CSS still counts as a DOM match unless you
   scope the locator to visible elements.
4. **Reproduce with a trace.** Playwright's trace viewer shows the DOM snapshot at the
   moment of the failed action, which settles "was there really more than one" without
   guessing from a screenshot.
5. **Re-test after any dynamic update.** A locator scoped correctly today can stop being
   unique after content changes; re-run the same check once the page has finished
   loading everything it lazily adds.

## What invisible_playwright does and does not touch here

Strict mode, locator resolution and the DOM a page renders are entirely Playwright's and
the target page's concerns. This project changes what the browser reports about itself
at the engine level; it does not change how many elements a CSS selector matches or how
Playwright counts them. A strict-mode violation on a patched Firefox has the identical
cause and the identical fix as the same violation on a stock browser.

## Conclusion

A strict-mode violation is Playwright telling you, precisely and immediately, that your
locator does not uniquely identify the element you are about to act on. Scoping the
locator to a stable container, filtering on real content, or naming the element directly
all fix the actual ambiguity. Picking a position or disabling the check does not; it just
moves the ambiguity from a loud exception to a silent, unverified guess.

## Short answers to the questions that lead here

**What does "strict mode violation: resolved to N elements" mean?** The locator matched
N elements where the action requires exactly one, and Playwright refused to guess which
one you meant.

**Is `.first()` a valid fix?** It stops the error, but it is fragile: it is only correct
while the element order does not change, and Playwright's own documentation calls it not
recommended for that reason.

**Should I set `strict_selectors=False`?** No, unless you specifically want Playwright to
silently pick a match for you, which reintroduces the exact ambiguity strict mode exists
to prevent.

**Why did this only start failing after I changed the page?** Because a locator that was
accidentally unique before is no longer unique after a new element with a matching role,
text or tag was added. The locator was always too broad; the page just stopped hiding it.

**Does this mean my test is flaky?** No. A strict-mode violation is deterministic given
the same DOM; it is not the intermittent-timing failure that "flaky" usually describes.

**Can two elements look the same in the accessibility tree but not visually?** Yes. A
Fragment, a transition-hidden duplicate, or an off-screen copy can all match a role or
text locator identically while only one is visible to a person.

## Sources

- Playwright's own documentation, [Locators - Strictness](https://playwright.dev/python/docs/locators#strictness),
  for the exact design statement quoted above and the guidance against `.first()` /
  `.last()` / `.nth()` as a first resort.
- Microsoft Playwright issue [#30069](https://github.com/microsoft/playwright/issues/30069),
  a strict-mode violation from `.or_()` combining two locators that both matched.
- Microsoft Playwright issue [#19398](https://github.com/microsoft/playwright/issues/19398),
  strict-mode behavior interacting with `strict_selectors=False`.
- Microsoft Playwright issue [#10611](https://github.com/microsoft/playwright/issues/10611),
  an early user question on what the violation means, answered by a maintainer.

**See also:** [Playwright "Element Is Not Attached to the DOM"](playwright-element-not-attached-to-dom.md),
another case where the fix is re-querying with a locator rather than trusting a stale
reference, and [Firefox preferences that silently do nothing](firefox-prefs-not-applying.md),
for the project's other write-up on why "the check passed" and "the thing worked" are not
the same claim.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Locator resolution is
Playwright's own logic and behaves identically underneath a patched engine or a stock
one.*
