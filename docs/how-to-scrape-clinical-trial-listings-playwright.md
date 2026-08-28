---
title: "How to scrape clinical trial listings with Playwright"
description: "Scrape clinical trial listings with Playwright: timestamp every status read, carry both trial identifiers, keep eligibility criteria as text, and split target enrollment from site-level recruiting status."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 143
---


# How to scrape clinical trial listings with Playwright

To scrape clinical trial listings with Playwright, timestamp every status read since the
status field decays between visits, carry both the registry's own identifier and any
international identifier the same trial carries in a different country's registry, keep
eligibility criteria as a list of text strings instead of parsed logic, store enrollment
as a target figure with its actual-or-anticipated label rather than a headcount, extract
one recruiting status per listed site instead of one per trial, and log the absence of a
results section as unreported rather than unfinished.

A trial listing reads like a fact sheet and behaves like several records glued onto one
page. The status line, the enrollment number, the site table and the results tab each
move on their own schedule, sometimes years apart, and a scrape that visits once and
never comes back is not recording a fact. It is recording whatever those four clocks
happened to say on the day you looked, mislabeled as current.

## Every status is a snapshot, and only a timestamp proves how old

A trial's overall status moves through a small, fixed set of values over its life: not
yet recruiting, recruiting, active but not recruiting, completed, terminated, withdrawn.
That field is the single most consequential thing on the page, because almost every other
decision a downstream user makes, whether to contact a site, whether to count the trial as
finished, depends on it. It is also the field most likely to have changed since your last
visit.

A row that stores "recruiting" with no indication of when that was true is not more
useful than no row at all; it is actively misleading, because a reader has no way to tell
a fresh read from one three years stale. The fix costs one extra field.

```python
from datetime import datetime, timezone
from invisible_playwright import InvisiblePlaywright

VALID_STATUSES = {
    "not yet recruiting", "recruiting", "active, not recruiting",
    "completed", "terminated", "withdrawn",
}

def read_status(page, trial_url):
    page.goto(trial_url, wait_until="domcontentloaded")
    raw = page.locator("[data-field='overall-status']").inner_text().strip().lower()
    return {
        "status": raw,
        "status_checked_at": datetime.now(timezone.utc).isoformat(),
    }

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    row = read_status(page, "https://example-registry.org/study/AB1234")
    if row["status"] not in VALID_STATUSES:
        raise ValueError(f"unrecognised status text: {row['status']!r}")
```

The `ValueError` on an unrecognised value matters more than it looks. A registry can
rename or add a status without warning, and a scraper that silently stores whatever text
it finds will happily record a typo, a translation, or a new category as if it were one of
the six you planned for. Re-checking a trial later, rather than trusting the first read
forever, is the same discipline as [scraping only new or changed
items](how-to-scrape-only-new-items-incremental-playwright.md), applied to a field instead
of a whole listing.

## A trial can carry two identifiers, and they are not interchangeable

Every listing carries the identifier assigned by whichever registry the page belongs to.
Many trials also carry a second, international identifier, issued when the same study is
registered a second time in a different country's registry, usually to satisfy that
country's own regulatory requirement. Both numbers point at the same protocol. Neither one
supersedes the other.

Treat a listing as unique to the registry that served it and you will count the same
trial twice, once under each registry's number, with no field connecting the two rows.
The international identifier is what a deduplication step joins on, and it usually sits
inside the same block that lists any other IDs the sponsor has recorded, not in a field of
its own.

```python
import re

INTERNATIONAL_ID_RE = re.compile(r"^[A-Z]{2,6}-?\d{4,}$")

def looks_like_international_id(text):
    return bool(INTERNATIONAL_ID_RE.match(text.strip()))

def read_identifiers(page):
    registry_id = page.locator("[data-field='registry-id']").inner_text().strip()
    other_ids = [t.strip() for t in page.locator(
        "[data-field='other-study-ids'] li"
    ).all_inner_texts()]

    international_id = next(
        (t for t in other_ids if looks_like_international_id(t)), None
    )
    return {
        "registry_id": registry_id,
        "international_id": international_id,
        "other_ids": other_ids,
    }
```

The regex is a starting point, not a promise. Registries do not agree on a shared format
for the international identifier, so validate a handful of known cross-registered trials
by hand before trusting the pattern across a whole sweep, and expect to widen it.

## Eligibility criteria stay as text, because the logic is not a data structure

Inclusion and exclusion criteria arrive as prose, almost always with an internal shape:
a numbered or bulleted list under an "Inclusion Criteria" heading, another under
"Exclusion Criteria." That internal structure is worth keeping. The medical logic inside
each line is not something a scraper should try to parse.

