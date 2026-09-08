---
title: "How to scrape patent listings with Playwright"
description: "Scrape patent listings with Playwright: bind each date to its labeled field, keep family members and claim dependencies intact instead of flattening them, and stamp legal status with the scrape's own timestamp."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 142
---


# How to scrape patent listings with Playwright

To scrape patent listings with Playwright, read every date, status, code, and
name from its explicit field label instead of its position on the row, keep
each family member and each claim's dependency structure intact instead of
collapsing them into one value, and attach the scrape's own timestamp to legal
status because the page itself rarely prints when "today" was.

A patent record looks like five or six short facts and is actually five or six
different data structures wearing the same plain layout. A date column can
hold a filing date, a publication date, a grant date, a priority date, or an
expiry date, and two of those often sit in the same row with no label at all.
The same invention can appear as three or four separate publications in
different jurisdictions, each with its own number and its own status. A claim
is not a paragraph, it is a node in a dependency graph. A classification code
is one entry in a set, not a single tag. Read any of these positionally or
flatten them early and the row still looks complete; it is just wrong in a way
that only shows up when someone tries to use the field you threw away.

## Bind each date to its field label, not its position

A bibliographic table on a patent page routinely lists four or five dates in
the same block, and the labels are the only thing that tells them apart. Read
column two because "that's where the publication date usually is" and the
script will work for months, until a jurisdiction that orders its rows
differently, or omits one date entirely, shifts everything by one and every
downstream date is now wrong under the right label. The fix is to match on
the label text itself, normalize it through a small alias table, and only then
read the adjacent value.

```python
import re
from datetime import datetime
from invisible_playwright import InvisiblePlaywright

DATE_FIELD_ALIASES = {
    "filing date": "filed",
    "application date": "filed",
    "date of filing": "filed",
    "publication date": "published",
    "priority date": "priority",
    "earliest priority date": "priority",
    "grant date": "granted",
    "date of grant": "granted",
    "issue date": "granted",
    "expiry date": "expires",
    "anticipated expiration": "expires",
}

def parse_date_cell(text):
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None

def read_bibliographic_dates(page):
    dates = {}
    rows = page.locator("table.bibliographic tr, dl.bibliographic > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        label = row.locator(".label, dt").inner_text().strip().lower()
        canonical = next(
            (v for k, v in DATE_FIELD_ALIASES.items() if k in label), None
        )
        if canonical is None:
            continue
        value_text = row.locator(".value, dd").inner_text()
        dates[canonical] = parse_date_cell(value_text)
    return dates
```

The alias table is doing the real work here. It absorbs the wording that
differs between offices ("date of grant" versus "issue date" for the same
concept) while keeping the five meanings distinct in the output. A row the
table does not recognize is skipped rather than guessed at, which is the
correct failure mode: a missing field is visible in the output dict, a
misattributed one is not.

## Model the family, do not collapse it into one publication

The same invention is frequently filed as a US application, a European
application, a PCT/WO application, and one or more national filings, and each
of those carries its own publication number and its own legal status. Treating
each family member as an unrelated patent misses that they describe one
invention and duplicates the same claims across rows with no way to tell they
are related. Merging them into a single record solves that problem and creates
a worse one: it throws away the jurisdiction-specific status, because "active"
in one country and "lapsed" in another are both true at once and a single
merged field can only hold one of them.

```python
def read_family_members(page):
    with page.expect_response(
        lambda r: "family" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.get_by_role("tab", name="Family").click()

    payload = caught.value.json()
    members = []
    for entry in payload.get("familyMembers", []):
        members.append({
            "jurisdiction": entry.get("country"),
            "publication_number": entry.get("publicationNumber"),
            "kind_code": entry.get("kindCode"),
            "application_number": entry.get("applicationNumber"),
        })
    return members

def build_family_record(family_id, title, members):
    return {"family_id": family_id, "title": title, "members": members}
```

The row shape that survives a second run keeps one record per family, with a
list of members underneath it, each member carrying its own jurisdiction,
number, and (from the next section) its own status and its own dates. Nothing
about the invention is deduplicated away, and nothing jurisdiction-specific
gets averaged into a value that describes no single country accurately.

## Stamp legal status with the scrape's own timestamp

