---
title: "How to scrape concert and tour dates with Playwright"
description: "Scrape concert and tour dates with Playwright: key rows on the event URL so a postponement does not vanish, timestamp every status read, and treat a cancelled badge as its own state instead of an absence."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 134
---


# How to scrape concert and tour dates with Playwright

To scrape concert and tour dates with Playwright, key every row on the event page's
URL instead of its date, stamp each status read with the time you read it, treat a
missing ticket link before the announced on-sale moment as an unopened sale rather
than a broken page, and read the cancelled badge as its own state instead of
inferring cancellation from a show dropping out of a list. Get those four things
right and the rest of the extraction, the artist name, the venue, the price, is the
easy part.

A tour date looks like four fields on the page: artist, venue, date, status. Treat
it as four static fields and the scraper works once and quietly breaks on the
second run. Status turns over faster than anything else in the row and nothing on
the page tells you when it was last true unless you write that down yourself. The
date field itself is not stable either, because a postponed show keeps its page and
swaps the date underneath it. This page walks through the parts of a tour-date
scraper that only show up on a re-run: the timestamp, the re-key, the on-sale
countdown, the cross-partner match, the presale window, and the cancelled badge
that never leaves the list.

| What breaks a tour-date scraper | The fix |
|---|---|
| A sellout read last week looks identical to one read an hour ago | Stamp every status read with a `checked_at` timestamp |
| A postponed show swaps its date but keeps its page | Key rows on the event URL, not on the date |
| No ticket link exists before the on-sale moment | Read the on-sale timestamp; do not treat an empty link as an error |
| Two ticketing partners list the same show with no shared ID | Match on artist, venue and date together, not on an identifier |
| A presale code changes what the page shows | Record which sale window you read, general or presale |
| A cancelled show can stay listed with a badge | Read the badge state; do not infer cancellation from an absence |

## Why a tour-date row needs a fifth field

A status field without a timestamp is a claim you cannot check. "Sold out" read
once tells you the state at that instant and nothing about whether it is still
true an hour, a day, or a week later. Store the moment of the read alongside the
value, not just the value, or every downstream comparison, "is this still
current," "did this change since yesterday," has nothing to compare against.

```python
from datetime import datetime, timezone
from invisible_playwright import InvisiblePlaywright

def read_row(page, url):
    page.goto(url, wait_until="domcontentloaded")
    return {
        "url": url,
        "artist": page.inner_text("[data-testid='artist-name']").strip(),
        "venue": page.inner_text("[data-testid='venue-name']").strip(),
        "date_raw": page.get_attribute("[data-testid='show-date']", "data-date"),
        "status": page.get_attribute("[data-testid='sale-status']", "data-state"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    row = read_row(page, "https://example.com/tour/summer-2026/city-a")
    print(row)
```

Reading the machine-readable `data-state` attribute, not the visible label
matters here for the same reason it matters on a single-event listing: the label
is copy, and copy gets restyled and localized. The state attribute is the site's
own canonical value, and it is what you diff against on the next visit. Once
`checked_at` is on every row, a downstream job can answer "how stale is this" with
a subtraction instead of a guess.

## Key the row on the event URL, not on the date

Postponed and rescheduled shows are the case that quietly deletes rows from a
series if you key on the original date. Many ticketing systems keep the same
event page for a postponed show and simply overwrite the date field on it. If your
storage keys a row as `(artist, venue, original_date)`, the next re-check reads a
different date on the same page, the old key no longer matches anything current,
and the row you already had just falls out of your dataset while the show itself
is still very much alive under a new date.

The event page's URL is almost always the stable part. Key storage on the URL,
and treat a changed `date_raw` on a re-visit as a signal to write, not a reason to
open a new row.

```python
def reconcile(previous_rows_by_url, url, fresh_row):
    """previous_rows_by_url: dict keyed on event URL, from your own storage."""
    prior = previous_rows_by_url.get(url)
    if prior is None:
        return fresh_row | {"event": "new"}

    if prior["date_raw"] != fresh_row["date_raw"]:
        # same page, different date: a postponement, not a new show
        return fresh_row | {
            "event": "rescheduled",
            "previous_date": prior["date_raw"],
        }

    if prior["status"] != fresh_row["status"]:
        return fresh_row | {"event": "status_changed", "previous_status": prior["status"]}

    return fresh_row | {"event": "unchanged"}
```

A series keyed on the date instead of the URL never sees the `rescheduled` branch
at all. It sees one row vanish and, if the postponed show later reappears in a
listing sorted by date, a second row that looks brand new. Both of those are the
same show, and the URL is what proves it.

