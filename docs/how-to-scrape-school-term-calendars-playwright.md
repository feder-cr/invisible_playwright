---
title: "How to scrape school term calendars with Playwright"
description: "Scrape school term dates with Playwright: prefer the calendar feed the page links to, expand date ranges into days, and keep the difference between a closure and a staff day."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 166
---


# How to scrape school term calendars with Playwright

To scrape term dates, look for the **calendar feed** before parsing the page. Schools and
districts publish the same information three ways: an HTML table, a PDF, and very often an
iCalendar subscription for parents. The feed is structured, dated, and already carries the
distinction between an all-day closure and a timed event, which the HTML table usually
loses.

When there is no feed, the work is expanding ranges. Term calendars are written as spans,
"14 October to 18 October", and almost every question people ask of this data is about a
single day. Storing the span and expanding at read time is fine; storing only the span text
is not.

This page covers finding the feed, expanding ranges correctly, and keeping the category of
each non-teaching day.

## Look for the feed first

```python
from urllib.parse import urljoin
from invisible_playwright import InvisiblePlaywright

FEED = ("a[href$='.ics'], link[type='text/calendar'], "
        "a[href*='webcal'], a[href*='ical']")

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-school.org/term-dates")

    node = page.query_selector(FEED)
    if node:
        feed_url = urljoin(page.url, node.get_attribute("href"))
        page.goto(feed_url.replace("webcal://", "https://"))
        ics_text = page.inner_text("pre") or page.content()
```

Fetch the feed inside the same browser rather than with a separate client. It is usually
served from the same host with the same protection, and the reasoning is the one that
applies to any secondary document: a side request is a fresh visitor with none of the
context the page accumulated.

Where the link points at a PDF instead, that is a different job with its own mechanics, in
[downloading and reading linked PDFs](how-to-scrape-linked-pdfs-playwright.md).

## Expanding a range needs the inclusive end

```python
from datetime import date, timedelta

def expand(start: date, end: date, label: str):
    day = start
    while day <= end:                 # inclusive: the last day is a holiday too
        yield {"date": day.isoformat(), "label": label}
        day += timedelta(days=1)
```

The inclusive comparison is the bug that shows up every single time. A half-term written
as "14 October to 18 October" is five days, and an exclusive loop returns four, silently
sending a child to school on the Friday.

Weekends inside a term-time range are also not closures in the useful sense, so decide once
whether your expansion emits them and record the choice, rather than leaving each consumer
to guess.

## Keep the category, because not all closures are the same

Term calendars mix several kinds of non-teaching day, and parents care about the
difference:

- **Term dates**: the boundaries of teaching.
- **Half term and holidays**: school closed, most childcare closed.
- **Staff training days**: school closed to pupils, often not to staff, and frequently
  moved by an individual school inside a district-wide calendar.
- **Occasional closures**: weather, elections, building work. These appear late and vanish
  from the page afterwards.

```python
        {"date": "2026-10-14", "kind": "half_term", "raw_label": "October half term"}
```

Keep `raw_label` next to your normalised `kind`. School sites write these labels in local
vocabulary that a fixed mapping will not cover, and the raw string is what lets a human
resolve an unfamiliar one later.

## District calendars and per-school overrides

Where a district publishes a calendar and individual schools publish their own, the school
page overrides on training days specifically. Scrape both and keep the source per row:

```python
        {"date": "2026-11-03", "kind": "staff_day", "source": "school", "school_id": "1234"}
```

Merging them into one calendar without a source column produces a dataset that disagrees
with itself on exactly the dates people need most, with no way to tell which row is
authoritative.

## These pages change once a year, and then quietly

Term dates for the following year are usually published months ahead and then adjusted.
A weekly pass is generous; the useful trigger is a change in the page rather than the
calendar. Hashing the extracted rows and only writing when the hash moves keeps the history
small and makes the amendments visible:

```python
    import hashlib, json
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
```

