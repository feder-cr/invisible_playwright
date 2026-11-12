---
title: "How to scrape currency and locale switchers with Playwright"
description: "Scrape currency and locale switchers with Playwright: find which signal the site obeys by moving one at a time, record the final URL, and separate a display conversion from a real regional price."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 113
---


# How to scrape currency and locale switchers with Playwright

**To scrape currency and locale switchers with Playwright, work out which signal the site
actually obeys before changing anything: move one signal per run (the cookie, the URL, the
context locale, or the proxy exit), read back the final URL, the cookies and the rendered
price, and stamp every row with the locale, the currency code, the tax rule and the URL you
ended on.** A price without the locale it was read under is not comparable to any other
price, including the one you read yesterday.

The same product page is not one page. Move the locale and the amount changes, the
availability changes, the shipping line changes, and sometimes the product list itself
changes because a variant is not sold in that market. Two rows pulled from the same address
an hour apart can disagree for reasons that have nothing to do with the seller.

Which version you got is decided by four competing signals, and the site resolves the
disagreement between them using a rule it never states. This page is about finding that rule
by experiment instead of assuming it, and about the row shape that keeps the answer readable
a month later.

## Four signals decide the locale, and the site picks the winner

A locale switcher is a control on the page, but the locale itself is not stored in one
place. Four independent signals feed it, they can contradict each other, and every site
ranks them differently.

| Signal | Where it lives | How you move it |
|---|---|---|
| Cookie or storage entry | set by the switcher, read on every request | `context.add_cookies()`, or click the control |
| URL path, subdomain or query | the address itself | request that address directly |
| `Accept-Language` header | the HTTP request, sent before any script runs | `new_context(locale=...)` |
| IP geolocation | the address the request left from | the proxy exit |

There is often a fifth behind a login, an account preference that outranks all four and is
invisible until you are signed in. If a scrape reads one price signed out and another signed
in, that preference is the first thing to check.

The ranking is the part nobody documents. One site treats the URL as authoritative. Another
lets the cookie override it, so a stored preference quietly rewrites the page you asked for.
A third reads the exit IP on the first load only, writes a cookie, and never looks at the IP
again. Guessing which one you have wastes more time than measuring it.

## Move one signal per run and read back what the page did

Change two signals at once and the result tells you nothing, because you cannot attribute
the difference. The probe below sets exactly one thing per call and returns everything you
need to compare runs, including the address you ended on rather than the one you asked for.

```python
from invisible_playwright import InvisiblePlaywright

def probe(url, price_selector, proxy=None, locale=None, cookies=None):
    with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
        context = browser.new_context(locale=locale) if locale else browser.new_context()
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        response = page.goto(url, wait_until="domcontentloaded")
        return {
            "asked_for":  url,
            "final_url":  page.url,
            "status":     response.status if response else None,
            "price_text": page.locator(price_selector).first.inner_text(),
            "html_lang":  page.get_attribute("html", "lang"),
            "cookies":    {c["name"]: c["value"] for c in context.cookies()},
            "languages":  page.evaluate("navigator.languages"),
        }
```

Run it once as a baseline, then once per signal. The signal that moves `price_text` is the
one the site reads. If two of them move it, you have a precedence chain, and you find the
order by setting those two to values that contradict each other on purpose: whichever one
the page follows sits higher. That is a two-run experiment, and it settles a question that
is otherwise argued about for an afternoon.

Read `cookies` and `html_lang` on every run even when the price did not move. A site that
silently writes a locale cookie on first load has just made your next run start from a
different state, and that is how a clean experiment stops being clean on the second
iteration.

## The context locale moves the header, not the site's mind

Stock Playwright's `locale` option on `new_context()` changes two things: the
`Accept-Language` header the requests carry, and the `Intl` formatting the page sees. It
does not touch the exit IP, it does not set the site's cookie, and it does not rewrite the
URL. On a site keyed to IP geolocation, every locale arm returns identical prices, and the
honest reading is that this arm never mattered, not that the switcher is broken.

Verify the arm applied before you interpret its result. A context option that did not reach
the engine is indistinguishable from a signal the site ignores, and both look like a null
result.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(locale="de-DE")
    page = context.new_page()

    sent = {}
    page.on("request", lambda r: sent.setdefault("al", r.headers.get("accept-language")))
    page.goto("https://example.com/product/123", wait_until="domcontentloaded")

    print("header    :", sent.get("al"))
    print("languages :", page.evaluate("navigator.languages"))
    print("formatting:", page.evaluate("new Intl.NumberFormat().format(1234.5)"))
    # If these three do not agree with each other, the arm is not an arm.
