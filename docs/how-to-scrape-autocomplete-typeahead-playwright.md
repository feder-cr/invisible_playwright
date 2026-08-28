---
title: "Scrape autocomplete and typeahead inputs with Playwright"
description: "Autocomplete and typeahead inputs ignore fill(). Type per character with Playwright to fire the debounced XHR, wait for the listbox, read the real value."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 68
---


# Scrape autocomplete and typeahead inputs with Playwright

An autocomplete or typeahead field looks like an ordinary text input and behaves
nothing like one. It does not read what the box contains. It reacts to the act of
typing: each keystroke restarts a short timer, the timer fires one request for the
current prefix, and a suggestion list is painted from the response while the field
still holds focus. The value you actually want to scrape is not what you typed. It is
the canonical suggestion the site puts in that list, the one its own search runs on.

This page is why a single `fill()` gets you an empty dropdown, how to drive the field
per character so the list renders, how to commit the right suggestion, and the one
behavioural caveat that decides whether a long sweep of lookups stays human-shaped.

## Why fill() writes the string and gets nothing back

`page.fill()` sets the input's value in one atomic operation. The field ends up
holding your whole string, which is exactly why it fails here. There was no first
keystroke, no second, no pause between them, so the debounce timer that fires the
suggestion request was never started and never elapsed. The XHR does not go out. The
listbox never mounts. You then wait for a `[role="option"]` that will not appear and
time out, or worse, you read the raw text back and ship whatever the user typed
instead of the canonical value the site would have matched.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/search")

    # Does NOT work: the value appears, the dropdown does not.
    page.fill("#q", "san fr")
    # page.wait_for_selector('[role="option"]')  # times out, nothing rendered
```

The same input reacts correctly to real per-character events, so the fix is to send
those events rather than to skip them.

## Type per character to trigger the debounce

Playwright types character by character when you ask it to. On a locator,
`press_sequentially()` sends a discrete key event per character with a delay between
them, which is what starts and restarts the debounce timer the way a person does. The
`delay` is a floor in milliseconds between keystrokes; pick a value larger than zero
so the events are actually spaced.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/search")

    field = page.locator("#q")
    field.click()                                   # focus first; the list needs focus
    field.press_sequentially("san fr", delay=120)   # one key event per character
```

A typical debounce window sits somewhere around 150 to 300 milliseconds, so a
per-character delay in the low hundreds both clears it and reads as ordinary human
typing. Do not overthink the exact figure. The point is that the events are separate
and spaced, not simultaneous.

If you are on an older Playwright, `field.type("san fr", delay=120)` does the same
thing; `press_sequentially` is the current name for it.

## Wait for the listbox, then read what it actually offers

Once the request returns, the site paints the suggestion list. Wait for a concrete
option to exist rather than for a fixed sleep, because the network time is variable
and a `wait_for_timeout` either races or wastes seconds every lookup.

```python
    # Wait for a real option, not a timer.
    page.wait_for_selector('[role="listbox"] [role="option"]', state="visible")

    options = page.locator('[role="listbox"] [role="option"]')
    count = options.count()
    for i in range(count):
        print(options.nth(i).inner_text())
```

Assert the list is present and non-empty, the same discipline that applies to any
signal you scrape: an empty result is a failure, not a clean pass, and it usually
means the debounce did not fire or the field lost focus mid-type. If the options come
back empty, re-check that you clicked the field before typing and that nothing else
stole focus between characters.

## Commit a suggestion to get the canonical value

Reading the visible text is often not enough. The label shown in the dropdown and the
value the site searches on can differ: the list may display "San Francisco, CA" while
the request the site fires on selection carries a place ID or a normalized string. To
capture what the site actually uses, commit a suggestion the way a user does, with the
arrow keys or a click, and let the field settle to the canonical value.

```python
    # Keyboard commit: move into the list and accept the highlighted row.
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")

    # or click a specific option by its text
    # page.locator('[role="option"]', has_text="San Francisco").click()

    committed = page.locator("#q").input_value()
    print("committed value:", committed)
```

The most reliable way to see the canonical value is to watch the request the
selection fires rather than trust the input box. If you already capture traffic, hook
the response for the search endpoint the commit triggers; the pattern is the same one
in [capture XHR and API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md),
pointed at the URL the suggestion request uses.

