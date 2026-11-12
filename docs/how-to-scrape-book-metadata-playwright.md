---
title: "How to scrape book metadata with Playwright"
description: "Scrape book metadata with Playwright: read the Book and workExample nodes, normalise every ISBN to ISBN-13, keep one row per edition, and split contributor roles out of the byline."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 110
---


# How to scrape book metadata with Playwright

To scrape book metadata with Playwright, read the `Book` node the page already ships and
treat its `workExample` entries as the rows: one row per edition, keyed on an ISBN
normalised to ISBN-13 with the printed form kept beside it, contributor roles split out of
the byline, the edition date and the first publication date stored as two separate fields,
and series position parsed by a stated rule rather than a hopeful regex.

Book pages are the friendliest looking data on the web and some of the easiest to store
wrongly. A title, an author, a cover, a year, an ISBN. Four of those five are ambiguous,
and the ambiguity does not announce itself: the scrape runs, the rows land, and the damage
only surfaces when a second source arrives and nothing joins.

The reason is that a book page describes two things at once. There is the work, which is
what a reader means by the title, and there is the edition, which is what the page is
selling. This article takes the edition as the row, normalises the identifier that lets
editions from different sites meet, and handles the four fields that get flattened most:
identifiers, contributors, dates and series position.

## Where the edition record actually lives

Before writing a selector, look at what the server sent. Most retail and catalogue book
pages carry an `application/ld+json` block with a schema.org `Book` node in it, and the
useful part is `workExample`: the list of editions, each a `Book` in its own right with its
own `isbn`, `bookEdition`, `bookFormat`, `numberOfPages` and `datePublished`. The rendered
page shows one of those editions and hides the rest behind a format selector.

```python
import json
from invisible_playwright import InvisiblePlaywright

def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

def read_ld_json(page):
    nodes = []
    for raw in page.locator('script[type="application/ld+json"]').all_text_contents():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for block in as_list(data):
            if isinstance(block, dict):
                nodes.extend(as_list(block.get("@graph", block)))
    return nodes

def book_editions(nodes):
    """Yield (work, edition) pairs. A Book with no workExample is its own edition."""
    for node in nodes:
        if "Book" not in as_list(node.get("@type")):
            continue
        editions = as_list(node.get("workExample"))
        if not editions:
            yield node, node
            continue
        for edition in editions:
            yield node, edition
```

The pairs are deliberate. The work node carries what belongs to the text, the edition node
what belongs to the printed object, and merging them at extraction time loses the ability
to say which was which. When there is no `workExample`, one node plays both parts. If the
block is missing or comes back empty, read that as a signal rather than a shrug:
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
covers the `@graph` wrapper and what an empty result usually means.

## The row is an edition, not a work

Collapsing editions into one row per work is the most expensive decision here, and it is
usually made by accident. One work has many editions: hardback, paperback, large print, a
reissue with a new cover, a translation, an audiobook. Each has its own ISBN, publisher,
page count and publication date.

Keep the work, but do not make it the row. A work-level table holds the title, the original
language and the first publication date; an edition table holds everything that varies per
printing, keyed back to the work. Flatten to one row per work and you have to choose which
edition's publisher and year survive, and whatever you choose is wrong for somebody.

If a second table is more than the job needs, keep one edition table with a `work_key`
built from the normalised title plus the primary author surname. Treat it as a grouping
hint, not an identity, and see
[writing scraped rows into a database](how-to-scrape-into-a-database-playwright.md) for the
upsert that keeps a re-run from duplicating.

## Normalise to ISBN-13 and keep the printed form

An ISBN-10 and an ISBN-13 can name the same edition, so a pipeline that stores whichever
the page printed will hold two rows for one book and never notice. Normalise on the way in:
strip the hyphens, drop the ISBN-10 check digit, prefix `978`, and compute a fresh check
digit over the twelve digits that remain.

Two details break naive code. The ISBN-10 check digit can be the character `X`, which
stands for the value ten, so an `int()` cast on the last character raises and a `\d{10}`
pattern misses the identifier. And ISBN-13s beginning `979` have no ISBN-10 form at all,
because there is no nine-digit core to build one from.

