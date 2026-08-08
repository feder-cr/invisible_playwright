---
title: "How to scrape infinite scroll pages with Playwright"
description: "A Playwright infinite-scroll loop that waits for content growth, not a fixed sleep: when to stop, how to dedupe, and avoiding the pattern that reads as a bot."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 3
---


# How to scrape infinite scroll pages with Playwright

Most infinite-scroll code looks the same: scroll down, sleep a couple of seconds, scroll
again, repeat a fixed number of times. It works on the page you tested it on and breaks
on the next one, because a fixed sleep is a guess about a network and a render pipeline
you do not control.

This page is the loop that does not guess: how to wait for content to actually arrive,
how to know when the page is really done rather than just slow, how to dedupe what you
collect, and the part generic scroll tutorials skip entirely - a perfectly mechanical
scroll loop is itself something a page can notice, and the block that shows up minutes
into a session is usually that, not your fingerprint.

## Infinite scroll scraping at a glance

A reliable infinite-scroll scraper makes four decisions, and the fragile version of
each is the one most tutorials ship. Wait on a condition, stop on a streak, key dedup on
an identifier, and vary the motion:

| Decision | Fragile version | Reliable version |
|---|---|---|
| Know a batch loaded | `sleep(2)` between scrolls | `wait_for_function` on `scrollHeight` + `wait_for_load_state("networkidle")` |
| Know the feed ended | stop on the first flat round | stop on a streak of consecutive flat rounds |
| Collect without repeats | index or scroll position | a stable per-item identifier |
| Stay unremarkable | constant wheel delta at a fixed interval | seeded, varied delta and dwell |

Each row is a section below.

## Why "just scroll and wait" breaks

A `sleep(2)` between scrolls encodes an assumption: that two seconds is always enough for
the next batch of items to load, render, and settle. It usually is not always true in
either direction. Slow connections and heavy pages make two seconds too little, so you
scroll past content that has not arrived yet and read a shorter page than exists. Fast
pages make it too much, and a thousand-item feed now takes ten times longer to collect
than it needs to.

The fix is the same one behind most Playwright reliability advice: [wait for a condition,
not for a duration](how-to-wait-for-page-load-playwright.md). For infinite scroll the
condition is content growth, which you can read directly from the DOM.

## The scroll loop: wait for growth, not for time

The reliable pattern is one loop: scroll, then wait for `document.body.scrollHeight` to
actually increase before scrolling again, with a per-round timeout standing in for the
sleep. When the height stops growing for several rounds in a row, the feed is done.

Start from a normal launch. The `browser` object below is a real Playwright `Browser`,
so everything from here on is standard Playwright, not a project-specific API:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listings")
    page.wait_for_load_state("networkidle")
```

Now the loop. Each round scrolls, then waits for `document.body.scrollHeight` to have
actually increased, with a timeout instead of a sleep:

```python
def scroll_until_stable(page, max_stable_rounds=3, round_timeout_ms=10_000):
    stable_rounds = 0
    last_height = page.evaluate("document.body.scrollHeight")

    while stable_rounds < max_stable_rounds:
        page.mouse.wheel(0, 2400)

        try:
            page.wait_for_function(
                "prevHeight => document.body.scrollHeight > prevHeight",
                arg=last_height,
                timeout=round_timeout_ms,
            )
        except Exception:
            # height did not grow within the timeout - this may be the end
            stable_rounds += 1
            continue

        stable_rounds = 0
        last_height = page.evaluate("document.body.scrollHeight")
        page.wait_for_load_state("networkidle")
