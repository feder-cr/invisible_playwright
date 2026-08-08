---
title: "How to scrape reviews and ratings with Playwright"
description: "Scrape star ratings hidden in aria-label or CSS fill-width, expand every per-review Read more, and page the load more XHR to its natural end with Playwright."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 39
---


# How to scrape reviews and ratings with Playwright

To scrape reviews and ratings with Playwright, read the star rating from its
`aria-label` or CSS fill-width instead of its text, click each per-review "Read more"
before you extract the body, and page the "load more" XHR until the DOM count stops
rising. Those are the three fields a review page hides, and each one has a specific fix.

Review pages are built to be read by a person and to resist being read by a program,
and they do it with the two fields you actually came for. The star rating is almost
never text. The review body you can see is almost never the whole review. And the
count of reviews in the DOM is almost never the count on the page.

This is a working order for getting all three right: read the rating from where it
really lives, expand each review before you extract it, and page the "load more"
button until there is genuinely nothing left. The examples use stock Playwright driven
through this project, so the browser looks like a returning visitor rather than a fresh
automated session on every request.

## Why the two fields you want are not in the text

Open a review block and read what it is made of, not what it looks like.

The **star rating** is drawn, not written. The common encodings are two. Either a
filled overlay whose CSS width is a percentage of five stars, so a 4.2 rating is an
element styled `width: 84%` sitting on top of five empty stars, and `innerText` on that
element is the empty string. Or an accessibility label on the container, an
[`aria-label`](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-label)
reading something like `Rated 4.2 out of 5`, which screen readers announce
and which never appears as visible text. Scraping the visible text of a star widget
gets you nothing in the first case and the label of the widget in neither.

The **review body** is truncated. Most sites render the first sentence or two and hide
the rest behind a per-review "Read more" control that expands the text in place, with no
navigation, no URL change and often no network request. If you read `innerText` before
clicking it, you capture the teaser, not the review, and every row comes back the same
suspicious length.

The **review count** lags. Reviews load in pages behind a "load more" button that fetches
the next batch over XHR and appends it to the list. The number of review nodes in the
DOM is only ever the number you have scrolled into existence, never the total the page
claims at the top.

Three different problems, three different fixes, in the three sections below.

| The field | Where it really lives | The fix |
|---|---|---|
| Star rating | An `aria-label` or a CSS fill-width, never `innerText` | Parse the label, or compute the fill width as a fraction of the scale |
| Review body | Truncated behind a per-review "Read more" that expands in place | Click every expander and wait for the block's text to grow |
| Review count | Only what you have paged into the DOM via "load more" XHR | Page until the DOM count stops rising, not the total shown at the top |

## Read the star rating from aria-label or computed style

Do not read the rating as text. Read it from the label or from the computed width, and
parse the number out.

```python
import re
from invisible_playwright import InvisiblePlaywright

def parse_rating(page, review):
    # 1. Preferred: the accessibility label on the stars container.
    stars = review.query_selector("[aria-label*='out of'], [role='img'][aria-label]")
    if stars:
        label = stars.get_attribute("aria-label") or ""
        m = re.search(r"([0-5](?:\.\d)?)\s*out of\s*([0-5])", label)
        if m:
            return float(m.group(1)), float(m.group(2))

    # 2. Fallback: the fill overlay, whose width is a percentage of the scale.
    fill = review.query_selector(".stars-fill, [class*='rating-fill']")
    if fill:
        width = fill.evaluate("el => getComputedStyle(el).width")            # "84.2px"
        track = fill.evaluate("el => getComputedStyle(el.parentElement).width")
        pct = float(width.rstrip("px")) / float(track.rstrip("px"))
        return round(pct * 5, 1), 5.0

    return None, None

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/item/123/reviews", wait_until="networkidle")
    for review in page.query_selector_all("[data-review], article.review"):
        value, scale = parse_rating(page, review)
        print(value, "of", scale)
```