```

Do not reach for `extra_http_headers={"Accept-Language": "de-DE"}` as a shortcut. It moves
the header and leaves `navigator.languages` where it was, and in a real browser both are
formatted from one preference, so they cannot disagree. That gap is a
[header against property contradiction](accept-language-navigator-languages.md) a detector
compares directly, and it also ruins the experiment: you can no longer tell which of the two
the page followed.

A pinned locale is a probe tool, not a collection setting. One that disagrees with the exit
country recreates the [region mismatch](timezone-proxy-mismatch.md) a locale-aware site
watches for. Once you know which signal wins, move the exit and let the locale follow it,
which is the method in
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md).

## Record the final URL, because a geo-redirect moves you first

The address you requested is not always the address you read. A first load from an exit in
another country often bounces through a 30x to a regional path, sometimes to a regional home
page rather than the equivalent product. Store the URL you landed on, as a column and not as
a debug print.

```python
response = page.goto("https://example.com/product/123", wait_until="domcontentloaded")

hops = []
request = response.request if response else None
while request is not None:
    hops.append(request.url)
    request = request.redirected_from
hops.reverse()

print("asked for:", "https://example.com/product/123")
print("hops     :", hops)        # server-side 30x chain, oldest first
print("landed on:", page.url)    # also catches a client-side bounce
```

The two outputs answer different questions. `redirected_from` walks the server's redirect
chain, so it shows the rule the site applied. `page.url` is where the browser actually ended,
including a redirect performed in JavaScript or by a meta refresh, which never appears in
that chain at all. When they differ, store `page.url`.

The nastier version of this failure is silent. A bounce that lands on a country home page
still returns 200, your selector still matches something, and you have stored a number
belonging to a different product. A row whose `final_url` no longer contains the product
identifier you asked for should be dropped, not parsed.

## Read what the switcher sets instead of guessing the URL

Reverse-engineering the switcher URL by hand is slower than watching the control do it.
Click through the real menu once, diff the cookies across it, and capture the request it
fires.

```python
before = {c["name"]: c["value"] for c in context.cookies()}

with page.expect_response(
    lambda r: r.request.resource_type in ("xhr", "fetch")
) as caught:
    page.get_by_role("button", name="Currency").click()
    page.get_by_role("option", name="EUR").click()

after = {c["name"]: c["value"] for c in context.cookies()}
changed = {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v}

print("cookies changed:", changed)
print("request fired  :", caught.value.url)
print("url now        :", page.url)
```

Now the rest of the run can skip the clicking: set that cookie at context creation and load
product pages directly. Two details decide whether that works. The cookie needs a `domain`
and `path` matching what the switcher wrote, which is why copying the values out of
`context.cookies()` beats
[hand-writing a cookie dict](read-set-cookies-playwright-context.md). And some sites re-set
the cookie from the exit IP on every load, which you see because your value comes back
changed. That is not a bug in your code. It is the site telling you the IP outranks the
cookie.

If the switch fires no request and rewrites nothing in the URL, the conversion is happening
in the page, which is the next section.

## Tell a display conversion from a real regional price

A currency menu that changes every number instantly, with no request and no reload, is
usually not showing you regional prices. It is showing you one base price multiplied by a
rate the page fetched once. Those two cases carry completely different meanings and must not
land in the same column.

Three checks separate them. The ratio: divide each switched amount by its base across several
products. One constant ratio to several decimals is arithmetic, not pricing. The endings:
regional prices are set per market and land on deliberate values like 9.99, while a converted
price lands wherever the rate put it. The underlying data: a `data-price` attribute, a JSON-LD
`priceCurrency` field, or the amount the cart request sends will often still carry the base
currency after the visible glyph has changed.

```python
def is_display_conversion(base_rows, switched_rows, tolerance=0.005):
    """base_rows / switched_rows: {product_id: float amount}"""
    ratios = []
    for key, base in base_rows.items():
        switched = switched_rows.get(key)
        if base and switched:
            ratios.append(switched / base)
    if len(ratios) < 3:
        return None                     # not enough products to decide either way
    return (max(ratios) - min(ratios)) < tolerance
```

The `None` is the honest part. Two products can share a ratio by coincidence, so a verdict
from two samples is a guess wearing a return value. Where the answer is a conversion, store
the base amount and base currency as the real data and the displayed figure as a derived
number with a timestamp, because the rate moves and re-converting later gives a different
answer for a product that never changed. The rate itself is usually fetchable and worth
capturing alongside, which is
[scraping exchange rates](how-to-scrape-exchange-rates-playwright.md).

## The row shape that survives a second locale

Everything above is worth nothing if the row does not carry its own context. A stored price
needs to answer, on its own, what locale produced it and under which rules.

```python
from datetime import datetime, timezone

