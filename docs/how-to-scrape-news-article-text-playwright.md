---
title: "How to scrape news article text with Playwright"
description: "Scrape news article text with Playwright: pull headline, author and date from JSON-LD, isolate the article node, expand continue-reading, strip boilerplate."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 40
---


# How to scrape news article text with Playwright

To scrape a news article cleanly, work in order: read the headline, author and
date from the page's JSON-LD, isolate the single node that holds the article
prose, expand any "continue reading" control, then strip the boilerplate that
lives inside that node. Doing it in that order is what separates the sentences a
reader sees from the navigation, rails and modals wrapped around them.

The hard part of a news page is not fetching it. It is that the sentence you want
shares a DOM with the navigation, a related-story rail, a newsletter modal, three
inline promos, and a footer with more links than the article has words. Grab
`document.body.innerText` and you get all of it, in an order no reader ever sees.

This is a practical order for it: read the structured fields from the page head
before you touch the visible text, isolate the one node that holds the article,
expand anything hidden behind a "continue reading" control, then strip the
boilerplate that lives inside that node. The examples use stock Playwright through
`invisible_playwright`, so every method below is the ordinary Playwright API you
already know.

## Read the head before you read the body

The two fields people most often want, the author and the publish date, are the two
least reliable to scrape from visible text. A byline in the page might read "By our
staff", the visible date might say "3 hours ago", and both move around between
templates. The trustworthy copy lives in the head, in a JSON-LD `<script
type="application/ld+json">` block or in `<meta>` tags, because that is what the
site feeds to search engines and social cards and therefore keeps correct. The
general technique for reading that block is covered in
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md);
here it is applied to the three fields a news page cares about:

| Field | Most reliable source | Fallback |
|---|---|---|
| Headline | JSON-LD `headline` | `og:title` meta tag |
| Author | JSON-LD `author.name` | `author` meta tag |
| Publish date | JSON-LD `datePublished` | `article:published_time` meta tag |

Parse JSON-LD first and fall back to meta tags:

```python
import json
from invisible_playwright import InvisiblePlaywright

def structured_fields(page):
    fields = {}
    for handle in page.query_selector_all('script[type="application/ld+json"]'):
        try:
            data = json.loads(handle.inner_text())
        except (ValueError, TypeError):
            continue
        # JSON-LD is sometimes a list, sometimes a @graph wrapper
        candidates = data if isinstance(data, list) else data.get("@graph", [data])
        for node in candidates:
            if not isinstance(node, dict):
                continue
            if node.get("@type") in ("NewsArticle", "Article", "ReportageNewsArticle"):
                fields["headline"] = node.get("headline")
                fields["published"] = node.get("datePublished")
                fields["modified"] = node.get("dateModified")
                author = node.get("author")
                if isinstance(author, dict):
                    fields["author"] = author.get("name")
                elif isinstance(author, list) and author:
                    fields["author"] = author[0].get("name")
                elif isinstance(author, str):
                    fields["author"] = author
    return fields

def meta_fallback(page):
    def meta(selector):
        el = page.query_selector(selector)
        return el.get_attribute("content") if el else None
    return {
        "headline": meta('meta[property="og:title"]'),
        "published": meta('meta[property="article:published_time"]'),
        "author": meta('meta[name="author"]'),
    }

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/news/some-article", wait_until="domcontentloaded")
    fields = structured_fields(page)
    for key, value in meta_fallback(page).items():
        fields.setdefault(key, value)  # only fill what JSON-LD missed
    print(fields)
```

`seed=42` fixes the identity so the run is reproducible: the same browser, the same
fingerprint, every time. When a field comes back empty and you need to know whether
the page changed or your selector did, you replay the exact same run instead of
drawing a fresh random machine.

## Isolate the article node, do not read the whole page

With the metadata in hand, the body is a separate problem. The goal is to find the
single container that holds the article prose and read only that, rather than
reading the page and trying to subtract the noise afterwards.

Most news templates mark that container. Try, in order, `<article>`, then a role or
an attribute that names the body, then the JSON-LD `articleBody` if the page
included it. Take the first that yields a substantial block of text:

