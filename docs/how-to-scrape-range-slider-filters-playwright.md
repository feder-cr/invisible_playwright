---
title: "How to scrape range slider filters with Playwright"
description: "Scrape range slider filters with Playwright: read aria-valuenow instead of calling fill, prefer the URL parameter, wait for the debounced response, and sweep the field in half-open buckets with logarithmic edges."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 112
---


# How to scrape range slider filters with Playwright

To scrape range slider filters with Playwright, do not move the handle: read the widget's
`aria-valuemin`, `aria-valuemax` and `aria-valuenow` to learn the scale, push the range in
through the URL parameter when the site has one and through the underlying input's native
value setter when it does not, wait for the debounced response instead of a timeout, and
sweep the field in explicitly half-open buckets whose edges are spaced logarithmically when
the values have a tail.

Almost none of that is what the widget invites you to do. A price track with two handles
looks like something you drag, and dragging it is the one approach that cannot be made
reliable, because the value the page records is derived from a pixel and then rounded to a
step you did not choose. The reliable paths go around the handle entirely.

This page is the crawl that goes around it: what the control is under the styling, why the
numbers at the ends of the track move when you touch an unrelated filter, and the two
arithmetic mistakes that make a range sweep produce totals nobody can reconcile.

## A slider gives you a scale, not a vocabulary

A checkbox facet hands you a list. The values exist, the site names them, and crawling that
group is a walk across a set somebody else defined, which is the job in
[multi-select facet filters](how-to-scrape-multi-select-facets-playwright.md).

A slider hands you two numbers and the space between them. There is no vocabulary to
enumerate, so the buckets are not discovered, they are invented by you. Every boundary in your
output is an artifact of your crawl instead of a property of the site.

So a range row carries five things, not two: the lower bound, the upper bound, whether each
end is open or closed, the count, and the state URL that produced it.

Drop the open and closed flags and two runs with different bucket sizes cannot be summed. Drop
the URL and no number in the table can be re-checked. Keep the value you asked for beside the
value the widget snapped to, because those are routinely different.

## Find out what the control is before you try to move it

The word slider describes the appearance, not the element. A native `input[type=range]` is
rare on a commercial filter panel, because it cannot be styled into a two-handle track with a
coloured segment between the handles. What you get instead is a `div` carrying `role="slider"`
and the ARIA value attributes, often with a hidden input behind it holding the real form value.

That distinction decides which calls can possibly work. `fill()` on the div raises an error
saying the element is not an input, a textarea or a contenteditable element, which is the good
outcome: it fails loudly.

On a real `input[type=range]` the same call succeeds, because Playwright special-cases that
input type, sets the value directly and fires `input` and `change`. So `fill()` is either the
entire answer or instantly fatal, and one probe tells you which.

```python
def describe_slider(page, selector):
    """Read what the control actually is, before deciding how to move it."""
    return page.locator(selector).evaluate("""
        el => {
            const input = el.matches("input") ? el : el.querySelector("input");
            const track = el.closest("[class*=track], [class*=slider]");
            return {
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute("role"),
                valuemin: el.getAttribute("aria-valuemin"),
                valuemax: el.getAttribute("aria-valuemax"),
                valuenow: el.getAttribute("aria-valuenow"),
                valuetext: el.getAttribute("aria-valuetext"),
                inputType: input ? input.type : null,
                inputStep: input ? input.step : null,
                inputValue: input ? input.value : null,
                trackWidth: track ? track.getBoundingClientRect().width : null,
            };
        }
    """)
```

Two fields there carry the weight. `inputStep` is the quantum every value gets rounded to, and
its default for a range input is 1, so a slider over money in cents that never declares a step
rounds silently to whole units.

`valuetext` is the human string, and on a non-linear scale it is the only place the real value
appears: plenty of price sliders keep `aria-valuenow` as a position between 0 and 100 and put
the currency amount in `aria-valuetext`. Read `valuenow` there and you store track positions
labelled as prices.

