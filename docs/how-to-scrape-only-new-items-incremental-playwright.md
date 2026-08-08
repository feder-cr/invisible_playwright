---
title: "Incremental scraping: only new items since last run"
description: "Scrape only new items in Playwright with a high-water mark that stops at the first already-seen id, handling out-of-order inserts, edits, and a varied schedule."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 75
---


# Incremental scraping: only new items since last run

**To scrape only the new items instead of the whole feed, keep the id of the newest item
you already have as a high-water mark, walk the feed newest-first on each run, and stop the
moment you reach that id.** Everything above it is new; everything from it down you already
own, so there is no reason to read further.

The default way to keep a dataset fresh is to re-scrape the whole feed on every run and
diff it against what you already have. It works, and it is the wrong shape twice over: it
pays for pages you already own, and it makes the same large, identical request pattern on
a schedule, which is exactly the kind of thing a site can learn to recognize.

Incremental scraping fetches newest-first and stops the moment it hits an item it has
already seen. This page is how to build that stop condition so it is correct, how to keep
it correct when items arrive out of order or get edited after you saved them, and the one
stealth caveat that a smaller footprint introduces rather than removes.

## Why re-scraping the whole feed every run is the wrong default

Two costs, and only one of them is on the invoice.

The visible cost is budget. A feed with a thousand items behind twenty pages of pagination
costs twenty page loads every run whether ten items changed or none did. Over a day of
hourly runs that is four hundred and eighty page loads to observe, on average, a handful of
new rows.

The invisible cost is exposure. Every one of those page loads is a request, and requests
are what a site counts. A scraper that pulls the entire back catalogue every hour is
generating a velocity and volume signature far larger than the information it is actually
collecting. [Request velocity is a scored detection signal, not a matter of politeness](how-to-rate-limit-your-scraper-playwright.md),
so the full-feed re-scrape is paying twice: once in load time and once in how legible it
makes you.

Fetching only what changed shrinks both. The rest of this page is how to know what changed
without reading everything to find out.

| | Full-feed re-scrape | Incremental (high-water mark) |
|---|---|---|
| Requests per run | Every page, every run (e.g. ~20 page loads for a 1,000-item feed) | Only the new top of the feed, often a few and sometimes zero |
| Cost when nothing changed | Unchanged: still the full pass | Near zero: stops at the first already-seen id |
| First run | Full pass | Full pass once, then incremental after |
| Pattern signature | Large, identical volume on a schedule, easy to recognize | Small, but a fixed size on a fixed clock is its own pattern (see below) |

## The high-water mark: stop at the first already-seen id

The mechanism is one value: the id of the newest item you have. Call it the high-water
mark. On each run you walk the feed newest-first and compare every id against it. The first
id you recognize is the boundary between new and old, and there is no reason to read past
it.

Each item needs a stable id. A permalink, a numeric post id, an SKU: anything the site
assigns that does not change between runs. A hash of the item's own text is a last resort,
because it changes when the item is edited, which defeats the point.

```python
import json
from pathlib import Path
from invisible_playwright import InvisiblePlaywright

STATE = Path("scraper_state.json")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seed": None, "high_water_id": None, "seen": {}}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def scrape_new_items(page, high_water_id):
    """Walk the feed newest-first, stop at the first id we already have."""
    new_items = []
    page.goto("https://example.com/feed", wait_until="domcontentloaded")

    for card in page.query_selector_all(".item-card"):
        item_id = card.get_attribute("data-id")
        if item_id == high_water_id:
            # Reached the boundary: everything below here is already ours.
            break
        new_items.append({
            "id": item_id,
            "title": card.query_selector(".title").inner_text(),
            "url": card.query_selector("a").get_attribute("href"),
        })

    return new_items


state = load_state()

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    fresh = scrape_new_items(page, state["high_water_id"])

if fresh:
    # newest item is first, so it becomes the new mark
    state["high_water_id"] = fresh[0]["id"]
    for item in fresh:
        state["seen"][item["id"]] = item
    save_state(state)

print(f"{len(fresh)} new items")
```

