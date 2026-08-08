---
title: "How to extract Open Graph and meta tags with Playwright"
description: "Extract Open Graph and meta tags with Playwright: read the rendered head after JS injection, resolve relative og:image URLs, and apply og:title fallbacks."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 58
---


# How to extract Open Graph and meta tags with Playwright

To extract Open Graph and meta tags with Playwright, read the rendered `<head>` after
JavaScript has run: query both `<meta property>` and `<meta name>` elements, take each
`content` attribute, resolve a relative `og:image` against `page.url`, and fall back to
the document `<title>` when `og:title` is absent. Parsing the server's first response
instead of the rendered DOM is the mistake that returns empty tags on modern sites.

Open Graph looks like the easiest thing on the page to read. It is a handful of
`<meta>` tags in the `<head>`, the values are right there in the attributes, and a
one-line query gets them. Then you ship it and half the `og:image` values are broken
links, a tenth of the pages return an empty dict, and a few come back looking perfect
from a page that never actually loaded for you.

This page is the four things that turn that one-liner into something you can trust:
where the tags really live, why you must read the rendered head rather than the first
response, how to resolve a relative `og:image`, and the fallback chain to use when a
key is simply absent. It ends on one honest caveat that is specific to running against
sites that would rather you did not.

## Where Open Graph tags actually live

Open Graph is a set of repeated `<meta property="og:...">` elements inside `<head>`.
The value is in the `content` attribute, not in the element text:

```html
<meta property="og:title" content="A page title">
<meta property="og:image" content="/static/card.png">
<meta property="og:type" content="article">
```

Two details trip people up. First, the namespace uses the
[`property`](https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Structuring_content/Webpage_metadata)
attribute, while the ordinary page description and many secondary card namespaces use
`name` instead. If your selector only looks at `property`, you silently drop
`<meta name="description">` and every `name`-based card tag. Read both.

Second, a key can legitimately appear more than once (`og:image` is the common case for
pages that offer several card images). For a first pass, keeping the first occurrence of
each key is the sane default; just know you are making that choice rather than getting
it for free.

```python
from invisible_playwright import InvisiblePlaywright

def read_meta(page):
    # Read both property= and name= tags from the head. Keep the first
    # value seen for each key.
    rows = page.eval_on_selector_all(
        "head meta[property], head meta[name]",
        """els => els.map(e => ({
            key: e.getAttribute('property') || e.getAttribute('name'),
            value: e.getAttribute('content')
        }))"""
    )
    meta = {}
    for row in rows:
        key, value = row["key"], row["value"]
        if key and value is not None and key not in meta:
            meta[key] = value
    return meta

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="load")
    meta = read_meta(page)
    print(meta.get("og:title"))
```

`browser` here is a real Playwright `Browser`, so `eval_on_selector_all`, `goto`, `title`
and everything else behave exactly as they do upstream. The seed only fixes the identity
the page sees, which matters later on this page.

## Read the rendered head, not the initial response

Read the Open Graph tags from the rendered DOM, not from the raw HTML the server first
returns. For a large and growing share of sites, parsing that first response reads
nothing, because the app ships a near-empty `<head>` and injects the Open Graph tags with
JavaScript after the first paint. The tags exist by the time a human sees the page; they
do not exist in the bytes the server sent.

Reading through a rendered DOM is what makes this correct. `eval_on_selector_all` runs
against the live document, so a tag written by a script five hundred milliseconds after
load is there when you query. The only thing you have to get right is timing: on an app
that injects late, query before the injection and you get the pre-injection head.

A load-state wait covers most cases, but the honest tool is to wait for the specific tag
you need to exist:

```python
page.goto("https://example.com", wait_until="load")

# On an app that injects og tags late, wait for the one you depend on
# rather than guessing at a sleep.
try:
    page.wait_for_selector('head meta[property="og:title"]', timeout=5000)
except Exception:
    pass  # fall through to the fallback chain below

meta = read_meta(page)
```

Waiting for a fixed number of seconds is the thing to avoid; it is either too short on a
slow load or wasted on a fast one. [Waiting for the right condition instead of a fixed
delay](how-to-wait-for-page-load-playwright.md) is the same discipline that every other
dynamic-page extraction needs, and it is worth reading once for the whole class of
problem.

## Resolve og:image against the document base

The single most common defect in Open Graph extractors is treating `og:image` as an
absolute URL. It routinely is not. It comes in three shapes:

- Absolute: `https://cdn.example.com/card.png` - use as is.
- Root-relative: `/static/card.png` - resolve against the site origin.
- Protocol-relative: `//cdn.example.com/card.png` - inherits the page's scheme.

