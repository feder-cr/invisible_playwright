---
title: "How to scrape GraphQL endpoints with Playwright"
description: "Scrape GraphQL endpoints with Playwright: filter captured calls by the operationName in the POST body, replay persisted queries by hash, check the errors array on a 200, and page by feeding endCursor back."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 103
---


# How to scrape GraphQL endpoints with Playwright

To scrape GraphQL endpoints with Playwright, stop filtering on the URL and filter on the
operation name inside the POST body: read `response.request.post_data`, key each captured
response by its `operationName`, parse the payload along the exact field names the sent
query asked for, treat any `errors` array as a failure even when the status is 200, and
page by feeding `pageInfo.endCursor` back into the variables.

A GraphQL site has one network surface. Every search, every filter, every "load more",
every profile card goes to the same path, usually `/graphql`, by POST, with a JSON body.
REST habits do not transfer. There is no `/api/products?page=2` to recognise, no path
segment to match on, no query string to edit. The URL is identical for the one call you
want and for the fifty you do not, so a URL filter takes either everything or nothing.

What distinguishes the calls sits in the request body. What distinguishes a good response
from a bad one sits in the response body rather than in the status line. This page works
through both, plus the three things that behave differently here than anywhere else:
persisted queries, which send a hash where the query text should be; a response shape
that is decided by the request; and pagination that never touches the URL.

## Every call goes to one URL, so filter on the body

The identifier you want is `operationName`, a top-level field in the request body naming
the operation the client is running. Read it off the request and use it as the filter key.
`request.post_data` gives you the raw body as a string, and everything you need to tell
one call from another is in there.

```python
import json
from invisible_playwright import InvisiblePlaywright

def operation_names(request):
    """Every operation in one request body. Batching clients send a list."""
    if request.method != "POST":
        return []
    raw = request.post_data
    if not raw:
        return []
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return []                 # a file upload arrives as multipart, not JSON
    ops = body if isinstance(body, list) else [body]
    return [op.get("operationName", "") for op in ops if isinstance(op, dict)]

captured = []

def on_response(response):
    if "SearchResults" in operation_names(response.request):
        captured.append(response.json())

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("response", on_response)
    page.goto("https://example.com/search?q=widgets", wait_until="networkidle")
```

To wait for one specific operation instead of collecting them all, the same helper drops
into a predicate: `page.expect_response(lambda r: "SearchResults" in
operation_names(r.request))`. The hooks themselves are stock Playwright and behave exactly
as documented, which is the subject of
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) and
[waiting for a specific API response](wait-for-specific-api-response-playwright.md).

Two details bite here. `operationName` is optional in the spec, so a client that omits it
leaves you nothing to key on, and the fallback is a substring of the root field inside the
`query` text. And when a client batches, one body holds a list of operations and the
response holds a list aligned to it by position, so unpacking `response.json()` as a dict
throws.

## Persisted queries send a hash, not a query

When the request body carries no `query` field at all, only
`extensions.persistedQuery.sha256Hash`, the site is using persisted queries and the server
is holding the text. You can replay that request. You cannot compose a new one.

```python
# what an ordinary client sends
{"operationName": "SearchResults",
 "variables": {"term": "widgets", "first": 20},
 "query": "query SearchResults($term: String!, $first: Int!) { ... }"}

# what a persisted client sends
{"operationName": "SearchResults",
 "variables": {"term": "widgets", "first": 20},
 "extensions": {"persistedQuery": {"version": 1,
                                   "sha256Hash": "0b4e...c1"}}}
```

The hash is a sha256 of the exact query string, whitespace included, and the server only
answers hashes it already knows. That is why a captured request keeps working while a
hand-written one does not: you cannot invent a query and produce a hash the server will
accept for it. Some servers answer an unknown hash with a `PersistedQueryNotFound` error
and let the client resend the full text alongside it; others accept nothing but their own
registered hashes. Check which one you face before planning around it.

What you can change is `variables`. That covers a different search term, a different
filter, the next page, which is most of what a scrape needs. What it does not cover is
adding a field the page never requested, so the data you can reach is exactly the data the
app itself asks for. When a working run suddenly stops, check the hash first: a deploy
rotates it, the old one comes back as an error, and the fix is a fresh capture rather than
a patch to the parser. A client that uses `extensions.doc_id` or a bare `id` field instead
of the standard shape works the same way.

## The response shape mirrors the query the page sent

A GraphQL response is shaped by its request. The keys in the payload are the exact field
names and aliases the query asked for, nested the way the query nested them, so there is
no schema-wide response shape to code against. Write the parser against the query you
captured, not against a path that looks reasonable.

Three things move under a parser that assumes. An alias renames a key, so
`primary: image { url }` puts `primary` in the response and no `image` anywhere. A
fragment flattens its fields inline into the parent, so they appear one level higher than
the query text reads. And a list arrives under `edges` with a `node` inside each entry, or
under `nodes`, or as a plain array, depending on the schema. Any of those changing gives
you zero rows from a response that looks perfectly healthy.