## The URL parameter beats the widget whenever it exists

Most filter panels write their state into the query string, and a range is usually two
scalars, `price_min=100&price_max=500`, or one packed value, `price=100-500`. Where that
parameter exists it is the better interface: one `goto`, no debounce, no pointer events, no
step rounding, and a row somebody else can reproduce without replaying your clicks.

Test it once, at the start, and let the answer decide the shape of the whole scraper.

```python
from urllib.parse import urlencode

def same_number(attr, wanted):
    try:
        return float(attr) == float(wanted)
    except (TypeError, ValueError):
        return False

def url_range_round_trips(page, base_url, params, lo_sel, hi_sel, lo, hi, total):
    """One request decides whether you ever need to touch a handle."""
    page.goto(f"{base_url}?{urlencode(params)}", wait_until="domcontentloaded")
    low = describe_slider(page, lo_sel)
    high = describe_slider(page, hi_sel)
    return {
        "handles_moved": same_number(low["valuenow"], lo) and same_number(high["valuenow"], hi),
        "result_count": total(page),   # the handles are not the evidence, this is
        "bounds": (low["valuemin"], high["valuemax"]),
    }
```

Three outcomes follow. The handles land and the count moves: use the URL and never touch the
widget again. The handles land and the count does not: the page paints the parameter but
filters client-side after hydration, so you still have to fire the component. Nothing lands:
the state is in a POST body or in storage.

A fourth case resembles the first. Some panels paint the handles from the parameter, then
derive the result set from somewhere else, which is
[an SPA rewriting its URL through the history API](how-to-scrape-spa-history-url-changes-playwright.md)
rather than reading it. That is why the check above returns the count.

## Set the value, do not drag the handle

Dragging is the obvious approach and the one that produces numbers you cannot defend, because
the widget turns a horizontal pixel into a value.

The mapping is roughly `min + (x - trackLeft) / trackWidth * (max - min)`, then snapped to the
step. A 300 pixel track spanning 0 to 1,000,000 makes one pixel worth more than three thousand
units, so no drag can address a value finer than that however carefully the mouse moves. Most
implementations also subtract the handle's own width from the usable track, which biases every
position in one direction.

[Dragging manually](drag-and-drop-playwright-firefox-drag-to.md) covers the gesture for the
components that genuinely need it, and some do: a slider using pointer capture ignores a value
set behind its back.

Two better routes exist. When there is an input behind the div, write to it through the
prototype's native value setter, which gets past the component's own value tracker, then
dispatch `input` and `change`.

When there is no input, use the keyboard. The ARIA slider pattern requires Arrow keys to move
by exactly one step and Home and End to reach the bounds, so `Home` plus a counted run of
presses lands on an exact step with no geometry at all.

```python
NATIVE_SET = """
(el, value) => {
    const input = el.matches("input") ? el : el.querySelector("input");
    if (!input) return null;
    const setter = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(input), "value").set;
    setter.call(input, String(value));       // gets past the component's value tracker
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
    return input.value;                      // what the element rounded it to
}
"""

def set_by_input(page, selector, value):
    return page.locator(selector).evaluate(NATIVE_SET, value)

def set_by_keyboard(page, selector, value, step):
    """For a div[role=slider] with nothing behind it. One arrow press is one step."""
    handle = page.locator(selector)
    handle.focus()
    handle.press("Home")                     # a known endpoint, no pixels involved
    start = float(handle.get_attribute("aria-valuenow"))
    for _ in range(max(0, round((value - start) / step))):
        handle.press("ArrowRight")
    return handle.get_attribute("aria-valuenow")
```

Whichever route you take, read the value back. Assigning to `.value` on a range input runs the
HTML value sanitization algorithm, which clamps to the bounds and rounds to the nearest step,
so the element can hold a different number from the one you gave it and never says so.