```python
import re

LABEL = re.compile(r"^\s*ISBN(?:[-\s]?1[03])?\s*:?\s*", re.I)

def isbn_digits(raw):
    """Drop the label first, or 'ISBN-13: 978-...' keeps a stray 13 on the front."""
    return re.sub(r"[^0-9X]", "", LABEL.sub("", raw or "").upper())

def isbn10_check(core):                       # core = the first 9 digits
    total = sum((10 - i) * int(d) for i, d in enumerate(core))
    value = (11 - total % 11) % 11
    return "X" if value == 10 else str(value)

def isbn13_check(core):                       # core = the first 12 digits
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(core))
    return str((10 - total % 10) % 10)

def to_isbn13(raw):
    """ISBN-13 form, or None when the check digit does not verify."""
    digits = isbn_digits(raw)
    if "X" in digits[:-1]:                    # X is legal only as the last character
        return None
    if len(digits) == 13 and digits.isdigit():
        return digits if digits[12] == isbn13_check(digits[:12]) else None
    if len(digits) == 10:
        if digits[9] != isbn10_check(digits[:9]):
            return None
        core = "978" + digits[:9]
        return core + isbn13_check(core)
    return None
```

Verify the check digit instead of trusting the length. A page with a typo, or a store code
that is thirteen characters long, hands you something that looks like an identifier and
joins to nothing. Store two columns: `isbn13` for joining, and `isbn_source` holding the
exact string the page printed. The source form is what a site's own search box accepts, and
it is the only way to audit a bad normalisation.

## Three identifiers on the page, one of them travels

A book page usually carries at least three identifiers and they are not interchangeable.
The ISBN identifies the edition everywhere. The store's own product code, a short
alphanumeric string in the canonical URL, identifies that edition inside that one store
only. And an internal numeric id, in a `data-` attribute or a query parameter, often
identifies the work rather than the edition, and changes when the site is redesigned.

Only the first one travels. Make it the primary key and keep the other two as source-scoped
columns, `source_site` plus `source_id`, so a store's private numbering never leaks into
the identity of a book.

```python
STORE_CODE = re.compile(r"/(?:product|item|edition)/([A-Za-z0-9]{8,14})")

def first_text(page, selector):
    node = page.locator(selector).first
    return node.text_content().strip() if node.count() else None

def identifiers(page, edition):
    """Three ids, kept apart. Only the ISBN means anything off this site."""
    match = STORE_CODE.search(page.url)
    node = page.locator("[data-product-id]").first
    return {
        "isbn_source": edition.get("isbn") or first_text(page, "[itemprop=isbn]"),
        "store_code": match.group(1) if match else None,
        "site_id": node.get_attribute("data-product-id") if node.count() else None,
    }

def row_key(source_site, ids):
    isbn13 = to_isbn13(ids["isbn_source"] or "")
    if isbn13:
        return ("isbn13", isbn13)
    local = ids["store_code"] or ids["site_id"]
    if local:
        return ("site_local", f"{source_site}:{local}")   # never merged across sites
    return None
```

This is where the approach stops. Audiobooks, many digital editions and most self-published
titles carry no ISBN at all, so the store code is the only identifier they have. The key
has to allow that fallback, and rows keyed that way must never be merged across sites:
nothing in them proves two stores describe the same object.

## Contributors are roles, not one author string

The byline on a book page is a role list rendered as a sentence. `Jane Doe, John Smith
(Translator), Ada Lovelace (Illustrator)` is three people doing three different jobs, and
storing that as one `authors` field means every later query for books by Jane Doe returns
the ones she translated. Split it at extraction time: by the time the rows land, some kept
the parentheses and some dropped them.

When JSON-LD is present the split is free. Schema.org gives `author`, `translator`,
`editor` and `illustrator` as separate fields, and each can be a single object or a list.
When only the rendered byline exists, read the parenthesised role label and treat an
unlabelled segment as an author.

