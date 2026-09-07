---
title: "How to scrape energy tariff comparisons with Playwright"
description: "Scrape energy tariff comparison sites with Playwright: drive the consumption form that produces the quote, keep standing charge and unit rate apart, and record the assumptions behind every annual figure."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 165
---


# How to scrape energy tariff comparisons with Playwright

To scrape energy tariffs, drive the **consumption form** and record what you entered.
These sites do not have prices, they have quotes, and a quote is a function of postcode,
annual consumption, meter type and payment method. Two runs with different inputs return
different tariffs at different prices, and a row that does not carry its inputs cannot be
compared with anything, including itself.

The second thing to separate is the tariff's two components. A tariff is a standing charge
per day plus a unit rate per kilowatt hour, and the headline annual figure is those two
combined with an assumed consumption. Store the components; recompute the annual figure
when you need it.

This page covers driving the quote form, keeping the components apart, and recording the
assumptions that make a number reproducible.

## Fill the form the way it expects, in order

```python
from invisible_playwright import InvisiblePlaywright

INPUTS = {
    "postcode": "BS1 4DJ",
    "annual_kwh_electricity": 2700,
    "annual_kwh_gas": 11500,
    "payment_method": "direct_debit",
    "meter": "single_rate",
}

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-compare.com/energy")

    page.fill("input[name='postcode']", INPUTS["postcode"])
    page.click("button#continue")

    page.check("input[value='know_usage']")           # reshapes the form
    page.fill("input[name='elecKwh']", str(INPUTS["annual_kwh_electricity"]))
    page.fill("input[name='gasKwh']", str(INPUTS["annual_kwh_gas"]))
    page.select_option("select[name='payment']", INPUTS["payment_method"])
    page.click("button#showResults")
    page.wait_for_selector(".tariff-result")
```

The `check` before the fills is the step that catches people. These forms branch on
whether you know your usage, and filling consumption fields that are still hidden silently
does nothing, so the quote comes back on the site's default assumption instead of yours.
This is a multi-step flow, and the ordering discipline is the one in
[scraping multi-step wizard flows](how-to-scrape-multi-step-wizard-flow-playwright.md).

## Keep standing charge and unit rate as separate fields

```python
    results = []
    for card in page.query_selector_all(".tariff-result"):
        def t(sel):
            n = card.query_selector(sel)
            return n.inner_text().strip() if n else None
        results.append({
            "supplier": t(".supplier-name"),
            "tariff": t(".tariff-name"),
            "unit_rate_text": t(".unit-rate"),          # p/kWh
            "standing_charge_text": t(".standing-charge"),  # p/day
            "annual_estimate_text": t(".annual-cost"),
            "contract_length": t(".contract-term"),
            "exit_fee_text": t(".exit-fee"),
            **{f"input_{k}": v for k, v in INPUTS.items()},
        })
```

Copying the inputs into every row looks redundant and is the single most valuable thing in
the schema. It makes each row self-describing, so a table assembled from ten runs across
five postcodes remains analysable a month later, when nobody remembers which run was which.

A tariff with a low unit rate and a high standing charge beats one with the opposite only
above a certain consumption. That crossover is the actual question these datasets can
answer, and it is unanswerable from the annual figure alone.

## Dual fuel, exit fees and the fields that decide the choice

Three fields change the ranking more than the price does:

- **Fuel scope.** A dual fuel quote and two single fuel quotes are different products, and
  comparison sites mix them in one list. Record which each row is.
- **Exit fee.** A cheap fixed tariff with a large exit fee is not cheap if rates fall.
- **Contract length and end date.** A twelve month fix quoted today ends on a date, and
  that date is what a rolling comparison has to line up.

Take them as text and parse later, the same discipline as elsewhere: "None", "£30 per
fuel" and "£75" are all common, and only one of them is a number.

## The quote is a moment, so timestamp it and expect it to move

```python
    row["quoted_at"] = time.time()
```

Energy tariffs change frequently and comparison sites cache aggressively. Two runs an hour
apart can differ, and a run against a cached result set can return tariffs that are already
withdrawn. Where the page shows a validity or a "prices correct as of" line, take it, and
prefer it to your own clock when the two disagree.

## Do not create an account, and do not submit a switch

Everything above is the public quote engine. These sites also offer to switch you, which
requires personal details and creates a real commercial transaction. That boundary is worth
stating plainly: reading a quote is scraping, and submitting a switch is entering a
contract on behalf of a person.

