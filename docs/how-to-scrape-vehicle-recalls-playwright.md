---
title: "How to scrape vehicle recall notices with Playwright"
description: "Scrape vehicle recall notices with Playwright: key each row to the campaign identifier and its VIN or build-date range, carry the notice revision so amendments update, and budget the VIN lookup separately."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 123
---


# How to scrape vehicle recall notices with Playwright

**To scrape vehicle recall notices with Playwright, key every row to the campaign
identifier plus the range it applies to, a VIN range or a build-date range and never a
model name, carry the notice revision so an amended notice updates that row instead of
arriving as a second recall, keep the manufacturer decision date, the publication date
and the owner notification date in three separate columns, and run the VIN lookup as its
own endpoint with its own much smaller request budget.** The extraction is the easy half.
The row shape is where this dataset is won or lost.

A recall page reads like a list of models with a date and a paragraph of prose. Store it
that way and the dataset answers nothing anyone asks. A recall applies to a set of vehicles
named by a campaign identifier and bounded by a VIN range or a window of build dates. Two
cars of the same model, year and trim can sit on opposite sides of that boundary, and only
the boundary says which one is affected.

The rest of this page is the parsing that keeps that boundary, the key that survives an
amendment, the three dates that get collapsed into one, and the second endpoint that will
throttle you long before the list does.

## A recall is a campaign plus a range, not a model

The campaign identifier is the primary key the source already uses, so use it too. Every
notice carries one: a manufacturer campaign code, a regulator reference number, or both.
Model names are a description printed for readers, not the applicability rule. They are
often a comma-joined string covering several nameplates, and the wording changes between
the list page and the detail page of the same notice.

Store the range beside the identifier in four columns, not one: a kind, a start, an end,
and the raw sentence you read them out of. That last column is what saves you later,
because ranges are written in prose and your parser will get some of them wrong. Keeping
the source text means a fixed parser can be re-run over stored rows instead of re-crawling
the site.

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class RecallRow:
    source: str                 # which regulator or publisher this row came from
    campaign_id: str            # the notice identifier, exactly as printed
    revision: str               # amendment marker, or one derived below
    manufacturer: str
    models: str                 # descriptive only, never part of the key
    component: str
    defect: str
    remedy: str
    status_raw: str             # the exact word the source used
    status_mapped: str          # your vocabulary, through a stated mapping
    applies_kind: str           # "vin_range", "build_date_range" or "unparsed"
    applies_start: Optional[str]
    applies_end: Optional[str]
    applies_raw: str            # the sentence the range was read out of
    decision_date: Optional[str] = None
    published_date: Optional[str] = None
    owner_notified_date: Optional[str] = None
    date_label_seen: str = ""   # the words the page printed above the date
    units_affected: Optional[int] = None
    detail_url: str = ""
    captured_at: str = ""
```

One field deserves its own warning. `units_affected` is a number the notice states, and it
is never derived from the range. VIN sequences are not dense, so the arithmetic distance
between a start VIN and an end VIN is not a vehicle count and is often wrong by an order of
magnitude.

## Read the list, then open the notice and read it by label

The list page carries a summary. The range, the remedy and the dates usually live on the
notice detail page, or in the JSON that page fetches to build itself. Crawl list to detail
and prefer the JSON when it exists, since the same fields arrive already separated. The
general shape of that walk is in
[crawling list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md), and
the response hook is covered in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

When you do read the DOM, read it as label-to-value pairs rather than by position. Notices
are rendered as definition lists or two-column tables whose row order differs between
sources and between notices from the same source. Positional extraction puts the publication
date in the decision date column on the first notice that adds a row, and nothing errors.

```python
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from invisible_playwright import InvisiblePlaywright

