---
title: "How to scrape insurance quotes with Playwright"
description: "Scrape insurance quotes with Playwright: fill the whole multi-step form fresh per profile, read only the final-step response, and capture every tier a tabbed result renders in one request."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 131
---


# How to scrape insurance quotes with Playwright

To scrape insurance quotes with Playwright, treat the quote as the output of the
entire risk profile rather than of any one field: fill the whole multi-step form
fresh for each profile instead of editing and resubmitting a partial one, wait for
the response on the actual final step rather than any mid-flow screen, and read
that response's body directly so a single request yields every coverage tier the
page renders one at a time through a tab.

A quote form looks like a sequence of small, independent questions: age here,
postcode there, coverage level on the next screen. It is not. The number that
comes back is a function of the whole form together, and the form's own session
handling punishes the instinct to change one answer and keep the rest. This page
covers the three places that instinct breaks a scrape: reusing a quote after
changing one field, reading a screen before the real price has been computed, and
missing tiers that a single response already contains.

## A quote is a function of the whole form, not of one field

Read the price for a profile, change the deductible, and the temptation is to
patch that one number and reuse everything else you already read. That produces a
wrong quote that looks exactly like a right one. Insurers price risk from the
combination of every field on the form, age against coverage level against
deductible against location, and the interactions between those fields are not
additive. A ten-year age difference can matter more or less depending on the
coverage tier already selected, and a lower deductible can move the premium by a
different percentage at different coverage levels. There is no way to know which
fields interact without asking the server, because the pricing logic lives there
and not in the page.

```python
from invisible_playwright import InvisiblePlaywright

profile = {
    "age": 34,
    "postcode": "10001",
    "coverage_level": "standard",
    "deductible": 500,
}

# WRONG: read a quote, mutate one field in your own dict, and reuse
# the rest of a previously read result. The number you keep is stale
# the moment any field changes, because it was never a function of
# that one field alone.

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/quote", wait_until="domcontentloaded")
    # every field below is filled from the CURRENT profile, every time
    page.get_by_label("Age").fill(str(profile["age"]))
    page.get_by_label("Postcode").fill(profile["postcode"])
    page.get_by_label("Coverage level").select_option(profile["coverage_level"])
    page.get_by_label("Deductible").select_option(str(profile["deductible"]))
```

The rule that follows is blunt: a new profile means a fresh run through the whole
form, not a diff against the last one. If you are comparing ten deductible values
for the same driver, that is ten full passes through every field, not one pass
plus nine patches.

## Multi-step forms keep state server-side, and going back invalidates silently

A quote form that spans several screens is not stateless between them. Most save
each step's answers server-side against a session id, often carried in a cookie
or a hidden token that gets forwarded on every subsequent request. That is
normal, and by itself harmless. The part that breaks a scrape is what happens
when you go back to change an earlier field: the server frequently drops or
recomputes every field entered after the one you changed, without telling you.
The page you see may still show old values in later fields that the server has
already discarded internally.

```python
from invisible_playwright import InvisiblePlaywright

def fill_quote_form(page, profile):
    page.goto("https://example.com/quote/step-1", wait_until="domcontentloaded")
    page.get_by_label("Age").fill(str(profile["age"]))
    page.get_by_label("Postcode").fill(profile["postcode"])
    page.get_by_role("button", name="Continue").click()

    page.wait_for_url("**/step-2")
    page.get_by_label("Coverage level").select_option(profile["coverage_level"])
    page.get_by_label("Deductible").select_option(str(profile["deductible"]))
    page.get_by_role("button", name="Continue").click()

    page.wait_for_url("**/step-3")
    page.get_by_label("Add-ons").set_checked(profile.get("addons", False))
    return page
```

Calling `fill_quote_form` again for a new profile, from a fresh `new_page()`,
costs a few more requests than editing step one and clicking forward through
steps you already answered. It also never produces a quote where one field
reflects the new profile and another silently reflects a value the server
already threw away. Do not try to detect which fields survived a back
navigation; the safe pattern is not going back at all.

## The real price is the final-step response, not what a mid-flow screen shows

