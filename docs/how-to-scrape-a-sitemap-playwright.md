---
title: "How to scrape a sitemap.xml with Playwright"
description: "Scrape a sitemap.xml with Playwright: walk the index-to-urlset tree, decompress the .xml.gz leaves, and recrawl only the URLs whose lastmod changed."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 63
---


# How to scrape a sitemap.xml with Playwright

To scrape a `sitemap.xml` with Playwright, treat it as a tree rather than a flat list: read
the root element to tell a `<sitemapindex>` from a `<urlset>`, recurse into the child
sitemaps, decompress any `.xml.gz` leaves, and key each URL on its `<lastmod>` so later runs
recrawl only what changed. Fetch the whole tree through the browser context, because the file
usually sits behind the same protection as the pages.

Most sitemap tutorials fetch `sitemap.xml` with a plain HTTP client, parse the `<loc>`
tags, and hand you a flat list of URLs. That works right up until two things happen: the
sitemap is an index that points at other sitemaps instead of at pages, and the site serves
the file only to a browser it recognises. Then the plain client gets a challenge page where
the XML should be, and the flat parser gets zero URLs and no error.

This page treats the sitemap as what it actually is: a small tree you walk, with compressed
leaves you decompress, and a `<lastmod>` on every entry that lets you recrawl only what
changed instead of sweeping the whole catalogue every night. And it fetches that tree
through the same browser that will later fetch the pages, because the sitemap usually sits
behind the same door.

## Why the sitemap is the cheapest crawl frontier you have

You could discover a site's URLs by loading the homepage, extracting every link, following
each one, and repeating. That is a lot of page loads, a lot of proxy budget, and a lot of
signal to whatever is watching. The sitemap is the site handing you the same URL set in one
document, already deduplicated, often already partitioned by section, and stamped with a
last-modified date per entry.

The date is the part people ignore and it is the most valuable field in the file. If you
store the `<lastmod>` you saw for each URL on the last run, the next run has to visit only
the URLs whose `<lastmod>` moved. A catalogue of a hundred thousand pages that changes by a
few hundred a day becomes a few hundred page loads instead of a hundred thousand. That is
the difference between a crawl the site notices and one it does not.

## A sitemap is a tree, not a list

There are two document shapes at the sitemaps.org schema, and they are not interchangeable:

| Shape | Root element | Contains | Each `<loc>` points at | Role |
|---|---|---|---|---|
| Sitemap index | `<sitemapindex>` | `<sitemap>` entries | another sitemap file | table of contents |
| URL set | `<urlset>` | `<url>` entries (usually with `<lastmod>`) | a real page | the actual leaf |

A **sitemap index** is a table of contents whose `<loc>` values point at more sitemaps; a
**url set** is the leaf whose `<loc>` values point at real pages and usually carry a
`<lastmod>`.

The single most common way this code breaks is iterating a document as if it were a url set
when it is really an index. You get a list of `<loc>` values that look like URLs, you crawl
them, and every one is another XML file rather than a page. So the rule is: read the root
element first and branch on it. Never assume the shape.

Child sitemaps are frequently gzip-compressed and served as `.xml.gz`, because an
uncompressed url set for a large site is many megabytes. So the tree you are walking is
`index -> child sitemaps -> url sets`, and some of those children arrive as compressed bytes
you have to inflate before you can parse them.

## Fetch the sitemap through the browser, not around it

Here is the caveat that makes this a stealth problem and not just an XML problem. On a site
that protects its HTML, `sitemap.xml` very often sits behind the exact same protection.
Requested by a plain HTTP client with a default TLS handshake and no browser fingerprint, it
returns a challenge or a block, not the XML. Requested by the real browser you are already
driving, it returns the file.

