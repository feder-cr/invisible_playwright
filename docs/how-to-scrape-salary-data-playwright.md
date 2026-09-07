---
title: "How to scrape salary and pay scale data with Playwright"
description: "Scrape salary and pay scale data with Playwright: expand the hidden pay breakdown, keep the range and period intact, tag self-reported figures apart from wage statistics, and carry sample size and location context with every number."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 138
---


# How to scrape salary and pay scale data with Playwright

To scrape salary and pay scale data with Playwright, expand any tooltip or collapsed row
before you read a figure, keep the period, currency, and base-versus-total flag attached
to every value, store a range as two numbers instead of a collapsed midpoint, and tag
each record with what kind of evidence it is: a figure typed by a job poster, one
submitted by a site visitor, or a line from a government wage survey. Each of those needs
its own sample size and collection date carried along with it, not folded into the
number.

A salary figure on a listing page looks like one fact and is usually four or five facts
wearing one costume. The headline number a page shows is frequently a blend, base pay
mixed with an assumed bonus, sometimes equity, rounded to a tidy round figure, with the
real components sitting one click away in a tooltip or an expandable row. Treat the
headline as the whole answer and you store a number that nobody involved actually meant.
This page keeps those pieces attached to the value instead of losing them on the way
into a row.

## Decompose the number before you store it

A salary value only means something once you know its period, its currency, and whether
it covers base pay alone or a blended total. "80,000" without a unit could be annual in
one country and monthly in another, and a page that shows one number rarely says out
loud which of those it picked. Job boards that embed a `JobPosting` block in JSON-LD
carry this structure directly: a `baseSalary` field wraps a `QuantitativeValue` with
`minValue`, `maxValue`, and a `unitText` such as `YEAR`, `MONTH`, or `HOUR`. Reading that
field beats parsing the rendered card, using the same pattern as
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
generally.

The honest limitation is that `unitText` and a currency code are not the same claim as
"this is base pay only." Schema.org's `baseSalary` field name suggests base pay, but
plenty of sites populate it with whatever number their own UI treats as headline
compensation, bonus included. Do not infer a components claim from a field name; wait for
the page to say so explicitly, which is what the next section reads for.

```python
def normalize_base_salary(salary_node):
    """A JobPosting baseSalary node -> a typed record with no field guessed."""
    if not salary_node:
        return None

    value = salary_node.get("value") or {}
    unit = (value.get("unitText") or "").upper()
    period_map = {"YEAR": "year", "MONTH": "month", "WEEK": "week", "HOUR": "hour"}

    return {
        "currency": salary_node.get("currency"),
        "period": period_map.get(unit),   # None when the page never said
        "min_value": value.get("minValue", value.get("value")),
        "max_value": value.get("maxValue", value.get("value")),
        "components": None,   # filled in only if the page confirms base vs. total
    }
```

A missing `period` here is a signal, not a bug to patch over with a guess. A downstream
report that assumes "no unit means annual" will misprice every hourly listing that came
through without one, and that mistake is invisible until someone compares two rows that
should agree and do not.

## The breakdown is one click away, not in the headline

The components a headline figure blends together are frequently visible, just not in the
initial paint. Many listing and comparison pages put "base," "bonus," and "equity" (or
"total cash," "on-target earnings") behind a tooltip icon or a collapsed row that expands
on click, the same widget shape covered in
[scraping accordion and tab content](how-to-scrape-accordion-and-tab-content-playwright.md).
The number you see before that click is a sum. The numbers you want are the addends.

Drive the toggle the same way that page describes: read `aria-expanded` before clicking
so you never re-close a panel the page already opened, and wait for text to land in the
specific panel instead of trusting that the click alone was enough.

```python
def read_pay_breakdown(page, toggle_selector, panel_id):
    toggle = page.locator(toggle_selector)
    if toggle.get_attribute("aria-expanded") == "false":
        toggle.click()
        page.wait_for_selector(f"#{panel_id}[aria-expanded='true'], #{panel_id}")
        page.wait_for_function(
            "id => { const el = document.getElementById(id);"
            " return el && el.textContent.trim().length > 0; }",
            arg=panel_id,
            timeout=15000,
        )

    rows = page.locator(f"#{panel_id} [data-comp-line]").evaluate_all(
        "nodes => nodes.map(n => ({"
        "  label: n.getAttribute('data-comp-line'),"
        "  text: n.textContent.trim(),"
        "}))"
    )
    return {row["label"]: row["text"] for row in rows}   # e.g. {"base": "...", "bonus": "..."}
```

