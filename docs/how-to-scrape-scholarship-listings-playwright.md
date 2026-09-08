---
title: "How to scrape scholarship listings with Playwright"
description: "Scrape scholarship databases with Playwright: parse deadlines that are often not dates, keep eligibility criteria as text, and separate the aggregator's summary from the provider's own page."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 161
---


# How to scrape scholarship listings with Playwright

To scrape scholarship listings, treat the **deadline** as a field that is frequently not a
date and the **eligibility** as text you must not compress. Those two fields carry all the
decision value, and both are routinely written in ways that defeat a naive parser:
"rolling", "varies by campus", "30 days before term start", "closed for 2026".

There is a second structural point. Most scholarship data lives on aggregators that
summarise a provider's own page, and the summary is often stale or subtly wrong. If your
use is helping someone decide where to apply, capture the provider link and treat the
aggregator row as an index entry, not as the truth.

This page covers deadlines that are not dates, eligibility that resists structuring, and
following through to the source.

## Parse the deadline into a shape that admits it is not a date

```python
import re
from datetime import date

RELATIVE = re.compile(r"(?P<n>\d+)\s+days?\s+before\s+(?P<anchor>.+)", re.I)

def parse_deadline(text):
    t = (text or "").strip()
    low = t.lower()
    if not t:
        return {"kind": "unknown", "raw": text}
    if "rolling" in low or "any time" in low:
        return {"kind": "rolling", "raw": t}
    if "varies" in low:
        return {"kind": "varies", "raw": t}
    if "closed" in low:
        return {"kind": "closed", "raw": t}
    m = RELATIVE.search(low)
    if m:
        return {"kind": "relative", "days_before": int(m.group("n")),
                "anchor": m.group("anchor").strip(), "raw": t}
    parsed = try_absolute(t)
    if parsed:
        return {"kind": "date", "date": parsed.isoformat(), "raw": t}
    return {"kind": "unparsed", "raw": t}
```

The `unparsed` branch is the one that keeps the dataset honest. A pipeline that coerces
everything into a date column has to invent something for "varies by campus", and whatever
it invents will send someone to a deadline that does not exist.

Absolute dates need care too: these listings mix `03/04/2026` in both conventions, often
on the same aggregator, because providers submit them as free text. Where the site
publishes a machine-readable date in an attribute, prefer it:

```python
    node = card.query_selector("time[datetime]")
    iso = node.get_attribute("datetime") if node else None
```

## Eligibility is prose, and compressing it does harm

Eligibility rules combine study level, field, nationality, residency, income, institution,
sometimes demographic criteria and sometimes an essay requirement. Aggregators show them
as chips, which loses the conjunctions: whether the criteria are all required or any of
them qualify.

Capture both forms:

```python
        "eligibility_chips": [c.inner_text().strip()
                              for c in card.query_selector_all(".eligibility .chip")],
        "eligibility_text": (card.query_selector(".eligibility-full").inner_text().strip()
                             if card.query_selector(".eligibility-full") else None),
```

The chips are searchable; the text is correct. If you only keep one, keep the text. A
student filtered out by a chip that dropped an "or" is a real cost, and it is invisible in
the data.

## The award amount has a shape too

```python
    "amount_text": "Up to $5,000 per year, renewable for 4 years"
```

Amounts carry a maximum, a period, a renewal and sometimes a count of awards. Store the
string and derive numbers with the same explicit-unknown discipline as deadlines. A single
`amount` float turns "up to" into "is", which is the most common way these datasets
overstate what a student will receive.

## Follow through to the provider

The aggregator row should carry a link to the source, and the source is where the current
deadline lives:

```python
    provider = card.query_selector("a.provider-link, a[rel='nofollow'][target='_blank']")
    row["provider_url"] = provider.get_attribute("href") if provider else None
```

Where you visit the provider page, capture its own deadline and eligibility separately
rather than overwriting the aggregator's. The disagreement between the two is useful
information about how stale the aggregator is, and it is lost the moment you merge them.

Many provider pages are university sites whose listings sit behind a search form; that is
the pattern in
[scraping search results by driving a form](how-to-scrape-search-results-form-playwright.md).

## Filters, pagination and a rewarding shortcut

Aggregators put the useful selection behind facets, which repopulate the list without a
navigation, exactly like
[multi-select facet filters](how-to-scrape-multi-select-facets-playwright.md). Set them
explicitly and record what you set, because a filter left at a default silently narrows
your dataset in a way no column reveals.

The shortcut worth trying first: many of these listings are marked up with structured data
for search engines, which gives you a clean object without parsing the cards at all. Check
before writing selectors, using
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md).

## Refresh on the deadline calendar

Scholarship data has a natural rhythm: providers update in a season, deadlines cluster,
and most rows do not change for months. A weekly full pass plus a daily pass over rows
whose deadline is within a month covers the volatility at a fraction of the requests, and
keeps you well inside the pacing described in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

## A complete pass, aggregator then provider