```python
def dig(payload, path):
    """Follow the exact key names the captured query asked for."""
    node = payload
    for step in path.split("."):
        if isinstance(node, list):
            node = [dig(item, step) for item in node]
            continue
        if not isinstance(node, dict) or step not in node:
            raise KeyError(f"{path}: no {step!r} here, the query changed")
        node = node[step]
    return node

def connection_rows(connection):
    """Both spellings of a cursor connection, plus the plain-list case."""
    if isinstance(connection, list):
        return connection
    if "edges" in connection:
        return [edge.get("node", edge) for edge in connection["edges"]]
    return connection.get("nodes", [])

rows = connection_rows(dig(payload, "data.search.results"))
```

Raising is the point of `dig`. A chain of `.get()` calls returns `None` at the first
missing step, the row list comes out empty, and the job records a success over nothing.
Keep the captured query text stored beside a sample response, derive the path from it, and
let a missing key stop the run loudly.

## A 200 with an errors array is a failure

GraphQL answers over HTTP 200 almost regardless of what happened, and reports the failure
in an `errors` array in the body. So `if response.status == 200` is not a success check.
It is a check that the transport worked, which was never the part in doubt.

Three cases come back through that same 200. `data` is null and `errors` is populated: the
operation failed completely. `data` is present with nulls at the failed paths and `errors`
is also populated: partial success, the expensive case, because rows do arrive and a null
is indistinguishable downstream from a value that is genuinely absent. Or
`errors` is missing and the data is real. Each error entry carries a `path` array naming
the field that died and usually an `extensions.code`.

```python
class GraphQLError(RuntimeError):
    pass

def unwrap(payload):
    errors = payload.get("errors") or []
    data = payload.get("data")
    if errors:
        codes = [(e.get("extensions") or {}).get("code", "") for e in errors]
        paths = [".".join(str(p) for p in e.get("path") or []) for e in errors]
        if data is None:
            raise GraphQLError(f"{codes} at {paths}: {errors[0].get('message', '')}")
        record_partial(codes, paths)     # partial data: keep it, but mark it
    return data
```

