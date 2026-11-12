---
title: "How to scrape auction listings with Playwright"
description: "Scrape auction listings with Playwright: stamp every read in UTC, take the server end timestamp instead of the rendered countdown, and read bid history from its own paginated call."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 101
---


# How to scrape auction listings with Playwright

**To scrape auction listings with Playwright, treat every row as a timed observation rather
than a record: read the volatile fields in one `page.evaluate` so they share a single
instant, stamp that instant in UTC, take the end time from the server timestamp behind the
countdown rather than the string it renders, pull bid history from its own paginated call,
and decide sold, unsold, reserve-not-met and no-bids from the response, not the badge.**

An auction page is the one listing shape that changes while you are reading it. A product
page holds still for a visit. An auction row can gain a bid between the moment you read the
price and the moment you read the bid count, and the time remaining is already wrong when you
write it down. Nothing in the markup tells you the three fields were sampled seconds apart.

That property decides the whole design. This page builds the row shape that survives it: one
atomic read per pass, a UTC stamp on every row, the end time as an absolute value rather than
a countdown, bid history walked as its own paginated resource, and the closing states taken
from the response, because the DOM collapses four outcomes into two badges.

## An auction row is a reading, not a record

Read the volatile fields in one round trip or they will not agree with each other. Three
separate locator calls are three separate moments in real time, and on a listing closing in
four minutes that is enough for the bid to move between the first call and the third. The row
you store then describes a state the auction never held.

One `page.evaluate` returning one object fixes it. Everything inside that callback runs as one
task on the page's main thread, so no bid can land in the middle of it. Stamp the clock in
Python immediately before the call and carry that stamp on the row.

```python
from datetime import datetime, timezone
from invisible_playwright import InvisiblePlaywright

READ_ROW = """
(sel) => {
  const root = document.querySelector(sel.root);
  if (!root) return null;
  const pick = (query, attr) => {
    const node = root.querySelector(query);
    if (!node) return null;
    return attr ? node.getAttribute(attr) : node.textContent.trim();
  };
  return {
    current_bid_text: pick(sel.bid),
    bid_count_text: pick(sel.bid_count),
    status_text: pick(sel.status),
    end_attr: pick(sel.end_time, "datetime"),
  };
}
"""

def read_listing(page, listing_id, sel):
    observed_at = datetime.now(timezone.utc)   # the stamp belongs to the read
    snapshot = page.evaluate(READ_ROW, sel)
    if snapshot is None:
        return None
    return {"listing_id": listing_id,
            "observed_at": observed_at.isoformat(),
            **snapshot}
```

A row without `observed_at` is not a weaker row, it is an unusable one. "Current bid 240" is a
claim about a moment. Without the moment you cannot order two readings, cannot compute a bid
rate, and cannot tell a stale row from a fresh one. Store the stamp in UTC and convert on the
way out.

## Scrape the end timestamp, not the countdown

The countdown you see is not data, it is a rendering. A script receives an absolute end time
from the server once, then subtracts the browser's own clock from it on a timer. Two things
follow: the string is only as correct as the client clock, and it means nothing tomorrow,
because "2h 14m left" does not survive being written to a file.

The absolute value is almost always reachable. It sits in a `datetime` attribute on a `<time>`
element, in a `data-end` attribute, or in the state blob the countdown script reads at
startup. Take that, normalise to UTC, and derive the remaining seconds against the timestamp
already stamped on the read.

```python
def parse_end_time(page, snapshot):
    raw = snapshot.get("end_attr")
    if not raw:
        # the same value the countdown script itself initialises from
        raw = page.evaluate(
            "() => (window.__STATE__ && window.__STATE__.listing || {}).endsAt || null"
        )
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw > 10000000000 else raw   # ms or s epoch
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)

def with_timing(row, ends_at):
    observed = datetime.fromisoformat(row["observed_at"])
    row["ends_at"] = ends_at.isoformat() if ends_at else None
    row["seconds_remaining"] = (ends_at - observed).total_seconds() if ends_at else None
    return row
```

Now `seconds_remaining` is a number derived from two timestamps you control, not a parsed
string. Two readings of the same listing become comparable, and a row read yesterday still
says how close to the end it was. The rules for typing the bid amount and any relative dates,
including why the browser's own locale is the safest formatter to read against, are in
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md).

## Bid history is a separate, separately paginated call

The listing page shows a bid count. The bid history is a different resource, fetched by its own
request when the history panel opens, and it carries a cursor of its own. Scrolling the listing
does not advance it, and a listing showing 60 bids will hand you 20 of them and a pointer.

