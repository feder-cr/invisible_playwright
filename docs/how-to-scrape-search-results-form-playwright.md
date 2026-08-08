---
title: "Scrape search results by driving a form in Playwright"
description: "Drive a search form in Playwright so its input and change events fire, get past a JS-gated submit button, and tell real results from a zero-results page."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 66
---


# Scrape search results by driving a form in Playwright

**To scrape search results from a form in Playwright, set the fields with `fill`, `type`
and `select_option` so the page's own `input` and `change` handlers run, wait for the
gated submit button to enable itself, then race the results container against the
zero-results element so an empty answer is recorded rather than mistaken for a timeout.**
Run that across the full set of queries under one seeded identity, at a human pace, so the
whole matrix reads as one visitor searching rather than a swarm of machines.

A lot of the data worth having is not on a page you can request by URL. It sits behind
a search form: a couple of typed query fields, one or two dropdowns where the second
depends on the first, and a submit button that either navigates to a results page or
fires an XHR and repaints in place. There is no list to page through until you have
asked the form a question.

This is a how-to for asking that question from Playwright: setting field values so the
page believes a human set them, getting past a submit button that stays disabled until
the site's own validation runs, telling a real results grid from a zero-results page
before you harvest anything, and doing all of that across hundreds of query
permutations without the run reading as one machine flipping switches.

The examples use `invisible_playwright`, which returns a stock Playwright `Browser`, so
every method below is the ordinary Playwright API. If you are on plain Playwright the
same calls apply; the identity and event-trust parts are where the two diverge, and
that is called out where it matters.

## Set field values so the handlers actually fire

**Set every field with Playwright's `fill`, `type` and `select_option`, never by assigning
`element.value` from injected JavaScript.** Those methods drive the field the way the
browser does for a person, so the `input` and `change` events fire and the form's own
validation and enable-submit logic actually run.

The mistake that wastes the most time here is setting a field's value directly and
wondering why the submit button never enables. A search form is usually wired to its
own `input` and `change` events: it validates as you type, it enables submit only once
the required fields are non-empty, and it populates the dependent dropdown in response
to a `change` on the first one. If you assign `element.value = "..."` from injected
JavaScript, the character lands in the box and not one of those handlers runs. The form
looks filled and behaves empty.

Playwright's `fill` and `type` do not have that problem. They drive the field the way
the browser drives it for a person, so the `input` and `change` events fire in order
and the page's own validation sees them:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/search")

    # fill dispatches input+change, so validation and the enable-submit
    # logic both run, exactly as they would for a person typing
    page.fill("#query", "blue widgets")

    # a dependent dropdown: pick the parent, wait for the child to
    # populate in response to the change event, then pick the child
    page.select_option("#region", "west")
    page.wait_for_selector("#city option:nth-child(2)")
    page.select_option("#city", "portland")
