---
title: "How to scrape public tender notices with Playwright"
description: "Scrape public tender notices with Playwright: one row per lot, deadlines kept with the offset the response carries, classification-code filters, and a re-scrape that reconciles revisions instead of appending."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 124
---


# How to scrape public tender notices with Playwright

**To scrape public tender notices with Playwright, key every row on the notice
identifier plus the lot identifier, not on the buyer and the title, take each
deadline from the offset-bearing value in the underlying response instead of the string
the page rendered, filter on classification codes, not free-text titles, and make
every re-scrape reconcile a revision and a status onto the existing row instead of
appending a new one.** Notices are documents about a procurement, not the procurement
itself, and that distinction decides the whole schema.

The same purchase is published several times over its life. An authority signals an
intention, then calls for tenders, then corrects the call, then announces who won, then
records a change to the running contract. Each of those is a separate document with its
own publication identifier, its own date and its own set of fields, and each one appears
on the portal with roughly the same title and the same buyer.

Treat them as duplicates of one record and you delete exactly the information anyone
wanted: the deadline that moved, the estimated value against the awarded value, the fact
that a contract grew by forty percent after signature. This page is the row shape that
keeps all of it, the fields that break first, and where driving a browser stops being the
right tool.

## One procurement is several notices, and none of them are duplicates

Dedupe on the notice identifier. Group on the procedure reference. Never dedupe on buyer
plus title, because that is precisely the pair every notice in a lifecycle shares.

The notice identifier is the identifier of the document. The procedure reference is the
buyer's own file number, and it is the thread that ties the lifecycle together, sometimes
appearing as a plain reference field and sometimes only as a "refers to notice X" pointer
on the later documents. Store both, plus the notice type, and the lifecycle reassembles
itself with a group-by instead of a guess.

| Notice type | What it carries that the others do not |
|---|---|
| Prior information | An intention and an indicative date, usually no deadline and no binding value |
| Contract notice | The submission deadline, the lots, the classification codes, the estimated value |
| Corrigendum | One changed field on an already published notice, most often the deadline |
| Award notice | The winner, the number of bids received, the value actually contracted |
| Modification | A change to a contract that is already running, with a new value |

The type is not cosmetic. A pipeline that sums value across a procedure without filtering
on type will add an estimate, a maximum and an awarded figure together and produce a
number that describes nothing.

## The lot is the row, not the notice

One notice is frequently divided into lots, and the lot is what a supplier actually bids
on. Lots carry their own titles, their own classification codes, their own values, their
own awards, and occasionally their own deadlines. A notice-level row flattens all of that
into whichever lot happened to be first.

So expand at read time and normalise the shape: a notice with no lots becomes a single
lot with a fixed synthetic identifier, so downstream code never has two shapes to handle.
The fallback direction is always lot first, notice second, and never the reverse.

```python
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

@dataclass
class TenderRow:
    notice_id: str                 # identifier of THIS document
    procedure_ref: str             # buyer's file number, shared across the lifecycle
    notice_type: str               # prior_information | contract | corrigendum | award
    lot_id: str                    # "1", "2" ... or "0" when the notice has no lots
    title: str
    buyer: str
    cpv_codes: list = field(default_factory=list)
    deadline_local: Optional[str] = None    # exactly as published, unconverted
    deadline_utc: Optional[str] = None      # only when an offset was available
    deadline_tz_known: bool = False
    value_amount: Optional[Decimal] = None
    value_currency: Optional[str] = None
    value_vat_included: Optional[bool] = None   # tri-state, None means unstated
    value_basis: Optional[str] = None           # estimated | maximum | awarded
    status: str = "active"                      # active | amended | withdrawn | awarded
    revision: int = 1
    content_hash: str = ""

def expand_lots(notice: dict) -> list:
    lots = notice.get("lots") or [{"id": "0"}]
    return [
        TenderRow(
            notice_id=notice["id"],
            procedure_ref=notice.get("procedureRef") or notice["id"],
            notice_type=notice["type"],
            lot_id=str(lot.get("id", "0")),
            title=lot.get("title") or notice["title"],
            buyer=notice["buyer"],
            cpv_codes=lot.get("cpv") or notice.get("cpv") or [],
        )
        for lot in lots
    ]
```

`("notice_id", "lot_id")` is the primary key for everything that follows. Every later
step in this page, the deadline, the value, the attachment inventory and the reconcile,
operates on that pair.

## A deadline without a timezone is a coin flip

