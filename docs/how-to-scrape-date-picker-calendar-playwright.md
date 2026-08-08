---
title: "Scrape date-picker calendars with Playwright"
description: "Scrape a date-picker calendar with Playwright: page between months, skip disabled cells, click to commit a date, and sweep a whole range under one identity."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 69
---


# Scrape date-picker calendars with Playwright

To scrape a date-picker calendar with Playwright, drive it the way a person does: click
the field to open the widget, page to the target month by reading the header, filter to
genuinely available day cells, then click the cell to commit the date. Typing into the
input rarely sticks. Sweeping a whole date range adds a second requirement that has
nothing to do with the DOM: humanized timing between clicks and one fixed identity held
across the entire walk.

A date-picker looks like a form field and behaves like an application. The value you
want is not in the DOM waiting to be read: it is behind a grid that shows one month at
a time, hides the target date until you page forward to its month, greys out the days
you are not allowed to pick, and commits the choice only when you click a rendered day
cell rather than typing into the box.

This page is the mechanics of driving one of those with stock Playwright: how to page
between months without losing the grid, how to tell an available cell from a disabled
one, how to actually commit the date, and how to sweep a range of dates in a single
session without the sweep itself becoming the thing that gets you blocked.

## Why typing into the field does not work

The instinct is to find the `<input>` and `fill()` it with `2026-09-14`. It almost
never sticks.

Most pickers render the visible box read-only and drive the real state from the
calendar widget. Some accept typed text but re-parse it against their own format and
silently discard anything that does not match, so an ISO string goes in and a blank
comes back out. Others fire the change event that the rest of the page listens for
only in response to a click on a day cell, so a typed value updates the input but never
updates the price, the availability, or whatever downstream field you were actually
after.

The reliable path is the one a human takes: open the picker, page to the right month,
click the right day. It is more clicks, and the extra clicks are exactly why identity
and timing matter later on this page.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/booking")

    # open the widget by clicking the field, not by filling it
    page.click("#checkin")
    page.wait_for_selector(".calendar-grid")
```

The `browser` here is a real Playwright `Browser`, so every method below is the stock
API you already know. The only thing the wrapper changes is what the browser looks like
to the page, which is what the last two sections are about.

## Navigate to the target month

The calendar shows one month. The date you want is some number of months ahead of what
is currently displayed, so the first job is to read the visible month, compare it to the
target, and click the forward control the right number of times.

Read the header rather than counting clicks blindly, because the picker may open on the
current month or on the last month the user touched, and you cannot assume a starting
point.

```python
import calendar

def visible_month(page):
    label = page.inner_text(".calendar-header .month-label").strip()
    # e.g. "September 2026" -> (2026, 9)
    name, year = label.rsplit(" ", 1)
    month = list(calendar.month_name).index(name)
    return int(year), month

def step_to_month(page, target_year, target_month):
    for _ in range(24):  # hard cap so a broken header cannot loop forever
        year, month = visible_month(page)
        if (year, month) == (target_year, target_month):
            return
        if (year, month) < (target_year, target_month):
            page.click(".calendar-header .next")
        else:
            page.click(".calendar-header .prev")
        page.wait_for_function(
            "(t) => document.querySelector('.calendar-header .month-label')"
            ".innerText.trim() !== t",
            arg=page.inner_text(".calendar-header .month-label").strip(),
        )
