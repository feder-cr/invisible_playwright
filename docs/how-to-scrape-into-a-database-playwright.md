---
title: "How to scrape into a SQLite database with Playwright"
description: "Write a Playwright crawl into SQLite so a refresh upserts on a natural key, one transaction per page, staying idempotent instead of piling up duplicate rows."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 55
---


# How to scrape into a SQLite database with Playwright

To scrape into a SQLite database with Playwright without accumulating duplicates,
give each entity a natural key drawn from stable page content, declare that key
`UNIQUE`, and write every row as an `INSERT ... ON CONFLICT DO UPDATE` upsert inside
one transaction per page. A re-crawl then updates rows in place instead of appending
them, so the database stays a current catalog rather than a log of visits.

A first crawl is easy: loop the pages, pull the rows, append them somewhere. The
problem shows up on the second crawl. You re-visit the same catalog a week later to
refresh prices, and every product you already had comes back again. Append it and you
now have two of everything. Do that weekly and the database stops being a catalog and
becomes a log of visits.

This page is about writing a Playwright crawl into SQLite so that the second run, and
the fiftieth, leave the database as a clean current picture rather than a growing pile.
The structural tools are a natural key and an upsert. The operational tool is one
transaction per page. The stealth tool, specific to refresh crawls, is a fixed seed so
the return visit looks like the same client coming back rather than a new machine
touching every item.

## Why the second crawl is the hard one

The first crawl has no conflicts because the table is empty. Correctness questions only
appear once a row can already exist.

Re-crawling to refresh means the same real-world entity appears again under whatever
identifier the page gives it. If your primary key is a row number or an insertion
order, the database has no way to know that this product is the same product it stored
last week, so it stores it twice. Nothing errors. The table just doubles, then triples,
and any count you run against it is wrong in a way that looks plausible.

The fix is to decide, before the first insert, what makes a row the same row across
visits. That identifier is the natural key, and it is what the rest of this page hangs
on.

## Pick a natural key from stable page data

A natural key is a value the page itself carries that identifies the entity and does not
change between visits. A product code, a listing slug, a permanent item URL. It is the
thing you would use to say "this is the same product" if you were reconciling by hand.

The one rule that matters here: derive the key from stable content, never from position.
The order that items appear in a listing reorders constantly. Sort changes, new arrivals
push everything down, a promoted item jumps to the top. If you key on "third card on
page two", then next week the third card on page two is a different product, your upsert
overwrites the wrong row, and you have silently corrupted two records at once. Position
is the most tempting key because it is always available, and it is the one that will
hurt you.

```python
import hashlib

def natural_key(item_url: str) -> str:
    """A stable id for one entity, derived from page content, not row order.

    Prefer an explicit id the page already exposes (a product code, a
    canonical URL). Hash it only to get a fixed-width, index-friendly key.
    """
    canonical = item_url.split("?")[0].rstrip("/").lower()
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()
```

If the page exposes an explicit product code, use that directly and skip the hash. The
hash is only there to turn a messy URL into a fixed-width column that indexes well.

## Create the table with a UNIQUE key and upsert into it

The database enforces "one row per entity" for you, if you tell it what an entity is.
Declare the natural key `UNIQUE` (or `PRIMARY KEY`), and then every write is an
`INSERT ... ON CONFLICT ... DO UPDATE`: insert when the entity is new, update in place
when it is already there. That single statement is what makes the crawl idempotent. Run
it once or run it ten times, the table ends up the same.

```python
import sqlite3

def open_db(path: str = "catalog.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")   # readers do not block the writer
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product (
            key         TEXT PRIMARY KEY,      -- the natural key
            url         TEXT NOT NULL,
            title       TEXT,
            price_cents INTEGER,
            first_seen  TEXT NOT NULL,
            last_seen   TEXT NOT NULL
        )
    """)
    return conn

UPSERT = """
INSERT INTO product (key, url, title, price_cents, first_seen, last_seen)
VALUES (:key, :url, :title, :price_cents, :now, :now)
ON CONFLICT(key) DO UPDATE SET
    url         = excluded.url,
    title       = excluded.title,
    price_cents = excluded.price_cents,
    last_seen   = excluded.last_seen
"""
```

Note what the conflict clause does not touch: `first_seen` keeps its original value on
update, so the row remembers when you first saw the entity, while `last_seen` and the
mutable fields move forward. That is the difference between a current catalog and an
append-only log, expressed in one statement. The database is now the source of truth
across every incremental run, not a transcript of them.

## One transaction per page so a crash rolls back cleanly

Commit once per page and a crash mid-crawl rolls back cleanly: a partial page never
lands in the table, and a re-run heals the gap because every write is an upsert.

A crawl fails partway for ordinary reasons: the network drops on page forty, the
process is killed, a parse throws on a malformed card. The question is what state the
database is in when that happens.

Commit once per page and the answer is clean. Every row from a page lands together or
none of it does, so an interrupted run leaves fully written pages behind and the page it
died on simply is not there yet. Re-run the crawl and, because every write is an upsert,
the completed pages update harmlessly and the missing page fills in. No half-written
page, no duplicated rows from a partial retry, no manual cleanup.

```python
from datetime import datetime, timezone

def save_page(conn: sqlite3.Connection, items: list[dict]) -> None:
    """All rows from one page commit together, or none do."""
    now = datetime.now(timezone.utc).isoformat()
    with conn:                       # BEGIN ... COMMIT, or ROLLBACK on any exception
        for it in items:
            conn.execute(UPSERT, {
                "key": natural_key(it["url"]),
                "url": it["url"],
                "title": it["title"],
                "price_cents": it["price_cents"],
                "now": now,
            })
```