If the panel never appears, that is a real answer too: the page is not disclosing a
breakdown, and `components` on that record stays `None` instead of an assumed split. A
figure with no visible breakdown is a blended total, and a blended total stored as if it
were base pay will overstate every comparison against a job that lists base pay alone.

## Store the range as two numbers, never a midpoint

A pay range is the normal shape of this data, not an edge case to smooth over. "$80,000
to $110,000" is two numbers with a $30,000 gap between them, and collapsing that pair to
a $95,000 midpoint throws away the exact detail a reader usually wants: how wide is the
band, and where in it does a given level of experience land. Keep `min_value` and
`max_value` as separate fields all the way through the pipeline. If a consumer wants a
single number later, they can compute their own midpoint from data that still has both
ends; you cannot go the other way.

Parsing the text itself needs to handle shorthand that a plain locale-aware number parser
does not, specifically the `K` and `M` suffixes common in pay-range text. The
thousands-separator and currency-symbol problem underneath that is the same one covered
in [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md);
what is specific to a salary range is the second number and its own optional suffix.

```python
import re
from decimal import Decimal

RANGE_RE = re.compile(
    r"(?P<low>[\d.,]+)\s*(?P<low_suffix>[kKmM])?\s*(?:-|to|through)\s*"
    r"(?P<high>[\d.,]+)\s*(?P<high_suffix>[kKmM])?"
)


def parse_range(raw):
    """'$80K - $110K' -> (Decimal('80000'), Decimal('110000')); a point value -> (None, None)."""
    cleaned = raw.replace("$", "").strip()
    match = RANGE_RE.search(cleaned)
    if not match:
        return None, None

    def expand(number_text, suffix):
        amount = Decimal(number_text.replace(",", ""))
        if suffix and suffix.lower() == "k":
            amount *= 1000
        elif suffix and suffix.lower() == "m":
            amount *= 1_000_000
        return amount

    low = expand(match.group("low"), match.group("low_suffix"))
    high = expand(match.group("high"), match.group("high_suffix"))
    return low, high
```

A `(None, None)` result is the parser telling you the text was a single point value, not
a failure. Route that case to `min_value == max_value` instead of discarding the row.

## Self-reported figures and wage statistics are not the same evidence

A number is not more trustworthy just because it appears on a page with a chart next to
it. Two different kinds of salary evidence circulate under the same visual style, and a
scraper that does not label which one it collected produces a table that looks uniform
and is not.

A figure typed into a job posting by the employer is a stated intent, sometimes required
by a pay-transparency law and sometimes a rough placeholder. A figure submitted by
a site visitor describing their own pay is self-report, shaped by who bothers to submit
one and how they remember or round their own number. A row on a government or official
wage survey page is a different kind of thing again: a sampled or census measurement with
a defined collection method behind it. None of these should share a column labeled just
"salary" without also carrying which kind of claim it is.

```python
SOURCE_TYPES = {"employer_listed", "self_reported", "wage_statistic"}


def tag_source(raw_record, source_type):
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unknown source_type: {source_type!r}")
    return {**raw_record, "source_type": source_type}
```

The tag is cheap to attach and expensive to reconstruct later. Once employer-listed
ranges, visitor submissions, and survey rows are merged into one file with no source
column, there is no way to separate them back out, and any average computed across the
mix answers a question nobody actually asked.

## Location text implies cost of living; it does not state it

A location string on a pay page can name a metro area, a specific city, or say "remote,"
and each of those implies a different cost-of-living context without the raw text
spelling that out. "San area, greater region" reads as a metro; "Springfield, IL" reads
as a specific city; "Remote" says nothing about geography at all, only that the pay figure
was not tied to one. Comparing a metro-area range against a specific-city figure as if
they were the same kind of location claim is how a table quietly compares unlike things.