```

The `wait_for_function` after each click is not decoration. The grid re-renders on every
month change, and if you fire the next click before the header has changed you will
either double-step or click a control that has just been replaced. Waiting for the label
text to differ from what it was is a cheap, reliable signal that the new month has
painted.

## Respect min, max, and disabled cells

A day cell being present does not mean it is pickable. Booking and pricing calendars
disable the past, dates before a minimum stay, dates after a maximum window, and
individual days that are sold out or blacked out. Clicking a disabled cell does nothing,
but your code will happily "click" it and then read a stale value, which is worse than
an error because it looks like it worked.

Filter to genuinely available cells before you pick. The markers vary by site, so check
several: the [`aria-disabled`](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-disabled)
attribute, a `disabled` class, [`pointer-events: none`](https://developer.mozilla.org/en-US/docs/Web/CSS/pointer-events)
in the computed style, and the absence of the click handler the enabled cells carry.

```python
def available_day(page, day):
    cell = page.query_selector(f".calendar-grid [data-day='{day}']")
    if cell is None:
        return None
    if cell.get_attribute("aria-disabled") == "true":
        return None
    cls = cell.get_attribute("class") or ""
    if "disabled" in cls or "unavailable" in cls:
        return None
    # a cell that is present but not clickable often has pointer-events off
    pe = cell.evaluate("el => getComputedStyle(el).pointerEvents")
    if pe == "none":
        return None
    return cell
```

If `available_day` returns `None`, that is data, not a failure: for an availability
scrape, an unpickable day IS the answer for that date. Record it and move to the next
one rather than retrying.

## Click to commit, and re-query after every render

Two failure modes converge here, and both come from the grid re-rendering underneath
you.

The first is the [stale handle](https://playwright.dev/python/docs/api/class-elementhandle).
If you grabbed a cell handle, then paged to another month and back, that handle points at
a DOM node the picker has thrown away. Playwright raises on it, or worse, it resolves
against a detached node and the click lands nowhere.
Re-query the cell immediately before you click it, every time, and never carry a cell
handle across a month change.

The second is that some pickers rebuild the grid on hover or on partial selection
(the "select the start of a range and the end days re-render" pattern). If your click
target was computed before that rebuild, it is gone. The defense is the same: locate,
then immediately act, with nothing in between.

```python
def pick_date(page, year, month, day):
    step_to_month(page, year, month)
    cell = available_day(page, day)
    if cell is None:
        return False  # date not selectable; that is a valid result
    cell.scroll_into_view_if_needed()
    cell.click()      # commits the value the downstream fields listen for
    # confirm the commit landed, do not assume it
    page.wait_for_function(
        "(d) => document.querySelector('#checkin')?.value?.includes(d)",
        arg=str(day),
    )
    return True
```

That final `wait_for_function` is the same discipline the rest of these docs push:
assert that the value is present, do not assume the click worked. A committed date that
never actually committed is the calendar equivalent of a green test on a dead feature.
If you drive the click through raw coordinates instead of the element, note that the
event has to look real to the page, which is [why a synthetic click without the trusted
flag gets ignored](playwright-clicks-istrusted.md).

## Sweep a date range under one identity

A calendar sweep stays off the radar only when two things hold at once: humanized
timing between clicks, and one fixed identity held across the entire walk. Here is
where scraping a calendar stops being a DOM problem and becomes a stealth problem.

Price-by-date and availability scraping is never one date. You want the next sixty days,
or a check-in cell against every check-out cell, or the same room across three months.
That means dozens to hundreds of month-forward clicks and cell clicks in a single
session, all against the same endpoint, all from the same browser. A naive loop does
this at machine speed and machine regularity: click, read, click, read, every 180
milliseconds, forever. Nothing about a single click is suspicious. The rhythm of two
hundred of them is.

Two things keep a long calendar walk from reading as a script clicking a button on a
fixed interval.

The first is humanized timing between actions. Real people do not page months at a
constant cadence; they pause, they overshoot, they slow down when they read. Vary the
gaps, and let the pointer travel to each control rather than teleporting to it. This
wrapper moves the cursor along a Bezier arc to every click target by default, which is
the visible half of the problem; the invisible half is [the shape of the pauses between
actions](human-mouse-movement.md), which you control by not clicking on a metronome.

```python
import random, time

def sweep(page, dates):
    results = {}
    for (year, month, day) in dates:
        ok = pick_date(page, year, month, day)
        results[(year, month, day)] = read_price(page) if ok else None
        # break the metronome: humans do not act on a fixed interval
        time.sleep(random.uniform(0.4, 1.9))
    return results
