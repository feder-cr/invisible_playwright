---
title: "How to scrape newsletter archives with Playwright"
description: "Scrape newsletter archives with Playwright: page until the archive repeats or runs dry, read the hosted rendering instead of the sent email, and resolve tracking-redirect links once with a cache."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 129
---


# How to scrape newsletter archives with Playwright

To scrape newsletter archives with Playwright, page through the archive index until a
page comes back empty or its first item repeats the item from the page before, since
these archives almost never print a total to stop at, read each issue from the hosted
rendering the archive actually serves instead of assuming it matches what a subscriber
got by email, and treat the date shown on the archive as the date the issue was added to
that archive, not the date it was sent, until you confirm the two agree.

A newsletter archive looks like a simple paginated blog and behaves like one only on the
surface. The page you scrape is a hosted rendering built for public viewing, not the
email that went out: tracking pixels are gone, click-tracked links are often rewritten to
a tracking domain, and images sometimes point at a web-friendly copy instead of the
original. The index has no total count in the markup, so the loop has to notice when it
has run out rather than count down to a known number. Two smaller traps sit underneath:
the publish date can be a backfill date, and an A/B tested subject line collapses to a
single archived title, so the record you scrape is not always the record that was sent.

## The archive page is not the email

Before writing a parser, compare one archived issue against a copy of the same email in
an inbox, if you have one. The differences are consistent across most newsletter
platforms and they are not bugs in your extraction, they are what the hosted page
actually is.

Tracking pixels, the invisible 1x1 images that record opens, are stripped from the public
archive because there is no subscriber session to attribute an open to. Click-tracked
links often survive into the archive, still pointed at a redirect domain, because
rewriting every link at publish time is more work than leaving it in place. Images are the
least consistent field: some platforms serve the exact asset the email used, others swap
in a resized, web-hosted copy under a different filename, so matching images between the
sent and archived versions by URL alone fails more often than it works.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/archive/issue-142", wait_until="networkidle")

    record = {
        "url": page.url,
        "h1": page.locator("h1").first.inner_text(),
        "published_raw": page.locator("[data-archive-date], time").first.inner_text(),
        "body_html": page.locator("article, .campaign-body, .email-body").first.inner_html(),
        "links": [
            a.get_attribute("href")
            for a in page.locator("article a, .campaign-body a, .email-body a").all()
        ],
    }
```

Grab the raw date string here and defer parsing it. The next section is why: what the
markup calls a publish date is not always the date the newsletter was sent.

## Page until it repeats or runs dry, not to a total

The archive index almost always uses a fixed page size and almost never prints how many
pages exist. Some platforms respond to an out-of-range page number with an empty list;
others silently redirect back to page one and serve the same first item again. Stopping
on a hardcoded page count guesses wrong in both directions: too low on an archive that
kept growing since you last checked, too high on one that trims old issues.

The reliable stop condition is behavioral, not numeric: keep the page's first item URL
from the previous round, and stop the moment a page is empty or its first item matches
what you already saw. This is the same shape as the [numbered pagination](how-to-scrape-paginated-pages-playwright.md)
problem in general, with one twist: here the "end" signal is a repeat, not just an
empty result, because of the redirect-to-page-one behavior some archives fall back to.

```python
def walk_archive_index(page, base_url):
    seen_first_item = None
    page_number = 1
    issue_urls = []

    while True:
        page.goto(f"{base_url}?page={page_number}", wait_until="networkidle")
        rows = page.locator(".archive-list-item a").all()
        if not rows:
            break

        current_first = rows[0].get_attribute("href")
        if current_first == seen_first_item:
            break  # the site looped us back to a page we already read

        issue_urls.extend(row.get_attribute("href") for row in rows)
        seen_first_item = current_first
        page_number += 1

    return issue_urls
```

Run this once with a print statement on `page_number` before trusting it against a real
archive. The platforms that redirect rather than empty out are common enough that
skipping the repeat check silently turns a 40-page archive into an infinite loop capped
only by memory.

## Two fields the archive cannot fully promise: date and subject line

This is the caveat that breaks a send-cadence analysis quietly, because nothing about it
looks wrong at extraction time. Some archives stamp every issue with the date it was
imported or backfilled into the archive system, which can be days or weeks after the
actual send when an older run of issues gets added in bulk. The field name in the markup
is rarely honest about this: it says "Published" or shows a `<time>` element regardless
of which date it actually holds.

There is no reliable way to recover the true send date from the archive page alone. The
honest move is to record what you can verify and mark the rest: keep the archive date as
`archived_date`, and only populate `sent_date` when a second, distinct timestamp exists,
an RSS `pubDate` for the same item or an email header from an inbox copy. Do not silently
treat `archived_date` as `sent_date`; a downstream cadence report built on that assumption
will show gaps and bursts that never happened.

A related gap sits in the title. Plenty of sending platforms let a publisher test two or
three subject lines and send whichever wins to the bulk of the list. The archive keeps
exactly one record per issue, so the subject line stored there is whichever variant the
platform decided to archive, and it is not always the same string the H1 on the page
shows. Treat this as a real limit of the source data rather than quietly picking the H1 or
the meta title and moving on: the discrepancy is not something extraction can resolve,
because the archive itself only kept one answer. Record both fields when they differ and
flag the row, instead of merging them into a single "title" and losing the fact that a
split existed.

```python
from datetime import datetime

