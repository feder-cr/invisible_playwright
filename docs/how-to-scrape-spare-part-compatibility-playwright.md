---
title: "How to scrape spare part compatibility with Playwright"
description: "Scrape fitment and compatibility tables with Playwright: drive cascading dropdowns that repopulate from the server, capture the whole combination as the key, and keep the exceptions the site prints."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 167
---


# How to scrape spare part compatibility with Playwright

To scrape compatibility data, drive the **cascading dropdowns** in order and record the
full combination with every result. These selectors are the classic make, model, year,
variant chain, where each choice repopulates the next from the server, and the answer only
exists at the end of a complete path.

That structure has a consequence people underestimate: the dataset is the cross product,
and it is large. A parts catalogue with 40 makes, 30 models each, 20 years and 4 variants
is 96,000 paths. Walking it exhaustively is both slow and a load pattern no catalogue will
tolerate, so the first design decision is which slice you actually need.

This page covers driving the cascade reliably, keying results on the full path, and
keeping the fitment exceptions that make compatibility data trustworthy.

## Drive the cascade, waiting for each level to repopulate

```python
from invisible_playwright import InvisiblePlaywright

def options(page, selector):
    return page.eval_on_selector_all(
        f"{selector} option",
        "els => els.map(e => ({value: e.value, label: e.textContent.trim()}))"
            ".filter(o => o.value && o.value !== '0')",
    )

def choose(page, selector, value, next_selector):
    before = page.eval_on_selector(next_selector, "e => e.options.length")
    page.select_option(selector, value)
    page.wait_for_function(
        "args => document.querySelector(args.sel).options.length !== args.n",
        arg={"sel": next_selector, "n": before},
    )
```

Waiting on the option count of the **next** control is what makes this reliable. Waiting a
fixed time is a race that fails on a slow response and wastes seconds on a fast one, and
waiting for a network idle event misses the case where the site repopulates from data it
already had. The count changes exactly when the level below is ready.

Where the chain repopulates without a full page load, capturing the underlying responses
directly can be faster than reading the DOM at each step; the mechanics are in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## The key is the whole path, not the part number

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://parts.example.com/fitment")

    rows = []
    for make in options(page, "select#make"):
        choose(page, "select#make", make["value"], "select#model")
        for model in options(page, "select#model"):
            choose(page, "select#model", model["value"], "select#year")
            for year in options(page, "select#year"):
                choose(page, "select#year", year["value"], "select#variant")
                for variant in options(page, "select#variant"):
                    page.select_option("select#variant", variant["value"])
                    page.click("button#findParts")
                    page.wait_for_selector(".part-result, .no-fit")
                    for part in page.query_selector_all(".part-result"):
                        rows.append({
                            "make": make["label"], "model": model["label"],
                            "year": year["label"], "variant": variant["label"],
                            "part_number": part.query_selector(".sku").inner_text().strip(),
                            "part_name": part.query_selector(".name").inner_text().strip(),
                        })
```

One row per combination per part is the shape that answers the real question, which runs in
both directions: what fits this vehicle, and what does this part fit. A schema keyed on
part number with a compatibility blob attached answers the first well and the second badly.

## Fitment notes are the difference between right and nearly right

Catalogues attach qualifiers to a fit: a build date cutoff, an engine code, a market, a
"with air conditioning" condition. These are the exceptions that make the difference
between the correct part and a return:

```python
                        note = part.query_selector(".fitment-note")
                        row["fitment_note"] = note.inner_text().strip() if note else None
```

Never drop these. A compatibility dataset without qualifiers is confidently wrong on
exactly the vehicles where the answer matters, and the failure is expensive in the physical
world rather than only in the data.

Capture the negative case too. A combination that returns no parts is information, and
storing it prevents you re-walking that path on the next run:

```python
                    if page.query_selector(".no-fit"):
                        rows.append({"make": ..., "variant": ..., "part_number": None,
                                     "result": "no_fit"})
```

## Slice before you sweep

Decide the scope explicitly rather than starting at the top of the cross product:

```python
    TARGET_MAKES = ["Example Motors"]
    TARGET_YEARS = range(2018, 2027)
```

A single make across nine years is a few thousand queries, which is a night's polite work.
The whole catalogue is not, and a run that tries it will be stopped partway with an
incomplete dataset and a blocked address. Where the site publishes a bulk fitment file for
trade customers, that is the honest route and worth asking about.

Make the run resumable, because at this scale it will be interrupted. Write each
combination as it completes and checkpoint the path you were on, using the pattern in
[resuming an interrupted scrape](how-to-resume-an-interrupted-scrape-playwright.md), with
[retrying failed requests](how-to-retry-failed-requests-playwright.md) for the transient
failures a long walk will meet.

## Pace it like the database query it is

Every combination is a lookup against a real catalogue database, not a cached page. Keep a
deliberate delay between combinations and run outside the retailer's trading hours where
you can. The reasoning, and why a self-imposed limit beats being told, is in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

## A complete resumable walk of one slice

```python
import json, time
from invisible_playwright import InvisiblePlaywright

TARGET_MAKES = ["Example Motors"]
PATH = "fitment.jsonl"

