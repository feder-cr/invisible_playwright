---
title: "How to track product stock and restocks with Playwright"
description: "Track product stock and restocks with Playwright: poll the per-variant availability XHR, diff the in-stock boolean, and pace the poll to read as one shopper."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 35
---


# How to track product stock and restocks with Playwright

To track product stock and restocks with Playwright, poll the per-variant
availability XHR the page fires after it loads, read the in-stock boolean for the
SKU you care about, and alert the moment it flips from `false` to `true`. Run every
poll from one seed-fixed session so it reads as a single returning shopper, and pace
it with jitter so the frequency itself does not give you away.

Stock is not a number you scrape once, it is a boolean you watch. A size sells out,
then two days later it comes back, and the only thing you cared about was the moment it
flipped. That shape - watch one variant, wait for a false to become true - is different
from every other scraping job, and it is the shape this page is built around.

The catch is that the useful signal lives in an XHR the page fires after it loads, not
in the HTML you get from the first request, and that watching it means asking the same
endpoint the same question on a tight schedule. Tight repeat polling of one endpoint is
the exact pattern naive scrapers get caught on, so the second half of this page is about
making the poll look like a returning shopper rather than a burst of new machines.

## The shell HTML does not tell you what is in stock

The in-stock flag is almost never in the shell HTML; it arrives in a later XHR, so
parsing the first response tells you nothing reliable. Open the product page source
and read it: on most modern storefronts the availability you see rendered - "In
stock", "Only 2 left", a greyed-out size button - is not in that HTML at all. The
shell arrives first, then a script fires an XHR to an availability or inventory
endpoint, and the response is what paints the button state.

That means three things for a stock tracker:

- **Parsing the shell HTML is a trap.** It is either stale, generic, or absent, and it
  will disagree with what a human sees a second later.
- **Availability is per variant, not per product.** The page for one item asks about
  every size and colour it offers, and each comes back with its own in-stock boolean.
  The unit you track is the SKU, not the URL.
- **The answer is a state, not a value.** It is `true` or `false` today, and it can be
  the other one tomorrow. There is nothing to average and nothing to trend. There is
  only the flip.

So the job is: find the XHR that carries the per-variant boolean, read the boolean for
the variant you care about, and notice when it changes.

## Find the availability XHR

