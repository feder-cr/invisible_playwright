---
title: "How to scrape pet adoption listings with Playwright"
description: "Scrape shelter adoption listings with Playwright: reconcile disappearances instead of deleting rows, key animals on the shelter's own identifier, and keep photos as references, not copies."
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
so in the row, not pretending the key is authoritative. A key you invented behaves
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
the list to settle, not for a load event.

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

## A complete daily run with reconciliation

```python
import json, time
from invisible_playwright import InvisiblePlaywright

def load_previous(path):
    ids, last = set(), {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["type"] == "appeared":
                    ids.add(r["shelter_id"]); last[r["shelter_id"]] = r
                elif r["type"] == "disappeared":
                    ids.discard(r["shelter_id"])
    except FileNotFoundError:
        pass
    return ids, last

path = "adoptions.jsonl"
previous_ids, _ = load_previous(path)

with InvisiblePlaywright(seed=42) as browser, open(path, "a", encoding="utf-8") as out:
    page = browser.new_page()
    page.goto("https://example-shelter.org/adopt")
    page.select_option("select[name='species']", "any")
    page.wait_for_selector(".animal-card, .no-animals")

    while True:                                  # walk every page of the list
        more = page.query_selector("button.load-more:not([disabled])")
        if not more:
            break
        before = len(page.query_selector_all(".animal-card"))
        more.click()
        page.wait_for_function("n => document.querySelectorAll('.animal-card').length > n",
                               arg=before)

    rows = listing_rows(page)
    run_at = time.time()

    if previous_ids and len(rows) < 0.5 * len(previous_ids):
        raise SystemExit("listing count halved: failed run, not a rescue")

    for event in reconcile(previous_ids, rows, run_at):
        out.write(json.dumps(event, ensure_ascii=False) + "\n")
    out.flush()
```

Loading the previous state from the event log rather than a separate snapshot file keeps
the two from drifting apart, and the halving guard is what stops a partial page load from
being written as a mass adoption. That guard has saved more datasets than any selector in
this page.

## What goes wrong here is rarely a defence

Shelter sites are small and mostly undefended, so the failures are ordinary web failures
with unusually costly consequences for the data.

**A slow list truncates itself.** The load-more loop above waits for the count to grow,
which is the reliable signal. A fixed sleep on a slow volunteer-hosted site produces a
short list, which reconciliation then reads as a wave of adoptions.

**A filter resets between runs.** Some listing widgets reset to a default species or
location when the session cookie expires. The filter is recorded per run for exactly this
reason, and a run whose filters differ from the previous one should not be reconciled
against it:

```python
    if run["filters"] != previous_run["filters"]:
        run["reconciled"] = False       # comparable only to runs with the same filters
```

**An aggregator hides the shelter's own record.** Where you scrape an aggregator, the
`shelter_id` may be the aggregator's, which changes when a shelter re-uploads. Prefer the
originating shelter's page where the aggregator links to it, and record which source the
id came from.

If a run does start returning empty pages or challenges, the diagnosis order is the same
as anywhere else and starts with looking at the page rather than guessing:
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## The event log, and the question it exists to answer

Three event types, appended, keyed on the shelter's own identifier:

| event | written when | carries |
|---|---|---|
| `appeared` | id not seen in the previous run | the full listing snapshot |
| `updated` | a tracked field changed | which fields, old and new |
| `disappeared` | id absent from this run | nothing but the id and the time |

From that log, the waiting time distribution falls out directly: the gap between
`appeared` and `disappeared` per animal. That is the number nobody publishes, it is the
number shelters use to argue for funding, and it is invisible in any mirror of the current
listings.

Two honest caveats belong next to it. `disappeared` is not `adopted`, so any published
figure should say so. And animals that are relisted after a returned adoption appear twice
with the same id, which is real signal rather than noise, but only if your analysis expects
it rather than deduplicating it away.

## Short answers to the questions that lead here

**What should I key an animal on?** The shelter's own identifier, taken from the
record URL. Names repeat, photographs change, and a hash of the visible fields moves
the moment the shelter edits a description.

**An animal vanished from the list. What happened?** That is the most valuable event
on these sites and it has more than one cause: adopted, withdrawn, or filtered out by
a default the site applied before you saw the page. Reconcile the set against the
previous run and record the disappearance as its own event.

**Am I seeing every animal?** Probably not on the first request. Shelter sites default
to filters more often than most listing sites - species, adoptable status, location -
so set the filters explicitly instead of accepting whatever the page opens with.

**Should I download the photographs?** Keep the URL, not the file. Mirroring images
costs a volunteer-run shelter real bandwidth and takes on a licensing question that
the URL does not.

**See also:** [Scrape load-more button pages with
Playwright](how-to-scrape-load-more-button-playwright.md), [Scrape lazy-loaded images
with Playwright](how-to-scrape-lazy-loaded-images-playwright.md), [How to rate limit
your own Playwright scraper](how-to-rate-limit-your-scraper-playwright.md)

## Sources

- Playwright, Locators, https://playwright.dev/python/docs/api/class-locator - the
  locator model used to enumerate cards and read a record URL, checked for keying on
  the shelter's identifier instead of on visible text.
- This project's pages on load-more pagination and lazy-loaded images, which cover the
  two mechanics an aggregator adds on top of a plain shelter list.
