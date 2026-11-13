---
title: "How to scrape shipping rates with Playwright"
description: "Scrape shipping rates with Playwright: a rate is keyed to destination, weight, dimensions and service level, the quote response carries surcharge lines the total hides, and free shipping makes the curve a step function."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 105
---


# How to scrape shipping rates with Playwright

To scrape shipping rates with Playwright, drive a real basket to the step where the site
actually quotes, capture the quote response rather than the total drawn on screen, and key
every row to destination, billable weight, parcel dimensions and service level, because a
rate is the answer to a four-part question and a row that records three parts cannot be
compared to anything.

A shipping rate looks like a price and behaves like a calculation. The figure on the
checkout line is the end of a chain that began with an address, a parcel shape and a
service the customer picked, then had fuel, residential and remote-area adjustments folded
in on the way to the screen. Save the figure alone and you have a number nobody can
reproduce a week later.

This page is the row shape that survives a second run, and the four places a rate scrape
goes wrong: the charged weight, the missing basket, the free-shipping step, and an
endpoint that is limited hard.

## Four inputs decide a rate, and the row needs all four

Destination, weight, dimensions, service level. Move any one and the number moves, so a
row missing one is a price with an unlabelled variable inside it.

Service level is the part most often dropped, because the checkout preselects an option
and the scraper reads whatever was highlighted. Two rows both saying 5.99 are not
comparable when one is a three-day economy service and the other is next-day before noon.
Keep the label the site printed, verbatim, not a normalised guess at the tier.

```python
row = {
    "destination": "",          # postcode or full address, as specific as the form took
    "actual_weight_g": None,    # what the goods weigh
    "billable_weight_g": None,  # what the carrier charged for, if the page says
    "length_mm": None,          # the parcel, not the product
    "width_mm": None,
    "height_mm": None,
    "service_level": "",        # the carrier's own label, copied as printed
    "carrier": "",
    "currency": "",
    "amount": None,             # the total as displayed
    "components": [],           # the separate charge lines behind that total
    "basket_subtotal": None,    # free shipping is a step function of this
    "basket_state": "",         # hash of the items that produced the quote
    "read_at": "",              # UTC, stamped when the response landed
}
```

`basket_state` and `read_at` are there for the same reason as the other four. A rate is
valid for one basket at one moment, and a row that cannot name its basket is a measurement
of something you can no longer identify.

## The charged weight is rarely the weight you measured

Carriers bill on the greater of actual weight and dimensional weight, which is volume
divided by a divisor. A large light box is charged as though it were heavy. The change is
not gradual: it happens the moment the volume figure passes the real one.

The page almost never says which rule bound. It prints one weight, when it prints any, and
that weight is the charged one. So the product page's weight field is not the input the
quote used, and copying it into the row as if it were is the quiet error in most shipping
datasets.

```python
def billable_weight_g(length_mm, width_mm, height_mm, actual_g, divisor_cm3_per_kg=5000):
    """Returns the charged weight, which rule produced it, and the divisor assumed.

    The divisor is a contract term, not a law.
    """
    volume_cm3 = (length_mm / 10.0) * (width_mm / 10.0) * (height_mm / 10.0)
    dimensional_g = (volume_cm3 / divisor_cm3_per_kg) * 1000.0
    if dimensional_g > actual_g:
        return dimensional_g, "dimensional", divisor_cm3_per_kg
    return actual_g, "actual", divisor_cm3_per_kg
```

Two details there earn their place. The divisor travels out with the answer, because a
billable weight is not a quantity until you say which divisor produced it. And the
dimensions are the parcel's, not the product's: packaging adds millimetres in every
direction, which near the boundary is enough to flip which rule binds.

When the site prints only one weight, write it to `billable_weight_g` and leave
`actual_weight_g` empty. An empty field is honest. The same number in both asserts an
agreement nobody stated.

## The rate does not exist until the basket does

Plenty of sites quote nothing until checkout. No rate table, no shipping page, no widget.
The number is produced when a basket holding real items is handed to a real address, and
not before.

That makes the basket part of the apparatus rather than a step on the way to it.

```python
from invisible_playwright import InvisiblePlaywright

def quote_for(skus, destination, seed=42, proxy=None):
    with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
        page = browser.new_page()

        for sku in skus:
            page.goto(f"https://example.com/p/{sku}", wait_until="domcontentloaded")
            page.get_by_role("button", name="Add to basket").click()

        page.goto("https://example.com/checkout", wait_until="domcontentloaded")
        page.fill("#delivery-postcode", destination)

        # open the wait before the click, so the quote cannot land in the gap
        with page.expect_response(
            lambda r: "/shipping" in r.url and r.status == 200
        ) as info:
            page.get_by_role("button", name="Calculate delivery").click()

        return info.value.json()
```