```

Two conditions are doing the work here, not one. `wait_for_function` catches the DOM
actually growing, and `wait_for_load_state("networkidle")` catches the requests that fill
it in settling down before the next scroll. A page that appends a placeholder and fills it
in later can pass the first check and still be mid-load, which is why the second one runs
too.

## Knowing when to stop

A scroll loop knows the feed ended when `max_stable_rounds` consecutive scrolls fail to
grow the page: once that streak is long enough, "still loading" is no longer the likely
explanation.

One round of no growth is not proof the feed ended. A slow response, a lazy-loaded image
block still resolving, or a scroll that landed just before a batch was pushed can all
produce a single flat round on a page with plenty left. Two or three consecutive flat
rounds is a much stronger signal, which is why the check is a streak and not a single
miss.

If the page also gives you something more direct - a "no more results" element, an item
count in the page, an API response that returns an empty array - prefer that over height
alone. Height is the general-purpose fallback, not the best signal when a better one
exists.

## Deduplicating items across scrolls

Scroll-and-collect naturally re-reads items you already have, because the DOM keeps
everything loaded so far rather than replacing it. Track what you have already recorded
by a stable identifier, not by position:

```python
seen_ids = set()
items = []

def harvest(page):
    cards = page.locator("[data-item-id]")
    count = cards.count()

    for i in range(count):
        card = cards.nth(i)
        item_id = card.get_attribute("data-item-id")
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        items.append({
            "id": item_id,
            "title": card.locator(".title").inner_text(),
        })
```

Call `harvest(page)` after each successful growth round rather than only once at the end.
Two reasons: it lets you inspect progress while the loop still runs, and it means a crash
or a block partway through still leaves you with everything collected up to that point
instead of nothing.

If the page has no stable identifier at all, the URL of a detail link or a normalized
title-plus-position hash is a reasonable fallback, but prefer whatever attribute the site
already uses to key the item - it is far less likely to collide.

## The tell a robotic scroll loop creates

Waiting for growth instead of a fixed sleep solves the reliability problem, but it does
nothing about a second problem that only shows up once a session runs for a while: the
loop is a perfect, identical motion repeated hundreds of times, and that pattern is
itself something a page can watch for.

A constant wheel delta, a scroll that never pauses, never overshoots, never slows down to
read anything, and fires at a rate no human scrolling with a mouse or trackpad produces -
that is a behavioral signature, and it is a different layer entirely from the fingerprint.
[The checklist for one-site detection](playwright-detected-as-bot.md) puts behavior at
step five for a reason: blocks that arrive **after an interaction**, minutes into a
session rather than at the first request, are usually this rather than anything about the
browser's reported GPU or fonts. A perfectly consistent fingerprint does not help if the
scrolling pattern on top of it is the part that stood out.

This is easy to miss because it produces no error. The loop above will run cleanly for a
while and then simply start getting shorter pages, emptier responses, or a challenge
instead of the next batch - which is the same shape of failure this project hit with a
WebRTC gate that asserted absence instead of presence: a suppressed or truncated result
looked fine to a check that was only watching for an exception.

## Humanizing the scroll

The fix is the same one this project applies to pointer movement: vary the motion instead
of repeating it, and derive the variation from a seed so a run that misbehaves can be
replayed rather than re-guessed.

```python
import random

sf = InvisiblePlaywright(seed=42)
with sf as browser:
    page = browser.new_page()
    page.goto("https://example.com/listings")

    rng = random.Random(sf.seed)

    def human_scroll_step(page):
        delta = rng.randint(500, 1800)
        page.mouse.wheel(0, delta)
        page.wait_for_timeout(rng.randint(200, 900))
