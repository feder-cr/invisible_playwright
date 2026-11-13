---
title: "How to scrape infinite carousels with Playwright"
description: "Scrape infinite carousels with Playwright: mark the cloned wrap slides by their own attribute, pause autoplay through reduced motion, track the real slide index, and stop when an index repeats."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 119
---


# How to scrape infinite carousels with Playwright

**To scrape an infinite carousel with Playwright, treat it as a ring with duplicated ends
instead of a list: pause the autoplay before reading anything, mark the cloned wrap slides
by the marker the widget puts on them rather than by matching their text, read a real
slide index off each slide as you advance, wait for that index's own content before
extracting it, and stop when an index you have already recorded comes round again rather
than when a step adds nothing new.**

A looping carousel earns its unbroken wrap by lying about how many slides it has. The
widget copies the first slides and appends them after the last, copies the last slides and
puts them in front of the first, then snaps the track back with transitions off while a
copy is on screen. The eye sees one ring. The DOM holds extra nodes carrying duplicate
content.

That is the opposite failure from the vertical case. A
[virtual scrolling table](how-to-scrape-virtual-scrolling-tables-playwright.md) keeps too
few nodes and recycles them, so records vanish before you read them. A carousel keeps too
many, so a plain collector records the first slides twice and the loop never runs out of
slides to count.

## The clones are real nodes, and that is why the count lies

Count what is in the track, then compare it against what the widget says it holds. Those
two numbers disagree on every looping carousel, and the gap is how many copies sit on each
side.

```python
from invisible_playwright import InvisiblePlaywright

SURVEY = """
(root, sel) => {
    const slides = [...root.querySelectorAll(sel)];
    const cloneish = (el) => {
        const cls = (el.className || '').toString().toLowerCase();
        return cls.includes('clone') || cls.includes('duplicate')
            || el.hasAttribute('data-cloned');
    };
    const readIndex = (el) => {
        for (const name of ['data-slide-index', 'data-index', 'data-position']) {
            const raw = el.getAttribute(name);
            if (raw !== null && raw !== '') return parseInt(raw, 10);
        }
        const pos = el.getAttribute('aria-posinset');
        return pos === null ? null : parseInt(pos, 10) - 1;
    };
    return {
        nodes: slides.length,
        marked_clone: slides.filter(cloneish).length,
        aria_hidden: slides.filter(s => s.getAttribute('aria-hidden') === 'true').length,
        indexes: slides.map(readIndex),
        set_size: parseInt(slides[0]?.getAttribute('aria-setsize') || '-1', 10),
        dots: root.querySelectorAll('[role="tab"], [class*="pagination"] > *').length,
    };
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/collection", wait_until="domcontentloaded")

    root = page.locator("div.carousel")
    root.wait_for(state="visible")
    print(root.evaluate(SURVEY, ".slide"))
    # {'nodes': 16, 'marked_clone': 4, 'aria_hidden': 4, 'set_size': 12, 'dots': 12,
    #  'indexes': [10, 11, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0, 1]}
```

Sixteen nodes for twelve slides, and look where the index list starts. The first node in
the DOM is slide 10, because two copies of the tail sit in front of it. Every habit that
reaches for `slides.first` or `nth(0)` picks a copy of the second to last slide and calls
it the beginning.

Extend the attribute list before trusting it. Most widgets stamp their own prefixed name,
in the shape `data-<widget>-slide-index`, and the generic ones above are only what survives
across implementations. When every entry comes back `null`, the next section is the work.

## Detect the marker, do not dedupe on the text

Three markers appear in practice and only one is unambiguous. A class containing `clone`
or `duplicate` says what it means, and so does an index outside the real range: negative in
front, at or above the set size behind. Some widgets stamp the original index on the copy,
which is better still, because the copy names what it duplicates.

`aria-hidden="true"` is the one people reach for first and the one that misleads. Plenty
of carousels set it on genuine off-screen slides too, so it over-marks. Treat it as a
hint, never as the classifier.

Validate with arithmetic before the loop starts: the unmarked slides must equal
`aria-setsize`, the pagination dot count, or the total the feed declared. Under-marking
leaves duplicates in the output, which is loud. Over-marking drops real slides, and the
result looks clean.

Text is the tempting shortcut and it fails in both directions at once. Promotional slides
repeat a caption, so a text key merges records that were never duplicates, while a copy
whose lazy image has not promoted yet reads differently from its original and slips
through as new.

## Pause the autoplay, then check that it actually stopped

An auto-rotating carousel moves between your read and your click. You read index 4, the
timer fires, your click lands, and you are on 6. Slide 5 is never recorded and nothing
failed.

