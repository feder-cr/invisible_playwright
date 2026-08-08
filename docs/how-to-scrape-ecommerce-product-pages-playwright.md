---
title: "How to scrape e-commerce product pages with Playwright"
description: "Product price and stock live in a variant XHR, not the served HTML. Select each variant, wait for the price response, and cross-check the Product JSON-LD."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 27
---


# How to scrape e-commerce product pages with Playwright

To scrape an e-commerce product page with Playwright, drive a real browser, select each
variant, wait for the offers XHR that the selection fires, and read the price and stock
from that response, then cross-check it against the page's Product JSON-LD. The price you
want is rarely in the served HTML: it is computed by client JavaScript after load, and a
variant's price does not exist until you pick the variant.

The mistake that wastes the most time on a product page is assuming the price is in
the HTML you were served. On a lot of storefronts it is not. The number you can see in
the browser was written by client JavaScript after load, and the number that matters,
the one tied to a specific size or color, only exists after you pick that variant and
the page fetches it.

This page is about scraping the page a human actually sees: pick each variant, wait for
the request that returns its price and stock, and read the canonical Product JSON-LD as
a second opinion. It uses stock Playwright throughout, driving a browser that runs the
variant scripts instead of guessing at them.

## Why the price is not in the HTML

The price is missing from the served HTML because the storefront computes the base price
in the browser after load, and a variant's price is fetched only once you select that
variant. An HTTP client that never runs the page's JavaScript therefore reads a template,
not a number.

Open a product page, right click, view source, and search for the price. Frequently it
is not there at all, or the number in the raw HTML is a placeholder that the rendered
page later replaces.

Two separate things are going on, and both defeat a requests-only scraper:

- **The base price is written by client JS after load.** The served HTML ships a
  template, and a script fills in the current price once it runs. Fetch the URL with an
  HTTP client and you read the template: an empty node, or last week's cached number.
- **The variant price does not exist until you choose the variant.** Selecting a size
  or a color fires an XHR to an offers endpoint that returns that SKU's price,
  availability and, often, its own image set. Nothing on the page carried those numbers
  before the request. There was nothing to scrape.

So the correct unit of work is not "load the page and read a number". It is "choose a
variant, let the request happen, and read the response". That requires something that
runs the site's JavaScript, which is exactly what a browser does and an HTTP library
does not.

There are three places a product number can live, and they are not equally trustworthy:

| Where the number lives | What you get | How fresh | Best use |
|---|---|---|---|
| Variant offers XHR | Price, stock and SKU for the variant you selected | Live, computed per selection | The number to ship |
| Price node in the DOM | Base price after client JS fills it | Live for the default variant | When there is no variant to pick |
| Product JSON-LD | Structured base offer embedded for search engines | As fresh as the render, can lag | Cross-check, not source of truth |

## Select the variant and wait for the price XHR

Playwright gives you the request directly. Wrap the click or the select in
`page.expect_response`, and you get the actual offers payload rather than whatever the
DOM happened to show a moment later. The wrapper is a drop-in for stock Playwright, so
`browser` below is a real Playwright `Browser` and every method is the one you already
know.

```python
from invisible_playwright import InvisiblePlaywright

# seed fixes the whole machine: same GPU, canvas, audio, fonts, screen every run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/p/12345", wait_until="domcontentloaded")

    # picking the variant is what triggers the offers request
    with page.expect_response(
        lambda r: "/offers" in r.url and r.status == 200
    ) as resp_info:
        page.select_option("#variant-size", "M")

    offer = resp_info.value.json()
    print(offer["sku"], offer["price"], offer["availability"])
```

`expect_response` opens the wait before the action, so there is no race: the click
fires, the browser sends the XHR, and `.value` blocks until the matching response lands.
Reading `resp_info.value.json()` gives you the numbers the site itself computed, not a
DOM node you have to trust. If you would rather subscribe to responses across a whole
session instead of one at a time, [capturing XHR and API responses in Playwright](how-to-capture-xhr-api-responses-playwright.md)
covers the listener form.

