---
title: "How to scrape package tracking status with Playwright"
description: "Scrape parcel tracking with Playwright: capture the event timeline rather than the headline status, keep the carrier's own wording, and poll on the shipment's clock instead of yours."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 155
---


# How to scrape package tracking status with Playwright

To scrape tracking, capture the **event timeline** rather than the headline status. A
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