An intermediate screen often shows a running number, a preliminary estimate that
updates as you add fields. Insurers frequently apply add-on pricing and taxes
only on the last step, after the last confirmation, so the number rendered on
step three of four is not the number you will be quoted at step four. Reading
that intermediate number and reporting it as the quote produces a figure that is
systematically low, and it will not match the price the same insurer's own
summary email or confirmation page shows for the same inputs.

The fix is mechanical: know which network response is the actual final quote,
and read only that one. `expect_response` while triggering the last submission
does this without depending on which DOM element the page decides to update.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page = fill_quote_form(page, profile)  # from the section above

    with page.expect_response(
        lambda r: "/quote/finalize" in r.url and r.request.method == "POST"
    ) as caught:
        page.get_by_role("button", name="Get my quote").click()

    final = caught.value.json()
    total_premium = final["total_premium"]   # taxes and add-ons already applied
```

If you cannot identify the finalize endpoint by URL, fall back to reading the
rendered summary page after that last click has fully resolved, but confirm it
against at least one known value first. A screen that says "estimated" anywhere
in its copy is not the final-step response, no matter how confident the number
looks.

## Quotes expire, and a stored one past that window is not comparable

A quote is valid for a stated window, sometimes shown on the page as "valid for
30 days", sometimes enforced only as a session timeout with no visible copy at
all. Either way, a quote fetched last week for a given profile is not the same
data point as a quote fetched today for the same profile, even though the input
fields are identical. Rates move, and a comparison that mixes fresh and stale
quotes for the same profile will show variance that has nothing to do with the
profile and everything to do with when each number was pulled.

```python
import time

def is_quote_fresh(fetched_at, validity_seconds=1800):
    return (time.time() - fetched_at) < validity_seconds

# store fetched_at alongside every quote row, and refuse to compare
# a row whose window has closed against one just pulled
```

Store the fetch timestamp with every row you keep, and treat two quotes for the
same profile as comparable only if both were fetched inside a window short
enough that the underlying rates plausibly did not move between them. What
counts as short enough depends on the insurer's own stated validity, when it
states one; absent that, re-pull rather than trust an old row.

## One response can carry every coverage tier at once

Some quote pages render basic, standard and premium coverage as three tabs, one
visible at a time, with the user clicking between them to compare. It is easy to
assume that means three separate quote requests, one per tab, and to drive the
page through three clicks to collect all three. Frequently the opposite is true:
the server already returned all three tiers in the single response that
generated the page, and the tab UI is client-side filtering over data that
already arrived. Clicking a tab in that case fires no network request at all,
because there is nothing left to fetch.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    with page.expect_response(
        lambda r: "/quote/finalize" in r.url and r.request.method == "POST"
    ) as caught:
        page = fill_quote_form(page, profile)
        page.get_by_role("button", name="Get my quote").click()

    payload = caught.value.json()
    tiers = payload.get("tiers") or payload.get("plans")
    for tier in tiers:
        print(tier["name"], tier["monthly_premium"])
```

Check the response body before writing a single line of tab-clicking code. If
`tiers` or an equivalent key holds all three plans, you have every price the
page can show from one request, and no amount of clicking through the tab UI
adds a fourth data point. Only build the click-through path if the response
genuinely holds one tier and the other two arrive on demand, which you confirm
by watching the network panel while clicking a tab yourself first.

## Vary the pace across profiles, not just the fields

A comparison run against many profiles, ages 25 through 65 in steps of five,
five coverage levels each, is naturally a lot of form submissions in a row from
one identity. That shape, many full-form submissions back to back with no pause,
is exactly what a rate limiter on a quote endpoint exists to catch, because a
real visitor requests one or two quotes in a sitting, not forty. Space the
submissions and keep the browser identity stable across the run rather than
starting a fresh one per profile, since a new fingerprint on every request reads
as more machines than a comparison shopper plausibly uses.