If the base price is what you are after and it is painted after load rather than behind
a variant click, wait for the value to appear instead of guessing at a delay. Blind
`sleep` calls are the classic flake here, and [waiting for the page to actually finish
loading](how-to-wait-for-page-load-playwright.md) has the specific predicates.

```python
# the price node exists in the HTML but is empty until JS fills it
page.wait_for_function(
    "() => document.querySelector('#price')?.textContent.trim().length > 0"
)
price = page.inner_text("#price")
```

## Read the canonical Product JSON-LD as a cross-check

Most storefronts also embed a `application/ld+json` block describing the product for
search engines. It is a structured, documented format, and it is the cheapest
cross-check you have against the number you scraped from the offers XHR. When the two
agree you are confident; when they disagree you have learned something real about the
page rather than shipping a wrong number silently.

```python
import json

def product_jsonld(page):
    blobs = page.eval_on_selector_all(
        'script[type="application/ld+json"]',
        "els => els.map(e => e.textContent)",
    )
    for raw in blobs:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        # a page may ship a list or a @graph; normalize to a flat list
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if item.get("@type") == "Product":
                return item
    return None

product = product_jsonld(page)
if product:
    offers = product.get("offers", {})
    # offers can be a single Offer or a list of them, one per variant
    print(offers)
```

Two honest limits on the JSON-LD. It is often the base offer only, so it is a check on
the default variant, not on the size M you just selected. And it is only as fresh as the
render, so on a page that updates price by XHR the JSON-LD can lag. Treat it as
corroboration for the offers response, not as a replacement for it.

## Enumerate variants across many SKUs from one session

A real catalogue crawl is not one price. It is every variant of every SKU, which means
looping the select-and-wait step across a page's whole option set and then across many
pages. That is a burst of XHR from a single browser session, and it only works if the
session behaves like one consistent visitor for its whole duration.

```python
from invisible_playwright import InvisiblePlaywright

def scrape_all_variants(page, sizes):
    rows = []
    for size in sizes:
        with page.expect_response(
            lambda r: "/offers" in r.url and r.status == 200
        ) as resp_info:
            page.select_option("#variant-size", size)
        offer = resp_info.value.json()
        rows.append({
            "size": size,
            "sku": offer["sku"],
            "price": offer["price"],
            "stock": offer["availability"],
        })
    return rows

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    for product_url in product_urls:
        page.goto(product_url, wait_until="domcontentloaded")
        sizes = page.eval_on_selector_all(
            "#variant-size option",
            "els => els.map(o => o.value).filter(Boolean)",
        )
        for row in scrape_all_variants(page, sizes):
            print(product_url, row)
```

Selecting a variant is a user action, and driving it through a real cursor rather than a
teleporting click keeps the interaction consistent with everything else the session
does; the wrapper arcs the pointer to controls on a Bezier curve by default, which is
covered under [human mouse movement](human-mouse-movement.md). The point is that the
offers endpoint keeps answering only if the session stays coherent from the first SKU to
the last.

## Why a real engine and a fixed seed matter here

Two properties of the tool do real work in this specific task.

**The engine runs the variant scripts.** The whole reason the price exists is that
JavaScript computed it in response to your selection. A browser that actually executes
that code gets the number; an HTTP client that does not, gets the template. This is not
a stealth point, it is a correctness point: requests-only scraping reads the wrong value
here even when nothing is blocking you.

**A fixed seed keeps the crawl one machine.** Pass `seed=42` and every fingerprint field
the identity implies (GPU, canvas hash, audio context, fonts, screen, hundreds of
fields in all) comes back identical on every run. That matters mid-crawl for a plain
reason: the offers endpoint you hit forty times in a row sees one consistent visitor
rather than a machine whose fingerprint drifts between SKUs. It also matters between
runs, because a crawl that regresses can be replayed as the exact same machine instead
of a new random one, so a bisect is a bisect. That is the same seed-fixing habit that
makes any detection failure reproducible; see [pinning fingerprint fields](pinning.md)
if you need one field held to a specific value while the rest stays seed-derived.

