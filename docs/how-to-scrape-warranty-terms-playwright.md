---
title: "How to scrape warranty terms with Playwright"
description: "Scrape warranty terms with Playwright by keeping parts, labor, powertrain and battery durations as separate fields, pairing each with its usage cap, tagging region and channel, and stamping the row with the term's own document date."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 136
---


# How to scrape warranty terms with Playwright

To scrape warranty terms with Playwright, do not compress the page into one "warranty length" field: read every labeled duration separately (parts, labor, powertrain, battery), pair each stated time period with the usage cap that limits it under "whichever comes first," tag the row with the region and the acquisition channel the term applies to, follow through to any separate registration page before deciding the term is complete, keep the exclusion list as plain text with a small set of keyword flags rather than forcing it into a schema, and record the document's own revision date so a term read today is never assumed to describe a purchase from last year.

A warranty page reads like a single fact and is actually five or six facts stacked on top of each other, each with its own duration, its own trigger, and its own fine print. A page that lists "3 years parts and labor" right above "8 years or 100,000 miles on the battery" is not one number: it is two coverage classes with different lengths, and a scraper that grabs the first duration it finds throws away the second. The rest of this page is about keeping that structure instead of flattening it, and about the three other ways a warranty term quietly changes meaning: the usage cap tucked inside "whichever comes first," the region and channel it was written for, and the document date that tells you whether it even applies to the purchase you are comparing it against.

## Why one warranty length field loses the coverage that matters

A single `warranty_length` column assumes the page states one number, and warranty pages rarely do. Parts, labor, the powertrain and, on anything with a battery, the battery itself commonly carry different periods, and a retailer's extended plan sits on top of all four with its own separate clock. Collapsing that into one field means picking a winner, and whichever duration your selector happens to match first becomes the one that survives, silently dropping the others.

The fix is mechanical: read the labeled sections one at a time and keep each duration under its own key. A row with four blank-or-filled duration fields is more honest than a row with one confident number that only ever captured a quarter of the coverage.

```python
import re
from invisible_playwright import InvisiblePlaywright

DURATION_RE = re.compile(r"(\d+)\s*(year|yr|month)s?", re.IGNORECASE)
COVERAGE_LABELS = ["parts", "labor", "powertrain", "battery"]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/123/warranty", wait_until="networkidle")

    coverage = {label: None for label in COVERAGE_LABELS}
    for text in page.locator("li, tr, p").all_inner_texts():
        lowered = text.lower()
        for label in COVERAGE_LABELS:
            if label in lowered and coverage[label] is None:
                match = DURATION_RE.search(text)
                if match:
                    coverage[label] = match.group(0)
```

The `coverage[label] is None` guard matters more than it looks. Some pages repeat "battery" in a footnote after already stating it in the main table, and without the guard the footnote's shorter promotional number overwrites the real one found further down the page.

## Store "whichever comes first" as two numbers, not one

"3 years or 36,000 miles, whichever comes first" is the most common shape a warranty term takes once a usage-heavy product is involved, and it is really two independent limits joined by an "or." A row that stores only the time component, three years, describes a warranty that in practice usually expires on the mileage side first for anyone who drives more than average. Dropping the usage cap does not just lose a data point, it changes which limit a buyer will actually hit.

Parse both numbers out of the same sentence and keep them as separate fields, because a comparison across products only works if both sides carry both limits.

```python
FIRST_OF_RE = re.compile(
    r"(\d+)\s*(?:year|yr)s?\s*(?:or|/)\s*([\d,]+)\s*(mile|km|cycle|hour)s?",
    re.IGNORECASE,
)

def parse_whichever_first(text):
    match = FIRST_OF_RE.search(text)
    if not match:
        return {"time_years": None, "usage_limit": None, "usage_unit": None}
    years, usage, unit = match.groups()
    return {
        "time_years": int(years),
        "usage_limit": int(usage.replace(",", "")),
        "usage_unit": unit.lower(),
    }
```

Some products state the usage cap in cycles or hours instead of a distance, which is why the code captures the unit instead of assuming it. A row that only expects "miles" silently returns nothing for a warranty measured in charge cycles, and a quiet `None` is worse than a regex that plainly failed, because nothing downstream flags it.

## Attach region and acquisition channel to every term

The same product can carry a different warranty depending on where a retailer sold it and how the buyer acquired it. A retail unit, a manufacturer-refurbished unit and a unit sold under a retailer's own extended program often sit on three different term pages, and a region can extend or shorten the base period entirely apart from the channel question. A term scraped without both facts attached defies comparison against a term from a different scrape, because nothing afterward can tell whether the difference is real or just an artifact of which page happened to load.

