---
title: "How to scrape job postings with Playwright"
description: "Scrape job postings with Playwright: drive the faceted search filters, wait for the results XHR instead of networkidle, and read the JobPosting JSON-LD."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 26
---


# How to scrape job postings with Playwright

To scrape job postings with Playwright, drive the board's faceted search controls instead
of walking URLs, wait for the specific results XHR rather than for `networkidle`, and read
each posting from its `JobPosting` JSON-LD instead of brittle card selectors. Run the whole
sweep under one stable browser identity so the board sees a returning visitor, and pace it
so the request volume does not give you away.

A job board is not a list of pages you can walk by number. It is a faceted search: a
keyword box, a location field, a stack of filter checkboxes, and a results column that
updates when you touch any of them. The update usually arrives by XHR or a "load more"
button, and the address bar does not change while it happens. If you write the scraper
around URLs and page numbers, you will collect the first screen of results and quietly
miss the rest.

This page is the shape that actually works: drive the facet controls like a person would,
wait for the specific network response that carries the results instead of guessing when
the page is done, and read each posting from the structured markup the site already ships
rather than from CSS classes that change every quarter.

## Why the URL does not move

The URL does not move because a job board's filters live in JavaScript state, not in the
address bar: the browser fetches matching results from a JSON endpoint by XHR and repaints
the list in place. Type a keyword, pick a location, tick "remote", and the results column
repaints while the address bar stays exactly the same. Some
boards put the query in the URL hash or query string, many do not, and none of them
reliably give you a clean paginated URL you can iterate.

That has two consequences for a scraper. First, you cannot enumerate results by editing a
number in a URL; you have to operate the controls. Second, the moment that matters is not
"the page finished loading" but "the results request came back", and those are different
events. A board can sit at `networkidle` with an empty column because the results XHR has
not been triggered yet, and it can also never reach `networkidle` at all because a board
keeps a telemetry socket open for the life of the session.

So the two load-bearing techniques are: interact with the facet controls, and wait on the
response, not the network going quiet. Both are plain Playwright once the browser is real.

## Drive the facet controls, then wait for the results response

