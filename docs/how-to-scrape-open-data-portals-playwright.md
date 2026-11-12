---
title: "How to scrape open data portals with Playwright"
description: "Scrape open data portals with Playwright: name the catalogue product from its JSON response shape, page its documented listing API, and open a browser only where the search UI hides the endpoint."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 118
---


# How to scrape open data portals with Playwright

**To scrape an open data portal with Playwright, identify which catalogue product it runs
before writing a single selector: probe `/api/3/action/package_search`,
`/api/explore/v2.1/catalog/datasets` and `/api/feed/dcat-us/1.1.json` on the portal host,
ask the central Socrata discovery service separately, name the product from the keys in
the response, then page that product's documented listing API.** The browser is for
portals that hide the catalogue behind a JavaScript search UI, and for very little else.

There are a great many open data portals and not many pieces of open data software. Most
of what looks like a bespoke municipal catalogue is one of five products with a local logo
in front of it, and the dataset listing underneath is a documented JSON endpoint at a path
the vendor fixed years ago.

So the identification is the whole job. Get it right and the scrape collapses into an API
client. Get it wrong and you spend a week writing selectors against a themed front end
that had a JSON endpoint two paths away.

## Identify the product from the response shape, not the branding

A portal's HTML tells you what a designer picked. Its JSON tells you what it runs. Each
product answers a fixed path with a fixed set of top-level keys, confirmed by live call on
2026-08-28:

- CKAN `package_search`: `success`, `result` and `help` at the top level, with `count`,
  `sort`, `facets`, `results` and `search_facets` inside `result`.
- Socrata Discovery: `results`, `resultSetSize`, `timings` and `warnings`.
- ArcGIS Hub DCAT: `@context`, `@type`, `conformsTo`, `describedBy` and `dataset`, where
  `@type` carries the value `dcat:Catalog`.
- Huwise, the product formerly sold as OpenDataSoft: `total_count` and `results`.

Run the probe from a browser context, not a bare HTTP client. Some portals answer an
unadorned client differently from a real session, which is the most confusing thing about
this exercise. `page.request` sends from the browser context, so it carries the same proxy
exit, cookie jar and TLS behaviour as a page load.

```python
from invisible_playwright import InvisiblePlaywright

PROBES = [
    ("ckan",       "/api/3/action/package_search?rows=1",        {"success", "result", "help"}),
    ("huwise",     "/api/explore/v2.1/catalog/datasets?limit=1", {"total_count", "results"}),
    ("arcgis_hub", "/api/feed/dcat-us/1.1.json",                 {"@context", "@type", "dataset"}),
]

def identify(page, base):
    for name, path, expected in PROBES:
        response = page.request.get(base + path)
        if not response.ok:
            continue
        try:
            body = response.json()
        except ValueError:
            continue
        if isinstance(body, dict) and expected.issubset(body.keys()):
            return name, body
    return None, None

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    print(identify(page, "https://portal.example.gov")[0])
```

Socrata is the asymmetric one. It is not a path on the portal host: the discovery service
lives at `https://api.us.socrata.com/api/catalog/v1` and is asked centrally, so a portal
failing all three host probes is still worth checking there. DKAN also exists here, but
its metastore path and pagination parameters were not confirmed, so this page prints no
path for it rather than a guess dressed up as a fact.

## The listing endpoint for each product

Everything in this table was checked on 2026-08-28. The pagination column is the part
worth reading twice, because no two of these products agree.

| Product | Dataset listing endpoint | Pagination |
|---|---|---|
| CKAN | `/api/3/action/package_search` | `rows` (default 10, max 1000) plus `start` |
| CKAN DCAT | `/catalog.jsonld`, also `.xml`, `.ttl`, `.rdf` | `page`, fixed at 100 per page |
| Socrata Discovery | `https://api.us.socrata.com/api/catalog/v1` | `limit` plus `offset`, hard cap at offset 10000 |
| Socrata SODA3 | `/api/v3/views/{uid}/query.json` | JSON body `page: {pageNumber, pageSize}` |
| Huwise (ex-OpenDataSoft) | `/api/explore/v2.1/catalog/datasets` | `limit` max 100 plus `offset`, sum capped at 10000 |
| ArcGIS Hub | `/api/feed/dcat-us/1.1.json` | None. One document. |
| data.gov | `https://api.gsa.gov/technology/datagov/v4/search` | `per_page`, with `q`, `sort`, `org_slug`, `keyword` |

