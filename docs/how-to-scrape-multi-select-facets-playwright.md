---
title: "How to scrape multi-select facet filters with Playwright"
description: "Scrape multi-select facet filters with Playwright: expand show-more first, wait for the facet response before reading counts, store the state URL on every row, and sweep one group at a time."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 107
---


# How to scrape multi-select facet filters with Playwright

To scrape multi-select facet filters with Playwright, expand every "show more" control
before reading a group, wait for the facet response, not for the URL to change,
write the resulting URL into every row you store, and crawl one facet group at a time
against the unfiltered base instead of enumerating combinations, then reconcile each group
against the base total.

A facet count is not a property of the facet. It is the answer the site's index gave to a
question that already contained every other filter active at that moment. "Blue (124)" with
nothing else ticked and "Blue (18)" with a size selected are both correct and answer
different questions. Store either without the state that produced it and you have a number
nobody can check or reproduce.

This page is the crawl that avoids that: the row shape that survives a second run, the
repaint race that hands you stale counts, the values that are not in the DOM until you
expand something, and why one pass per group beats walking the combinations.

## A count means nothing without the state that produced it

Every number beside a facet is computed against the current filter state, so ticking one
thing recomputes every other number on the page. The unit you store is therefore not a
count, it is a triple: which facet, under which state, at what time.

There is a second rule most sites follow and almost nobody expects: counts inside a group
are computed with that group's own selections removed. Tick "Blue" and the number beside
"Red" does not become "blue and red", it becomes "what you would get if you ticked Red
too". So the active group's counts stay identical to the unfiltered base while every other
group moves. That is also the test for it.

The cheapest reproducible representation of state is the URL, so that is what the row
carries. Keep the raw label text too: a count mis-parsed out of "Blue (1.2k)" can be
re-derived, and a rounded count stored as an integer cannot be told apart later from an
exact one.

## Read the group in one round trip, and expand it first

Sites truncate facet groups. The top five or eight values render and the rest sit behind a
"show more" control, and in the common case those values are not in the document at all
until it is clicked. A scraper that reads the list on load captures the head of the
distribution and calls it the whole vocabulary.

Tell that apart from the CSS-hidden variant, because they fail differently. If the values
are present and hidden, `all_inner_texts()` returns empty strings for them while
`text_content()` returns the real text, and a run of blank labels is the signature. If they
are genuinely absent, the list-item count grows after the click, and that growth is the
thing to wait on.

```python
import re
from playwright.sync_api import TimeoutError as PlaywrightTimeout

def expand_group(group, max_clicks=20):
    """Click show-more until the group stops growing."""
    more = group.get_by_role("button", name=re.compile(r"show more|see all", re.I))
    for _ in range(max_clicks):
        if more.count() == 0 or not more.first.is_visible():
            break
        before = group.locator("li").count()
        more.first.click()
        try:
            # the (before)th index existing means the list grew past its old length
            group.locator("li").nth(before).wait_for(state="attached", timeout=5000)
        except PlaywrightTimeout:
            break   # the control is still there but adds nothing: the group is complete

def read_group(group):
    expand_group(group)
    return group.locator("li[data-facet]").evaluate_all("""
        nodes => nodes.map(n => ({
            raw: n.textContent.trim(),
            value: n.getAttribute("data-facet"),
            ariaDisabled: n.getAttribute("aria-disabled"),
            inputDisabled: !!n.querySelector("input:disabled"),
        }))
    """)
```

`evaluate_all` is doing real work there. Four properties read through separate Playwright
calls is four round trips per value, fine for eight values and painful for two hundred and
forty. One call returns the group, and `textContent` reads through CSS hiding, so the same
code covers both truncation styles.

## A hidden zero and a disabled zero are different facts

A value shown greyed out with "(0)" is a positive statement: the site is telling you the
value exists in its vocabulary and currently has no items. Collect those from the
unfiltered base and you have the group's complete value list, which is the schema to check
the dataset against later.

A value that disappears tells you nothing. You cannot distinguish "zero under this state"
from "this site has no such value" from "it was behind a show-more you did not expand".
Absence is not evidence, and a coverage report built on it will claim gaps that are
artifacts of the read.

Read the flag explicitly instead of asking whether the row is clickable. A `disabled`
attribute on a `<li>` or an `<a>` is ignored by the browser, since only native controls
like `input` and `button` can be disabled that way, so such a row stays clickable. What
means something is `aria-disabled` on the row and the `disabled` property on the input
inside it, which is why `read_group` captures both.

## Wait for the response, not for the URL

Selecting a facet fires a request and rewrites the address bar, and those are two different
moments. The URL rewrite runs inside the click handler, before the request has even been
sent. Read the counts when the URL changes and you get the previous state's numbers stamped
with the new state's URL: every row looks plausible and the whole table is shifted one step.

