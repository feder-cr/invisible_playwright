---
title: "How to scrape package tracking status with Playwright"
description: "Scrape parcel tracking with Playwright: capture the event timeline instead of the headline status, keep the carrier's own wording, and poll on the shipment's clock instead of yours."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 155
---


# How to scrape package tracking status with Playwright

To scrape tracking, capture the **event timeline**, not the headline status. A
tracking page shows one summary word at the top, and that word is a carrier's
interpretation of a list of scans underneath it. The list is the data. The summary is a
derived field you can always recompute, and it is the field carriers change the wording
of without warning.

The second thing to get right is the polling interval. A parcel generates a handful of
events over several days, so a tight poll produces thousands of identical reads and an
obvious traffic signature, on endpoints that are unusually well monitored because they
are a favourite target of abuse.

This page covers reading the timeline, keeping carrier vocabulary intact, and polling in
a way that matches how shipments actually move.

## Read the timeline, one row per scan

Each scan is a timestamp, a location and a description. Capture all three:

```python
from invisible_playwright import InvisiblePlaywright

def timeline(page):
    events = []
    for row in page.query_selector_all(".tracking-event, li.event"):
        def text(sel):
            node = row.query_selector(sel)
            return node.inner_text().strip() if node else None
        events.append({
            "when_text": text(".event-date, time"),
            "where": text(".event-location"),
            "what": text(".event-description, .status-text"),
        })
    return events

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example-carrier.com/track?number=ABC123456789")
    page.wait_for_selector(".tracking-event, .not-found")
    events = timeline(page)
```

Keep `when_text` as text at capture time. Carriers write dates in local formats, in the
timezone of the scanning facility, sometimes without a year, and a parse that fails
silently at capture puts a wrong timestamp in your database forever. Parse in a second
pass where you can see what failed.

## Order is not guaranteed, and neither is stability

Two properties of these timelines surprise people. Events do not always arrive in order,
because facilities upload in batches, so a scan from Tuesday can appear after one from
Wednesday. And events are sometimes **revised**: a carrier can correct a location or drop
a duplicate scan between two of your reads.

Treat the timeline as a set keyed on the event, not as an append-only log:

```python
def merge(known, fresh):
    index = {(e["when_text"], e["what"], e["where"]): e for e in known}
    for e in fresh:
        index[(e["when_text"], e["what"], e["where"])] = e
    return sorted(index.values(), key=lambda e: e["when_text"] or "")
```

If you also want to detect revisions rather than absorb them, keep each capture whole,
with the time you took it, and diff captures later. That costs more rows and answers
questions about carrier behaviour that a merged view erases.

## Keep the carrier's words, add your own status separately

Carriers use different vocabularies for the same physical event, and they change them.
"Out for delivery", "With courier", "On vehicle for delivery" all mean the same thing at
three carriers. Store the original string, then map it in a layer you control:

```python
DELIVERED = {"delivered", "consegnato", "livre", "zugestellt"}

def is_delivered(event):
    return (event["what"] or "").strip().lower() in DELIVERED
```

Keeping the mapping outside the capture means a new phrase costs you a line, rather than
a re-scrape. It also keeps the honest fact that you are guessing at semantics visible in
the code, instead of buried in a column called `status`.

## Poll on the shipment's clock

A parcel produces events at handoffs: collection, hub in, hub out, local depot, out for
delivery, delivered. That is a handful of moments over days. Polling every minute is
noise, and it looks exactly like the abuse these endpoints exist to resist.

A workable shape is an interval that widens when nothing changes and tightens near
delivery:

```python
def next_interval(events, last_seen):
    if not events:
        return 6 * 3600            # nothing yet: check rarely
    latest = events[-1]["what"] or ""
    if "out for delivery" in latest.lower():
        return 30 * 60             # the one phase worth watching closely
    if events == last_seen:
        return 4 * 3600
    return 60 * 60
```

The general pacing rules, and why a self-imposed limit beats being told, are in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md).

## Three traps worth knowing

**The number is entered, not in the URL.** Many carriers reject a URL with a tracking
number in it unless the session has been through the form. Type it into the field and
submit, the same way a person would.

**Not found and not yet scanned look identical.** A number that a carrier has never seen
and one that was created but not collected both render as an empty page with a generic
message. Capture the message text as a distinct outcome instead of writing an empty
timeline.

**Consent walls and locale interstitials come first.** Especially on European carrier
sites, the first visit is a consent dialog. Clear it once per session as in
[handling cookie consent banners](how-to-handle-cookie-consent-banners-playwright.md).

## Track your own parcels

Tracking numbers are not personal data in themselves, but a timeline is a record of where
someone's package went, and often where they live. Scrape numbers you have a reason to
hold, keep the retention short, and do not enumerate. Guessing tracking numbers to see
what comes back is the behaviour these endpoints are defended against, and it is the one
use of this page that is not worth having.

## A complete watcher for a handful of parcels

