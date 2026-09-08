---
title: "How to scrape domain WHOIS records with Playwright"
description: "Scrape WHOIS records with Playwright by parsing labeled fields instead of positions, treating REDACTED FOR PRIVACY as a real value, keeping raw EPP status codes, and pacing lookups tighter than a catalog page."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 140
---


# How to scrape domain WHOIS records with Playwright

To scrape domain WHOIS records with Playwright, read the record page as labeled
key-value lines instead of fixed positions, treat REDACTED FOR PRIVACY as a real
value instead of a missing field, keep the raw EPP status code instead of
paraphrasing it, bind creation, expiry and updated dates to their own labels, flag
records that describe a privacy proxy instead of the actual registrant, and pace
each lookup by whole seconds because the query itself, not the page around it, is
the resource the registry is rationing.

A WHOIS record reads like a simple text dump and mostly is one: a block of
"Label: value" lines describing who registered a domain, when, and what state it
is in. What trips up a scraper is not the format, it is what each field quietly
implies. A redacted value is not an empty one. A populated registrant field can
describe a proxy company standing in front of the real owner. A status line is a
legal term from a fixed vocabulary, not a sentence you can rephrase. Three dates
sit close enough together that swapping two of them produces a value that still
looks perfectly plausible.

This page covers the browser-driven route: a registry's or registrar's own WHOIS
lookup page, rendered as HTML and read like any other page with Playwright. WHOIS
also has a real wire protocol, plain text over TCP port 43, answering straight
from the authoritative source with none of the caching a web page adds in front
of it. That socket is out of scope here, but it is worth knowing it exists for
the case where freshness matters more than staying inside a browser session.

## Two places WHOIS data comes from, and this page reads one of them

The protocol answer and the web page answer are not the same request underneath,
even when a registrar's site fronts the protocol server directly. Many lookup
pages cache the protocol response for a stretch of hours, so a status change or
a fresh expiry date can already be true on the wire while the page still shows
yesterday's answer.

That gap rarely matters for a one-off check. It matters a great deal if you are
watching for a state change, a domain moving into `pendingDelete` or losing a
`clientTransferProhibited` lock, because the page can lag exactly the transition
you are tracking. Querying port 43 directly sidesteps the cache; this page
assumes you are reading the rendered lookup page instead, the route that fits a
Playwright session without any raw socket code.

## Read the record as labeled fields, never by position

A WHOIS record page usually renders its answer as one monospaced block, sometimes
inside a `<pre>` tag, sometimes as a table of label and value cells. Either shape
is a list of key-value lines under the surface, and the key identifies a field,
not where it sits in the block. Two registrars order the same fields
differently, and one blank line shifts every downstream field by one if you are
counting lines instead of reading labels.

```python
from invisible_playwright import InvisiblePlaywright

def read_raw_record(page, domain):
    page.goto(f"https://example.com/whois/{domain}", wait_until="networkidle")
    # most lookup pages put the raw answer in one monospaced block
    block = page.locator("pre, .whois-result, [data-testid='whois-raw']").first
    return block.inner_text()

def parse_whois_block(raw):
    record = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label, value = label.strip(), value.strip()
        if not label or not value:
            continue
        if label in record:
            existing = record[label]
            record[label] = existing + [value] if isinstance(existing, list) else [existing, value]
        else:
            record[label] = value
    return record

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    raw = read_raw_record(page, "example-domain.com")
    record = parse_whois_block(raw)
```

Repeated labels, `Domain Status` and `Name Server` almost always appear more than
once, collapse into a list instead of overwriting the earlier value. Everything
downstream reads from `record` by name, never by an index into the raw text.

## REDACTED FOR PRIVACY is a value, not a missing field

Since the 2018 privacy rules that followed GDPR, most gTLD registrars withhold
personal registrant, admin and tech contact details by default. The field is
still present: what comes back for the name, email, phone and address is
literally the string `REDACTED FOR PRIVACY`, or a close registrar-specific
variant, and that string is a legitimate answer, not a placeholder for missing
data.

The distinction that matters is between a field the page never mentions at all
and a field the page mentions with that redacted value. `record.get("Registrant
Organization")` returning `None` means the registry's format never carried that
line, which happens on plenty of ccTLDs. The same lookup returning the literal
string means the field exists and the registrant was defaulted into privacy, a
genuine answer that just carries no personal detail. Counting the second case
as "missing" gives a wrong read on which domains actually withhold an owner
versus which simply use a thinner record format.

## A proxy's contact is not the owner's

A narrower case sits behind the same-looking fields: some domains route
registration through a dedicated privacy or proxy service, a company whose
entire business is to appear as registrant of record on someone else's behalf.
The contact fields are populated, not redacted, but they describe the proxy,
not the business that actually runs the domain. The usual tell is the proxy
service's own name in the organization field next to a role-account email at
the proxy's own domain.