The submission deadline is the field the whole dataset turns on, and the rendered string
almost never carries the zone. "31/03/2026 12:00" is a two-hour question at noon and a
different-day question at 23:30. Get the offset wrong near midnight and the date itself
flips, which is the version of this bug that survives review because the time still looks
plausible.

Worse, some portals convert the deadline to the client's clock before painting it. The
browser timezone is derived from the egress IP, so two exits produce two different
deadlines for the same notice, and neither of them is what the buyer published. That is a
[timezone and locale mismatch](timezone-proxy-mismatch.md) turned into a data error
instead of a detection one.

The value with the offset usually exists, just not in the text node. It is in the JSON the
detail view fetches, or in the `datetime` attribute of a `<time>` element while the
element's text carries the localised string. Read the attribute or the response, never the
rendered text. Capturing that response directly is the same technique as
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

```python
from datetime import datetime, timezone

def read_deadline(page, url):
    """Return (as_published, utc_iso, tz_known). Never invent the zone."""
    with page.expect_response(
        lambda r: "/notice/" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.goto(url, wait_until="domcontentloaded")

    raw = caught.value.json().get("submissionDeadline")
    if not raw:
        return None, None, False

    # fromisoformat accepts a trailing Z from 3.11 on; the replace keeps
    # the same line working on older interpreters.
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # No offset anywhere. Store it naive and flag it. Do NOT borrow the
        # browser's zone, which follows the proxy exit, not the buyer.
        return raw, None, False

    return raw, parsed.astimezone(timezone.utc).isoformat(), True
```

Keep the published string as well as the computed instant. And if you ever do have to
attach a zone by hand, attach a zone name through `zoneinfo`, not a fixed offset: a
deadline in late March sits on the other side of a clock change from the day you scraped
it, so a hardcoded plus-two is wrong for half the year.

## Filter on classification codes, not on titles

Titles are written by hundreds of different authorities in free text, in several
languages, with internal project names, abbreviations and department jargon. A keyword
filter over that column misses most of what it should catch and catches things it should
not. The classification code is the only field with a controlled vocabulary behind it.

The common procurement vocabulary is eight digits plus a check digit, and the hierarchy
lives in the prefix: the first two digits are the division, the first three the group, the
first four the class. That means one prefix test covers a whole branch, and a broad filter
and an exact code can sit in the same expression.

```python
import re

CPV_RE = re.compile(r"\b(\d{8})(?:-\d)?\b")

def normalize_cpv(text: str) -> list:
    """Every 8-digit code in a CPV field, check digit dropped."""
    return [m.group(1) for m in CPV_RE.finditer(text or "")]

def matches_any_prefix(codes, prefixes) -> bool:
    return any(code.startswith(p) for code in codes for p in prefixes)

WANTED = (
    "45",         # division: construction work
    "7112",       # class: engineering design services
    "48000000",   # exact code: software packages and information systems
)

selected = [row for row in rows if matches_any_prefix(row.cpv_codes, WANTED)]
```

Keep every code you find, not just the main one. The real subject of a lot is often in the
second or third additional code while the main code is a generic parent. Supplementary
codes are letter-prefixed and describe an attribute rather than a subject, so they belong
in their own column and must not be mixed into the prefix test.

Where this stops: the code is typed by a person at the buyer's office, and it is sometimes
too generic or simply wrong. It is the best filter available, not a correct one. Keep the
title text alongside it for a second pass, and expect a small tail that only a human
reading the title will ever classify properly.

## A published value is three separate facts in one column

The number is the easy part. What makes value fields unusable is that the same portal is
inconsistent about the other three facts across notices: the currency, whether the figure
includes tax, and whether it is an estimate, a ceiling or an amount actually contracted.
One column of floats destroys all three.

Store four fields plus the raw string, and let unknown stay unknown. `None` for the tax
treatment is a row you can exclude from a total. `False` guessed by a default is a row
that pollutes the total and looks perfectly healthy while it does it.