```python
import json, time
from invisible_playwright import InvisiblePlaywright

def read_tracking(page, number):
    page.goto("https://example-carrier.com/track")
    dismiss_consent(page)
    box = page.wait_for_selector("input[name='trackingNumber']")
    box.click()
    box.fill("")
    box.type(number, delay=70)
    page.keyboard.press("Enter")
    page.wait_for_selector(".tracking-event, .not-found, .no-info", timeout=25000)

    if page.query_selector(".not-found") or page.query_selector(".no-info"):
        note = page.inner_text(".not-found, .no-info").strip()
        return {"number": number, "events": [], "carrier_note": note}
    return {"number": number, "events": timeline(page), "carrier_note": None}

def dismiss_consent(page):
    node = page.query_selector("#onetrust-accept-btn-handler, .consent-accept")
    if node:
        node.click()
        page.wait_for_timeout(400)

state = {}

with InvisiblePlaywright(seed=42) as browser, open("parcels.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    while parcels_in_flight:
        for number in list(parcels_in_flight):
            result = read_tracking(page, number)
            merged = merge(state.get(number, []), result["events"])
            if merged != state.get(number):
                out.write(json.dumps({**result, "events": merged,
                                      "observed_at": time.time()}, ensure_ascii=False) + "\n")
                out.flush()
                state[number] = merged
            if merged and is_delivered(merged[-1]):
                parcels_in_flight.discard(number)
            page.wait_for_timeout(4000)
        time.sleep(next_interval(merged, state.get(number)))
```

Writing only when the merged timeline changes is what keeps a multi-day watch from
producing thousands of identical records. Dropping a parcel from the set once it is
delivered is the other half: a delivered parcel never changes again, and continuing to
poll it is pure noise on someone else's servers.

## Why carrier endpoints are among the best defended

Tracking pages are attacked constantly, because a valid tracking number is the entry
point for parcel redirection fraud and for phishing that quotes a real shipment. The
defences that result are aggressive, and three of them shape any honest scraper.

**Enumeration is the thing they watch for.** Sequential or high-volume lookups from one
source are the signature, and they are met with a hard block rather than a challenge. This
is the strongest practical argument for the rule above: hold numbers you have a reason to
hold, and the traffic shape stops looking like the attack.

**A stripped client usually never sees a timeline.** The result region is fetched after the
form commits, from an endpoint expecting the browser's context. Driving a real browser is
what makes the request resolve, and a patched Firefox driven by stock Playwright presents
the consistent handshake and fingerprint that a bare client cannot.

**Refusal is frequently disguised as "no information available".** That message is also the
genuine response for a parcel not yet scanned, so the two are indistinguishable without
care. Keep the carrier's exact wording and, when it appears for a number that previously
had events, treat it as a suspicious read rather than a regression:

```python
    if not result["events"] and state.get(number):
        result["suspect"] = "timeline disappeared for a number that had events"
```

The general debug order, cheapest check first, is in
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## A schema that survives revisions

Two tables rather than one. A capture table holding the whole timeline per read, and a
derived event table keyed on the event's own identity:

| table | key | holds |
|---|---|---|
| capture | `number`, `observed_at` | the full timeline as read, plus the carrier note |
| event | `number`, `when_text`, `what`, `where` | one row per distinct scan, first and last seen |

Keeping the captures is what lets you detect a carrier revising or removing a scan, which
a merged-only view silently absorbs. Keeping the derived events is what makes the ordinary
question fast. The cost is duplication, and it is small compared with the alternative,
which is a table that quietly rewrites its own history every time the carrier does.

For retention, delete captures once a parcel is delivered plus whatever window your use
actually needs. A tracking archive is a movement record, and the honest default is to keep
it only as long as it is answering a question.

## Short answers to the questions that lead here

**Why are the scan events out of order?** Because facilities upload in batches and a
later scan can be written before an earlier one. Order is not guaranteed and neither
is stability: a timeline can be revised between two reads, so store what you saw and
when you saw it instead of assuming the last read is the truth.

**Should I map the carrier's wording to my own status?** Keep both. Carriers use
different vocabularies for the same physical event and change them without notice, so
the carrier's words are the record and your status is a derivation you can redo when
the mapping turns out to be wrong.

**Why does the tracking page reject my URL?** Many carriers only accept a tracking
number that was entered on the page, not one placed in the address bar. Type it into
the field and submit the form.

**How often should I check a parcel?** On the shipment's clock. Parcels produce events
at handoffs, a few times a day at most, so a tight poll loop buys nothing and is the
easiest possible thing for a carrier to spot.

**See also:** [How to handle cookie consent banners in
Playwright](how-to-handle-cookie-consent-banners-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md), [How to
scrape without getting blocked](how-to-scrape-without-getting-blocked.md)

## Sources

- Playwright, Input, https://playwright.dev/python/docs/input - `locator.fill()` and
  `press_sequentially()`, checked for entering a tracking number into a form that
  refuses a value placed in the URL.
- This project's page on scraping without getting blocked, for the debug order when a
  read starts degrading instead of failing.
