---
title: "Scrape load-more button pages with Playwright"
description: "Scrape load-more button pages with Playwright: re-query the button each round, wait for the item count to grow, stop when it disappears, and vary click timing."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 77
---


# Scrape load-more button pages with Playwright

**To scrape a load-more list with Playwright, hold the button as a
`page.locator()` instead of a captured element, click it in a loop, wait for the
item count to grow after each click, and stop when the button disappears or goes
disabled rather than after a fixed number of clicks.** That handles the two ways
this pattern breaks: the stale-element crash, and the fixed cadence that reads as
a bot.

A load-more list looks like infinite scroll and behaves nothing like it. Scroll
appends as you move; a load-more list appends only when an explicit button is
clicked, and that button is one of the least stable elements on the page. It
detaches and re-renders after each batch, it moves down as rows push it, it goes
disabled while a request is in flight, and it is sometimes swapped for a spinner
and then swapped back. Point a locator you captured a moment ago at it and you get
a stale-element error instead of the next page.

This page is the loop that survives that: click, wait for the item count to
actually grow, re-query the button every round, and stop when it disappears or
disables rather than after a fixed number of clicks. Then the stealth part, which
is that a click on a fixed cadence is a behavioral signature the same way a uniform
scroll is.

## Why load-more is not infinite scroll

The two get filed under the same heading and need opposite code.

[Infinite scroll](how-to-scrape-infinite-scroll-playwright.md) is driven by the
viewport: you move it, an observer fires, more rows arrive on their own. There is no
element you have to keep a handle on, only a scroll position and a count.

Load-more is driven by a click, and the thing you click is a live DOM node that the
page rewrites underneath you on every batch. So the whole problem moves from "when
do I stop scrolling" to "is the element I am about to click still the element I
found", and that question has to be re-answered every single round.

## The stale-element trap

Here is the version almost everyone writes first, and why it throws.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog", wait_until="domcontentloaded")

    # WRONG: captured once, reused every round.
    button = page.query_selector("button.load-more")
    for _ in range(50):
        button.click()   # throws the moment the button re-renders
```

`page.query_selector()` returns an ElementHandle, a reference to one specific node
that existed at that instant. The first click triggers a fetch, the list re-renders,
the old button node is detached, and the handle now points at nothing. The second
click raises an error about an element that is not attached to the document.

The fix is to stop holding a node and start holding a query. `page.locator()`
returns a Locator, which does not point at an element at all: it re-resolves the
selector every time you act on it. Re-querying each round is then not extra work,
it is the default behaviour of the right object.

```python
button = page.locator("button.load-more")   # a query, resolved fresh each use
button.click()   # resolves now
button.click()   # resolves again, against whatever the DOM currently holds
```

## The loop that actually works

Two conditions drive the loop, and neither is a click counter. You continue while
the button is present and enabled, and you advance only once the item count has
grown. A fixed `range(50)` either stops early on a long list or clicks into the void
on a short one, and a fixed sleep between clicks is a guess that is too short half
the time and wasteful the other half.

```python
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from invisible_playwright import InvisiblePlaywright

def scrape_load_more(url, item_selector, button_selector, max_rounds=500):
    with InvisiblePlaywright(seed=42) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")

        items = page.locator(item_selector)
        button = page.locator(button_selector)

        for _ in range(max_rounds):
            count_before = items.count()

            # Stop when the control is gone or has gone disabled. Short-circuit
            # on count() first so is_enabled() is never called on zero matches.
            if button.count() == 0 or not button.is_enabled():
                break

            button.click()

            # Advance on real growth, not on a timer. If the click produced no
            # new rows, the list is exhausted and this is also a valid stop.
            try:
                page.wait_for_function(
                    "(a) => document.querySelectorAll(a.sel).length > a.n",
                    arg={"sel": item_selector, "n": count_before},
                    timeout=15000,
                )
            except PlaywrightTimeout:
                break

        return items.all_inner_texts()
```

`max_rounds` is a safety ceiling against a page that keeps offering a button that
never adds anything, not the exit condition. The real exits are the button
vanishing, the button disabling, and the count refusing to grow after a click:

| Stop condition | How the loop detects it |
|---|---|
| Button removed from the DOM | `button.count() == 0` at the top of the round |
| Button present but disabled | `not button.is_enabled()` |
| Click added no new rows | `wait_for_function` times out waiting for the count to grow |
| Safety ceiling reached | `max_rounds` exhausted (a guard, not a real exit) |

The
spinner case is handled for free: while the request is in flight the button is
either detached or disabled, and both are caught by re-querying `count()` and
`is_enabled()` at the top of the next round instead of trusting a stale reference.

## Vary the click timing from the seed

A long load-more run is a long series of identical actions, and identical is the
tell. A click fired every 400ms exactly draws the same flat line in an interaction
log that a perfectly uniform scroll does, and a page that watches
[behaviour rather than fingerprints](playwright-detected-as-bot.md) reads that line
as clearly as it reads a missing GPU. The click itself is already a real, trusted
event here: the cursor arcs to the button on a
[Bezier curve](human-mouse-movement.md) and the browser dispatches the press at the
C++ level, so `isTrusted` is genuinely true rather than
[a synthetic event wearing the flag](playwright-clicks-istrusted.md). What is left
to humanize is the rhythm between clicks, and it should be varied without being
random noise you cannot reproduce.

Derive the pause from the same seed that drives the fingerprint. The run stays
reproducible, so a failure can be replayed exactly, while no two gaps are equal.

```python
import random
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from invisible_playwright import InvisiblePlaywright