```

`select_option` fires `change` too, which is why the pattern above works: selecting the
region triggers the site's handler, the city dropdown fills in, and only then is there
a second option to select. If you tried to set both in one shot you would select a city
into an empty list.

There is a deeper reason `fill` and `type` are the right tools, and it is the one that
survives a site that checks harder than "is the field non-empty". The events they
generate carry `isTrusted: true`, because a real input pipeline produced them. Events
synthesized in page script report `isTrusted: false`, and a form that reads that flag
can accept your keystrokes and silently score the submission. With this project the
trust is genuine at the engine level rather than reconstructed in JavaScript, which is
the whole point of [why its clicks and keystrokes report isTrusted](playwright-clicks-istrusted.md).

## Get past a submit button that is gated

**Wait for a gated submit button to enable itself once every field the site's own
validation checks has actually been set, rather than forcing the click.** Once the
fields are set correctly the button usually enables itself, because you have satisfied
the exact validation the gate was waiting on. The failure to plan for is the one where
it does not: a required field you missed, an async validity check that has not
resolved, a dependent dropdown still empty.

Do not force it. Clicking a disabled button, or removing the `disabled` attribute from
script and clicking, both diverge from what a person can do and neither submits the form
the site expects. Wait for the button to become enabled on its own, and treat a timeout
as a signal that a field is still wrong:

```python
# wait for the site's own validation to enable submit; a timeout here
# means a field is unsatisfied, not that you should force the click
page.wait_for_selector("#submit:not([disabled])", timeout=10_000)
page.click("#submit")
```

From here the form does one of two things. It navigates to a results URL, in which case
`page.wait_for_load_state("networkidle")` or waiting for the results container is enough.
Or it fires an XHR and repaints without a navigation, in which case there is no load
event to wait on and the useful move is to read the response directly. The submitted
query and its answer both live in that request, and [capturing the XHR the form fires](how-to-capture-xhr-api-responses-playwright.md)
is often cleaner than scraping the grid it renders, because you get structured JSON
instead of parsed HTML.

## Tell a results grid from a zero-results page

**After submit, wait for the results container or the zero-results element and race the
two, so a genuine empty answer is recorded as data rather than mistaken for a slow load.**
A row count of zero cannot, on its own, tell an empty result from a page that never
finished loading.

This is the step people skip and then silently corrupt a dataset with. After submit,
the page can be in three states, not two: results, a genuine zero-results message, or
still loading. A selector that waits only for the results container will hang on a
zero-results page until it times out, and code that treats that timeout as "no results"
cannot tell it apart from a slow network or a layout change.

Assert the positive signal for whichever state you are in, and race the two so the
loading state resolves into one of them:

```python
# race the two terminal states; whichever appears first tells you
# which page you got, and a genuine empty result is a first-class
# outcome rather than a timeout
page.wait_for_selector("#results .row, #no-results", timeout=15_000)

if page.query_selector("#no-results"):
    rows = []            # a real zero-results answer, recorded as such
else:
    rows = [
        r.inner_text()
        for r in page.query_selector_all("#results .row")
    ]
```

The reason to wait for `#no-results` explicitly, rather than infer emptiness from a row
count of zero, is the same reason a suppressed signal is treated as a failure across the
rest of this project: an empty container and a container that never loaded look
identical if all you check is the count. Waiting for the page to positively declare
"no matches" distinguishes a real zero from a scrape that ran too early, and it turns a
class of silent data loss into a case you handle on purpose.

## Iterate permutations under one identity

**Run the whole matrix of queries under one seeded identity at a human pace, not a fresh
browser per query.** A fixed `seed` holds the fingerprint constant across every
permutation, so the site sees one consistent visitor searching a lot rather than a stream
of new machines hitting the same endpoint.

You rarely want one query; you want the form asked a matrix of them,
regions crossed with categories crossed with date ranges. The instinct is to spread that
across fresh contexts so each query starts clean. Resist it, for two reasons that pull
the same direction.

First, a search form asked hundreds of questions in a short window is a velocity signal
on its own, and re-drawing the browser fingerprint per query does not hide the velocity,
it adds a second tell on top of it. A site that fingerprints will see a stream of
distinct machines all hitting the same search endpoint from one network, which is a
worse story than one consistent visitor doing a lot of searching. Hold one identity
across the whole matrix. A fixed `seed` gives you exactly that: the same GPU, canvas,
audio and font surface on every permutation, so the fingerprint is a constant instead of
a flickering variable.

```python
from invisible_playwright import InvisiblePlaywright

QUERIES = [
    {"region": "west", "category": "hardware"},
    {"region": "west", "category": "software"},
    {"region": "east", "category": "hardware"},
    # ... the full matrix
]

def run_query(page, q):
    page.goto("https://example.com/search")
    page.select_option("#region", q["region"])
    page.wait_for_selector("#category option:nth-child(2)")
    page.select_option("#category", q["category"])
    page.wait_for_selector("#submit:not([disabled])", timeout=10_000)
    page.click("#submit")
    page.wait_for_selector("#results .row, #no-results", timeout=15_000)
    if page.query_selector("#no-results"):
        return []
    return [r.inner_text() for r in page.query_selector_all("#results .row")]

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for q in QUERIES:
        rows = run_query(page, q)
        print(q, "->", len(rows), "rows")
        page.wait_for_timeout(4_000)   # pace it; a search endpoint hit
                                       # flat-out is the velocity signal
```