`urllib.parse.urljoin` handles all three correctly when you give it the document's own
URL as the base, and `page.url` is the right base because it reflects the final URL after
any [redirect](https://playwright.dev/python/docs/navigations), not the one you passed
to `goto`:

```python
from urllib.parse import urljoin

def resolve_image(page, meta):
    raw = meta.get("og:image")
    if not raw:
        return None
    # page.url is the post-redirect document URL, the correct base for
    # relative and protocol-relative image references.
    return urljoin(page.url, raw)
```

The specification says `og:image` should be absolute. In the field a large fraction of
pages ignore that, and an extractor that trusts the spec produces broken links for every
one of them. Resolve unconditionally; `urljoin` leaves an already-absolute URL untouched,
so it costs nothing on the pages that got it right.

## Fallback chains when a key is absent

Not every page sets every key, and a missing key should degrade rather than crash. Open
Graph itself defines a natural chain: when `og:title` is absent, the document `<title>`
is the intended human-readable name, and `<meta name="description">` backs up
`og:description`.

```python
def card(page, meta):
    title = meta.get("og:title") or page.title()
    description = meta.get("og:description") or meta.get("description")
    image = resolve_image(page, meta)
    url = meta.get("og:url") or page.url
    return {"title": title, "description": description, "image": image, "url": url}
```

The order encodes a preference: the curated card value first, the generic document value
second, never a hard failure. This is the same shape you want for
[article headline and body text](how-to-scrape-news-article-text-playwright.md), where the
`og:title` and the on-page `<h1>` are two witnesses to the same fact and either can be
missing on any given page.

## The stealth caveat: a real head over a placeholder body

Here is the failure that is specific to scraping sites that gate their content, and the
reason Open Graph metadata is a weaker "did the page load" signal than it looks.

A gated response can serve a complete, correct `<head>` - all the Open Graph tags
present and well formed - on top of a body that is a placeholder, a challenge, or an
empty shell. The card metadata is often static or edge-cached and comes back intact even
when the real article, listing or product body was withheld from you. Read only the
`<head>` and you will record a perfect card for a page you never actually saw, and it
will look like a success in your logs.

So confirm you also got real body content before you trust the metadata as evidence the
page loaded for real. Assert the presence of something that only exists on the genuine
page, not merely the absence of an error:

```python
meta = read_meta(page)
result = card(page, meta)

# The head can be intact while the body is a placeholder. Require a real
# content signal before trusting the card as proof the page loaded.
body_text = page.inner_text("body")
if len(body_text) < 200 and not page.query_selector("article, main, [role=main]"):
    raise RuntimeError("head parsed but body looks like a placeholder")
```

This is the positive-signal rule that runs through all of this project's testing: an
empty or blocked result is a failure, not a pass, and the check has to look for the thing
that should be there rather than for the thing that should not.
[The full argument for asserting presence over absence](how-to-test-bot-detection.md) is
worth reading, because it is the mistake that most quietly corrupts a dataset.

The reason a placeholder body shows up at all is a separate question about whether the
page trusted your session, and that is where the browser underneath matters. Driving
stock Playwright over a Firefox patched at the C++ level, a fixed seed gives the same
roughly 400-field fingerprint on every run, so when a body does come back thin you can
replay the exact identity and tell a gate apart from a slow load instead of guessing.
Everything upstream of that - not setting contradictory headers, not stacking a second
spoofer - is covered in [how to scrape without getting
blocked](how-to-scrape-without-getting-blocked.md).

## Conclusion

Open Graph extraction is easy to write and easy to write wrong. Read both `property` and
`name` tags from the rendered head rather than the first response, resolve `og:image`
against `page.url` every time, fall back from `og:title` to the document title when a key
is absent, and never treat an intact head as proof the page loaded until a real body
confirms it. The first three make the data correct; the last one keeps a gated placeholder
out of your dataset.

## Short answers to the questions that lead here

**How do I get Open Graph tags with Playwright?** Query `head meta[property], head
meta[name]` against the rendered DOM with `eval_on_selector_all`, read the `content`
attribute, and keep the first value per key.

**Why is my og:image a broken link?** It is almost certainly relative or
protocol-relative. Resolve it against `page.url` with `urllib.parse.urljoin`, which leaves
absolute URLs unchanged.

**Why does parsing the raw HTML return no og tags?** Many single-page apps inject the tags
with JavaScript after load, so they exist in the rendered head but not in the server's
first response. Read through the browser and wait for the tag you need.

**What do I use when og:title is missing?** Fall back to the document `<title>`, and back
`og:description` with `<meta name="description">`. Prefer the curated value, never crash on
its absence.

**Should I trust the metadata if the body is empty?** No. A gated page can serve a correct
head over a placeholder body. Require a positive body-content signal before treating the
card as evidence the page really loaded.

**How do I read the card namespace that uses name instead of property?** Include
`meta[name]` in the same selector. The secondary card namespace uses `name`, so a
`property`-only query drops it along with the page description.

## Sources

- The Open Graph protocol's own definition of the `og:` namespace and its title and
  description fallbacks, read against real rendered pages rather than a spec in the
  abstract.
- [MDN's guide to page metadata](https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Structuring_content/Webpage_metadata),
  which documents the `property`-based namespace Open Graph tags use and shows the same
  `og:title` / `og:image` shape used above.
- [Playwright's own navigation docs](https://playwright.dev/python/docs/navigations),
  which describe how a client-side redirect resolves before `page.url` reflects the
  final address.
- This project's release gates, whose recurring lesson is that an empty or blocked result
  is a failure and a check must assert the presence of the right signal, not the absence
  of a wrong one.

**See also:** [waiting for the right load condition](how-to-wait-for-page-load-playwright.md)
for the timing half of this, [scraping without getting blocked](how-to-scrape-without-getting-blocked.md)
for why a body comes back as a placeholder in the first place, and
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the other metadata block that lives in the same head.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The placeholder-body caveat
is here because a head that parses perfectly is the most convincing way to record a page
you never actually loaded.*