```python
import json, time
from invisible_playwright import InvisiblePlaywright

def aggregator_rows(page):
    rows = []
    for card in page.query_selector_all(".scholarship-card"):
        def t(sel):
            n = card.query_selector(sel)
            return n.inner_text().strip() if n else None
        provider = card.query_selector("a.provider-link, a[target='_blank']")
        rows.append({
            "source": "aggregator",
            "title": t(".title"),
            "amount_text": t(".amount"),
            "deadline": parse_deadline(t(".deadline")),
            "eligibility_chips": [c.inner_text().strip()
                                  for c in card.query_selector_all(".eligibility .chip")],
            "eligibility_text": t(".eligibility-full"),
            "provider_url": provider.get_attribute("href") if provider else None,
            "observed_at": time.time(),
        })
    return rows

with InvisiblePlaywright(seed=42) as browser, open("scholarships.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    page.goto("https://example-aggregator.com/scholarships")
    page.select_option("select[name='level']", "undergraduate")
    page.wait_for_selector(".scholarship-card")

    rows = aggregator_rows(page)
    for row in rows:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")

    for row in rows:
        if not row["provider_url"]:
            continue
        try:
            page.goto(row["provider_url"], wait_until="domcontentloaded", timeout=25000)
        except Exception as exc:
            out.write(json.dumps({"source": "provider", "of": row["title"],
                                  "error": str(exc)[:120],
                                  "observed_at": time.time()}) + "\n")
            continue
        out.write(json.dumps({
            "source": "provider",
            "of": row["title"],
            "url": page.url,                       # after redirects: the real page
            "deadline_text": first_text(page, ".deadline, .apply-by, time[datetime]"),
            "observed_at": time.time(),
        }, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(3000)
```

Writing the provider record as a separate row rather than merging it into the aggregator's
is the design decision that pays off. The disagreement between the two is the most useful
field in the dataset: it tells you which aggregators are stale, which is what decides
whether their rows are worth showing to anyone.

Recording `page.url` after the navigation catches the other common case, where a provider
link redirects to a general funding page because the specific scholarship has closed.

## Aggregators defend their listings, providers do not

Two very different targets in one pipeline, and they fail differently.

**The aggregator is the commercial one.** Its listings are its product, so it is more
likely to sit behind edge protection, to lazy-load cards, and to cap how deep the facets
let you go. The card list frequently arrives after the shell renders, so waiting for the
container rather than a card returns an empty page that parses cleanly.

**The provider is a university or a foundation.** Those sites are slow, occasionally
broken, and frequently redirect. The failure to plan for is not a block but a timeout, and
the code above records it as a row rather than losing the aggregator's data because one
provider was down.

Where the aggregator does start refusing, the tell is usually a card count that drops
rather than an error. Assert against the total the site itself prints:

```python
    claimed = page.inner_text(".results-count")          # "412 scholarships"
    got = len(page.query_selector_all(".scholarship-card"))
    if int(claimed.split()[0].replace(",", "")) > got and not page.query_selector(".load-more"):
        raise RuntimeError(f"page claims {claimed} but rendered {got} cards")
```

The general order for diagnosing a degraded read is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## The schema that keeps a student from a dead deadline

One row per listing per source per observation, with the deadline kept as an object rather
than a date:

| field | example |
|---|---|
| `deadline.kind` | `date`, `rolling`, `varies`, `relative`, `closed`, `unparsed` |
| `deadline.date` | only present when `kind` is `date` |
| `deadline.raw` | always, verbatim |
| `eligibility_text` | the full prose, never only the chips |
| `amount_text` | `"Up to $5,000 per year, renewable"` |
| `source` | `aggregator` or `provider` |

The `unparsed` kind is the field that makes this dataset honest. Every alternative design
invents a date for a listing that does not have one, and the cost of that invention is
borne by whoever trusts the row. Surfacing "we could not read this deadline, here is what
it said" is both easier to build and more useful than a confident wrong date.

## Short answers to the questions that lead here

**How should I store a deadline that is not a date?** As a shape that admits it.
Scholarship deadlines are often rolling, or a term, or a phrase with a condition
attached. Keep an unparsed branch: a pipeline that coerces everything into a date
silently invents precision the source never had.

**Can I compress the eligibility rules into fields?** Not without doing harm.
Eligibility combines study level, field, nationality, residency, income and
institution, and a student excluded by a field you dropped never finds that out. Keep
the prose and derive alongside it.

**Should I trust the aggregator's deadline?** Follow through to the provider. The
aggregator row is a pointer, and the current deadline lives at the source, which is
also where a withdrawn award disappears first.

**How often is a refresh worth it?** On the deadline calendar. Providers update in a
season and deadlines cluster, so a pass timed to that rhythm sees the changes; a daily
pass mostly re-reads rows that have not moved.

**See also:** [How to scrape multi-select facet filters with
Playwright](how-to-scrape-multi-select-facets-playwright.md), [How to extract JSON-LD
structured data with
Playwright](how-to-extract-json-ld-structured-data-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Locators, https://playwright.dev/python/docs/api/class-locator - waiting
  for a facet to repopulate a list with no navigation, checked for the filtering
  behaviour these aggregators use.
- This project's pages on multi-select facets and on JSON-LD, which cover the two
  routes into an aggregator's own model of a listing.