One caveat on the CKAN row. `/catalog.jsonld` comes from `ckanext-dcat`, a separate
plugin, so an instance can answer `package_search` perfectly and return 404 on the DCAT
path. That 404 is not evidence the portal is something else.

No defensible market share figure exists for these products, so this page ranks nothing.
CKAN and Socrata turn up often enough in public catalogues that probing them first tends
to resolve quickly, which is a working guess about probe order, not a measurement.

## The data.gov API moved, and three government pages disagree about it

Most tutorials on the United States federal catalogue teach the dead path.
`catalog.data.gov/api/3/action/` is superseded. resources.data.gov documents a v4 API at
`api.gsa.gov/technology/datagov/v4/` and says in as many words that it "replaces the
previous CKAN-based API", with the old surface kept read-only for existing integrations.

Authentication is an `X-Api-Key` header, and the rate limits decide the shape of the
client. A personal key allows 1000 requests per hour. `DEMO_KEY` allows 30 per IP per hour
and 50 per day, and is documented as "not suitable for production use or automated
queries", so one hand-run example spends it.

```python
import os
import httpx

V4_SEARCH = "https://api.gsa.gov/technology/datagov/v4/search"

def search(query, per_page=100, org_slug=None, keyword=None, sort=None):
    params = {"q": query, "per_page": per_page}
    for name, value in (("org_slug", org_slug), ("keyword", keyword), ("sort", sort)):
        if value is not None:
            params[name] = value
    response = httpx.get(
        V4_SEARCH,
        params=params,
        headers={"X-Api-Key": os.environ["DATA_GOV_API_KEY"]},   # never DEMO_KEY in a loop
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
```

Those five parameters are the ones confirmed on 2026-08-28. The paging parameter was not,
so read the current reference instead of copying a plausible guess out of an article. At
1000 requests per hour a sweep is a budget rather than a loop, the same arithmetic as
[rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md).

Now the hard part. The government's own three properties contradict each other right now:
resources.data.gov documents v4 and calls CKAN superseded, open.gsa.gov still documents a
v3 CKAN proxy, and data.gov/developers/apis still documents raw `catalog.data.gov/api/3`.
Treat none as authoritative, because two must be stale and the pages give no way to tell
which.

Whether the old CKAN path is genuinely retired is unresolved. Probing returned 404 on three
`catalog.data.gov/api/3/action` paths while the HTML site loaded fine, which is consistent
with removal and equally consistent with a filter answering an automated client. Settle
that one from a real browser session on your own network.

## The offset wall, and the number that lies about the total

The Socrata Discovery cap was pinned exactly on 2026-08-28: `offset=9999` returns 200,
`offset=10000` returns HTTP 400. That is the Elasticsearch `max_result_window` signature
showing through, and it surfaces as a 400 that reads like a malformed request rather than
the edge of a result window.

The second half fails silently, which is worse. `resultSetSize` returned exactly 10000 on
a query matching more than that, so the field is capped at the same wall and is not a total
count. A pager that divides it by the page size stops at exactly 10000 rows and reports a
complete-looking run.

```python
import httpx

CATALOG = "https://api.us.socrata.com/api/catalog/v1"
WALL = 10000

def fetch_page(params, offset, limit=100):
    if offset + limit > WALL:
        raise RuntimeError(f"offset {offset} reaches the {WALL} wall; partition the query")
    response = httpx.get(CATALOG, params=dict(params, limit=limit, offset=offset), timeout=30)
    response.raise_for_status()
    return response.json()

def walk(params, limit=100):
    offset = 0
    while offset + limit <= WALL:
        body = fetch_page(params, offset, limit)
        rows = body["results"]
        if not rows:
            return                      # an empty page is the only trustworthy end
        yield from rows
        offset += len(rows)             # resultSetSize saturates, so it cannot end this loop
```

