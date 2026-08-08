---
title: "How to scrape forum and community threads with Playwright"
description: "Scrape forum and community threads with Playwright: walk the reply tree depth-first, expand collapsed branches over XHR, strip quoted text under one identity."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 46
---


# How to scrape forum and community threads with Playwright

To scrape forum and community threads with Playwright, treat each thread as a tree:
walk the reply nesting depth-first, expand every collapsed "load more replies" branch
over XHR before reading the DOM, and strip quoted blockquotes so a copied paragraph is
not counted twice. Run the whole multi-thread sweep under one seeded identity and pace
it to a per-identity request budget. The rest of this page shows each step in stock
Playwright.

A product listing is a flat sequence. A forum thread is not. Replies nest under
parents to an arbitrary depth, whole branches hide behind a "load more replies"
control, and a single post can quote another post word for word. Scrape it the way you
would scrape a list of rows and you get a flattened mess: quoted text counted twice,
deep branches missing entirely, and vote scores attached to the wrong body.

This page treats the thread as what it is, a tree, and walks it in the order that keeps
the structure intact. The extraction is stock Playwright. The one part that is not is
keeping a broad, multi-thread crawl reading as a single member rather than a new device
per thread.

## Why a forum thread is a tree, not a list

Every reply has a parent. The top-level posts hang off the thread, replies hang off
those, replies to replies hang off those, and there is no fixed limit to how deep it
goes. In the DOM this shows up as nesting: a reply's children live inside its own
container, not as siblings after it.

Three consequences fall out of that shape, and each one breaks a naive scraper:

- **Depth is unbounded.** You cannot write a fixed number of nested loops. The walk has
  to recurse or it will stop at whatever depth you happened to hard-code.
- **Metadata sits beside the body, not inside it.** The vote score, the timestamp and
  the user flair are usually sibling nodes to the post body within the same reply
  container. Grab the body text alone and you have thrown away everything that tells you
  who said it and how the community rated it.
- **A post can contain a copy of another post.** Quoted text is embedded, usually inside
  a blockquote, and it is a verbatim copy of a body that already exists elsewhere in the
  tree. If you extract text blindly, you count that content twice and corrupt any
  frequency or reply-count analysis you run later.

So the job is not "collect all the comment nodes". It is "walk the tree, keep each
node's own text separate from what it quotes, and carry the sibling metadata along with
each body".

## Set up one identity for the whole crawl

Before touching the DOM, launch the browser once with a fixed seed and keep it for the
entire sweep. A community site rarely rate-limits a single thread. It watches identity
across a broad crawl, and a harvest that spans dozens of threads over dozens of pages is
exactly the pattern it is built to catch.

```python
from invisible_playwright import InvisiblePlaywright

# One seed, one identity, reused for every thread and every page in the run.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/forum/thread/1001")
    page.wait_for_selector(".comment")
    # ... walk the tree (below)
```

The `browser` here is a real Playwright `Browser`, so `new_page`, `goto`,
`query_selector_all` and every other method behave exactly as they do upstream. The only
difference from plain `firefox.launch()` is that the GPU, fonts, audio device, screen
and the many other fields behind them are all derived from that one seed and stay
identical for the whole run. Requesting fifty threads from one consistent member looks
ordinary. Requesting fifty threads each from a freshly randomised device does not.

## Walk the reply tree depth-first

Recursion mirrors the structure. At each node, read the node's own fields, then descend
into its direct children before moving to the next sibling. Depth-first keeps a branch
together in your output, which is what you want when the meaning of a reply depends on
the reply it answers.

```python
def extract_node(handle):
    """Read one reply's own fields, then recurse into its direct children."""
    # Metadata lives in sibling nodes to the body, so read it off the container.
    author = handle.eval_on_selector(".comment__author", "el => el.textContent.trim()")
    score = handle.eval_on_selector(".comment__score", "el => el.textContent.trim()")

    # Direct children only: the container each reply keeps for its own replies.
    child_handles = handle.query_selector_all(":scope > .comment__children > .comment")

    return {
        "author": author,
        "score": score,
        "body": original_text(handle),          # defined in the next section
        "replies": [extract_node(c) for c in child_handles],
    }

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/forum/thread/1001")
    page.wait_for_selector(".comment")
    roots = page.query_selector_all("#thread > .comment")
    tree = [extract_node(r) for r in roots]
```