def read_labelled_fields(page):
    """Read the notice as label -> value, never by row position."""
    pairs = {}
    rows = page.locator("dl > div, table.notice tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        label = row.locator("dt, th").first.inner_text().strip()
        value = row.locator("dd, td").first.inner_text().strip()
        if label:
            pairs[label.rstrip(":").lower()] = value
    return pairs

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/recalls?page=1", wait_until="domcontentloaded")

    links = page.locator("a.recall-detail")
    detail_urls = [links.nth(i).get_attribute("href") for i in range(links.count())]

    notices = []
    for url in detail_urls:
        payload = None
        try:
            with page.expect_response(
                lambda r: "/api/recall" in r.url and r.request.resource_type in ("xhr", "fetch"),
                timeout=8000,
            ) as caught:
                page.goto(url, wait_until="domcontentloaded")
            payload = caught.value.json()
        except PlaywrightTimeout:
            pass                      # server-rendered notice: the DOM is the source
        notices.append({
            "url": url,
            "json": payload,
            "fields": read_labelled_fields(page),
        })
```

Keep both paths in one scraper. A single source often serves old notices as static HTML and
recent ones through the API, and the label walk covers the old ones without a second job.

## Parse the range without guessing what it means

Ranges arrive in three shapes: an explicit VIN start and end, a window of build dates, and a
sentence that names neither. Parse the first two, mark the third as unparsed, and never
convert one into the other. A build-date window is not a VIN range, and turning it into one
requires production records you do not have.

Do not expand a VIN range into a list of VINs either. The sequential part of a VIN is not
contiguous across a production run, and the check digit makes incrementing a VIN produce
strings that are not valid VINs at all.

```python
import re

VIN_CHARS = "[A-HJ-NPR-Z0-9]"          # I, O and Q are not used in VINs
VIN_RANGE = re.compile(rf"\b({VIN_CHARS}{{17}})\b.{{0,40}}?\b({VIN_CHARS}{{17}})\b", re.S)
DATE_RANGE = re.compile(
    r"(?:built|manufactured|produced)\s+(?:between|from)\s+(.+?)\s+(?:and|to|through)\s+([^.;]+)",
    re.I,
)

def parse_range(text):
    """Return (kind, start, end, raw). Never invent a range you did not read."""
    match = VIN_RANGE.search(text)
    if match:
        return "vin_range", match.group(1), match.group(2), match.group(0)
    match = DATE_RANGE.search(text)
    if match:
        return "build_date_range", match.group(1).strip(), match.group(2).strip(), match.group(0)
    return "unparsed", None, None, text.strip()

def _vin_prefix(vin):
    # Positions 1-8 and 10-11 identify the line, the model year and the plant.
    # Position 9 is the CHECK DIGIT and differs between two VINs of one sequence,
    # so comparing the first eleven characters wholesale rejects valid matches.
    return vin[:8] + vin[9:11]

def vin_in_range(vin, start, end):
    """True, False, or None when the comparison is not meaningful."""
    if not (len(vin) == len(start) == len(end) == 17):
        return None
    if _vin_prefix(vin) != _vin_prefix(start) or _vin_prefix(start) != _vin_prefix(end):
        return None                    # different plant or model year: not comparable
    return start[11:] <= vin[11:] <= end[11:]
```

That third return value is the point. `vin_in_range` answers `None` far more often than it
answers `False`, and folding the two together is how a vehicle gets reported clear when the
truth is that the notice does not say. Only the last six characters are ordered, and only
inside one plant's sequence.

## Three dates, and the page shows you one

Every recall has at least three dates that mean different things, and most pages print one
of them under a label as vague as "date".

| Date | What it marks | Why it is not the others |
|---|---|---|
| Manufacturer decision date | The day the manufacturer determined a defect exists | The earliest of the three, and the one regulatory deadlines count from |
| Publication date | The day the notice appeared on the regulator's site | Moves when a notice is amended, so it is not a stable event date |
| Owner notification date | The day letters went out, or are scheduled to | Often in the future when you scrape it, and often absent entirely |

Assign by the label you actually read, and leave the other two null. A null is recoverable
on the next pass; a publication date sitting in the decision date column is not, because
nothing downstream can tell it apart from a real one. Store the label string too, so an
ambiguous source can be re-mapped in bulk once you learn what its wording means. Format
parsing is the smaller problem and it is covered in
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

The gap between these dates is also information. A decision date months before a publication
date is a real, measurable interval, and it disappears the moment the two are merged into a
single column called `date`.

## Status words are per-regulator and not comparable

Status is the field most likely to produce a confident, wrong chart. One regulator's "open"
means the campaign is active, another's means the remedy is not yet available, and a third
publishes completion percentages instead of a status at all. None of those are the same
claim, and none of them are comparable until you write the mapping down.

Keep `status_raw` exactly as printed, always. Then map into your own small vocabulary and
ship the mapping table next to the data, so anyone counting rows can see what was folded
together.

| Raw wording seen | Internal state | What it does not mean |
|---|---|---|
| open, active, ongoing | `active` | Nothing about whether a fix exists yet |
| remedy available, parts available | `remedy_available` | Not that any vehicle has been repaired |
| incomplete, not repaired | `active` | A per-vehicle answer, not a campaign state |
| closed, completed | `closed` | Not that every vehicle was fixed |
| superseded, replaced by | `superseded` | The successor campaign id belongs in its own column |

A row whose raw status does not match the table gets `unmapped`, not a best guess. An
unmapped row is a question you can answer later. A guessed row is a wrong answer that looks
like every other row.

## Amendments: the key needs a revision

Recall notices are amended after publication. The remedy changes when the first fix does not
hold, the affected range widens when more production is implicated, and the unit count moves.
This breaks both of the obvious keys. Key on the campaign identifier alone and each amendment
silently overwrites the previous text, destroying the history that made the change visible.
Key on the content and the amendment arrives as a brand new recall, inflating every count you
publish.

Key on source, campaign identifier and revision together, and keep a hash of the fields that
matter so an unlabelled edit is still caught. Many sources print a revision or an amendment
date; the ones that do not still change their text, and the hash is what notices.

```python
import hashlib
import json

MATERIAL_FIELDS = ("defect", "remedy", "component", "status_raw",
                   "applies_kind", "applies_start", "applies_end", "units_affected")

def content_hash(row):
    blob = json.dumps({name: getattr(row, name) for name in MATERIAL_FIELDS},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

def upsert(store, row):
    """store maps (source, campaign_id) to a list of revisions, newest last."""
    history = store.setdefault((row.source, row.campaign_id), [])
    digest = content_hash(row)
    if history and history[-1]["hash"] == digest:
        history[-1]["last_seen"] = row.captured_at      # unchanged: touch, do not append
        return "unchanged"
    revision = row.revision or str(len(history) + 1)    # derive one if none is printed
    history.append({"revision": revision, "hash": digest, "row": row,
                    "first_seen": row.captured_at, "last_seen": row.captured_at})
    return "amended" if len(history) > 1 else "new"
```

The three return values are worth logging separately. A run that reports thousands of new
rows on a source you already crawled is not reporting a busy week at the regulator, it is
reporting that your identifier extraction moved. Pairing this with a
[high-water mark so you only read the new notices](how-to-scrape-only-new-items-incremental-playwright.md)
works, with one caveat specific to recalls: an amendment does not appear at the top of the
feed, so a pure newest-first stop condition never sees it. Re-read open campaigns on a slower
cycle alongside the incremental pass.

## VIN lookup is a different endpoint with a much smaller budget

Lookup by VIN is almost always a separate form on its own URL, posting to its own endpoint,
and it is throttled far harder than the list. That is not arbitrary. The list is a cached
page of published notices, while the VIN query joins one vehicle against campaign ranges and
sometimes against repair records, so it costs the provider real work per call. Treat it as a
second scraper with a second budget rather than another page in the same loop.

Fill it by label and submit by role, the way any
[search form scrape](how-to-scrape-search-results-form-playwright.md) should, capture the
response instead of re-reading the repainted panel, and cache negative answers as carefully as
positive ones. A VIN with no open campaigns is a result, and asking twice spends budget to
learn nothing.

```python
import time
from playwright.sync_api import TimeoutError as PlaywrightTimeout

def lookup_vin(page, vin, budget, cache, rng):
    if vin in cache:
        return cache[vin]                     # negative answers are cached too
    if budget["remaining"] <= 0:
        raise RuntimeError("VIN lookup budget spent: stop rather than push through")

    page.goto("https://example.com/recalls/vin", wait_until="domcontentloaded")
    page.get_by_label("VIN").fill(vin)

    try:
        with page.expect_response(lambda r: "/api/vin" in r.url, timeout=20000) as caught:
            page.get_by_role("button", name="Search").click()
        response = caught.value
    except PlaywrightTimeout:
        budget["remaining"] -= 1
        return None

    budget["remaining"] -= 1
    if response.status in (403, 429):
        time.sleep(rng.uniform(30, 90))       # this endpoint gives up sooner than the list
        return None

    campaign_ids = [item["campaign_id"] for item in response.json().get("recalls", [])]
    cache[vin] = campaign_ids                 # keep the ids, not the owner-facing payload
    page.wait_for_timeout(rng.randint(4000, 11000))
    return campaign_ids
```

Say the data part plainly, because it is the part people skip. A VIN identifies one physical
vehicle. Joined with registration, warranty or owner records it becomes personal data in
several jurisdictions, and a scrape that stores the whole lookup response usually stores more
of that join than the task needs. Keep the campaign ids and the date you asked. Drop the rest,
do not collect VINs you were not asked about, and do not build a VIN list by enumerating
sequences. Enumeration is also the pattern that gets an endpoint closed for everyone, and the
[pacing rules for a shared budget](how-to-rate-limit-your-scraper-playwright.md) apply here at
a much tighter setting than on the list.

## Cross-region duplicates: the identifier finds too few, the text finds too many

One physical defect is regularly issued as several campaigns, one per region, each with its
own identifier, its own dates and its own VIN range covering the vehicles sold there. This
breaks deduplication from both directions at once. Match on the identifier and you find
almost no duplicates, so a count of distinct defects comes out inflated, with the same
steering fault counted four times. Match on the description and you find far too many,
because notice text is boilerplate and two unrelated campaigns about a fastener read nearly
identically.

Neither is a merge you should perform automatically. Keep every campaign as its own row,
since the ranges and the remedy timing genuinely differ per region, and add a nullable group
id that is a review artifact rather than a key.

```python
def defect_group_key(row, month_bucket):
    """A candidate grouping. Never a primary key, never an automatic merge."""
    return (
        row.manufacturer.strip().lower(),
        row.component.strip().lower(),     # the regulator's component code where there is one
        month_bucket,
    )

def group_candidates(rows, window_months=6):
    buckets = {}
    for row in rows:
        if not row.published_date:
            continue
        year, month = int(row.published_date[:4]), int(row.published_date[5:7])
        bucket = ((year * 12 + month) // window_months)     # wide on purpose
        buckets.setdefault(defect_group_key(row, bucket), []).append(row)
    # only groups spanning more than one source are interesting as candidates
    return {key: rows_ for key, rows_ in buckets.items()
            if len({r.source for r in rows_}) > 1}
```

The window is deliberately wide because regional filings for one defect can land months
apart, and a narrow bucket hides exactly the pairs worth reviewing. Over-generating candidates
costs a person some reading. Under-generating them produces a clean number that is wrong, and
nothing in the pipeline will ever contradict it.

## Conclusion

Recall data punishes a convenient row shape more than most. The campaign identifier and its
range are the whole record, so a table keyed on model names is a summary of a summary. The
revision is what keeps an amended notice from arriving twice. The three dates are three
columns, and one label read correctly beats three guessed. Status needs a written mapping
before any two sources can be counted together, and cross-region duplicates need review
rather than a merge rule, because both obvious rules fail in opposite directions. The VIN
lookup is a separate scraper on a separate budget, holding the smallest amount of vehicle
data the task can work with. Get the row shape right and the extraction is a morning's work.
Get it wrong and every downstream number is confidently incorrect.

## Short answers to the questions that lead here

**What should the primary key of a recall row be?** The source, the campaign identifier and
the revision, together. The campaign id alone loses history when a notice is amended, and
anything model-based is not a key at all, since one campaign covers several models and one
model appears in many campaigns.

**Can I just store which models are recalled?** No. The recall applies to a VIN range or a
build-date window, so two identical-looking cars can fall on opposite sides of it. Storing
only the model discards the one field that answers whether a given vehicle is affected.

**Why does my recall count go up every time I re-scrape?** Amended notices. The remedy text
or the affected range changed, so a content-based key treats the amendment as a new recall.
Key on the campaign id plus a revision and hash the fields that matter to catch unlabelled
edits.

**Why is the VIN lookup blocking me when the list works fine?** It is a different endpoint
with a much lower limit, because a per-vehicle query is expensive to answer while the list is
a cached page. Give it its own budget, cache negative answers, and back off on the first 429
instead of retrying.

**How do I dedupe the same defect across regions?** Carefully, and not automatically. Matching
identifiers finds almost nothing because each region issues its own, matching descriptions
matches unrelated campaigns because the text is boilerplate. Group on manufacturer, component
and a wide date window, then review the candidates.

**Is a VIN personal data?** On its own it identifies a vehicle, but combined with owner,
registration or warranty records it is treated as personal data in several jurisdictions.
Keep only what the task needs, which is usually the campaign ids and the date you asked, and
do not enumerate VINs.

## Sources

- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`Response.json`](https://playwright.dev/python/docs/api/class-response#response-json),
  retrieved 2026-08-28, used exactly as documented upstream: the browser this library returns
  is a real Playwright `Browser`.
- Playwright's [`get_by_label`](https://playwright.dev/python/docs/api/class-page#page-get-by-label)
  and [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  retrieved 2026-08-28, which is how the VIN form above is filled and submitted without a
  positional selector.
- Playwright's [Locator semantics](https://playwright.dev/python/docs/api/class-locator),
  retrieved 2026-08-28: a Locator re-resolves its selector on every use, which is what makes
  the label-to-value walk safe on a notice page that re-renders after load.
- The VIN field layout the `vin_in_range` helper depends on: seventeen characters, with the
  check digit at position nine and the sequential serial in the last six, which is why the
  prefix comparison skips position nine.

**See also:** [crawling list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
for the list-to-notice walk, [scraping a search results form](how-to-scrape-search-results-form-playwright.md)
for the VIN lookup form itself, [handling 403 and 429 mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md)
for the backoff the lookup endpoint will demand, and
[scraping into a database](how-to-scrape-into-a-database-playwright.md) for storing the
revision history the upsert above produces.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this keyed
rows on the model name and the single date the page printed, so a widened VIN range came back
as a second recall and the same defect was counted twice for a month before anyone noticed.*