```python
ROLE_LABEL = re.compile(r"\(([^)]+)\)\s*$")
KNOWN_ROLES = {"author", "translator", "illustrator", "editor", "narrator"}

def contributors_from_jsonld(work, edition):
    rows = []
    for field in ("author", "translator", "editor", "illustrator"):
        for person in as_list(edition.get(field) or work.get(field)):
            name = person.get("name") if isinstance(person, dict) else person
            if name:
                rows.append({"name": name.strip(), "role": field})
    return rows

def contributors_from_byline(byline):
    """'Jane Doe, John Smith (Translator)' -> two rows carrying explicit roles."""
    rows = []
    for part in (p.strip() for p in byline.split(",")):
        if not part:
            continue
        match = ROLE_LABEL.search(part)
        role = "author"
        if match:
            word = match.group(1).strip().lower().split()[0]
            role = word if word in KNOWN_ROLES else "contributor"
            part = ROLE_LABEL.sub("", part).strip()
        rows.append({"name": part, "role": role})
    return rows
```

The comma split has a hole worth naming. A comma also separates a surname-first name, so
`Doe, Jane` becomes two contributors called Doe and Jane, and nothing in the string tells
the two meanings apart. The defensive version splits on commas only when a parenthesised
role label appears in the byline, keeps the raw byline on the row, and emits contributors
as their own rows.

## The date on the page is the reprint, not the first edition

The date a book page shows is the publication date of the edition it is selling, which for
anything older than a few years is a reprint date. A novel from 1979 in a 2021 paperback
shows 2021. Store that as the book's year and every chronology built on the dataset is
wrong, in one direction, silently.

Two fields, always. `edition_published` comes from the edition node and is safe to take
from the page. `work_first_published` describes the text, so it must come from a source
talking about the work: the work-level node, an author or series page, or a stated
first-publication line. Never fill it from the edition's own date. Store a precision flag
beside each, because a bare year written into a date column becomes the first of January
and reads downstream as a real day.

```python
def edition_dates(work, edition):
    """Two dates, never one. The edition's is a reprint date more often than not."""
    edition_date = edition.get("datePublished") or edition.get("copyrightYear")
    work_date = work.get("datePublished") if work is not edition else None
    return {
        "edition_published": as_date(edition_date),
        "edition_precision": precision_of(edition_date),
        "work_first_published": as_date(work_date),
        "work_precision": precision_of(work_date),
    }

def precision_of(value):
    """'2019' -> year, '2019-05' -> month, anything longer -> day."""
    if not value:
        return None
    return {4: "year", 7: "month"}.get(len(str(value).strip()), "day")
```

