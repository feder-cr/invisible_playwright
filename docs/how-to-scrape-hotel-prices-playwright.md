---
title: "How to scrape hotel room prices with Playwright"
description: "Scrape hotel room prices with Playwright by driving the date and occupancy form, then reading the rate XHR per search: rates do not exist until you query."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 29
---


# How to scrape hotel room prices with Playwright

The first thing to understand about hotel room prices is that there is no price on the
page to scrape. Open a property page with no dates chosen and there is nothing to read:
a per-night rate does not exist until you tell the server who is staying and when. The
rate is a function of the form state, computed on the server per query and delivered by a
background call, not a static field sitting in the HTML waiting for a selector.

That single fact decides the whole approach. You are not extracting a value, you are
running a search, waiting for its answer, and running the next one. This page shows how
to drive the date and occupancy form, how to read the rate response the page fetches
instead of the rendered box, how to iterate date windows across one property, and one
honest caveat about what "reproducible" does and does not buy you here.

## Why there is no price until you run a search

A retail product page usually carries its price in the markup, so a single request and a
selector are enough. A room rate does not work that way. The same room is a different
price for one night versus three, for two guests versus four, for this weekend versus the
one after, and the server decides that number when you ask, not before.

So the page ships you a form, not a number: a calendar widget for check-in and check-out,
and an occupancy control for guests and rooms. Nothing quotes a rate until all of those
have a value. When they do, the frontend sends a query to a rate endpoint and paints the
JSON that comes back. Parse the painted box and you are parsing a rendering of a response
you could have read directly, which is [why capturing the XHR beats reading the rendered
HTML](how-to-capture-xhr-api-responses-playwright.md): the response is typed, it usually
carries the full rate breakdown the box only summarizes, and it does not rot when the
markup changes.

The practical consequence: a rate scan is many searches. The query count is the product
of every dimension you vary, so a modest scan of one property is already dozens of
distinct searches you want from one browser session:

| Search dimension | Example range | Values |
|---|---|---|
| Arrival date | one week of check-ins | 7 |
| Length of stay | two options | 2 |
| Occupancy | two settings | 2 |
| **Total distinct queries** | | **28** |

That is the part that needs a real browser and a stable identity, for reasons the last
two sections get to.

## Drive the date and occupancy form

You cannot skip the form by guessing the endpoint's query string, because the calendar
and occupancy widgets carry state the endpoint reads (session tokens, a normalized date
format, an availability precheck) that only exists once you have actually operated them.
Drive the widget the way a person does.

The launch is the usual two-line change from stock Playwright, and everything after it is
the ordinary Playwright API, because the `browser` you get back is a real Playwright
`Browser`:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/property/12345", wait_until="domcontentloaded")

    # open the calendar, pick check-in then check-out
    page.click("[data-testid='date-field']")
    page.click("[data-date='2026-09-14']")
    page.click("[data-date='2026-09-17']")

    # set occupancy: 2 guests, 1 room
    page.click("[data-testid='occupancy-field']")
    page.click("[data-testid='guests-increment']")   # 1 -> 2
    page.click("[data-testid='search']")
```

Two details matter more than they look. First, click the calendar days, do not type into
a read-only date input: many pickers ignore a value you set directly and only commit the
range on a real click, so a typed date produces a search that silently uses today. Second,
the mouse arriving at each day on a curved path rather than teleporting is not decoration
here. A rate scan fires the same occupancy widget dozens of times in a session, and a
control that is always clicked at the exact same pixel with zero travel between clicks is
one of the cheapest interaction signals a site can watch for.

## Wait on the rate XHR, not the rendered page

Once the search is submitted, the rate arrives asynchronously. The moment to read it is
when its response lands, not after a fixed sleep and not on a generic load event, because
the surrounding page finished loading long before the rate did. Wait for the specific
response:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/property/12345", wait_until="domcontentloaded")

    # arm the wait BEFORE the click that triggers the request
    with page.expect_response(
        lambda r: "/api/rates" in r.url and r.status == 200
    ) as rate_info:
        page.click("[data-testid='date-field']")
        page.click("[data-date='2026-09-14']")
        page.click("[data-date='2026-09-17']")
        page.click("[data-testid='search']")

    rates = rate_info.value.json()
    print(rates["nightly"], rates["currency"])
```

Arming [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response) before the click that causes it is the part people get wrong: if
you click first and wait second, the response can arrive in the gap and you wait forever.
Keying the predicate on both the URL fragment and `status == 200` matters too, because a
rate call that comes back empty or with a non-200 is exactly the failure mode the next
section is about, and you want to see it as a failure rather than silently read an empty
body. For the general version of this timing problem, see [how to wait for the right
signal instead of a fixed delay](how-to-wait-for-page-load-playwright.md).

## Iterate date windows across a property

A single search is a warm-up. The real job loops arrival dates and occupancy settings and
collects the quote for each, all inside one session so you are not paying the cost of a
fresh browser per query:

```python
from datetime import date, timedelta
from invisible_playwright import InvisiblePlaywright

def scan(browser, property_id, start, nights, occupancies, days=7):
    page = browser.new_page()
    page.goto(f"https://example.com/property/{property_id}",
              wait_until="domcontentloaded")
    out = []
    for offset in range(days):
        check_in = start + timedelta(days=offset)
        check_out = check_in + timedelta(days=nights)
        for guests in occupancies:
            with page.expect_response(
                lambda r: "/api/rates" in r.url and r.status == 200
            ) as info:
                set_dates(page, check_in, check_out)
                set_occupancy(page, guests)
                page.click("[data-testid='search']")
            body = info.value.json()
            out.append({
                "check_in": check_in.isoformat(),
                "nights": nights,
                "guests": guests,
                "nightly": body.get("nightly"),
                "currency": body.get("currency"),
            })
    page.close()
    return out

with InvisiblePlaywright(seed=42) as browser:
    rows = scan(browser, "12345", date(2026, 9, 14), nights=3, occupancies=[2, 4])
    for r in rows:
        print(r)
```