Store each version rather than overwriting, so "when did they move the training day" stays
answerable. [JSON Lines](how-to-scrape-to-json-lines-playwright.md) suits this well, and
the pacing is trivially inside anything
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) recommends.

## A note on scope

School sites are small, often run by one person, and contain material about children.
Take the calendar, which is published for parents, and leave the rest. There is rarely a
reason for a scraper to touch newsletters, staff lists or photo galleries, and taking only
the pages that answer your question is both the polite and the defensible default.

## A complete run across several schools

```python
import hashlib, json, time
from urllib.parse import urljoin
from invisible_playwright import InvisiblePlaywright

SCHOOLS = {
    "1234": "https://example-school.org/term-dates",
    "5678": "https://another-school.example/calendar",
}

def read_calendar(page, school_id, url):
    page.goto(url, wait_until="domcontentloaded")
    node = page.query_selector(FEED)
    if node:
        feed_url = urljoin(page.url, node.get_attribute("href")).replace("webcal://", "https://")
        page.goto(feed_url)
        raw = page.inner_text("pre") if page.query_selector("pre") else page.content()
        return {"school_id": school_id, "format": "ics", "raw": raw}

    pdf = page.query_selector("a[href$='.pdf']")
    if pdf and not page.query_selector("table.term-dates"):
        return {"school_id": school_id, "format": "pdf-only",
                "pdf_url": urljoin(page.url, pdf.get_attribute("href"))}

    rows = []
    for tr in page.query_selector_all("table.term-dates tbody tr"):
        cells = [td.inner_text().strip() for td in tr.query_selector_all("td")]
        if len(cells) >= 2:
            rows.append({"raw_label": cells[0], "raw_dates": cells[1]})
    return {"school_id": school_id, "format": "html", "rows": rows}

with InvisiblePlaywright(seed=42) as browser, open("terms.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    for school_id, url in SCHOOLS.items():
        record = read_calendar(page, school_id, url)
        payload = json.dumps(record.get("rows") or record.get("raw") or "", sort_keys=True)
        record["digest"] = hashlib.sha256(payload.encode()).hexdigest()
        record["observed_at"] = time.time()
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(3000)
```

The three-way branch is the point. A feed, an HTML table and a PDF-only page are different
sources with different reliability, and recording which one you read is what lets you
prefer the feed next time and flag the PDF-only schools for a human.

## These sites fail by being small, not by defending

Almost nothing here is an anti-bot problem. The failures are the ones small sites have.

**The page is a link to a document.** Many schools publish the calendar only as a PDF or as
an image of a table. The code above detects that rather than returning an empty row set,
which is what a table-only parser does. Handling the document itself is
[downloading and reading linked PDFs](how-to-scrape-linked-pdfs-playwright.md).

**The calendar is a third-party embed.** Term dates frequently live inside an iframe from a
calendar provider. A selector against the top document finds nothing while the dates are
plainly on screen, and the fix is to read inside the frame:

```python
    frame = next((f for f in page.frames if "calendar" in (f.url or "")), None)
    rows = frame.query_selector_all(".event") if frame else []
```

**The site is seasonally wrong.** Schools leave last year's dates up for months after they
expire. The digest and the observation time make that visible: a calendar whose content has
not changed while its dates have all passed is stale, not stable, and should be reported
rather than stored as current.

## The schema, and the merge that needs a source column

Two levels: the capture, and the expanded days.

| table | key | holds |
|---|---|---|
| `capture` | `school_id`, `observed_at` | format, digest, raw content or rows |
| `day` | `school_id`, `date` | kind, raw_label, source (`district` or `school`) |

The `source` column on the day table is what keeps a district calendar and a school's own
overrides from silently contradicting each other. Training days are the field where they
disagree most, and they are also the field parents most need right.

Expanding to days at read time rather than at capture keeps the correction path open: when
you discover that a particular school writes "w/c 14 October" meaning the whole week, you
fix the expansion and re-derive, instead of re-scraping a hundred small sites that were
kind enough to publish the dates in the first place.
