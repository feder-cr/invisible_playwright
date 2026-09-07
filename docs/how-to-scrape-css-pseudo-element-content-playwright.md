---
title: "How to scrape CSS pseudo-element content with Playwright"
description: "Read text injected by ::before and ::after with Playwright: it is not in the DOM and inner_text will not return it, so query the computed style instead."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 169
---


# How to scrape CSS pseudo-element content with Playwright

Text injected by `::before` and `::after` is **not in the DOM**, so `inner_text` and
`text_content` will never return it. Read it with
[`getComputedStyle`](https://developer.mozilla.org/en-US/docs/Web/API/Window/getComputedStyle)
passing the pseudo-element as the second argument, and read the `content` property.

This catches people because the text is plainly visible on screen. You can select it in
some browsers, you can screenshot it, and every DOM-based extraction returns an empty
string. Nothing errors, which is the worst version of the problem: the row is written with
a blank field and the run reports success.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/1234")

    value = page.eval_on_selector(
        ".badge",
        "el => getComputedStyle(el, '::after').content",
    )
    print(value)        # '"In stock"'  including the quotes
```

## The value comes back quoted, and sometimes as none

`content` is a CSS value, not a string, so it arrives with its quotation marks and needs
unwrapping. It can also be the keyword `none` when no rule applies, and `normal` on
elements where the pseudo-element does not generate a box:

```python
def pseudo_text(page, selector, pseudo="::after"):
    raw = page.eval_on_selector(
        selector,
        "(el, p) => getComputedStyle(el, p).content",
        pseudo,
    )
    if raw in (None, "none", "normal", '""'):
        return None
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw
```

Returning `None` for `none` matters. An empty string and an absent pseudo-element are
different facts, and only the second means the rule did not apply to this element.

## Four things `content` can hold besides text

The property is more expressive than a string, and each form needs different handling:

- **A literal string**, which is the common case above.
- **An `attr()` reference**, such as `content: attr(data-label)`. Computed style may
  return the resolved text or the function itself depending on the engine, so read the
  attribute directly as a fallback: it is in the DOM and always available.
- **A `url()`**, meaning the visible thing is an image, and there is no text to recover.
- **A counter**, from `counter()` or `counters()`, which is how numbered lists and section
  numbers are often rendered. The computed value gives you the rendered number.

```python
    raw = pseudo_text(page, ".step")
    if raw and raw.startswith("attr("):
        attr = raw[len("attr("):-1].strip()
        raw = page.get_attribute(".step", attr)
```

## Reading many elements at once

Doing this per element from Python costs a round trip each time. For a list, collect them
in one evaluation:

```python
ALL = """
(sel) => Array.from(document.querySelectorAll(sel)).map(el => ({
  text: el.textContent.trim(),
  before: getComputedStyle(el, '::before').content,
  after: getComputedStyle(el, '::after').content,
}))
"""
    rows = page.evaluate(ALL, ".product-card .label")
```

One evaluation is also one moment in time, which matters on pages where a class is toggled
by script and the pseudo-element content changes with it.

## Where this actually shows up

Four patterns account for almost all real cases:

**Status badges.** "New", "Sold out", "Sale" are frequently pure CSS, driven by a class on
the parent. The class itself is often the better field to capture, since it is stable while
the rendered word is localised.

**Currency symbols and units.** A price of `19.99` in the DOM with the currency in a
`::before` produces a dataset of bare numbers whose currency you cannot recover later. This
is the case where the omission does the most damage, because the number looks complete.

**Required-field markers and icons.** Usually noise, worth recognising so you do not chase
them.

**Row numbering.** Counters render an index that has no DOM representation at all, which
matters when the number is the identifier a user would quote.

## Check for it deliberately, rather than discovering it

The reliable way to find out whether a page uses this is to ask, once, before writing
selectors:

```python
FIND = """
() => {
  const out = [];
  document.querySelectorAll('*').forEach(el => {
    for (const p of ['::before', '::after']) {
      const c = getComputedStyle(el, p).content;
      if (c && !['none', 'normal', '""'].includes(c) && !c.startsWith('url('))
        out.push({ tag: el.tagName, cls: el.className, pseudo: p, content: c });
    }
  });
  return out.slice(0, 50);
}
"""
    print(page.evaluate(FIND))
```

Run that on one representative page of a new site. It takes a second and tells you whether
any visible text is missing from your extraction, which is a question that is otherwise
answered weeks later by someone noticing a column of blanks.

The same class of invisibility appears in two other places worth knowing:
[shadow DOM content](how-to-scrape-shadow-dom-playwright.md), which needs a different
traversal, and canvas rendering, where there is no text at all and the approach is in
[extracting data from canvas charts](how-to-extract-data-from-canvas-charts-playwright.md).
