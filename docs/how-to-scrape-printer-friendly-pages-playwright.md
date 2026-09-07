---
title: "How to scrape printer-friendly pages with Playwright"
description: "Scrape printer-friendly pages with Playwright: find the print URL, emulate print media on the same DOM, diff one selector under both media types, and read the print stylesheet as the site's own map of its content."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 120
---


# How to scrape printer-friendly pages with Playwright

To scrape printer-friendly pages with Playwright, look for a separate print URL first, fall
back to `page.emulate_media(media="print")` on the same document when there is no second
URL, and choose between the two views with a diff that reads the same selector under both
media types instead of assuming the print view is the richer one.

A print view is the same data with the furniture taken out. No navigation, no carousels, no
sticky header, no lazy-loaded image placeholders, and very often no pagination. It is
frequently the cleanest extraction target on the whole site, and almost nobody goes looking
for it, because it is never in the main navigation and it never appears in a screenshot of
the rendered page.

It also has a failure mode, and it runs in the opposite direction to the one people expect.
A print stylesheet is mostly a set of instructions for hiding things. Emulate print and the
reviews, the comments and the related items you came for can disappear in the same pass that
unfolds the accordions. This page is the check that tells you which of the two happened,
before you build a scraper on top of a guess.

## Three different things get called a print view

Three separate mechanisms end up filed under the same name, and they are three different
extraction problems: a second document at its own URL, a print stylesheet applied to the DOM
you already have, and a generated PDF.

| What it is | How you reach it | What you get |
|---|---|---|
| A second URL | `?print=1`, `/print`, a `rel="alternate"` link | A different, simpler document, often unpaginated |
| A print stylesheet | `page.emulate_media(media="print")` | The same DOM, rendered under different rules |
| A generated PDF | a download, or an endpoint returning `application/pdf` | Bytes for a PDF parser, no DOM at all |

Work out which one you are holding before writing anything, because the first is a request,
the second is a style change, and the third is a different toolchain. The PDF case is covered
in [generating a PDF with Playwright and Firefox](how-to-generate-pdf-with-playwright-firefox.md),
which also explains why `page.pdf()` is not available on this engine.

## Find the print URL before you write a selector

The print affordance is usually in the document, just not anywhere a person looks. It hides
in three places: a `<link>` element carrying `media="print"`, an anchor whose href or label
contains some form of the word print, and a button whose handler calls `window.print()`.

That last one is the useful negative result. A control calling `window.print()` is not a
second URL, it is the print stylesheet route, so finding it tells you to stop hunting for an
endpoint that does not exist.

```python
PRINT_HINTS = ("print", "printable", "printer-friendly", "print-view")

def find_print_candidates(page):
    return page.evaluate(
        """(hints) => {
            const out = [];
            for (const link of document.querySelectorAll('link[href]')) {
                const media = (link.getAttribute('media') || '').toLowerCase();
                if (!media.includes('print')) continue;
                const rel = (link.getAttribute('rel') || '').toLowerCase();
                // rel=stylesheet means "a print stylesheet exists, emulate it".
                // anything else with media=print is usually a second document.
                out.push({kind: rel === 'stylesheet' ? 'stylesheet' : 'alternate',
                          url: link.href, rel: rel});
            }
            for (const a of document.querySelectorAll('a[href]')) {
                const hay = (a.href + ' ' + a.textContent + ' ' + (a.title || '')).toLowerCase();
                if (hints.some(h => hay.includes(h))) out.push({kind: 'anchor', url: a.href});
            }
            for (const el of document.querySelectorAll('[onclick]')) {
                if ((el.getAttribute('onclick') || '').includes('window.print')) {
                    out.push({kind: 'dialog-only', url: null});
                }
            }
            return out;
        }""",
        list(PRINT_HINTS),
    )
```

When nothing turns up, guessing is cheap: append `?print=1`, `?view=print` or `/print` to a
URL you already have. Check the result properly, though. A soft 404 answers `200` with the
site's shell, so the status code alone proves nothing, and an empty template is a miss. When
the site publishes a sitemap, that is a faster place to spot a whole family of print paths at
once, and [scraping a sitemap](how-to-scrape-a-sitemap-playwright.md) covers reading one.

## Emulate print media without printing anything

`page.emulate_media(media="print")` switches the media type the style engine resolves
against. Print rules start matching, screen-only rules stop, and no print job is created.
Nothing goes to a printer, no `beforeprint` event fires, and no PDF is produced.

The emulation is page state, not a one-shot call. It survives navigations on that page until
you change it, which is how a run ends up parked in print media for an hour by accident. Set
it back explicitly rather than trusting the next `goto` to clear it.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/article/123", wait_until="domcontentloaded")

    page.emulate_media(media="print")      # print rules match from here
    print_view = page.locator("main").inner_text()

    page.emulate_media(media="screen")     # explicit, not implied by the next goto
    screen_view = page.locator("main").inner_text()
