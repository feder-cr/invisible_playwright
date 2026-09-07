---
title: "How to scrape court docket listings with Playwright"
description: "Scrape public court dockets with Playwright: work case by case rather than by enumeration, capture the docket entries as an ordered record, and handle the documents that open in a viewer."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 160
---


# How to scrape court docket listings with Playwright

To scrape a public docket, work from **case numbers you already have** and capture the
docket entries as an ordered list. A docket is a chronological record of filings, and its
value is the sequence: what was filed, when, by which party, and what the court did next.
The case summary at the top is a rollup of that sequence and loses the timing.

The other thing to settle before writing code is scope. Court systems publish these
records deliberately, and they also rate limit hard, because enumeration is a known abuse
pattern. A scraper that walks a case number space is doing the thing the defences exist
for, and it will be stopped. One that resolves a list of cases it has a reason to hold
behaves like a researcher and generally works.

This page covers driving the case lookup, reading the docket sheet, and dealing with the
documents attached to entries.

## Look up cases you already identified

```python
from invisible_playwright import InvisiblePlaywright

def open_case(page, case_number):
    page.goto("https://courts.example.gov/case-search")
    page.fill("input#caseNumber", case_number)
    page.click("button#search")
    page.wait_for_selector("#docketSheet, .case-not-found")
    return page.query_selector("#docketSheet") is not None

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for case_number in known_cases:
        if open_case(page, case_number):
            rows = docket_entries(page)
```

Keep the not-found branch explicit. Sealed, expunged and simply mistyped cases all render
as an absence, and conflating them with an empty docket produces a dataset that quietly
asserts cases had no activity.

## The docket sheet is an ordered record

```python
def docket_entries(page):
    entries = []
    for tr in page.query_selector_all("#docketSheet tbody tr"):
        cells = [td.inner_text().strip() for td in tr.query_selector_all("td")]
        if len(cells) < 3:
            continue
        entries.append({
            "sequence": cells[0],
            "filed": cells[1],
            "text": cells[2],
            "documents": [a.get_attribute("href")
                          for a in tr.query_selector_all("a[href]")],
        })
    return entries
```

Keep the court's sequence number as given, and do not renumber. Courts skip numbers, add
entries out of order after the fact, and use suffixes such as `12-1` for related filings.
Your own index built by enumeration will silently disagree with every citation anyone
writes against that docket.

Store `text` verbatim. Docket text is terse legal shorthand written by clerks, and any
normalisation loses information that a lawyer reading the row will need.

## Documents open in a viewer, not as a download

Attachments on these systems usually open a PDF in a viewer tab rather than downloading.
That is a specific mechanic worth handling deliberately, and it is the same one described
in [when a PDF opens in a new tab](how-to-handle-pdf-opens-new-tab-playwright.md).

Where documents are behind a paywall or a per-page fee, which is common, stop at the
metadata. The entry text plus the document reference is usually enough to answer questions
about activity and timing, and it avoids both the cost and the licensing question that
comes with mirroring court documents. If you do fetch documents you are entitled to,
[downloading linked PDFs](how-to-scrape-linked-pdfs-playwright.md) covers doing it through
the browser session rather than a side client.

## Pace it slowly and deliberately

Court portals are among the strictest rate limiters in public web infrastructure, and for
good reason. A pace of one case every several seconds, with a hard daily cap, is both
respectful and the pace that keeps working:

```python
    import time
    for case_number in known_cases[:200]:        # a cap, not a full sweep
        ...
        time.sleep(5)
```

The reasoning behind self-imposed limits, and why they beat waiting to be blocked, is in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md). Run
overnight in the court's timezone where you can: these systems are used by people doing
their jobs during the day.

## The parts to leave alone

Three things on these systems are worth naming as out of scope, not because they are hard
but because taking them is the difference between research and harm.

**Do not enumerate.** Walking a case number range harvests the cases of people who are not
your subject, including sealed matters that leak through misconfiguration.

**Do not aggregate people.** A docket is public; a compiled profile of every case a named
individual appears in is a different artefact with different consequences, and several
jurisdictions treat it as such in law.

**Do not re-publish documents wholesale.** Court records frequently contain personal
identifiers that the court expects a human reader to see one at a time.

Scraping dockets for a defined question, on cases you can name, is the version of this
that is both legal in most places and defensible. Store it in something queryable such as
[a SQLite database](how-to-scrape-into-a-database-playwright.md), keep the raw entry text,
and keep the run's own metadata so you can say later exactly what you asked and when.