```python
import re

BASIS_HINTS = (
    ("maximum", "maximum"), ("ceiling", "maximum"), ("up to", "maximum"),
    ("estimated", "estimated"), ("indicative", "estimated"),
    ("awarded", "awarded"), ("final value", "awarded"),
)

def parse_value(raw, currency_field="", vat_field="", parse_amount=None):
    """Split a published value into the facts it actually contains."""
    text = (raw or "").strip()
    blob = f"{text} {vat_field}".lower()

    vat = None                                   # unstated stays unstated
    if any(k in blob for k in ("excluding vat", "excl. vat", "net of vat")):
        vat = False
    elif any(k in blob for k in ("including vat", "incl. vat", "vat included")):
        vat = True

    basis = None
    for hint, label in BASIS_HINTS:
        if hint in blob:
            basis = label
            break

    currency = (currency_field or "").strip().upper() or None
    if currency is None:
        found = re.search(r"\b([A-Z]{3})\b", text)
        currency = found.group(1) if found else None

    # parse_amount is a locale-aware parser, never a chain of str.replace calls
    return {
        "amount": parse_amount(text) if parse_amount else None,
        "currency": currency,
        "vat_included": vat,
        "basis": basis,
        "value_raw": text,
    }
```

The digits themselves are a locale problem, not a procurement one, and it is already
solved next door in [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).
Delegate to that parser instead of writing a second one here. The rule that survives the
whole exercise is simple: never sum a column that mixes basis values, and never sum across
currencies without recording which rate and which date produced the conversion.

## List the attachments before downloading any of them

Most of the substance is not on the notice page. It is in the tender documents: the
specification, the bill of quantities, the drawings, the contract template, the
clarification log. Downloading all of them on every run is slow, expensive in storage, and
the single most conspicuous request pattern a scraper can produce on a portal.

Inventory first. If the documents tab fires its own request, that response usually carries
filename, size, media type and a document identifier already, which is the cheapest
possible answer. When it does not, issue a HEAD through `page.request`, which shares the
cookie storage and the proxy of the browser context, so a session-gated document link
answers instead of redirecting to a login page.

```python
def list_attachments(page, links):
    """Inventory documents without pulling their bytes."""
    inventory = []
    for href in links:
        resp = page.request.head(href, max_redirects=5)
        if resp.status in (403, 405):        # server refuses HEAD
            resp = page.request.get(href, headers={"Range": "bytes=0-0"})

        headers = resp.headers               # keys are lowercased by Playwright
        inventory.append({
            "url": href,
            "media_type": headers.get("content-type", "").split(";")[0],
            "size": headers.get("content-length"),
            "last_modified": headers.get("last-modified"),
            "etag": headers.get("etag"),
            "disposition": headers.get("content-disposition", ""),
        })
    return inventory
```

Then fetch selectively: only the media types you actually parse, and only when the
`(etag, last-modified, content-length)` tuple has changed since the last run. Clarification
documents get republished constantly, and that tuple is what tells you which one moved. The
[file download mechanics](how-to-download-files-playwright.md) apply once you decide to
pull one. Two honest gaps: a streamed response can omit `content-length` entirely, and some
servers return the same `last-modified` for a regenerated file, so the tuple detects most
changes rather than all of them.

## A re-scrape reconciles, it never appends

Notices are amended after publication and sometimes withdrawn while staying visible, with
a status flag instead of a 404. Awards arrive months later against the same procedure. So
the second run is an upsert on `("notice_id", "lot_id")`, comparing a hash over the fields
that matter, bumping a revision and archiving the previous version.

```python
import hashlib
import json

TRACKED = ("title", "deadline_local", "deadline_utc", "value_amount",
           "value_currency", "value_vat_included", "value_basis",
           "cpv_codes", "status")

def content_hash(row: dict) -> str:
    payload = json.dumps({k: str(row.get(k)) for k in TRACKED}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def reconcile(store, row: dict) -> str:
    key = (row["notice_id"], row["lot_id"])
    row["content_hash"] = content_hash(row)
    previous = store.get(key)

    if previous is None:
        row["revision"] = 1
        store.put(key, row)
        return "inserted"

    if previous["content_hash"] == row["content_hash"]:
        store.touch(key)                    # update last_seen and nothing else
        return "unchanged"

    row["revision"] = previous["revision"] + 1
    store.archive(key, previous)            # keep the old deadline and old value
    store.put(key, row)
    return "revised"
```

Three outcomes, and "appended" is not one of them. A run reporting nine hundred inserts on
a portal you already hold is not a good run, it is a broken key, and the counts are the
cheapest place to notice that. The same shape as
[incremental scraping](how-to-scrape-only-new-items-incremental-playwright.md), with one
extra rule that matters more here than anywhere else.

That rule: absence from a listing is not a withdrawal. A notice drops out of a result set
because a facet changed, because the default date window rolled forward, or because
pagination shifted under you. Mark a row withdrawn only when the notice page itself says
so. Everything else gets a stale `last_seen` and keeps its status.

## Where the browser is the wrong tool