Legal status active, expired, lapsed, or withdrawn changes over a patent's
life, and the page almost always renders it as of today with no as-of date
printed anywhere near it. A status column copied straight into a dataset reads
fine on the day it was captured and becomes actively misleading eight months
later, when the patent has since lapsed and nothing in the row says the value
is stale. The label alone is not data; the label paired with the moment it was
read is.

```python
from datetime import datetime, timezone

def read_legal_status(page):
    status_text = page.locator(".legal-status, .patent-status").inner_text()
    return {
        "status": status_text.strip(),
        "status_as_of": datetime.now(timezone.utc).isoformat(),
    }
```

This is a two-line function and it is the single most consequential one on
this page, because it is the field a reader is most likely to treat as
permanent. Store `status_as_of` next to `status` in every row, not in a
separate crawl log a reader has to go find, so the two travel together however
the record is later exported or filtered.

## Keep the claim dependency tree, do not flatten it to text

Claims carry an internal numbered structure that a single text blob throws
away. Claim 1 is usually independent and stands on its own; later claims
narrow it, and they do so by naming it explicitly: "The method of claim 1,
wherein...". That back-reference is frequently exactly what a reader of patent
data wants, because it is how a competitor's engineering team or an examiner
works out which claims actually matter and which ones only restate a narrower
case of an earlier one.

```python
CLAIM_REF_RE = re.compile(r"claim (\d+)", re.IGNORECASE)

def parse_claims(page):
    claim_nodes = page.locator(".claim, li.claim-item")
    claims = []
    for i in range(claim_nodes.count()):
        node = claim_nodes.nth(i)
        number_text = node.locator(".claim-number").inner_text()
        number = int(re.sub(r"\D", "", number_text) or 0)
        body = node.locator(".claim-text").inner_text()
        refs = sorted({int(m) for m in CLAIM_REF_RE.findall(body) if int(m) != number})
        claims.append({
            "number": number,
            "text": body,
            "depends_on": refs,
            "independent": not refs,
        })
    return claims
```

`depends_on` is empty for an independent claim and non-empty for a dependent
one, which is a cheap and reliable proxy: independence is defined by the
absence of a back-reference, not by position in the list, since some
jurisdictions do not require independent claims to come first. A single text
field can hold the same words and answer none of the questions this structure
answers directly.

## Capture every classification code, not just the first

Classification codes, CPC and IPC both, are hierarchical, and more than one
code routinely applies to a single patent. A patent's field, and its subfield,
is a set, not a scalar, and reading only the first code listed on the page
silently discards every other one, which usually means discarding the more
specific classification in favor of whichever one the layout happened to put
first.

```python
def parse_classification_codes(page):
    codes = {"cpc": [], "ipc": []}
    for scheme, selector in (("cpc", ".cpc-code"), ("ipc", ".ipc-code")):
        nodes = page.locator(selector)
        for i in range(nodes.count()):
            code = nodes.nth(i).inner_text().strip()
            if code and code not in codes[scheme]:
                codes[scheme].append(code)
    return codes
```

Keep both schemes as lists rather than picking one canonical code per patent.
A downstream query that filters "all patents in this subfield" needs to check
membership in the list, not equality against a single stored string, or it
will miss every patent whose matching code happened not to be first.

## Separate inventor from assignee, and do not assume today's owner is the original one

Inventor and assignee are different roles and can list entirely different
people or companies. The inventors are the individuals who did the work and
that list does not change after filing. The assignee is whoever currently
holds the rights, and a patent can be reassigned, sold, or transferred to a
holding company well after grant, so the assignee shown on the page today may
have nothing to do with who originally filed it.

```python
def parse_parties(page):
    def names(selector):
        nodes = page.locator(selector)
        return [nodes.nth(i).inner_text().strip() for i in range(nodes.count())]

    return {
        "inventors": names(".inventor-name"),
        "current_assignee": names(".current-assignee"),
        "original_assignee": names(".original-assignee"),
    }
```

`original_assignee` is absent on plenty of pages, and an empty list there is
the honest answer, not a bug to work around. What matters is not merging
`current_assignee` and `original_assignee` into one "assignee" field: the
whole reason a reader looks at both is to see whether they differ, and a
merged field erases the comparison before anyone gets to make it.

## Assemble one row per family member, and let dates and status ride along with it

Every function above reads one page. A useful dataset needs one row per family
member, because status, dates, and even the claim set can differ by
jurisdiction for what is nominally the same invention. The assembly step pulls
the shared invention-level fields once, then attaches jurisdiction-specific
fields per member, so a query for "every active family member in this
jurisdiction" does not require reconstructing the family from scratch.