To find the availability XHR, load the page once with a network listener attached and
let it tell you which request carries the inventory. A patched browser driven by stock
Playwright uses the ordinary [`page.on("response")`](https://playwright.dev/python/docs/network#network-events)
event, so this is standard Playwright with nothing extra to learn:

```python
from invisible_playwright import InvisiblePlaywright

json_responses = []

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on(
        "response",
        lambda r: json_responses.append((r.status, r.url))
        if "application/json" in (r.headers.get("content-type") or "")
        else None,
    )
    page.goto("https://example.com/p/some-item", wait_until="networkidle")

for status, url in json_responses:
    print(status, url)
```

One of those URLs will carry a payload with a per-variant field - something shaped like
`{"variants": [{"id": "SKU-42-M", "in_stock": false}, ...]}` or a per-SKU endpoint like
`/api/variants/SKU-42-M/availability`. Open the responses and read them; the field names
are the site's, not ours. [Capturing and reading XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
covers the general technique in more depth, including how to grab the response body
rather than just the URL.

Note the endpoint and the exact field once, by hand. From here on you talk to that
endpoint directly.

## Poll one variant and diff the boolean

Once you know the endpoint and the field, reading current stock for one variant is a
single call. Fire it from inside the browser page, not from a bare HTTP client, so it
carries the same cookies, the same session and the same TLS handshake the storefront
just saw load its own page:

```python
def read_in_stock(page, variant_id):
    # Fire the same availability request the page fires, from inside the
    # browser, so it shares the session and the fingerprint of the page load.
    return page.evaluate(
        """async (id) => {
            const res = await fetch(`/api/variants/${id}/availability`, {
                headers: {"accept": "application/json"},
            });
            const data = await res.json();
            return Boolean(data.in_stock);   // the site's own field
        }""",
        variant_id,
    )
```

Why fire it through [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate)
and a real [`fetch`](https://developer.mozilla.org/en-US/docs/Web/API/Window/fetch) rather
than a plain Python request library? Because a request library makes its own connection
with its own handshake and no browser session, and a storefront that sees its inventory
endpoint hit by something that never loaded a page is looking at a request with no
history. The in-page `fetch` is the same call the shopper's browser would make.

Diffing is then trivial: read once, store it, read again later, compare. The whole
tracker is that loop with pacing and an alert on change.

## Restock watch: alert when false flips to true

A restock is a single transition: the variant read `false`, and now it reads `true`.
Everything else is noise you do not act on. The loop below warms the session by loading
the product page once, then polls the one variant on a paced schedule and prints only on
a change:

```python
import time
import random
from invisible_playwright import InvisiblePlaywright

PRODUCT_URL = "https://example.com/p/some-item"
VARIANT_ID = "SKU-42-M"

def read_in_stock(page, variant_id):
    return page.evaluate(
        """async (id) => {
            const res = await fetch(`/api/variants/${id}/availability`, {
                headers: {"accept": "application/json"},
            });
            const data = await res.json();
            return Boolean(data.in_stock);
        }""",
        variant_id,
    )

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto(PRODUCT_URL, wait_until="domcontentloaded")

    last = read_in_stock(page, VARIANT_ID)
    print(f"{VARIANT_ID}: {'in stock' if last else 'out of stock'}")

    while True:
        # Pace the poll. A tight fixed interval is itself the signal.
        time.sleep(random.uniform(60, 120))
        try:
            now = read_in_stock(page, VARIANT_ID)
        except Exception as exc:
            print("poll failed, backing off:", exc)
            time.sleep(300)
            continue

        if now != last:
            event = "RESTOCK" if now else "SOLD OUT"
            print(f"{event}: {VARIANT_ID}")
            # ... send your alert here (webhook, email, queue)
            last = now
```

Because the session is created with `seed=42`, every poll comes from the same machine:
the same GPU, the same fonts, the same screen, the same audio device, roughly 400 fields
that stay put across the whole run. That is the property that matters for a watcher. A
returning shopper who reloads a page they want is one device asking twice; a scraper that
launches a fresh random identity per poll is a hundred different devices all fixated on
one endpoint, which is a far stranger thing to be.

Here is the measurement that makes it concrete. Read the FingerprintJS visitor ID on
poll 1 and again on poll 500 of a seed-fixed session and it is byte-for-byte the same ID
both times - one recognisable returning visitor. Launch a fresh unseeded session per poll
and you get 500 distinct visitor IDs, which is 500 brand-new devices converging on a
single SKU inside a few hours. Same request rate, opposite story. The seed is what keeps
the poll looking like one shopper instead of a crowd.

## The honest caveat: state is not a number, and polling still needs pacing

Two things separate this from tracking a price, and both are easy to get wrong.

**You are tracking a state, not a value.** A price is a number you can sample lazily and
interpolate, so [tracking a product's price](how-to-track-product-prices-playwright.md)
tolerates a much slower poll and a wrong or slightly stale price is a small error. A
stock flag is a boolean whose entire value is the instant it changes, and the window can
be minutes. That pushes
you toward polling more often, which pushes you straight into the second problem.

**A consistent fingerprint does not exempt you from the rate limit you are about to
hit.** This is the caveat to say out loud. The seed keeps every poll looking like the
same machine, and that is necessary, but it is not a licence to poll as fast as you like.
Frequency is its own signal, independent of identity. One machine requesting the same
inventory endpoint every three seconds around the clock is still an obvious watcher, no
matter how real that machine looks. Realness answers "is this a normal browser"; it does
not answer "does a normal shopper refresh one size 1,200 times a day". They do not.

So pace it, and mean it:

- **Poll on a human-plausible interval with jitter**, not a metronome. The loop above
  sleeps a random 60 to 120 seconds; widen that as far as your restock window tolerates.
  [Rate limiting your own scraper properly](how-to-rate-limit-your-scraper-playwright.md)
  is the companion page, and for a stock watcher it is not optional.
- **Back off on the first sign of friction** - a slow response, a challenge, an empty
  body where a JSON payload used to be. Treat those like the failures they are and give
  the endpoint room. [Retrying failed requests with backoff](how-to-retry-failed-requests-playwright.md)
  is the pattern; a stock watcher that hammers harder after a soft block turns a pause
  into a ban.
- **Spread many SKUs across sessions and exits.** Watching one variant is one session.
  Watching two hundred is a scheduling problem, and firing all of them from one identity
  and one IP re-creates the burst you removed at the fingerprint layer.
  [Rotating proxies across your watchers](how-to-rotate-proxies-playwright.md) keeps a
  large watch list from concentrating on one exit.

The stealth layer buys you the right to poll like a returning shopper. It does not buy
you the right to poll like a machine.

## Conclusion

A stock tracker is three moving parts and one discipline. Find the availability XHR,
because the shell HTML does not carry the truth. Read the per-variant boolean and diff
it, because stock is a state and the only event is the flip. Poll on a paced, jittered
schedule from a seed-fixed session, so every read is the same recognisable shopper rather
than a fresh device. The discipline is remembering that a consistent identity and a sane
request rate are two different requirements, and a watcher that gets the first one perfect
and the second one wrong still gets blocked.

## Short answers to the questions that lead here

**Where is the in-stock flag in the page?** Usually not in the HTML at all. The shell
loads, then an XHR fetches availability and paints the button. Find that XHR and read its
payload; parsing the shell is a trap.

**Can I check stock per size or only per product?** Per variant. One product page asks
about every SKU it offers, and each comes back with its own boolean. You track the SKU.

**How do I detect a restock?** Poll the same variant endpoint on a schedule and watch the
boolean. A restock is a single transition from `false` to `true`. Alert on the change, not
on the value.

**How often should I poll?** As rarely as your restock window allows, with jitter, never a
fixed metronome. Frequency is a signal on its own, separate from how real your browser
looks. Start in the minutes and widen from there.

**Does a fixed seed mean I can poll as fast as I want?** No. The seed keeps every poll
looking like one returning shopper, which is necessary but not sufficient. You still hit
the endpoint's rate limit at speed, and a real shopper does not refresh one size hundreds
of times an hour.

**Should I use a plain HTTP client for the poll instead of the browser?** No. Fire the
availability `fetch` from inside the page so it carries the session, cookies and handshake
of a browser that actually loaded the product. A bare request with no page history is the
easier thing to spot.

## Sources

- This project's own [Quickstart](quickstart.md) and [Configuration](configuration.md)
  pages for the real API used above: `InvisiblePlaywright(seed=...)` returns a stock
  Playwright `Browser`, so [`page.on("response")`](https://playwright.dev/python/docs/network#network-events),
  `page.goto` and [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate)
  are the documented Playwright methods, unchanged, and the in-page
  [`fetch`](https://developer.mozilla.org/en-US/docs/Web/API/Window/fetch) call is the
  same standard web API a browser tab would use.
- The seed-reproducibility measurement (identical FingerprintJS visitor ID across every
  poll of one seed versus a distinct ID per unseeded launch) is the same consistency
  property the project's release gates assert: same seed, same machine, every run.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
to locate the availability endpoint, [rate limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
to pace the poll, and [rotating proxies](how-to-rotate-proxies-playwright.md) once your
watch list outgrows one session.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed is what turns 500
polls of one SKU into one returning shopper instead of 500 machines; the pacing is what
keeps that shopper from behaving like none.*