```python
def article_node_text(page):
    selectors = [
        "article",
        '[itemprop="articleBody"]',
        '[data-component="text-block"]',
        "main [class*='article-body']",
    ]
    best = ""
    for selector in selectors:
        for handle in page.query_selector_all(selector):
            text = handle.inner_text()
            if len(text) > len(best):
                best = text
    return best
```

Reading one node instead of the page is what keeps the related-story rail and the
site footer out of your text in the first place. The rails and modals are siblings
of the article container, not children of it, so scoping the read to the container
excludes most of them for free.

If even the scoped node comes back thin or empty, the block is often not detection
at all but ordinary loading: the body arrives in lazy chunks, or the server returned
a shell. That failure mode, and how to tell it apart from a real block, is the
subject of [scraping a site that blocks headless browsers](how-to-scrape-headless-blocked.md).

## Expand "continue reading" before you extract

To get the full body, click any "continue reading" or "show more" control first,
then scroll to force lazy-loaded paragraph chunks to attach, and wait for the
paragraph count to stop growing before you extract. Skip either step and you get
a clean-looking result that is silently incomplete, which is worse than an obvious
failure: a lot of article bodies stay truncated in the DOM until the reader acts,
and chunks below the fold only attach once they scroll into view.

Click the expander if it exists, then scroll to force the lazy chunks to load, then
wait for the paragraph count to stop growing:

```python
def expand_full_body(page):
    for label in ("text=Continue reading", "text=Show more", "text=Read more"):
        control = page.query_selector(label)
        if control and control.is_visible():
            control.click()
            page.wait_for_timeout(500)
            break

    # force lazy paragraph chunks to attach by scrolling to the bottom
    previous = 0
    for _ in range(20):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(400)
        count = len(page.query_selector_all("article p"))
        if count == previous:
            break
        previous = count
```

The `page.mouse.wheel` call moves the real cursor and fires the same scroll events a
reader would, which is also what most lazy-load observers are waiting for. Call
`expand_full_body(page)` after `goto` and before `article_node_text(page)`.

## Strip the boilerplate that lives inside the node

Even a well-scoped article container usually carries some furniture: an inline
newsletter signup, a "related" block, share buttons, image credits, a promo card two
paragraphs down. The clean way to remove these is to drop their nodes in the page
before you read the text, so the `innerText` you extract never contained them:

```python
def strip_boilerplate(page):
    junk = [
        "aside",
        "figure figcaption",
        "[class*='newsletter']",
        "[class*='related']",
        "[class*='promo']",
        "[class*='share']",
        "[data-testid*='ad']",
    ]
    page.evaluate(
        """(selectors) => {
            const root = document.querySelector('article') || document.body;
            for (const sel of selectors) {
                root.querySelectorAll(sel).forEach(n => n.remove());
            }
        }""",
        junk,
    )

def clean_paragraphs(page):
    strip_boilerplate(page)
    root = page.query_selector("article") or page
    paras = [p.inner_text().strip() for p in root.query_selector_all("p")]
    return [p for p in paras if len(p) > 40]  # drop captions and one-line promos
```

The length filter is a cheap last pass: real article sentences run long, while
captions, credits and "sign up for our newsletter" fragments are short. Reading
paragraph by paragraph rather than one `innerText` blob also gives you a natural
place to rejoin with `"\n\n"` and keep the paragraph breaks a reader sees.

Putting the four steps together, one run produces structured fields plus clean body
text:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/news/some-article", wait_until="domcontentloaded")

    fields = structured_fields(page)
    for key, value in meta_fallback(page).items():
        fields.setdefault(key, value)

    expand_full_body(page)
    body = "\n\n".join(clean_paragraphs(page))

    print(fields["headline"], "-", fields.get("author"), "-", fields.get("published"))
    print(body[:500])
