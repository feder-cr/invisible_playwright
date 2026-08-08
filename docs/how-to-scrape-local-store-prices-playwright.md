---
title: "How to scrape location-based store prices with Playwright"
description: "Scrape local store prices with Playwright: drive the store or ZIP picker, verify the location cookie stuck, then align your proxy exit and timezone."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 36
---


# How to scrape location-based store prices with Playwright

To scrape location-based store prices with Playwright, drive the store or ZIP picker
first, read the location cookie back to confirm the site accepted it, then crawl with
your proxy exit and browser timezone pointed at the same region as the store. Skip that
order and every price you collect is a national default no local shopper actually sees.

For any retailer with physical stores, the price and the availability you see are not
a property of the product. They are a property of the store you are standing in front
of, and the site decides which store that is from a picker you set once and a cookie it
sets in return. Every product page after that reflects the chosen store.

Scrape without setting the location and you get a default: a national list price, or
the price for whatever store the site guessed from your exit IP. That number is real,
but no local shopper is looking at it, so as a dataset it is quietly wrong. This page is
the order that gets the right one: drive the picker, confirm the cookie stuck, then
crawl with the proxy exit and the timezone aligned to the same region.

## Why a default price is the wrong price

Two shoppers loading the identical product URL from two ZIP codes see two prices, two
"in stock" strings, sometimes two different products entirely. The URL did not change.
The store cookie did.

This is the failure mode that makes a location-scoped crawl look successful and be
useless. The requests all return 200, the selectors all match, the numbers all parse.
Nothing errors. But the number you parsed is the one served to a session that never
picked a store, and a session that never picked a store is not a session any customer
has. You measured the fallback, not the shelf.

So the first thing to establish is not "can I read the price element" but "which store
is this session bound to right now". Everything else is downstream of that answer.

## Drive the store picker before you crawl

The picker is an ordinary interaction: open it, enter a ZIP or pick a branch, confirm.
Do it once at the start of the session, on a real browser, and let the site write its
own cookie. Do not try to forge the cookie by hand. The value is usually a store ID
plus a signature you cannot reproduce, and a hand-set cookie that fails validation drops
you straight back to the default you were trying to escape.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# The proxy exit and the timezone are pinned to the SAME region as the store
# we are about to select. More on why that has to be true in the next section.
with InvisiblePlaywright(seed=42, proxy=proxy, timezone="America/New_York") as browser:
    page = browser.new_page()
    page.goto("https://example.com/store-locator")

    # Open the picker, type the postal code, confirm. Selectors are illustrative;
    # read them off the real page. These are all stock Playwright methods.
    page.click("#change-store")
    page.fill("input[name='postal-code']", "10001")
    page.click("button[data-action='set-store']")

    # Wait for the page to reflect the chosen store rather than sleeping a fixed
    # amount. A store name appearing is the signal that the cookie was accepted.
    page.wait_for_selector("[data-selected-store]")
```

The `browser` here is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so `new_page`, `click`, `fill` and
`wait_for_selector` behave exactly as they do upstream. The only difference from plain
Playwright is that the pointer arcs to each control on a curve and the fingerprint is
whole and consistent, which matters because a store picker is often the most heavily
watched interaction on the site.

## Verify the location cookie actually stuck

Clicking the confirm button is not proof the store changed. The request can be
rejected, silently reset on the next navigation, or accepted for the page you are on
and dropped when you move. Read the cookie back before you trust a single price.

```python
def selected_store(page):
    """Return the store cookie's value, or None if it was never set."""
    for c in page.context.cookies():
        if c["name"] in ("storeId", "preferredStore", "localizationCookie"):
            return c["value"]
    return None

store = selected_store(page)
assert store is not None, "picker did not set a store cookie; still on the default"
print("bound to store:", store)
```

[`page.context.cookies()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies)
is stock Playwright and it reads the jar the site actually wrote, not the one you hoped for. If it comes back without a store entry, stop: every
price you scrape from here is the default, and no amount of parsing fixes that. This is
the same principle the [how to test whether your browser is detected](how-to-test-bot-detection.md)
page insists on for fingerprints. Assert the presence of the right signal, not the
absence of a wrong one. A crawl that produced numbers is not the same as a crawl bound
to the store you meant.

With the cookie confirmed, the store stays selected for the life of the context, so a
single picker interaction covers the whole product crawl that follows.

## Align the proxy exit and timezone with the store region

Here is the trap that turns a working crawl into flagged traffic. The store you selected
has a region. Your proxy exit has a region. Your browser timezone has a region. A
detector reads all three, and it reads them together.

Select a store in one city while your exit IP is in another country and your timezone
is a third, and you have manufactured a contradiction no real customer produces. A local
shopper's store, IP and clock all point at the same place, because they are one person
in one location. Three signals have to agree, and each is set separately:

| Signal | What sets it | Must point at |
|---|---|---|
| Selected store | The picker interaction, which writes the store cookie | The store's region |
| Proxy exit IP | Your proxy choice | The store's region |
| Browser timezone | Auto-derived from the egress IP, or an explicit IANA zone you pass | The store's region |

 Three regions that disagree is both wrong data, because the store you
picked is not where you appear to be, and a mismatch a detector treats as a signal in
its own right. The store picker was working against you the moment the regions split.

The alignment is your job, not the fingerprint's. A fixed seed makes the browser
consistent with itself, but a seed says nothing about geography: it will happily present
a flawless, internally coherent browser sitting in the wrong country. Geolocation is set
by the proxy exit and the timezone, and you have to point both at the store's region
yourself.

