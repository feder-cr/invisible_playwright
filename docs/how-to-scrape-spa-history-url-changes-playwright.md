---
title: "Scrape an SPA that changes URL via history API"
description: "Scrape a single-page app that changes URL via history.pushState with no page load: wait on route and DOM markers, capture the view XHR, re-query each route."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 79
---


# Scrape an SPA that changes URL via history API

A single-page app does not load a new document when you move between views. It
rewrites the address bar with `history.pushState`, swaps some DOM, and fetches the
new view's data over XHR. The URL changes, but nothing that Playwright calls a
navigation ever happens. If your scraper waits for a load or a navigation event, it
waits forever.

This page is the pattern that works: wait on route and DOM markers instead of load
events, capture the XHR that actually carries the view's data, and re-query the page
after every route change because the old handles are now pointing at a view that no
longer exists.

## Why a load event never fires in a single-page app

`history.pushState` fires none of the events Playwright waits on for a navigation:
no `load`, no `domcontentloaded`, no `framenavigated`, and no `networkidle`
transition. [`pushState`](https://developer.mozilla.org/en-US/docs/Web/API/History/pushState)
is a JavaScript call that edits the session history and the address bar in place -
the document object is the same one it was a moment ago, and no request is tied to
the URL change, so there is nothing for `networkidle` to settle on either.

Compare that with a classic multi-page site: click a link, and the browser tears down
the document, makes a request, and builds a new one. Playwright has an event for every
step of that: `load`, `domcontentloaded`, a `framenavigated`, and the `networkidle`
state settles once the requests stop. Concretely, all four of these hang until they
time out on a pushState route:

```python
# every one of these waits for an event a pushState app never emits
page.wait_for_load_state("load")
page.wait_for_load_state("networkidle")
with page.expect_navigation():
    page.click("a[href='/app/items']")
page.on("framenavigated", handler)   # never called for a pushState route
```

Here is what actually happens on each signal, classic navigation versus a
`pushState` route:

| Signal | Classic multi-page navigation | `history.pushState` route |
|---|---|---|
| `load` | Fires | Never fires |
| `domcontentloaded` | Fires | Never fires |
| `framenavigated` | Fires | Never fires |
| `networkidle` | Settles once requests stop | No transition (the URL change makes no request) |
| `page.url` | Updates | Updates immediately |
| The DOM | A brand-new document | Same document, one subtree swapped |

The URL did change. `page.url` will read the new path immediately after the click.
What did not change is anything Playwright treats as a navigation, which is exactly
the thing your wait was hanging on. [The general question of what to wait for when the
usual load states lie](how-to-wait-for-page-load-playwright.md) has its own page; an
SPA route is the sharpest case of it.

## Wait on route and DOM markers, not navigation

The two things that genuinely change on a pushState route are the URL and the DOM.
Wait on those directly.

Playwright's `wait_for_url` accepts a glob or regex and resolves when `page.url`
matches, whether the change came from a real navigation or from `pushState`. Pair it
with a `wait_for_selector` for a marker the new view renders, so you are certain the
view is present and not merely that the address bar moved ahead of the render:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/app")

    # the initial document load is a real navigation, so goto is enough here.
    # confirm the app has mounted before touching it.
    page.wait_for_selector("[data-view='home']")

    # clicking a nav link swaps the view via pushState. wait for the URL to
    # move AND for the destination view's marker to render.
    page.click("a[href='/app/items']")
    page.wait_for_url("**/app/items")
    page.wait_for_selector("[data-view='items'] .item")
```

The `browser` object here is a real Playwright `Browser`, so `wait_for_url`,
`wait_for_selector`, `click` and everything else behave exactly as documented
upstream. The only difference from plain Playwright is the two-line launch and a
fingerprint underneath.

If the app renders its route marker before the data arrives (a skeleton or a spinner),
wait for a marker that only the loaded state shows, or use `wait_for_function` to poll
for a condition the framework sets once the view is ready:

```python
# wait for the item list to actually contain rows, not just exist empty
page.wait_for_function("() => document.querySelectorAll('[data-view=\"items\"] .item').length > 0")
```

## Capture the XHR that carries the view's data

The DOM is the rendered result. The clean data is in the XHR the view made to fetch
itself, and reading that response is far more reliable than scraping rendered HTML
that the framework may re-render underneath you.

`page.expect_response` opens a wait for a matching response and lets you trigger it in
the same block. Match on the API path and, if you want to be strict, on the request's
resource type so a same-URL document does not satisfy it:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/app")
    page.wait_for_selector("[data-view='home']")

    # arm the response wait BEFORE the click that causes the fetch,
    # or the response can arrive before you started listening
    with page.expect_response(
        lambda r: "/api/items" in r.url and r.request.resource_type == "xhr"
    ) as resp_info:
        page.click("a[href='/app/items']")

    page.wait_for_url("**/app/items")
    response = resp_info.value
    data = response.json()          # the view's data, already structured
    print(len(data["items"]), "items on this route")
```

Arm the listener before the action that triggers the request. If you click first and
call `expect_response` after, the response can land in the gap and you wait for one
that already happened. [Capturing XHR and JSON API responses](how-to-capture-xhr-api-responses-playwright.md)
covers matching, buffering several responses per route, and reading bodies that are
not JSON.

## Re-query after every pushState

This is the mistake that produces the strangest bugs. A pushState route replaces the
view's DOM but keeps the same page and the same execution context, so any element
handle, `ElementHandle`, or evaluated reference you captured on the old view is now
attached to nodes that have been removed. It does not error loudly. It returns stale
or empty results, which reads like a scraping bug rather than a lifecycle one.

The discipline: query fresh after each route change. Wrap a route transition in one
helper so re-querying is not something you can forget:

```python
def goto_route(page, link_selector, url_glob, view_marker, api_fragment):
    """Follow an in-app pushState link and return the view's XHR payload."""
    with page.expect_response(
        lambda r: api_fragment in r.url and r.request.resource_type == "xhr"
    ) as resp_info:
        page.click(link_selector)
    page.wait_for_url(url_glob)
    page.wait_for_selector(view_marker)      # fresh query, current view
    return resp_info.value.json()


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/app")
    page.wait_for_selector("[data-view='home']")

    payloads = []
    for path in ["items", "orders", "settings"]:
        payload = goto_route(
            page,
            link_selector=f"a[href='/app/{path}']",
            url_glob=f"**/app/{path}",
            view_marker=f"[data-view='{path}']",
            api_fragment=f"/api/{path}",
        )
        payloads.append(payload)
```

Prefer `page.locator(...)` over a stored `ElementHandle` across route changes: a
locator re-resolves its selector every time you use it, so it naturally points at the
current view, while a handle is frozen to the node it was created from.

## One long-lived context, and the caveat that comes with it

Here is the stealth upside, and it is genuine. Because a pushState route is not a full
navigation, one browser context holds the entire multi-view session. There is no
document teardown, no new page, no fresh identity draw between views. The fingerprint
that was generated when the context launched persists across every route for free.

With `seed=42` you can watch this hold. Read a stable surface such as the canvas hash
or a FingerprintJS visitor ID on the first view, walk twenty routes, and read it
again: it is identical on every route, because it is the same context throughout. A
design that spun up a fresh context per view would instead present twenty independently
generated machines to any scoring endpoint, and a session that changes machine between
clicks is a louder signal than any single fingerprint.

The caveat is the same coin's other face. That same longevity means execution-context
state and page state accumulate across a long session: listeners, timers, retained
references, and memory the app never releases between views. Two habits keep it honest.
Re-query after each route change, as above, so you are never reading a dead view. And
be ready for the app itself to blow away the execution context mid-session, which
surfaces as an `"Execution context was destroyed"` error on your very next call:

```python
from playwright.sync_api import Error as PlaywrightError

def read_current_view(page, view_marker):
    try:
        page.wait_for_selector(view_marker)
        return page.locator(view_marker).inner_text()
    except PlaywrightError as exc:
        if "Execution context was destroyed" in str(exc):
            # the app re-created the context (a hard in-app redirect or a
            # framework remount). settle on the new one and query again.
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_selector(view_marker)
            return page.locator(view_marker).inner_text()
        raise
```

That error is not always a bug in your code. In an SPA it is frequently the
application doing a hard remount or an in-app redirect underneath you.
[Why "Execution context was destroyed" happens and how to tell the two causes apart](execution-context-destroyed.md)
separates the ordinary race from the app-triggered one, because the fix differs.

## Conclusion

An SPA that routes with `history.pushState` breaks every wait that assumes a
navigation, because there is no navigation: no document load, no navigation event, no
`networkidle`. Wait on the URL and a DOM marker instead, capture the XHR that carries
the view's data rather than scraping re-rendered HTML, and re-query after each route so
you are never holding a handle to a view that no longer exists. The long-lived context
that makes all of this necessary is also what keeps one consistent identity across the
whole session for free, as long as you re-query and stay ready for the app to reset the
context on you.

## Short answers to the questions that lead here

**Why does wait_for_load_state hang on a single-page app?** Because `pushState`
changes the URL without loading a document, so `load`, `domcontentloaded` and
`networkidle` never fire for the route change. Wait for the URL and a DOM marker
instead.

**How do I wait for a pushState URL change in Playwright?** `page.wait_for_url("**/app/items")`
resolves on a `pushState` change as well as on a real navigation. Pair it with a
`wait_for_selector` for a marker the new view renders.

**Should I scrape the rendered DOM or the XHR?** The XHR, when you can. Arm
`page.expect_response` before the click that triggers it, match on the API path, and
read `response.json()`. It is cleaner and more stable than HTML the framework may
re-render.

**Why are my element handles suddenly stale after clicking a link?** A route change
replaces the view's DOM while keeping the same page, so handles created on the old view
point at removed nodes. Re-query after each route, and prefer `page.locator(...)`,
which re-resolves every use.

**What causes "Execution context was destroyed" in an SPA?** Often the app itself,
doing a hard remount or in-app redirect, not the classic navigation race. Catch it,
settle the page, and re-query the current view.

**Does one context for the whole app help or hurt stealth?** It helps: one identity
persists across every route with no per-view redraw. The cost is accumulated page and
context state, which is why re-querying and watching for a destroyed context are part
of the pattern.

## Sources

- Playwright's `wait_for_url`, `expect_response`, `locator` and `wait_for_function`
  APIs, read from their documented behaviour rather than from a verdict:
  [Playwright Python `Page` API reference](https://playwright.dev/python/docs/api/class-page).
- The `history.pushState` behaviour itself - it edits the session history and the
  address bar without asking the browser to load a document:
  [MDN, `History.pushState()`](https://developer.mozilla.org/en-US/docs/Web/API/History/pushState).
- This project's own measurements of a single context across a multi-route session:
  with a fixed seed, the canvas hash and visitor ID stay identical on every route.

**See also:** [what to wait for when the usual load states lie](how-to-wait-for-page-load-playwright.md),
[capturing the XHR and JSON responses a view fetches](how-to-capture-xhr-api-responses-playwright.md),
and [the two causes of a destroyed execution context](execution-context-destroyed.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The stale-handle bug in
section four cost me an afternoon before I understood it was a lifecycle problem, not a
selector one.*