`browser` here is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so `new_page`,
`query_selector_all`, `get_attribute` and `inner_text` are the stock methods you already
know. The only thing the wrapper changes is that the session behind them carries a full,
consistent fingerprint. The `seed=42` is deliberate and the next sections explain why it
stays fixed.

## Handling out-of-order inserts and edits to items you already have

A naive "stop at the first known id" is correct only if the feed is strictly newest-first
and nothing is ever backdated. Real feeds break both assumptions, so the stop condition
needs two adjustments.

**Out-of-order inserts.** Some feeds place an item by its creation time, not its publish
time, so a row can appear *below* your high-water mark after you have already passed that
point. If you stop dead at the first known id you will never see it. The fix is an overlap
window: do not stop at the first match, keep reading a fixed number of items past it, and
only stop once you have seen an unbroken run of already-known ids. The window is small and
fixed, so the extra cost is a handful of comparisons, not another full pass.

**Edits to items you already have.** An id you have seen is not proof the item is unchanged.
Store a cheap content signature next to each id and compare it when the id is a repeat. If
the signature moved, the item was edited and you re-capture it even though it is not new.

```python
import hashlib


def signature(fields):
    raw = "|".join(str(fields.get(k, "")) for k in ("title", "price", "body"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def scrape_incremental(page, state, overlap=5):
    new_or_changed = []
    known_streak = 0
    page.goto("https://example.com/feed", wait_until="domcontentloaded")

    for card in page.query_selector_all(".item-card"):
        item_id = card.get_attribute("data-id")
        fields = {
            "title": card.query_selector(".title").inner_text(),
            "price": card.query_selector(".price").inner_text(),
            "url": card.query_selector("a").get_attribute("href"),
        }
        sig = signature(fields)
        prior = state["seen"].get(item_id)

        if prior is None:
            new_or_changed.append(("new", item_id, fields, sig))
            known_streak = 0
        elif prior.get("sig") != sig:
            # id is old but the content moved: an edit
            new_or_changed.append(("edit", item_id, fields, sig))
            known_streak = 0
        else:
            # genuinely already have it, unchanged
            known_streak += 1
            if known_streak >= overlap:
                break   # an unbroken run of known items: safe to stop

    return new_or_changed
```

The overlap turns a brittle single-id boundary into a small buffer that tolerates a few
backdated rows, and the signature turns "have I seen this id" into "have I seen this exact
item". Together they are the difference between an incremental scraper that quietly drifts
out of sync and one that stays correct.

If the feed is spread across numbered pages rather than one long list, the same boundary
logic applies per page: keep turning pages only while the last page still contained a new
or changed item. [Crawling pagination without the stale-handle crash](how-to-scrape-paginated-pages-playwright.md)
covers the page-turn mechanics that sit underneath this loop.

## Keep the identity, vary the schedule

Here is the honest caveat, and it is the reason a smaller footprint is not automatically a
quieter one.

Incremental scraping makes each run tiny, which is good. But a tiny, fixed-size request
made from a fixed identity at the same minute every hour is its own pattern. Three requests
at 09:00, three at 10:00, three at 11:00, all with the same fingerprint, is a metronome. A
site does not need to break your fingerprint to notice a metronome; it just needs a clock.

So the move is to split the two things people usually couple:

**Keep the identity stable.** Reuse [the same seed across runs](reproducible-agent-browser-identity-seed.md)
so the fingerprint is continuous. A returning visitor who looks identical week to week is
normal; an identity that
is freshly minted on every visit is not, and rotating the fingerprint every run to "look
different" actually manufactures the anomaly. This is also what makes a failed run
reproducible, since the same seed rebuilds the same machine. If you also want cookies and
local storage to carry across runs, keep the identity in [a persistent profile on disk](persistent-profiles.md)
rather than a fresh context each time.

**Vary the schedule.** Do not run it like [a cron a site can watch for](schedule-invisible-playwright-scrapes-with-cron.md).
Add jitter to the interval and randomize the minute so the run times do not form a straight
line. The seed stays put; the clock moves.