Catch the request rather than the panel it paints, then follow its cursor with `page.request`.
That goes out on the browser context, so the cookies, headers and proxy of the session come
with it, unlike a bare HTTP client started on the side.

```python
def read_bid_history(page, max_pages=50):
    with page.expect_response(
        lambda r: "/bids" in r.url and r.request.resource_type in ("xhr", "fetch")
    ) as caught:
        page.get_by_role("button", name="Bid history").click()

    payload = caught.value.json()
    rows = list(payload.get("bids", []))

    for _ in range(max_pages):
        cursor = payload.get("next")     # the history's own cursor, not the listing's
        if not cursor:
            break
        payload = page.request.get(cursor).json()
        rows.extend(payload.get("bids", []))

    return rows
```

Each history entry carries its own server-side timestamp, and that is the one piece of auction
data which is not a moving target. A bid placed at 14:02:11 stays there. The history is the
reliable spine of the dataset; the polled rows are the approximation around it. The response
hooks are covered on their own in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md), and the
cursor walk is the same shape as any other
[paginated resource](how-to-scrape-paginated-pages-playwright.md).

## Reserve not met and no bids look the same in the DOM

These two states render almost identically. Both show a price with no winning highlight and the
same muted styling, and on plenty of templates the only visible difference is a line of text
that is simply absent when the listing has no reserve. Classify from the DOM and the two
collapse into one bucket, which ruins any analysis of which starting prices attract bidders.

The response separates them cleanly, and the trap is in how you test the field. `reserve_met`
is frequently absent on a no-reserve listing, so `if not listing.get("reserve_met")` folds
three states into one branch: reserve unmet, no reserve at all, and field missing from this
response shape.

```python
def classify_live(listing):
    bids = listing.get("bid_count") or 0
    reserve_met = listing.get("reserve_met")     # often absent when there is no reserve

    if bids == 0:
        return "no_bids"
    if reserve_met is False:                     # not "is falsy"
        return "reserve_not_met"
    return "bidding"
```

The distinction matters more than it looks. A listing with no bids has had no market response
at all, while a listing at reserve-not-met has an active market that disagrees with the
seller's floor. Those are opposite facts and they arrive down the same pipe.

## Order the crawl by end time, and know that changes the dataset

A crawl ordered by listing id and a crawl ordered by end time are two different datasets, not
one job run two ways. The first is a census: broad coverage, each listing seen a few times, the
close missed entirely. The second is a study of the last hour, where the bid count, the price
and the reserve state all move at once.

Pick deliberately, then poll on a schedule that tracks time remaining rather than a flat
interval. A listing four days out does not need reading every ten minutes; one eight minutes
out does.

```python
from datetime import timedelta

def due_at(ends_at, now):
    remaining = (ends_at - now).total_seconds()
    if remaining <= 0:
        return None                                  # closed: one final read, then stop
    if remaining < 900:
        return now + timedelta(seconds=60)
    if remaining < 6 * 3600:
        return now + timedelta(minutes=15)
    return now + timedelta(hours=6)

def next_batch(queue, now, limit=40):
    ready = [item for item in queue if item["due_at"] <= now]
    ready.sort(key=lambda item: item["ends_at"])     # end time, not listing id
    return ready[:limit]
```

The `limit` is doing real work. Every listing in a closing cohort comes due within the same few
minutes, so an unbounded queue becomes a burst against one host at the busiest moment on the
site. Cap the batch and hold a floor between requests; why request velocity is a scored signal
rather than a courtesy is in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

## Sold and unsold both become "ended"

At close the badge stops being informative. A listing that sold at 400 and a listing that drew
twelve bids without clearing its reserve can carry the same "Ended" label, in the same grey
pill, with the final bid shown the same way. Reading the badge produces a dataset in which
every closed auction looks successful.

Take the outcome from the response, in order of how directly each field states it: an explicit
sold flag first, a winning bidder id second, and only then the reserve and bid count to name
the two unsold reasons. When none of them is present, record that.

```python
def classify_closed(listing):
    if listing.get("sold") is True:
        return "sold"
    if listing.get("winning_bidder_id"):
        return "sold"

    bids = listing.get("bid_count") or 0
    if bids == 0:
        return "unsold_no_bids"
    if listing.get("reserve_met") is False:
        return "unsold_reserve_not_met"
    return "unknown"          # write it down as unknown rather than guessing "sold"
```