```python
    with page.expect_response("**/autocomplete**") as resp_info:
        page.locator("#q").press_sequentially("san fr", delay=120)
    payload = resp_info.value.json()   # the suggestions exactly as the site sees them
```

That JSON is usually the cleanest source of the canonical fields, ranked in the order
the site would rank them, without any DOM scraping at all.

## The stealth caveat: cadence is the signal, not the string

Per-character typing solves the mechanics, and it also happens to be what keeps a
lookup sweep from standing out. On a field like this the site is already handling
keystrokes one at a time, so the timing between them is right there to be measured. A
run that fills instantly and repeats that exact zero-delay burst across two hundred
lookups draws a shape no human hand produces: identical inter-key intervals, no
per-character variance, the same cadence on every field. That regularity is the tell,
not any single value. Blocks that arrive minutes into a session rather than at the
first request are frequently this.

Two things have to hold for the typing to read as real. The events must be genuine
input events the page trusts, and their timing must vary. On a stock automation build
the key events a page receives can carry the marks of a synthetic dispatch, which is a
separate problem from cadence; this project's engine delivers key and pointer events
that the page treats as [genuine trusted input](playwright-clicks-istrusted.md), so the
debounce and the listbox behave for automation exactly as they do for a person.

On top of that, `press_sequentially(delay=...)` gives you spacing rather than a
machine-gun burst, and the same seeded identity keeps every other surface consistent
across the whole sweep, so two hundred lookups look like one person doing two hundred
searches instead of two hundred identical robots. The same reasoning covers the pointer
path when you click an option instead of pressing Enter; see
[human mouse movement](human-mouse-movement.md) for why the cursor arc matters for the
same reason the typing rhythm does.

## Conclusion

A typeahead is an event-driven widget wearing a text input's clothes. Drive it with
the events it expects: click to focus, `press_sequentially` with a real delay to fire
the debounce, wait for a concrete option to render, then commit a suggestion or read
the response JSON to get the canonical value the site searches on. `fill()` skips the
one mechanism the whole widget is built around and comes back empty. Do the per-
character version and you get both the data and, because the cadence is right, a
session that keeps its shape across a long run of lookups.

Catalog search boxes behave the same way and hide a four-level tree behind them: see
[how to scrape course catalogs with Playwright](how-to-scrape-course-catalogs-playwright.md).

## Short answers to the questions that lead here

**Why does fill() leave the autocomplete dropdown empty?** Because `fill()` sets the
value in one operation with no keystrokes, so the debounce timer that fires the
suggestion request never starts. Type per character instead.

**How do I type character by character in Playwright?** Use
`locator.press_sequentially("text", delay=120)`, or `locator.type("text", delay=120)`
on older versions. The delay spaces the key events so the debounce fires.

**How do I wait for the suggestion list?** Wait for a real option to be visible, for
example `page.wait_for_selector('[role="listbox"] [role="option"]', state="visible")`,
not a fixed `wait_for_timeout`, because the network time varies.

**How do I get the value the site actually searches on?** Commit a suggestion with
ArrowDown then Enter, or click the option, then read `input_value()`. The cleanest
source is the response JSON captured with `page.expect_response`.

**Why does my lookup sweep get blocked after it works at first?** Often the typing
cadence. Instant, zero-variance input repeated across many fields is a behavioural
signal even when every fingerprint value is correct. Spacing the keystrokes and
varying them removes it.

**Do I need a real focus before typing?** Yes. The listbox only mounts while the field
holds focus, so click the field first and make sure nothing steals focus between
characters.

## Sources

- Playwright's official API reference for
  [`Locator.press_sequentially` / `Locator.type`](https://playwright.dev/python/docs/api/class-locator)
  and [`Page.expect_response`](https://playwright.dev/python/docs/api/class-page), read for
  the per-character `delay` semantics and the response-capture pattern.
- This project's behavioural notes on typing cadence and trusted input events, and the
  measured failure mode of a zero-delay burst repeated across a long lookup run.

**See also:** [capture XHR and API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md),
[why trusted input events matter for clicks and keys](playwright-clicks-istrusted.md),
[scrape a date picker or calendar, another event-driven widget](how-to-scrape-date-picker-calendar-playwright.md),
and [the checklist for being detected on one site](playwright-detected-as-bot.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-dropdown
mistake and the zero-delay-burst mistake are both ones I made before writing them down.*
