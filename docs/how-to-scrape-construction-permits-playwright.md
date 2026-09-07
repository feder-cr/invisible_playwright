---
title: "How to scrape construction permit records with Playwright"
description: "Scrape municipal permit portals with Playwright: drive the postback search forms these systems use, page through result sets that only exist in a session, and keep the permit status history."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 159
---


# How to scrape construction permit records with Playwright

To scrape a permit portal, drive its **search form** and stay in the session it gives you.
These systems are almost always server-rendered enterprise software, where the results
page is a postback rather than a URL, page two is a form submission, and a link copied out
of the browser returns a session-expired notice ten minutes later.

That single property decides the whole design. You cannot build a list of URLs and fetch
them in parallel. You drive one session in order, and you make it resumable, because a
run over a year of permits takes long enough that something will interrupt it.

This page covers filling the search the way the form expects, walking result pages that
only exist inside a session, and capturing a permit's status history rather than its
current state.

## Fill the search in the order the form expects

Permit searches are usually date-range plus type, and the fields depend on each other:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://permits.example.gov/search")

    page.select_option("select#permitType", "BUILDING")
    page.fill("input#dateFrom", "01/01/2026")
    page.fill("input#dateTo", "01/31/2026")
    page.click("input#btnSearch")
    page.wait_for_selector("#resultsGrid tr, .no-records")
```

Select the type before the dates where the form reloads on change. Many of these pages
issue a postback when a dropdown changes, which clears fields filled before it, so the
order that works is the order a person would use: the thing that reshapes the form first,
the details after.

If the date fields are pickers that ignore typed values, treat them as
[date-picker calendars](how-to-scrape-date-picker-calendar-playwright.md) and click the
days.

## Page two is a form submission, not a URL

```python
    def next_page(page):
        link = page.query_selector("a#nextPage:not(.disabled)")
        if not link:
            return False
        first_before = page.inner_text("#resultsGrid tr:nth-child(1)")
        link.click()
        page.wait_for_function(
            "prev => document.querySelector('#resultsGrid tr').innerText !== prev",
            arg=first_before,
        )
        return True
```

Waiting for the first row to change is the reliable signal. Waiting for a navigation
returns immediately on a page that never navigates, and waiting a fixed time either wastes
seconds or reads the previous page. The general shape is the one in
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md), with the extra
constraint that here you genuinely cannot skip ahead.

Narrow the query rather than paging deeply. Most portals cap results, often silently, at a
few hundred rows. A month at a time with an explicit row count check is more work and
returns the whole set:

```python
    total = page.inner_text(".result-count")     # "1 to 25 of 412"
```

Compare the total against what you collected and fail loudly on a mismatch. A silent cap
is the most common way these datasets end up missing a third of a year.

## Capture the status history, not just the current status

A permit moves through review stages, and the interesting questions are about duration:
how long between application and issue, where files stall, which reviewers are backed up.
The detail page usually carries this as a table:

```python
def permit_detail(page, href):
    page.click(f"a[href='{href}']")
    page.wait_for_selector("#permitDetail")
    stages = []
    for tr in page.query_selector_all("#statusHistory tbody tr"):
        cells = [td.inner_text().strip() for td in tr.query_selector_all("td")]
        stages.append({"stage": cells[0], "date": cells[1], "outcome": cells[2] if len(cells) > 2 else None})
    return stages
```

If the portal only shows a current status, capture it with your run timestamp and build
the history yourself across runs. That is slower to become useful, and it is the only way
to get duration data out of a system that does not publish it.

## Make the run resumable, because it will be interrupted

A session-bound sequential crawl over months of records is exactly the job that dies at
70%. Write each page as you go and record where you were:

```python
    checkpoint = {"type": "BUILDING", "from": "01/01/2026", "to": "01/31/2026", "page": 7}
```

Then a restart re-runs the search and pages forward to the checkpoint rather than starting
over. The pattern and its edge cases are in
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md), and
[retrying failed requests](how-to-retry-failed-requests-playwright.md) covers the
transient failures that make it necessary.

## These are public records, and they contain people

Permit data is published deliberately, and it is genuinely useful: construction activity,
contractor patterns, neighbourhood change. It also contains owner names and addresses,
which are personal data in most jurisdictions regardless of being publicly displayed.

Take the fields your question needs rather than the whole record by default, keep the
retention deliberate, and check whether the jurisdiction already publishes a bulk export.
Many do, on an open data portal, and pulling a file is better for everyone than paging a
search form for a week. The technique for those is in
[scraping open data portals](how-to-scrape-open-data-portals-playwright.md).