That `unknown` branch is the point of the function. A guess is indistinguishable from a
measurement once it is in the database, and sell-through rate is usually the number the whole
scrape exists to produce. One row that guesses wrong is a rounding error; a rule that guesses
wrong is a biased dataset.

## Store observations, never an update in place

Write an append-only observation table keyed on listing id plus `observed_at`, and keep the
stable facts in a second table upserted on listing id alone. Title, seller, currency and end
time go in the second. Current bid, bid count and status go in the first, once per pass,
forever.

Updating the listing row in place feels tidy and destroys the only thing an auction crawl can
uniquely produce. The final price is public afterwards; anyone can read it. When the first bid
landed, how long the listing sat at its opening price, whether the reserve cleared early or in
the last minute: that exists only in readings you kept. The append-versus-upsert split is
worked through in [scraping into a SQLite database](how-to-scrape-into-a-database-playwright.md),
and the high-water mark for picking up new listings between runs is in
[incremental scraping](how-to-scrape-only-new-items-incremental-playwright.md).

## Where this approach stops

It does not get you the closing sequence. The decisive bids on a contested listing land in the
final seconds, and no polling interval you can defend against a rate limiter samples that
window. Treat the last polled row as the final observation before close, and let the read taken
after the end time decide the outcome. Anything in between is reconstruction, and the schema
should say so.

Two other limits are worth stating plainly. Bid history behind a login is an authorisation
boundary, not a detection one, and no amount of fingerprint work opens it. If the ending-soon
sweep starts drawing challenges, that is a velocity answer to a velocity question: this library
does not solve captchas, and the remedy is a wider window and a smaller batch.

## Conclusion

Auction data is a time series wearing the costume of a listing. The fixes are one fix applied in
different places: read the volatile fields together so they share an instant, stamp that instant
in UTC, take the absolute end time instead of the string a script derived from it, and pull the
closing states from the response, because the badge cannot tell four outcomes apart. Then choose
the crawl order on purpose, and keep every reading instead of overwriting yesterday's. The
parsing here is not hard. Knowing exactly when each number was true is the entire job.

## Short answers to the questions that lead here

**Why does my row have a bid count that does not match the bid?** Because the two fields were
read by separate locator calls, seconds apart, on a page that changed in between. Read them in
one `page.evaluate` returning a single object, and stamp that read in UTC.

**Should I parse "2h 14m left"?** No. A script paints that string from an absolute server end
time minus the browser's clock, so it depends on your machine and means nothing once stored.
Read the end timestamp, keep it as UTC, and compute the remaining seconds against your own read
time.

**Where is the bid history?** Behind its own request, usually fired when the history panel
opens, with a cursor the listing page does not advance. Catch the response and follow that
cursor with `page.request`, so the follow-up pages keep the session's cookies and proxy.

**How do I tell reserve-not-met from no bids?** Not from the DOM, where they look nearly
identical. Read `bid_count` and test `reserve_met is False` explicitly, because the field is
often missing on no-reserve listings and a falsy check merges three states.

**How do I tell a sold listing from an unsold one after it closes?** Both show an "Ended" badge.
Use the response: a sold flag or a winning bidder id first, then bid count and reserve to name
the unsold reason. When nothing states it, store "unknown" instead of assuming sold.

**Does it matter whether I crawl by listing id or by end time?** It decides which dataset you
get. By id you get a broad census that misses the close; by end time you get the closing
behaviour of a much smaller set. Cap the batch either way, since a closing cohort all comes due
at once.

## Sources

- Playwright's [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate),
  which runs its callback as one task on the page, so the fields it returns share one instant.
  Retrieved 2026-08-28.
- Playwright's [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  for catching the bid-history call as it is fired. Retrieved 2026-08-28.
- Playwright's [`page.request`](https://playwright.dev/python/docs/api/class-page#page-request)
  and the [APIRequestContext](https://playwright.dev/python/docs/api/class-apirequestcontext)
  behind it, which issue requests on the browser context and inherit its cookies, headers and
  proxy. Retrieved 2026-08-28.
- This project's own behaviour: the browser the library returns is a real Playwright `Browser`,
  so every call above is upstream Playwright as documented.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the bid-history request, [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for typing the timestamp and the bid amount,
[scraping into a SQLite database](how-to-scrape-into-a-database-playwright.md) for the
append-versus-upsert split, and
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md) for pacing the
ending-soon batch.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The first version of this crawler
updated one row per listing in place, looked correct for a week, and then could not answer when
the bidding had accelerated, because every earlier reading was already overwritten.*
