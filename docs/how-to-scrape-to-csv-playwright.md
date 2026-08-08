---
title: "How to scrape to CSV with Playwright"
description: "Scrape JS-rendered values to CSV with Playwright: correct delimiter and newline escaping, a UTF-8 BOM spreadsheets read, and crash-safe incremental appends."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 53
---


# How to scrape to CSV with Playwright

To scrape to CSV with Playwright, drive the page in a real browser, extract each row with
`page.evaluate`, and write it with Python's `csv.DictWriter` using `encoding="utf-8-sig"`
and `newline=""`. That pulls the JavaScript-rendered value a human actually sees and
escapes every comma, quote and line break, so one messy cell cannot split a row or corrupt
the file a spreadsheet opens.

Writing a CSV row looks like joining strings with commas. It is not, and the moment a
scraped cell contains a comma, a line break or an accented character, the naive version
splits one row into three columns or corrupts the whole file when a spreadsheet opens it.

This page is the correct way to get scraped values into a CSV: pull them from a real
rendered DOM so the numbers are the ones a human sees, escape and encode them so they
survive a round trip through a spreadsheet, and append them so a long crawl that dies
halfway does not take the file with it.

## Why a real browser writes different cells than an HTTP fetch

A real browser writes the values a user actually sees, while a plain HTTP fetch writes
whatever sat in the raw HTML before JavaScript ran, which is often a placeholder or an
empty cell. Before any escaping question, then, there is a correctness question: are the
values you are writing the real ones?

Prices, stock counts, ratings and anything else that updates without a full page load are
written into the DOM by JavaScript after the document arrives. An HTTP client that fetches
the raw HTML and parses it gets whatever was in the markup before that script ran, which
is often a placeholder, a zero, or nothing at all. You then write a clean, well-escaped
CSV full of stale cells and do not notice until the numbers are wrong in aggregate.

A real browser runs the script, so the value in the DOM is the value on the screen. That
is the whole reason to drive Playwright for this instead of a request library. Switching
from stock Playwright is [a two-line change and every method is identical](quickstart.md)
afterwards:

```python
import csv
from invisible_playwright import InvisiblePlaywright

FIELDS = ["name", "price", "stock", "url"]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/catalog")

    # Wait for the client-rendered value, not just the document. If you read
    # before the script writes the price, you write an empty cell.
    page.wait_for_selector(".product-card .price")

    rows = page.evaluate("""
        () => Array.from(document.querySelectorAll(".product-card")).map(card => ({
            name:  card.querySelector(".title")?.textContent.trim() ?? "",
            price: card.querySelector(".price")?.textContent.trim() ?? "",
            stock: card.querySelector(".stock")?.textContent.trim() ?? "",
            url:   card.querySelector("a")?.href ?? "",
        }))
    """)

with open("catalog.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
```

Note the `wait_for_selector` before the read. An empty result is not a clean result: a
page that came back blocked, still loading, or challenged returns zero cards, and a scrape
that writes a header and no rows looks exactly like a scrape that ran fine on an empty
catalog. Assert the value is present before you trust it.

## The escaping problem: delimiters, newlines and accents inside a cell

Real cell text carries the three things that break a hand-built CSV.

- A **comma** inside a value (`"Jacket, black"`) becomes a column boundary if you join on
  commas yourself.
- A **line break** inside a value (a two-line address, a description with a hard return)
  becomes a row boundary.
- A **double quote** inside a value has to be doubled, or it closes the field early.

