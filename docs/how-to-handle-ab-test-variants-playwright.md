---
title: "How to handle A/B test variants when scraping with Playwright"
description: "Same URL, same 200, different DOM: read the assignment marker from a cookie, data attribute or global object, pin it with add_cookies or storage_state, and record variant as a column when you cannot pin."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 121
---


# How to handle A/B test variants when scraping with Playwright

**To handle A/B test variants when scraping with Playwright, stop treating the
difference as flakiness and pin the assignment: find the marker the site already
writes (a cookie, a `data-` attribute on `html` or `body`, or a global JavaScript
object), capture it once from a session that rendered the arm you want, replay it into
every later context with `add_cookies` or `storage_state`, and when it cannot be
pinned, record the variant as a column on every row instead of pretending it is not
there.**

A large share of "my scraper is flaky" reports are this and nothing else. The selector
works on Monday, throws on Tuesday, works again that afternoon. Nothing changed in your
code and nothing changed in the proxy. What changed is which arm of a live experiment
the session landed in, and that gets decided again every time a clean context asks for
the page.

Plenty of sites run several experiments at once, so what comes back is one point in a
grid of arms rather than one of two layouts. The data is almost always the same in all
of them. The markup is not. This page covers how to tell that apart from blocking, how
to find the marker, and what to do when it cannot be held steady.

## Assignment looks like blocking and is not

The signature is narrow enough to be diagnostic: same URL, same 200 status, roughly the
same body size, and a DOM whose structure does not match what your selector expects.

A block does not look like that. A block arrives as a 403, a 429, a challenge page, or
a body a fraction of the normal size with none of the real content in it. An experiment
arm returns the whole page, every field present, wearing different class names.

So record the status code and the response length next to every parse failure. If both
are normal and the parse still failed, the problem is not in [the detection
stack](playwright-detected-as-bot.md) and no amount of stealth will move it. People
lose days here, because a broken selector and a soft block feel identical from inside
the traceback.

## A fresh context re-rolls the dice

Assignment is sticky per visitor, on purpose. The framework decides once, writes the
decision into a cookie or a local storage key, and reads it back on every later
request. An experiment whose visitors flip between arms measures nothing, so stickiness
is the whole design.

A scraper that opens a clean context each run is a brand new visitor each run. The jar
is empty, no key sits in local storage, and the server rolls again. Ten runs become ten
independent draws, which is why the failures look statistical rather than causal.

Keep the isolation anyway: [one context per
identity](isolate-identities-browser-context-per-session.md) stops one run's state from
contaminating the next. It just carries a side effect nobody mentions. Throw away all
state and you throw away the assignment with it.

## Find the marker before you touch a selector

The assignment id is almost always visible from the page, in one of three places. A
cookie whose name contains something like `exp`, `ab`, `variant`, `bucket` or `split`.
A `data-` attribute on `html` or `body`, which is how a framework tells its own CSS
which arm to paint. A global object the page reads at boot, with the arm inside it.
Read all three in one pass instead of guessing.

```python
import re
from invisible_playwright import InvisiblePlaywright

MARKER_NAME = re.compile(r"(exp|ab|variant|bucket|split|test)", re.I)

def read_variant(page):
    """Collect every assignment marker the page exposes, in one pass."""
    marks = {}

    for cookie in page.context.cookies(page.url):
        if MARKER_NAME.search(cookie["name"]):
            marks["cookie:" + cookie["name"]] = cookie["value"]

    marks.update(page.evaluate("""() => {
        const out = {};
        for (const el of [document.documentElement, document.body]) {
            if (!el) continue;
            for (const attr of el.attributes) {
                if (attr.name.startsWith('data-')) {
                    out['attr:' + attr.name] = attr.value;
                }
            }
        }
        for (const key of ['__EXPERIMENTS__', 'dataLayer', '__NEXT_DATA__']) {
            if (window[key] !== undefined) {
                out['window:' + key] = JSON.stringify(window[key]).slice(0, 400);
            }
        }
        return out;
    }"""))
    return marks
```

Run it against two sessions you know rendered differently and diff the dicts. The key
whose value changes while the rest hold still is your assignment. Session ids, CSRF
tokens and timestamps change too, for reasons unrelated to layout, so the diff only
means something when both sessions really did disagree on the DOM.

If you do not have that pair yet, make one: open the same URL from several clean
contexts under a fixed seed and count what comes back.

```python
from collections import Counter

def sample_assignments(url, runs=8):
    seen = Counter()
    with InvisiblePlaywright(seed=42) as browser:
        for _ in range(runs):
            context = browser.new_context()   # empty jar: a brand new visitor
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            seen[tuple(sorted(read_variant(page).items()))] += 1
            context.close()
    return seen
```