The quote is a live server call, sometimes one the retailer pays a carrier for, and it can
land before a fixed sleep finishes counting. `expect_response` removes that race by
opening the wait first. The checkout around it is usually a stepper whose shipping step
refuses input until the address step validated, which is
[a multi-step wizard flow](how-to-scrape-multi-step-wizard-flow-playwright.md).

A product-page widget that offers a rate before any basket exists is quoting a default
parcel, one unit, from a default warehouse. That is a legitimate reading and a cheap one,
but it is not the customer's rate, so `basket_state` has to say which of the two the row
holds. Baskets live in cookies and storage, so
[a browser context per session](isolate-identities-browser-context-per-session.md) keeps
two samples independent.

## Read the quote response, not the total on screen

The display folds every component into one figure. The response usually keeps them apart:
base rate, fuel, residential, remote area, oversize, insurance, tax, each with its own
code and amount.

```python
def capture_quotes(page, fragment="/shipping/quote"):
    """Collect parsed quote payloads as they arrive."""
    payloads = []

    def on_response(resp):
        if fragment in resp.url and resp.request.resource_type in ("xhr", "fetch"):
            try:
                payloads.append(resp.json())
            except ValueError:
                pass   # not JSON; leave it out rather than guessing at the body

    page.on("response", on_response)
    return payloads


def charge_lines(option):
    # keep the lines apart: the sum is derivable, the split is not
    return [
        {"code": line.get("code", ""),
         "label": line.get("label", ""),
         "amount": line.get("amount")}
        for line in option.get("charges", [])
    ]
```

The sum is derivable from the split. The split is not derivable from the sum. Once the
lines collapse into a total you cannot separate a fuel adjustment that moves on the
carrier's schedule from a residential surcharge fixed to the address. A series built on
totals reads the first as a policy change.

One checkout load fires several calls that look alike, one for basket totals, one for tax,
one for shipping options, and telling them apart by URL fragment alone gets brittle.
Matching on the body instead is in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## Surcharges attach to the address as much as to the parcel

Residential delivery, remote area and fuel are conditions, not noise, and they behave
nothing like each other. Fuel is a percentage of the base rate that the carrier revises on
its own schedule. Residential is a classification of the destination. Remote area is a
lookup against a postcode list the carrier publishes and edits.

So two neighbouring postcodes can differ by an amount that has nothing to do with
distance, and one can move next week while nothing about your parcel changed.

Copy the codes the response used, verbatim, and do not invent one where it gave none. If
the payload returns one figure with no breakdown, record it and leave `components` empty.
A reconstructed split in the same column as an observed one is a guess promoted to data.

One more trap. Some sites classify residential only after address verification runs, which
makes a postcode-only quote and a full-address quote two different measurements at the
same destination. Record which one the form actually accepted.

## Free shipping turns the rate into a step function

A free-shipping threshold means the rate is a staircase. Sample one basket value and you
learn the height of one tread. You do not learn where its edges are, and a zero only tells
you the test basket sat above a line somewhere.

Finding the edge is a search, and it is worth doing efficiently: every sample costs a
basket build plus a quote.

```python
def find_threshold(quote_at, low_cents, high_cents, tolerance_cents=100):
    """quote_at(subtotal_cents) -> shipping cost in cents, 0 when free."""
    if quote_at(high_cents) != 0:
        return None                     # no threshold inside the range searched
    while high_cents - low_cents > tolerance_cents:
        mid = (low_cents + high_cents) // 2
        if quote_at(mid) == 0:
            high_cents = mid
        else:
            low_cents = mid
    return high_cents                   # first subtotal known to ship free
```

A linear sweep in steps of 10 across a range of 200 is 20 quotes. Bisection to the same
resolution is about 5, since each step halves what is left. Against an endpoint that
watches, that is the difference between a search that finishes and one throttled halfway.

Bisection assumes the cost drops to zero once and stays there. Most thresholds behave that
way, but some do not, because the rule is per category or per service level, so confirm
the boundary with one sample either side. The threshold is also often set per destination
zone, so it has to be found per zone.

## Pace to the endpoint, and vary the destination

Quoting is expensive on the server. Every answer is a live calculation, often including a
paid call out to a carrier, so this endpoint is limited far harder than any product page
on the same site.

The request shape that gets throttled fastest is a burst of quotes to one destination,
which is exactly what a threshold search looks like when each search runs to completion.
Interleave instead: one step of each search per round.