The guard budgets on `offset + limit`, which is stricter than what was measured: the
boundary pinned here is on offset alone, while the neighbouring product documents its
ceiling as a sum. Either way, the fix for a catalogue larger than the window is to
partition the query into slices that each stay under it, never to page deeper. Same
structural move [numbered pagination](how-to-scrape-paginated-pages-playwright.md) needs
when a site caps its own page count.

## Every product enforces a different ceiling

CKAN is the generous one. `rows` defaults to 10 and accepts up to 1000, so a four thousand
dataset catalogue is four calls with `start` stepping by 1000, not four hundred. People
copy the default of 10 out of the examples and then wonder why a sweep takes an afternoon.

Huwise is the one that surprises. Its `limit` maxes out at 100, not 1000, and above that
the API tells you to use `/exports`. The `offset + limit <= 10000` ceiling is a second,
independent wall, so a large catalogue needs a small page size and a partitioning strategy
at once.

ArcGIS Hub has no pagination at all, which sounds like a mercy and is not. The DCAT feed is
a single document, and an organisation-wide feed measured over 10 MB in one response.
Stream it and pull one record at a time out of the top-level `dataset` array.

```python
import httpx
import ijson   # 3.2 or newer accepts an iterable of byte chunks

FEED = "https://portal.example.gov/api/feed/dcat-us/1.1.json"

def stream_datasets(url):
    with httpx.stream("GET", url, timeout=120) as response:
        response.raise_for_status()
        for record in ijson.items(response.iter_bytes(), "dataset.item"):
            yield record

for record in stream_datasets(FEED):
    print(record.get("identifier"), record.get("title"))
```

The `dataset.item` prefix comes from the confirmed shape: a `dcat:Catalog` object whose
datasets hang off a top-level `dataset` key. Write each record out as you receive it, one
JSON object per line, and the feed never has to fit in memory at either end, which is
[scraping to JSON Lines](how-to-scrape-to-json-lines-playwright.md).

## The token rules contradict themselves, so measure

Socrata's documentation disagrees with itself about whether an application token is
required. dev.socrata.com/docs/queries says requests must be authenticated or carry an app
token. dev.socrata.com/docs/app-tokens says tokens are optional. Both pages were live on
2026-08-28.

The measured behaviour that day was that the Discovery endpoint served results with no
token at all. That is one endpoint on one day, and it does not settle which page is right.
A token costs nothing and retires the question; running without one works today and rests
on whichever page turns out to be stale.

The shape recurs across all of these products. Authentication is the least reliable section
of open data documentation, and a short probe recording the status code with and without
credentials resolves it faster than reading does. Treat a sudden 401 or 429 mid-sweep as a
policy change rather than a client bug, and back off the way
[403 and 429 handling](how-to-handle-403-429-backoff-mid-scrape-playwright.md) describes.

## A rename that moved the docs and left the API alone

OpenDataSoft rebranded to Huwise on 2025-09-30. The API surface did not change:
`/api/explore/v2.1/` still answers, with the same parameters and the same `total_count`
and `results` keys. What moved was the documentation, to help.huwise.com.

The practical consequence is a detection trap. Search results, older tutorials and any code
sniffing for a vendor string in the HTML still point at the old brand, so a portal on
current Huwise looks like an unknown product while its endpoint answers normally. That is
why the identification above keys on response shape: `total_count` plus `results` survived
the rename, the name in the footer did not.

## Where a browser actually earns its place

Most of this page argues against driving a browser, and that argument should be made
honestly. When a documented API exists, call it. A browser to fetch JSON is a whole process
and a page load for something `httpx` does in one request, which is what
[combining a browser with httpx](combine-invisible-playwright-with-httpx-for-speed.md)
is about.

Three cases genuinely need it. The first is a portal whose catalogue only exists behind a
JavaScript search UI, where none of the probe paths answer. Drive the UI once and capture
the request it fires: that request is often one of the documented APIs above under a
rewritten path, and once its URL is known the browser leaves the pipeline.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://portal.example.gov/datasets", wait_until="domcontentloaded")

    with page.expect_response(
        lambda r: r.request.resource_type in ("xhr", "fetch") and "search" in r.url
    ) as caught:
        page.get_by_role("searchbox").fill("air quality")
        page.keyboard.press("Enter")

    hit = caught.value
    print(hit.request.method, hit.url)          # the endpoint the UI calls
    print(sorted(hit.json().keys()))            # compare against the four shapes above
