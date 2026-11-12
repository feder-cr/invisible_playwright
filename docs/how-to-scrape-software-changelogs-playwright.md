---
title: "How to scrape software changelogs and release notes with Playwright"
description: "Scrape changelogs with Playwright: detect the page shape, parse versions into sortable tuples, read the change type from the heading above each bullet, and stop at the version you already have."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 114
---


# How to scrape software changelogs and release notes with Playwright

To scrape software changelogs and release notes with Playwright, decide which of the
three page shapes you are on before writing a walker, parse every version string into a
sortable tuple while keeping the original text, read the change type from the heading a
bullet sits under rather than from the bullet, flag yanked releases instead of dropping
them, and stop the walk at the newest version you already have.

A changelog looks like the easiest page on the internet to parse. It is a list of
versions, each with a date and some bullets, usually plain HTML with no lazy loading and
no consent banner. Getting the text out is not the hard part.

The hard part is that nearly every field means something other than what it looks like.
The version is a string that sorts wrong. The date is usually not the release date. The
type of a change is not stored on the change. And a release still sitting in the list may
have been withdrawn. This page is the parser that survives those four facts, and the walk
that keeps a daily run cheap.

## Know which of the three shapes you are on

Changelogs come in three shapes, they need three different walkers, and the detection
costs one page load.

The first is a single long page: every version is a heading with an anchor id, and one
request gives you the whole history. The second is one page per version behind an index,
so the history costs one request per release. The third is a feed, RSS or Atom or a JSON
releases endpoint, which is the cheapest source and the most limited, because a feed is
nearly always capped at the most recent entries.
[RSS and Atom feeds](how-to-scrape-rss-atom-feeds-playwright.md) covers that third shape.

```python
import re
from invisible_playwright import InvisiblePlaywright

# detect_shape leans on parse_version, defined in the "reading versions"
# section below: when composing the page's blocks into one script, that
# block goes above this one.

def detect_shape(page):
    """Report every shape the page offers and let the caller choose."""
    feed = page.locator('link[rel="alternate"][type*="xml"]').first
    feed_href = feed.get_attribute("href") if feed.count() else None

    headings = [t for t in page.locator("h1, h2, h3").all_inner_texts()
                if parse_version(t)]

    hrefs = page.locator("a[href]").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))")
    # An in-page anchor like "#v1-10-0" is the single-page shape wearing a link.
    links = [h for h in hrefs
             if h and not h.startswith("#") and parse_version(h)]

    return {"feed": feed_href,                  # capped: newest N entries only
            "single_page": len(headings) >= 5,  # versions are headings here
            "per_version": len(links) >= 5}     # versions are separate URLs

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/changelog", wait_until="domcontentloaded")
    shape = detect_shape(page)
```

Filtering out hrefs that start with `#` is what stops a single long page from reporting
itself as the per-version shape. Without it, a page with sixty anchored headings looks
like an index of sixty release pages, and the walker makes sixty pointless requests to
fragments of a document it already has.

## Version strings do not sort as text

`1.10.0` sorts before `1.9.0` as text and after it as a version, because text comparison
hits the `1` against the `9` in the second component and stops there. A scraper that
stores versions as plain strings will eventually report the wrong latest release, and it
will do it silently.

Parse into a tuple of integers and keep the original string alongside it. The tuple is
what you sort; the string is what you display and match back to the anchor. Keep a third
value, a join key with any leading `v` stripped, because an index link says `v1.10.0`
while the heading says `1.10.0`, and set membership between those two fails without a
warning.

```python
# Text order is not version order. The whole problem in one line:
#   sorted(["1.9.0", "1.10.0", "1.2.0"]) -> ['1.10.0', '1.2.0', '1.9.0']

VERSION_RE = re.compile(r"""
    v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?
    (?:-(?P<pre>[0-9A-Za-z][0-9A-Za-z.-]*))?
    (?:\+(?P<build>[0-9A-Za-z][0-9A-Za-z.-]*))?
""", re.VERBOSE)

DOTTED_DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$|^\d{4}\.\d{1,2}\.\d{1,2}$")

def parse_version(text):
    """Return a version dict, or None when the text carries no version."""
    for m in VERSION_RE.finditer(text or ""):
        if DOTTED_DATE.match(m.group(0)):
            continue          # 11.02.2026 is a date, not version 11.2.2026
        return {
            "raw": m.group(0),                     # exactly as the page printed it
            "id": m.group(0).lstrip("vV"),         # join key: v1.10.0 == 1.10.0
            "release": (int(m["major"]), int(m["minor"]), int(m["patch"] or 0)),
            "pre": m["pre"],
            "build": m["build"],
        }
    return None
```

