---
title: "How to clean scraped prices and dates with Playwright"
description: "Parse locale-formatted prices and relative dates into typed numbers and UTC timestamps with Playwright, using the browser's own stable locale and timezone."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 65
---


# How to clean scraped prices and dates with Playwright

To clean scraped prices and dates, read the browser's resolved locale and timezone once
per page, then parse every value against that single locale: numbers through a
locale-aware library into `Decimal`, and relative or localized dates into UTC. Do not
autodetect the format per value.

The hard part of scraping prices and dates is not extracting the text. It is turning
that text into a typed number and a UTC timestamp when the text does not tell you, on
its own, which convention it was written in.

`1.234,56` is one thousand two hundred thirty four and change in one locale and is a
syntax error in another. `03/04/2026` is in March or in April depending on where the
page thinks you are. `2 days ago` is not a date at all until you know the clock it was
measured against. Every one of these depends on the locale the page rendered in, and
that locale is decided by your exit, not by your parser. This page is how to parse them
correctly, and why keeping the browser's locale stable is what makes the parser
reliable run after run.

## Why locale is the hard part of price and date parsing

Three things vary with locale and all three break naive parsing:

- **The decimal and thousands separators swap.** `1.234,56` and `1,234.56` are the same
  amount written two ways. A parser that assumes one reads the other off by a factor of
  a thousand, and it does it silently, because both strings are valid floats to a regex.
- **The currency symbol and its position move.** Leading `$`, trailing ` kr`, a
  non-breaking space, an ISO code like `EUR` instead of a glyph. You have to strip the
  right thing before you parse the number, and what is right depends on the locale.
- **Dates arrive localized and relative.** Month names in the page's language, day and
  month in either order, and relative forms like `2 days ago`, `vor 2 Tagen`, `hace 2
  dias`. Resolving a relative form needs both the language and the clock it counts back
  from.

The trap is that all of this is stable only if the page keeps rendering in the same
locale. Geotargeted pages render in the locale tied to your exit IP and timezone. If
that pairing drifts between runs, the formats drift with it, and a parser that worked
yesterday quietly misreads today.

## Anchor the browser to its exit so the formats stay stable

Geotargeted content picks its locale from the exit the request appears to come from,
cross-checked against the browser's own timezone and language. Keep those consistent
with the proxy exit and the page renders the same way every time, which is what makes a
parser something you can write once. Let them disagree and you get two failures at once:
the page may serve a different locale than you planned for, and the mismatch is itself a
[cross-check a detector uses to flag the session](timezone-proxy-mismatch.md).

This wrapper derives the timezone and language from the egress IP by default, and a
fixed seed makes the whole identity reproducible, so the locale you parse against does
not move underneath you:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product")

    # Read the locale and timezone the browser actually ships. These are the
    # ones the page saw and rendered against, so they are the ones to parse with.
    locale = page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().locale")
    tz = page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().timeZone")
    print("parsing against", locale, tz)   # e.g. de-DE Europe/Berlin

    raw_price = page.inner_text(".price")   # "1.234,56 EUR"
    raw_posted = page.inner_text(".posted") # "vor 2 Tagen"
```

Because the browser returned by `InvisiblePlaywright` is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), `page.evaluate`,
`page.inner_text` and every other method are the stock API. There is nothing
wrapper-specific to learn on the extraction side. What the wrapper gives you is
a locale and timezone that agree with the exit and stay put across runs, so the two
values you read above are trustworthy inputs to the parser rather than a moving target.

## Parse locale-formatted prices into typed numbers

Strip everything that is not part of the number, then parse the digits and separators
with the page's locale rather than guessing. Babel does the locale-aware step:

```bash
pip install babel
```

```python
import re
from decimal import Decimal
from babel.numbers import parse_decimal

def clean_price(raw, locale):
    """'1.234,56 EUR' + 'de-DE' -> Decimal('1234.56')."""
    # keep only digits and the two separator characters and a sign
    number_part = re.sub(r"[^0-9.,-]", "", raw).strip(".,")
    # Babel wants an underscore locale ('de_DE'), the browser gives a hyphen
    value = parse_decimal(number_part, locale=locale.replace("-", "_"), strict=False)
    return Decimal(str(value))

price = clean_price(raw_price, locale)   # Decimal('1234.56')
```

`parse_decimal` reads `1.234,56` as `1234.56` under `de_DE` and `1,234.56` as the same
under `en_US`, because it knows which character is the grouping separator in each. That
is the entire point: the separators are ambiguous in isolation and unambiguous once you
name the locale. Keep the currency symbol separately if you need it (a plain regex for
the ISO code or glyph), and keep the amount as a `Decimal`, never a float, so cents do
not drift.

## Parse localized and relative dates into UTC timestamps

Relative and localized dates need the page's language to read the words and the page's
timezone to anchor the arithmetic. `dateparser` takes both:

```bash
pip install dateparser
```

```python
import dateparser