The load-bearing detail is `:scope > .comment__children > .comment`. The
[`:scope` pseudo-class](https://developer.mozilla.org/en-US/docs/Web/CSS/:scope) anchors
the match to the node you called the query on; without the `:scope >` combinator a plain
descendant selector matches every reply anywhere below the node, including grandchildren,
and your recursion visits them again at the wrong depth. The direct-child combinator is
what keeps each post counted once and placed correctly.

## Expand collapsed branches before you read

Deep branches do not ship in the initial HTML. The server sends the thread down to some
depth and replaces the rest with a "load more replies" control that fetches the missing
subtree over XHR when clicked, the same [load-more button pattern](how-to-scrape-load-more-button-playwright.md)
seen on flat listings but applied to nested replies. Read the DOM before expanding and
those branches simply are not there. There is no error, just a silent hole in your data.

So expansion has to happen first, and it has to repeat, because expanding one branch can
reveal another collapsed control nested inside it. Loop until none remain.

```python
def expand_all(page, pause_ms=1200):
    """Click every 'load more' control, repeatedly, until the tree is fully open."""
    while True:
        controls = page.query_selector_all(".load-more-replies")
        if not controls:
            break
        for control in controls:
            if control.is_visible():
                control.click()               # arcs to the control on a Bezier curve
                # Each click is an XHR that injects a subtree. Wait for it to land.
                page.wait_for_load_state("networkidle")
        page.wait_for_timeout(pause_ms)       # pacing, not decoration - see below
```

If you would rather read the injected subtrees straight off the wire instead of
re-scraping the DOM after each click, the response bodies those controls fetch are
plain data, and [capturing the XHR responses directly](how-to-capture-xhr-api-responses-playwright.md)
is often cleaner than waiting for the DOM to settle. Either way, the branch has to be
requested before it can be read.

## Separate quoted text from original text

A quote is a copy. When someone replies "as the parent said, X" and their client embeds
the parent's paragraph inside a blockquote, that paragraph now exists in two nodes: the
original post and the quoting reply. Extract the reply's full text and you have double
counted X.

The fix is to read the body with the quoted blocks removed, and, if you want them,
capture the quotes separately as references rather than as content.

```python
ORIGINAL_TEXT_JS = """
el => {
    // Clone so we do not mutate the live page, then drop embedded quotes.
    const clone = el.cloneNode(true);
    clone.querySelectorAll("blockquote, .quoted-post").forEach(q => q.remove());
    return clone.textContent.trim();
}
"""

QUOTED_REFS_JS = """
el => Array.from(el.querySelectorAll("blockquote, .quoted-post"))
        .map(q => q.textContent.trim())
"""

def original_text(handle):
    return handle.evaluate(ORIGINAL_TEXT_JS)

def quoted_refs(handle):
    return handle.evaluate(QUOTED_REFS_JS)
```

Now each node carries only the words its author actually wrote, and the quotes it leans
on are kept as a separate list you can match back to the posts they came from. Any later
analysis, top authors, most-repeated claims, reply volume, is counting real
contributions instead of an echo.

## Pace the sweep to stay under the per-identity budget

Here is the honest caveat, and it is the part that a fingerprint alone does not solve.
Matching a real browser removes the automation and device tells, so the crawl stops
looking like a bot on inspection. It does nothing about volume. Expanding every
collapsed branch multiplies requests: one thread that arrived as three HTTP responses
can become forty once you have clicked open every subtree, and a hot thread spans dozens
of pages on top of that. A stable identity that behaves like a member is still a member
who can be throttled, and a member firing forty XHRs a second is a member who gets rate
limited.

So pace it. The `wait_for_timeout` in the expansion loop is not cosmetic, and the same
budget applies across threads, not just within one. The mechanics of holding a crawl
under a per-identity request budget have [their own page on rate limiting your
scraper](how-to-rate-limit-your-scraper-playwright.md), and for threads that paginate
rather than expand in place, [the paginated-pages walk](how-to-scrape-paginated-pages-playwright.md)
covers advancing page to page without tripping the same signal. The identity keeps you
from looking like fifty devices. The pacing keeps you from looking like one very busy
one.

## Conclusion

A thread is a tree, so scrape it as a tree. Walk it depth-first so each branch stays
together, expand the collapsed branches over XHR before you read anything, and strip
quoted blocks so a copied paragraph is not counted as new content. Run the whole
multi-thread, multi-page sweep under one seeded identity so the site sees a single
member, and pace that sweep so the single member does not exhaust its own request
budget. The fingerprint is what lets the crawl look ordinary. The structure and the
pacing are what make the data correct and the crawl sustainable.

## Short answers to the questions that lead here

**Why does my forum scraper miss deep replies?** Because they are not in the initial
HTML. They sit behind a "load more replies" control that fetches them over XHR, so you
have to click every such control, repeatedly, before reading the DOM.

**How do I avoid counting quoted text twice?** Clone each reply node, remove the
blockquote and quoted-post elements, and read the text from the clone. Keep the quotes
as a separate list of references if you need them.

**Should I flatten the thread into a list?** Only after you have walked it as a tree.
Flatten during extraction and you lose the parent-child relationships and mis-attach
metadata. Walk depth-first, then flatten your finished structure if you want.

**Why do I get throttled halfway through a big crawl?** Community sites throttle by
identity across the whole crawl, not per thread. Expanding branches multiplies requests,
so a deep sweep needs pacing even when the browser fingerprint is clean.

**Does a stable seed help or hurt across many threads?** It helps. A fresh random device
per thread is a stronger signal than one consistent member browsing many threads. Reuse
one seeded identity for the whole run.

**Where does the vote score live in the DOM?** In a sibling node to the post body inside
the same reply container, alongside the author and timestamp. Read it off the container,
not out of the body text.

## Sources

- The real `invisible_playwright` API as documented in [Quickstart](quickstart.md) and
  [Configuration](configuration.md): `InvisiblePlaywright(seed=...)` returns a stock
  Playwright `Browser`.
- Standard Playwright element-handle and evaluation methods, used exactly as documented
  upstream, for the tree walk and the quote-stripping.
- [MDN: the `:scope` pseudo-class](https://developer.mozilla.org/en-US/docs/Web/CSS/:scope),
  for why `:scope > .comment` matches direct children only, not every descendant.
- This project's own measurements on identity-based throttling across broad crawls,
  summarised in the pacing section above.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading injected subtrees off the wire, [rate limiting your
scraper](how-to-rate-limit-your-scraper-playwright.md) for the per-identity budget, and
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) for threads that
span many pages.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The tree walk and the
quote-stripping are stock Playwright; the one seeded identity across a broad crawl is the
part the wrapper adds.*
