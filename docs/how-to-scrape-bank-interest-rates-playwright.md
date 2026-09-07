---
title: "How to scrape bank interest rates with Playwright"
description: "Scrape published deposit and lending rates with Playwright: resolve the footnotes that change the rate, keep the balance tiers separate, and record the effective date the bank prints."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 164
---


# How to scrape bank interest rates with Playwright

To scrape published rates, capture the **footnote with the number**. A rate table on a
bank site is a set of headline figures whose real meaning lives in the small print
underneath: an introductory period, a balance tier, a requirement to hold another product,
a rate that drops after twelve months. The headline without its footnote is not a
simplification, it is a different number.

The second structural fact is tiering. One product usually has several rates, one per
balance band, and the page shows the best one in large type. A scrape that takes the large
type produces a table where every bank looks like its best case.

This page covers resolving footnote markers to their text, reading tiers as rows, and
keeping the effective date the bank publishes.

## Resolve the marker to the text, at capture time

Footnote markers are superscripts that point at a list further down the page. Follow the
pointer while you have the DOM:

```python
from invisible_playwright import InvisiblePlaywright

RESOLVE = """
() => {
  const notes = {};
  document.querySelectorAll('.footnotes li[id]').forEach(li => {
    notes[li.id] = li.textContent.trim();
  });
  return Array.from(document.querySelectorAll('table.rates tbody tr')).map(tr => {
    const cells = Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim());
    const marks = Array.from(tr.querySelectorAll('sup a[href^="#"]'))
      .map(a => notes[a.getAttribute('href').slice(1)])
      .filter(Boolean);
    return { cells, footnotes: marks };
  });
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-bank.com/savings/rates")
    page.wait_for_selector("table.rates tbody tr")
    rows = page.evaluate(RESOLVE)
```

Doing this in one evaluation matters because the markers and the notes are in different
parts of the document, and resolving them later from two separately captured lists means
matching on an id you may not have kept.

## Tiers are rows, and the tier bounds are part of the rate

```python
import re

TIER = re.compile(r"(?P<lo>[\d,]+)\s*(?:to|-|and above|\+)?\s*(?P<hi>[\d,]+)?", re.I)

def parse_tier(text):
    m = TIER.search(text.replace("$", "").replace("£", ""))
    if not m:
        return {"tier_text": text}
    return {
        "tier_text": text,
        "min_balance": int(m.group("lo").replace(",", "")),
        "max_balance": int(m.group("hi").replace(",", "")) if m.group("hi") else None,
    }
```

A null `max_balance` means the top band, which is open ended. Keep `tier_text` as well,
because banks write bands in ways this regex will not always survive, and the original
string is what lets you tell a parse failure from an unusual product.

## APR and APY are not interchangeable

Deposit products publish an annual yield that includes compounding; lending products
publish an annual rate that includes fees, under names that vary by country. The two are
not comparable and the page often shows both:

```python
    row["rate_kind"] = header_for(column_index)     # "APY", "APR", "AER", "nominal"
```

Take the label from the column header rather than assuming. Storing every percentage in a
column called `rate` merges two different quantities, and the error is invisible until
someone compares a savings account to a loan and gets a sensible-looking answer.

## Effective date, and the fact that these pages are cached hard

Banks print an effective date, and it is the closest thing to a timestamp the data has:

```python
    node = page.query_selector(".rates-effective, .as-of-date")
    row["effective_text"] = node.inner_text().strip() if node else None
    row["captured_at"] = time.time()
```

Keep both, for the same reason as any live-status scrape: the page you fetched today may
be serving a rate table from last week, and the effective date is the only field that
reveals it. If the effective date has not moved in weeks while a central bank has, the
page is stale rather than the rates being unchanged.

## Products live behind a selector

Larger banks put rates behind a product picker, a region picker, or both, with no URL
change. Set them explicitly and record them:

```python
    page.select_option("select#region", "CA")
    page.select_option("select#product", "TFSA")
    page.wait_for_selector("table.rates tbody tr")
    row["region"] = "CA"
    row["product"] = "TFSA"
```

Rates genuinely differ by region within the same bank, and a table captured without the
region column cannot be compared with itself next month. Where the region is inferred from
the visitor rather than selected, that is
[geotargeted content](how-to-scrape-geotargeted-content-playwright.md) and the exit
becomes part of the query.

## Read the published rate, and nothing behind a login

Everything above is the public rate card, which banks publish deliberately and update on a
schedule. The parts behind authentication are account data belonging to a person, and the
reasoning in
[why automating login is riskier than reusing a session](automating-login-vs-session-reuse.md)
applies with the extra weight that financial institutions treat automated access to
accounts as a security event, correctly.

Public rate cards change daily at most, usually less. A daily pass is generous, the pacing
in [rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) applies,
and storing the history in
[a SQLite database](how-to-scrape-into-a-database-playwright.md) gives you the one thing
the bank's own page never shows: what the rate used to be.