def build_row(page, signals, amount, currency, tax_included, price_kind):
    return {
        "captured_at":  datetime.now(timezone.utc).isoformat(),
        "final_url":    page.url,             # where you landed, not what you asked for
        "signals":      signals,              # exactly what this run set, per signal
        "html_lang":    page.get_attribute("html", "lang"),
        "currency":     currency,             # ISO code, never the glyph
        "amount":       amount,
        "tax_included": tax_included,         # True, False, or None when undetermined
        "price_kind":   price_kind,           # "regional" or "converted"
    }
```

Take the currency from a machine-readable field, never from the symbol. The `$` glyph serves
a long list of unrelated currencies, so a column that stores it has thrown away the one thing
that made the amounts comparable. The JSON-LD `priceCurrency`, a meta `content` attribute, or
the switcher's own option value all beat the rendered text.

`tax_included` is the field most datasets skip and then regret. Display rules differ by
region: the same listing can show a tax-inclusive figure in one market and a tax-exclusive
one in another, so two amounts that look equal are not. It is rarely a structured field,
usually a sentence beside the price, so store `None` when you could not determine it rather
than defaulting to `False`. A null is a known gap. A wrong default is a silent error that
compounds across the whole comparison.

The last trap is the decimal separator, which flips with the locale you just changed.
`1.234,56` and `1,234.56` are the same amount in different conventions, and a parser keyed to
the wrong one turns twelve hundred into one point two without raising anything. Key the
parser to the locale of the row, not to a global assumption, and keep the raw string beside
the parsed number, as in
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

## Conclusion

Locale is not a setting you apply. It is a negotiation between four signals that the site
resolves without telling you. Find the winner by moving one signal per run and reading back
the final URL, the cookies and the price, rather than moving three and reasoning about the
result. Verify each arm applied, because an inert arm and an ignored signal produce the same
null. Separate a regional price from a client-side conversion before either reaches a column,
and stamp every row with its locale, ISO currency, tax rule, capture date and landing URL.
Rows built that way stay comparable. Rows without that stamp are numbers that happen to sit
in the same table.

## Short answers to the questions that lead here

**Does setting `locale` in Playwright change the site's language?** It changes the
`Accept-Language` header and the `Intl` formatting for that context, and nothing else. A
site that keys on IP geolocation or on its own cookie will ignore it completely, so the arm
returning no change is a valid result rather than a failure.

**Which signal does a site actually use for locale?** There is no general answer, so measure
it. Move one signal per run and see which one moves the price. When two of them do, set them
to conflicting values and the page shows you which one ranks higher.

**Why did I get a different country's page than the URL I requested?** A geo-redirect fired
on the first load. Read `page.url` after loading rather than trusting the address you passed
to `goto`, and walk `request.redirected_from` if you want the server-side chain that caused
it.

**Is a currency switch showing me real local prices?** Often not. If the switch fires no
request and every amount changes by the same ratio, it is one base price times a fetched
rate. Store the base amount and treat the displayed figure as a derived number with a
timestamp.

**Can I compare prices across two locales directly?** Only if you recorded the currency code,
whether tax is included, and the capture date for both. Tax display rules differ by region,
so two identical-looking amounts can differ by the tax rate alone.

**Should I set the locale cookie instead of clicking the switcher?** Yes, once you have
watched the switcher write it and copied the exact name, domain and path. If your value comes
back changed on the next load, the site is re-deriving locale from the exit IP and the cookie
is not the deciding signal.

## Sources

Retrieved 2026-08-28.

- Playwright's [`browser.new_context()`](https://playwright.dev/python/docs/api/class-browser#browser-new-context),
  whose `locale` option is documented as affecting the `Accept-Language` header and the
  page's `Intl` behaviour.
- Playwright's [`context.cookies()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies)
  and [`context.add_cookies()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies),
  the before-and-after diff across the switcher click.
- Playwright's [`request.redirected_from`](https://playwright.dev/python/docs/api/class-request#request-redirected-from)
  and [`page.url`](https://playwright.dev/python/docs/api/class-page#page-url): the
  server-side redirect chain, and the address finally reached.
- Playwright's [`page.expect_response()`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  used to catch the request the switcher fires.
- This project's own configuration behaviour: the browser locale and timezone are derived
  from the egress IP by default, which is why a hand-pinned locale is a probe tool.

**See also:** [scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md)
for getting every region surface to agree with the exit,
[Accept-Language against navigator.languages](accept-language-navigator-languages.md) for why
a header-only override splits a pair that cannot split in a real browser,
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md) for
the separator and parsing side, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for reading
the switcher's request instead of the repainted DOM.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The final-URL column exists
because a price series once switched region halfway through a two-week run, and nothing in
the dataset recorded that the geo-redirect had moved it.*
