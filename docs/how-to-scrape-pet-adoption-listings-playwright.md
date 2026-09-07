---
title: "How to scrape pet adoption listings with Playwright"
description: "Scrape shelter adoption listings with Playwright: reconcile disappearances instead of deleting rows, key animals on the shelter's own identifier, and keep photos as references rather than copies."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 158
---


# How to scrape pet adoption listings with Playwright

To scrape adoption listings, treat a **disappearance as data**. Listings leave these sites
constantly, because the animal was adopted, moved to another shelter, or the listing was
merged, and a scraper that mirrors the current page loses the single most interesting
signal the dataset contains: how long an animal waited.

The second decision is the key. Shelter software gives every animal an identifier, usually
in the URL. Use it. Names repeat heavily in this domain, and keying on name plus breed
merges two different dogs called Luna every time.

This page covers reconciling each run against the last, keying on the shelter's own
identifier, and handling the photo-heavy pages these listings live on.

## Key on the shelter's identifier, from the URL

```python
import re
from invisible_playwright import InvisiblePlaywright

ID_IN_URL = re.compile(r"/(?:animal|pet)/(?P<id>[A-Za-z0-9-]+)")

def listing_rows(page):
    rows = []
    for card in page.query_selector_all(".animal-card"):
        link = card.query_selector("a")
        href = link.get_attribute("href") or ""
        m = ID_IN_URL.search(href)
        rows.append({
            "shelter_id": m.group("id") if m else None,
            "href": href,
            "name": card.query_selector(".name").inner_text().strip(),
            "summary": card.inner_text().strip(),
        })
    return rows
```

When no identifier is available anywhere, fall back to a hash of the stable fields and say
so in the row, rather than pretending the key is authoritative. A key you invented behaves
differently from one the site issued, and the difference shows up as churn that looks like
animals arriving and leaving daily.

## Reconcile, do not mirror

Each run, compare the set you see with the set you saw last time, and write three kinds of
event:

```python
def reconcile(previous_ids, current_rows, run_at):
    current_ids = {r["shelter_id"] for r in current_rows}
    events = []
    for row in current_rows:
        if row["shelter_id"] not in previous_ids:
            events.append({"type": "appeared", "at": run_at, **row})
    for gone in previous_ids - current_ids:
        events.append({"type": "disappeared", "at": run_at, "shelter_id": gone})
    return events
```

"Disappeared" is not "adopted", and it is worth being strict about that in the schema.
Listings also vanish because a shelter reorganised its site, because a filter you did not
set was applied, or because a page failed to load fully. Record the neutral fact and let
an analysis layer interpret it, ideally alongside the run's own health, such as how many
cards the page returned in total.

Guard against the failure mode where an empty page reads as a mass adoption event:

```python
    if len(current_rows) < 0.5 * len(previous_ids):
        raise SystemExit("listing count halved: treating as a failed run, not a rescue")
```

## The list is filtered before you see it

Shelter sites default to filters more often than most listing sites: species, adoptable
status, location radius, sometimes an animal's readiness date. Set them deliberately and
record what you set:

```python
    page.select_option("select[name='species']", "any")
    page.check("input[name='include_pending']")
    row["filters"] = {"species": "any", "include_pending": True}
```

Recording the filters is what makes two runs comparable. A dataset whose filters drifted
between runs shows population changes that never happened, and there is nothing in the
rows to reveal it.

Facet controls that repopulate the list without a navigation behave like the ones in
[multi-select facet filters](how-to-scrape-multi-select-facets-playwright.md): wait for
the list to settle rather than for a load event.

## Photos: keep the URL, not the file

These pages are image heavy, and mirroring photographs is both a bandwidth cost to a
shelter and a licensing question you probably have not answered. Keep the URL and the
alt text:

```python
        img = card.query_selector("img")
        row["photo_url"] = img.get_attribute("src") if img else None
        row["photo_alt"] = img.get_attribute("alt") if img else None
```

If the images are lazy loaded, the `src` may be a placeholder until the card scrolls into
view. The handling is the same as in
[scraping lazy-loaded images](how-to-scrape-lazy-loaded-images-playwright.md): trigger
the load, then read, or read the real URL from the `data-src` attribute the page is
holding it in.

## Pagination and the long tail

Most shelters have few animals and one page. Aggregators have many, behind
[a load-more button](how-to-scrape-load-more-button-playwright.md) or classic pagination.
Walk it to the end rather than taking the first page, because the animals that have been
waiting longest are usually last in the default sort, and those are exactly the rows a
"how long do they wait" question needs.

## Be gentle, and consider asking

Shelters are volunteer-run more often than not, and several publish an export or an API on
request precisely so people stop scraping them. That is a genuinely better source: it is
faster for you, cheaper for them, and it comes with permission attached. Where you do
scrape, run once a day, respect the pacing in
[rate limiting your own scraper](how-to-rate-limit-your-scraper-playwright.md), and keep
the contact details of the site you are reading in case someone wants to ask you to stop.