The number that belongs in your row is the one you read back. If `aria-valuenow` does not move
after a single arrow press, the widget has no keyboard handling and the drag is all that is
left.

## The bounds describe the current result set, not the catalogue

Here is the property with no equivalent on a checkbox facet. `aria-valuemin` and
`aria-valuemax` are almost never the catalogue's minimum and maximum. They are the minimum and
maximum of whatever the current filter state returns, recomputed every time any other filter
changes.

Select a brand and the top of the price track drops to that brand's most expensive item.
Compute buckets from bounds read on the unfiltered page, apply them under that brand, and most
come back empty while the first holds everything. Every count is correct and the sweep is
worthless.

So re-read the bounds after every change to any other filter, never cache them across states,
and store them beside the counts so a later reader can see which scale the buckets were cut
from.

When min equals max the field has one distinct value in the current set, which is an answer
rather than an error. When the slider comes back disabled the result set is empty, and a
disabled slider is not a zero, it is the absence of a scale.

Displayed bounds are also rounded for a tidy label, outward or inward. That is why the sweep
below gives the first bucket no lower bound and the last no upper bound: an unbounded outer
edge costs nothing and catches whatever sits outside a rounded display.

## Wait for the debounced response, not for a timeout

A slider emits continuously while it moves, so every implementation debounces. The request goes
out a few hundred milliseconds after the last change, and every number on the page is stale
until it lands.

`wait_for_timeout` is wrong in both directions. Too short and you read the previous state's
total, too long and a sweep of sixty buckets spends a minute waiting. Worse, a timeout cannot
tell "the debounce has not fired yet" from "this range returned no items", and those two
produce an identical empty page.

Wait on the response instead, and match it against the values you asked for. A component that
fired several times during a drag will answer for an intermediate position first.

```python
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import TimeoutError as PlaywrightTimeout

def apply_range(page, lo, hi, set_value, key_lo="min", key_hi="max", timeout=10000):
    """Catch the response carrying OUR bounds, not an intermediate one."""
    def is_our_query(response):
        if response.request.resource_type not in ("xhr", "fetch"):
            return False
        query = parse_qs(urlparse(response.url).query)
        return query.get(key_lo) == [str(lo)] and query.get(key_hi) == [str(hi)]

    try:
        with page.expect_response(is_our_query, timeout=timeout) as caught:
            set_value(page, lo, hi)          # dispatches input AND change
        return caught.value.json()
    except PlaywrightTimeout:
        # Not "no results". The component never saw the event.
        raise RuntimeError(f"slider did not fire for [{lo}, {hi})")
```

Read the payload while you are there. It usually carries the new total and the new bounds
together, which removes the repaint race, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) covers the
hooks. The debounce is the mechanism behind
[a typeahead](how-to-scrape-autocomplete-typeahead-playwright.md), with one difference: a
typeahead fires on every keystroke you send, a slider only if the component recognised your
event.

That is why the timeout branch above raises rather than returning an empty result. Silence
there almost always means the component listens for `input` and you dispatched only `change`,
or it listens on a pointer event that a value assignment never produces. Treat it as "no items
in this bucket" and you write zeros for buckets that were never queried, and nothing downstream
can tell those zeros from real ones.

## Sweep in half-open intervals, and say so out loud

Two adjacent buckets written as 0 to 100 and 100 to 200 double count every item priced at
exactly 100, because a site's range filter is nearly always inclusive at both ends: `value >=
lo AND value <= hi`. The output shows no symptom. Every bucket looks reasonable alone, and the
only trace is that the counts sum to more than the unfiltered total.

Measure the inclusivity rather than assuming it. Ask for a window whose lower and upper bounds
are the same number, using a value you know exists. A non-zero count means both ends are
inclusive.

Then keep half-open intervals in your own bookkeeping, `[lo, hi)`, and shrink the upper bound
by one unit of the field's precision when you build the query. Precision belongs to the data,
not to the widget: a slider stepping in tens over money that resolves in cents needs 0.01
subtracted, not 10.

```python
def snap(value, precision):
    """One rounding rule for every edge, so adjacent buckets share an exact number."""
    return round(round(value / precision) * precision, 10)

