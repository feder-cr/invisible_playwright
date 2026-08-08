---
title: "How to scrape into a pandas DataFrame with Playwright"
description: "Scrape a JavaScript-rendered, logged-in table into a pandas DataFrame with Playwright: feed read_html page.content(), then fix the dtypes it guesses wrong."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 64
---


# How to scrape into a pandas DataFrame with Playwright

**To scrape into a pandas DataFrame with Playwright, hand `read_html` the browser's
rendered `page.content()` string instead of a URL, then set the column dtypes yourself
instead of trusting the ones it guesses.** Those two moves fix the two ways `read_html`
quietly goes wrong; the rest of this page is why each half matters.

`pandas.read_html` is the fastest way to turn an HTML table into a DataFrame, and it
is also the fastest way to end up with a DataFrame that is quietly wrong. It has two
blind spots that this page is about: it cannot see a table that JavaScript rendered or
that a login gates, and once it does see a table it guesses the column types, often
badly, and never tells you.

The fix for both is the same shape: stop handing `read_html` a URL and start handing it
the HTML your browser already rendered, then set the dtypes yourself instead of trusting
the guess. The extraction mechanics live in
[the HTML tables how-to](how-to-scrape-html-tables-playwright.md); this page is the typed
output step that comes after.

## Why read_html needs a rendered page, not a URL

When you call `pandas.read_html("https://example.com/report")`, pandas makes its own
plain HTTP request. No JavaScript runs, no cookies are sent, no session exists. What
comes back is the raw HTML the server sent to an anonymous client, which for most modern
pages is a shell with the table not in it yet, or a login wall where the table should be.

So `read_html` on a URL fails in one of two ways, and both are silent:

- **The table is rendered client-side.** pandas parses the pre-render HTML, finds no
  `<table>`, and raises `ValueError: No tables found`. That one at least tells you.
- **The table is behind a login.** pandas parses the logged-out page and cheerfully
  returns a table, just the wrong one: the marketing table, the sample rows, the "sign
  in to see more" placeholder. That one does not tell you. You get a DataFrame and it is
  garbage.

The browser is what turns a URL into a rendered, authenticated page. Let it do that
first, then give pandas the result.

## Feed read_html the page after it has rendered and logged in

`read_html` accepts an HTML string, not only a URL. `page.content()` returns the current
serialized DOM, which is the fully rendered, post-login HTML with every client-side table
already in it. Wrap that string in `io.StringIO` (pandas asks for a file-like object for
literal HTML) and pass it in.

```bash
pip install invisible-playwright pandas lxml
```

```python
import io
import pandas as pd
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/login")

    # log in with real form interaction, then reach the gated table
    page.fill("#username", "me@example.com")
    page.fill("#password", "hunter2")
    page.click("#submit")
    page.goto("https://example.com/report")

    # wait for the table to actually exist before you serialize the DOM
    page.wait_for_selector("table#results tbody tr")

    html = page.content()

# read_html sees exactly what the browser saw: rendered and authenticated
tables = pd.read_html(io.StringIO(html))
df = tables[0]
print(df.shape)
```

The `wait_for_selector` line is the part people drop and then debug for an hour. If you
serialize the DOM before the rows are in it, `page.content()` returns the shell and you
are back to "No tables found", except now it looks like a pandas problem instead of a
timing one.

For the session mechanics behind that login block, keeping the cookie jar across runs so
you are not signing in on every launch, see
[scraping behind a login](how-to-scrape-behind-login-playwright.md).

## Fix the dtype inference that corrupts your IDs

Now the part that bites silently. `read_html` runs the same type inference `read_csv`
does, and it has no idea which columns are identifiers. Anything that looks numeric
becomes numeric.

The classic loss is a SKU, part number, or zip code column full of values like `00741`.
pandas sees digits, calls it `int64`, and stores `741`. The leading zeros are gone, the
value no longer matches the source system, and nothing errored. A column that mixes text
and numbers gets the opposite treatment: it collapses to `object`, and your numeric
comparisons downstream start throwing or, worse, comparing strings.

Look before you trust:

```python
print(df.dtypes)
# sku       int64      <- wrong, this is an identifier and lost its leading zeros
# price     object     <- wrong, "1,299.00" never parsed to a number
# in_stock  object     <- wrong, "Yes"/"No" instead of bool
```

The rule is: never let inference decide an ID column, and coerce the rest explicitly.

```python
# identifiers are strings, always, so leading zeros and formatting survive
df["sku"] = df["sku"].astype("string")

# money arrives as text with separators; strip them, then parse, keeping bad rows visible
df["price"] = pd.to_numeric(
    df["price"].str.replace(r"[,$]", "", regex=True),
    errors="coerce",   # unparseable -> NaN you can count, not a crash you cannot
)

# booleans by explicit mapping, not truthiness
df["in_stock"] = df["in_stock"].map({"Yes": True, "No": False}).astype("boolean")
```

Do this the moment the DataFrame exists, before any join or aggregation. A `sku` that
silently became `int64` will merge against a string key and match nothing, and you will
not find out until the counts look wrong.