Second, the pacing is not politeness, it is part of the disguise. The `wait_for_timeout`
between permutations, and ideally a jittered one rather than a flat interval, keeps the
request rate inside what a person could produce. If a results page paginates, treat each
permutation's grid the way you would any list and [step through its result pages](how-to-scrape-paginated-pages-playwright.md)
before moving to the next query, rather than firing a fresh search for every page.

One more thing about reuse. Reusing the same `page` across permutations, as above, is
correct and cheaper than a new context each time, but revisit `example.com/search`
fresh for each query so a stale results grid from the previous permutation cannot be
scraped as if it belonged to the current one. The `goto` at the top of `run_query`
does exactly that.

## Run it where the results are the same as production

**Route every permutation through the exit you actually intend, and let the browser's
timezone follow that exit, because a search that geo-filters returns a different grid, or
none, for a request that appears to come from the wrong country.**

A form scrape has one extra failure mode over a static scrape: the results themselves
can depend on where the request appears to come from. A search that geo-filters will
return a different grid, or a zero-results page, for an exit in the wrong country, and
you will harvest a real page that is simply the wrong one. Route every permutation
through the exit you actually intend, and let the browser's timezone follow that exit so
the session tells one story; the proxy and timezone handling for that is in
[configuration](configuration.md).

The seed and the exit together make a run reproducible: same identity, same network
path, same answers. When a permutation returns something surprising you can replay that
exact query under that exact identity instead of guessing whether the site changed or
your machine did.

## Conclusion

Driving a search form well is four disciplines stacked. Set values with `fill`, `type`
and `select_option` so the page's own `input` and `change` handlers run and the trusted
events reach a form that checks for them. Wait for a gated submit to enable itself rather
than forcing it, and read the XHR when submit fires one. Race the results and
zero-results states so an empty answer is a recorded outcome and not a timeout. And run
the whole permutation matrix under one seeded identity at a human rate, because a fixed
fingerprint asked many questions slowly is a far quieter thing than a new fingerprint per
question asked fast.

## Short answers to the questions that lead here

**Why does my form fill leave the submit button disabled?** You almost certainly set the
value directly, which does not fire the `input` and `change` events the enable-submit
logic listens for. Use `fill` or `type` so those events run.

**How do I fill a dropdown whose options depend on another dropdown?** Select the parent
with `select_option`, wait for the child's options to populate in response to the change
event, then select the child. Doing both at once selects into an empty list.

**How do I tell no results from a page that has not loaded yet?** Wait for the page to
positively show its zero-results element, racing it against the results container. A row
count of zero cannot distinguish an empty answer from an early read.

**Should I use a fresh browser for each query to look less repetitive?** No. Re-drawing
the fingerprint per query does not hide the request velocity and adds a second tell. Hold
one seeded identity across the whole matrix and pace the requests instead.

**Should I click a disabled submit button by removing the disabled attribute?** No. That
diverges from what a person can do and skips the validation the gate is enforcing. Fix
the field that is keeping it disabled and let it enable itself.

**The form submits an XHR instead of navigating. How do I get the data?** There is no
load event to wait for, so read the response directly rather than scraping the repainted
grid. The submitted query and its results both live in that request.

## Sources

- The real `invisible_playwright` API as documented in this set: `InvisiblePlaywright`
  returns a stock Playwright `Browser`, and a fixed `seed` reproduces the full
  fingerprint surface across runs.
- This project's own rule that a suppressed or empty signal is a failure to be asserted
  against, not a pass to be inferred, applied here to the zero-results state.
- Playwright's [documented input methods](https://playwright.dev/python/docs/input)
  (`fill`, `type`, `select_option`, `wait_for_selector`) and their event behaviour.

**See also:** [capture the XHR an API-backed form returns](how-to-capture-xhr-api-responses-playwright.md),
[why driven clicks and keystrokes report isTrusted](playwright-clicks-istrusted.md), and
[stepping through paginated result pages](how-to-scrape-paginated-pages-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The results-versus-empty
race is here because inferring emptiness from a zero row count quietly loses data on any
form slow enough to lose it.*
