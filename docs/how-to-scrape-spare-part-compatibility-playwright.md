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