A concrete before-and-after from our own test fixtures: a 240-row product table read
straight from the URL returned an empty result (client-rendered), and read from
`page.content()` returned all 240 rows but with 3 of the 8 columns typed wrong, including
the SKU column, where 61 values carried a leading zero that inference dropped. The rows
came from the browser; the correctness came from the explicit coercion. Neither step is
optional.

## read_html reads one page, it cannot paginate or click

Worth being blunt about the boundary. `read_html` is a parser, not a browser. It consumes
one already-rendered HTML string and returns the tables in it. It cannot click "next", it
cannot scroll, it cannot follow a "load more" button, and it has no session of its own.

So the division of labour is fixed: the browser navigates, waits, and paginates; pandas
types. For a multi-page table you drive the pagination in Playwright and accumulate:

```python
frames = []
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")

    while True:
        page.wait_for_selector("table#results tbody tr")
        frames.append(pd.read_html(io.StringIO(page.content()))[0])

        next_button = page.query_selector("a[rel=next]:not([disabled])")
        if not next_button:
            break
        next_button.click()
        page.wait_for_load_state("networkidle")

df = pd.concat(frames, ignore_index=True)
# then apply the dtype coercion above, ONCE, on the concatenated frame
```

Coerce dtypes after the `concat`, not inside the loop: inference can pick a different type
per page, and concatenating an `int64` chunk with an `object` chunk gives you `object` for
the whole column. The loop mechanics, and when to prefer `networkidle` over a selector
wait, are in [scraping paginated pages](how-to-scrape-paginated-pages-playwright.md).

## The stealth part is upstream of pandas, why the rows exist at all

None of the above helps if `page.content()` comes back as a challenge page instead of your
table. That is the upstream question, and it is where the browser doing the rendering
matters.

`read_html` is only ever as good as the HTML you feed it, and the HTML is only there if
the site served the real page to your session. A rendered, logged-in, returning session
is what produces the rows; an automation tell anywhere in the stack produces a login loop
or an interstitial, and then pandas faithfully parses that instead.

This wrapper is stock Playwright driving a Firefox patched at the engine level, so the
fingerprint the page reads is a real browser's: same GPU, fonts, audio and screen every
run for a given seed, which is why `seed=42` above gives you a reproducible machine to
debug against rather than a new identity per launch.

The honest caveat: the fingerprint is handled, the behaviour and the exit are not free.
A table that only appears after interaction still needs real interaction, and a session on
a flagged address still gets the challenge page. If your `page.content()` is returning the
wrong page rather than the wrong dtypes, that is a detection problem, not a pandas one, and
[the detected-as-a-bot checklist](playwright-detected-as-bot.md) is the order to work it in.

## Conclusion

`read_html` is a great last mile and a terrible first one. Give it a URL and it renders
nothing and logs in to nothing; let inference run and it turns identifiers into integers
without a word. The working recipe is two moves: feed it `page.content()` after the browser
has rendered and authenticated, and coerce every dtype explicitly, IDs to string first. The
browser earns the rows, pandas types them, and you check the `dtypes` before you trust the
frame.

## Short answers to the questions that lead here

**Can pandas.read_html scrape a JavaScript-rendered table?** Not from a URL. It makes a
plain HTTP request with no JS engine. Render the page in Playwright, then pass
`page.content()` to `read_html` instead of the URL.

**Why did read_html return the wrong table or no table?** Because it parsed the logged-out
or pre-render HTML the server sends an anonymous client. Log in and wait for the rows in the
browser, then serialize the DOM with `page.content()`.

**Why did my SKU or zip code column lose its leading zeros?** Type inference saw digits and
made it `int64`. Set identifier columns to `string` explicitly with
`df["sku"] = df["sku"].astype("string")` and never let inference decide an ID.

**How do I stop read_html guessing dtypes wrong?** Read the frame, print `df.dtypes`, then
coerce: `astype("string")` for IDs, `pd.to_numeric(..., errors="coerce")` for numbers after
stripping separators, explicit `map` for booleans.

**Can read_html follow pagination or click a button?** No. It parses one HTML string.
Drive the pagination in Playwright, collect one DataFrame per page, `pd.concat` them, and
coerce dtypes once on the combined frame.

**Do I need io.StringIO around the HTML?** Yes, in current pandas. `read_html` wants a
file-like object for literal HTML, so wrap the `page.content()` string in `io.StringIO`.

## Sources

- The [pandas `read_html` documentation](https://pandas.pydata.org/docs/reference/api/pandas.read_html.html):
  its own HTTP fetch on a URL, its shared type-inference path with `read_csv`, and the
  `io.StringIO` requirement for literal HTML input.
- This project's scraping test fixtures, including the 240-row product table measured both
  from the URL (empty) and from `page.content()` (full rows, three columns mistyped).

**See also:** [scraping HTML tables](how-to-scrape-html-tables-playwright.md) for the
extraction mechanics before the typing step, [scraping behind a login](how-to-scrape-behind-login-playwright.md)
for the session the gated table needs, and [scraping paginated pages](how-to-scrape-paginated-pages-playwright.md)
for multi-page collection.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The leading-zero SKU bug
above is one I shipped into a join before I learned to print dtypes first.*