def buckets_from_edges(edges, precision):
    """Half-open by construction, with both outer edges left unbounded."""
    last = len(edges) - 2
    for i, (left, right) in enumerate(zip(edges, edges[1:])):
        yield {
            "label": f"[{left}, {right})",
            "lower_closed": True,
            "upper_closed": False,
            # the site's filter includes its upper bound, so step back one unit
            "query_lo": None if i == 0 else left,
            "query_hi": None if i == last else snap(right - precision, precision),
        }
```

Reconcile at the end. Sum the bucket counts and compare them against the unfiltered total.
Above it, something is still double counting. Below it is the part no care repairs: some items
have no value in that field at all.

A listing that says "call for price", a record where the column is null. Neither is reachable
by a range query in either direction, because a null is neither inside nor outside an interval.
The gap between your sum and the base total is the size of that unreachable set.

## Space the edges logarithmically when the values have a tail

Linear buckets assume a flat distribution, and price data rarely is one. Cut 0 to 1,000,000
into twenty buckets of 50,000 and every item under 50,000 falls into the first, while the top
fifteen return nothing. Twenty requests, and the crawl has resolved almost nothing.

Geometric edges put the resolution where the items are. Ten to 100,000 in five steps gives 10,
63, 398, 2512, 15849, 100000: narrow buckets at the low end, wide ones in the tail, which
follows the shape of the data instead of the shape of the axis.

Round every edge exactly once, through one function, and take both sides of a boundary from the
same rounded list. Round an upper edge down to 63 and the next lower edge up to 64 and you open
a gap that swallows everything between them.

Geometry is still a guess. The version that does not guess reads the count and splits: query
the range, and when the total is larger than pagination can serve, split at the geometric
midpoint and recurse into both halves. That adapts to any shape without knowing it first.

```python
from invisible_playwright import InvisiblePlaywright

def log_edges(lo, hi, steps, precision):
    """Geometric spacing. A geometric scale cannot start at zero."""
    lo = max(lo, precision)
    ratio = (hi / lo) ** (1.0 / steps)
    edges = [snap(lo * ratio ** i, precision) for i in range(steps + 1)]
    edges[-1] = hi
    return sorted(set(edges))

def split_until_under_cap(page, lo, hi, cap, precision, out):
    total = apply_range(page, lo, hi, set_value)["total"]
    if total <= cap or hi - lo <= precision:
        out.append({"lo": lo, "hi": hi, "total": total, "capped": total > cap})
        return
    mid = min(max(snap((lo * hi) ** 0.5, precision), lo + precision), hi - precision)
    split_until_under_cap(page, lo, mid, cap, precision, out)
    split_until_under_cap(page, mid, hi, cap, precision, out)

with InvisiblePlaywright(seed=42) as browser:   # one identity for the whole sweep
    page = browser.new_page()
    page.goto(BASE_URL, wait_until="domcontentloaded")
    scale = describe_slider(page, LO_SELECTOR)
    buckets = []
    split_until_under_cap(page, float(scale["valuemin"]), float(scale["valuemax"]),
                          cap=1000, precision=0.01, out=buckets)