The `with conn:` block is the whole mechanism. SQLite opens a transaction and commits it
if the block exits normally, or rolls the entire block back if anything raises. Keep the
unit of work at one page: small enough that a failure costs you one page to redo, large
enough that you are not paying a commit per row.

## Wire the crawl to the browser and refresh with a fixed seed

Drive the browser with ordinary Playwright and pass a fixed seed so each refresh returns
as the same client. The extraction runs in a real Firefox driven by stock Playwright, and
the `browser` you get back is a real Playwright `Browser`, so the page-driving code below
is ordinary Playwright.

```python
from invisible_playwright import InvisiblePlaywright

def extract_items(page) -> list[dict]:
    page.wait_for_selector("[data-product]")
    return page.eval_on_selector_all("[data-product]", """
        cards => cards.map(c => ({
            url:         c.querySelector('a.item').href,
            title:       c.querySelector('.title').textContent.trim(),
            price_cents: Math.round(
                parseFloat(c.querySelector('.price').dataset.amount) * 100
            ),
        }))
    """)

def refresh_catalog(seed: int = 42, pages: int = 20) -> None:
    conn = open_db()
    # A fixed seed: the refresh weeks later is the SAME client returning.
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        for n in range(1, pages + 1):
            page.goto(f"https://example.com/catalog?page={n}")
            save_page(conn, extract_items(page))   # one commit per page
    conn.close()

if __name__ == "__main__":
    refresh_catalog()
```

Here is why the seed matters for an update crawl specifically, and not just for
debugging. The whole point of a refresh is that the same site sees you come back. If
every run drew a fresh random identity, then from the site's side a brand-new machine,
with a different GPU, different fonts and a different canvas hash, would appear each week
and walk the entire catalog end to end. That pattern is more conspicuous than a returning
visitor, not less. A fixed seed makes every field the identity implies come back
identical, so the second pass presents a byte-identical fingerprint to the first: one
client that checks back periodically, which is what an ordinary returning user looks
like. The [quickstart](quickstart.md) shows the seed round-trip, and
[pinning specific fingerprint fields](pinning.md) covers forcing one value while the rest
stay seed-derived.

One honest caveat. A stable fingerprint controls what the *browser* looks like across
runs; it does not control the *cadence*. If you re-crawl a large catalog every hour from
one address, the fingerprint being consistent will not hide the fact that a single client
is fetching thousands of pages on a machine schedule. The identity should be steady; the
request rate should look human, which is a separate control covered in
[how to rate-limit your scraper](how-to-rate-limit-your-scraper-playwright.md).

## Conclusion

The database part of a recurring crawl is three decisions made once. Choose a natural
key from stable page content, never from row position. Declare it unique and write every
row as an upsert, so a re-crawl updates in place instead of duplicating. Commit one
transaction per page, so [an interrupted run](how-to-resume-an-interrupted-scrape-playwright.md)
rolls back to a clean page boundary and a re-run heals it. Do that and the database stays the current truth across every
incremental refresh.

The stealth part is one decision: pass a fixed seed, so the refresh weeks later is the
same client returning rather than a new machine discovering the whole catalog again. Both
halves are pulling in the same direction, which is idempotence. The crawl should be safe
to run again, and it should look like it was.

## Short answers to the questions that lead here

**How do I stop my scraper inserting duplicate rows on every run?** Give the table a
unique natural key and write with `INSERT ... ON CONFLICT(key) DO UPDATE`. The re-crawl
then updates the existing row instead of adding a second one.

**What should the natural key be?** A stable value the page carries: a product code, a
canonical item URL, a permanent slug. Never a row number or list position, because those
reorder between visits and you will overwrite the wrong record.

**Why one transaction per page?** So a crash leaves whole pages committed and the failed
page absent, instead of a half-written page. Combined with upserts, re-running the crawl
finishes it cleanly with no manual repair.

**Do I need Postgres for this?** No. SQLite handles `ON CONFLICT` upserts and
transactions fine for single-writer crawls. Turn on WAL mode so a reader can query while
the crawl writes.

**Why keep a first_seen and last_seen column?** So the row records when the entity first
appeared and when you last confirmed it, which an append-only table cannot tell you. The
conflict clause updates `last_seen` and leaves `first_seen` alone.

**Does re-crawling with the same identity get me flagged?** A consistent fingerprint
looks like a returning visitor, which is what you want. What gets flagged is cadence, one
client fetching a whole catalog on a fixed schedule, so pace the requests separately.

## Sources

- SQLite documentation, [UPSERT](https://sqlite.org/lang_upsert.html) for the
  `ON CONFLICT` clause, [Transaction](https://sqlite.org/lang_transaction.html) for the
  commit and rollback behaviour above, and [Write-Ahead Logging](https://sqlite.org/wal.html)
  for the WAL journal mode, retrieved 2026-08-28.
- This project's own API: the seed-to-identity round-trip from the quickstart, verified
  against the reproducible-fingerprint behaviour where one seed yields a byte-identical
  fingerprint on a later run.

**See also:** [how to scrape paginated pages](how-to-scrape-paginated-pages-playwright.md)
for walking the catalog a refresh crawl writes into a database,
[how to scrape only new items incrementally](how-to-scrape-only-new-items-incremental-playwright.md)
for skipping rows you already have, and [the quickstart](quickstart.md) for the two-line
switch and the seed round-trip.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The duplicate-row problem
here is one I shipped before I fixed it, which is why the natural key comes first.*