Racing that timer does not help, because the gap is milliseconds wide and the widget owns
the clock. Ask the component to stop instead. Carousels that respect
`prefers-reduced-motion` suspend their own rotation when it is set, and Playwright sets
that media feature at page creation, before the widget initialises. Setting it afterwards
through `emulate_media` helps only when the widget subscribed to the change event, and
many read it once at startup.

```python
import re

def pause_autoplay(page, root, slide_sel, quiet_ms=6000):
    # The ARIA pattern requires a pause control on an auto-rotating carousel.
    control = root.get_by_role("button", name=re.compile(r"paus|stop", re.I))
    if control.count() and control.is_visible():
        control.click()

    # Verify from inside the page that it stopped, instead of assuming it did.
    before = root.evaluate(ACTIVE_INDEX, slide_sel)
    page.wait_for_timeout(quiet_ms)
    if root.evaluate(ACTIVE_INDEX, slide_sel) != before:
        raise RuntimeError("carousel is still rotating after the pause attempt")

page = browser.new_page(reduced_motion="reduce")   # set before the widget loads
```

Keep the verification: a pause you did not confirm is a guess, and its cost is a gap in
the middle of a tidy dataset. Freezing the clock looks clever and breaks the run, because
it stops the CSS transition along with the timer, so the track never settles and every
read lands mid-flight.

## Track the real index, because the count is not progress

Two stopping rules get written here and both are wrong. "Stop when a step adds no new
slides" never fires, because a ring produces slides forever. "Stop when the index returns
to 0" fires only sometimes, because a carousel that moves a group at a time steps by two
or three and can jump straight over index 0 without landing on it.

The rule that survives both is a set. Record every real index you visit and stop the first
time the current index is already in it. Whatever the step size and whatever the starting
slide, the ring closes on the first revisit.

```python
ACTIVE_INDEX = """
(root, sel) => {
    const slides = [...root.querySelectorAll(sel)];
    const real = slides.filter(s => !s.matches('[class*="clone"], [class*="duplicate"]'));
    const box = root.getBoundingClientRect();
    const centre = box.left + box.width / 2;
    let best = null, bestGap = Infinity;
    for (const s of slides) {
        const r = s.getBoundingClientRect();
        if (r.width === 0) continue;
        const gap = Math.abs((r.left + r.width / 2) - centre);
        if (gap < bestGap) { bestGap = gap; best = s; }
    }
    if (!best) return null;
    for (const name of ['data-slide-index', 'data-index', 'data-position']) {
        const raw = best.getAttribute(name);
        if (raw !== null && raw !== '') return parseInt(raw, 10);
    }
    const pos = best.getAttribute('aria-posinset');
    if (pos !== null) return parseInt(pos, 10) - 1;
    return real.indexOf(best);   // -1 means: on a clone with no index to read
}
"""
```

Picking the active slide geometrically, as the one whose horizontal centre sits nearest
the track's centre, outlasts every active-class convention. `-1` is an answer and not an
error: the slide in front of you is a copy carrying no index, so advance without recording
rather than writing an entry you cannot key. Do not assume the starting index is zero
either, since carousels restore a position from session storage or open on whichever slide
a URL anchor asked for.

## Wait for the slide's own content, keyed to its index

Slides hydrate on approach. A slide read in the frame it arrives is half built: the image
is still a placeholder, the caption is empty, the price has not been written. Native lazy
loading makes it worse, because a slide translated off to the side never intersects the
viewport, so the browser leaves its images alone until the track brings it in.

The wait has to name the index, not a position. `nth(2)` after a transform is a different
slide than it was, and the copy of that slide sits elsewhere in the same track looking
identical.

```python
SLIDE_READY = """
(a) => {
    const root = document.querySelector(a.root);
    if (!root) return false;
    const slide = [...root.querySelectorAll(a.sel)].find(
        s => !s.matches('[class*="clone"], [class*="duplicate"]')
          && (s.getAttribute('data-slide-index') === String(a.index)
              || s.getAttribute('data-index') === String(a.index))
    );
    if (!slide) return false;
    const images = [...slide.querySelectorAll('img')];
    // complete alone is true for a finished placeholder; the width is the test.
    return images.every(i => i.complete && i.naturalWidth > 1)
        && slide.innerText.trim().length > 0;
}
"""

READ_SLIDE = """
(root, a) => {
    const slide = [...root.querySelectorAll(a.sel)].find(
        s => !s.matches('[class*="clone"], [class*="duplicate"]')
          && (s.getAttribute('data-slide-index') === String(a.index)
              || s.getAttribute('data-index') === String(a.index))
    );
    if (!slide) return null;
    const img = slide.querySelector('img');
    return {
        index: a.index,
        text: (slide.innerText || slide.textContent || '').trim(),
        href: slide.querySelector('a')?.href || null,
        image: img ? (img.currentSrc || img.src) : null,
    };
}
"""
```