So fetch it through the browser context.
[Playwright's request API](https://playwright.dev/python/docs/api/class-apirequestcontext) sends
the fetch through the same browser network stack, sharing its cookies, its proxy, and its
engine-level handshake, which is the whole point of using a patched engine rather than a
header generator. With `invisible_playwright` that browser is a real Firefox patched at the
C++ level and driven by stock Playwright, so switching to it is the launch line and nothing else:

```python
import gzip
import xml.etree.ElementTree as ET
from invisible_playwright import InvisiblePlaywright

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def fetch_xml(page, url):
    """Fetch through the browser context, inflate .xml.gz, return the parsed root."""
    resp = page.request.get(url)
    if not resp.ok:
        raise RuntimeError(f"{url} -> HTTP {resp.status}")
    raw = resp.body()
    # gzip magic is 0x1f 0x8b; a .xml.gz leaf arrives still compressed.
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)
```

`resp.body()` gives you the raw bytes rather than a decoded string, which matters because a
`.xml.gz` leaf is not text yet. Checking the
[two-byte gzip magic](https://datatracker.ietf.org/doc/html/rfc1952) and calling
`gzip.decompress` handles the compressed case; a file the server already decompressed for you
(via transport `Content-Encoding`) falls straight through, because its bytes start with `<`.

## Walk index to child to url set and decompress the .xml.gz

Now the walk itself. Read the root tag, strip the namespace, and branch. On an index, recurse
into each child `<loc>`. On a url set, record each page keyed by its `<loc>` with its
`<lastmod>` as the value, which deduplicates URLs and carries the recrawl signal in one dict:

```python
def collect(page, url, seen=None):
    if seen is None:
        seen = {}
    root = fetch_xml(page, url)
    tag = root.tag.rsplit("}", 1)[-1]   # drop the namespace, keep the local name

    if tag == "sitemapindex":
        for child in root.findall(f"{SM}sitemap"):
            loc = child.findtext(f"{SM}loc")
            if loc:
                collect(page, loc, seen)          # recurse into the child sitemap
    elif tag == "urlset":
        for entry in root.findall(f"{SM}url"):
            loc = entry.findtext(f"{SM}loc")
            if loc:
                seen[loc] = entry.findtext(f"{SM}lastmod")   # None if absent
    else:
        raise ValueError(f"unexpected sitemap root <{tag}> at {url}")

    return seen


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    frontier = collect(page, "https://example.com/sitemap.xml")
    print(len(frontier), "URLs discovered")
```

The `seed=42` is deliberate. The whole walk, and the page crawl that follows it, run under
one reproducible identity: same GPU, canvas, fonts and screen every time. That keeps the
index fetch, every child fetch, and every page visit looking like one returning visitor
rather than a fleet, and it means a failing run replays exactly instead of drawing a new
machine each time.

## Drive incremental recrawl from lastmod under one identity

The dict you just built is the frontier. Persist it, and the next run only has to diff its
`<lastmod>` values against the stored ones to know what to visit:

```python
import json
import pathlib

STATE = pathlib.Path("sitemap_lastmod.json")
previous = json.loads(STATE.read_text()) if STATE.exists() else {}

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    current = collect(page, "https://example.com/sitemap.xml")

    # A URL is worth revisiting if it is new, or its lastmod moved. When a
    # sitemap omits lastmod entirely, fall back to always revisiting it.
    to_crawl = [
        loc for loc, lastmod in current.items()
        if lastmod is None or previous.get(loc) != lastmod
    ]
    print(f"{len(to_crawl)} of {len(current)} URLs changed since last run")

    for loc in to_crawl:
        page.goto(loc)
        # ... extract the page under the same seeded identity ...

STATE.write_text(json.dumps(current))
```

That `None` branch is the honest part. `<lastmod>` is optional in the schema and plenty of
sites either omit it or stamp every entry with the same generated timestamp, in which case
the delta is worthless and you crawl everything. Treat a present, changing `<lastmod>` as a
gift and a missing one as "revisit and find out"; do not assume the field is trustworthy just
because it is there.

When the changed set is large, run it politely rather than all at once. Fanning the whole
frontier out in a burst is the velocity signal that undoes a good fingerprint, so pair this
with [request throttling](how-to-rate-limit-your-scraper-playwright.md) and a bounded number
of [parallel workers](how-to-scrape-multiple-pages-in-parallel-playwright.md) rather than a
tight loop over ten thousand `goto` calls.

## Conclusion

A sitemap is a tree with compressed leaves and a change-timestamp on every page, and scraping
it well means respecting all three facts: branch on the root element instead of assuming a
flat list, inflate the `.xml.gz` children, and key your frontier on `<lastmod>` so recrawls
touch only what moved. Fetch the whole tree through the browser, because the file sits behind
the same protection as the pages, and do it under one seeded identity so the index walk and
the page crawl read as a single consistent visitor. Get those right and the sitemap turns a
full nightly sweep into a few hundred deliberate visits.

## Short answers to the questions that lead here

**How do I parse a sitemap index versus a regular sitemap?** Read the root element first. A
`<sitemapindex>` root means the `<loc>` entries point at other sitemaps, so you recurse; a
`<urlset>` root means they point at pages, so you record them. Branching on the root is the
whole trick.

**How do I read a .xml.gz sitemap?** Fetch the raw bytes, check for the gzip magic
`0x1f 0x8b`, and call `gzip.decompress` before parsing. A file the server already
decompressed over the wire starts with `<` and needs no extra step, so testing the magic
bytes handles both.

**Why does fetching sitemap.xml return a challenge page?** Because the sitemap is protected
the same way the HTML is, and a plain HTTP client does not look like a browser. Fetch it
through the browser context so it goes over the same engine, proxy and handshake as your
page loads.

**How do I only crawl pages that changed?** Store the `<lastmod>` you saw per URL, and on the
next run visit only URLs that are new or whose `<lastmod>` moved. Fall back to a full visit
for any entry that has no `<lastmod>`, since the field is optional and not always honest.

**Should I use requests or Playwright for sitemaps?** Use a plain client if the file is
unprotected and static; use the browser when the same site blocks non-browser fetches, which
is common precisely on the sites worth crawling.

**How do I keep the crawl from looking like a bot?** Walk and crawl under one seeded identity,
throttle the request rate, and cap concurrency. Reproducibility is what lets the whole tree
walk read as one returning visitor rather than a burst.

## Sources

- The sitemaps.org protocol schema for the `<sitemapindex>`, `<urlset>`, `<loc>` and
  `<lastmod>` elements and the gzip transport convention.
- [RFC 1952](https://datatracker.ietf.org/doc/html/rfc1952), the gzip file format
  specification, which defines the `0x1f 0x8b` identification header a `.xml.gz` leaf starts
  with.
- Python's standard `gzip` and `xml.etree.ElementTree` modules.
- [Playwright's request API](https://playwright.dev/python/docs/api/class-apirequestcontext),
  which routes a fetch through the browser context rather than a separate client.
- This project's own crawl gates, where fetching a protected sitemap with a plain client
  returned a challenge and the browser context returned the file.

**See also:** [how to scrape paginated pages](how-to-scrape-paginated-pages-playwright.md)
for the sibling case where the frontier is discovered page by page, and
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md) for the
identity and exit choices that decide whether any of this reaches the XML at all.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The recrawl trick is only as
good as the site's lastmod, and the browser-context fetch is what gets you the file to trick
with in the first place.*