Stay on the public side, keep runs to a handful of profiles rather than a sweep of every
postcode, and follow the pacing in
[rate limiting your own scraper](how-to-scrape-without-getting-blocked.md). A comparison
engine runs a real pricing computation per query, so each request costs the operator
meaningfully more than serving a page.

## Store it long, one row per tariff per run

Append rather than overwrite, with the inputs and timestamps attached, in
[JSON Lines](how-to-scrape-to-json-lines-playwright.md) or
[a SQLite database](how-to-scrape-into-a-database-playwright.md). The value of this data
is entirely in the series: which suppliers moved, when, and how the crossover consumption
shifted. A table holding only today's best deal throws away everything that made it worth
collecting.

## A complete run over several consumption profiles

```python
import json, time
from invisible_playwright import InvisiblePlaywright

PROFILES = [
    {"postcode": "BS1 4DJ", "elec": 1800, "gas": 7500, "payment": "direct_debit"},
    {"postcode": "BS1 4DJ", "elec": 2700, "gas": 11500, "payment": "direct_debit"},
    {"postcode": "BS1 4DJ", "elec": 4200, "gas": 18000, "payment": "direct_debit"},
]

def quote(page, profile):
    page.goto("https://example-compare.com/energy", wait_until="domcontentloaded")
    page.fill("input[name='postcode']", profile["postcode"])
    page.click("button#continue")

    page.check("input[value='know_usage']")            # reshapes the form first
    page.wait_for_selector("input[name='elecKwh']:not([disabled])")
    page.fill("input[name='elecKwh']", str(profile["elec"]))
    page.fill("input[name='gasKwh']", str(profile["gas"]))
    page.select_option("select[name='payment']", profile["payment"])
    page.click("button#showResults")
    page.wait_for_selector(".tariff-result, .no-tariffs", timeout=40000)

    if page.query_selector(".no-tariffs"):
        return [{"result": "no_tariffs", **{f"input_{k}": v for k, v in profile.items()}}]

    validity = page.query_selector(".prices-correct-as-of")
    return [{
        **row,
        "prices_as_of": validity.inner_text().strip() if validity else None,
        **{f"input_{k}": v for k, v in profile.items()},
    } for row in read_results(page)]

with InvisiblePlaywright(seed=42) as browser, open("tariffs.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    for profile in PROFILES:
        for row in quote(page, profile):
            row["quoted_at"] = time.time()
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(8000)
```

Three consumption profiles rather than one is what makes the dataset answer the question
that matters. The cheapest tariff at 1,800 kWh is frequently not the cheapest at 4,200,
and that crossover is invisible from a single quote no matter how often you repeat it.

The wait for `input[name='elecKwh']:not([disabled])` is the guard on the branch: it fails
loudly if the form did not reshape, instead of quietly filling a hidden field and taking
the site's default assumption.

## A pricing engine is an expensive request, and behaves like one

Comparison sites differ from most scraping targets in that each query runs a real
computation against supplier tariffs. Three consequences.

**Slow is normal, and a timeout is not a block.** Forty seconds for a result set is
ordinary at peak. Treat a timeout as a retry candidate rather than as a refusal, using the
backoff shape in
[retrying failed requests](how-to-retry-failed-requests-playwright.md).

**Cached result sets serve withdrawn tariffs.** The engine may return a set computed
minutes ago, which is why `prices_as_of` is captured whenever the site prints it. Where two
runs an hour apart return identical results with the same stamp, you read a cache twice
rather than confirming stability.

**Volume is what draws attention, not identity.** A handful of profiles a day is
indistinguishable from a household comparing options. A sweep of every postcode is a
different traffic shape entirely and is the one thing guaranteed to end the access. That is
a limit worth writing into the script rather than remembering, and the general argument is
in [rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

## The schema, and the crossover query it exists for

One row per tariff per profile per run:

| field | why |
|---|---|
| `unit_rate_text`, `standing_charge_text` | the two components, unparsed |
| `annual_estimate_text` | the site's own arithmetic, kept for comparison |
| `input_elec`, `input_gas`, `input_postcode`, `input_payment` | the query, copied down |
| `contract_length`, `exit_fee_text` | what decides the ranking beyond price |
| `prices_as_of`, `quoted_at` | their clock and yours |

With the components and several profiles, the crossover falls out as arithmetic rather
than another scrape: for two tariffs, the consumption at which their annual costs are equal
is the difference in standing charges divided by the difference in unit rates. That number
is the actual advice a household needs, it is not published anywhere, and it is
unreachable from the annual estimate the site puts in large type.