def build_dates(record, rss_pubdate=None):
    archived_date = datetime.fromisoformat(record["published_raw"])
    sent_date = rss_pubdate if rss_pubdate else None
    return {
        "archived_date": archived_date.isoformat(),
        "sent_date": sent_date.isoformat() if sent_date else None,
        "date_is_uncertain": sent_date is None,
    }

def title_fields(page):
    meta_subject = page.locator('meta[name="subject"]').get_attribute("content")
    h1_title = page.locator("h1").first.inner_text()
    return {
        "meta_subject": meta_subject,
        "h1_title": h1_title,
        "titles_disagree": bool(meta_subject) and meta_subject.strip() != h1_title.strip(),
    }
```

Carrying `date_is_uncertain` and `titles_disagree` forward into the dataset is cheap, and
it is the difference between a limitation you documented and one a later analyst
discovers the hard way. A dataset with those flags set on a few dozen rows out of a few
hundred is telling you the truth about the source; one that silently picked a date and a
title is telling you a story.

## Resolve tracking-redirect links once, and cache the target

Links inside the issue body are frequently wrapped in a tracking domain rather than
pointing straight at the real target, so `https://track.example.com/c/abc123` has to be
followed before you know where it actually goes. Following every link on every read is
wasteful and it hammers a tracking service that was built to log a human clicking once,
not a script re-resolving the same handful of links on every re-scrape of an archive.

Resolve once per unique tracking URL, store the result, and read from the cache on every
later run. The general technique of following a link without paying for a full page load
is the same one covered in [extracting links and building a crawl frontier](how-to-extract-links-crawl-frontier-playwright.md);
here the extra piece is the cache, since the whole point is to avoid repeating the
resolution.

```python
import json
from pathlib import Path

CACHE_PATH = Path("redirect_cache.json")

def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}

def resolve_redirect(context, url, cache):
    if url in cache:
        return cache[url]
    response = context.request.get(url, max_redirects=0)
    target = response.headers.get("location", url)
    cache[url] = target
    CACHE_PATH.write_text(json.dumps(cache))
    return target
```

`max_redirects=0` is the part that keeps this cheap: it stops at the first redirect
response instead of the request context silently following the whole chain, so one
request tells you the real target without a full navigation and without spending a page
load on a domain that only exists to log the click.

## RSS for the recent window, the archive page for full history

Most of these platforms publish an RSS or Atom feed alongside the archive, and the two
serve different jobs. The feed usually holds only the most recent handful of issues,
often twenty to fifty, but it returns in one request with clean, pre-parsed fields:
title, a `pubDate` that is genuinely the send date on most platforms, and a summary or
full body. The archive index, by contrast, holds everything ever published, but getting
it costs one page load per archive page plus one per issue.

Reach for the feed when the job is "what changed since I last checked", and reach for the
paginated archive when the job is "get the whole back catalog" or when an issue is old
enough to have fallen out of the feed's window. The mechanics of parsing the feed itself
are covered in [scraping RSS and Atom feeds](how-to-scrape-rss-atom-feeds-playwright.md);
the piece worth calling out here is that the feed's `pubDate` is often the best available
substitute for the send date the archive page does not reliably give you, which is why the
combined script below reads it with `feedparser` before falling back to the archive date.

## A row key that survives a second run

Re-running the scrape tomorrow should not re-download every issue from scratch, and it
should not produce duplicate rows for issues you already have. The natural key is the
issue's own archive URL, since it is stable across re-scrapes even when the visible title
or the reported date shift underneath it. Store rows keyed on that URL and skip a fetch
whenever the key is already present with a body hash that has not changed, which is the
same shape used for [incremental scraping of only new items](how-to-scrape-only-new-items-incremental-playwright.md)
in general.

```python
import hashlib

def row_key(record):
    return record["url"]

def body_hash(record):
    return hashlib.sha256(record["body_html"].encode("utf-8")).hexdigest()

def merge_row(existing_rows, record):
    key = row_key(record)
    new_hash = body_hash(record)
    old = existing_rows.get(key)
    if old and old["body_hash"] == new_hash:
        return existing_rows  # unchanged, nothing to update
    existing_rows[key] = {**record, "body_hash": new_hash}
    return existing_rows
```