## An empty ticket link before on-sale is not a broken page

On-sale time is frequently a future timestamp with its own countdown, entirely
separate from the show date. A show can be announced weeks before tickets go on
sale, and the page in that window has no ticket link at all: no price, no buy
button, sometimes nothing but the countdown widget itself. Read that page with a
scraper that expects a purchase link and it looks exactly like an error, a broken
selector, a dead endpoint, when the page is doing precisely what it was built to
do.

The fix is to read the on-sale timestamp explicitly and branch on it, not
treating a missing link as a failure to retry.

```python
from datetime import datetime, timezone

def read_availability(page, url):
    page.goto(url, wait_until="domcontentloaded")
    onsale_iso = page.get_attribute("[data-testid='onsale-countdown']", "data-onsale-utc")
    buy_link = page.query_selector("[data-testid='buy-tickets']")

    if buy_link is None and onsale_iso:
        onsale_at = datetime.fromisoformat(onsale_iso)
        if onsale_at > datetime.now(timezone.utc):
            return {"state": "not_yet_on_sale", "onsale_at": onsale_iso}
        # onsale_at is in the past but no link rendered: this one is worth a retry
        return {"state": "unclear", "onsale_at": onsale_iso}

    if buy_link is None:
        return {"state": "no_link_no_countdown"}

    return {"state": "on_sale"}
```

The distinction that matters is between "nothing is here yet, on purpose" and
"something should be here and is not." Only the second case is worth retrying or
alerting on; the first is just the calendar not having arrived.

## Matching the same show across ticketing partners

Multiple partners commonly list the same show at different prices with different
fee structures, and there is no identifier shared between them: each partner's
event ID is internal to that partner. Matching across sources has to run on the
fields a human would use, artist, venue, and date, normalized enough that spelling
and formatting differences do not split one show into two rows.

```python
import re

def match_key(artist, venue, date_iso):
    def norm(s):
        s = s.casefold().strip()
        return re.sub(r"[^a-z0-9]+", " ", s).strip()

    # date only, no time: partners round or omit showtime differently
    date_only = date_iso[:10]
    return (norm(artist), norm(venue), date_only)

partner_a = {"artist": "The Long Winters", "venue": "The Grand Hall", "date_raw": "2026-09-14T19:00:00"}
partner_b = {"artist": "the long winters", "venue": "Grand Hall, Downtown", "date_raw": "2026-09-14T00:00:00"}

print(match_key(**{k: v for k, v in partner_a.items() if k in ("artist", "venue")}, date_iso=partner_a["date_raw"]))
```

Venue names are the weakest field in that key, because one partner adds a
neighborhood or a legal suffix and another does not; treat an exact match on
artist and date plus a fuzzy match on venue as strong evidence, and hold anything
softer than that for a person to confirm rather than silently merging two
different shows into one price comparison.

## Presale is a different page than general on-sale

A presale code opens an earlier window than the general on-sale, and a page can
show radically different availability depending on whether that code was entered:
a presale session might show tickets available while the same event, read without
a code, shows nothing yet on sale. Neither read is wrong. They are two different,
legitimate states of the same page, and a dataset that mixes them without saying
which one it captured is not comparable to itself from one row to the next.

The concrete rule is to be explicit in the row about which state was read. If the
scraper never submits a presale code, every row it produces is the general
on-sale view, and it is worth recording that as a field, `row["sale_window"] =
"general"`, rather than leaving it implicit, because a downstream consumer has no
other way to know the session never saw the presale window at all.

If a project later adds presale tracking, it needs its own explicit `sale_window`
value and its own re-check cadence, because a presale window opens and closes on
a schedule that has nothing to do with the general sale's own timeline.

## A cancelled show can stay listed with a badge

Cancelled shows sometimes stay on the listing page with a cancelled badge rather
than being removed. A scraper that only checks "is this show still in the list"
misses every one of those, because the show is still there, just marked. Treating
absence from a list page as the sole cancellation signal also produces false
cancellations whenever a listing page is paginated, re-sorted, or temporarily
short a row for reasons that have nothing to do with the show itself.

Read the state on the page directly instead of inferring it from a set
difference between two crawls.

```python
def is_cancelled(page):
    badge = page.query_selector("[data-testid='sale-status'][data-state='cancelled']")
    return badge is not None

def reconcile_with_cancellation(page, url, previous_rows_by_url):
    row = read_row(page, url)
    row["cancelled"] = is_cancelled(page)
    return reconcile(previous_rows_by_url, url, row)
```