Check for a documented listing endpoint or a bulk export before writing a selector. Many
procurement portals publish one, and when they do, an API client beats a browser on every
axis that matters. That check is the whole first move in
[scraping open data portals](how-to-scrape-open-data-portals-playwright.md), and it is
worth ten minutes before any of the code above.

A browser earns its place in three narrower cases: the search form builds its query in
JavaScript so the result URL is not constructible by hand, the document links are gated on
a cookie the page sets, and the detail view fetches the offset-bearing JSON that the
server-rendered HTML never shows. All three are real, and all three are why this page
exists.

What no amount of session realness fixes: a registration wall, an accepted-terms step, an
account tied to a verified legal entity, or a qualified signature certificate on the
submission side. Those are authorisation, not detection, and this library does not solve
captchas either. If the document list sits behind a login you are entitled to hold, that
is a credentials problem with a session behind it, not a fingerprinting one.

## Conclusion

Tender data punishes a schema chosen for the page instead of the domain. The notice is a
document, not a purchase, so the key is the notice identifier and the lot, with the
procedure reference tying the lifecycle together. The deadline is only a deadline once it
carries an offset that came from the response, not from the browser's clock. The
classification code is the only filter with a vocabulary behind it, and it is still
imperfect. A value is four fields, and unknown has to stay unknown. Attachments get
inventoried before they get downloaded. And the second run reconciles onto the first,
because the amendment, the withdrawal and the award are the entire reason anyone tracks
this in the first place.

## Short answers to the questions that lead here

**The same tender appears four times. Should I deduplicate it?** No. Those are four
documents about one procurement: a prior information notice, a contract notice, a
corrigendum and an award notice. Dedupe on the notice identifier and group on the
procedure reference, never on buyer plus title.

**Why is my scraped deadline off by an hour, or by a day?** The rendered string carried no
timezone and something filled one in. Take the deadline from the offset-bearing value in
the response or the `datetime` attribute, and if no offset exists anywhere, store the
value naive and flag it rather than guessing.

**Should I store one row per notice or one per lot?** One per lot. Lots carry separate
values, separate classification codes, separate awards and sometimes separate deadlines. A
notice without lots becomes a single lot with a fixed identifier so the shape stays
uniform.

**How do I filter tenders by subject reliably?** On classification codes, using prefix
matching, because the hierarchy is in the prefix. Titles are free text from hundreds of
authorities. Keep every code on the record, not only the main one, and accept that a
mistyped code is a real residual error.

**Do I have to download every attached document?** No. HEAD each link through
`page.request` so it inherits the session cookies, record media type, size, etag and
last-modified, then fetch only the types you parse and only when that tuple changes.

**A notice vanished from my search results. Was it withdrawn?** Probably not. Facets, date
windows and pagination all remove rows from a result set. Mark a row withdrawn only when
the notice page states it, and otherwise just let `last_seen` go stale.

## Sources

- Playwright Python [`Page.expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  retrieved 2026-08-28, used to capture the detail request that carries the offset-bearing
  deadline.
- Playwright Python [`Page.request`](https://playwright.dev/python/docs/api/class-page#page-request)
  and [`APIRequestContext.head`](https://playwright.dev/python/docs/api/class-apirequestcontext#api-request-context-head),
  retrieved 2026-08-28: the request context attached to a page shares cookie storage with
  its browser context, which is why a session-gated document link answers a HEAD issued
  through it, and response header keys are lowercased.
- Playwright Python [`Locator`](https://playwright.dev/python/docs/api/class-locator),
  retrieved 2026-08-28, for reading the `datetime` attribute rather than the rendered text.
- The common procurement vocabulary's published structure: eight digits plus a check digit,
  hierarchy carried in the prefix by division, group and class, with letter-prefixed
  supplementary codes describing attributes rather than subjects.
- CPython's `datetime.fromisoformat`, which accepts a trailing Z from 3.11 onward and
  returns a naive datetime when the string carries no offset. That naive return is the
  condition the deadline parser branches on.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the response that carries the deadline offset,
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for the locale-aware number parse the value splitter delegates to,
[scraping into a database](how-to-scrape-into-a-database-playwright.md) for the upsert and
history tables the reconcile step writes into, and
[scraping search results forms](how-to-scrape-search-results-form-playwright.md) for
driving the faceted query that produces the notice list in the first place.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Keying rows on buyer plus
title is a mistake that shipped here: it merged a contract notice with its award notice,
overwrote the deadline with an award date, and the loss was only spotted because a count
of procedures came back larger than the count of notices.*