```python
import random

rng = random.Random(42)   # same seed as the browser, reproducible pacing

with InvisiblePlaywright(seed=42) as browser:
    results = []
    for profile in profiles_to_check():
        page = browser.new_page()
        page = fill_quote_form(page, profile)
        with page.expect_response(
            lambda r: "/quote/finalize" in r.url and r.request.method == "POST"
        ) as caught:
            page.get_by_role("button", name="Get my quote").click()
        results.append(caught.value.json())
        page.close()
        time.sleep(rng.randint(4, 11))   # the page is closed; pause off it
```

The pause between profiles is doing the same job the pause between clicks does
in a [load-more scrape](how-to-scrape-load-more-button-playwright.md): it keeps
a long, mechanically identical sequence from reading as one identical action
repeated on a stopwatch.

## Where this stops

This technique reads publicly available quote figures, the same numbers any
visitor sees by entering a hypothetical profile into a public quote form, for
comparison research. It does not collect personal data, because the profiles
driving it are inputs you chose, not information about a real person. Any flow
that asks for identity verification, a real policy number, an existing customer
login, or anything that ties the quote to a specific person's actual coverage is
out of scope for what is described here. Those flows are gated for a reason that
has nothing to do with scraping mechanics, and reading a network response past
that gate is a different problem than the one this page solves.

## Conclusion

An insurance quote is not a number attached to one field, it is the output of a
whole risk profile, and the form's own session handling actively works against
patching a single answer and resubmitting. Fill the form fresh per profile, treat
only the final-step response as the real quote, respect the stated or observed
validity window before comparing two quotes, and check the response body for
every tier before writing code to click through tabs that may already be
client-side. The mechanics here are ordinary Playwright: `expect_response`,
`fill`, `select_option`. What is not ordinary is knowing which response is the
one that matters and which one is a preview.

## Short answers to the questions that lead here

**Can I change one field and reuse the rest of a quote I already read?** No. The
premium is computed from every field together, and interactions between fields
are not predictable from the outside. Run the whole form again for the new
profile.

**Why did my quote come out lower than the price shown on the confirmation
page?** You likely read an intermediate screen. Add-ons and taxes are frequently
applied only at the final step, so the real quote is the response from that
step, not a running total shown earlier in the flow.

**I went back to change my age and now the deductible looks reset. What
happened?** Multi-step forms often invalidate fields entered after the one you
changed when you go back, without showing it clearly. Fill the form fresh
instead of editing and resubmitting.

**Is a quote from three days ago still valid for comparison?** Only if it is
still inside the insurer's stated validity window or a session timeout that has
not lapsed. Rates move, so an expired quote and a fresh one for the same profile
are not the same data point.

**The page shows three coverage tiers through tabs. Do I need to click all
three?** Check the response body from the final submission first. Many pages
return every tier in that one response and use the tabs only to filter what is
already loaded, in which case one request gets you all three prices.

**Does this collect anyone's personal insurance information?** No, as described
here. The profiles are hypothetical inputs for comparison, not a real person's
data, and any flow requiring identity verification or an existing policy number
is outside what this technique covers.

## Sources

Retrieved 2026-08-28.

- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`wait_for_url`](https://playwright.dev/python/docs/api/class-page#page-wait-for-url),
  used exactly as documented upstream to catch the finalize request rather than
  the rendered screen.
- Playwright's [`Locator.select_option`](https://playwright.dev/python/docs/api/class-locator#locator-select-option)
  and [`get_by_label`](https://playwright.dev/python/docs/api/class-page#page-get-by-label),
  the two calls that fill a multi-step form's fields in the examples above.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the general technique behind reading a finalize response instead of a
rendered screen, [scraping flight prices](how-to-scrape-flight-prices-playwright.md)
and [scraping hotel prices](how-to-scrape-hotel-prices-playwright.md) for two
other domains where the displayed price and the final price diverge, and
[scraping a search results form](how-to-scrape-search-results-form-playwright.md)
for the mechanics of filling and submitting a form Playwright has to drive step
by step.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. A deductible
comparison run once patched one field into a cached quote instead of resubmitting
the form, and every row in that comparison table was wrong by an amount that only
showed up when a manual spot check disagreed with the script.*