The date guard earns its place. Headings carry both values, and a dot-separated date
matches a three-component version pattern perfectly. Without it, a heading reading
`11.02.2026` enters the dataset as version 11.2.2026 and stays the newest release
forever.

## Prereleases and build metadata break the comparison

Two suffixes turn a working sort into a broken one, in opposite directions. A prerelease
ranks below the release it belongs to, so `1.0.0-rc.1` comes before `1.0.0`, which is the
reverse of what a tuple comparison gives you when the numbers are identical. Build
metadata is ignored entirely for ordering, so `1.0.0+build.5` and `1.0.0+build.6` have
equal precedence and neither outranks plain `1.0.0`.

A naive parser handles these two ways, both wrong: it raises on the suffix and loses the
entry, or it strips the suffix and treats a release candidate as the finished release.

```python
def sort_key(v):
    """Release tuple first, then prereleases below their own release."""
    if v["pre"] is None:
        return (v["release"], (1,))            # 1.0.0 outranks every 1.0.0-anything
    parts = []
    for ident in v["pre"].split("."):
        if ident.isdigit():
            parts.append((0, int(ident), ""))  # numeric identifiers rank lowest
        else:
            parts.append((1, 0, ident))
    return (v["release"], (0, parts))
    # Build metadata is deliberately absent: it carries no precedence at all.

newest_first = sorted(versions, key=sort_key, reverse=True)
```

That leaves one trap. The sort key drops build metadata, so two different releases can
produce identical keys: safe for ordering, unsafe as a dictionary key or a dedupe key.
Order on `sort_key` and dedupe on `id`, or one build silently overwrites its twin.

## The date on the page is the note's, not the artifact's

The visible date is the publish date of the note, not the release date of the artifact,
and the two can differ by days. Both are real and answer different questions, so store
the one you actually have and name the column for what it is.

Read the machine attribute, not the rendered text. Human date text is often relative
(`2 days ago`), computed against the reader's clock at render time, so it means something
different on every run, while a `datetime` attribute is absolute. The relative-date
problem in full is
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

```python
DATE_JS = """
(root) => Array.from(root.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(h => {
  const near = h.querySelector("time") ||
    (h.nextElementSibling && h.nextElementSibling.querySelector("time"));
  return {text: h.textContent.trim(),
          published: near ? near.getAttribute("datetime") : null,
          shown: near ? near.textContent.trim() : null};
})
"""

def version_rows(page, root="main"):
    rows = []
    for h in page.locator(root).first.evaluate(DATE_JS):
        found = parse_version(h["text"])
        if not found:
            continue
        rows.append({
            "version": found["id"],
            "note_published_at": h["published"],   # absolute, from the attribute
            "note_date_shown": h["shown"],         # "2 days ago", moves every run
            "artifact_released_at": None,          # not on this page. Ask the index.
        })
    return rows
```

The `nextElementSibling` hop covers the common layout where the heading holds the version
and the paragraph under it holds the date. It is bounded to one sibling on purpose. Widen
it and a version with no date of its own inherits the date of the release below it, which
is worse than a null.

## The change type is a heading, not a field

Entries are grouped by type under headings like Added, Fixed, Deprecated and Breaking
changes, so the type of a bullet is not written on the bullet. It is the nearest heading
above it, and it holds until the next heading replaces it or a version heading clears it.

That makes extraction a stateful walk in document order, not a set of selectors. Take one
flat stream of headings and list items, carry the current version and group as you go,
and attach both to every bullet. The selector `li:not(li li)` drops nested sub-bullets so
a two-level list does not emit the child twice.

