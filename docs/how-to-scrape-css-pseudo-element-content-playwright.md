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

## A complete extractor that never loses the invisible half

```python
import json, time
from invisible_playwright import InvisiblePlaywright

CARD = """
(sel) => Array.from(document.querySelectorAll(sel)).map(card => {
  const read = (el) => {
    if (!el) return null;
    const before = getComputedStyle(el, '::before').content;
    const after  = getComputedStyle(el, '::after').content;
    const clean  = (c) => (!c || ['none', 'normal', '""'].includes(c) || c.startsWith('url('))
      ? '' : c.replace(/^["']|["']$/g, '');
    return {
      dom: el.textContent.trim(),
      before: clean(before),
      after: clean(after),
      classes: el.className,
    };
  };
  return {
    price: read(card.querySelector('.price')),
    badge: read(card.querySelector('.badge')),
    href:  card.querySelector('a')?.getAttribute('href') || null,
  };
})
"""

def full_text(part):
    if not part:
        return None
    return (part["before"] + part["dom"] + part["after"]).strip() or None

with InvisiblePlaywright(seed=42) as browser, open("cards.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    page.goto("https://example.com/category", wait_until="domcontentloaded")
    page.wait_for_selector(".product-card")

    for card in page.evaluate(CARD, ".product-card"):
        out.write(json.dumps({
            "href": card["href"],
            "price_rendered": full_text(card["price"]),      # "€19.99"
            "price_dom_only": (card["price"] or {}).get("dom"),  # "19.99"
            "badge_rendered": full_text(card["badge"]),
            "badge_classes": (card["badge"] or {}).get("classes"),
            "observed_at": time.time(),
        }, ensure_ascii=False) + "\n")
    out.flush()
```

Composing `before + dom + after` reproduces what a reader sees, which is the value you
usually want. Keeping `price_dom_only` alongside it is the cheap insurance: when the two
differ, you know a pseudo-element was carrying something, and when they stop differing after
a site update you know the currency symbol moved into the DOM rather than disappearing.

## The failure mode is silence, not refusal

Nothing here is a defence, and that is worth stating because it changes how you find it.
Pseudo-element content is a styling choice, and no site is hiding a price from you by
putting the euro sign in a `::before`. The consequence is that no error, status code or
challenge will ever tell you that you missed it.

That leaves three habits that actually catch it.

**Probe once per new site**, with the audit snippet above, before writing any selectors.
Ten seconds at the start against weeks of a silently wrong column.

**Compare rendered text against extracted text** on a sample. If the page shows `€19.99`
and your row says `19.99`, something is generating the difference, and pseudo-elements are
the first suspect:

```python
    shown = page.eval_on_selector(".price", "e => e.getBoundingClientRect() && e.innerText")
```

**Prefer the class to the rendered word** for status badges. `badge_classes` survives
localisation and copy changes, while the rendered "Sold out" becomes "Ausverkauft" for a
German visitor and breaks any filter built on the string.

If a page genuinely renders text you cannot reach by any of these routes, the remaining
possibilities are shadow DOM, canvas, or an image, and each has its own page. What almost
never explains it is blocking, which is why this page does not send you to
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md) first: that is
the right guide when content is absent, not when it is visible and unreadable.

## What to store, and why both forms

| field | example | why keep it |
|---|---|---|
| `price_rendered` | `€19.99` | what a person sees |
| `price_dom_only` | `19.99` | detects the day the markup changes |
| `badge_rendered` | `Sold out` | human readable |
| `badge_classes` | `badge badge--soldout` | stable across languages and copy |

Two columns for one visible string looks redundant and costs almost nothing. It converts a
class of silent breakage into a visible one: a diff between the two columns is a fact about
the site's markup, and a diff that suddenly disappears across a whole crawl is the earliest
warning you will get that the page was redesigned under you.