```

The second case is confirming what the API leaves out: a listing record can be missing a
field the rendered page shows, and one visit settles whether it is absent from the data or
from the endpoint. The third is the ambiguity above, where an automated client got 404 and
a browser loaded the site fine. The capture technique generalises, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) covers
the response and routing hooks in full.

## Conclusion

Open data portals are a handful of software products wearing different logos, so the first
move is never a selector. Probe the three host paths, ask the Socrata discovery service
separately, name the product from the keys that come back, and the scrape becomes an API
client with documented pagination. Then respect that product's ceiling: 1000 rows for CKAN,
100 for Huwise with a 10000 sum on top, offset 10000 for Socrata with a `resultSetSize`
that lies about the total at exactly that number, and no pagination at all for an ArcGIS
Hub feed arriving as one document over 10 MB. Keep the browser for JavaScript-only
catalogues and for telling a retired endpoint apart from a filtered one.

## Short answers to the questions that lead here

**How do I tell which software a portal runs?** Probe `/api/3/action/package_search`,
`/api/explore/v2.1/catalog/datasets` and `/api/feed/dcat-us/1.1.json` on its host, plus the
central Socrata discovery service, and match the top-level keys of whatever answers.
Response shape survives rebrands and themes; page HTML does not.

**Why does my data.gov script suddenly 404?** It is probably calling
`catalog.data.gov/api/3/action/`, which resources.data.gov describes as replaced by a v4
API at `api.gsa.gov/technology/datagov/v4/`. Three government pages still document three
different answers, so verify from a real browser first.

**Is `resultSetSize` the number of matching datasets?** No. It saturates at 10000 on the
Socrata Discovery API, the same value at which `offset` starts returning HTTP 400. End the
loop on an empty page and partition the query when the catalogue is larger than the window.

**Do I need a Socrata app token?** The documentation contradicts itself: the queries page
says a token or authentication is required, the app-tokens page says tokens are optional.
Requests succeeded without one on 2026-08-28, a measurement rather than a resolution, so
get a token if the pipeline has to keep running.

**Why does the ArcGIS Hub feed exhaust memory?** It has no pagination, so an
organisation-wide feed arrives as one document that measured over 10 MB. Parse it
incrementally over the top-level `dataset` array instead of loading the whole body.

**Should I use Playwright at all if the API exists?** No, call the API. The browser earns
its cost for catalogues that only exist behind a JavaScript search UI, for confirming a
field the API omits, and for telling a genuinely retired path from one a filter is hiding.

## Sources

- CKAN's API documentation and the `ckanext-dcat` endpoint reference at docs.ckan.org,
  retrieved 2026-08-28, for `package_search` with `rows` and `start`, and for the
  `/catalog.jsonld` family and its fixed 100 records per page.
- dev.socrata.com queries and app-tokens, retrieved 2026-08-28: the two pages that
  disagree about whether a token is required.
- resources.data.gov catalog-api documentation, retrieved 2026-08-28, for the v4 endpoint,
  the `X-Api-Key` header and both rate limits.
- Live calls to the endpoints in the table above, made 2026-08-28, for the response shapes,
  the `offset=9999` versus `offset=10000` boundary, the saturated `resultSetSize` and the
  feed size.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`APIRequestContext`](https://playwright.dev/python/docs/api/class-apirequestcontext),
  retrieved 2026-08-28 and used exactly as documented upstream, since the browser this
  library returns is a real Playwright `Browser`.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for pulling the endpoint out of a JavaScript-only catalogue,
[scraping numbered pagination](how-to-scrape-paginated-pages-playwright.md) for the
partition-instead-of-page pattern, [403 and 429 handling](how-to-handle-403-429-backoff-mid-scrape-playwright.md)
for a key's hourly budget running out mid-sweep, and
[scraping to JSON Lines](how-to-scrape-to-json-lines-playwright.md) for writing a streamed
feed out one record at a time.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The count that stopped at
exactly 10000 was read as a total before it was read as a ceiling, and a pager built on it
reported a complete run that was nothing of the kind.*
