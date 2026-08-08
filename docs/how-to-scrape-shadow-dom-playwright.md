---
title: "How to scrape shadow DOM content with Playwright"
description: "Playwright locators pierce open shadow roots automatically, so css and text selectors reach into web components. Closed roots stay unreachable by design."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 22
---


# How to scrape shadow DOM content with Playwright

Playwright locators already cross open shadow roots, so you do not need a special
traversal helper to scrape shadow DOM content. A `css=` or text selector reaches into a
web component with no extra API, because the selector engine descends through open roots
on its own. The one real limit is closed roots, and that limit is honest and absolute: no
tool reaches them, because it is a DOM boundary rather than a detection surface.

Most people arrive here convinced otherwise: that shadow DOM needs a recursive walk that
hops from host to `shadowRoot` to host again, or a library that promises to "pierce" the
boundary for you. For the common case that belief is simply wrong, and acting on it
produces slower, more fragile code than doing nothing.

## What a shadow root actually is

A web component attaches a shadow root to a host element, and its internal markup lives
inside that root instead of in the main document tree. That is the whole point: the
component's internals are encapsulated so that page-level CSS and page-level scripts do
not accidentally reach in.

The root is created in one of two modes, per the
[`attachShadow` specification](https://developer.mozilla.org/en-US/docs/Web/API/Element/attachShadow):

- **Open.** `attachShadow({ mode: "open" })`. The host exposes an `.shadowRoot`
  property, so anything with a reference to the host can walk into the tree.
- **Closed.** `attachShadow({ mode: "closed" })`. The host's `.shadowRoot` reads back
  `null`. The subtree exists and renders, but the document has no handle to it.

The distinction is the entire story of this page. Almost every component you meet in the
wild is open, because closed mode breaks accessibility tooling and the component author's
own testing, so authors rarely choose it.

## Why document.querySelector stops at the boundary

`document.querySelector` returns `null` on shadow content because it searches only the
light DOM and stops at the host element, never descending into a shadow root, open or
closed. That is the source of the confusion. The content you want is one boundary further
in, so the call returns `null` even though the element is visible on screen.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # The DOM's native query does NOT cross the shadow boundary.
    inner = page.evaluate("() => document.querySelector('.price-inside-component')")
    print(inner)   # -> None, even though the element is visible on screen
```

People see that `None`, conclude that shadow DOM is a wall, and go looking for a piercing
helper. The wall is real for `document.querySelector`. It is not real for the tool you
are already holding.

## Playwright locators cross open shadow roots with no special API

Playwright's [selector engine](https://playwright.dev/python/docs/locators) is not the
DOM's. When you write `page.locator("css=...")` or a text selector, the engine walks
open shadow roots as part of its normal descent. You do not opt in, you do not name the
host, and you do not chain through `shadowRoot`. The same selector you would write for
an ordinary element finds the element inside an open component.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # Same selector, but through Playwright's engine: it descends into the
    # open shadow root on its own.
    price = page.locator(".price-inside-component").inner_text()
    print(price)   # -> the real text, read from inside the component

    # Text selectors cross the boundary too.
    add = page.get_by_text("Add to cart")
    add.click()
```

The measurement that makes this concrete is a direct A/B on the same page and the same
element: the DOM's `document.querySelector` returns `null`, and `page.locator(...)` with
the identical CSS returns the element and its text. One number, two ways of asking, two
different answers, and the difference is entirely which engine did the query. Run both in
the same session and you will never reach for a traversal helper again.

Two practical consequences follow:

- **CSS combinators do not cross the boundary; the engine's descent does.** A single
  selector string like `.host .inner` written as one CSS rule will not match across the
  boundary, because CSS combinators respect encapsulation. But `page.locator(".inner")`
  on its own finds `.inner` inside the open root, because the engine steps into the root
  and then applies the selector there. If you need to scope, chain locators
  (`page.locator("my-widget").locator(".inner")`) rather than writing one combinator.
- **`page.query_selector` and `page.locator` both descend; `page.evaluate` with raw DOM
  calls does not.** The moment you write JavaScript by hand inside `evaluate`, you are
  back on the DOM's own rules and the boundary reappears.

## Closed shadow roots: the honest limit

A closed shadow root is unreachable, and no scraping tool, stealth engine or otherwise,
changes that. This is the part most pages skip. The host's `.shadowRoot` is `null` by the
component author's explicit choice, so there is no handle for any query to follow. This
is not a detection surface and there is nothing to spoof or patch: it is a DOM boundary,
the same one for a stock browser and an automated one.

If a locator cannot find content and you have confirmed the element renders, check
whether the host was attached in closed mode. If it was, the honest answer is that you
cannot reach it from the DOM at all. The realistic routes are all outside the DOM:

- Read the data from the network response that populated the component, before it was
  rendered, rather than from the rendered tree.
- Read it from a `<script type="application/json">` payload or a data attribute in the
  light DOM, if the component was hydrated from one.
- Accept that a genuinely closed component with no other data source is not scrapable
  through selectors, and stop spending time on selector variations that cannot work.

Do not trust a blog snippet that claims to "bypass" closed shadow roots by monkeypatching
`Element.prototype.attachShadow` before the component runs. That works only if you can
inject before the page's own scripts, it changes observable behaviour the page can notice,
and it is exactly the kind of environment tampering that consistency checks like
[CreepJS](creepjs-explained.md) are built to catch. A quiet, real browser that reads what
it can and leaves the rest is a better position than a loud one that reaches for
everything.

## Keeping one identity while you crawl components

Component-heavy pages tend to be multi-step: expand a section, wait for a lazily rendered
web component, read it, move on. Two things make that reliable.

First, wait for the component's content, not a fixed sleep. A locator's own waiting is
usually enough, and for content that grows after interaction the pattern is the same one
used [when scrolling loads more items](how-to-scrape-infinite-scroll-playwright.md): wait
for a signal that the new nodes exist rather than guessing at a delay.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog")

    page.get_by_role("button", name="Show details").click()

    # Locator auto-waits for the element to appear inside the open component,
    # then reads it. No fixed sleep, no manual shadowRoot walk.
    detail = page.locator("product-card .spec-value")
    detail.first.wait_for()
    for value in detail.all_inner_texts():
        print(value)
```

Second, keep the identity fixed while you iterate. Passing `seed=42` gives the same
fingerprint on every run (GPU, canvas, audio, fonts, screen), so a page that renders its
components differently between two runs is the page changing, not your machine changing.
That is the difference between debugging a component-loading failure and guessing at one,
and if you need a specific field held constant across runs, [pinning a single fingerprint
field](pinning.md) leaves the rest seed-derived. When a page turn resets the DOM and your
locators go stale, that is the ordinary [execution-context lifecycle](execution-context-destroyed.md),
not a shadow-DOM problem.

## Conclusion

Open shadow roots need no special handling in Playwright: `css=` and text selectors, and
`page.locator`, descend into them automatically, while the DOM's own `document.querySelector`
stops at the host. Prove it to yourself with one A/B on a single element and you can
delete every piercing helper you were about to install. Closed roots are the honest
exception: they are unreachable by design, no tool changes that, and the right move is to
read the data from the network or the light DOM instead of fighting a boundary that does
not move.

## Short answers to the questions that lead here

**Do I need a special helper to scrape shadow DOM in Playwright?** No, not for open
roots. A normal `page.locator("css=...")` or text selector descends into open shadow
roots on its own. The helper you were about to install does what the engine already does.

**Why does document.querySelector return null on an element I can see?** Because the DOM's
native query stops at the shadow boundary and the element is one root further in. Use
`page.locator` instead, which crosses open roots.

**Can Playwright get into a closed shadow root?** No. A closed root reads back `.shadowRoot
== null`, so there is no handle for any query to follow. This is a DOM boundary, not
something a stealth engine can change.

**How do I know if a root is open or closed?** Check the host element's `.shadowRoot` in
the page. A non-null value is open and reachable; `null` is either no shadow root or a
closed one, and both are unreachable through selectors.

**Should I monkeypatch attachShadow to force closed roots open?** It is fragile, needs
injection before the page's scripts, and is observable tampering that consistency checks
flag. Prefer reading the underlying data from the network response or a light-DOM payload.

**Do CSS combinators reach across the boundary?** No. `.host .inner` as one CSS rule
respects encapsulation and will not match across the root. Chain locators instead:
`page.locator("host-element").locator(".inner")`.

## Sources

- [Playwright's own locators documentation](https://playwright.dev/python/docs/locators),
  which states that locators work with shadow DOM by default, confirmed here by the direct
  A/B on the same element: the DOM's `document.querySelector` returning `null` while
  `page.locator` with the identical CSS returns the element.
- [MDN's `Element.attachShadow()` reference](https://developer.mozilla.org/en-US/docs/Web/API/Element/attachShadow)
  for the open/closed shadow-root modes and the `.shadowRoot` handle that reads back
  `null` for closed roots.

**See also:** [waiting for content that renders after interaction](how-to-scrape-infinite-scroll-playwright.md),
[the checklist for when automation gets a different page than a human](playwright-detected-as-bot.md),
and [how to test whether the browser itself is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The `seed=42` in every
example is the same identity each run, which is how a component-loading failure gets
debugged instead of guessed at.*