```python
GROUPS = [
    ("breaking", "breaking"), ("incompatible", "breaking"),
    ("added", "added"), ("new", "added"), ("feature", "added"),
    ("fixed", "fixed"), ("bug", "fixed"),
    ("removed", "removed"), ("deprecat", "deprecated"),
    ("security", "security"),
    ("changed", "changed"), ("improve", "changed"),
]

def classify(heading):
    if not heading:
        return "unknown"                  # bullets sitting above any group heading
    text = heading.strip().lower()
    for needle, label in GROUPS:          # first match wins, so breaking goes first
        if needle in text:
            return label
    return "other"

STREAM_JS = """
(root) => Array.from(
  root.querySelectorAll("h1, h2, h3, h4, h5, h6, li:not(li li)")
).map(n => ({tag: n.tagName, text: n.textContent.trim()}))
"""

def entries(page, root="main"):
    stream = page.locator(root).first.evaluate(STREAM_JS)
    version, group, rows = None, None, []
    for node in stream:
        if node["tag"] != "LI":
            found = parse_version(node["text"])
            if found:
                version, group = found, None   # a version heading opens a release
            else:
                group = node["text"]           # any other heading is a group label
            continue
        if version:
            rows.append({"version": version["id"],
                         "type": classify(group),
                         "text": node["text"]})
    return rows
```

The walk keys off whether a heading parses as a version, never off its tag, because
plenty of projects use `h3` for versions and `h4` for groups. `GROUPS` is an ordered list
and not a dict, because "Breaking changes" would otherwise be a coin toss against any
rule matching the word change.

## Yanked releases stay listed and must not win

A withdrawn release is not removed from the changelog. It stays in the list with a
marker: the word yanked or withdrawn in the heading, a strikethrough element, a class
name, or a warning paragraph under the version. The entry has to survive the parse,
because somebody may be running that version and needs to know it was pulled, so the
handling is a boolean column and not a filter.

What changes is the definition of latest. The current release is the highest version that
is neither a prerelease nor yanked, so the flag has to be read before the sort result is
used for anything. A dashboard that reports the top row is wrong exactly on the days it
matters most.

The honest limit sits here. Some projects yank by quietly editing the note, or by pulling
the artifact while leaving the note untouched, and neither leaves a mark on the page. The
changelog is authoritative for what the maintainers said, never for what is installable
right now.

## Walk backwards and stop at the version you already have

Once you hold a version, everything below it in the list is already yours, so a daily run
only needs what came after it. Record the newest version seen, walk newest-first, and stop
when the page has fallen behind the mark. The general form, including items edited after
you saved them, is
[incremental scraping](how-to-scrape-only-new-items-incremental-playwright.md).

Stopping at the first known version is too eager for a project with maintenance branches,
because 1.9.1 can be published after 1.10.0 and sit below it in the list. Stop after a
short run of consecutive known versions instead. Three crosses a backport and costs
nothing.

```python
import random
from urllib.parse import urljoin

def walk_index(page, index_url, seen, stop_after=3, seed=42):
    """Newest-first walk of a one-page-per-version changelog."""
    rng = random.Random(seed)
    page.goto(index_url, wait_until="domcontentloaded")
    hrefs = page.locator("a[href]").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))")

    fresh, known_streak = [], 0
    for href in hrefs:                        # index order: newest first
        found = parse_version(href or "")
        if not found or href.startswith("#"):
            continue
        if found["id"] in seen:
            known_streak += 1
            if known_streak >= stop_after:
                break                         # the rest is already stored
            continue
        known_streak = 0
        page.goto(urljoin(index_url, href), wait_until="domcontentloaded")
        fresh.extend(entries(page))
        page.wait_for_timeout(rng.randint(700, 2400))
    return fresh
```

What the stop is worth depends on the shape, which is why the shape check comes first. On
the per-version shape it saves a page load per release: the walker above never fetches a
page for a version it already stored. On a single long page it saves nothing in requests,
since one load brought the whole history. On a feed it saves everything and adds a
ceiling, because a capped feed cannot backfill. Feed for the daily run, page for the
first import. The crawl side is
[list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md), and `seen`
is one query against [a SQLite table](how-to-scrape-into-a-database-playwright.md) keyed
on the version id.