Fill the search fields and click the filters the way the page expects, then wait for the
specific XHR that returns results.
[`page.expect_response()`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
lets you arm the wait before the click that triggers it, so there is no race between the
request firing and you starting to listen.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/jobs")

    # Fill the facet controls the way a person would.
    page.fill("input[name='keyword']", "data engineer")
    page.fill("input[name='location']", "Berlin")

    # Arm the wait BEFORE the action that triggers the request, so the
    # response cannot arrive before you are listening for it.
    with page.expect_response(
        lambda r: "/api/search" in r.url and r.status == 200
    ) as resp_info:
        page.click("button[type='submit']")

    results = resp_info.value.json()
    for card in results["jobs"]:
        print(card["title"], card.get("detailUrl"))
```

Match the response by a stable fragment of its URL path, not by an exact string. The
query parameters change on every facet combination; the endpoint path (`/api/search`,
`/graphql`, `/postings`) usually does not. If the board uses a "load more" button instead
of numbered pages, the same pattern drives the sweep: click the button, wait for the next
results response, repeat until the button disappears. The details of that loop, and how to
stop cleanly at the end, are in [scraping paginated pages](how-to-scrape-paginated-pages-playwright.md).

If you would rather read the results out of the network layer than out of the page, arm a
`page.on("response", ...)` handler once and let every facet change stream through it.
[Capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) covers
that shape, including the buffering you need so a response is not garbage-collected before
you read it.

## Prefer the JobPosting JSON-LD over card selectors

The results card is the wrong place to read a posting from. It is styled HTML, the class
names are generated, and the card deliberately omits fields the detail page has: the full
salary range, the exact location, the posting date, the employment type. Scrape the card
and you get a brittle title plus a link and not much else.

The detail page is different. Most job boards embed a `JobPosting` block of JSON-LD in the
page head, because that is what search engines read to build a rich result. It is a single
`<script type="application/ld+json">` tag with a documented, stable schema:
`title`, `datePosted`, `hiringOrganization`, `jobLocation`, `baseSalary`, `employmentType`.
Read that and you are reading the same structured record the site publishes to the rest of
the world, not a guess assembled from `<div>` classes.

```python
import json

def read_job_posting(page, url):
    page.goto(url)

    # The JobPosting block is the site's own machine-readable copy of the
    # posting. Read it instead of scraping styled card markup.
    blocks = page.locator("script[type='application/ld+json']").all_inner_texts()
    for raw in blocks:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # A page can hold several JSON-LD graphs; keep the JobPosting one.
        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get("@type") == "JobPosting":
                return {
                    "title": item.get("title"),
                    "datePosted": item.get("datePosted"),
                    "employmentType": item.get("employmentType"),
                    "salary": item.get("baseSalary"),
                    "location": item.get("jobLocation"),
                }
    return None
```

One honest caveat, because this is the site's markup and not yours: JSON-LD can be stale
or incomplete. A board that hides salary in its UI will often omit `baseSalary` from the
JSON-LD too, or leave it as an empty object, and a posting edited after publication can
carry a `datePosted` that no longer matches the visible text. The fingerprint makes the
session look like a real visitor; it cannot invent a field the page never rendered. Treat
a missing field as missing, fall back to the visible text only when you have to, and never
assume every posting fills every slot.

## Why a stable identity keeps the sweep answering

A stable identity keeps the sweep answering because a board that scores velocity is far
more likely to challenge a device it has never seen than a returning one. Scraping one
board thoroughly means running the same faceted search across many keywords and many
locations, usually on a schedule. That is, by construction, repeated near
identical traffic from one machine: the same endpoint, similar payloads, over and over.
The results endpoint is exactly the surface a board watches for that pattern, because a
human does not run four hundred location filters before breakfast.

Here is where a stock automation browser quietly costs you coverage. If every launch
produces a new random device (a different GPU string, a different canvas hash, a different
screen), then today's request for "data engineer in Berlin" comes from a machine the site
has never seen, even though you ran that exact query yesterday. A per-launch random
identity turns a returning visitor into a new stranger on every sweep, and a board that
scores velocity has every reason to start challenging the stranger that keeps rerunning
the same expensive search.

This is what a seed-stable fingerprint is for. Passing `seed=42` gives you the same GPU,
the same canvas hash, the same audio context, the same roughly four hundred fields on
every run, so the facet endpoint sees a consistent returning device rather than a fresh
one each morning. Combined with a real browser engine, whose TLS handshake and JavaScript
surface match the user agent it presents, the query that answered yesterday keeps
answering today instead of flipping into a challenge because the request arrived from a
device that did not exist an hour ago.

Consistency is not the same as invisibility. A board that watches request velocity will
still notice four hundred searches in ten minutes from one address, seed or no seed. Pace
the sweep: jitter the interval between queries, cap concurrency, and treat the throttle as
part of looking real rather than as politeness. The reasoning, and the measurement behind
it, are in [rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

## A full sweep, end to end

Putting the three techniques together: iterate the facets, wait on each results response,
collect the detail URLs, then read the `JobPosting` block from each detail page under one
stable identity.

```python
import json
from invisible_playwright import InvisiblePlaywright

QUERIES = ["data engineer", "backend developer"]
LOCATIONS = ["Berlin", "Munich", "Remote"]


def collect_detail_urls(page, keyword, location):
    page.fill("input[name='keyword']", keyword)
    page.fill("input[name='location']", location)
    with page.expect_response(
        lambda r: "/api/search" in r.url and r.status == 200
    ) as resp_info:
        page.click("button[type='submit']")
    payload = resp_info.value.json()
    return [c["detailUrl"] for c in payload.get("jobs", [])]


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/jobs")

    seen = set()
    postings = []
    for keyword in QUERIES:
        for location in LOCATIONS:
            for url in collect_detail_urls(page, keyword, location):
                if url in seen:
                    continue
                seen.add(url)
                record = read_job_posting(page, url)  # from the section above
                if record:
                    postings.append(record)

    print(f"collected {len(postings)} postings across "
          f"{len(QUERIES)} queries x {len(LOCATIONS)} locations")
```

`read_job_posting` is the function from the JSON-LD section. The `seen` set matters more
than it looks: the same posting shows up under several facet combinations, so
deduplicating on the detail URL is what keeps a two-query, three-location sweep from
reading the same page six times. Because the whole run happens under one seed, every one
of those detail-page visits presents the same device to the site, which is the point of
the previous section made concrete.

## Conclusion

Job boards break the URL-walking scraper on purpose, and the fix is not a cleverer
selector. Operate the facet controls, wait for the results response instead of a quiet
network, and read each posting from its `JobPosting` JSON-LD rather than from card markup
that is designed to change. Then run the whole sweep under one seed so the board sees a
returning visitor and not a new random machine every morning, and pace it so the volume
itself does not give you away. The structured markup gives you clean data; the stable real
browser keeps the endpoint willing to hand it over across a long, repetitive sweep.

The same listing-versus-record problem shows up in academic catalogs, where the row is
the section: [how to scrape course catalogs with Playwright](how-to-scrape-course-catalogs-playwright.md).

## Short answers to the questions that lead here

**Why does the URL not change when I filter a job board?** Because the filters live in
JavaScript state and the results arrive by XHR that repaints the list in place. You have
to drive the controls, not edit a URL.

**Should I wait for networkidle after applying a filter?** No. A board can idle with an
empty column, or never idle because of a telemetry socket. Wait for the specific results
response with `page.expect_response`, armed before the click that triggers it.

**Where do I get salary and posting date?** From the `JobPosting` JSON-LD block on the
detail page, which carries `baseSalary`, `datePosted` and `employmentType`. The results
card usually omits them.

**What if the JSON-LD has no salary?** Then the site did not render one. JSON-LD is the
site's own markup, so a missing field is genuinely missing; fall back to visible text only
when you must, and do not assume every posting fills every field.

**Why does the same query get blocked today when it worked yesterday?** Often because a
per-launch random fingerprint made today's request look like a brand new machine running a
repeated expensive search. A seed-stable identity presents a consistent returning device
across the sweep.

**Does a stable fingerprint mean I can skip rate limiting?** No. Consistency stops you
looking like a new stranger each run; it does not hide four hundred searches in ten
minutes from one address. Jitter the interval and cap concurrency as well.

## Sources

- The real wrapper API used above: [Quickstart](quickstart.md) and
  [Configuration](configuration.md), read from this project's own documentation.
- [`page.expect_response()`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response),
  read from Playwright's own documentation, is the primary-source method behind the
  wait-for-the-response pattern this page builds on.
- The `JobPosting` type is a public schema.org vocabulary; the fields named here are the
  ones a job board embeds for search engines to read.
- This project's own velocity experience, where a scheduled sweep flagged itself and the
  flag belonged to the request rate rather than the browser.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading results straight from the network layer,
[waiting for the page to load](how-to-wait-for-page-load-playwright.md) for why
networkidle is the wrong signal here, and [rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md)
for pacing the sweep.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The facet-XHR and JSON-LD
patterns are how the job-board sweeps in these notes were actually written.*