```python
def scrape_patent_record(url, browser):
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded")

    number = page.locator(".publication-number").inner_text().strip()
    title = page.locator("h1.invention-title").inner_text().strip()

    record = {
        "publication_number": number,
        "title": title,
        "dates": read_bibliographic_dates(page),
        "legal_status": read_legal_status(page),
        "claims": parse_claims(page),
        "classification": parse_classification_codes(page),
        "parties": parse_parties(page),
        "family": read_family_members(page),
    }
    page.close()
    return record

with InvisiblePlaywright(seed=42) as browser:
    record = scrape_patent_record("https://example.com/patent/US1234567", browser)
```

The `family` list inside this record holds only the identifying fields for
each sibling publication (jurisdiction, number, kind code). A full pipeline
walks that list and calls `scrape_patent_record` again on each member's own
page, so the per-jurisdiction status and dates are read from that
jurisdiction's own page rather than assumed to match the one you started on.
That second pass is slower and it is the only way the status for the European
member is not just a guess borrowed from the US one.

## Conclusion

A patent listing reads like a short record and is really several coupled
structures pretending to be one row: five different dates that only a label
can tell apart, a family spread across jurisdictions that should not be merged
into one status, a claim set with an internal dependency graph, a
classification that is a set rather than a single tag, and two roles, inventor
and assignee, that can diverge the moment a patent changes hands. Bind every
field to its label, keep the family and the claims structured instead of
flattened, and write down the scrape's own timestamp next to legal status,
because that field is true only as of the moment you read it and the page will
not tell a later reader when that was.

## Short answers to the questions that lead here

**Why do two dates on the same patent page look unlabeled or ambiguous?**
Because offices print filing, publication, grant, priority, and expiry dates
in whatever order their template uses, and not every page labels all five.
Match on the label text through an alias table instead of reading a column by
position, so a reordered or partially-labeled row does not silently swap two
meanings.

**Should I merge family members into one patent record?** No. Keep one record
per family with a list of members underneath it, each carrying its own
jurisdiction, publication number, and legal status. Merging them loses the
country-specific status that is usually the reason someone is looking at the
family at all.

**How do I know if a legal status value is still accurate?** You cannot,
unless you wrote down when you read it. Store `status_as_of` next to `status`
in every row; the page itself rarely prints an as-of date, so the scrape has
to supply one.

**Is it fine to store claims as one paragraph of text?** Only if the reader
never needs to know which claims are independent or what they narrow. Parsing
claim numbers and their "claim N" back-references into a `depends_on` list
keeps that structure available without much extra code.

**Why keep a list of classification codes instead of one?** Because more than
one CPC or IPC code routinely applies to the same patent, and the codes are
hierarchical. Reading only the first one discards the others, often the more
specific ones, which breaks any later filter by subfield.

**Is the assignee shown today the same as who filed the patent?** Not
necessarily. Patents get reassigned after grant, sometimes more than once.
Track `current_assignee` and `original_assignee` as separate fields instead of
one, so a transfer is visible rather than silently overwritten.

## Sources

- WIPO Standard ST.60, the bibliographic data element recommendation that
  defines filing, publication, priority, and grant dates as distinct elements,
  which is why a page's date table has that many rows to begin with.
- The CPC classification scheme, jointly maintained by the EPO and the USPTO,
  which documents the hierarchical, multi-code structure that a single
  first-listed code does not represent.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`locator`](https://playwright.dev/python/docs/api/class-page#page-locator)
  APIs, used exactly as documented upstream, retrieved 2026-08-28. The browser
  returned by this library is a real Playwright `Browser`.

**See also:** [how to clean scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for parsing the date formats this page's `parse_date_cell` only sketches,
[incremental scraping: only new items since last run](how-to-scrape-only-new-items-incremental-playwright.md)
for re-checking legal status without re-scraping an entire family from
scratch, [how to scrape into a SQLite database](how-to-scrape-into-a-database-playwright.md)
for storing one family with many jurisdiction-specific member rows, and
[how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the family-tab request pattern used above.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A legal-status
column scraped once and reused for months read "active" on a family member
that had lapsed nearly a year earlier, because nothing in the row recorded
when it had been read.*