def done_paths(path):
    seen = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                seen.add((r["make"], r["model"], r["year"], r["variant"]))
    except FileNotFoundError:
        pass
    return seen

seen = done_paths(PATH)

with InvisiblePlaywright(seed=42) as browser, open(PATH, "a", encoding="utf-8") as out:
    page = browser.new_page()
    page.goto("https://parts.example.com/fitment", wait_until="domcontentloaded")

    for make in options(page, "select#make"):
        if make["label"] not in TARGET_MAKES:
            continue
        choose(page, "select#make", make["value"], "select#model")
        for model in options(page, "select#model"):
            choose(page, "select#model", model["value"], "select#year")
            for year in options(page, "select#year"):
                choose(page, "select#year", year["value"], "select#variant")
                for variant in options(page, "select#variant"):
                    key = (make["label"], model["label"], year["label"], variant["label"])
                    if key in seen:
                        continue
                    page.select_option("select#variant", variant["value"])
                    page.click("button#findParts")
                    page.wait_for_selector(".part-result, .no-fit", timeout=25000)

                    base = dict(zip(("make", "model", "year", "variant"), key))
                    parts = page.query_selector_all(".part-result")
                    if not parts:
                        out.write(json.dumps({**base, "result": "no_fit",
                                              "observed_at": time.time()}) + "\n")
                    for part in parts:
                        note = part.query_selector(".fitment-note")
                        out.write(json.dumps({
                            **base,
                            "part_number": part.query_selector(".sku").inner_text().strip(),
                            "part_name": part.query_selector(".name").inner_text().strip(),
                            "fitment_note": note.inner_text().strip() if note else None,
                            "observed_at": time.time(),
                        }, ensure_ascii=False) + "\n")
                    out.flush()
                    page.wait_for_timeout(2500)
```

Resuming from the written rows rather than a checkpoint file is what makes an interrupted
walk safe to restart at any point, including after a crash that never got to write a
checkpoint. Writing the `no_fit` row is what stops the resume from re-walking every
combination that legitimately has no parts.

## Catalogues protect fitment data specifically

Fitment is the most valuable asset a parts retailer has, more than prices, because it is
expensive to compile and it is what makes their search work. The defences reflect that.

**The cascade is the rate limiter.** Each level costs a query, so a full walk is thousands
of requests whatever the delay between them. Slicing by make is not only politeness, it is
the difference between a run that completes and one that is cut off partway with an
unusable partial dataset.

**Refusal appears as an empty next level.** When the site decides to stop serving you, the
model dropdown comes back with only its placeholder, which the code reads as a make with no
models. Guard on it, because it is the failure that silently ends a sweep:

```python
    models = options(page, "select#model")
    if not models:
        raise RuntimeError(f"{make['label']}: no models returned, treat as a refusal")
```

**A stripped client is refused at the first cascade step.** The repopulation request is
issued by the page and validated against its session. Driving the real controls in a real
browser is what keeps the chain answering, and the diagnosis order when it stops is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

Where a trade or bulk fitment feed exists, ask for it. It is a normal commercial request,
it is exactly this data in a form built to be consumed, and it removes the whole problem.

## The schema, and why it must be queryable in both directions

One row per combination per part, with `no_fit` rows kept:

| field | note |
|---|---|
| `make`, `model`, `year`, `variant` | the full path, never collapsed to a vehicle id |
| `part_number`, `part_name` | the catalogue's own identifiers |
| `fitment_note` | the qualifier that makes the fit conditional |
| `result` | `no_fit` where the combination returned nothing |
| `observed_at` | catalogues correct fitment, and corrections matter |

Both directions then work with a single index each: which parts fit this vehicle, and which
vehicles take this part. The second is the one a schema keyed on part number with an
embedded compatibility blob cannot answer without unpacking every row, and it is the query
that matters commercially, because it is the one that tells you how much inventory risk a
part carries.

## Short answers to the questions that lead here

**How do I wait for a cascading dropdown correctly?** Wait on the option count of the
next control, not on a fixed timeout. Each level repopulates the one below it, and a
sleep long enough today is a race tomorrow.

**What identifies a fitment row?** The whole path, not the part number. A part fits a
combination, and the question runs in both directions: which parts fit this vehicle,
and which vehicles take this part. Only the full path answers both.

**Why keep the fitment notes as text?** Because they are the difference between a
right part and a wrong one: a build-date cutoff, an engine code, a market, an
equipment condition. A boolean fit with the qualifier discarded is worse than no
answer.

**Should I sweep the whole cross product?** No. Decide the slice explicitly. Every
combination is a lookup against a real catalogue database, not a cached page, so the
cost is carried by the catalogue and the sweep is the most visible thing you can do.

**See also:** [How to capture XHR and API responses in
Playwright](how-to-capture-xhr-api-responses-playwright.md), [How to resume an
interrupted scrape with
Playwright](how-to-resume-an-interrupted-scrape-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Locators, https://playwright.dev/python/docs/api/class-locator - waiting
  on the state of a dependent control, checked for the cascade this page drives.
- Playwright, Network, https://playwright.dev/python/docs/network -
  `page.expect_response()`, for the catalogues that answer each cascade level with a
  request you can read directly.