`as_date` is the ordinary localized-date problem, and it depends on the locale the page
rendered in rather than on the parser.
[Cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
reads the browser's resolved locale once per page and parses everything against it.

## Series position is a sentence, not a field

Series position almost never arrives as a number. It arrives as text: `Book 2 of 5`, `#2`,
`Volume II`, a subtitle, or a breadcrumb that names the series and stops there. Parsing it
is fine, as long as the rules are written down and the parser refuses what they do not
cover. Three rules carry most real pages.

- Store the position as a string, not an integer. Novellas get numbered 1.5 and 2.5, and an
  `int()` cast either raises or quietly folds two different books into one position.
- Store the total separately and let it be null. `Book 2` alone is common, and a total read
  off a series still being written goes stale.
- Keep the source text. When the parser refuses, that text is the only surviving record of
  what the page actually said.

```python
SERIES_POSITION = re.compile(
    r"(?:book|bk\.?|vol\.?|volume|part|no\.?|#)\s*"
    r"([0-9]+(?:\.[0-9]+)?)"
    r"(?:\s*(?:of|/)\s*([0-9]+))?",
    re.I,
)

def parse_series(text):
    """'Book 2 of 5' -> position '2', total 5. Refuse anything the rules miss."""
    if not text:
        return None
    match = SERIES_POSITION.search(text)
    if not match:
        return {"position": None, "total": None, "position_source": text.strip()}
    return {
        "position": match.group(1),                                  # str: 2.5 exists
        "total": int(match.group(2)) if match.group(2) else None,
        "position_source": text.strip(),
    }
```

A book can belong to more than one series, so series membership is its own row: `isbn13`,
`series_name`, `position`. As a column it forces a choice between the trilogy and the
omnibus that reprints it, decided by whichever the page printed first.

## Sweeping a catalogue without walking it in order

A book dataset is never one page, and the obvious way to build one is the shape a catalogue
site is best at spotting. Incrementing a numeric id, walking an ISBN range, stepping
through an author index: each produces a perfectly ordered, evenly spaced sequence that
repeatedly asks for rows no reader would request.

Crawl the site's own structure instead: a series page, an author page, a publisher's
edition list, a sitemap. Each gives URLs a reader could plausibly reach, in an order that
came from the site rather than a counter.
[Crawling from list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
keeps the association intact, and a
[sitemap crawl](how-to-scrape-a-sitemap-playwright.md) is the cheapest source of edition
URLs.

Then hold one identity for the whole sweep. The same `seed` gives the same GPU, canvas,
font and audio profile on every page, so a thousand edition pages read as one person rather
than a new machine per request.
[Rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md) has the pacing
that keeps a long run alive.

## Conclusion

Book metadata punishes the schema more than the selector. Take the edition as the row and
keep the work beside it, because collapsing the two throws away format, publisher and year.
Normalise every identifier to ISBN-13, verify the check digit, remember it can be an `X`,
and keep the printed form. Split contributors into roles at
extraction time, store two dates with a precision flag, and parse series position by a rule
that refuses what it cannot read. Then take the URLs from the site's own structure rather
than a counter, under one seeded identity.

## Short answers to the questions that lead here

**Should I store the ISBN-10 or the ISBN-13?** Both. Normalise to ISBN-13 and join on that,
and keep the exact string the page printed. The two forms name the same edition, so storing
only whichever the page showed gives two rows for one book.

**Why does my ISBN parser crash on some books?** Almost certainly the ISBN-10 check digit
`X`, which stands for the value ten. An `int()` on the last character raises, and a
`\d{10}` pattern skips the identifier entirely. Accept `X` in the last position only.

**One work or one edition per row?** One edition. The edition carries the format, the
publisher, the page count and the ISBN, and all four are lost when editions collapse into
the work. Keep a `work_key` to group them again.

**How do I keep translators out of the author field?** Read the typed schema.org fields
when JSON-LD is present, since `translator`, `editor` and `illustrator` sit apart from
`author`. From a rendered byline, parse the parenthesised role label and emit one row per
contributor.

**The publication year looks wrong for old books. Why?** The page shows the edition's date,
which is a reprint date. Store `edition_published` and `work_first_published` as two fields,
and fill the second only from a source describing the work.

**Can I just regex "Book 2 of 5" into a number?** Match it, but store the position as a
string and the total as a nullable integer, because novellas are numbered 1.5. Keep the
source text whenever the pattern does not match.

## Sources

- Playwright's [`locator`](https://playwright.dev/python/docs/api/class-page#page-locator),
  [`all_text_contents`](https://playwright.dev/python/docs/api/class-locator#locator-all-text-contents)
  and [`get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute),
  used as documented upstream, since the browser this library returns is a real Playwright
  `Browser`. Retrieved 2026-08-28.
- Playwright's [`page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto)
  and its `wait_until` states, for the load condition each edition page is read under.
  Retrieved 2026-08-28.
- The schema.org `Book` type and its `workExample`, `bookEdition`, `bookFormat`, `isbn`,
  `translator` and `illustrator` fields, which are the shape the first code block walks.
- The ISBN check digit definitions: modulo 11 over descending weights for ISBN-10, where
  the value ten is written `X`, and alternating 1 and 3 weights modulo 10 for ISBN-13.

**See also:** [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the block the edition record lives in,
[crawling list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md) for
walking a series page into its editions,
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for the date parsing this page hands off, and
[scraping into a database](how-to-scrape-into-a-database-playwright.md) for the ISBN-13
upsert.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this
keyed rows on the work and stored one publication year, so a paperback reprint overwrote the
hardback it shared a title with, and the format column was the last thing anyone thought to
check.*