```python
PRIVACY_PROXY_MARKERS = (
    "privacy", "proxy", "whoisguard", "domains by proxy", "perfect privacy",
)

def looks_like_proxy(record):
    org = str(record.get("Registrant Organization", "")).lower()
    email = str(record.get("Registrant Email", "")).lower()
    return any(marker in org for marker in PRIVACY_PROXY_MARKERS) or "proxy" in email

record["is_proxied"] = looks_like_proxy(record)
```

Carry that flag alongside the fields instead of quietly trusting them. A
pipeline that treats a proxy's address as the registrant's is correct in form
and wrong in substance, the harder kind of mistake to catch later.

## The format is not one schema across registries

Two registries answering about two different domains rarely spell the same
field the same way. `Registry Expiry Date`, `Registrar Registration Expiration
Date` and plain `Expiration Date` are three labels for the same fact. Dates
arrive as ISO-8601 with a trailing `Z`, as `DD-Mon-YYYY`, or as a bare
`DD/MM/YYYY` that is ambiguous once the day is 12 or under. Nameservers ride on
one comma-separated line for one registrar and one line per server for the
next. There is no single schema to assume.

Normalize before doing anything with the values: map the label variants you have
actually seen onto a small set of canonical keys, and try each known date format
in turn instead of committing to one.

```python
from datetime import datetime

LABEL_ALIASES = {
    "Creation Date": "created",
    "Domain Registration Date": "created",
    "Registry Expiry Date": "expires",
    "Registrar Registration Expiration Date": "expires",
    "Expiration Date": "expires",
    "Updated Date": "updated",
    "Last Updated On": "updated",
}

DATE_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y")

def parse_date(value):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None  # keep the raw string on the caller side; do not guess a format
```

Keep the exact label you matched next to the canonical key while you are still
adding registries to the map. A normalization that throws the original label away
early cannot be corrected without a re-scrape once you find the one registry that
does not fit the pattern you assumed.

## Domain status codes are a small vocabulary, not free text

The `Domain Status` line is not prose describing a domain's situation in words
of your choosing; it draws from a fixed set of EPP status codes that ICANN
standardizes, `clientTransferProhibited`, `pendingDelete`, `redemptionPeriod`,
`ok`, `clientHold`, and a short list of others. Store the code exactly as it
appears. Paraphrasing it, "locked" for `clientTransferProhibited`, "about to be
deleted" for `pendingDelete`, throws away the precision the standard provides:
a domain in `redemptionPeriod` can still be reclaimed by its original owner for
a fee, while one in `pendingDelete` generally cannot.

Registries commonly append the ICANN reference URL to each code on the same
line. Strip that suffix and keep the bare token.

```python
def canonical_statuses(record):
    raw_statuses = record.get("Domain Status", [])
    if isinstance(raw_statuses, str):
        raw_statuses = [raw_statuses]
    return [entry.split()[0] for entry in raw_statuses if entry]
    # keep the bare EPP token, never a paraphrase of what it means
```

A domain frequently carries two or three statuses at once, so keep the result as
a list instead of collapsing it to a single value.

## Bind creation, expiry and updated dates to their own labels

The three dates that matter, creation, expiry and last update, sit close
together on the page and are easy to mix up. A script that grabs "the next
date-looking string" or splits the block by position will silently swap two of
them the moment it meets a registry that orders the fields differently, and
nothing about the output will look wrong: both values stay valid dates, just
attached to the wrong meaning.

```python
def bind_dates(record):
    dates = {}
    for label, canonical in LABEL_ALIASES.items():
        if label in record and canonical not in dates:
            dates[canonical] = parse_date(record[label])
    return dates   # {"created": ..., "expires": ..., "updated": ...}, never by position
```

Reading `record[label]` through the alias map from the previous section means a
date only ever lands in `created`, `expires` or `updated` because the field in
front of it said so, not because it happened to be the second or third
recognizable date on the page.

## Pace lookups tighter than you would a catalog page

A WHOIS query is not a cached listing served off a database index the registry
built ahead of time. Every lookup, protocol or web page, runs an authoritative
query against the live registry for that one domain, which makes the lookup
itself the expensive part of the exchange, not the page wrapped around it.
Rate limits on WHOIS lookups are tighter than on most catalog pages for exactly
that reason, and a pacing scheme copied from a catalog scraper, a random gap
of a second or two, will get a lookup service returning empty answers or a
captcha well before a similar gap trips a catalog site's limiter. When the
limiter does answer, the response is usually a status code and not a page, and
[the backoff that belongs in the middle of a run](how-to-handle-403-429-backoff-mid-scrape-playwright.md)
is a different piece of work from the pacing that avoids reaching it.