## The honest caveat: JavaScript is not quota

Running the site's JavaScript solves the correctness problem. It does not solve the
volume problem, and it is important not to sell it as if it did.

Enumerating every variant of every SKU is, by construction, a lot of requests to one
offers endpoint in a short window. A coherent browser fingerprint does nothing about
per-account or per-IP request quotas. Hit that endpoint fast enough from one address and
you get rate limited, throttled, or served stale numbers, and no fingerprint work
changes that arithmetic. The engine gets you the right number per request; it does not
grant you unlimited requests.

So a large catalogue still needs the boring infrastructure: pace the crawl so you are
not bursting, and spread it across exits so no single address carries the whole volume.
[Rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) and
[rotating proxies across the crawl](how-to-rotate-proxies-playwright.md) are the two
pieces that turn a working single-SKU scrape into a catalogue crawl that finishes. Note
that rotation trades against the fixed-seed coherence above: rotate the exit, and let
the timezone follow it rather than pinning one, which is why
[timezone and proxy have to agree](timezone-proxy-mismatch.md).

## Conclusion

Product-page scraping goes wrong at the same place for almost everyone: the price is
treated as text in the HTML when it is really the result of a request the page makes
after you pick a variant. Drive the variant selection with a real browser, wait for the
offers response with `expect_response`, cross-check it against the Product JSON-LD, and
fix the seed so the whole crawl is one consistent machine. Then accept the honest limit,
that running JavaScript is not the same as having quota, and pace and rotate accordingly.
Do that and you scrape the page the customer sees rather than the template the server
shipped.

## Short answers to the questions that lead here

**Why is the price empty when I scrape the HTML?** Because it is written by client
JavaScript after the page loads, or it only exists after you select a variant and the
page fetches it. The raw HTML you download carries a template, not the number.

**How do I get the price for a specific size or color?** Select that variant and wait
for the request it fires. Wrap the selection in `page.expect_response` and read the
offers payload from the response, which is the number the site itself computed.

**Do I need a real browser or will requests work?** You need something that runs the
site's JavaScript. A requests-only scraper reads the empty template here even when
nothing is blocking you, because the price is produced by code that never runs.

**What is the JSON-LD good for?** It is a cross-check. The `application/ld+json` Product
block gives you a structured offer to compare against the number you scraped, so a
disagreement surfaces instead of shipping silently. It is usually the base variant only.

**Why fix the seed when scraping a catalogue?** So the whole crawl is one consistent
machine. The offers endpoint you hit repeatedly sees a stable visitor, and a failing run
can be replayed as the exact same identity rather than a new random one.

**Can I just crawl the whole catalogue fast?** No. A coherent fingerprint does nothing
about per-account or per-IP quotas. Enumerating every variant is a burst of requests, so
you still have to pace the crawl and rotate exits or you get rate limited.

## Sources

- Playwright's documented [`expect_response`, `eval_on_selector_all` and
  `wait_for_function`](https://playwright.dev/python/docs/api/class-page), used here
  exactly as upstream defines them.
- The schema.org [Product](https://schema.org/Product) and
  [Offer](https://schema.org/Offer) vocabulary that storefronts embed as JSON-LD.
- This project's own measurements of product pages whose price and stock arrive only in
  a variant XHR, with the base price written by client JavaScript after load.

**See also:** [capturing XHR and API responses in Playwright](how-to-capture-xhr-api-responses-playwright.md),
[waiting for the page to actually finish loading](how-to-wait-for-page-load-playwright.md),
and [rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The empty-price problem is
one I shipped a wrong number over before I learned to wait for the request.*
