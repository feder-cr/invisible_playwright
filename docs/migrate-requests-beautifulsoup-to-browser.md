---
title: "Migrating from requests + BeautifulSoup to a browser"
description: "How to migrate a requests + BeautifulSoup scraper to a real browser when the data is rendered by JavaScript, while keeping BeautifulSoup as the parser."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 42
---


# Migrating from requests + BeautifulSoup to a browser

`requests` fetches bytes and `BeautifulSoup` parses them. Between those two steps
there is a thing neither of them does: run JavaScript. That gap is why a scraper that
worked for months suddenly returns a page with none of the data on it, or hangs on a
blank body that never fills in.

This page is about crossing that gap without throwing away the half of your code that
still works. The browser replaces `requests`. `BeautifulSoup` stays exactly where it
is. And before you migrate anything, there is a one-command test for whether you even
need to.

## Why the raw HTML sometimes has nothing in it

`requests` returns the exact bytes the server sent to that one HTTP call. If the
server renders the page on its side and ships finished HTML, those bytes contain your
data and `BeautifulSoup` finds it. Many sites still work this way, and for them a
browser is pure overhead.

The trouble is the other pattern. The server ships a nearly empty shell plus a
JavaScript bundle, and the bundle fetches the data and builds the DOM in the browser
after load. `requests` never runs that bundle, so it hands `BeautifulSoup` a shell
with empty containers where the content should be. Your selector matches nothing, and
nothing in the traceback says why, because there is no error - the element genuinely
is not there.

A second, sharper version of the same gap: some pages ship a script that expects a
real JavaScript runtime and will not proceed to the real content until it has run.
Feed that to `requests` and you get the challenge shell forever, because the thing it
is waiting for never happens. [How websites decide a client is automated](how-do-websites-detect-bots.md)
covers the range of these; for this page the point is narrow. No JavaScript engine,
no final DOM.

## The test that tells you whether to migrate at all

Do not migrate on a hunch. Ask the raw response directly whether it already contains
what you want. If the string is in there, `requests` is fine and a browser would make
your scraper slower for nothing.

```bash
# Does the raw HTML already contain the value you are scraping?
curl -s "https://example.com/listing" | grep -c "Total price"
```

A non-zero count means the data is in the bytes the server sends, and your current
stack can reach it - the bug is a selector, an encoding, or a header, not a missing
JavaScript engine. A count of zero, on a page where a human clearly sees "Total
price", is the signal that the value is rendered client-side and only a browser will
expose it.

You can run the same test from Python without leaving your existing code:

```python
import requests

raw = requests.get("https://example.com/listing", timeout=30).text
print("in raw HTML:", "Total price" in raw)   # False -> you need a browser
```

Run this first. It is the difference between a justified migration and a heavier
scraper that solved a problem you did not have.

## The migration that keeps BeautifulSoup

Here is the part people expect to be a rewrite and is not. `BeautifulSoup` does not
care where its HTML came from. It parses a string. `requests` was one source of that
string; a browser is another, and a better one when JavaScript is involved, because
the browser hands you the DOM *after* the scripts have run.

Launching the browser is a two-line change and the rest of your extraction is
untouched:

```python
from bs4 import BeautifulSoup
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")

    # page.content() is the FINAL, rendered HTML - after the JS built the DOM.
    soup = BeautifulSoup(page.content(), "html.parser")

# every selector you already wrote keeps working, unchanged
price = soup.select_one(".total-price").get_text(strip=True)
print(price)
```

The single load-bearing call is `page.content()`. It returns the serialized DOM as it
stands at that moment, JavaScript included, and it is an ordinary string you pass
straight to `BeautifulSoup`. The `browser` object is a real Playwright `Browser`, so
`page`, `goto` and `content` are the standard documented methods - nothing here is a
wrapper you have to relearn. `seed=42` fixes the identity so a failing run is
reproducible instead of a fresh random machine every time.

One timing caveat that is the whole ballgame with rendered pages: `page.content()`
captures the DOM *now*, and "now" can be before the client-side fetch has finished. If
your selector intermittently finds nothing, wait for the actual element before reading
the HTML rather than sleeping a fixed number of seconds:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")
    page.wait_for_selector(".total-price")     # block until the JS has rendered it
    soup = BeautifulSoup(page.content(), "html.parser")