```python
import random
import time
from invisible_playwright import InvisiblePlaywright

def scrape_domains(domains, seed=42):
    rng = random.Random(seed)
    rows = []
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        for domain in domains:
            raw = read_raw_record(page, domain)
            record = parse_whois_block(raw)
            record["is_proxied"] = looks_like_proxy(record)
            record["status"] = canonical_statuses(record)
            record.update(bind_dates(record))
            rows.append(record)

            # a WHOIS lookup is a live query against the registry, not a
            # cached listing, so the safe gap is measured in whole seconds
            time.sleep(rng.uniform(4.0, 9.0))
    return rows
```

Treat a rate-limit response as a hard stop, not something to retry right
away. Most lookup services signal it with a specific error string, a blank
result, or a captcha page, not an HTTP error code, and hammering past that
signal tends to extend the block instead of working around it.

## Conclusion

A WHOIS record looks like a short block of plain text and behaves like one full
of footnotes a naive parser walks straight past. Read it by label, not by
position, so a reordered field or a blank line never shifts your data one row
off. Keep REDACTED FOR PRIVACY as the real value it is, flag a proxy's contact
for what it describes, and store the raw EPP status code instead of a
paraphrase that drifts from the standard's meaning over time. Bind the three
dates to their own labels, and pace every lookup on the assumption that the
registry is doing real, expensive work for each one, because it is. The
parsing is straightforward once each value is tied to the field that names it;
the pacing is what decides whether you get to keep making requests at all.

One record per domain, with a variable set of labels and a raw block kept
alongside, is the shape
[JSON Lines handles without a schema argument](how-to-scrape-to-json-lines-playwright.md):
a ccTLD that carries three fields and a gTLD that carries twenty go into the
same file without either one padding the other.

## Short answers to the questions that lead here

**Does WHOIS only exist as a web page?** No. The original protocol answers in
plain text over TCP port 43, straight from the authoritative registry, and most
WHOIS web pages are a rendering of that same protocol answer with caching added
in front. The web page can lag the live protocol answer by hours.

**What does REDACTED FOR PRIVACY mean, and is that field missing?** The field
exists; the registrant's personal detail was withheld under the privacy rules
most gTLD registrars apply by default since 2018. Treat the string as a real
value, distinct from a field the registry's format never includes at all.

**The registrant email looks like a company, not a person. Why?** The domain
likely routes through a privacy or proxy service, and the fields you are reading
describe that service, not the actual owner. Flag records like this instead of
trusting the contact details at face value.

**Can I paraphrase a domain status code to make it more readable?** No. EPP
status codes are a small, fixed vocabulary that ICANN standardizes, and each one
carries a specific legal meaning, `redemptionPeriod` is recoverable, `pendingDelete`
generally is not. Store the raw code and translate it for display only, never in
the stored record.

**How do I avoid mixing up the creation, expiry and updated dates?** Bind each
value to the exact label in front of it rather than grabbing dates by position on
the page. Registries order these three fields differently, and two valid-looking
dates swapped for each other will not look wrong on inspection.

**How fast can I query a WHOIS lookup page?** Slower than a catalog or product
page. The lookup is a live authoritative query rather than a cached listing, so
the safe pace is measured in whole seconds per request, and a rate-limit
response should stop the run rather than trigger an immediate retry.

## Sources

- Playwright's [`Locator.inner_text`](https://playwright.dev/python/docs/api/class-locator#locator-inner-text)
  and [`Page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto),
  used exactly as documented upstream to read the rendered record block.
- ICANN's EPP domain status codes, a fixed vocabulary (`clientTransferProhibited`,
  `pendingDelete`, `redemptionPeriod` and the rest) that WHOIS records draw the
  `Domain Status` field from rather than free text.
- The 2018 privacy rules that followed the temporary specification adopted after
  GDPR, which is why most gTLD registrant, admin and tech contact fields default
  to a redacted value rather than a personal one.

**See also:** [rate-limiting a scraper](how-to-rate-limit-your-scraper-playwright.md)
for the pacing mechanics behind the last section, [scraping microdata and
structured markup](how-to-scrape-microdata-markup-playwright.md) for another case
where reading by label beats reading by position, [scraping to CSV](how-to-scrape-to-csv-playwright.md)
for turning the bound record into rows worth storing, and
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md) for
what to do once a lookup source starts refusing requests.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A batch job once
swapped the creation and updated dates for every registry that lists them in the
opposite order, because the parser grabbed the second and third date-looking
string on the page instead of pairing each value with the label in front of it;
finding it took longer than the fix did.*