Two operational rules keep a loop like this returning real numbers. Space the searches
out rather than firing them back to back: dozens of identical-shaped queries per minute
from one session is itself the signal, and a deliberate pause between queries is the
difference between a scan that keeps quoting and one that starts returning blanks. The
mechanics of doing that without turning it into its own tell are in [how to rate-limit
your scraper](how-to-rate-limit-your-scraper-playwright.md). And remember that the same
room is priced in the currency and region the session appears to be in, so a scan meant to
compare markets has to control the exit, which is the subject of [scraping geotargeted
content](how-to-scrape-geotargeted-content-playwright.md).

## Why one fingerprint has to carry the whole scan

One fingerprint has to carry the whole scan because, from the server's side, a rate scan
is dozens of near-identical searches from one visitor in a short window: same property,
same device, dates marching forward one day at a time. That is a legitimate thing for a person
comparing dates to do, and a rate endpoint will keep answering it as long as the visitor
stays coherent. The failure mode is the tenth identical-looking search of the minute
coming back blank, not because you were "detected" in the dramatic sense, but because
something about the session stopped being consistent partway through.

This is where the browser having one stable machine underneath matters more than any
single stealth property. If the GPU string, the fonts, the audio device, the screen and
the timezone are one coherent identity for the whole scan, the sequence reads as one
person browsing dates. If those drift, or if two spoofing layers disagree so the identity
subtly changes between the third search and the ninth, the session starts contradicting
itself and the rate endpoint has every reason to stop quoting. `invisible_playwright`
derives all of those fields from one seed, so `seed=42` is the same machine on query one
and query fifty. If you want to hold a specific field constant across a fleet of scanners
while leaving the rest seed-derived, [pinning covers forcing individual
fields](pinning.md) without breaking the correlations a detector cross-checks.

The measurable version of this: with the identity fixed, a canvas or audio hash read
twice in one session comes back byte-identical, and a detector that logs suppression by
name has nothing to log. A scan that runs on a browser whose surfaces shift per call
produces a different hash each read, which is the cheapest inconsistency signal there is
and precisely what makes a long single-session scan stop working.

## The honest caveat: reproducible browser, volatile prices

The seed makes the browser reproducible. It does not make the price reproducible, and it
is important not to confuse the two.

Room rates are computed per query against inventory, demand and yield rules that move on
their own schedule. Run the exact same search, same property, same dates, same occupancy,
twice an hour apart, and the two nightly rates can legitimately differ. That is not your
scraper being non-deterministic and it is not a fingerprint problem: it is the actual
answer changing, because the thing you are measuring is a live price and not a static
attribute. A room that was 180 at 09:00 and 195 at 10:00 was correctly quoted both times.

So treat every row you collect as a timestamped observation, not a fact. Record the wall
clock next to `check_in` and `guests`, and when you compare two runs, compare like a
price historian rather than a test harness: a difference is data about the market, not a
bug in the scan. The seed guarantees that if the number moves, the browser was not what
moved. That is exactly the property you want, because it lets you attribute every change
to the server instead of wondering whether your own identity drifted mid-run.

## Conclusion

Scraping room rates is not extraction, it is search automation. There is no price until a
check-in, a check-out and an occupancy have been driven through the real widgets; the
answer arrives as an XHR you should read directly rather than scrape off the rendered box;
and a useful scan is many such searches from one session. That last point is why the work
wants a real browser holding one coherent identity for the whole run: the sequence has to
read as one person comparing dates, or the rate endpoint stops quoting. Fix the identity
with a seed, wait on the specific response, space the queries out, and timestamp every row,
because the browser is the only reproducible half of the system and the price is meant to
move.

## Short answers to the questions that lead here

**Why is there no price in the page HTML?** Because a room rate does not exist until you
submit dates and occupancy. The server computes it per query and returns it over a
background call, so the price is a function of the form state, not a field in the markup.

**Can I skip the browser and call the rate endpoint directly?** Usually not reliably. The
endpoint reads state the calendar and occupancy widgets set up (session tokens, a
normalized date format, an availability precheck), so a hand-built query string tends to
be rejected or answered with defaults.

**Why does the same search return two different prices?** Because it is a live price.
Rates move with demand and yield rules on the server's schedule, so two identical searches
minutes apart can both be correct and still differ. Timestamp every observation.

**Why do the later searches in my loop come back empty?** A long run of near-identical
searches per minute from a session whose identity drifts stops reading as one coherent
visitor. Keep one stable fingerprint for the whole scan and space the queries out.

**Does the seed make the prices reproducible?** No. It makes the browser reproducible, so
you can attribute any change in a rate to the server rather than to your own identity. The
pricing is not yours to make deterministic.

**How do I read the rate instead of scraping the box?** Arm `page.expect_response` on the
rate call before the click that triggers it, then read `.json()`. It gives you the full
breakdown the rendered box only summarizes.

## Sources

- This project's own measurements on long single-session search loops, where a coherent
  seed-derived identity kept a rate endpoint quoting across dozens of queries while a
  drifting one did not.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-wait-for-response)
  and [`page.on`](https://playwright.dev/python/docs/network#network-events) APIs, read
  from Playwright's own documentation rather than a rendered example, and exercised
  unchanged because the wrapper returns a real Playwright `Browser`.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the rate call directly, and [rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for spacing the searches without creating a new tell.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The rule that a rate scan
lives or dies on one coherent identity is something the search loops taught us, not a
slogan.*