```

[Waiting for the page to actually be ready](how-to-wait-for-page-load-playwright.md)
goes into the several notions of "loaded" and which one you want; the rule of thumb is
to wait for the specific thing you are about to read. If you would rather keep every
selector as a `BeautifulSoup` call and only borrow the browser for rendering,
[using BeautifulSoup with invisible_playwright](use-beautifulsoup-with-invisible-playwright.md)
is the same pattern written up on its own.

## What the browser fixes, and what it does not

Be precise about what this migration buys, because the honest boundary is where people
get burned.

A real browser executes the JavaScript, so it fixes the actual problem you migrated
for: the data that was never in the raw HTML is now in `page.content()`, and the
challenge script that stalled `requests` runs to completion. And because
invisible_playwright is a Firefox patched at the C++ level and driven by stock
Playwright, it is built to read as a genuine Firefox run by a person - the fingerprint,
the TLS handshake and the driver layer look like a real browser, which is why it clears
most of the fingerprint and driver checks that stop a plain automation stack. You can
confirm that for yourself against the public suites; [when Playwright gets detected on
one specific site](playwright-detected-as-bot.md) is the checklist for the cases that
remain.

What the browser does not fix, on its own, is everything that is not the browser:

- **IP reputation.** A convincing browser coming from a datacenter range is still
  coming from a datacenter range. You supply a clean exit; the browser cannot.
- **Rate limits and per-account quotas.** A real browser making a hundred requests a
  minute is a real browser making a hundred requests a minute. The pacing is yours.
- **Behaviour and timing.** Loading twenty pages in twenty seconds with no pause is a
  pattern no person produces, whatever the fingerprint says.

The engine handles what the engine can see. IP, pacing and account limits are inputs
you bring - a clean proxy and human timing. Anyone who tells you a browser alone makes
a scraper undetectable is selling the half of the problem they do not have to deliver.
[Configuration](configuration.md) covers wiring in a proxy and letting the browser
timezone follow the exit.

## A note on cost: migrate deliberately

A browser starts a process, renders a page, and runs a JavaScript engine for every
single fetch. Against a bare `requests.get`, that is dramatically slower and heavier -
more memory, more CPU, more latency per page. For a job pulling thousands of pages the
difference compounds into real time and real machines.

Which is the whole reason for the grep test at the top. Migrate the requests that
genuinely need a runtime, and leave the ones whose data is already in the raw HTML on
`requests`, where they belong. A mixed scraper - `requests` for server-rendered pages,
a browser only for the JavaScript-rendered ones - is usually the right shape, not an
all-or-nothing switch.

## Conclusion

The migration is smaller than it looks because only one piece changes. `requests`
could not run JavaScript, so it missed client-rendered data and stalled on scripts
that demanded a runtime; a real browser runs that JavaScript and hands you the final
DOM through `page.content()`, and `BeautifulSoup` parses that string exactly as it
parsed the old one. Test first with a grep so you only pay for a browser where you
need one, pair it with a clean exit and human pacing for the parts the browser cannot
see, and keep the parser you already trust.

## Short answers to the questions that lead here

**Do I have to rewrite my BeautifulSoup code?** No. `BeautifulSoup` parses a string,
and `page.content()` gives it the same kind of string `requests.text` did - only
rendered. Your selectors are untouched.

**How do I know if I even need a browser?** Grep the raw response for the value you
want: `curl -s URL | grep "the text"`. If it is there, stay on `requests`. If a human
sees it but the grep finds nothing, it is rendered by JavaScript and you need a
browser.

**Why does requests return a page with no data?** Because the server sent a shell plus
a JavaScript bundle that builds the content in the browser after load. `requests` never
runs the bundle, so the containers come back empty with no error.

**Will a browser make my scraper undetectable?** No, and treat anyone who says so with
suspicion. It makes the browser itself look real, which clears most fingerprint and
driver checks. It does nothing about your IP reputation, your rate, or your timing -
those are yours to supply.

**Why is my selector still empty after switching?** You probably read `page.content()`
before the client-side fetch finished. Call `page.wait_for_selector(...)` on the
element first, then read the HTML.

**Should I move every request to a browser?** Rarely. A browser is far slower and
heavier per page. Move only the pages whose data is not in the raw HTML, and keep the
rest on `requests`.

## Sources

- The invisible_playwright [quickstart](quickstart.md) and [configuration](configuration.md)
  pages for the launch API and proxy wiring shown above.
- Playwright's documented [`Page.content()`](https://playwright.dev/python/docs/api/class-page#page-content)
  and `Page.wait_for_selector()`, which behave identically here because the returned object
  is a real Playwright `Browser`.
- This project's own measurements of what the engine covers (fingerprint, TLS, driver
  layer) versus what it does not (IP, pacing, quotas), reflected in the boundary drawn
  above.

**See also:** [using BeautifulSoup with invisible_playwright](use-beautifulsoup-with-invisible-playwright.md),
[when a browser beats curl-based tools](vs-curl-cffi.md), and
[waiting for a page to finish rendering](how-to-wait-for-page-load-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The grep test at the top
has saved more migrations than it has caused - most "I need a browser" problems are a
selector on a page that already had the data.*