Tag the row at the point of collection, while the region and channel are still known, rather than trying to infer them later from the URL.

```python
REGIONS = {
    "us": {"url": "https://example.com/us/warranty", "exit": "us-exit.example.com"},
    "eu": {"url": "https://example.com/eu/warranty", "exit": "eu-exit.example.com"},
}

def scrape_region(region_code, region_cfg, channel):
    proxy = {"server": f"socks5://{region_cfg['exit']}:1080"}
    with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
        page = browser.new_page()
        page.goto(region_cfg["url"], wait_until="networkidle")
        row = {"region": region_code, "channel": channel}
        row.update(parse_coverage(page))   # the per-label extraction from above
        return row
```

Route each region through an exit that actually sits in that region, the same way [geotargeted content](how-to-scrape-geotargeted-content-playwright.md) has to be requested from the right place before you trust what comes back. A US exit reading the EU term page is not guaranteed to see the EU-specific text at all, and a mismatch there produces a row that looks complete and is quietly wrong.

## Registration windows live on a page the base term does not mention

Some warranties only reach their full stated length if the buyer registers the product within a window, often thirty or ninety days, and the page describing that requirement is frequently a different page from the one stating the base term. A scraper that reads only the base-term page reports a duration that is technically the ceiling, not the duration a buyer gets by default if they never register anything.

Look for the registration link on the term page and follow it before treating the row as final, the same crawl-then-follow shape used in [crawling from list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md).

```python
def follow_registration_terms(page):
    link = page.get_by_role("link", name=re.compile("register", re.IGNORECASE))
    if link.count() == 0:
        return {"registration_required": False, "registration_window_days": None}

    with page.expect_navigation():
        link.first.click()

    body = page.locator("body").inner_text()
    window = re.search(r"within\s+(\d+)\s*day", body, re.IGNORECASE)
    return {
        "registration_required": True,
        "registration_window_days": int(window.group(1)) if window else None,
    }
```

If the link is missing, record that plainly rather than guessing. A product with no registration link at all is a different case from one where the link exists but the regex could not find the window, and folding both into the same blank field erases that distinction.

## Keep exclusions as text, and flag the tricky keywords instead of structuring them

Exclusions read like a list and behave like prose: bullet points with clauses, conditions and cross-references that do not reduce cleanly to a table. Forcing each exclusion into its own structured field produces categories that fit some products and break on the next one, and the effort buys little, because most downstream uses of exclusions ask "does this row mention X," not "list every excluded condition in order."

Store the bullets as text, then run a small keyword pass over the same text for the exclusions that actually change a buying decision: accidental damage, commercial use, and unauthorized or third-party repair are the three that come up often enough to be worth flagging on their own.

```python
TRICKY_TERMS = {
    "accidental damage": "accidental",
    "commercial use": "commercial_use",
    "unauthorized repair": "unauthorized_repair",
    "third-party repair": "unauthorized_repair",
    "normal wear": "wear_and_tear",
}

def extract_exclusions(text):
    bullets = re.findall(r"(?:^|\n)\s*[-*]\s*(.+)", text)
    lowered = text.lower()
    flags = sorted({flag for phrase, flag in TRICKY_TERMS.items() if phrase in lowered})
    return {"exclusion_text": bullets, "exclusion_flags": flags}
```

The keyword list is deliberately short and deliberately not exhaustive. Adding a flag for every possible exclusion just rebuilds the structured-field problem one keyword at a time; the point is to surface the handful of conditions worth a human's attention, not to parse legal prose completely.

## Terms are versioned by document date, not by when you scraped them

Warranty pages change over time, and the page you read today shows the current revision, not necessarily the one that applied when an older purchase happened. A term scraped this afternoon does not retroactively describe a product bought a year ago even though the URL and the product name look identical; the underlying document may have changed length, added an exclusion, or tightened a registration window since then.

Pull the revision date the page states for itself and carry it as part of the row, so anything comparing a scraped term against a purchase date can check whether the two actually line up.

```python
from datetime import datetime

REVISION_RE = re.compile(
    r"(?:last updated|effective|revised)[:\s]+([A-Za-z]+ \d{1,2}, \d{4})",
    re.IGNORECASE,
)

def extract_document_date(text):
    match = REVISION_RE.search(text)
    return match.group(1) if match else None

def covers_purchase(document_date, purchase_date):
    # A term with no stated document date cannot be trusted to predate
    # or postdate a given purchase, so treat it as unknown, not as current.
    if document_date is None:
        return None
    # the regex hands back "Month D, YYYY" as text; parse before comparing,
    # because "April 2, 2026" <= "March 1, 2020" is true as strings
    parsed = datetime.strptime(document_date, "%B %d, %Y").date()
    return parsed <= purchase_date
```