```

Two things it does not do. It does not paginate the content into sheets, so `@page` sizing
and page breaks have no visible effect. And it fetches nothing, so a print stylesheet cannot
reveal rows that were never in the DOM in the first place.

## What print media adds, and what it quietly removes

Print cannot be interactive, so the stylesheet has to flatten every interaction the screen
version relies on. Accordions get opened, tabs get stacked, text clamped by a `max-height`
is released, and links often grow a visible URL after them through
`content: " (" attr(href) ")"`. On a page with a maintained print template that is a large
gain.

The same stylesheet is also where the site deletes everything it does not want on paper.
Navigation, cookie banners and share buttons, certainly, but frequently also the comment
thread, the review list, the related-items rail and every image on the page. If the reviews
are what you came for, print emulation is the one thing that will remove them.

The full-URL trick deserves its own warning, because it is a gift that does not arrive.
Generated content is not in the DOM. There is no node, so `text_content()` and `inner_text()`
both skip it, and
[`getComputedStyle(el, "::after").content`](how-to-scrape-css-pseudo-element-content-playwright.md)
is no substitute either: `attr()`
resolves at used-value time, so the computed string can come back with the literal
`attr(href)` still in it. Read `el.getAttribute("href")` and skip the detour.

## Diff the same selector under both media types

Do not reason about which view is richer. Measure it, on this page, with this selector. The
diff is short, and it is the only part of this article that is mandatory.

```python
def media_diff(page, selectors):
    rows = []
    for name, sel in selectors.items():
        loc = page.locator(sel)

        page.emulate_media(media="screen")
        screen_chars = len(" ".join(loc.all_inner_texts()))

        page.emulate_media(media="print")
        print_chars = len(" ".join(loc.all_inner_texts()))

        page.emulate_media(media="screen")
        rows.append({
            "selector": name,
            "nodes": loc.count(),            # control: this does not move
            "screen_chars": screen_chars,
            "print_chars": print_chars,
            "verdict": ("print richer" if print_chars > screen_chars else
                        "print poorer" if print_chars < screen_chars else "no change"),
        })
    return rows