def scrape_load_more(url, item_selector, button_selector, seed=42, max_rounds=500):
    rng = random.Random(seed)   # same seed as the browser -> same rhythm each run
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")

        items = page.locator(item_selector)
        button = page.locator(button_selector)

        for _ in range(max_rounds):
            count_before = items.count()
            if button.count() == 0 or not button.is_enabled():
                break

            # Read the batch, then pause an unequal, reproducible amount before
            # reaching for the button again.
            page.wait_for_timeout(rng.randint(600, 2200))
            button.click()

            try:
                page.wait_for_function(
                    "(a) => document.querySelectorAll(a.sel).length > a.n",
                    arg={"sel": item_selector, "n": count_before},
                    timeout=15000,
                )
            except PlaywrightTimeout:
                break

        return items.all_inner_texts()
```

The pause goes before the click, in the space where a person would be reading the
rows that just loaded before deciding to ask for more. Seeding `random.Random`
from the same value you pass to `InvisiblePlaywright` means the whole session,
identity and timing together, is one reproducible thing: pass `seed=42` and both
come back identical, change it and both change.

## Putting it together

The two pieces compose into one runnable job. Log the seed so any run can be
reproduced later.

```python
if __name__ == "__main__":
    rows = scrape_load_more(
        "https://example.com/catalog",
        item_selector=".product-card",
        button_selector="button.load-more",
        seed=42,
    )
    print(f"collected {len(rows)} rows")
    for row in rows:
        print(row)
```

The `browser` object is a real Playwright `Browser`, so everything else you already
do stays the same: `new_page()`, `goto()`, locators, `all_inner_texts()`. Nothing on
this page is a wrapper method you have to learn. The only additions are the loop
shape that re-queries instead of caching, and a pause drawn from the seed instead of
a constant.

## Conclusion

Load-more scraping fails in one specific way and succeeds in one specific way. It
fails when you hold onto an element the page is about to rewrite and click it on a
metronome. It succeeds when you hold a query instead of a node, advance on measured
growth instead of a timer, stop on the button's own state instead of a count you
picked in advance, and let the gaps between clicks vary from a seed so a long run
does not draw a straight line. The re-query is what keeps it from throwing; the
varied timing is what keeps it from standing out.

## Short answers to the questions that lead here

**Why does clicking the load-more button throw a stale-element error?** Because you
captured it as an ElementHandle, the list re-rendered, and the node you are holding
was detached. Use `page.locator()`, which re-resolves the selector on every action
instead of pointing at one dead node.

**How do I know when to stop clicking?** When the button disappears, when it goes
disabled, or when a click no longer grows the item count. Do not stop after a fixed
number of clicks, which is either too few or too many.

**Should I wait a fixed time after each click?** No. Wait for the item count to
actually increase with `wait_for_function`, and treat a timeout there as "list
exhausted". A fixed sleep is a guess that is wrong in both directions.

**How is this different from infinite scroll?** Infinite scroll appends as you move
the viewport and needs no element handle. Load-more appends only on an explicit
click against a button that re-renders each batch, so re-querying is mandatory.

**Can a repeated click get me detected?** A click on a perfectly uniform interval
is a behavioral signature, like a uniform scroll. Vary the pause between clicks, and
derive that variation from the seed so the run stays reproducible.

**Why seed the timing instead of using plain random?** So a failing run can be
replayed byte for byte. Seeding `random.Random` from the same value as the browser
identity makes the whole session, fingerprint and rhythm, reproducible.

## Sources

- Playwright's own [Locator versus ElementHandle semantics](https://playwright.dev/python/docs/api/class-elementhandle),
  read from the upstream API: a Locator re-resolves its selector on use, an
  ElementHandle is a reference to one node that can detach.
- This project's own behaviour notes on interaction cadence, where a fixed-interval
  action is recorded as a signature the same way a uniform scroll is, and its
  trusted-click path that dispatches the press at the engine level.

**See also:** [scraping infinite scroll](how-to-scrape-infinite-scroll-playwright.md)
for the viewport-driven sibling of this pattern, [scraping numbered pagination](how-to-scrape-paginated-pages-playwright.md)
for lists split across page-numbered URLs instead of a button, [why isTrusted matters for
clicks](playwright-clicks-istrusted.md) for what makes the click real, and
[the detection checklist](playwright-detected-as-bot.md) for where behavioural tells
sit relative to everything else.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The stale-element
loop and the metronome click are both mistakes that shipped before they were fixed.*