Wait on the response instead, and better still read it, since the payload usually carries
the new counts and skipping the DOM removes the repaint race completely. Capturing the
request instead of the repainted markup is a habit worth having generally, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) covers
the hooks.

```python
def is_facet_query(response):
    return "/search" in response.url and response.request.resource_type in ("xhr", "fetch")

def apply_facet(page, control, results_selector):
    url_before = page.url
    with page.expect_response(is_facet_query) as caught:
        control.click()
    response = caught.value            # the new state, before anything repaints

    # pushState already ran in the click handler, so the URL moved first.
    page.wait_for_function("u => location.href !== u", arg=url_before)
    page.locator(results_selector).first.wait_for(state="visible")
    return page.url, response
```

Keep the URL even when you parse the payload: it is the one artifact that lets someone
re-derive the row without replaying your clicks. Facet state appears either as a repeated
key, `color=blue&color=red&size=l`, or as a comma list, `color=blue,red`. Neither triggers
a document load, which is the whole subject of
[scraping an SPA that changes URL via the history API](how-to-scrape-spa-history-url-changes-playwright.md).

## Measure the OR and AND semantics, do not assume them

Values inside one group are almost always OR: blue or red. Groups combine with AND: (blue
or red) and size large. A second value in the same group widens the set, a size narrows it.

Getting it backwards is expensive both ways. Assume AND inside a group and you crawl
intersections that are nearly all empty, then conclude the site has no inventory. Assume OR
across groups and the state space explodes while the same items come back over and over.
Three measurements settle it, and they are worth repeating per group, because an amenities
group is sometimes AND on purpose.

```python
def group_semantics(page, base_url, values, total, apply):
    """Returns 'or', 'and' or 'ambiguous' for one facet group."""
    page.goto(base_url, wait_until="domcontentloaded")
    base = total(page)                # also the denominator for reconcile()

    page.goto(base_url, wait_until="domcontentloaded")
    apply(page, values[0])
    one = total(page)

    apply(page, values[1])            # second value, same group, first still on
    two = total(page)

    if two > one:
        return "or", base             # a union can only grow
    if two < one:
        return "and", base            # an intersection can only shrink
    return "ambiguous", base          # try another pair; this one added nothing
```

`base` is not decoration in that function. It is what the group's remaining counts get
compared against to find out whether the group is excluded from its own counts, and it is
the denominator for the reconciliation below.

## Sweep one group at a time against the unfiltered base

Enumerating combinations is not merely slow, it is arithmetically hopeless. Six groups of
ten values, any subset allowed per group, is 1024 states per group and about 1.15
quintillion overall. Restrict it to one value per group and it is still 1,771,561 states.

Sweep one group at a time and the cost stops being a product and becomes a sum: six groups
of ten values is sixty states. Every state is one facet applied to the clean base, which is
also the only state whose count you can interpret without a footnote.

Reset by loading the base URL again, not by unticking. Unticking is another request, and a
half-cleared group is the commonest source of a row carrying the wrong state.

```python
import random
from datetime import datetime, timezone

def sweep_group(page, base_url, group_name, values, rng, results_selector="#results"):
    rows = []
    for value in values:
        page.goto(base_url, wait_until="domcontentloaded")
        page.wait_for_timeout(rng.randint(700, 2400))   # read time, not a metronome

        control = page.locator(f'li[data-facet="{value}"] input')
        state_url, response = apply_facet(page, control, results_selector)

        rows.append({
            "group": group_name,
            "value": value,
            "count": response.json().get("total"),
            "raw_label": page.locator(f'li[data-facet="{value}"]').text_content(),
            "state_url": state_url,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows
```

Every row now carries its own URL, so any single number in the output can be re-checked by
opening one address. That is what makes the dataset auditable, and it costs one string per
row.

## Reconcile the sweeps, and find what the facets miss

Sum the counts of one group and compare that sum to the base total. Three outcomes, and
each changes what you do next.

Equal means the group partitions the catalogue: every item has exactly one value, and
crawling the group reaches every item once. Greater than the base means items carry several
values there, one product listed under two of them, so the per-facet crawls overlap and the
union has to be deduplicated on an item id.

Smaller than the base is the important one. Some items have no value in that group at all,
and they are unreachable through it, so a crawl that walks only the facets will silently
miss them and raise no error anywhere. Crawl the base separately.

```python
def reconcile(rows, base_total):
    counted = sum(r["count"] for r in rows if r["count"] is not None)
    delta = counted - base_total
    if delta == 0:
        return "partition", 0
    if delta > 0:
        return "overlapping", delta      # multi-valued items: dedupe on item id
    return "incomplete", delta           # items with no value here are unreachable
```