```python
import random
import time

BASE_INTERVAL = 3600          # target one run per hour on average
JITTER = 900                  # plus or minus fifteen minutes


def sleep_until_next_run():
    delay = BASE_INTERVAL + random.randint(-JITTER, JITTER)
    time.sleep(max(60, delay))


def run_forever():
    state = load_state()
    if state["seed"] is None:
        state["seed"] = random.randint(0, 2**31)   # chosen once, then kept
        save_state(state)

    while True:
        with InvisiblePlaywright(seed=state["seed"]) as browser:
            page = browser.new_page()
            changed = scrape_incremental(page, state)

        for kind, item_id, fields, sig in changed:
            fields["sig"] = sig
            state["seen"][item_id] = fields
        if changed:
            state["high_water_id"] = changed[0][1]
        save_state(state)

        print(f"{len(changed)} new/changed; sleeping with jitter")
        sleep_until_next_run()
```

The seed is drawn once and stored in the same state file as the high-water mark, so the
identity is as durable as the dataset it maintains. The sleep is what changes from run to
run. That is the split: continuity in who you are, variation in when you show up.

## A complete incremental run, start to finish

Putting the pieces in order, one run does exactly this:

1. Load state: the fixed seed, the high-water id, the map of seen ids to their signatures.
2. Launch with that seed, so this run's browser is the same machine as last run's.
3. Walk the feed newest-first, collecting new ids and edited ones, stopping after an
   unbroken run of `overlap` already-known unchanged items.
4. Update the high-water mark to the newest id seen and merge the changed items into state.
5. Sleep for the base interval plus jitter, not a fixed schedule.

The result reads the whole feed exactly once, on the very first run, and after that reads
only the top of it until it recognizes what it already has. A day of that is a few dozen
requests instead of a few hundred, from one steady identity, at times that do not line up
on a grid.

## Conclusion

Incremental scraping is one idea applied carefully: remember the newest thing you have, and
stop reading when you reach it. The care is in the edges. An overlap window keeps backdated
inserts from slipping under the boundary, a content signature catches edits to ids you
already hold, and separating a stable seed from a varied schedule keeps the smaller
footprint from turning into a recognizable rhythm.

Fewer requests is a real reduction in exposure. It is not, by itself, invisibility, and the
thing that makes it quiet rather than merely small is the discipline of showing up as the
same visitor at genuinely irregular times.

## Short answers to the questions that lead here

**How do I scrape only new items instead of the whole feed?** Keep the id of the newest
item you have as a high-water mark, walk the feed newest-first, and stop when you reach that
id. Everything above it is new.

**What if items arrive out of order?** Use an overlap window: do not stop at the first known
id, keep reading a few items past it, and only stop after an unbroken run of already-known
items. That absorbs a handful of backdated rows.

**How do I catch edits to items I already saved?** Store a short content hash next to each
id and compare it when the id repeats. A changed hash means the item was edited, so you
re-capture it even though the id is not new.

**Should I rotate the fingerprint on every run to look different?** No. A returning visitor
who looks the same over time is normal; a brand-new identity every visit is the anomaly.
Reuse one seed and vary the run timing instead.

**Does fewer requests mean I am harder to detect?** It helps, but a tiny fixed request on a
fixed clock is its own pattern. Add jitter to the schedule so the run times do not form a
straight line.

**Where do I store the high-water mark?** In a small state file alongside your seed and the
map of seen ids, loaded at the start of each run and saved at the end. Durable state is the
whole point.

## Sources

- This project's [quickstart](quickstart.md) and [configuration](configuration.md) pages for
  the real launch API and the seeded, reproducible identity used above.
- [Playwright's `Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for what a launched `Browser` object exposes once the wrapper hands it back.
- This project's own rate-limiting notes, where request velocity is treated as a scored
  signal rather than politeness, and the self-flag incident behind that rule.

**See also:** [how to rate limit your own scraper](how-to-rate-limit-your-scraper-playwright.md)
for the throttling that pairs with a small footprint, [crawling paginated feeds](how-to-scrape-paginated-pages-playwright.md)
for the page-turn logic under the boundary loop, and [persistent profiles](persistent-profiles.md)
for carrying cookies and storage across runs alongside the seed.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The high-water mark, the
overlap window and the varied schedule are all things a full-feed re-scrape taught me to
stop skipping.*