```

The second, and the one people miss, is that the whole sweep has to happen under one
coherent identity. A calendar walk is a long session by definition, and a long session
is exactly where a fingerprint that drifts, or a fingerprint that contradicts the exit
IP, gets caught. Every field the browser reports (GPU, canvas, audio, fonts, screen,
timezone) has to stay fixed and mutually consistent for the entire walk, and the
timezone in particular has to agree with the proxy the whole time. Because every surface
here is derived from the one seed you passed to `InvisiblePlaywright(seed=42)`, the
identity is stable across all two hundred clicks for free: same machine, same browser,
start to finish. You did not assemble it and you cannot accidentally desync it
mid-sweep.

That combination, humanized cadence plus one immovable identity across a long walk, is
what separates "read a calendar" from "read a calendar without the session looking like
a script." The DOM part is the same for everyone. The session part is the product.

## Conclusion

Scraping a date-picker is four mechanical steps and one behavioral one. Open the widget
instead of typing into it. Page to the target month by reading the header and waiting
for the re-render, not by counting clicks. Filter to genuinely available cells and treat
an unavailable day as data. Re-query and click the cell to commit, then assert the value
landed. And when you scale that to a real date sweep, keep the cadence human and the
identity fixed, because a long calendar walk is a stress test of exactly the two things
a script does worst.

The mechanics are stock Playwright. The one thing you cannot bolt on afterwards is a
session that holds one honest identity across hundreds of clicks, and that is the part
worth getting from the engine rather than reinventing per scrape.

## Short answers to the questions that lead here

**Why can't I just fill the date input?** Because most pickers keep the input read-only
or re-parse typed text against their own format, and they commit the real value only in
response to a click on a day cell. Typing updates the box and not the price.

**How do I move to a future month?** Read the visible month from the header, compare it
to your target, and click the forward control once per month, waiting for the header text
to change after each click before you click again.

**Why does my cell handle go stale?** Because the grid re-renders on every month change,
and often on hover or partial selection, throwing away the node your handle pointed at.
Re-query the cell immediately before you click it, every time.

**How do I know a day is actually selectable?** Check more than one marker: `aria-disabled`,
a disabled or unavailable class, and `pointer-events: none` in the computed style. A cell
can be visible and still not clickable.

**Won't scraping many dates fast get me blocked?** The individual clicks are fine; the
constant rhythm of hundreds of them is the tell. Vary the gaps between actions and let
the pointer travel to each target rather than jumping.

**Does the identity matter for a single-page calendar?** It matters more, not less,
because a date sweep is a long session against one endpoint. A fingerprint that drifts
or contradicts the exit IP mid-walk is what a long session is good at exposing.

## Sources

- The wrapper's real API as documented in [Quickstart](quickstart.md) and
  [Configuration](configuration.md): the seed-derived identity and the default Bezier
  cursor motion referenced above.
- This project's own testing method, in particular the rule that you assert a value is
  present rather than assuming an action worked, from
  [how to test whether your browser is detected](how-to-test-bot-detection.md).
- The browser-level behavior behind the disabled-cell and stale-handle checks above:
  MDN on [`aria-disabled`](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-disabled)
  and [`pointer-events`](https://developer.mozilla.org/en-US/docs/Web/CSS/pointer-events),
  and Playwright's own docs on [`ElementHandle`](https://playwright.dev/python/docs/api/class-elementhandle)
  staleness.

**See also:** [why a synthetic click without the trusted flag is ignored](playwright-clicks-istrusted.md),
[the shape of human pointer motion and pauses](human-mouse-movement.md), and
[when "Execution context was destroyed" is the grid re-rendering rather than a bug](execution-context-destroyed.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The calendar mechanics are
the same for every tool; the one-identity-across-a-long-sweep part is why this one exists.*