```

## The paywall caveat, stated honestly

A real fingerprint gets a scraper past a soft metered paywall, the kind that counts
views per identity and blocks automation outright on suspicion alone; it does
nothing to a hard paywall where the article body is never sent to an unauthenticated
client, because that decision happens on the server before any HTML reaches you.
Confusing the two wastes time chasing a fix that cannot exist.

A soft metered wall counts article views per identity, where the identity is a
fingerprint plus a cookie. A headless or obviously-automated browser often trips
that wall on paragraph one, not because it read too many articles but because the
automation tell itself flips the meter straight to zero free reads. A browser that
presents a consistent, real fingerprint removes that specific signal, so a soft
meter treats the session like an ordinary first-time reader.

Because every field derives from the seed, you decide the identity deliberately. One
clean identity per run gives each run a fresh meter. A persistent identity that keeps
its cookies and fingerprint across runs behaves like one returning reader and keeps
its meter state, which is sometimes what you want; the mechanics of holding an
identity across sessions are in [persistent profiles](persistent-profiles.md), and
carrying an authenticated session across runs is covered in
[scraping behind a login](how-to-scrape-behind-login-playwright.md).

That boundary again, precisely: a real fingerprint does not defeat a hard paywall or
a server-side entitlement check. If the article body is never sent to an
unauthenticated client, no browser property changes that, because the decision is
made on the server before any HTML reaches you. What a real fingerprint stops is the
automated-browser signal that a soft meter uses to skip you past the free allowance.
Anything enforced on the server is out of scope, and treating it as a fingerprint
problem only wastes time.

## Conclusion

News extraction is mostly about order. Read the structured head first, because the
author and date are trustworthy there and fragile in the visible text. Scope the read
to the one article node instead of subtracting noise from the whole page. Expand the
"continue reading" control and scroll the lazy chunks in before you extract, or you
ship a result that looks complete and is not. Strip the furniture that survives
inside the node, and filter by length. Fix the seed so a thin result is reproducible
and you can tell a site change from a selector change. And keep the paywall boundary
straight: a real browser flips a soft meter back to a normal reader, and does nothing
at all to a hard server-side check.

## Short answers to the questions that lead here

**Where do I get the author and publish date reliably?** From the JSON-LD block in
the head, then `<meta>` tags as a fallback. The visible byline and "3 hours ago"
timestamp are the least stable things on the page.

**How do I get just the article text and not the whole page?** Scope the read to the
article container (`<article>`, `articleBody`, or the site's body class) and read
that node's text, rather than reading the page and trying to remove the navigation
and rails afterwards.

**My scraper only gets the first few paragraphs. Why?** The body is behind a
"continue reading" expander or loads in lazy chunks. Click the expander and scroll to
the bottom until the paragraph count stops growing, then extract.

**Does a real fingerprint get me past a paywall?** It gets you past a soft metered
wall that was flipping to zero free reads because it detected automation. It does
nothing to a hard paywall where the body is never sent to an unauthenticated client,
because that is decided on the server.

**How do I keep or reset the paywall meter deliberately?** Because the identity comes
from a seed, a fresh identity per run gets a fresh meter, and a persistent identity
keeps its meter state across runs. You choose which, rather than leaking a tell that
decides for you.

**Why fix the seed when scraping?** So a failing extraction is reproducible. With a
random identity each run you cannot tell whether the site changed or the machine did;
with a fixed seed you replay the same browser and bisect.

## Sources

- The [Playwright Python API](https://playwright.dev/python/docs/api/class-page)
  (`query_selector_all`, `inner_text`, `get_attribute`, `evaluate`, `mouse.wheel`),
  used unchanged through `invisible_playwright`.
- The [Schema.org `NewsArticle`](https://schema.org/NewsArticle) / `Article`
  structured-data fields, as emitted in the JSON-LD and Open Graph blocks that news
  templates ship for search and social.
- This project's own notes on soft-meter behaviour: a metered wall counts views per
  fingerprint-plus-cookie identity, and a seed-reproducible browser lets you set that
  identity on purpose.

**See also:** [persistent profiles](persistent-profiles.md) for keeping one reader
identity across runs, [scraping behind a login](how-to-scrape-behind-login-playwright.md)
for carrying an authenticated session, [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the head-reading technique in general, and [pinning fingerprint fields](pinning.md)
for forcing a single field while leaving the rest seed-derived.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The metadata-first
order and the "continue reading" trap are both mistakes that shipped incomplete text
before I fixed them.*
