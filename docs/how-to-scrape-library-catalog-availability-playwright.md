---
title: "How to scrape library catalog availability with Playwright"
description: "Scrape library catalogue availability with Playwright: read holdings per branch rather than per title, keep the loan status vocabulary the catalogue uses, and handle the session the OPAC hands you."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 154
---


# How to scrape library catalog availability with Playwright

To scrape catalogue availability, read the **holdings table**, not the title page. A
library record is one work with many physical copies, each at a branch, each with its own
status, and the headline "Available" on the record page is a summary that hides which
branch has it and how many copies are out.

Public catalogues also carry a second surprise: they are session-driven. The search you
run creates server-side state, results are addressed by a token that expires, and a URL
copied from one run frequently returns an empty result set in the next. Scraping them by
saving result URLs produces a script that works once.

This page covers driving the search rather than replaying URLs, reading holdings per
copy, and keeping the catalogue's own status words instead of flattening them to a
boolean.

## Drive the search, do not replay the result URL

Run the query in the browser and read the results in the same session:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://catalog.example.org/")

    page.fill("input[name='q']", "the master and margarita")
    page.press("input[name='q']", "Enter")
    page.wait_for_selector(".result-item, .no-results")

    hits = []
    for item in page.query_selector_all(".result-item"):
        link = item.query_selector("a.title")
        hits.append({
            "title": link.inner_text().strip(),
            "href": link.get_attribute("href"),
        })
```

Keep `href` relative and resolve it against `page.url` when you follow it. Catalogue
links routinely embed the session token in the path, and an absolute URL captured today
is a dead link tomorrow. Following it inside the same browser session works because the
token is still the one the server issued you.

## Holdings are per copy, per branch

Open a record and read the holdings table rather than the availability badge:

```python
def holdings(page):
    rows = []
    for tr in page.query_selector_all("table.holdings tbody tr"):
        cells = [td.inner_text().strip() for td in tr.query_selector_all("td")]
        if len(cells) < 3:
            continue
        rows.append({
            "branch": cells[0],
            "call_number": cells[1],
            "status": cells[2],
            "due": cells[3] if len(cells) > 3 else None,
        })
    return rows
```

One row per physical copy is the right grain. It answers "which branch can I walk to
today", which the record-level badge cannot, and it makes the count of copies visible,
which matters for anything about demand. A record with nine copies all on loan is a very
different signal from one copy on loan, and both render as "Checked out" at the top of
the page.

## Keep the catalogue's words

Resist mapping status to a boolean at capture time. Library systems use a vocabulary that
carries real distinctions:

- **Available** and **On shelf** mean you can take it now.
- **Checked out** has a due date. **On hold** does not, and means someone is waiting.
- **In transit** is between branches, so it is available soon but not anywhere now.
- **Reference only**, **Reserve**, **Staff use**, **Missing**, **On order**, **Withdrawn**
  are all non-available for different reasons, and only some of them ever become
  available again.

Store the raw string and derive a boolean at read time if you need one. The mapping from
these words to availability differs between library systems, and a mapping baked into the
capture is a decision you cannot revisit without re-scraping.

## The three mechanical traps

**Consent and session interstitials.** Many public catalogues put a terms banner or a
branch selector in front of the first search. Clear it once at the start of the session,
the same way you would any
[cookie consent banner](how-to-handle-cookie-consent-banners-playwright.md), and the rest
of the run is clean.

**Results paginate server-side against the session.** The "next" control posts back
rather than changing a query string. Click it and wait for the list to change instead of
constructing page numbers, which is the pattern in
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md).

**Holdings load after the record.** On several widely used platforms the holdings table
arrives in a second request and the record page renders complete without it. Wait for the
table specifically, and treat its absence after a timeout as a distinct outcome rather
than as an empty holdings list.

## Scrape the catalogue, not the patron account

Everything above works against the public catalogue with no login. It is worth being
explicit that the interesting-looking parts behind a library card, such as your loans,
holds and fines, are personal data attached to a person, and the general point in
[why automating login is riskier than reusing a session](automating-login-vs-session-reuse.md)
applies with extra force when the account is a library record.

The public catalogue is enough for availability questions, and it is the part libraries
publish deliberately.

## Pace it, and prefer a narrow question

Libraries run these systems on small budgets and the search endpoint is the expensive one.
A run that walks an entire catalogue is both slow and unkind. Pick the titles you actually
care about, poll them at a human interval, and use
[the rate limiting rules](how-to-rate-limit-your-scraper-playwright.md) rather than
running flat out. Availability changes when someone physically returns a book, which is a
scale measured in days.
