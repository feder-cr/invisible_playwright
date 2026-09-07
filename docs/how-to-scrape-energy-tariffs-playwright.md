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