A page with no visible revision date is common, and the honest answer in that case is "unknown," not a silent assumption that today's text always applied. Related dates and durations parsed elsewhere on the same page benefit from the same discipline; the mechanics for turning loose date and price text into comparable values are in [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

## Assemble the row, and recheck the flagged fields later

Once the separate pieces exist, the row is a join, not a rewrite: one dict per product per region per channel, carrying the four coverage durations, the usage cap and its unit, the registration window, the exclusion text and flags, and the document date. The shape favors more columns and more blanks over a compressed summary that hides which duration a "3 year" figure actually described.

| Field | What it holds | Why it stays separate |
|---|---|---|
| `coverage[label]` | duration per parts, labor, powertrain, battery | one product can carry four different lengths |
| `usage_limit`, `usage_unit` | the cap paired with the time period | "whichever comes first" is two limits, not one |
| `region`, `channel` | where and how the product was acquired | the same base term is not portable across either |
| `registration_required`, `registration_window_days` | whether the full term needs activation | often lives on a page the base term never mentions |
| `exclusion_text`, `exclusion_flags` | the bullets as written, plus keyword hits | forcing structure onto prose breaks on the next product |
| `document_date` | the page's own stated revision date | today's text is not guaranteed to match an old purchase |

Free-text pages of this kind also tend to embed a `Product` block in JSON-LD for pricing and availability even when the warranty itself is not part of that schema; checking it first for anything it does carry is the same habit covered in [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md), worth a look before falling back to the prose parsing above. The exclusions and the document date rarely live there, but the base coverage duration sometimes does, and reading it from the structured block first saves a regex.

## Conclusion

A warranty term looks like one fact and is a small bundle of them: several durations under one heading, a usage cap riding along with the time period, a region and a channel that decide which base term even applies, a registration step that can live on a page of its own, an exclusion list that resists structure, and a document date that tells you whether any of it still describes a purchase made earlier. Keep each piece as its own field instead of folding them into a single summary line, and the row survives a comparison against a different product, a different region, or a purchase from a year ago. The parsing here is plain regex over rendered text; the discipline is refusing to let five facts pretend to be one.

## Short answers to the questions that lead here

**Why is a single "warranty length" field not enough?** Because most pages state several durations at once, parts, labor, powertrain and battery among them, and a field that captures only the first one it finds silently drops the rest.

**What does "whichever comes first" mean for scraping?** It means the stated time period and a usage cap are both binding limits, not one. Store both numbers and the usage unit, because a row with only the years figure misses the limit that actually applies to a heavy user.

**Does region change the warranty I should record?** Often, yes, and acquisition channel does too. A retail unit, a refurbished unit and a retailer's extended program can each carry a different base term, so tag every row with both before comparing it to anything else.

**Where does registration-dependent coverage usually appear?** On a separate page from the one stating the base term. Follow the registration link before treating a scrape as complete, and record explicitly when no such link exists.

**Should exclusions be turned into structured fields?** No, not fully. Keep the bullets as text and run a short keyword pass for the handful of exclusions, accidental damage, commercial use, unauthorized repair, that actually change a buying decision.

**Can I trust a scraped term to describe an old purchase?** Only if the page's own document date predates the purchase. A term read today shows the current revision, and treating it as unconditionally retroactive is the mistake this whole approach exists to avoid.

## Sources

- Playwright's own [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  [`expect_navigation`](https://playwright.dev/python/docs/api/class-page#page-wait-for-navigation) and
  `locator().all_inner_texts()`, used exactly as documented upstream; the browser this
  library returns is a real Playwright `Browser`, retrieved 2026-08-28.
- This project's own configuration behaviour: a proxied session's timezone and locale
  follow the exit IP, which is what makes a region-tagged scrape actually see the
  region-specific page instead of a default one.

**See also:** [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the base coverage duration some pages embed as structured data,
[crawling from list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
for the registration-page follow-through, [scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md)
for routing a region-tagged request through the right exit, and
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for turning the loose date and duration text into comparable values.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this
parser matched "battery" against the first duration on the page, a promotional footnote,
and overwrote the real battery figure on every row until the guard against re-matching
an already-filled label went in.*