Here is where the approach stops helping. One group at a time gives marginals, never the
joint distribution, so "how many blue and large" needs that exact state visited. Pairs are
quadratic and usually affordable, triples usually are not, so pick the pairs you need
instead of generating them. A capped result set forces the same targeted split, and the
[paginated pages](how-to-scrape-paginated-pages-playwright.md) mechanics apply to each
slice unchanged.

## Pace the sweep so it does not read as switch-flipping

Sixty facet states is sixty queries against the site's search index, the most expensive
path it owns and the one most likely to be rate limited. A visitor ticks two or three boxes
and reads for a while. A sweep ticks sixty in ninety seconds, each a clean single-facet
query from the same session. That traffic shape is recognisable without anyone looking at a
fingerprint.

Two things keep it reasonable. Hold one seeded identity across the whole sweep and keep the
same page and context, so cookies and session state persist the way they would for one
person refining a search rather than sixty machines each asking one question. Then vary the
gap, the `rng.randint` call in `sweep_group`, seeded from the value you pass to the browser
so the timing is reproducible too.

```python
from invisible_playwright import InvisiblePlaywright

SEED = 42
rng = random.Random(SEED)          # same seed as the identity: one reproducible run

with InvisiblePlaywright(seed=SEED) as browser:
    page = browser.new_page()
    page.goto(BASE_URL, wait_until="domcontentloaded")

    groups = {name: [v["value"] for v in read_group(page.locator(sel))]
              for name, sel in GROUP_SELECTORS.items()}

    rows = []
    for name, values in groups.items():
        rows.extend(sweep_group(page, BASE_URL, name, values, rng))
```

Resist fanning the facets out across parallel pages. Parallelism belongs at the item level,
once the sweep has told you which items exist, not at the facet level where it turns a
paced sequence into a burst.

## Conclusion

Facet crawling fails in ways that produce data rather than errors, which is what makes it
worth being careful about. The counts are state-dependent, so every row carries the URL
that produced it or it cannot be checked. The URL moves before the numbers do, so wait on
the response. A value that vanished tells you nothing while a value greyed out at zero
tells you a lot. Measure the OR and AND rules instead of assuming them, sweep one group at
a time so the cost is a sum rather than a product, and reconcile against the base total,
because the gap is exactly the set of items your facet crawl will never see.

## Short answers to the questions that lead here

**Why do the facet counts change when I tick a different filter?** Each count is computed
against the current filter state, not against the catalogue, so the same facet legitimately
reports different numbers under different states. Store the state URL with every count.

**Do I have to visit every combination of facets?** No, and you cannot: six groups of ten
values is about 1.15 quintillion subsets. Crawl one group at a time against the unfiltered
base, then visit specific combinations only when you need that exact joint figure.

**Are facets in the same group AND or OR?** Almost always OR inside a group and AND across
groups, but measure rather than assume: tick one value, note the total, tick a second in
the same group. A union grows, an intersection shrinks.

**Half the filter values are missing from my scrape. Where are they?** Behind a show-more
control, and usually not in the DOM at all until it is clicked. Expand until the list stops
growing, and wait on the item count growing rather than on a timeout.

**Should I record a facet that shows zero?** Yes when it is shown disabled, because that is
the site confirming the value exists in its vocabulary. A value that disappeared entirely
carries no information, and treating its absence as a zero invents data.

**Why are my counts always one step behind?** You waited for the URL. The address bar is
rewritten in the click handler, before the request resolves, so the visible counts are
still the previous state's. Wait for the facet response, then read.

## Sources

- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  [`wait_for_function`](https://playwright.dev/python/docs/api/class-page#page-wait-for-function),
  [`Locator.evaluate_all`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate-all)
  and [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  retrieved 2026-08-28 and used exactly as documented upstream. The browser returned here
  is a real Playwright `Browser`.
- Playwright's [actionability notes](https://playwright.dev/python/docs/actionability),
  retrieved 2026-08-28, for why a `disabled` attribute on a non-form element is ignored by
  the browser while `aria-disabled` on the row is not.
- This project's own behaviour notes on interaction cadence, where a fixed-interval action
  is recorded as a signature in the same way a uniform scroll is.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the facet payload instead of the repainted DOM,
[scraping an SPA that changes URL via the history API](how-to-scrape-spa-history-url-changes-playwright.md)
for why no load event fires when a facet is applied,
[scraping search results by driving a form](how-to-scrape-search-results-form-playwright.md)
for the query side of the same interface, and
[scraping accordion and tab content](how-to-scrape-accordion-and-tab-content-playwright.md)
for the general "hidden or absent" test the show-more case is one instance of.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Reading the counts as soon as
the URL changed is the mistake this page corrects: the address bar updates inside the click
handler and the numbers repaint after the response, so a whole facet table came back shifted
by one step and every row in it looked plausible.*