```python
# Wrong: seed makes the browser coherent, but the store, the exit and the clock
# disagree. The fingerprint is perfect and the SESSION is not.
InvisiblePlaywright(seed=42, proxy=exit_in_texas, timezone="Europe/Rome")
# ... then select a store in New York -> three regions, three stories.

# Right: one region across all three. The default timezone auto-derives from the
# egress IP, so a proxy that exits in the store's region already lines the clock up.
with InvisiblePlaywright(seed=42, proxy=exit_in_new_york) as browser:
    page = browser.new_page()
    page.goto("https://example.com/store-locator")
    # ... select a New York store; IP, timezone and store now agree.
```

By default the timezone is derived from the egress IP, so choosing a proxy that exits in
the store's region lines the clock up for free. When you need an explicit zone, pass
`timezone=` and make it the store's zone, not your own. The full list of surfaces that
have to agree is in [when the timezone does not match the proxy](timezone-proxy-mismatch.md),
and if you are crawling from inside a container where no clock service is reachable,
[deriving the timezone offline from the proxy IP](offline-geoip-timezone-proxy.md)
covers doing it without a network round trip. For a wider treatment of pinning a session
to a place, see [how to scrape geotargeted content](how-to-scrape-geotargeted-content-playwright.md).

## Keep the location-scoped session coherent across the whole crawl

A store crawl is not one page. It is the picker, then hundreds of product pages, then
maybe pagination and stock checks, all of which must stay the same visitor as the one
that set the store. If the fingerprint drifts mid-crawl, you are a different browser
holding someone else's store cookie, which is a worse tell than never setting one.

Fixing the seed is what keeps it one visitor. Same seed, same GPU, same canvas, same
audio, same fonts, page after page, so the identity that picked the store is the
identity that reads every price under it.

```python
# One coherent identity for the entire location-scoped crawl.
with InvisiblePlaywright(seed=42, proxy=exit_in_new_york, timezone="America/New_York") as browser:
    page = browser.new_page()

    # 1. Bind the session to the store, once.
    page.goto("https://example.com/store-locator")
    page.click("#change-store")
    page.fill("input[name='postal-code']", "10001")
    page.click("button[data-action='set-store']")
    page.wait_for_selector("[data-selected-store]")
    assert selected_store(page) is not None

    # 2. Crawl product pages; every one now reflects the selected store, and the
    #    fingerprint is identical to the one that set it because the seed is fixed.
    for path in ("/p/aaa", "/p/bbb", "/p/ccc"):
        page.goto(f"https://example.com{path}")
        price = page.inner_text("[data-price]")
        stock = page.inner_text("[data-availability]")
        print(path, price, stock)   # local price and local stock, not the national default
```

If you need the same store to persist across separate runs rather than a single context,
a [persistent profile](persistent-profiles.md) keeps the cookie jar on disk so the store
survives a restart, and pinning the seed keeps the browser that owns it identical. And if
the store's region also constrains hardware you want to hold steady, such as a particular
GPU or screen, [pinning individual fingerprint fields](pinning.md) lets you fix those
while the rest stays seed-derived.

The measurement that makes all this concrete is small and worth doing once on any target:
read `page.context.cookies()` before the picker and after it. Before, there is no store
entry and the product page shows the national default. After the picker and with the
regions aligned, the same product URL shows a price that changes with the ZIP you typed.
That single cookie is the entire difference between the price a shopper sees and the price
a lazy crawl records.

## Conclusion

Location-based prices are not hidden, they are scoped, and the scope is a cookie you set
by driving the picker like a customer would. Do that first, read the cookie back to prove
it stuck, and then keep the proxy exit and the timezone pointed at the same region as the
store so the session tells one story instead of three. The seed keeps the browser coherent
across the whole crawl for free, but it will not place you on the map. That part is yours,
and it is the part that separates the local price from the default nobody pays.

## Short answers to the questions that lead here

**Why do I get a different price than the store website shows?** Because you never picked
a store. The site is serving a national default or a guess from your IP, and the real
price is scoped to a store cookie you have not set yet.

**Can I just set the store cookie directly instead of clicking the picker?** Usually no.
The value is typically a store ID plus a signature you cannot reproduce, and a cookie that
fails validation drops you back to the default. Drive the picker and let the site write it.

**How do I know the store actually changed?** Read `page.context.cookies()` after the
picker and confirm a store entry exists. A confirm click is not proof; the request can be
rejected or reset on the next navigation.

**Does the proxy region have to match the store I picked?** Yes. A store in one region
served to an IP in another is both wrong data and a mismatch a detector reads. Align the
exit and the timezone with the store's region.

**Does a fixed seed set my location?** No. The seed fixes the fingerprint so the browser
is consistent with itself, but geolocation comes from the proxy exit and the timezone. A
seeded browser will sit coherently in the wrong country until you align those two.

**Will the store stay selected across pages?** For the life of the browser context, yes,
so one picker interaction covers the crawl. Across separate runs, use a persistent profile
so the cookie jar survives a restart.

## Sources

- Playwright's own API reference for the calls used here:
  [`Browser`](https://playwright.dev/python/docs/api/class-browser) and
  [`BrowserContext.cookies()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies).
- This project's configuration behaviour: proxy schemes, and the browser timezone being
  auto-derived from the egress IP unless an explicit IANA zone is passed.
- Direct observation of retail store-scoping: the store cookie written by the picker, read
  back through `page.context.cookies()`, and the product-page price changing with the
  selected store while the URL stays fixed.

**See also:** [how to scrape geotargeted content](how-to-scrape-geotargeted-content-playwright.md)
for pinning a whole session to a place, [when the timezone does not match the proxy](timezone-proxy-mismatch.md)
for every surface that has to agree, and [persistent profiles](persistent-profiles.md)
for keeping the store across runs.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed keeps the browser
honest with itself; keeping it honest about where it is standing is still your job.*
