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

## A complete run over a named case list

```python
import json, time
from invisible_playwright import InvisiblePlaywright

CASES = ["2026-CV-001234", "2026-CV-001987"]      # cases you can name and justify
PACE_SECONDS, DAILY_CAP = 6, 200

def already_done(path):
    seen = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                seen.add(json.loads(line)["case_number"])
    except FileNotFoundError:
        pass
    return seen

path = "dockets.jsonl"
done = already_done(path)
budget = DAILY_CAP

with InvisiblePlaywright(seed=42) as browser, open(path, "a", encoding="utf-8") as out:
    page = browser.new_page()
    for case_number in CASES:
        if case_number in done or budget <= 0:
            continue
        found = open_case(page, case_number)
        record = {
            "case_number": case_number,
            "observed_at": time.time(),
            "found": found,
            "entries": docket_entries(page) if found else [],
        }
        if not found:
            note = page.query_selector(".case-not-found")
            record["not_found_text"] = note.inner_text().strip() if note else None
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        budget -= 1
        time.sleep(PACE_SECONDS)
```

The daily cap is written into the loop rather than left to discipline. A court portal that
decides you are abusive does not send a warning, and the cap is what keeps a long research
project from ending on its second day.

Keeping the `not_found_text` verbatim matters more here than elsewhere: sealed, expunged,
transferred and mistyped all produce different wording, and that wording is the only
evidence you will have about which one it was.

## What these systems do when they decide you are a problem

Court portals sit at the strict end of public infrastructure, and their responses are worth
recognising because two of the three look like ordinary results.

**A silent empty docket.** The case page renders with no entries rather than an error. Since
a genuinely new case also has few entries, the difference is invisible unless you compare
against a previous read. A case that had thirty entries yesterday and none today is a
failed read, not a purged docket:

```python
    if previous.get(case_number) and not record["entries"]:
        record["suspect"] = "entries vanished from a docket that had them"
```

**An interstitial that consumes the click.** Several portals put a terms page in front of
the first search per session and again after a period of inactivity. A run that does not
re-check for it after a pause submits its search into a page that is not the search page.

**A hard block on the address.** This is the honest end state of ignoring the pace, and it
is usually not reversible by waiting a few minutes. The general diagnosis order is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md), but the
specific advice for court systems is different from most targets: the fix is almost always
to slow down and narrow the question, not to look more like a browser.

## The schema, and the retention decision that comes with it

| table | key | holds |
|---|---|---|
| `case` | `court`, `case_number` | caption, type, filed date, current status |
| `entry` | `court`, `case_number`, `sequence` | filed date, verbatim text, document references |
| `run` | `run_id` | what was asked, when, and under what cap |

The `run` table is unusual and worth keeping. Docket research is the kind of work where
someone may later ask what you collected and why, and a log of the queries you made is a
better answer than a reconstruction from the data.

For retention, the defensible default is to keep the entries you analysed and drop the
rest, rather than accumulating dockets because they were cheap to fetch. The value of this
data is in answering a specific question about court activity; the risk in it is that a
general archive of dockets is a general archive about people, and the second grows quietly
out of the first if nobody decides otherwise.

## Short answers to the questions that lead here

**Can I search for cases by name?** This page does not, and that is deliberate. It
looks up cases already identified by number. Bulk identification of people through a
court portal is a different activity with different consequences.

**Why is the docket numbering full of gaps?** Because courts skip numbers, add entries
out of sequence and amend them later. Keep the court's sequence number exactly as
given and never renumber: the gaps are part of the record.

**The document did not download, it opened in a viewer.** That is the normal behaviour
on these systems: attachments open a PDF in a viewer tab instead of triggering a
download, so the download-handling route does not apply.

**A case returned nothing. Does that mean it does not exist?** Not necessarily.
Sealed, expunged and mistyped cases all render as the same absence, so keep the
not-found branch explicit instead of treating it as an empty result.

**See also:** [How to Handle a PDF That Opens in a New Tab with
Playwright](how-to-handle-pdf-opens-new-tab-playwright.md), [Download and read PDFs
linked from a page with Playwright](how-to-scrape-linked-pdfs-playwright.md), [How to
rate limit your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Network, https://playwright.dev/python/docs/network - request
  interception and response capture, checked for the case where a document opens in a
  viewer instead of arriving as a download.
- This project's pages on PDFs that open in a new tab and on scraping linked PDFs,
  which carry the mechanics this page points at instead of repeating them.