```

That pause is not the thing telling you content arrived - `wait_for_function` still is,
exactly as before. It only changes how long the loop dwells between wheel events, so the
gaps look like a device scrolling rather than a timer firing at a fixed interval. Wire
`human_scroll_step` in as the scroll action inside `scroll_until_stable` and the growth
check around it does not change at all.

The same idea already ships for clicks and hovers: [humanized mouse
movement](human-mouse-movement.md) is on by default in this project, driven from the same
per-session seed, so the arc to a button and the pacing of a scroll come from one
consistent identity rather than two unrelated randomizers. Bezier curves get the pointer
path right; what actually gets read on a scroll loop is closer to timing than shape, since
a wheel event does not carry the same rich field set a pointer move does - but a constant
interval is exactly as loud as a teleporting cursor, and for the same reason.

## When scrolling destroys the execution context

Infinite scroll sometimes ships with client-side routing bolted on: a "load more" click
that also swaps the URL, or a feed that quietly replaces the whole document once you pass
a certain point instead of appending to it. Either one is a navigation, and a navigation
mid-evaluation produces the classic error:

```
Error: Execution context was destroyed, most likely because of a navigation
```

Most of the time this is your own code holding a handle across a scroll that triggered a
route change, and the fix is the ordinary one: re-query elements after the growth check
instead of carrying a `Locator` snapshot across it. Occasionally it is the site deciding,
mid-scroll, that this session should go somewhere else, and the two look identical in the
stack trace. [The full breakdown of that error](execution-context-destroyed.md) covers how
to tell them apart - the short version is to read `page.url` the moment the error fires:
the URL you expected means your code raced, a different one means the page moved you.

Given the previous section, there is a specific reason to check this on infinite scroll
in particular: if the context keeps dying at the same scroll position, after the same
number of rounds, every single run, that is not a race. Races are intermittent. A page
that reacts at a consistent point in a repeated action is behaving like a threshold, not
a bug.

## Conclusion

An infinite-scroll loop that works on every page you throw it at waits for the DOM to
actually grow and for the network to actually settle, stops on a streak of flat rounds
rather than one, and keys its dedup on an identifier rather than position. None of that
is specific to this project; it is just Playwright used the way its own waiting primitives
are meant to be used.

The part worth adding on top is the one generic tutorials leave out: the motion of the
loop is itself a signal, separate from anything about the browser's fingerprint, and a
uniform scroll at a uniform interval is exactly as noticeable as a teleporting cursor. Vary
it, derive the variation from a seed so a bad run is reproducible, and read a block that
arrives mid-session as a hint about behavior rather than a fingerprint bug to chase.

## Short answers to the questions that lead here

**How do I know when infinite scroll has finished loading?** Wait for
`document.body.scrollHeight` to grow after each scroll, with a timeout instead of a sleep,
and stop after a streak of rounds that did not grow, not after a single one.

**Why does my scroll loop miss items or duplicate them?** Because it is scrolling on a
fixed schedule instead of waiting for content, so it either reads before a batch arrives
or re-reads a batch it already has. Key what you collect by a stable identifier and only
harvest after a confirmed growth round.

**Should I just use `wait_for_timeout` between scrolls?** Only for pacing, never as the
signal that content arrived. Use it to vary how long the loop dwells; use
`wait_for_function` or `wait_for_load_state("networkidle")` to know whether the page
actually grew.

**Why do I get blocked partway through a long scroll session and not at the start?** A
block that shows up after a while, rather than on the first request, usually means
behavior rather than fingerprint. A scroll loop that never varies its pace or distance is
one of the more obvious things there is to watch for.

**What does "Execution context was destroyed" mean during a scroll?** Usually that your
code held a reference across a navigation the scroll itself triggered. Re-query after
each round. If it happens at the exact same point every run, it may be the page reacting
rather than a race.

**Does humanizing the scroll matter if the site does not watch behavior?** No. Check
whether the block arrives at the first load or only after scrolling for a while. The
first points at the fingerprint or the address; the second points here.

## Sources

- [Playwright's own waiting primitives](https://playwright.dev/python/docs/api/class-page)
  (`wait_for_function`, `wait_for_load_state`, `expect_navigation`) and its
  [auto-waiting / actionability](https://playwright.dev/python/docs/actionability) model,
  used here for content growth rather than a fixed sleep.
- This project's release gates, including the WebRTC gate whose absence-only assertion
  produced a false pass, cited here as the same shape of mistake a scroll loop can make by
  treating a truncated or suppressed page as a clean end of feed.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md)
for where behavior sits in the overall order, [how to scrape without getting
blocked](how-to-scrape-without-getting-blocked.md) for the layers above the scroll loop
itself, [how to scrape a "load more" button](how-to-scrape-load-more-button-playwright.md)
for the click-driven cousin of this same pattern, and [how to test whether your browser is
detected](how-to-test-bot-detection.md) for why a clean-looking run is not the same thing
as a passing one.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The scroll loop in this
project's own test suites started as a fixed-sleep version and got rewritten after it
silently under-collected on a slower connection.*