Throttling is the case that makes this urgent. A rate limit on a GraphQL endpoint
frequently arrives as a 200 with a code such as `RATE_LIMITED` or `THROTTLED` in
`extensions`, not as a 429, so a retry wrapper keyed on the status code never fires and
the loop keeps requesting. Read the code out of the body and feed it to the same backoff
you would use for
[a 403 or 429 mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## The cursor lives in the payload, so store it

Cursor pagination is carried in `pageInfo`: `hasNextPage` says whether to continue, and
`endCursor` is the token you pass back as the `after` variable on the next call. Nothing
about your position appears in the URL, which means a resumable run has to store the
cursor itself. There is no page number to recompute.

The cursor is opaque and server-defined. It often base64-decodes into something readable;
do not build on that, because the encoding is not part of the contract and changes without
warning. Store it verbatim, next to the operation name and the rest of the variables that
produced it, since a cursor is only meaningful for that exact operation and filter set.
That pairing is what makes a
[resumable scrape](how-to-resume-an-interrupted-scrape-playwright.md) work here.

```python
def page_through(page, endpoint, base_body, path, headers=None, max_pages=200):
    """path is relative to data, which unwrap() has already peeled off."""
    cursor, seen = None, []
    for _ in range(max_pages):
        body = json.loads(json.dumps(base_body))      # never mutate the template
        body["variables"]["after"] = cursor
        reply = page.request.post(endpoint, data=body, headers=headers or {})
        data = unwrap(reply.json())

        connection = dig(data, path)
        batch = connection_rows(connection)
        seen.extend(batch)

        info = connection.get("pageInfo") or {}
        cursor = info.get("endCursor")
        save_checkpoint(base_body["operationName"], body["variables"], cursor)

        # a server that always answers hasNextPage: true will not stop on its own
        if not batch or not cursor or not info.get("hasNextPage"):
            break
    return seen
```

`max_pages` is a ceiling against a server that keeps promising another page, not the exit
condition. The real exits are an empty batch, a missing cursor and an honest
`hasNextPage: false`. Schemas that page by `offset` and `limit` instead are the same job
with a resumable number rather than an opaque token, and they still keep it in the body.

## Watching the call and replaying it are different tactics

Both work and they fail differently, so the choice is a trade rather than a ranking.
Driving the page and reading what it fetches keeps the app in charge of the query, the
hash and the headers. Replaying the captured operation yourself is far faster and reaches
deep pages directly, and it hands you everything the app used to handle.

| | Driving the page | Replaying the operation |
|---|---|---|
| What triggers the call | a click, a scroll, a route change | your own POST with edited variables |
| Cost per batch | a render plus the app's own delay | one request |
| Reaching page 40 | interact 39 times to get there | set `after` and start there |
| Breaks when | the button or component changes | the hash, the schema or a token changes |
| How it fails | loudly, on a timeout or a missing locator | quietly, as a 200 with an errors array |

That last row is the one to weigh. A page-driven run stops when something moves, and you
find out immediately. A replay run keeps going: the endpoint answers 200, the reason sits
in the body, and a job that does not read it writes empty pages and reports success. If
you take the replay route for the volume, the checks from the two sections above stop
being optional.

Where the replay runs matters too. `page.request.post()` issues the call from the browser
context, so it carries that context's cookies and the same TLS handshake the page uses.
What it does not do is run the app's JavaScript, so any header the client computes at call
time, a bearer token held in memory, a per-request trace id, a client-name header, is
absent unless you copy it off the request you observed. Moving the same replay out to a
bare HTTP client drops the browser handshake as well, which is the sharp edge described in
[combining this browser with httpx](combine-invisible-playwright-with-httpx-for-speed.md).

```python
with page.expect_request(
    lambda r: "SearchResults" in operation_names(r)
) as caught:
    page.get_by_role("button", name="Search").click()

observed = caught.value
template = json.loads(observed.post_data)          # keeps the persisted hash intact
headers = {k: v for k, v in observed.all_headers().items()
           if k.lower() not in ("host", "content-length")}

# replayed from the browser context: same cookies, same handshake as the page
rows = page_through(page, observed.url, template, "search.results", headers)
```

## Pacing a sweep against a single endpoint

A GraphQL sweep is the easiest traffic shape there is to rate limit. One URL, one method,
the same operation name in body after body, with only a cursor changing between them. The
operation name sits right there in the request, so a per-operation limit is cheap to write
and common to meet.

Keep the page size the app itself asks for. If the observed request sends `first: 20` and
yours sends `first: 500`, that is a request no version of the client would make, and an
out-of-range value usually comes back as another 200 with an errors entry anyway. Hold one
seed-stable identity across the whole sweep. The endpoint sees a session, not isolated
calls, and a fingerprint that changes between two pages of one result set is stranger than
either page alone.

Then pace it deliberately. A replay loop has no render time in it and no reading pause, so
it runs at a speed the app never could, and that gap is exactly what a limiter measures.
Space the calls and back off on the codes you find in the body rather than only on the
status line, which is the shape [rate limiting your own
scraper](how-to-rate-limit-your-scraper-playwright.md) takes.

## Conclusion

GraphQL moves every question you would normally ask the URL into the body. Which call is
this: the `operationName`. Can I write my own version of it: only if there is a `query`
field and not just a hash. What does the payload look like: whatever the sent query asked
for, aliases and all. Did it work: read `errors`, because the 200 will not tell you. Where
does the run stand in the list: `endCursor`, saved next to the variables that produced it.
Point a
scraper at the URL and you get every operation in one bucket with no way to tell them
apart. Point it at the body and each of those questions has one exact field that answers
it.

## Short answers to the questions that lead here

**How do I filter GraphQL calls when every request goes to the same URL?** Filter on
`operationName` inside `request.post_data` instead of on `response.url`. Parse the body as
JSON, handle the list case for batching clients, and use the operation name as your key.

**The request has no query field, only a hash. Can I still scrape it?** Yes, by replaying
the captured request and changing only `variables`. The hash is a sha256 of the exact query
text and the server answers only hashes it already holds, so composing a new query does not
work. A rotated hash means a fresh capture.

**Why does my parser return zero rows from a response that looks fine?** Because the
response shape follows the query that was sent, and an alias, a fragment or an
`edges` versus `nodes` difference moved the path. Derive the path from the captured query
and raise on a missing key instead of returning an empty list.

**The status is 200 and I got no data. What happened?** Read the `errors` array in the
body. GraphQL reports failures at 200, including throttling, so a status check reports
success on an empty result. Each entry has a `path` naming the field that failed and often
an `extensions.code`.

**Where is the page number in a GraphQL request?** There is not one. Position lives in
`pageInfo.endCursor` inside the response, and you send it back as the `after` variable.
Store the cursor with the operation name and variables it belongs to, or the run cannot
resume.

**Should I replay the request or drive the page?** Replay for volume and for deep pages,
drive the page when the client computes headers you cannot reproduce. Replay is much
faster and fails silently at 200, so it only pays off with the errors check and the
cursor checkpoint in place.

## Sources

- Playwright's [network events and `page.on("response")`](https://playwright.dev/python/docs/network),
  and the [`Request`](https://playwright.dev/python/docs/api/class-request) API for
  `post_data`, `post_data_json` and `all_headers()`, retrieved 2026-08-28.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`expect_request`](https://playwright.dev/python/docs/api/class-page#page-expect-request),
  plus [`APIRequestContext`](https://playwright.dev/python/docs/api/class-apirequestcontext)
  behind `page.request`, which issues calls from the browser context, retrieved 2026-08-28.
- The GraphQL specification's response format, which defines `data`, `errors`, the error
  `path` and `extensions`, and does not tie any of them to an HTTP status.
- The cursor connection convention (`edges`, `node`, `pageInfo`, `hasNextPage`,
  `endCursor`) as implemented by the common server libraries.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the response and route hooks in general,
[waiting for a specific API response](wait-for-specific-api-response-playwright.md) for the
predicate form, [scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) for
the URL-based sibling of cursor paging, and
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md) for
where the cursor gets checkpointed.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The URL filter and the
unchecked 200 are both mistakes that shipped here first: one put every operation into the
same bucket, the other wrote empty rows over a throttle message and called the run a
success.*