An age range written as "must be between 18 and 65 years of age" looks like two numbers
waiting to be extracted, until the next criterion reads "diagnosed within the last five
years" or "prior treatment with at least one but no more than three agents," where the
comparison, the units and the exceptions are all doing real work in the sentence. Storing
each criterion as its own string keeps the boundary honest: the scraper's job stops at
splitting the list, and a clinician or a downstream model reads the sentence.

```python
def read_eligibility(page):
    block = page.locator("#eligibility-criteria").inner_text()
    inclusion, exclusion = [], []
    current = None
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("inclusion criteria"):
            current = inclusion
            continue
        if lowered.startswith("exclusion criteria"):
            current = exclusion
            continue
        if current is not None:
            current.append(line.lstrip("-*0123456789. ").strip())
    return {"inclusion_criteria": inclusion, "exclusion_criteria": exclusion}
```

The output is two lists of strings, one per criterion, in the order the page presented
them. That is the whole contract. Anything that wants structured logic out of that text
is a separate, harder project, and pretending the scraper already solved it is how a
downstream query silently drops half the exclusion criteria because a sentence did not
match the pattern someone wrote.

## Enrollment is what the protocol promised, not who showed up

The enrollment number on a listing is, in most cases, the target written into the study
protocol: how many participants the trial is designed to recruit. It is not a running
count of how many people have actually joined so far. Registries that publish an actual
enrollment figure usually label it explicitly, often with a small tag distinguishing
"anticipated" or "estimated" from "actual."

Drop that label while scraping and the number survives, unchanged and now meaning
something else. A completed trial that recruited only 40 of a planned 200 participants
still shows 200 in the field unless the actual count was posted separately with its own
label. Store the count and its label as a pair, and any total built from it later stays
honest about which half of that pair got summed.

## Site-level status is a different field than the trial's status

A single trial listing can name several locations: hospitals, clinics or research centers
where the study actually runs. Each of those sites carries its own recruiting state,
independent of the trial's overall status and independent of every other site on the same
list. A trial marked "recruiting" overall can have three sites still enrolling and four
already closed, and a trial marked "active, not recruiting" can still show a site that has
not updated its own entry.

Collapsing all of that down to the one status printed at the top of the page throws away
exactly the information a reader in a specific city needs: which site near them is
actually open. The location table is usually the plainest structure on the page, and
reading it the same way you would read [an HTML
table](how-to-scrape-html-tables-playwright.md) elsewhere keeps the two levels separate
instead of merged.

```python
def read_sites(page):
    rows = page.locator("table.locations tbody tr")
    sites = []
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td").all_inner_texts()
        if len(cells) < 3:
            continue
        facility, place, site_status = cells[0], cells[1], cells[2]
        sites.append({
            "facility": facility.strip(),
            "location": place.strip(),
            "site_status": site_status.strip().lower(),
        })
    return sites
```

Store `sites` as a nested list under the trial record rather than flattening it into one
row per site. A trial is one study; a site is a fact about where that study runs, and the
two belong at different levels of the same record.

## A missing results section usually means not yet, not never

Registration and results are two different records that happen to share a page.
Registration is written when the trial starts. Results, when they exist at all, are added
long after the trial finishes, often years later, once the sponsor has analyzed the data
and gone through whatever review the registry requires before publishing it. A trial with
no results section is, more often than not, simply a trial whose results have not arrived
yet.

The distinction worth encoding is "not reported" versus "not applicable," and most
registries mark the second case explicitly rather than leaving it to be inferred: a note
attached to the results area stating that results reporting does not apply to this study
type or this outcome. Absent that explicit marker, treat a missing results section on a
completed or terminated trial as pending, not as evidence the study never finished, and
re-check it on the same schedule you use for status.

```python
def read_results_status(page):
    not_applicable = page.locator("[data-field='results-not-applicable']")
    if not_applicable.count() > 0:
        return {
            "results_status": "not_applicable",
            "reason": not_applicable.inner_text().strip(),
        }
    posted = page.locator("[data-field='results-first-posted']")
    if posted.count() > 0:
        return {"results_status": "reported", "results_first_posted": posted.inner_text().strip()}
    return {"results_status": "not_reported"}
```

Three outcomes, not two. Collapsing "not applicable" into "not reported" makes a study
that will never post results look like one you should keep polling forever, which wastes
a re-check cycle on a record that is already complete in every sense that matters.

## Assembling one row per trial, sized for re-checking

Put the pieces together and the record for one trial is a dict with a handful of scalar
fields, two nested lists (eligibility, sites), and a checked-at timestamp that makes the
whole thing safe to overwrite on the next pass instead of trusting it forever. Getting to
that record usually means walking a paginated search listing first to collect trial URLs,
the same [numbered pagination](how-to-scrape-paginated-pages-playwright.md) pattern used
on any other listing page, then visiting each one, which is the general [list-to-detail
crawl](how-to-crawl-list-to-detail-pages-playwright.md) shape.