One entry in the counter means the page is not splitting you and the failures come from
somewhere else. Two or more, from a fixed seed and one exit, and you have found the
flakiness. The fixed seed is what makes that readable: the identity is identical across
all eight runs, so the empty jar is the only thing varying.

## Pin the assignment instead of fighting it

The fix is one call, made before the first request goes out. Write the marker from a
known-good session into every later context, and the server reads its own earlier
decision back instead of making a new one.

```python
PINNED = [{
    "name": "exp_bucket",       # the key the diff identified
    "value": "b7f21c",          # the value from the known-good session
    "domain": ".example.com",
    "path": "/",
}]

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context()
    context.add_cookies(PINNED)     # in the jar before the first request
    page = context.new_page()
    page.goto("https://example.com/product/123")

    got = read_variant(page).get("cookie:exp_bucket")
    if got != PINNED[0]["value"]:
        raise RuntimeError(f"pin did not hold: wanted {PINNED[0]['value']}, got {got}")
```

The re-read after the load is the part everyone skips and the part that matters. A
cookie can be rejected over a domain mismatch, overwritten by the server's own
`Set-Cookie` on the first response, or ignored because this framework keeps its decision
in local storage. All three fail without raising anything, so your code believes it
pinned an arm while the server keeps rolling. [Reading and setting the
jar](read-set-cookies-playwright-context.md) has the full API.

When the assignment lives in local storage, `add_cookies` cannot reach it and the tool
is `storage_state`: dump cookies and local storage from a good session to a file, then
load it into every later context. [Saving and reusing a
session](save-reuse-login-storage-state-playwright.md) is the same mechanism applied to
logins, and it carries the caveat that matters here too, which is to keep the seed
pinned alongside the state.

## When you cannot pin, make the variant a column

Some assignments will not hold. The decision is taken at the edge before any JavaScript
runs, the cookie is signed against the session it was issued to, or the key rotates
faster than your run. The honest answer is not to hide it. Read the marker on every
load and treat variant as a field like price or title.

```python
from datetime import datetime, timezone

def scrape_row(page, url, expected=None):
    page.goto(url, wait_until="domcontentloaded")
    marks = read_variant(page)
    variant = (marks.get("cookie:exp_bucket")
               or marks.get("attr:data-variant")
               or "unknown")

    row = {
        "url": url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "variant": variant,
        "pin_held": None if expected is None else variant == expected,
    }
    row.update(extract_fields(page))   # the arm-tolerant reader, below
    return row
```

Two fields, not one. `variant` says which arm produced the row. `pin_held` says whether
you got the arm you asked for, which turns a silent pin failure into something you can
group by three months later. Rows where it is false are still good data, just a
different series.

The `"unknown"` string is deliberate, and a null would be worse. A null reads as "this
page runs no experiment", a different claim from "nothing here was recognised as a
marker". Store the whole marker dict beside the row too; it costs a text column and
answers the question when the site adds a second experiment on top of the first.

## An experiment and a content change look identical without that column

Here is the reason the column earns its place, and it has nothing to do with parsing. A
series with no variant column cannot tell you why a number moved. The site changed the
price, or the site put you in an arm that displays a different price, and both arrive as
a new value on the same URL at the same hour. The row does not say which.

Record the variant and the same data answers the question by grouping. Filter to one arm
and the series is comparable over time. Compare the arms and the step you saw reads as a
split rather than a move. Leave the column out and every experiment the site runs is
folded silently into your history as a real change.

Long collections are where this bites hardest, which is why [tracking prices over
time](how-to-track-product-prices-playwright.md) is worth pinning for. The same holds
for text: an experiment on the description, or on which reviews appear first, looks
exactly like the site editing its own page.

## Selectors that survive both arms

Arms are usually a redesign of the same fields. The price is still there, the title is
still there, and the class names around them are new, because the class names are what
is being tested. A selector like `.pdp-price__value--v2` names the layout, so it aims
straight at the moving part. Key on semantics instead, ordered from most stable to
least.

```python
def first_text(page, *locators):
    """Return the first locator that resolves, so one arm's markup can miss."""
    for locator in locators:
        if locator.count():
            return locator.first.inner_text().strip()
    return None

def extract_fields(page):
    return {
        "title": first_text(
            page,
            page.locator("[data-testid='product-title']"),
            page.get_by_role("heading", level=1),
            page.locator("h1"),
        ),
        "price": first_text(
            page,
            page.locator("[itemprop='price']"),
            page.locator("[data-testid*='price']"),
            page.locator("[class*='price']"),   # last resort, and first to break
        ),
    }
```

