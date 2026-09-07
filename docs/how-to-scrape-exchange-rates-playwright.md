---
title: "How to scrape currency exchange rates with Playwright"
description: "Scrape currency exchange rates with Playwright: capture the quote response instead of the animating node, and store base, quote, side, amount and a UTC arrival time in every row."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 108
---


# How to scrape currency exchange rates with Playwright

**To scrape currency exchange rates with Playwright, capture the quote response the
widget already fetches instead of reading the number it paints, and write a row
carrying the base code, the quote code, the side, the amount and a UTC arrival
timestamp, because a bare "1.08" names one currency out of two, states no direction and
hides which side of the spread produced it.**

Rate pages look like the easiest data on the web. One number, big type, updates on its
own. That is why rate datasets go wrong more often than harder-looking ones: the
extraction succeeds, the number is correct, and the column is unusable three weeks later
because nobody wrote down what it was a rate of.

Every problem below is a semantics problem, not a scraping problem. The row shape decides
whether the series is worth keeping, and it has to be chosen before the first poll.

## A rate is a pair, a direction, a side and a moment

"EUR 1.08" is not a rate. It is one currency code and a float. A rate needs the second
currency, the direction between them, the side of the spread it came from, and the
instant it was quoted. Drop any of the four and the value cannot be compared to
anything, including the same page an hour later.

Direction is the one people assume is obvious. It is not. One EUR in USD and one USD in
EUR are both "the EUR/USD rate" in ordinary speech, and a page showing both in adjacent
cells hands you either one depending on which selector matched. Fix the convention in
the schema and never vary it. Store codes, never the glyph, because one symbol serves
several currencies.

```python
from decimal import Decimal
from datetime import datetime, timezone

def rate_row(base, quote, side, raw, amount=None, amount_in=None,
             observed_utc=None, quoted_utc=None, source_kind="consumer"):
    """One observation, one direction, one side. Never a bare number."""
    return {
        "base": base.upper(),        # ISO 4217 code of the currency being priced
        "quote": quote.upper(),      # ISO 4217 code the price is expressed in
        "side": side,                # "mid" | "bid" | "ask", from the quoting party
        # units of quote per 1 base, always. Comma-decimal pages need the swap.
        "rate": Decimal(raw.replace(",", "")),
        "raw": raw,                  # the string the page served, kept verbatim
        "amount": amount,            # the amount this quote was given for
        "amount_in": amount_in,      # "base" or "quote": the amount has a side too
        "source_kind": source_kind,  # "reference" or "consumer"
        "observed_utc": observed_utc or datetime.now(timezone.utc).isoformat(),
        "quoted_utc": quoted_utc,    # the feed's own timestamp, when it ships one
    }
```

`rate` means units of quote per one base, in every row, forever. A `Decimal` keeps the
low digits that pairs with small units depend on, and the raw string beside it means a
parsing mistake stays recoverable instead of baked in.

## Bid, ask and mid are three numbers on one page

A rate page shows at least two prices and usually three. The mid sits between them, and
the gap between the outer two is not noise: it is the product being sold. Capturing
whichever your selector reached first and calling that column "rate" mixes two
quantities into one series.

The labels are worse than ambiguous, they are reversed depending on who is speaking. "We
buy" on a provider's page is the price that provider pays you, which is the price you
get when you sell. Map the page's wording onto bid and ask at capture time, defined
against the quoting party, and the wording stops mattering.

```python
def rows_from_quote(payload, base, quote, observed_utc):
    """One payload, up to three rows. Do not collapse them into one column."""
    rows = []
    for side, key in (("mid", "mid"), ("bid", "bid"), ("ask", "ask")):
        raw = payload.get(key)
        if raw is None:
            continue
        rows.append(rate_row(base, quote, side, str(raw),
                             observed_utc=observed_utc,
                             quoted_utc=payload.get("ts")))
    return rows
```

Two derivations look reasonable and are wrong. The reciprocal of the bid is not the ask,
because the spread widens outward from the mid on both sides, so inverting one side
produces a price nobody quoted. The average of a provider's two sides is not a reference
mid either, because the margins are rarely symmetric. If you need the mid, capture the
mid.

## A consumer quote already carries a margin

A reference rate and a consumer quote print in the same format and are different
quantities. The reference number is roughly what the interbank market is doing. The
consumer number is that plus the provider's margin plus whatever the corridor and payout
method cost. Subtract one from the other and you get a margin, a spread and a time gap
added together, which answers no question you had.

That is why `source_kind` is a column. One field stops a later join from comparing two
things that were never comparable. Two consumer quotes do not compare either, unless
corridor, amount and payout method match.