def clean_date(raw, locale, tz):
    """'vor 2 Tagen' + 'de-DE' + 'Europe/Berlin' -> aware UTC datetime."""
    language = locale.split("-")[0]          # 'de'
    return dateparser.parse(
        raw,
        languages=[language],                 # pin the language, do not sniff it
        settings={
            "TIMEZONE": tz,                   # the clock the page counted from
            "TO_TIMEZONE": "UTC",             # normalize everything to UTC
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )

posted = clean_date(raw_posted, locale, tz)  # datetime in UTC, tz-aware
```

`2 days ago` only becomes a timestamp once you fix the timezone it was measured in;
computed against the wrong zone it lands hours off, and near midnight it lands on the
wrong day. Passing the same `tz` you read from the browser closes that gap. Store the
result in UTC and keep the original string in a separate column, so a parse you later
doubt can be re-derived instead of guessed.

## Pin the parsing locale to the fingerprint, do not autodetect

The one rule that saves the most cleanup later: **parse every value in a run against the
one locale the browser reported, not against a locale you sniff per value.**

Per-value autodetection looks convenient and fails on exactly the rows that matter. Feed
an autodetector `1.234` and it has no way to know whether that is one thousand two
hundred thirty four written in one convention or one-point-two-three-four in another;
it will pick one, then pick the other three rows down, and your column ends up with a
thousand-fold error scattered through it that no single value looks wrong enough to
catch. The locale is not a property of the value. It is a property of the page, and you
already read it once, up front, from the browser that rendered the page.

Concretely, in a batch of price strings mixing `1.234,56` and `1,234.56`, per-value
autodetection flips the decimal separator on every ambiguous short value it meets, while
pinning to the page's own `locale` reads all of them the same correct way. Read the
locale once per page load, thread it through every `clean_price` and `clean_date` call,
and the ambiguity is gone by construction.

This is also why a stable browser identity matters to a data pipeline and not just to
staying unblocked. If the exit, timezone and language stay consistent, the locale you
pinned at the top of the run is the locale every value on the page was written in.

## A short demonstration

Put the three pieces together and one page yields typed rows:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")

    locale = page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().locale")
    tz = page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().timeZone")

    rows = []
    for card in page.query_selector_all(".card"):
        rows.append({
            "price": clean_price(card.query_selector(".price").inner_text(), locale),
            "posted_utc": clean_date(card.query_selector(".posted").inner_text(), locale, tz),
            "price_raw": card.query_selector(".price").inner_text(),
        })

    for r in rows:
        print(r["price"], r["posted_utc"].isoformat())
```

Re-run it with the same seed and proxy and the page renders in the same locale, so the
same parser reads the same rows the same way. That reproducibility is the difference
between a cleaning step you trust and one you re-audit every morning.

## Conclusion

Cleaning scraped prices and dates is a locale problem wearing a parsing costume. Read
the browser's own locale and timezone once per page, parse numbers with a locale-aware
library instead of a bare `float`, resolve relative and localized dates against the
timezone the page used, keep amounts as `Decimal` and timestamps as UTC, and never let a
per-value autodetector guess a separator it cannot know. The upstream requirement for all
of it is a browser whose locale and timezone stay consistent with the exit, because that
is what keeps the formats stable enough to parse the same way twice.

## Short answers to the questions that lead here

**Why is my scraped price a thousand times too big or too small?** Your parser assumed
the wrong decimal separator. `1.234,56` and `1,234.56` are the same amount in different
locales; parse with the page's locale instead of a bare `float()`.

**How do I parse "2 days ago" into a real date?** With a library that handles relative
dates, and pass it the page's timezone as the base clock. Without the timezone it lands
hours off and, near midnight, on the wrong day.

**Should I autodetect the locale for each value?** No. Pin one locale per page, read from
the browser, and use it for every value. Ambiguous short numbers are exactly the ones
autodetection gets wrong.

**Why do the formats change between runs?** Because geotargeted pages render in the
locale tied to your exit, and if the exit, timezone or language drift, the formats drift
with them. Keeping them consistent with the exit keeps the formats stable.

**Do I need the currency symbol to parse the number?** No, strip it first. Keep it in a
separate field if you want it; it does not belong in the numeric column.

**Float or Decimal for money?** `Decimal`. Floats lose cents at scale, and money that is
off by a rounding error is off.

## Sources

- The wrapper's real API for launching the browser and reading the page, from the
  [Quickstart](quickstart.md) and [Configuration](configuration.md) pages in this set.
- [`Intl.DateTimeFormat().resolvedOptions()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat/resolvedOptions)
  for the browser's resolved locale and timezone, read live from the page rather than
  assumed.
- The locale-aware behaviour of the Babel and dateparser libraries, used as documented
  upstream.

**See also:** [how to scrape geotargeted content](how-to-scrape-geotargeted-content-playwright.md)
for making the exit, locale and timezone agree in the first place,
[when the timezone does not match the proxy](timezone-proxy-mismatch.md) for what a
mismatch costs beyond broken formats, and
[how to export scraped data to CSV](how-to-scrape-to-csv-playwright.md) for keeping the
`Decimal` and UTC values intact on the way out.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The locale you parse
against is a property of the page, and the page's locale is a property of your exit.*