Keep the original text untouched and add a coarse classification alongside it, rather than
rewriting the source string into something more convenient.

```python
def classify_location(raw):
    """Keep the source text; add a coarse category, never overwrite it."""
    text = raw.strip()
    lowered = text.lower()

    if "remote" in lowered:
        return {"location_raw": text, "location_type": "remote", "metro": None}
    if re.search(r",\s*[A-Za-z]{2}$", text):
        return {"location_raw": text, "location_type": "city", "metro": None}
    return {"location_raw": text, "location_type": "metro", "metro": text}
```

This heuristic is coarse on purpose and will misclassify some edge cases, a metro area
that happens to end in a state abbreviation, for one. That is an acceptable cost as long
as `location_raw` survives untouched next to it, so a later pass can correct the category
without having lost the original string.

## Keep experience level as text when it is not a structured field

Some job and pay-scale pages carry a structured seniority field, entry, mid, or senior,
set once by whoever built the listing form. Plenty do not, and the only signal available
is the job title itself: "Staff," "Senior," "Associate," or nothing distinguishing at
all. Forcing every title into a structured guess destroys the difference between a page
that told you the level and a page you inferred it from.

Keep both. When a structured field exists, trust it and record that it came from the
page. When it does not, extract a signal from the title, but mark the result as inferred
rather than letting it overwrite an empty structured field as if the page had said so.

```python
SENIORITY_HINTS = [
    ("staff", "senior"), ("principal", "senior"), ("lead", "senior"),
    ("senior", "senior"), ("sr.", "senior"),
    ("junior", "entry"), ("jr.", "entry"), ("entry", "entry"), ("intern", "entry"),
]


def experience_fields(structured_value, title):
    if structured_value:
        return {
            "experience_level": structured_value,
            "experience_source": "structured",
            "title_text": title,
        }

    lowered = title.lower()
    for hint, level in SENIORITY_HINTS:
        if hint in lowered:
            return {
                "experience_level": level,
                "experience_source": "inferred_from_title",
                "title_text": title,
            }

    return {"experience_level": None, "experience_source": "unknown", "title_text": title}
```

`title_text` travels with the record either way. A "mid" level guessed from a title with
no seniority word in it at all is worse than no guess, because it reads exactly like a
field the page actually set.

## Carry sample size and collection date with every aggregated figure

A median or a set of percentiles on an aggregator page is a statistic computed over some
number of data points, collected as of some date, and both numbers change what the figure
is worth. A median built from twelve submissions and a median built from twelve thousand
can share the same page layout and mean very different things, and a percentile collected
two years ago is a different claim than one refreshed last month.

Most pages that show this kind of aggregate state the sample size and an as-of date
somewhere near the figure, often in a footnote or a small caption. Parse that text
alongside the number instead of parsing the number alone, and treat the two as required,
not optional.

```python
import re
from datetime import date
from decimal import Decimal

STAT_ROW_RE = re.compile(
    r"(?P<label>median|p10|p25|p75|p90)\D+"
    r"(?P<value>[\d.,]+)\D+"
    r"n\s*=\s*(?P<n>[\d,]+)\D+"
    r"as of\s*(?P<as_of>\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def parse_stat_row(text):
    match = STAT_ROW_RE.search(text)
    if not match:
        return None   # do not report a figure you cannot attach a sample size and a date to

    return {
        "stat": match.group("label").lower(),
        "value": Decimal(match.group("value").replace(",", "")),
        "sample_size": int(match.group("n").replace(",", "")),
        "as_of": date.fromisoformat(match.group("as_of")),
    }
```

A `None` return here is a refusal, not a bug. Reporting a percentile with no attached
sample size just because the number was easy to find is how a twelve-point estimate ends
up sitting next to a twelve-thousand-point one with no way to tell them apart later, the
same failure mode covered from the table-layout side in
[scraping HTML tables](how-to-scrape-html-tables-playwright.md).

## Assemble one row that keeps every qualifier attached

A single record shape that fits both an individual listing and an aggregate statistic
avoids branching logic downstream on which kind of page produced a given row. The record
carries a value or a range, the period and currency, the components if disclosed, the
location classification, the experience fields, and the source tag, with `sample_size`
and `as_of` populated only when the row is an aggregate.