The display holds one more trap. Many converters show a headline rate and a fee on a
separate line, so the effective rate is the amount out divided by the amount in, and
that ratio does not equal the number in large type. Capture both amounts and derive it
yourself. Getting glyphs and separators off those strings is [its own careful
step](how-to-clean-scraped-prices-and-dates-playwright.md).

## Read the quote response, not the animating node

The number on screen is a frame of an animation. The widget holds a socket open or polls
every few seconds, and many tween the digits between the old value and the new one, so a
read landing mid-transition returns a number the server never sent. Without a tween you
still get whatever survived long enough to paint.

Read the response instead. Playwright hands you the same bytes the page's own JavaScript
received, on [`response`](https://playwright.dev/python/docs/network) for a polling
endpoint and on `framereceived` for a socket. The arrival stamp belongs on the first line
of the handler, not wherever the row eventually gets written. The general form of this
subscription is in [capturing XHR and API
responses](how-to-capture-xhr-api-responses-playwright.md).

```python
import json
from datetime import datetime, timezone
from invisible_playwright import InvisiblePlaywright

observations = []

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_response(response):
        if response.request.resource_type not in ("xhr", "fetch"):
            return
        if "/rates" not in response.url:
            return
        observed = stamp()                            # first line, before parsing
        server_date = response.header_value("date")   # the server's own clock
        cached_age = response.header_value("age")     # non-zero means it is not fresh
        try:
            payload = response.json()
        except Exception:
            return
        for row in rows_from_quote(payload, "EUR", "USD", observed):
            row["server_date"] = server_date
            row["cached_age"] = cached_age
            observations.append(row)

    def on_websocket(ws):
        def on_frame(payload):
            observed = stamp()
            try:
                msg = json.loads(payload)
            except (TypeError, ValueError):
                return
            observations.extend(rows_from_quote(msg, "EUR", "USD", observed))
        ws.on("framereceived", on_frame)

    page.on("response", on_response)
    page.on("websocket", on_websocket)
    page.goto("https://example.com/converter")
    page.wait_for_timeout(60_000)
```

Both handlers are registered because a widget's transport is not visible from outside,
and some use a socket for the ticker and a request for the converter. Log raw payloads
before writing the parser: heartbeats share that socket with the quotes.

## Three clocks, and your write time is none of them

The row carries three timestamps, and none is when it reached your database. That fourth
time includes your queue, your batch interval and any retry, and it is the one people
store because it is easiest to reach.

The three that matter are ordered. The feed's own `quoted_utc` is when the price was
made, and it is the only one that belongs on a time axis. The HTTP `Date` header is the
server clock at response time, at one second of resolution, so it brackets, not
measures. Your arrival stamp is when the bytes reached the browser, and it is the only
one you can always produce.

The `Age` header is the cheap check nobody makes. A non-zero value means a cache served
that response, so the quote was already that many seconds old when your arrival stamp was
taken. Read it once and the "our rates lag the market" question answers itself.

## The amount changes the rate, so the amount is part of the key

Consumer converters tier their pricing. Type one hundred and you get one rate; type one
hundred thousand and the margin narrows and the quoted rate improves. Both are real,
neither is wrong, and a series that mixes them without recording the amount is a set of
unrelated observations sharing a column.

The amount is an input, and it has a direction: sending a fixed amount of the base and
receiving a fixed amount of the quote are different questions with different
answers. Sweep a fixed ladder so rows stay comparable across days, and wait on the
refetch rather than a timeout, because the field debounces before it fires: the same wait
any [search form that refetches on
change](how-to-scrape-search-results-form-playwright.md) needs.

```python
AMOUNT_LADDER = ["100", "1000", "10000", "100000"]

def quotes_by_amount(page, base, quote):
    rows = []
    field = page.get_by_role("textbox", name="Amount")
    for amount in AMOUNT_LADDER:
        with page.expect_response(
            lambda r: "/rates" in r.url and r.request.resource_type in ("xhr", "fetch")
        ) as caught:
            field.fill(amount)          # the debounce fires the refetch for us
        observed = stamp()
        for row in rows_from_quote(caught.value.json(), base, quote, observed):
            row["amount"] = amount
            row["amount_in"] = "base"   # this widget's field is the send side
            rows.append(row)
    return rows
```

Here is where the technique stops. Some widgets refetch only on blur or on a button
press, so `fill` alone fires nothing and `expect_response` times out on a page that works
fine. Others ship the whole tier table in the first payload and compute in JavaScript, so
there is no per-amount request to catch. Watch one manual amount change in the network
panel first.

## A flat weekend series is not a broken scraper

Spot currency markets close for the weekend and for holidays. The page does not. It keeps
rendering the last number it received, with the same live styling, and your poller keeps
recording it. The result is a run of identical values that looks exactly like a scraper
that quietly stopped working.

The discriminator is already in the row. Across a frozen market the arrival stamps keep
advancing while `quoted_utc` stands still. Store both and the question is a query. Store
only the value and a closed market, a cached response and a dead poller are the same
evidence.

```python
def flag_staleness(rows):
    """Rows for one pair and one side, in observed_utc order."""
    previous_quoted = None
    for row in rows:
        quoted = row.get("quoted_utc")
        if quoted is None:
            row["stale"] = None          # unknown, and unknown is not False
        else:
            row["stale"] = (quoted == previous_quoted)
            previous_quoted = quoted
    return rows
```

The other half is a storage rule: write one row per observation and never dedupe on value
change. A change-only writer produces zero rows across a closed weekend, which is
indistinguishable from a job that crashed on Friday night. Many consumer providers keep
quoting through the weekend at a held rate with a wider margin, so that number is real,
but it is not a market rate.

## One identity for a long polling run

A rate series is a long session by construction. You hold a page open, or reopen it on a
schedule for weeks, and that shape is what a provider meters rather than any single
request. Passing the same `seed` derives every fingerprint surface from one value, so a
reconnect presents the same device.

Geography matters here for correctness before access. Consumer providers quote by region
and corridor, so an exit in the wrong country is not a blocked request, it is a different
product answering your question. Match the exit to the corridor you mean to price, as in
[scraping geotargeted content](how-to-scrape-geotargeted-content-playwright.md), and let
timezone and locale follow the egress IP rather than pinning either by hand, which is the
[mismatch a cross-check looks for](timezone-proxy-mismatch.md).

The honest limit: none of this fixes anything else on this page. A stable identity keeps
a long run from being throttled. It does not tell you which side of the spread you
captured, it does not remove a margin, and it does not make a weekend number move.

## Conclusion

The hard part is not getting the number out, it is writing down enough about the number
that it still means something later. A rate is a pair, a direction, a side, an amount and
a moment, and each of those is a column. Capture the response rather than the painted
value, stamp arrival in UTC at the top of the handler, keep the feed's quote time beside
yours, and record the amount that produced the quote. Then a flat weekend reads as
a closed market instead of an outage, and a consumer quote never gets subtracted from a
reference rate. Skip it and you have a column of floats nobody can safely use.

## Short answers to the questions that lead here

**Why does my scraped rate not match the site?** Usually because you read the painted node
mid-update, or because you captured a different side of the spread than your comparison.
Read the response payload and record the side.

**Which number is "the" exchange rate?** There is no single one. The page shows a bid, an
ask and often a mid, and the gap between them is the provider's product. Store all three
as separate rows instead of picking one.

**Can I invert a rate to get the other direction?** Not from one side of the spread. The
reciprocal of the bid is not the ask, because the spread widens outward from the mid on
both sides, so inverting produces a price nobody quoted.

**Why does the rate change when I type a bigger amount?** The margin is tiered by amount.
That makes the amount an input to the quote, so it belongs in the row along with whether
it was the send side or the receive side.

**My series is flat for two days. Is my scraper broken?** Probably not. Spot markets close
at the weekend while the page keeps showing the last number as live. If your arrival
stamps advance and the feed's quote timestamp does not, the market is closed.

**Which timestamp should I store?** All three you can get, and not your database write
time. The feed's quote time goes on the time axis, the HTTP `Date` header brackets it,
and your UTC arrival stamp is the one you can always produce.

## Sources

- Playwright's [network events](https://playwright.dev/python/docs/network),
  [`Response`](https://playwright.dev/python/docs/api/class-response) including
  `header_value` and `json`, and
  [`expect_response`](https://playwright.dev/python/docs/api/class-page#page-expect-response),
  used exactly as documented upstream. Retrieved 2026-08-28.
- Playwright's [`WebSocket` class and its `framereceived`
  event](https://playwright.dev/python/docs/api/class-websocket), for widgets that stream
  rather than poll. Retrieved 2026-08-28.
- ISO 4217 currency codes, which is why the row stores codes and never glyphs.
- This project's own configuration behaviour: fingerprint surfaces derive from one seed,
  and the browser timezone follows the egress IP unless pinned by hand.

**See also:** [capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for the subscription pattern in full, [scraping cryptocurrency
prices](how-to-scrape-cryptocurrency-prices-playwright.md) for the socket-heavy sibling of
this capture problem, [cleaning scraped prices and
dates](how-to-clean-scraped-prices-and-dates-playwright.md) for turning formatted strings
into numbers, and [scraping stock and financial
data](how-to-scrape-stock-and-financial-data-playwright.md) for a page with three data
layers that each need a different reader.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The first version of this
stored one number per poll with no side and no amount, and weeks later I could not tell
which rows were bids, which were asks and which were a weekend repeating itself.*