```python
import random
import time

def interleaved_search(destinations, seed=42):
    """One bisection step per destination per round, not one search to completion."""
    rng = random.Random(seed)                     # same seed as the browser identity
    searches = {d: Bisection(low=0, high=20000) for d in destinations}

    while any(not s.done for s in searches.values()):
        pending = [d for d in destinations if not searches[d].done]
        rng.shuffle(pending)
        for destination in pending:
            search = searches[destination]
            cost = quote_for(basket_for(search.next_subtotal()), destination)
            search.record(cost)
            time.sleep(rng.uniform(20, 60))       # pace to the endpoint, not the hardware

    return {d: s.threshold for d, s in searches.items()}
```

`Bisection` is the loop from the previous section as a small state machine, one per
destination. Steps stay sequential within a destination, since each depends on the
previous answer, but not with each other. Round-robin spreads any single address across
the whole run.

Seed the shuffle and the sleep from the same value that drives the browser identity, so
the run replays exactly when a row needs rechecking. One context, sequential requests, and
an interval set by how fast rates move rather than by how fast the machine can ask.

Parallel contexts across destinations are the pattern the limit exists to stop. Picking an
interval is in [rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md),
and what to do when the endpoint answers with a status code instead of a quote is in
[403 and 429 backoff mid-scrape](how-to-handle-403-429-backoff-mid-scrape-playwright.md).

## Where this stops working

Three cases, stated plainly, because a better row shape fixes none of them.

Contract rates are the big one. A logged-in business account is quoted from a negotiated
table the public checkout never shows, and that gap is the point of having a contract. A
public scrape measures list rates, and calling them "the rates" is the error. When the
account is yours, [scraping behind a login](how-to-scrape-behind-login-playwright.md) is
the path; when it is not, the number is out of reach.

Second, some checkouts refuse to quote until a payment method or an account exists. There
is no anonymous reading of that endpoint, and no amount of session realness produces one.

Third, a quote endpoint behind an interactive challenge is where the run stops. This
library does not solve captchas and does not claim to. It makes the browser a real one,
which is a different problem from being asked to prove it by hand.

## Conclusion

Shipping rates punish a thin row shape, because the number is a function and the page
prints only its output. Key every row on destination, billable weight, parcel dimensions
and service level, and say which basket and which moment produced it. Read the quote
response so the surcharge lines survive into your table. Treat free shipping as a boundary
to be searched, per destination zone, rather than a value to be sampled. Then slow the run
down and spread the samples, because each answer costs the retailer real money to compute.
The parsing is the easy half; knowing what the number is a function of is what decides
whether the table means anything.

## Short answers to the questions that lead here

**Why do two runs return different shipping costs for the same product?** At least one of
the four inputs differed. The basket subtotal is a fifth, since it decides which side of a
free-shipping threshold the quote lands on.

**Can I get a rate without putting anything in the basket?** On some sites, from a
product-page widget, which quotes a default parcel of one unit from a default warehouse.
That is a baseline, not a customer's rate, and the row must say which.

**The weight on the page does not match the product weight. Which is right?** Both. The
page shows billable weight, the greater of actual weight and volume divided by the
carrier's divisor. Store it as the billable figure and leave the actual weight empty.

**Why does my total not match the sum of the charges I collected?** The display folds base
rate, fuel, residential and remote-area lines into one figure. Read them from the quote
response, where they usually arrive as separate coded lines.

**How do I find the free-shipping threshold?** Bisect the basket subtotal between a value
that ships free and one that does not, then confirm with a sample either side. One basket
value gives the height of a step, not its position.

**How fast can I poll a quote endpoint?** Slower than a product page, because every answer
is a live calculation and often a paid carrier call. Keep requests sequential in one
context and interleave destinations instead of bursting against one.

## Sources

- Playwright documentation, [Network](https://playwright.dev/python/docs/network),
  retrieved 2026-08-28
- Playwright documentation, [Events](https://playwright.dev/python/docs/events),
  retrieved 2026-08-28
- Playwright documentation,
  [page.expect_response](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  retrieved 2026-08-28
- Playwright documentation,
  [Auto-waiting and actionability](https://playwright.dev/python/docs/actionability),
  retrieved 2026-08-28
- Playwright documentation,
  [Browser contexts](https://playwright.dev/python/docs/browser-contexts),
  retrieved 2026-08-28

**See also:** [scraping delivery slots](how-to-scrape-delivery-slots-playwright.md) for the
sibling reading that is also scoped to a basket and an address,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for
finding the quote call among the several a checkout fires,
[rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md) for pacing
against an expensive endpoint, and
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for turning the printed total into a number.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The free-shipping threshold
is the one that cost real time: a rate table was built from one basket value per
destination, and half the rows were zeros that only meant the test basket sat above a line
nobody had looked for.*
