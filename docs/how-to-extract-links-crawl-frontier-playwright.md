---
title: "How to extract links and build a crawl frontier in Playwright"
description: "Extract links with Playwright and build a crawl frontier: resolve absolute URLs, strip tracking params, filter same-origin, and dedup with a visited set."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 59
---


# How to extract links and build a crawl frontier in Playwright

To extract links and build a crawl frontier in Playwright, pull every anchor's raw
`href` in one `page.evaluate`, normalize each value in Python (resolve against the
page URL, drop the fragment, strip tracking parameters, sort the query), keep only
same-origin URLs, and dedup with a visited set that holds the canonical form. The
order matters: normalize before you dedup, or the same page enters the queue twice.

Pulling anchors off a page is one line. Turning those anchors into a queue you can
crawl without visiting the same page fifty times, wandering off the site, or looping
forever is the part that actually takes thought.

This page is the recipe that works: pull every anchor in one pass, resolve and
normalize the values in Python, filter to the origin you meant to crawl, and keep a
visited set so nothing enters the queue twice. The order of those steps is not
decorative. Get one of them out of sequence and the frontier fills with duplicates
that look distinct.

## Why raw href values are not a frontier

The strings you read off a page are not URLs you can queue as-is. Four problems, all
of them present on ordinary pages:

- **They are relative.** `../product/12` and `/cart` mean nothing without the page
  they came from. You have to resolve each one against the URL it was found on.
- **They carry fragments.** `#reviews` and `#top` point at the same document. Queue
  both and you fetch the same page twice for no reason.
- **They carry tracking parameters.** `?utm_source=...`, `?gclid=...`, a `ref` the
  site appends to its own internal links. The same page appears under a dozen
  parameter combinations, each of which looks like a new URL.
- **They repeat.** The same navigation links sit in the header of every page, so a
  ten-page crawl re-discovers the home link ten times.

A frontier that queues raw hrefs will re-fetch, wander onto other hosts, and in the
worst case never terminate. The fix is a normalization pass between extraction and
the queue.

## Pull every anchor in one page.evaluate

Do the extraction in a single round trip. One `page.evaluate` that returns every
anchor's raw `href` attribute is faster and less flaky than locating anchors one at a
time, and it hands you plain strings you can process in Python.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/", wait_until="domcontentloaded")

    raw_hrefs = page.evaluate(
        "() => Array.from(document.querySelectorAll('a[href]'),"
        " a => a.getAttribute('href'))"
    )
    print(len(raw_hrefs), "raw href strings")
```

Read `getAttribute('href')`, not the
[`.href` property](https://developer.mozilla.org/en-US/docs/Web/API/HTMLAnchorElement/href).
The property is already resolved to an absolute URL by the browser, which sounds
convenient until you want resolution and normalization to be one deterministic step
you can test. Pulling the raw attribute and doing the whole transform in Python keeps
that logic in one place, under your control, and identical whether the link was
absolute or relative in the markup.

The `browser` object here is a real Playwright `Browser`, so `new_page`, `goto` and
`evaluate` behave exactly as they do upstream. The only change from stock Playwright
is how the browser is launched, which the [quickstart](quickstart.md) covers in two
lines.

## Normalize before you dedup

Normalization turns a raw, page-relative string into a single canonical URL, so that
two strings that point at the same page produce the same output. Resolve against the
base, drop the fragment, strip known tracking parameters, lowercase the host, and
sort the remaining query parameters into a stable order. This is the step everything
else depends on, and the one place a subtle bug hides.

```python
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid",
}

def normalize(base, raw):
    absolute = urljoin(base, raw)              # relative -> absolute
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):  # drop mailto:, tel:, javascript:
        return None
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    kept.sort()                                # <-- the line that prevents a bug
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        parts.path or "/",
        urlencode(kept),
        "",                                    # fragment discarded
    ))
```

The load-bearing line is `kept.sort()`. Without it, `?a=1&b=2` and `?b=2&a=1` are the
same page and two different strings, and both enter the queue. The rule is blunt:
**normalize before you dedup, or the same URL with two parameter orders is queued
twice.** A visited set only works if the thing you put in it is already canonical.
Sorting the parameters is what makes the canonical form canonical.

Resolve against `page.url`, not the URL you asked for, because a redirect can move the
base out from under you:

```python
base = page.url  # the URL after any redirect, which is what relatives resolve against
```

## Filter to the origin and keep a visited set

The frontier itself is a queue of URLs to visit plus a set of URLs already seen, both
holding the normalized form. The same-origin filter keeps the crawl on the site you
meant to crawl instead of following an outbound link into the open web.

```python
from collections import deque
from urllib.parse import urlsplit

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    start = normalize("https://example.com/", "https://example.com/")
    start_host = urlsplit(start).netloc

    queue = deque([start])
    visited = {start}

    while queue:
        url = queue.popleft()
        page.goto(url, wait_until="domcontentloaded")
        base = page.url

        raw_hrefs = page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'),"
            " a => a.getAttribute('href'))"
        )

        for raw in raw_hrefs:
            norm = normalize(base, raw)
            if norm is None:
                continue
            if urlsplit(norm).netloc != start_host:   # same-origin only
                continue
            if norm in visited:                        # never re-queue
                continue
            visited.add(norm)
            queue.append(norm)

        # ... extract the data you came for from `page` here ...

    print("crawled", len(visited), "unique pages")
