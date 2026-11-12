---
title: "How to scrape cursor-based pagination with Playwright"
description: "Scrape cursor pagination with Playwright: read endCursor and hasNextPage out of the JSON payload, pass the token back untouched, and checkpoint it with the last item id."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 106
---


# How to scrape cursor-based pagination with Playwright

To scrape cursor-based pagination with Playwright, read the cursor out of the JSON
payload rather than the DOM, hand it back to the next request exactly as the server
issued it, stop when the payload says `hasNextPage` is false or `endCursor` is null, and
write the cursor together with the last item id at every step so a resume can be checked
instead of assumed.

Cursor pagination has no page numbers. The address of page 7 is a token that exists only
inside page 6's response, so the crawl is a chain. You cannot split it across workers,
and you cannot enter it in the middle.

You give up parallelism and you get correctness. A cursor crawl does not duplicate rows
and does not skip them when the list changes while you read it, which is exactly what
offset does on a feed that gains rows at the head.

## A cursor is a chain, not an index

With a page number in the URL, every page's address is computable before you fetch
anything. Forty pages, eight workers, five each, and any page can be re-fetched later on
its own. None of that survives the move to cursors.

Nothing computes the next token. The server hands it over at the end of a response, so
page 7 is reachable only after page 6 has arrived. Three consequences follow, and all
three are structural:

- **No parallelism inside one list.** A second worker would have to wait for the first
  one's answer before it knew what to ask for.
- **No jumping.** "The newest 500 rows" is one request. "The oldest 500" is a full walk.
- **No isolated retry.** Re-fetching a page in the middle needs the previous page's
  cursor.

Concurrency is still available one level up. Ten filters are ten independent chains,
running side by side with one worker and one identity each.
[Scraping pages in parallel](how-to-scrape-multiple-pages-in-parallel-playwright.md)
covers why those workers need distinct seeds and distinct exits.

## Read the cursor and the stop flag from the payload

The cursor is never in the rendered list. The DOM holds the rows; the token that produced
them lives in the response those rows were painted from, and so does the flag that says
whether more exist.

Naming varies and the shape does not. A GraphQL connection puts them in
`pageInfo.hasNextPage` and `pageInfo.endCursor`, with a per-row copy in `edges[].cursor`.
A REST feed calls the same thing `next_cursor`, `next_page_token`, `meta.next`, or ships
it in a `Link` header with `rel="next"`. Capture one response by hand before writing any
loop.

```python
import json
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/feed", wait_until="domcontentloaded")

    # Turn one page with the site's own control and read what came back.
    with page.expect_response(
        lambda r: "/api/" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.get_by_role("button", name="Next").click()

    response = caught.value
    print(response.request.url)                    # which parameter carries the cursor
    print(response.request.headers)                # what the app sends alongside it
    print(json.dumps(response.json(), indent=2)[:2000])   # where the next one comes out
```

Three answers come out of that one call: which parameter carries the cursor, which field
returns the next one, and which field says whether there is a next one. The general
capture technique is in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## Treat the cursor as an opaque token

The only correct operation on a cursor is handing it back unchanged. Not parsing it, not
rebuilding it, not incrementing it.

Decode one and you usually find two values: the key the list is ordered by, and an id
that breaks ties between rows sharing that key. That pair is what makes a cursor a
position rather than a count, which is the stability argument further down.

```python
import base64

# What a cursor often turns out to be, and why that is not an invitation.
raw = base64.b64decode("MTcyNDgwMDAwMDoxOTg3NDIz")
print(raw)      # b'1724800000:1987423'  -> sort key, then tiebreaker id
```

Understanding the format is not permission to generate one. The encoding is server
private and changes without a version bump. Many tokens carry a signature, so the server
refuses a hand-built one outright. And even when your construction parses, you are
guessing the server's ordering and its tiebreaker, and a wrong guess skips rows quietly.

One byte-level trap rides along. Cursors routinely contain `+`, `/` and `=`, so a token
dropped into an f-string URL arrives with `+` decoded as a space, and the server rejects
it, or worse, ignores it. In the loop below that job belongs to `URLSearchParams`.

## The loop stops on the flag, not on an empty page

The stopping condition lives in the payload: `hasNextPage` false, or a null `endCursor`.
Nothing in the DOM knows the list is finished, and two DOM-shaped stop conditions are
actively wrong here.

Stopping on a response with zero rows truncates the crawl, because a filtered feed can
legally return an empty window with `hasNextPage` still true. Stopping when the on-screen
item count stops growing is right for a button-driven list, where
[the load-more loop](how-to-scrape-load-more-button-playwright.md) advances on measured
growth, and wrong here: a virtualized list holds its DOM count flat while the payload
advances.

A third exit is worth coding. Servers do occasionally repeat a token, and if the cursor
you were just handed is one you already used, the loop never ends.

