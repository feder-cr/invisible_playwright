---
title: "How to scrape to JSON Lines with Playwright"
description: "Scrape to JSON Lines with Playwright: why NDJSON, not one big JSON array, is the crash-safe format for a long crawl, plus the flush rule that keeps your data."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 54
---


# How to scrape to JSON Lines with Playwright

**To scrape to JSON Lines with Playwright, write one `json.dumps` object per line and
call `fh.flush()` after each write.** Every record is then a complete, valid line the
moment it lands, so a mid-crawl crash costs you at most the single line in flight instead
of the whole file. JSON Lines (also called NDJSON) is the append-safe alternative to
collecting everything into one big JSON array, which is only valid once its closing
bracket is written.

Scraped records are nested and ragged. One page yields a list of items, the next
yields one item with an extra field, a third yields none. A flat CSV cannot hold that
shape without inventing columns (that is the trade covered in
[how to scrape to CSV](how-to-scrape-to-csv-playwright.md)), so the instinct is to
collect everything into a list and write one big JSON array at the end. On a short run
that is fine. On a crawl of tens of thousands of pages it is the format most likely to
lose you a day of work.

This page is about the container, not the extraction: why a single top-level array is
the wrong shape for a long crawl, why JSON Lines is the append-safe one, the flush rule
that is the whole point, and how a fixed seed lets you resume a dead run without
changing the fingerprint the earlier lines were collected under.

## Why one big JSON array is the wrong container for a crawl

A JSON array is one syntactic object. It opens with `[`, closes with `]`, and is only
valid once the `]` is written. That single fact causes two problems that do not show up
until the run is long enough to matter.

The first is that you cannot append to it safely. To add a record you have to either
hold the whole list in memory until the end, or seek past the trailing `]`, overwrite
it, and rewrite it. Hold it in memory and a 40,000-page crawl carries 40,000 records of
RAM it does not need to. Rewrite the closing bracket every time and a single mistimed
crash leaves the file without one.

The second is the failure mode itself. If the process dies at page 12,000 while the
array is still open, the file on disk ends mid-record with no closing bracket. It is not
"12,000 records you can recover and 28,000 you lost". It is **invalid JSON**, top to
bottom, because a parser reads the whole array as one value and that value is
incomplete. `json.load` raises, and the 12,000 good records are trapped behind the
missing byte. Crawls die for reasons you do not control: an exit going dark, a page that
never settles, the machine getting a kill signal. The container has to survive that.

## JSON Lines is the append-safe shape

JSON Lines, also written NDJSON, drops the array and makes every line a complete JSON
value on its own. One `json.dumps(record)` per line, a newline, the next record. No
enclosing brackets, no commas between records, nothing that has to be closed at the end.

```jsonl
{"url": "https://example.com/p/1", "title": "First", "tags": ["a", "b"], "price": 12}
{"url": "https://example.com/p/2", "title": "Second", "tags": [], "price": null}
{"url": "https://example.com/p/3", "title": "Third", "specs": {"weight": "1kg"}}
```

Every property that made the array fragile is now the opposite. You append by writing one
more line and closing nothing. Each line is independent, so the file is valid after every
single write instead of only at the end. And the ragged shape is a non-issue: line two
carries an empty `tags` and a null `price`, line three carries a nested `specs` and no
`tags` at all, and neither line has to agree with the others on structure the way a CSV
column would demand.

The crash math is now the one you want. A 40,000-page run that dies at page 12,000 leaves
**12,000 valid, complete lines** and a partial 12,001st. You read the file line by line,
the 12,000 good ones parse, and you skip or truncate the one that did not finish. The
work up to the failure is money in the bank rather than a corrupt blob.

The contrast between the two containers, on the properties that matter for a long crawl:

| Property | One big JSON array | JSON Lines (NDJSON) |
|---|---|---|
| Valid on disk | Only after the closing `]` | After every single line |
| Append a record | Rewrite the trailing `]`, or hold all records in memory | Write one line, close nothing |
| Crash at page 12,000 of 40,000 | Whole file invalid, none recoverable by a standard parse | 12,000 valid lines plus one partial |
| Ragged / nested records | Fine, inside the one value | Fine, each line independent |
| Memory for a long crawl | Grows with the run | Constant |

## Stream a crawl to JSON Lines with the real API

The values come straight out of the rendered DOM.
[`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate) runs
JavaScript in the page and returns whatever that JavaScript returns, and if you return a
plain object or array it arrives in Python as a plain `dict` or `list`, already
serializable. There is no marshalling step to write and no schema to declare in advance:
the shape of the record is whatever the page function builds.

Switching from stock Playwright is the two-line change from the
[quickstart](quickstart.md), after which every Playwright method works as documented.
Here the browser drives the pages and a plain file handle takes the output:

```python
import json
from invisible_playwright import InvisiblePlaywright

urls = [f"https://example.com/p/{i}" for i in range(1, 40001)]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    with open("out.jsonl", "a", encoding="utf-8") as fh:
        for url in urls:
            page.goto(url, wait_until="domcontentloaded")

            # page.evaluate returns a plain dict; it lands in Python ready to dump
            record = page.evaluate(
                """() => ({
                    url: location.href,
                    title: document.querySelector('h1')?.textContent?.trim() ?? null,
                    tags: [...document.querySelectorAll('.tag')].map(t => t.textContent.trim()),
                    price: (() => {
                        const el = document.querySelector('[data-price]');
                        return el ? Number(el.dataset.price) : null;
                    })(),
                })"""
            )

            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()   # the line above is not on disk until this returns
```

Three details carry the crash-safety. The file is opened in append mode (`"a"`), so a
resumed run adds to the existing lines instead of truncating them. Each record is one
`json.dumps` followed by exactly one `"\n"`, so one line is one complete object and a
reader can split on newlines. And `ensure_ascii=False` keeps real text readable without
affecting validity. What page function you write is up to the page; the container around
it is what makes the run survivable.

## Flush discipline, or you lose the last record

The one caveat that turns this from "usually fine" into "actually crash-safe" is buffering.

`fh.write(...)` does not put bytes on disk. It copies them into an in-memory buffer that
the operating system flushes later, on its own schedule, and always when the file closes
cleanly. If the process exits normally, the buffer drains and you never notice it existed.
If the process is killed, the machine loses power, or an unhandled exception tears the
interpreter down before the buffer drains, everything still sitting in it is gone. That is
not one truncated line. Depending on the buffer size it can be the last several thousand
records, all of which your code "wrote" and none of which reached the file.

`fh.flush()` after each line forces the buffer out to the OS, so a kill at page 12,000
costs you at most the single record that was mid-write. If you want to survive a power cut
and not just a process kill, follow it with `os.fsync(fh.fileno())` to push past the OS
cache to the physical disk; it is slower, so reach for it only when the run genuinely
cannot tolerate losing the OS buffer. For most crawls, per-line `flush()` is the right
trade: the record is a finished object and it is on disk before the next `goto` can
crash the process.

The rule is small and it is the entire reason the format holds: **each line is a
complete object, and you flush before moving on.** Skip the flush and JSON Lines gives
you the same last-record loss the big array gave you the whole file, just quieter.

## Resume the crash under the same fingerprint

JSON Lines makes resuming cheap: read the URLs you already have and skip them. But there
is a stealth-specific reason to resume under the *same identity* you started with, and it
is the honest caveat of this whole approach.

Every session gets a full fingerprint, and by default a fresh one each run. If a 40,000
page crawl dies at 12,000 and you relaunch with a new random identity, page 12,001 is now
served to a different machine than pages 1 through 12,000: a different GPU, different
canvas hash, different fonts, different screen. To a site that fingerprints, one logical
crawl has suddenly become two visitors mid-session, and a fingerprint that changes partway
through a linked set of requests is itself a signal, not a fix. Pinning the identity is
what makes a failure reproducible in the first place, which the
[detection checklist](playwright-detected-as-bot.md) calls the single highest-value
debugging habit.

Passing a fixed `seed` is the resume mechanism. The same seed produces the same machine
every time, so page 12,001 continues under the identical fingerprint that produced the
first 12,000 lines:

```python
import json
import os

done = set()
if os.path.exists("out.jsonl"):
    with open("out.jsonl", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["url"])   # complete lines parse; a torn last line is skipped
            except json.JSONDecodeError:
                pass   # the one record the crash truncated

remaining = [u for u in urls if u not in done]

with InvisiblePlaywright(seed=42) as browser:   # same seed, same fingerprint as the first 12,000 lines
    page = browser.new_page()
    with open("out.jsonl", "a", encoding="utf-8") as fh:
        for url in remaining:
            page.goto(url, wait_until="domcontentloaded")
            record = page.evaluate("() => ({ url: location.href /* ... */ })")
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
```

The `try/except` around `json.loads` is where the two ideas meet: the good lines parse
and populate the skip set, and the single record the crash truncated raises and is
ignored, so the resume starts exactly where the valid data ended. If you also want to pin
individual fields such as a specific GPU or screen while leaving the rest seed-derived,
that is what [pinning fingerprint fields](pinning.md) covers.

## Conclusion

The extraction is the interesting part and the container is the part that loses you a
day. A single JSON array cannot be appended to safely and turns any mid-crawl crash into
one invalid file. JSON Lines writes one finished `json.dumps` object per line, stays
valid after every write, and turns the same crash into 12,000 recoverable records plus one
you throw away. The values come out of `page.evaluate` as plain dicts with no
serialization dance. The two things you owe the format are that each line is a complete
object and that you flush before the next page can kill the process, and a fixed seed lets
you resume the run under the identity that produced the lines you already have.

## Short answers to the questions that lead here

**Should I write scraped data as one JSON array or JSON Lines?** JSON Lines for anything
that runs long enough to crash. An array is only valid once it is closed, so a crash
leaves an invalid file; JSON Lines is valid after every line.

**What happens to my file if the crawl crashes halfway?** With JSON Lines, every complete
line up to the crash is still valid and readable, and only the record that was mid-write
is lost. With one big array, the whole file is invalid because the closing bracket was
never written.

**Do I need to serialize the DOM data myself?** No. `page.evaluate` returning a plain
object or array arrives in Python as a `dict` or `list`, which `json.dumps` writes
directly. You only build the shape you want inside the page function.

**Why does my last batch of records go missing even though the code wrote them?**
Buffering. `write` fills an in-memory buffer that only reaches disk on flush or clean
close. A killed process loses whatever is still buffered, so call `fh.flush()` after each
line.

**How do I resume a crawl without redoing the finished pages?** Read the existing
`.jsonl`, collect the URLs already present, and skip them. Wrap the parse in a try/except
so the one truncated line does not stop the load.

**Why keep the same seed when I resume?** So the second half of the crawl runs under the
same fingerprint as the first half. A new random identity mid-crawl turns one visitor
into two, which is its own signal.

## Sources

- This project's own crawl runs, where a 40,000-page job that died partway left every
  complete JSON Lines record intact and only the truncated final line unusable.
- The `json` module's `dumps` behaviour and Python file-object buffering, which is why the
  per-line flush is load-bearing rather than decorative.
- Playwright's own [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate)
  documentation, for the plain-object return value that lands in Python ready to serialize.
- The real wrapper API in [quickstart](quickstart.md) and [configuration](configuration.md).

**See also:** [how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
when the data is in a JSON endpoint rather than the rendered DOM,
[how to resume an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md)
for the resume pattern in full, and [configuration](configuration.md) for proxies and
timezone on a long crawl.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The array-versus-JSON-Lines
lesson cost a partial crawl before it cost a paragraph.*