Keying on the URL rather than on the title or the date is what makes this survive a
backfill: the date can move, the subject can be reported differently, but the archive URL
for a given issue does not change once it is published.

## Putting the crawl together

The pieces compose into one run: walk the index for the URL list, pull the recent feed
for a better date on newer issues, fetch each issue page, resolve its links through the
cache, and merge the result into a keyed store instead of a flat list.

```python
import feedparser

def run(base_url, feed_url, browser):
    page = browser.new_page()
    context = page.context
    cache = load_cache()

    issue_urls = walk_archive_index(page, base_url)
    recent_dates = {}
    try:
        feed = feedparser.parse(feed_url)
        recent_dates = {e.link: e.get("published") for e in feed.entries}
    except Exception:
        pass  # the feed is a nice-to-have, not a requirement

    rows = {}
    for url in issue_urls:
        page.goto(url, wait_until="networkidle")
        record = {
            "url": page.url,
            "h1": page.locator("h1").first.inner_text(),
            "published_raw": page.locator("[data-archive-date], time").first.inner_text(),
            "body_html": page.locator("article, .campaign-body").first.inner_html(),
        }
        record.update(build_dates(record, recent_dates.get(url)))
        record.update(title_fields(page))
        record["resolved_links"] = [
            resolve_redirect(context, href, cache)
            for href in page.locator("article a, .campaign-body a").evaluate_all(
                "els => els.map(e => e.getAttribute('href'))")
            if href
        ]
        rows = merge_row(rows, record)

    return rows
```

Nothing here is a special wrapper method: `new_page()`, `context.request` and the locators
all come straight from Playwright's own API. The only additions are the repeat-based stop
condition, the redirect cache, and the fields that keep an honest record of what the
source could confirm.

## Conclusion

A newsletter archive fails the same way in most implementations: no total page count, a
hosted rendering that quietly diverges from the sent email, a date field that sometimes
means "added here" instead of "sent then", and a title field that lost an A/B test result
the moment it was archived. Page by behavior instead of by count, keep the archive URL as
the row's stable key, resolve tracking-redirect links once and cache the answer, and
record the uncertainty around dates and titles instead of papering over it. Reach for the
feed when the job is the recent window and the archive when the job is the whole history.
The parsing is the easy part; knowing what the source cannot promise you is what keeps the
dataset honest on a second run.

## Short answers to the questions that lead here

**How do I know when to stop paginating a newsletter archive?** Stop when a page comes
back empty or its first item matches the first item from a page already read. Some
archives redirect an out-of-range page back to page one instead of returning nothing, so
the repeat check is not optional.

**Why does the archived issue not match the email a subscriber got?** The archive is a
hosted rendering built for public viewing. Tracking pixels are usually stripped, links
are often left as tracking-domain redirects, and images are sometimes swapped for a
separately hosted copy.

**Is the date on the archive page the date the newsletter was sent?** Not reliably. Some
platforms stamp the date an issue was added to the archive, which can differ from the
send date by days on a backfilled batch. Treat it as uncertain unless a second source, an
RSS `pubDate` or an inbox copy, confirms it.

**Should I follow every tracking-redirect link every time I scrape?** No. Resolve each
unique URL once, cache the result, and read from the cache on later runs. Re-resolving the
same links on every re-scrape wastes requests against a service built to log one human
click.

**What do I do when the subject line does not match the archived H1?** Record both and
flag the row instead of picking one. The mismatch usually comes from an A/B tested subject
line collapsing to a single archived record, which is a real limit of the source, not
something extraction can fix.

**Should I use the RSS feed or the archive page?** The feed for the recent window, since
it returns fast with a real send date on most platforms. The archive page for full history
or for any issue old enough to have fallen out of the feed's window.

## Sources

- Playwright's [`APIRequestContext.get`](https://playwright.dev/python/docs/api/class-apirequestcontext#api-request-context-get)
  with `max_redirects`, used above to resolve a tracking redirect without a full navigation.
- Playwright's [`Locator`](https://playwright.dev/python/docs/api/class-locator) and
  [`Page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto), used as
  documented upstream for reading the archive index and each issue page.
- `feedparser`, the Python library used above to read the RSS or Atom feed alongside the
  archive, where one exists.

**See also:** [scraping RSS and Atom feeds](how-to-scrape-rss-atom-feeds-playwright.md)
for the feed side of this page, [extracting links and building a crawl frontier](how-to-extract-links-crawl-frontier-playwright.md)
for resolving redirects in general, [incremental scraping of only new items](how-to-scrape-only-new-items-incremental-playwright.md)
for the keyed-store pattern used above, and [scraping paginated pages](how-to-scrape-paginated-pages-playwright.md)
for the numbered-pagination case this archive's index resembles but does not quite match.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A backfilled batch of
issues once got its archive date treated as the send date in a cadence report, and the
report showed a two-week silent stretch that had never actually happened.*