```python
def fetch_page(page, cursor):
    # Runs inside the document, so the app's own origin, cookies and network
    # stack make the request rather than a separate HTTP client.
    return page.evaluate(
        """async (cursor) => {
            const url = new URL("/api/feed", location.origin);
            url.searchParams.set("limit", "50");
            if (cursor) url.searchParams.set("after", cursor);   // encoded for us
            const res = await fetch(url, {credentials: "same-origin"});
            return {status: res.status, body: res.ok ? await res.json() : null};
        }""",
        cursor,
    )


def walk(page, cursor=None, max_pages=10000):
    seen_cursors = set()
    for _ in range(max_pages):
        result = fetch_page(page, cursor)
        if result["status"] != 200:
            raise RuntimeError(f"cursor rejected with HTTP {result['status']}")

        info = result["body"]["pageInfo"]
        rows = [edge["node"] for edge in result["body"]["edges"]]
        yield rows, info["endCursor"]

        # The flag decides. An empty page with hasNextPage still true is legal,
        # and stopping on len(rows) == 0 silently truncates the run.
        if not info["hasNextPage"] or not info["endCursor"]:
            return
        if info["endCursor"] in seen_cursors:
            return              # the server repeated a token; otherwise this never ends
        seen_cursors.add(info["endCursor"])
        cursor = info["endCursor"]
```

`max_pages` is a ceiling against a server that promises a next page forever, not an exit
condition. The real exits are the flag, the null cursor and the repeat.

## Store the cursor next to the last item id

Write both at every step. The pair is what makes a resume verifiable, and the cursor on
its own is not.

A stored cursor answers no question you can check. Hand it back and rows come out, but
nothing in that response says whether they follow the rows you already have or whether
the server ignored the token and served the head again. Store the id of the last row
written under that token and the resumed response is checkable on its first line.

```python
import time

state = {
    "seed": 42,
    "cursor": None,         # the token, byte for byte as the server returned it
    "last_item_id": None,   # the id of the last row written under that token
    "issued_at": None,      # when the token was handed over; cursors expire
    "count": 0,
}

for rows, next_cursor in walk(page, cursor=state["cursor"]):
    for row in rows:
        write_row(row)              # durable sink first
    state.update(
        cursor=next_cursor,
        last_item_id=rows[-1]["id"] if rows else state["last_item_id"],
        issued_at=time.time(),
        count=state["count"] + len(rows),
    )
    save_checkpoint(state)          # then the checkpoint, atomically
```

The order inside that loop is deliberate. Rows first, checkpoint second. Crash between
them and you re-fetch one page, and the id dedupe drops the duplicates. Reverse the two
and a crash leaves a checkpoint pointing past rows that were never written, which is a
gap, and a gap is invisible. Atomic-write mechanics are in
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md).

## Cursors expire, so a stored cursor is not a bookmark

A cursor is a position in a result set the server is under no obligation to keep. Some
implementations hold a snapshot with a time to live, some sign the token with an expiry,
and some bind it to the session that issued it. Store one on Friday, come back on Monday,
and it can be gone.

A rejection is the good case. A `400` with `invalid_cursor`, a `410`, anything with a
status you can branch on, tells you where you stand.

The bad case returns `200`. An unknown token treated as no token means the head of the
list, with a healthy status code and a full page of rows, so a resume that checks only
the status re-collects the whole feed and reports success. That is why `last_item_id` is
in the checkpoint.

```python
def resume(page, state, seen_ids, max_age=6 * 3600):
    age = time.time() - (state["issued_at"] or 0)
    if not state["cursor"] or age > max_age:
        return restart_from_head(state, seen_ids)

    try:
        rows, next_cursor = next(walk(page, cursor=state["cursor"]))
    except RuntimeError:            # 400, 404, 410: the token is gone and said so
        return restart_from_head(state, seen_ids)

    # A 200 is not proof the token survived. If the first row back is one we
    # already wrote, the server ignored the cursor and served the head instead.
    if rows and rows[0]["id"] in seen_ids:
        return restart_from_head(state, seen_ids)

    return rows, next_cursor
```

Restarting from the head is not the disaster it would be on an offset crawl: dedupe by id
is exact and the walk is stable, so a bad resume costs bandwidth rather than correctness.
What `last_item_id` buys is legibility: the log names the row the run stopped on, so a
bad resume shows on the first response.

## Why a moving list is the reason to prefer cursors

Offset pagination counts. `LIMIT 50 OFFSET 100` skips the first hundred rows of whatever
the result set holds right now, and that set can change between your third request and
your fourth.

Insert one row at the head in that gap and every existing row shifts down by one, so the
row that ended page 3 now begins page 4 and you write it twice. Delete one and everything
shifts up, so a row crosses the boundary the other way and no page ever returns it.
Nothing errors. Both pages look fine. The damage is in your data.

A cursor does not count, it points. The token names a position in the sort order, sort
key plus tiebreaker, and the next request means "rows ordered after this key". Inserting
or deleting a row elsewhere changes no other row's key, so the boundary between page 3
and page 4 is the same boundary it was an hour ago. That is the whole argument for
accepting a serial crawl.