The CSV format has escaping rules for all three, formalized in
[RFC 4180](https://datatracker.ietf.org/doc/html/rfc4180): fields that contain the
delimiter, a newline or a quote are wrapped in double quotes, and literal quotes inside
are doubled. `csv.DictWriter` applies those rules for you. This is the entire reason to
use it over an f-string. The naive version below and the correct version above differ
only in whether the data happens to contain a comma today:

```python
# WRONG: splits columns the first time a name contains a comma,
# and breaks a row the first time a cell contains a newline.
line = ",".join([row["name"], row["price"], row["stock"], row["url"]])
f.write(line + "\n")
```

Do not build CSV by hand. The failure is silent and data-dependent: it works on every row
you tested and breaks on the one customer whose name has a comma in it.

## Encoding: why utf-8-sig and the BOM matter

The second silent failure is encoding. Scraped text is full of non-ASCII characters:
accented names, currency symbols, dashes, quotation marks pasted from a rich editor. Those
have to be written as UTF-8, and the file has to announce that it is UTF-8, or a
spreadsheet that defaults to a legacy code page renders `Munchen` as mojibake.

Opening the file with `encoding="utf-8-sig"` writes a byte order mark at the front. That
mark is the signal a common spreadsheet application reads to pick UTF-8 automatically
instead of guessing. Reading the same file back with `utf-8-sig` strips the mark
transparently, so your own resume step (below) is not confused by it.

Two more details in the `open()` call that are not optional:

- `newline=""` hands line-ending control to the `csv` module. Without it, on Windows you
  get a blank line between every row, because Python and the csv module both add a
  carriage return.
- `encoding="utf-8-sig"` on **both** the write and any later read, so the BOM is written
  once and never read as data.

```python
with open("catalog.csv", "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
```

## Crash-safe incremental writing for long crawls

To make a long crawl survive its own failure, append each page's rows as you go, `flush()`
and `os.fsync()` after every page, and on restart skip the keys already written. A catalog
crawl runs for an hour across dozens of pages. If it holds every row in memory
and writes once at the end, an exception on page 47, a killed process or a lost connection
throws away everything. The fix is to append each page's rows as you go, and to make the
file safe to resume.

Three things make the append safe:

- Open in append mode and write the header only when the file is new.
- After each page, `flush()` and `os.fsync()` so the rows are on disk, not sitting in a
  buffer the crash will discard.
- On restart, read back the URLs already written and skip them, so a resumed run does not
  duplicate rows.

```python
import csv
import os
from invisible_playwright import InvisiblePlaywright

FIELDS = ["name", "price", "stock", "url"]
OUT = "catalog.csv"

EXTRACT = """
    () => Array.from(document.querySelectorAll(".product-card")).map(card => ({
        name:  card.querySelector(".title")?.textContent.trim() ?? "",
        price: card.querySelector(".price")?.textContent.trim() ?? "",
        stock: card.querySelector(".stock")?.textContent.trim() ?? "",
        url:   card.querySelector("a")?.href ?? "",
    }))
"""

def already_written(path):
    if not os.path.exists(path):
        return set()
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return {row["url"] for row in csv.DictReader(f)}

done = already_written(OUT)
is_new_file = not os.path.exists(OUT)

with InvisiblePlaywright(seed=42) as browser, \
        open(OUT, "a", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    if is_new_file:
        writer.writeheader()
    page = browser.new_page()

    for n in range(1, 51):
        page.goto(f"https://example.com/catalog?page={n}")
        page.wait_for_selector(".product-card .price")
        rows = page.evaluate(EXTRACT)

        wrote = 0
        for row in rows:
            if not row["url"] or row["url"] in done:
                continue
            writer.writerow(row)
            done.add(row["url"])
            wrote += 1

        f.flush()
        os.fsync(f.fileno())
        print(f"page {n}: wrote {wrote} new rows")
```

Because the crash-safe file keys on the URL, the resume is exact rather than approximate:
you continue from the first row you had not yet committed, not from a page number you hope
was the right one. This is also why the extraction includes a stable `url` per row even
when you do not care about the URL as data. It is the dedup key. The same durable-checkpoint
idea, generalised beyond CSV, is the subject of
[resume an interrupted scrape with Playwright](how-to-resume-an-interrupted-scrape-playwright.md).

## One row = one entity: the shape decision CSV forces

The honest limitation. CSV is flat: a file is a rectangle of rows and columns, and it
cannot represent a record that nests. A product with a list of images, a set of size
variants each with its own stock count, or a review thread does not fit in one row, and
there is no correct automatic answer for how to flatten it.

So decide the grain up front, before you write a line: **what is one row?** If one row is
one product, a product's five variants collapse into a summary and you lose the per-variant
stock. If one row is one variant, the product's name and description repeat on all five
rows. Both are valid; you have to pick, and you have to pick before you design the
`FIELDS` list, because changing your mind later means re-scraping.

If your data is genuinely tabular already, the mechanics of reading it out of the page are
their own topic, covered in
[how to scrape HTML tables with Playwright](how-to-scrape-html-tables-playwright.md).
CSV is the right output when one entity really is one flat row. When the record genuinely
nests, a line-per-record format carries it without flattening: see
[how to scrape to JSON Lines with Playwright](how-to-scrape-to-json-lines-playwright.md).
When it is not, the fix is to choose a different grain, not to bury the delimiter and
pretend.

## Why a fixed seed belongs in a scraper that resumes

The examples pass `seed=42` on purpose. A resumable crawl is, by definition, more than one
process: it starts, dies, and restarts, possibly on a different day. If every launch drew
a fresh fingerprint, the second half of your file would be collected by a visibly
different browser than the first half, from the site's point of view.

Pinning the seed makes the whole file, across every resume, attributable to one consistent
browser identity: same GPU, same canvas hash, same fonts, same screen, run after run. A
partial file topped up next Tuesday looks like the same visitor coming back, not a new one
appearing where the old one stopped. It also makes a failure reproducible, which is the
[same reason to fix the identity while debugging any detection](playwright-detected-as-bot.md).
If you need one field held constant while the rest stays seed-derived, that is what
[pinning individual fingerprint fields](pinning.md) is for.

## Conclusion

The hard part of scraping to CSV is not the loop that writes rows. It is that scraped
values carry delimiters, newlines and non-ASCII text that a hand-built line silently
mangles, that a spreadsheet misreads UTF-8 written without a BOM, and that a long crawl
has to survive its own crash without corrupting the file. `csv.DictWriter` with
`encoding="utf-8-sig"` and `newline=""` handles the escaping and encoding; append mode with
`flush` plus `fsync` and a URL dedup key handles the crash. Pull the values from a real
rendered DOM so they are the numbers a human sees, decide one-row-is-one-entity before you
start, and pin the seed so a resumed file is one identity from top to bottom.

## Short answers to the questions that lead here

**How do I write scraped data to CSV in Python?** Use `csv.DictWriter`, not a comma join.
Open the file with `newline=""` and `encoding="utf-8-sig"`, write a header row, and write
each record as a dict. The writer escapes commas, quotes and newlines inside cells for you.

**Why is my CSV splitting into extra columns?** A cell contains a comma and you built the
line by hand. Let `csv.DictWriter` quote fields that contain the delimiter instead of
joining strings yourself.

**Why does my CSV show garbled accented characters in a spreadsheet?** It was written as
UTF-8 with no byte order mark, so the spreadsheet guessed a legacy code page. Write it with
`encoding="utf-8-sig"` so the file announces UTF-8.

**Why are my prices or stock numbers empty or wrong?** Those values are rendered by
JavaScript after the page loads. An HTTP fetch sees the markup before the script runs; a
real browser sees the value on screen. Drive Playwright and `wait_for_selector` on the
value before reading it.

**How do I make a long scrape survive a crash?** Append each page's rows in append mode,
call `flush()` then `os.fsync()` after each page, and on restart read back the keys already
written so you skip them. Do not hold everything in memory to write once at the end.

**How do I handle nested data in a flat CSV?** Decide what one row represents before you
design the columns. CSV cannot nest, so either one row is the parent and you summarise
children, or one row is a child and the parent columns repeat. Pick the grain up front.

## Sources

- [RFC 4180](https://datatracker.ietf.org/doc/html/rfc4180), the CSV format specification,
  for the delimiter/newline/quote escaping rules and the doubled-quote convention.
- The Python standard library `csv` module and its quoting rules, and the `utf-8-sig`
  codec's byte order mark behaviour.
- This project's own crash-safe append and resume pattern, keyed on a stable per-row URL,
  and the seed-reproducible identity that keeps a resumed file attributable to one browser.

**See also:** [how to scrape HTML tables with Playwright](how-to-scrape-html-tables-playwright.md)
for genuinely tabular pages, [how to export scraped data to Excel with Playwright](how-to-export-scraped-data-to-excel-playwright.md)
when a spreadsheet keeps mangling SKUs and codes, [Configuration](configuration.md) for
proxy and timezone setup on a long crawl, and [the quickstart](quickstart.md) for the
two-line switch from stock Playwright.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-cell bug in the
first section is one I shipped before adding the wait.*