`img.complete` on its own is the trap: it turns true the moment the 1x1 placeholder
finishes loading, which is immediately. `naturalWidth > 1` separates a real asset from a
spacer, and `currentSrc` gives the candidate the browser picked out of the `srcset` rather
than the fallback in `src`. The same problem without the ring around it is
[scraping lazy-loaded images](how-to-scrape-lazy-loaded-images-playwright.md).

One caveat on the text test. Carousels that hide inactive slides with `visibility: hidden`
return an empty `innerText` for content that is fully present, because `innerText` reports
rendered text. Use `textContent` on those, and prefer `innerText` everywhere else, since
`textContent` also returns the strings inside hidden helper markup.

## The control may need revealing before it can be clicked

Arrows are often invisible until the pointer enters the carousel, and how they are hidden
decides whether that matters. An arrow at `opacity: 0` still has a bounding box and no
`visibility: hidden`, so Playwright treats it as visible and clicks it without complaint.
An arrow at `visibility: hidden` or `display: none` fails that check, and the click waits
out its timeout on an element sitting right there in the DOM.

```python
def advance(page, root, next_sel):
    control = root.locator(next_sel)
    if control.count():
        if not control.is_visible():
            root.hover()                                  # reveal, then click
            control.wait_for(state="visible", timeout=3000)
        control.click()
        return

    root.press("ArrowRight")                              # keyboard-driven widget
    box = root.bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + box["width"] * 0.8, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.2, y, steps=12)
    page.mouse.up()                                       # steps matter: 0 reads as a click
```

Some widgets render no arrow at all until hover, so `count()` is zero on the first look
and one a moment later. Hover the container first and wait for the control to attach
rather than concluding there is none.

The drag is the last resort and it has a hard edge. Those moves produce pointer and mouse
events, and a carousel bound only to `touchstart` and `touchmove` ignores every one of
them. The `steps` argument is not cosmetic: a single jump from press to release carries no
intermediate movement, and the widget scores that as a click on whatever sits underneath.

## The whole pass

The pieces compose into one loop. The extra helper is a settle check, because a read taken
while the track is still transitioning finds the outgoing slide nearest the centre and
returns its index.

```python
import random
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from invisible_playwright import InvisiblePlaywright

def settled(root, slide_sel, previous, tries=25):
    """Return once the index has changed and then held still for two reads."""
    last, stable = None, 0
    for _ in range(tries):
        now = root.evaluate(ACTIVE_INDEX, slide_sel)
        if now == last and now != previous:
            stable += 1
            if stable >= 2:
                return now
        else:
            stable = 0
        last = now
        root.page.wait_for_timeout(120)
    return last

def scrape_carousel(url, root_sel, slide_sel, next_sel, seed=42, max_steps=200):
    rng = random.Random(seed)
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page(reduced_motion="reduce")
        page.goto(url, wait_until="domcontentloaded")

        root = page.locator(root_sel)
        root.wait_for(state="visible")
        survey = root.evaluate(SURVEY, slide_sel)
        pause_autoplay(page, root, slide_sel)

        expected = survey["set_size"] if survey["set_size"] > 0 else survey["dots"]
        args = {"root": root_sel, "sel": slide_sel}
        seen, collected = set(), []

        for _ in range(max_steps):
            index = root.evaluate(ACTIVE_INDEX, slide_sel)
            if index is not None and index >= 0:
                if index in seen:
                    break                                 # the ring closed
                seen.add(index)
                try:
                    page.wait_for_function(
                        SLIDE_READY, arg={**args, "index": index}, timeout=10000
                    )
                except PlaywrightTimeout:
                    pass                                  # record it, report the gap below
                row = root.evaluate(READ_SLIDE, {"sel": slide_sel, "index": index})
                if row:
                    collected.append(row)

            advance(page, root, next_sel)
            page.wait_for_timeout(rng.randint(280, 900))
            if settled(root, slide_sel, index) == index:
                break                                     # finite carousel, at its end

        return collected, expected, len(seen)
```

Comparing `len(seen)` against `expected` at the end is the honest report. A ring that
closes after nine of twelve slides means the step skipped three, and the fix is a smaller
step or a click on each pagination dot in turn. The pause before each advance comes from
the same seed as the browser identity, so the run replays exactly while no two gaps match,
which is the cadence argument that applies to
[any repeated action](how-to-scrape-load-more-button-playwright.md) in a long session.

## Where this stops, and what to read instead

Two different widgets get called an infinite carousel and only one is a ring. The ring
holds a fixed set of slides and wraps through clones, and everything above applies. The
other is an endless rail that fetches more slides as it reaches its tail, which is
horizontal infinite scroll with arrows on it. No first index ever comes back, so the stop
has to come from the feed's own paging, the way a
[vertical infinite scroll](how-to-scrape-infinite-scroll-playwright.md) takes it.

