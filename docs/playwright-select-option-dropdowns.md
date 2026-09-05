---
title: "Select from dropdowns with Playwright, native and custom"
description: "select_option() drives a real <select>; a custom listbox needs a click, a wait, then a click. How to tell the two apart and drive both in Playwright."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 148
---


# Select from dropdowns with Playwright, native and custom

A dropdown in Playwright is one of two things wearing the same look: a real `<select>`,
driven with `locator.select_option()`, or a custom widget built from a div and some
JavaScript, driven by clicking the trigger, waiting for the option list, and clicking the
option. Telling them apart takes one line of code.

## The two jobs hiding inside one dropdown

A native `<select>` is a browser control: the browser owns the open state, the keyboard
navigation, and the value, and Playwright drives all of it through one method,
`select_option()`. You never open the list yourself; the call selects the option and
reports back which values ended up chosen.

A custom dropdown is not a form control at all, usually a button or div with
`role="combobox"` or `role="listbox"` behind it, with the option list only present in the
DOM while it is open. There is no select-an-option API for that, so you drive it the way
a person would: click to open, wait for the option to render, click it. Both look
identical by eye; only the tag name tells you which one you have.

## Tell them apart in one line

Read the tag name of the dropdown's visible trigger. A real `<select>` reports `SELECT`;
anything else is a custom widget you have to click open.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/form")

    tag = page.locator("#country").evaluate("el => el.tagName")
    is_native = tag == "SELECT"
    print(tag, is_native)
```

Do this once, while writing the script; the tag does not change between runs, so there
is no need to check it live on every navigation.

## Selecting from a native select

For a real `<select>`, `Locator.select_option()` takes the option's `value`, its visible
`label`, or its zero-based `index`, and returns the list of values that ended up selected:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/form")

    page.locator("#country").select_option(value="IT")
    page.locator("#country").select_option(label="Italy")
    page.locator("#country").select_option(index=2)
```

Pick `label` when you only know the visible text (a country name); pick `value` when you
know the underlying code and want the match to survive a copy change. The method already
waits for the element to be attached and enabled, the same checks Playwright applies
before a click.

## A multi-select select takes a list

A `<select multiple>` accepts more than one option, and `select_option()` mirrors that:
pass a list and every value in it gets selected, replacing whatever was chosen before.

```python
page.locator("#toppings").select_option(["olives", "mushrooms"])
```

That is the whole difference: a string or dict selects one option, a list selects
several. The same idea reappears in a faceted search page that lets you tick several
categories at once; see
[scraping multi-select facets](how-to-scrape-multi-select-facets-playwright.md) for that
pattern.

## Selecting from a custom dropdown

A custom dropdown has no `select_option()` to call. You open it, wait for the option to
exist, and click it, the same three steps a person takes:

```python
page.get_by_role("combobox", name="Country").click()

option = page.get_by_role("option", name="Italy")
option.wait_for(state="visible")
option.click()
```

`.click()` already waits for the target to be visible and stable, so the explicit
`wait_for()` above mostly documents the two separate steps: open, then find. A fast page
works without it; a slow one just retries the click until the option exists.

## ARIA roles keep a custom dropdown readable

`page.get_by_role()` locates elements by accessible role and name rather than by class
name, which for a custom dropdown survives a CSS refactor that would break a class
selector. `role="combobox"` marks the trigger, `role="listbox"` marks the open list, and
`role="option"` marks each choice, roles the widget already carries for its own
accessibility tree.

No ARIA roles at all still means clickable, just less pleasant: fall back to a
`data-testid` or a stable class, and treat the gap as a sign this widget was not built
with accessibility in mind.

## When the option list only appears after you click

Some dropdowns fetch their options from the network the moment they open, an
autocomplete city picker, a search-as-you-type combobox, anything too large to ship up
front. The click that opens the trigger and the request that fills the list are two
different events; clicking an option before the request resolves finds nothing there.

```python
page.get_by_role("combobox", name="City").click()

# Scope the wait to the list. A bare get_by_role("option") also matches the
# options inside every native <select> on the page, and .first will be one of
# those: measured, it waits 30 seconds and times out on an element that is
# real but not visible.
options = page.get_by_role("listbox").get_by_role("option")
options.first.wait_for(state="visible")
page.get_by_role("option", name="Rome").click()
```

Waiting on the first option instead of a fixed delay makes the script wait exactly as
long as the network takes. The same idea, wait for the thing that has to appear rather
than a fixed number of seconds, is the whole subject of
[how to wait for a page to finish loading](how-to-wait-for-page-load-playwright.md).