A show that disappears from a listing page and does not resolve when its own URL
is visited directly is a much stronger cancellation signal than a missing row on
its own. Visiting the event page and reading its badge before marking anything
cancelled avoids the false positives a set difference alone produces.

## Running the re-check as one recurring job, not a fresh scrape

Every fix above assumes the scraper runs more than once against the same list of
event URLs and remembers what it saw last time. That is a different shape from a
one-off crawl: instead of writing rows, the job writes events, `new`,
`rescheduled`, `status_changed`, `unchanged`, `cancelled`, each stamped with
`checked_at`, and the current state is just the latest event per URL rather than
the only thing kept.

```python
def run_recheck(urls, previous_rows_by_url):
    events = []
    with InvisiblePlaywright(seed=42) as browser:
        page = browser.new_page()
        for url in urls:
            row = read_row(page, url)
            row["cancelled"] = is_cancelled(page)
            events.append(reconcile(previous_rows_by_url, url, row))
    return events
```

The output of one run becomes the `previous_rows_by_url` input to the next, and
the event log itself is the useful artifact: it is what lets a later question,
"when did this show go from on sale to sold out," get answered by reading history
instead of being asked of a page that has already moved on.
[Scheduling scrapes with cron](schedule-invisible-playwright-scrapes-with-cron.md)
covers running this on a recurring interval instead of by hand.

## Conclusion

A tour-date row is not four static fields, it is a small state machine, and the
part that trips up a first pass is always the part that changes after the row was
first written. Stamp every status read with the time you read it so a stale
sellout and a current one are distinguishable. Key rows on the event URL so a
postponed show does not vanish when its date field changes underneath it. Read
the on-sale timestamp explicitly so an unopened sale does not look like a broken
page. Match shows across partners on artist, venue and date together, because no
shared identifier exists. Record which sale window a row came from, general or
presale, and read the cancelled badge directly instead of trusting an absence.
None of this is exotic scraping; it is ordinary extraction plus the bookkeeping
that survives a second run.

## Short answers to the questions that lead here

**Why does my sellout status look wrong sometimes?** It is probably not wrong,
it is old. Store the time you read the status alongside the value, so a stale
read and a current one are distinguishable instead of looking identical.

**A show I was tracking disappeared from my dataset. Where did it go?** Check
whether it was postponed. Many event pages keep the same URL and swap the date
field, so a row keyed on the original date stops matching and looks gone, while
the show is still live under a new date on the same page.

**Why does a newly announced show have no ticket link at all?** On-sale time is
often a separate future timestamp with its own countdown. Before that moment
there is no link to find, which is not the same as a broken page. Read the
on-sale timestamp and branch on it instead of retrying blindly.

**How do I compare prices for the same show across two ticketing partners?**
Match on artist, venue and date together, normalized for case and punctuation.
There is no identifier shared between partners, so the match has to run on the
fields a person would use.

**Why does the same show look sold out on one visit and available on
another?** A presale code opens an earlier window with different availability
than the general on-sale. Record which window your scraper actually read; do
not assume every row represents the same state.

**A show is missing from the listing page. Is it cancelled?** Not necessarily.
Visit the event page directly and read its status badge. Some cancelled shows
stay listed with a cancelled state rather than being removed, and some absences
are just pagination.

## Sources

- Playwright's own [`get_attribute`](https://playwright.dev/python/docs/api/class-page#page-get-attribute)
  and [`query_selector`](https://playwright.dev/python/docs/api/class-page#page-query-selector),
  used exactly as documented upstream to read machine-readable state rather than
  rendered labels; the browser returned by this library is a real Playwright
  `Browser`.
- This project's own configuration notes on reading state attributes over
  visible text for status fields, the same approach used for single-event
  availability tracking.
- Retrieved 2026-08-28.

**See also:** [scraping event and ticket listings](how-to-scrape-event-and-ticket-listings-playwright.md)
for the availability XHR and calendar-widget mechanics behind a single show's
page, [tracking product stock](how-to-track-product-stock-playwright.md) for the
same diff-and-timestamp shape applied to a boolean instead of a status enum,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading a countdown or availability payload directly instead of the rendered
page, [cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
for turning the raw date and price strings into typed values, and
[scheduling scrapes with cron](schedule-invisible-playwright-scrapes-with-cron.md)
for running the re-check on a recurring cadence instead of by hand.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The first version
of this scraper keyed rows on artist, venue and date; a postponed show swapped
its date field on the same page and the row silently dropped out of a series
that was keyed on the date I no longer had.*