The ordering is the whole idea. Test ids and microdata are written by the same team
running the experiment, and they survive a redesign because that team's own end-to-end
tests depend on them. Roles and headings come from the document outline, which rarely
gets rewritten to test a button colour. The class fallback exists so a run degrades
instead of throwing, and any row it produced deserves a second look.

The strongest version skips the rendered markup entirely. Where the page ships [a
JSON-LD block](how-to-extract-json-ld-structured-data-playwright.md), it is generated
from one record for every arm, so it is usually the one part an experiment leaves alone.

## Where this stops

A server-side assignment can expose no marker at all. The edge picks an arm, renders it,
and sends HTML with no cookie, no data attribute and no differing global object. You
will see two shapes and have nothing to key on.

The fallback there is to fingerprint the shape yourself: hash the set of selectors that
resolved and store that hash as the variant. It is a weak marker, since an ordinary
redesign changes it too, and it still beats a column of "unknown".

An arm that changes what data exists cannot be reconciled by any selector strategy. If
one arm lists three reviews and the other lists ten, or one hides the shipping cost
until checkout, the two are not two renderings of one record. They are two records.
Merging them gives you the average of two different things, and no locator ordering
rescues that. Pin, or keep the arms as separate series.

The last limit is a trade rather than a failure. A pinned scraper measures one arm, not
the site. If the question is what a typical visitor sees, pinning answers it confidently
and wrongly. Sample across the arms, record each, and aggregate afterwards.

## Conclusion

Variant assignment explains a whole class of scraper reports filed as flakiness or
blocking. The status code stays 200, the data stays present, and only the markup moves.
Once you know that, the work is small: find the marker, pin it with `add_cookies` or
`storage_state`, and re-read it after the load so a failed pin raises instead of
drifting. Where pinning is impossible, record the variant on every row. That column is
the difference between a series you can compare over time and one where the site's
experiments and its real changes are mixed together for good.

## Short answers to the questions that lead here

**My selector works some runs and fails others. Is the site blocking me?** Check the
status code and the response length on the failing run. If both are normal and the page
is full of data your parser could not find, that is a different arm of an experiment,
not a block.

**Why does the layout change when nothing in my code changed?** A clean browser context
is a brand new visitor with an empty jar, so the site assigns an arm again on every run.
Assignment is sticky per visitor, and you throw the visitor away each time.

**Where is the variant id kept?** Usually a cookie whose name contains `exp`, `ab`,
`variant`, `bucket` or `split`; otherwise a `data-` attribute on `html` or `body`, or a
global object the page reads at boot. Diff two sessions that rendered differently to
find it.

**How do I make every run see the same version?** Inject the marker with
`context.add_cookies()` before the first request, or restore a saved `storage_state`
when the value lives in local storage. Then re-read it after the load and fail loudly
when it did not hold.

**What if the assignment cannot be pinned?** Record it. Put a `variant` column on every
row plus a flag saying whether the pin held, then group by it during analysis instead of
mixing the arms into one series.

**Should selectors just cover both layouts?** Cover them in order of stability: test ids
and microdata first, then roles and headings, then class names as a last resort. The
class names are the part being tested.

## Sources

- Playwright's [`add_cookies`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies)
  and [`cookies`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies)
  on `BrowserContext`, which read and write the jar a sticky assignment lives in.
- Playwright's [`storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state)
  and [`new_context`](https://playwright.dev/python/docs/api/class-browser#browser-new-context),
  the pair that saves and restores cookies plus local storage.
- Playwright's [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate)
  and [`get_by_role`](https://playwright.dev/python/docs/api/class-page#page-get-by-role),
  used as documented upstream, because the browser this library returns is a real
  Playwright `Browser`. All pages above retrieved 2026-08-28.
- This project's own behaviour: a fixed seed produces the same identity on every run,
  which is what lets the sampling probe blame the empty jar rather than a moving
  fingerprint.

**See also:** [reading and setting cookies](read-set-cookies-playwright-context.md) for
the jar API behind the pin, [saving and reusing a session](save-reuse-login-storage-state-playwright.md)
for the local storage case, [scraping into a database](how-to-scrape-into-a-database-playwright.md)
for where the variant column belongs, and [tracking product prices](how-to-track-product-prices-playwright.md)
for the series that needs it.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. A selector that broke
every third run got blamed on the proxy for a day before the cookie turned out to be
handing out a different arm on every clean context.*