```python
from invisible_playwright import InvisiblePlaywright

def read_enrollment(page):
    count = page.locator("[data-field='enrollment-count']").inner_text().strip()
    kind = page.locator("[data-field='enrollment-type']").inner_text().strip().lower()
    return {"enrollment_count": int(count), "enrollment_type": kind}  # "anticipated" or "actual"

def collect_trial_urls(page, search_url):
    urls = []
    page.goto(search_url, wait_until="domcontentloaded")
    while True:
        urls.extend(page.locator("a.trial-result-link").evaluate_all("els => els.map(e => e.href)"))
        next_link = page.locator("a[rel='next']")
        if next_link.count() == 0:
            break
        next_link.click()
        page.wait_for_load_state("domcontentloaded")
    return urls

def scrape_trial(page, url):
    record = {"url": url}
    record.update(read_status(page, url))
    record.update(read_identifiers(page))
    record.update(read_eligibility(page))
    record["enrollment"] = read_enrollment(page)
    record["sites"] = read_sites(page)
    record.update(read_results_status(page))
    return record

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    trial_urls = collect_trial_urls(page, "https://example-registry.org/search?cond=example")
    rows = [scrape_trial(page, url) for url in trial_urls]
```

Write those rows into whatever store keeps history rather than only the latest value, so
a status change or a newly posted results record shows up as a new row instead of erasing
the old one. That is the same upsert-with-history shape covered in [scraping into a
database](how-to-scrape-into-a-database-playwright.md), and it is what makes the
`status_checked_at` field from the first section actually pay for itself later.

## Conclusion

A clinical trial listing is not one fact, it is several records at different ages sharing
a page. Status decays and needs a re-check timestamp. The registry-specific identifier and
the international identifier both belong on the row, because a dedup step across
registries needs the second one. Eligibility criteria stay as text, because the logic
inside them is not something a scraper should parse. Enrollment needs its label kept
alongside the number, or a target quietly becomes a headcount. Site status is a separate,
finer field than trial status. And a missing results section is usually a timing gap, not
a dead end, unless the page says otherwise. Build the row to be overwritten safely on the
next pass, and the pipeline stays honest about how current every field actually is.

## Short answers to the questions that lead here

**Why does the same trial show up twice with two different ID numbers?** It is registered
in two registries, once under each registry's own numbering, and one of those numbers is
the international identifier that links the two rows. Join on that identifier rather than
treating each registry's number as unique to one trial.

**Should I parse the eligibility criteria into structured rules?** No. Keep each criterion
as its own text string in an inclusion list and an exclusion list. The medical logic
inside the sentences is a separate problem from scraping the page.

**Is the enrollment number the actual number of participants?** Usually not. It is most
often the target written into the protocol, and it is only an actual count when the page
labels it that way. Store the label with the number.

**A trial shows one status but a location page shows a different one for a specific
site. Which is right?** Both, because they are different fields. Trial-level status and
site-level status move independently, and a site can close or open without the overall
trial status changing.

**A completed trial has no results section. Is that a bug in my scraper?** Probably not.
Results are added long after registration, sometimes years later, so a completed trial can
legitimately show no results yet. Look for an explicit "not applicable" marker before
assuming the study never finished.

**How often should I re-scrape a trial once I have it?** Often enough that the
`status_checked_at` field stays meaningful for your use case. A recruiting trial changes
state more often than a completed one, so a fixed schedule for every trial wastes cycles
on the ones that rarely move and under-checks the ones that do.

## Sources

- Playwright's [`Locator`](https://playwright.dev/python/docs/api/class-locator) API,
  used exactly as documented upstream for `count()`, `inner_text()` and
  `all_inner_texts()`, since the browser this library returns is a real Playwright
  `Browser`.
- Playwright's
  [`wait_for_load_state`](https://playwright.dev/python/docs/api/class-page#page-wait-for-load-state),
  used to detect the end of a pagination click before reading the next page's rows.
- This page describes registry mechanics in generic terms on purpose: field names,
  status vocabularies and identifier formats differ between registries, so verify the
  selectors above against the specific registry you are reading before trusting them
  at scale.

**See also:** [crawling a list to its detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
for the general listing-to-detail shape used in the last section,
[scraping numbered pagination](how-to-scrape-paginated-pages-playwright.md) for walking a
multi-page search result, [scraping only new or changed items](how-to-scrape-only-new-items-incremental-playwright.md)
for turning a re-check timestamp into an actual incremental run, and
[scraping into a database](how-to-scrape-into-a-database-playwright.md) for storing a row
that gets safely overwritten on the next pass.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this
pipeline treated every completed trial with no results tab as abandoned and dropped it
from the dataset, right up until one of those exact identifiers turned up eighteen months
later with a results record attached, under a status that had never stopped saying
"completed."*