When either kind is fed by an endpoint, read the endpoint. A recommendation rail almost
always fetches a JSON list and renders slides from it, and that response carries the real
count, stable ids, full-size image URLs and the fields the caption truncates, with no
clones and no transitions to wait out. The hooks are in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

Three other cases end the DOM route. A carousel inside a closed shadow root is not
reachable by a locator at all, though open roots are pierced for you and are covered in
[scraping shadow DOM content](how-to-scrape-shadow-dom-playwright.md). A track painted
into a `<canvas>` has no per-slide markup at any position. And when the slides are
thumbnails whose full assets live behind a lightbox, the ring gets you the list and
[scraping image galleries](how-to-scrape-image-galleries-playwright.md) gets the pictures.

## Conclusion

A looping carousel is a ring with duplicated ends, and every mistake here comes from
reading it as a list. The clones are ordinary nodes holding real content, so counting
overcounts and the first node in the track is not the first slide. Find the marker the
widget already puts on those copies, check it against the declared set size, and do not
fall back to matching text, which merges different slides and misses identical ones in the
same pass. Stop the autoplay through the component's own supported path and confirm it
stopped. Then track the real index, wait for that index's content, and stop on the first
repeat. The advancing is easy. Knowing which of the sixteen slides in front of you are the
twelve that exist is the whole job.

## Short answers to the questions that lead here

**Why does my carousel scraper record the first slides twice?** Because a looping carousel
appends copies of the opening slides after the last one so the wrap looks continuous.
Those copies are real nodes with the same content. Detect them by a clone class or an
index outside the real range, and skip them.

**How do I know when the carousel has come full circle?** Keep a set of the real slide
indexes you have visited and stop the first time the current index is already in it.
Returning to index 0 is not the test, because a carousel that moves a group at a time can
step straight over it.

**Can I dedupe the slides by their text?** No, and it fails twice. Different promotional
slides often share a caption, so a text key merges records that were never duplicates, and
a clone whose lazy image has not loaded reads differently from its original, so the same
key misses the duplicate it was meant to catch.

**How do I stop the carousel advancing on its own?** Create the page with reduced motion
so the widget suspends its own rotation, click its pause control if it has one, then
verify by reading the index twice with a wait between. Do not freeze the clock: that stops
the transition too, so the track never settles.

**The next arrow is in the DOM but the click times out. Why?** It is hidden with
`visibility: hidden` or `display: none` until hover, and both fail Playwright's visibility
check. Hover the container first, then wait for the control. An arrow hidden with
`opacity: 0` is a different case and clicks fine.

**Why is the slide I just extracted half empty?** Carousels hydrate slides on approach, so
a read in the arrival frame gets the placeholder image and an empty caption. Wait on that
specific index and require `naturalWidth > 1`, since `complete` turns true as soon as the
1x1 placeholder loads.

## Sources

- Playwright's [`emulate_media`](https://playwright.dev/python/docs/api/class-page#page-emulate-media)
  and the `reduced_motion` option on context and page creation, which is what asks the
  component to suspend its own rotation, retrieved 2026-08-28.
- Playwright's [actionability rules](https://playwright.dev/python/docs/actionability),
  which define a visible element as one with a non-empty bounding box and without
  `visibility: hidden`, so an element at `opacity: 0` is still actionable, retrieved
  2026-08-28.
- Playwright's [`wait_for_function`](https://playwright.dev/python/docs/api/class-page#page-wait-for-function),
  [`hover`](https://playwright.dev/python/docs/api/class-locator#locator-hover) and
  [`mouse.move`](https://playwright.dev/python/docs/api/class-mouse#mouse-move) with its
  `steps` argument, retrieved 2026-08-28.
- The WAI-ARIA carousel pattern this article reads rather than infers: `aria-setsize` and
  `aria-posinset` on the slides, `aria-hidden` on the ones not presented, and the rotation
  control an auto-rotating carousel is expected to expose.

**See also:** [scraping virtual scrolling tables](how-to-scrape-virtual-scrolling-tables-playwright.md)
for the vertical sibling that recycles nodes instead of cloning them,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for the
feed that carries the slide list without the ring,
[scraping lazy-loaded images](how-to-scrape-lazy-loaded-images-playwright.md) for the
placeholder problem on its own, and
[scraping infinite scroll](how-to-scrape-infinite-scroll-playwright.md) for the endless
rail that fetches rather than wraps.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The clone read is the one
that cost a run here: the first three slides landed in the output twice, a text dedupe went
in to clean that up, and it quietly merged two different slides sharing a caption, so the
file came back shorter and looked correct.*