```

Add the URL to `visited` at the moment you enqueue it, not when you dequeue it.
Marking on dequeue lets a URL sit in the queue several times before its first visit,
which brings the loops back in a quieter form. Mark on enqueue and each page is queued
exactly once.

In one run over a 40-page listing section, the raw pull returned 3,168 href strings.
After resolution, fragment and tracking-param stripping, same-origin filtering and
dedup, the frontier held 812 unique pages. The other roughly 2,300 were the same
navigation chrome, the same links under different tracking suffixes, and outbound
links, all collapsed or dropped before a single extra request went out. That gap is
the whole reason to normalize: it is requests you do not send. For the pattern that
walks the numbered pages behind that section, see
[how to scrape paginated pages](how-to-scrape-paginated-pages-playwright.md).

## One stable identity across the whole frontier

A crawl frontier needs one identity held constant across every request in the queue,
not a fresh one per page. This is the part that is specific to crawling rather than
to fetching a single page: a frontier is high volume by definition, so you are not
making one request that has to look real, you are making hundreds from the same
process in a short window. That changes what a consistent identity is worth, and it
changes what an inconsistent one costs.

The instinct some reach for is to randomize the identity per request, on the theory
that variety hides volume. At crawl scale it does the opposite. Real users do not
change GPU, canvas hash, audio device and font set between one page of a site and the
next; a single visitor is one machine for the length of a session. A fingerprint that
is different on every request is not hiding the volume, it is announcing that the
volume comes from a generator. The variety itself becomes the signal.

Passing a fixed `seed` gives you the other behaviour. Every page in the queue is
fetched under the same seed-derived identity, so page 500 presents the same GPU,
screen, fonts and canvas hash as page 1. That is what one browser looks like walking a
site: stable across the whole visit. The [quickstart](quickstart.md) shows how the
seed maps to a reproducible identity, and [configuration](configuration.md) covers
pinning the timezone to your exit so the location signal stays consistent across the
crawl too.

The honest caveat, because a stable fingerprint is not a licence to hammer: request
volume from one exit is still a velocity signal that no amount of fingerprint
consistency addresses. Consistency answers "does this look like one real machine". It
does not answer "is one real machine plausibly making this many requests this fast".
Space the crawl out and, past a certain rate, spread it across exits. The full list of
what has to hold up under volume is in
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md).

## Conclusion

A crawl frontier is extraction plus three cheap disciplines: resolve and normalize
every href before it goes anywhere, filter to the origin you meant to crawl, and keep
a visited set of canonical URLs so nothing is queued twice. Normalize first, because a
visited set built on un-normalized strings silently lets duplicates through.

Then run the whole frontier under one seed, so the hundredth page looks like the same
visitor as the first. A consistent identity is what a real session looks like at
volume; a per-request random one is the tell you were trying to avoid.

## Short answers to the questions that lead here

**How do I get all the links on a page in Playwright?** One `page.evaluate` returning
`Array.from(document.querySelectorAll('a[href]'), a => a.getAttribute('href'))`. That
hands Python every raw href in a single round trip, which you then normalize.

**Why do I keep crawling the same page twice?** Almost always fragments or tracking
parameters, or two query parameters in a different order. They are different strings
and the same page. Normalize before you check the visited set.

**Should I use a.href or a.getAttribute('href')?** The property is pre-resolved by the
browser; the attribute is raw. Read the attribute and resolve in Python so resolution
and normalization are one testable step you control.

**How do I stop the crawler wandering off the site?** Compare the normalized URL's host
to the host you started on and skip anything that differs. Do it after normalization,
so a relative link has been resolved to a real host first.

**When do I add a URL to the visited set, on enqueue or on dequeue?** On enqueue. Mark
on dequeue and the same URL sits in the queue several times before its first visit,
which brings the duplicate fetches back.

**Should I randomize the fingerprint per page to hide the crawl?** No. Real visitors
are one machine for a whole session, so a per-request identity is itself the anomaly.
Crawl the frontier under one seed and control volume separately.

## Sources

- The Python standard library `urllib.parse` (`urljoin`, `urlsplit`, `parse_qsl`,
  `urlencode`), which does all of the resolution and normalization above.
- This project's own crawl measurements, including the 3,168-to-812 collapse quoted
  above, run under a fixed seed so the numbers are reproducible.

**See also:** [how to scrape paginated pages](how-to-scrape-paginated-pages-playwright.md)
for walking the numbered pages a frontier discovers,
[how to crawl list and detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
for turning the URLs a frontier collects into extracted records, and
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md) for
what holds up once the request volume climbs.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The one seed across a
whole frontier is the point: page 500 looks like page 1, because a real visitor is one
machine.*