## When an empty changelog is not an empty changelog

Zero versions parsed is a fetch failure until proven otherwise, and the parser cannot
tell the difference on its own. A challenge page, an edge error page and a redirected
login are all valid HTML documents with headings in them. Run `detect_shape()` against
one and it answers cleanly: no feed, no version headings, no version links. The walk then
reports an empty release history, writes nothing, exits zero, and the job goes green while
the dataset quietly stops updating.

Put a floor under it. If a page that had thirty version headings yesterday has zero today,
raise instead of writing, and treat the run as failed. Docs paths often sit behind the
same edge as the product itself, so they inherit its protection even though the content is
public, and a first-request block looks exactly like a project that never shipped:
[what a block looks like mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

The request pattern is the other half. A per-version walk is a burst of loads under one
path prefix in strict version order, which is not what a reader does, and the incremental
stop keeps that burst down to the releases that are new. The `browser` is a real
Playwright `Browser`, so pacing is `wait_for_timeout` between visits, drawn from the same
seed as the identity.

## Conclusion

Changelog scraping is a parsing problem in the costume of a scraping problem. Detect the
shape first: it decides the walker and what an incremental run is worth. Parse versions
into tuples and keep the original string, because text order and version order disagree
the moment a minor number reaches ten. Put the prerelease and build rules in one sort key,
and dedupe on a value you do not order on. Name the date column for the publish event it
records. Carry the group heading onto each bullet, because the type of a change is context
and not content. Flag yanked releases rather than deleting them, and never let one answer
what is current. Then stop at the version you already own, and refuse to believe a page
that suddenly has no versions on it.

## Short answers to the questions that lead here

**Why does my scraper think 1.9.0 is newer than 1.10.0?** Because it compares strings, and
text comparison hits the `1` against the `9` in the second component and stops. Parse into
a tuple of integers, sort on that, keep the original string for display.

**How do I handle 1.0.0-rc.1 and 1.0.0+build.5?** A prerelease ranks below the release it
belongs to, and build metadata carries no precedence at all. Build both rules into the
sort key, then dedupe on the id instead, since two different builds produce identical
keys.

**The date I scraped is a day off from the release. Which is right?** Both. The page shows
when the note was published, not when the artifact shipped. Store it as the note's publish
date and take the artifact date from the package index.

**How do I know whether an entry is a fix or a breaking change?** From the heading it sits
under, not from the bullet. Walk headings and list items in document order, carry the
current group as state, and clear it at each version heading.

**Should I skip yanked releases?** Keep them and flag them. Somebody may be running one
and needs to know it was pulled. What changes is the definition of latest: the highest
version that is neither yanked nor a prerelease.

**How do I fetch only new releases each day?** Walk newest-first and stop after a few
consecutive versions you already have. Stopping at the very first known one misses a
backport published after a newer minor.

## Sources

- Playwright's [`Locator.evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all)
  and [`Locator.evaluate`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate),
  used to pull one flat stream of nodes out of the document in a single round trip.
  Retrieved 2026-08-28.
- Playwright's [`Locator.get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute),
  which reads the `datetime` attribute of a `time` element without requiring it to be
  visible. Retrieved 2026-08-28.
- The Semantic Versioning 2.0.0 specification, clauses 9 to 11: prerelease precedence, the
  numeric versus alphanumeric identifier rule, and the exclusion of build metadata from
  ordering. `sort_key` is a transcription of them.

**See also:** [incremental scraping](how-to-scrape-only-new-items-incremental-playwright.md)
for the high-water mark in general form,
[RSS and Atom feeds](how-to-scrape-rss-atom-feeds-playwright.md) for the third changelog
shape, [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for turning a rendered date into a stable timestamp, and
[scraping into a SQLite database](how-to-scrape-into-a-database-playwright.md) for a table
keyed on the version id.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The lexical version sort is
not a hypothetical here: a version check that compared release strings directly read 1.10.0
as older than 1.9.0, which is why the tuple parse is the first thing on this page.*