```

The `nodes` column is a control, and knowing why is what makes the rest readable. `count()`
matches against the DOM, and a media switch adds or removes no nodes at all, so that number
is identical under both passes. If it ever moves, a script ran between your two reads and the
comparison is measuring that instead.

`all_inner_texts()` is the column that moves, because `innerText` is rendered text and
respects `display: none`. `all_text_contents()` would not move either, and that is a shortcut
worth taking: `text_content()` reads straight through CSS, so text hidden only by screen
rules is already yours without emulating anything. Print emulation is not how you uncover
hidden text. It is how you learn what the site treats as content.

## The print stylesheet is the site's own map of its content

Here is what makes this worth more than a shortcut. A print stylesheet is a hand-written
declaration, shipped by the site, of which parts of the page are not the article. No
heuristic about class names is as accurate as that, because it is not an inference. Someone
on that team decided, element by element, what does not belong on paper.

Read it as a labelling signal. Collect the elements whose computed `display` is `none` under
print but not under screen, and you have the site's own boilerplate list.

```python
def print_dropped_nodes(page):
    page.evaluate("""() => {
        let i = 0;
        for (const el of document.querySelectorAll('body *')) el.dataset.probe = String(i++);
    }""")

    def hidden_ids():
        return set(page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('body *')) {
                if (getComputedStyle(el).display === 'none') out.push(el.dataset.probe);
            }
            return out;
        }"""))

    page.emulate_media(media="screen")
    hidden_on_screen = hidden_ids()

    page.emulate_media(media="print")
    hidden_on_print = hidden_ids()

    page.emulate_media(media="screen")
    return hidden_on_print - hidden_on_screen     # the site's own boilerplate list
```

Keep the complement of that set and you have a content subtree that survives a rename of the
class names, which is the usual reason an article extractor rots. It pairs with the
text-density approach in [scraping news article text](how-to-scrape-news-article-text-playwright.md):
the stylesheet supplies the labels, the density scoring covers pages that ship no print rules
at all.

One caveat about that snippet. It writes `data-probe` attributes into the live DOM, which a
`MutationObserver` can see. Run it as a scratch pass while you work out the selectors, not
inside the session doing the real collection.

## One request instead of a paged crawl

The separate-URL case has a payoff the stylesheet case cannot match. Pagination exists for
screen reading, and a print template often has no reason to keep it, so the print URL
frequently returns the entire record set in one document. A long paged crawl collapses into a
single request, with no next-button loop and no chance of a page landing twice.

Verify that once instead of assuming it. Crawl the paged view properly one time, count the
rows, then check the print URL returns at least that many.

```python
def rows_from_print_view(page, print_url, row_selector, expected=None):
    response = page.goto(print_url, wait_until="domcontentloaded")
    if not response or not response.ok:
        return None
    rows = page.locator(row_selector).all_text_contents()
    if not rows:
        return None                       # soft 404: status 200, empty template
    if expected is not None and len(rows) < expected:
        return None                       # print view is capped too, keep paging
    return rows
```

The `expected` check is the whole safety net. Some print templates keep the page parameter
and print one page at a time, which looks like a complete result and is a fraction of one.
When the count comes up short, fall back to
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) and treat the print
URL as a failed optimisation rather than a source.

## Where the print route stops

Print views are unloved, and unloved templates go stale. A field added to the main page last
quarter is often missing from a print template whose last edit predates it, so a print scrape
returns a complete-looking record with an empty column the screen page fills in. Compare one
record across both views before switching a pipeline over.

Some print endpoints sit behind authentication when the screen page does not, on the
reasoning that only a signed-in user prints. You get a redirect to a login form where the
public page returned content, and the fix is a session rather than a selector:
[scraping behind a login](how-to-scrape-behind-login-playwright.md) covers holding one.

If the print path returns `application/pdf`, stop treating it as a scraping problem. There is
no DOM and no selector that applies, and the work moves to a PDF text layer instead.

Last, do not leave a long session sitting in print media. `window.matchMedia("print").matches`
reads `true` for as long as the emulation is on, which no ordinary session shows outside the
instant of a real print, with no `beforeprint` fired to explain it. That is cheap for a page
to read, in the same family as the other zero-JavaScript signals in
[CSS media query fingerprinting](css-media-query-fingerprinting.md). Do the diff, take the
answer, switch back to `"screen"`.

## Conclusion

The print view is one of the few places where a site hands you a curated version of its own
data and forgets to tell anyone. Look for the second URL first, because that is where the
pagination disappears and a crawl becomes a request. With no second URL, emulate print media
on the DOM you have, but treat it as an experiment rather than an improvement: run the diff,
read the character counts under both media, and accept that print sometimes removes exactly
what you wanted. The most durable thing you get is not the extra text. It is the drop list,
the site's own written answer to which parts of its page are not the content.

## Short answers to the questions that lead here

**How do I apply a print stylesheet in Playwright without printing?** Call
`page.emulate_media(media="print")`. It changes the media type the style engine resolves
against, so print rules match and screen-only rules stop. No print job, no `beforeprint`, no
PDF.

**Does emulating print reveal hidden text?** Not the way people expect. `text_content()`
already reads through CSS, so text hidden by screen rules is available without emulating
anything. Print emulation changes what is rendered, which is what matters for captures and
for learning what the site treats as content.

**Why does my diff show no difference at all?** You are probably comparing `count()` or
`text_content()`, and neither responds to a media change. Compare `inner_text()` or
`all_inner_texts()`, which return rendered text.

**Can emulating print lose content?** Yes, and that is the usual outcome on comment threads,
review lists, related-item rails and images. A print stylesheet is mostly a hiding
instruction, so run the diff before switching a scraper over to it.

**How do I get the full URLs a print stylesheet adds after links?** You do not read them
back. They are generated content with no node behind them, and the computed value can still
contain a literal `attr(href)`. Read the `href` attribute instead.

**Is a print URL always the whole record set?** No. Some print templates keep the page
parameter and print one page at a time. Count the rows from one proper paged crawl and refuse
any print result that comes back short.

**Does emulating print make me look like a bot?** Leaving it on does not help.
`window.matchMedia("print").matches` stays `true` for a whole session in which no print ever
happened. Switch back to `"screen"` once the comparison is done.

## Sources

- Playwright's [`page.emulate_media()`](https://playwright.dev/python/docs/api/class-page#page-emulate-media),
  retrieved 2026-08-28: it changes the CSS media type of the page and accepts `"screen"` and
  `"print"`.
- Playwright's [`locator.all_inner_texts()`](https://playwright.dev/python/docs/api/class-locator#locator-all-inner-texts)
  and [`locator.text_content()`](https://playwright.dev/python/docs/api/class-locator#locator-text-content),
  retrieved 2026-08-28, the rendered-text and raw-DOM-text reads the diff depends on.
- The CSS specification's treatment of `content` and `attr()`, where `attr()` resolves at
  used-value time, which is why the computed string is not a dependable place to harvest a
  link URL.

**See also:** [scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) for the
fallback when the print URL is capped, [scraping accordion and tab content](how-to-scrape-accordion-and-tab-content-playwright.md)
for the panels a print stylesheet unfolds for free,
[scraping news article text](how-to-scrape-news-article-text-playwright.md) for the extractor
the boilerplate list feeds, and
[generating a PDF with Playwright and Firefox](how-to-generate-pdf-with-playwright-firefox.md)
for the case where the print path returns a document instead of a page.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The first version of that diff
compared `count()` under each media type and reported no difference on any page, which read
as proof that print emulation did nothing: node counts do not move, only rendered text does.*