## select_option can succeed while the page never notices

Here is the failure mode that costs an afternoon: `select_option()` reports success, the
DOM shows the right option selected, and the page's own behaviour, a totals
recalculation, a chart redraw, a results grid re-sort, never happens. Nothing threw. The
value is correct. The page just did not react.

Playwright fires both an `input` and a `change` event once the selection lands, which
covers most sites, and both arrive with `isTrusted` set to `true`: measured on 5
September 2026 on a local page that logs every event it receives, three selections in a
row, six events, `isTrusted` true on all of them. So a framework that gates on
`event.isTrusted` before reacting is not the explanation when nothing moves. Some
pages instead listen for a `keyup` from arrow-keying through the list, or a `mousedown`
on the option, because that is how the widget was built by hand, and `select_option()`
never fires either one.

When the value changes but nothing downstream moves, drive it from the keyboard instead.
The order matters more than it looks, and this is the part worth measuring rather than
copying from a blog:

```python
# Works: focus, then arrow. Each press moves the selection one option down.
page.locator("#sort").focus()
page.locator("#sort").press("ArrowDown")

# Also works: type the first letter of the option's label.
page.locator("#sort").focus()
page.locator("#sort").press("I")     # jumps to "Italy"
```

Measured on 5 September 2026 against a three-option native select starting on the first
option: `focus()` then one `ArrowDown` moved it to the second, two presses moved it to
the third, and pressing the label's first letter jumped straight to that option. The
sequence you will see recommended most often, `click()` then `ArrowDown` then `Enter`,
**left the value untouched** on the same page: clicking a native select opens the
platform's own dropdown, and the key presses that follow do not land where you expect.
Focus without clicking, and skip the `Enter`.

## Conclusion

Work out which dropdown you have before writing the rest of the script:
`locator.evaluate("el => el.tagName")` answers it in one line. A native `SELECT` takes
`select_option()`, with `value`, `label`, `index`, or a list for a multiple select; a
custom dropdown takes a click, a wait, and a click on the option, and ARIA roles make
that sequence readable. Whatever the dropdown filters, that result is usually what you
came to scrape; see
[how to scrape HTML tables with Playwright](how-to-scrape-html-tables-playwright.md).

## Short answers to the questions that lead here

**How do I select a dropdown option in Playwright with Python?** Call
`locator.select_option(value=...)`, `label=...`, or `index=...` on a real `<select>`. For
a custom widget, click the trigger, wait for the option, then click it.

**How do I know if a dropdown is a real select or a custom one?** Read its tag name:
`locator.evaluate("el => el.tagName")`. `SELECT` means use `select_option()`; anything
else means click, wait, click.

**How do I select multiple options in Playwright?** Pass a list to `select_option()`,
for example `select_option(["olives", "mushrooms"])`. Every value in the list gets
selected in one call.

**Why did select_option() work but the page did not update?** The listener is probably
bound to a keyboard or mouse event, not `change`. Open the dropdown and press
`ArrowDown` then `Enter` instead.

**Why can't I click an option I know is on the page?** It likely has not rendered yet.
Many custom dropdowns fetch their options after the click that opens them; wait for the
option before clicking it.

**See also:** [scraping multi-select facets](how-to-scrape-multi-select-facets-playwright.md),
[waiting for a page to finish loading](how-to-wait-for-page-load-playwright.md), and
[scraping HTML tables once the filter is applied](how-to-scrape-html-tables-playwright.md).

## Sources

- Playwright documentation, [Input: select options](https://playwright.dev/python/docs/input#select-options), covering `select_option()` and the events it fires.
- Playwright documentation, [Locator.select_option()](https://playwright.dev/python/docs/api/class-locator#locator-select-option).
- Playwright documentation, [Locator.get_by_role()](https://playwright.dev/python/docs/api/class-page#page-get-by-role), the ARIA role locator strategy.
- Playwright, Input, https://playwright.dev/python/docs/input - "Selects one or multiple
  options in the `<select>` element with locator.select_option(). You can specify option
  `value`, or `label` to select. Multiple options can be selected." Read 5 September 2026.
- Our own measurement, 5 September 2026, on a local page: `select_option()` by value, by
  label and by index each returned the list of selected values; a multiple select
  accepted a list; both `input` and `change` fired with `isTrusted` true; and calling
  `select_option()` on a non-select element raised `Error: Locator.select_option: Element
  is not a <select> element`.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The dropdown API here is
stock Playwright end to end; nothing about it changes on this engine.*