```

The recursion stops in two ways and they mean different things. A bucket whose count fits under
the cap is finished. A bucket one unit of precision wide and still over the cap is a dead end:
more items share that single exact value than
[pagination](how-to-scrape-paginated-pages-playwright.md) can reach, and no range filter can
divide them. The `capped` flag carries that distinction out, because the fix is to split that
branch on a different filter entirely.

## Conclusion

A range filter fails quietly, in arithmetic, which is what makes it worth this much care. The
control is usually a div wearing `role="slider"`, so read its ARIA attributes before deciding
which call can move it. Prefer the URL parameter, set the value through the native setter or
the arrow keys when there is none, and do not trust a drag, because the pixel is the real step.
Re-read the bounds after every other filter, since they describe the current result set rather
than the catalogue. Wait for the debounced response and match it to the values you sent, because
silence there is a component that never fired, not an empty bucket. Then sweep in half-open
intervals and reconcile the sum, because the difference between your buckets and the base total
is the set of items no slider can reach.

## Short answers to the questions that lead here

**Why does `fill()` do nothing on the price slider?** Because the element is not an input. Most
sliders are a `div` with `role="slider"` driven by pointer events, and `fill()` raises an error
saying the element is not an input, a textarea or contenteditable. On a genuine
`input[type=range]` it does work, since Playwright sets that input type's value directly and
fires `input` and `change`.

**Should I drag the handle with `mouse.move`?** Only as a last resort. The widget converts a
pixel to a value, so on a 300 pixel track spanning a million units one pixel is worth over three
thousand, and the handle width biases the mapping on top of that. Set the value through the
input or the arrow keys instead.

**Why does the value I set come back as a different number?** Step quantisation. Assigning to a
range input's value runs the HTML sanitization algorithm, which clamps to the bounds and rounds
to the nearest step, and the default step is 1. Store the value you read back, not the one you
requested.

**Why did the maximum on the slider change when I ticked a brand?** Because the bounds describe
the current result set, not the catalogue. They are recomputed on every filter change, so
buckets cut from the unfiltered scale are wrong under any other state. Re-read the bounds after
each change.

**How long should I wait after moving a slider?** Do not wait a fixed time. The request is
debounced a few hundred milliseconds after the last change, so wait on the response and match it
against the bounds you asked for. A timeout usually means the component never saw your event,
not that the range is empty.

**My bucket counts add up to more than the total. Why?** The site's filter includes both ends,
so items sitting exactly on a boundary are counted in the bucket below and again in the bucket
above. Use half-open intervals and shrink each upper bound by one unit of the field's precision.

## Sources

- Playwright's [`Locator.fill`](https://playwright.dev/python/docs/api/class-locator#locator-fill),
  [`Locator.evaluate`](https://playwright.dev/python/docs/api/class-locator#locator-evaluate),
  [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response)
  and [`Keyboard.press`](https://playwright.dev/python/docs/api/class-keyboard#keyboard-press),
  used exactly as documented upstream, retrieved 2026-08-28. The browser returned here is a real
  Playwright `Browser`.
- Playwright's [input and manual dragging notes](https://playwright.dev/python/docs/input),
  retrieved 2026-08-28, for the pointer sequence a component using pointer capture requires.
- The HTML specification's
  [range state](https://html.spec.whatwg.org/multipage/input.html#range-state-(type=range)),
  retrieved 2026-08-28, for the value sanitization algorithm that clamps to the bounds and rounds
  to the step, and for the default step of 1.
- The WAI-ARIA Authoring Practices [slider pattern](https://www.w3.org/WAI/ARIA/apg/patterns/slider/),
  retrieved 2026-08-28, for the `aria-valuenow` and `aria-valuetext` contract and the keyboard
  behaviour the arrow-key path depends on.

**See also:** [scraping multi-select facet filters](how-to-scrape-multi-select-facets-playwright.md)
for the discrete sibling of this problem, where the values are a vocabulary rather than a scale,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for reading the
debounced payload instead of the repainted DOM,
[dragging manually](drag-and-drop-playwright-firefox-drag-to.md) for the components that really do
need the pointer sequence, and
[scraping paginated pages](how-to-scrape-paginated-pages-playwright.md) for the per-bucket limit
that decides how far a range has to be split.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Sweeping a range in fixed steps with
both ends inclusive is the mistake this page corrects: the bucket counts summed to more than the
unfiltered total, and the surplus was exactly the items sitting on a boundary, counted once in the
bucket below and once in the bucket above.*