```python
def build_salary_record(source_type, currency, period, min_value, max_value,
                         components, location, experience,
                         sample_size=None, as_of=None):
    return {
        "source_type": source_type,     # "employer_listed", "self_reported", "wage_statistic"
        "currency": currency,
        "period": period,               # "year", "month", "week", "hour", or None
        "min_value": min_value,
        "max_value": max_value,
        "components": components,       # {"base": ..., "bonus": ..., "equity": ...} or None
        **location,                     # location_raw, location_type, metro
        **experience,                   # experience_level, experience_source, title_text
        "sample_size": sample_size,     # None for a single listing
        "as_of": as_of,                 # required whenever sample_size is set
    }
```

The point of one shape is that a query against this table never has to ask "is this row
a listing or a statistic" before it can filter or compare. The qualifiers that make a
number interpretable ride along in every row instead of living only in the page it came
from.

## Conclusion

A salary number by itself answers almost nothing. It needs a period and a currency, and a
components flag saying what it covers; a range instead of a midpoint, to keep the spread a
reader wants; a source tag distinguishing an employer's figure, a visitor's submission,
and a survey's measurement; a location classification that admits metro, city, and remote
are different claims; an experience field that stays honest about whether it was
structured or guessed from a title; and, for any aggregate figure, the sample size and
date behind it. None of that is hard to extract once you know to look for it. The failure
mode is not missing data. It is data that looks complete because a page rendered a
clean-looking number, when the number was never the whole answer.

## Short answers to the questions that lead here

**Why does the same job title show two different salaries on the same page?** One is
usually the blended headline figure and the other is a component, base pay alone or a
bonus-inclusive total, shown once you expand a tooltip or a collapsed breakdown row.
Read both and keep them as separate fields rather than picking one.

**Should I collapse a salary range to its midpoint before storing it?** No. Keep
`min_value` and `max_value` as two fields. A consumer can compute a midpoint from a real
range; there is no way to recover the range from a stored midpoint.

**Is a visitor-submitted salary figure as reliable as a government wage statistic?** No,
they are different kinds of evidence. Tag the source type on every record so a report
never averages the two together as if they measured the same thing.

**Does "remote" tell me anything about cost of living?** No, and neither does a bare metro
name by itself. Keep the original location text, add a coarse category, and treat all
three, metro, city, and remote, as distinct claims about geography rather than
interchangeable labels.

**What do I do when the page has no structured experience level field?** Extract a
signal from the job title and store it as inferred, in its own field, rather than writing
a guessed value into the same column a structured field would have used.

**Why does a median on one page mean something different from a median on another?**
Because the sample size and the collection date behind it differ. A page that states both
next to the figure is giving you what you need to judge it; parse that text along with the
number, and skip a figure that states neither.

## Sources

- Schema.org `JobPosting`, `MonetaryAmount`, and `QuantitativeValue` types, whose
  `unitText` and `minValue`/`maxValue` fields are the structure the base-salary parsing
  above reads.
- Playwright documentation, [Locators](https://playwright.dev/python/docs/locators),
  retrieved 2026-08-28.
- Playwright documentation, [Auto-waiting](https://playwright.dev/python/docs/actionability),
  retrieved 2026-08-28.
- Playwright documentation, [locator.evaluate_all()](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all),
  retrieved 2026-08-28.

**See also:** [how to scrape job postings](how-to-scrape-job-postings-playwright.md) for
the faceted search and sweep mechanics this page assumes,
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the general parsing pattern the base-salary field builds on,
[scraping accordion and tab content](how-to-scrape-accordion-and-tab-content-playwright.md)
for the toggle mechanics behind a hidden pay breakdown,
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for the locale side of number parsing, and
[scraping HTML tables](how-to-scrape-html-tables-playwright.md) for reading an aggregate
percentile table without losing rows.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this
pipeline stored a visitor-submitted figure, an employer-listed range, and a wage-survey
percentile in the same unlabeled column, and a report built on top of it averaged all
three together as if they had measured the same thing.*