The label form is more reliable when it is present, because it carries the number the
site itself computed. The computed-width form is the fallback when there is no label,
and it depends on
[`getComputedStyle`](https://developer.mozilla.org/en-US/docs/Web/API/Window/getComputedStyle)
returning the resolved width of both the fill and its track, since the percentage
is meaningless without the scale it is a percentage of. Rounding to one decimal matches
how these values are usually authored; do not report `4.199999`.

The selectors above are illustrative. Inspect the real block once, in the same browser
you scrape with, and confirm which of the two encodings the site uses before you write
the parser against a guess.

## Expand every 'Read more' before you extract

The teaser is not the review. Click each expander, wait for the text to grow, then read.

```python
def expand_all(page):
    # Clicking mutates the list, so re-query each pass instead of holding
    # a stale handle. Stop when a full pass finds nothing left to expand.
    while True:
        buttons = page.query_selector_all(
            "button:has-text('Read more'), [data-expand]:not([aria-expanded='true'])"
        )
        clicked = 0
        for btn in buttons:
            if btn.is_visible():
                before = btn.evaluate("b => b.closest('[data-review]').innerText.length")
                btn.click()
                # The body expands in place: wait for its length to actually grow.
                page.wait_for_function(
                    "([b, n]) => b.closest('[data-review]').innerText.length > n",
                    arg=[btn, before],
                    timeout=3000,
                )
                clicked += 1
        if clicked == 0:
            break

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/item/123/reviews", wait_until="networkidle")
    expand_all(page)
    bodies = page.eval_on_selector_all(
        "[data-review] .review-body",
        "els => els.map(e => e.innerText.trim())",
    )
    print(len(bodies), "full review bodies")
```

Two details make this robust. First, the expansion happens in place, so the signal that
a click worked is the text getting longer, not a navigation or a network response;
`wait_for_function` on the block's own text length is the honest wait. Waiting a fixed
number of milliseconds instead will sometimes read the block mid-expansion and capture a
half-grown body. Second, clicking mutates the DOM, so re-query the buttons each pass
rather than iterating a list captured once, and stop only when a whole pass expands
nothing.

## Page the 'load more' XHR to a natural end

The list is partial until you have exhausted the button. Click it, wait for the batch to
land, and repeat until it is gone or the count stops rising.

```python
def load_all_reviews(page, max_batches=200):
    seen = 0
    for _ in range(max_batches):
        count = page.eval_on_selector_all("[data-review]", "els => els.length")
        more = page.query_selector("button:has-text('load more'), [data-load-more]")
        if not more or not more.is_visible():
            break
        # The click triggers an XHR that appends the next batch. Wait for the
        # response, then confirm the DOM count actually grew before continuing.
        with page.expect_response(lambda r: "review" in r.url and r.status == 200):
            more.click()
        page.wait_for_function(
            "n => document.querySelectorAll('[data-review]').length > n",
            arg=count,
            timeout=10000,
        )
        new_count = page.eval_on_selector_all("[data-review]", "els => els.length")
        if new_count == seen:            # count stalled: end of the list
            break
        seen = new_count
    return seen

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/item/123/reviews", wait_until="networkidle")
    total = load_all_reviews(page)
    print("loaded", total, "reviews before extraction")
```

The DOM count is the source of truth for "am I done", not the button and not the total
printed at the top of the page, which is frequently rounded or cached. Wait on the
count rising after each click, and treat a click that does not raise it as the end,
because the button sometimes lingers in the DOM after the last batch. If the site
exposes the batches as a clean JSON endpoint, reading those responses directly is often
simpler than paging the UI at all; see
[how to capture XHR API responses](how-to-capture-xhr-api-responses-playwright.md) for
attaching to the response the button fires, and
[how to scrape infinite scroll](how-to-scrape-infinite-scroll-playwright.md) for the
scroll-driven variant of the same append-and-wait loop.

## One session, one identity, and the rate signal no fingerprint hides

Ratings are only interesting in bulk, and bulk is the access pattern review sites watch
for. Harvesting one rating means visiting one item page; harvesting a catalogue means
visiting thousands, fast, from one place. That is precisely the shape a per-identity
throttle is built to catch.

A seed-stable fingerprint is the right first half of the answer. Because every surface
this browser exposes is derived from one seed, every request in a run carries the same
GPU, the same fonts, the same audio device and the same canvas hash, so the session
reads as one returning visitor rather than a rotating swarm of strangers. Reuse the seed
and you are the same person across the whole crawl; that consistency is what a
per-identity limiter measures you against, and here it measures a coherent visitor.

Now the honest half. Matching a real browser removes the fingerprint tell, and it
removes nothing else. Firing a thousand item pages a minute from a single address is a
velocity signal, and no fingerprint hides velocity: one consistent identity making
requests faster than any human hand could is a cleaner flag than an inconsistent one
making them slowly. We tripped this on our own gates once and the flag belonged to the
test harness hammering one endpoint, not to the browser. So this technique pairs with
pacing rather than replacing it. Space the item pages out, cap concurrency, and let the
crawl look like reading rather than draining; [how to rate limit your
scraper](how-to-rate-limit-your-scraper-playwright.md) covers the delay and concurrency
side, and [Configuration](configuration.md) covers giving a long crawl a matching exit
and timezone so the identity stays coherent end to end.

## Conclusion

Reviews and ratings resist scraping in three specific, predictable ways, and each has a
specific fix. The rating lives in an `aria-label` or a computed fill-width, so parse it
from there rather than from text. The body is truncated behind a per-review expander, so
click every one and wait for the text to grow before you extract. The list is paged
behind an XHR button, so click to a natural end measured by the DOM count rather than
trusting the total on the page. Do those three and you have the real data. Do them from
one seed-stable identity, paced like a reader, and you keep getting it.

## Short answers to the questions that lead here

**Why is the star rating empty when I read its text?** Because it is drawn, not written.
The value is in an `aria-label` on the container or in the CSS width of a fill overlay,
and `innerText` on a star widget is usually blank. Read the label or compute the width.

**How do I get the full review, not the preview?** Click the per-review "Read more"
first. It expands the body in place with no navigation, so wait for the block's text
length to grow, then extract.

**Why does my review count not match the number on the page?** Because reviews load in
XHR batches and the DOM only holds what you have paged into it. Click "load more" until
the count stops rising; the number at the top is often rounded or cached.

**Should I scrape the reviews UI or the JSON behind it?** If the "load more" button
fires a clean JSON endpoint, read that response directly; it is simpler and lighter than
paging the DOM. Attach to the response the button triggers.

**Will a good fingerprint stop me getting throttled?** No. It removes the fingerprint
tell, but a thousand pages a minute from one IP is a velocity signal no fingerprint
hides. Keep one identity and pace the requests.

**How do I make a failing crawl reproducible?** Pass an explicit `seed`. The same seed
gives the same browser every run, so a run that got blocked can be replayed exactly
instead of hoping the next random identity reproduces it.

## Sources

- Stock Playwright's `aria-label` querying, `expect_response`, `wait_for_function` and
  `eval_on_selector_all`, from the upstream
  [Playwright for Python API](https://playwright.dev/python/docs/api/class-page).
- The `aria-label` accessible-name attribute, per
  [MDN's aria-label reference](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-label),
  and the resolved-style read behind the fill-width fallback, per
  [MDN's getComputedStyle reference](https://developer.mozilla.org/en-US/docs/Web/API/Window/getComputedStyle).
- This project's own release gates, including the velocity flag that turned out to be the
  test harness hammering one endpoint rather than a browser problem.

**See also:** [how to capture XHR API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the "load more" batches directly, [how to scrape infinite
scroll](how-to-scrape-infinite-scroll-playwright.md) for the scroll variant of the same
loop, and the [Quickstart](quickstart.md) for the two-line switch from plain Playwright.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The rating parser and the
expand loop are both mistakes I shipped the naive version of first.*