Here is where the guarantee stops. It covers inserts and deletes, not a sort key that
moves. Order a list by something mutable, a "last active" or "bumped" timestamp, and a
row can be updated ahead of your cursor and served twice, or fall behind it and never
appear. The stability belongs to an immutable sort key with an id tiebreaker, so dedupe
by id regardless.

Offset deserves its due. Every page is addressable, so eight workers can split forty
pages, page 12 can be re-fetched alone, and the crawl can start at the tail. On a list
that does not move, an archive or an export, that is free and the missing safety costs
nothing. The mechanics are in
[scraping numbered pagination](how-to-scrape-paginated-pages-playwright.md). Still list,
offset. Moving list, cursor.

## One identity for the whole chain

You cannot split a cursor walk across workers, so one identity carries the entire run and
every resume after it. That makes the seed do more work here than on a numbered crawl.

You can spread forty numbered pages over eight browsers, and each looks like a short
visit. A chain of four hundred requests is one session, in order, from one place, and
adding machines cannot shorten it. So the fingerprint must not change partway, which is
what a fixed `seed` guarantees and why the seed sits in the checkpoint.

The cadence matters too. Four hundred requests at a fixed interval draw a line no person
draws, so derive the pause from the same seed with `random.Random(seed)`. And because
`fetch_page` runs inside the document, each call leaves with the page's cookies, its
origin and the browser's own network stack, which a separate HTTP client replaying the
same URL does not.

Rate limits hit a chain differently. On an offset crawl you can route around a `429` by
fetching another page first; on a chain there is no other page, so the only move is to
wait and retry the same cursor. That retry shape is in
[handling 403 and 429 mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## Conclusion

Cursor pagination trades everything convenient for one thing that matters. You cannot
parallelise a chain, you cannot jump into it, and you cannot rebuild a token you lost, so
the whole crawl is serial and one identity. In exchange the list can churn underneath you
and the walk still returns every row exactly once, which offset cannot promise on
anything that moves. Read the cursor and the stop flag from the payload, pass the token
back untouched, and write it next to the last item id at every step so tomorrow's resume
can prove where it landed. The stability is why you accept the chain; the stored pair is
what keeps it honest.

## Short answers to the questions that lead here

**Is there a way to scrape a cursor-paginated list in parallel?** Not within one list.
Page 7's cursor exists only inside page 6's response, so you have to fetch the pages in
order. Run several lists or filters side by side instead, one worker and one identity
each.

**Can you decode a cursor and build your own?** No. The encoding is server private,
frequently signed, and it changes without notice. Decoding explains why the crawl is
stable and is useless for anything else: a wrong guess at the sort key skips rows without
an error.

**How do you know when to stop?** On `hasNextPage` going false or `endCursor` coming back
null, both read from the payload. Not on an empty page, which is legal on a filtered
feed, and not on a DOM item count that stopped growing.

**My resume came back with page one instead of continuing. Why?** The cursor expired and
the server treated an unknown token as no token, so it served the head with a `200`.
Store the last item id beside the cursor and compare it on the first resumed response,
then fall back to a full walk with id dedupe.

**Why prefer cursors over offset at all?** Offset counts rows from the start of a result
set that can change between requests, so an insert duplicates a row across the page
boundary and a delete drops one entirely. A cursor names a position in the sort order,
and edits elsewhere do not move it.

**Is the cursor guarantee absolute?** No. It covers inserts and deletes, not a sort key
that can change. On a list ordered by a mutable field a row can jump the cursor and be
served twice, or fall behind it and vanish. Dedupe by id even on a cursor crawl.

## Sources

- Playwright's [`page.expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  [`Response.json`](https://playwright.dev/python/docs/api/class-response#response-json)
  and the [`Request`](https://playwright.dev/python/docs/api/class-request) class, used as
  documented upstream, for reading the payload and the request that carried the cursor
  (retrieved 2026-08-28).
- Playwright's [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate),
  which runs the expression in the page's own context, which is what keeps the cursor
  requests on the document's origin and cookies (retrieved 2026-08-28).
- The GraphQL connection convention that names `pageInfo`, `hasNextPage` and `endCursor`,
  which is the field layout the loop above walks.
- This project's own seed behaviour: one seed yields the same GPU, canvas hash, audio
  context, fonts and screen across processes, which is what lets a resumed chain present
  the same visitor as the run that started it.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for finding the payload in the first place,
[scraping numbered pagination](how-to-scrape-paginated-pages-playwright.md) for the
offset sibling and when it is the better tool,
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md) for
the durable checkpoint mechanics, and
[scraping pages in parallel](how-to-scrape-multiple-pages-in-parallel-playwright.md) for
the fan-out a chain forces one level up.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The checkpoint that stored
only the cursor is the mistake this page corrects: an expired token came back as a
healthy 200 carrying the head of the list, and the resume reported success while
re-collecting rows it already had.*
