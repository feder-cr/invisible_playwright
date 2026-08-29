---
title: "How to resume an interrupted scrape with Playwright"
description: "Resume an interrupted Playwright scrape: write a durable checkpoint, skip completed work, re-validate the boundary item, and reload the same seeded identity."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 74
---


# How to resume an interrupted scrape with Playwright

To resume an interrupted Playwright scrape, write a small durable checkpoint after each
unit of work, then on restart skip the work already done, re-validate the boundary item,
and relaunch with the same seed so the site sees one returning visitor rather than a new
one. Stock Playwright gives you the checkpoint and the resume path; a seeded fingerprint
keeps the resumed run continuous.

A long crawl dies partway through. A process crash, a dropped connection, a block, a
machine that got rebooted under you. The question is what happens when you start it
again, and there are only three answers: it redoes everything, it loses the progress it
had, or it picks up exactly where it stopped. The first two are the default. The third
takes a durable checkpoint and a resume path, and this page is how to build both with
stock Playwright.

There is a second failure most guides miss. If every restart launches a fresh browser
identity, the site does not see one job that hiccuped. It sees a new visitor appear each
time the job restarts, which is a pattern in its own right. The fix for that is the same
lever this project uses everywhere: a seeded, reproducible fingerprint, so a resumed run
is the same visitor coming back rather than a new one arriving.

## Why a naive restart is the worst of both options

A naive restart either redoes everything or loses all progress, and both cost you the
whole run so far. What you want instead is a narrow, durable record of the last unit you
finished, so a single interruption costs one item and not hours.

Say you are walking a paginated listing, one page at a time, writing rows as you go. The
job stops on page 240 of 900.

If your code starts from page 1 every time, a crash near the end throws away hours and
hammers the site with requests it already served you. If instead your code holds progress
only in memory, the crash took the progress with it and you are back to page 1 anyway. In
both cases the cost of a single interruption is the whole run so far.

What you actually want is narrow: a small, durable record of the last thing you finished,
written often enough that a crash costs you one item and not the run. Everything below is
built around that record.

## Write a durable checkpoint as you go

A checkpoint is the smallest piece of state that lets you answer "where do I resume". For
a paginated crawl it is the last page number and the last item ID on it. For a cursor API
it is the cursor token the server handed you. For a list of URLs it is the set of URLs
already done.

Two rules make it durable rather than decorative. Write it incrementally, after each unit
of work, not once at the end where a crash guarantees you never reach it. And write it
atomically, so a crash in the middle of writing the file cannot leave you with a
half-written checkpoint that is worse than none.

```python
import json
import os
import tempfile
from pathlib import Path

CHECKPOINT = Path("checkpoint.json")

def save_checkpoint(state: dict) -> None:
    # Write to a temp file in the same directory, then atomically replace.
    # A crash mid-write leaves the old checkpoint intact, never a truncated one.
    fd, tmp = tempfile.mkstemp(dir=str(CHECKPOINT.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CHECKPOINT)   # atomic on Windows and POSIX
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    return {"seed": 42, "last_page": 0, "last_item_id": None, "done_ids": []}
```

The `seed` living inside the checkpoint is deliberate and the next two sections are why.

## Resume by skipping completed work and re-validating the boundary

On restart, load the checkpoint and start after the last confirmed unit. The trap is the
boundary item: the last thing you recorded may have been written half-way when the process
died. Trusting it blindly duplicates or corrupts one row on every resume, and those are the
rows you will never notice until much later.

So the resume path does two things. It skips everything up to and including the last fully
completed unit, and it re-reads the boundary unit before continuing, comparing it to what
the checkpoint claims. If they disagree, the boundary is where you resume, not the item
after it.

```python
from invisible_playwright import InvisiblePlaywright

def scrape(page, page_number):
    page.goto(f"https://example.com/listing?page={page_number}")
    page.wait_for_selector(".item")
    return [
        {
            "id": el.get_attribute("data-id"),
            "title": el.inner_text(),
        }
        for el in page.query_selector_all(".item")
    ]

def run():
    state = load_checkpoint()
    start_page = state["last_page"]          # re-validate the boundary page...
    if state["last_page"] > 0:
        start_page = state["last_page"]      # ...by re-reading it, not skipping past it
    else:
        start_page = 1

    with InvisiblePlaywright(seed=state["seed"]) as browser:
        page = browser.new_page()
        done = set(state["done_ids"])

        for page_number in range(start_page, 901):
            rows = scrape(page, page_number)
            for row in rows:
                if row["id"] in done:
                    continue             # already recorded on a previous run
                write_row(row)           # your durable sink: DB, file, queue
                done.add(row["id"])

            state.update(last_page=page_number,
                         last_item_id=rows[-1]["id"] if rows else state["last_item_id"],
                         done_ids=sorted(done))
            save_checkpoint(state)       # incremental: one page of loss on a crash, at most
```

`done_ids` doing the dedup means the boundary page can be re-read safely: rows already
written are skipped by ID, so re-validating costs you a re-fetch and never a duplicate. For
larger runs, keep `done_ids` in a set-backed store (a SQLite table, a Redis set) rather than
a growing JSON array, but the shape is identical.

If a page fails transiently rather than fatally, resuming the whole process is the wrong
granularity. Retry the single request in place first, and only fall back to a checkpointed
restart when the retry budget is spent. That inner loop is its own topic:
[retrying failed requests without restarting the run](how-to-retry-failed-requests-playwright.md).

## Resume the same seeded identity, so the site sees one visitor

Resume with the same seed and the site sees one returning visitor, not a new one every
restart. This is the part specific to this product rather than to checkpointing in general.

Every session generated by `InvisiblePlaywright` comes from a seed. Pass one and every
field it implies - GPU, canvas hash, audio context, fonts, screen - comes back identical,
run after run. That is why the checkpoint above carries the seed: a resumed run loads it
and relaunches the same machine.

From the site's side, that changes what a restart looks like. Without a fixed seed, each
restart is a browser it has never seen, appearing at the same account or the same crawl
pattern, minutes apart. A brand-new fingerprint materialising every time a job restarts is
a velocity signal a detector can key on directly. With a fixed seed, the second run
presents the same fingerprint as the first, so the linkable identity a fingerprinting
service computes is the same identity - one visitor who paused and came back.

You can measure this directly rather than taking it on faith. Launch with `seed=42`, read a
[FingerprintJS](fingerprintjs-visitor-id.md) visitor ID, close, relaunch with the same seed,
and read it again:

```python
def visitor_id(seed):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com/fingerprint-probe")
        page.wait_for_function("window.__visitorId !== undefined")
        return page.evaluate("window.__visitorId")

first = visitor_id(42)
second = visitor_id(42)    # a separate process would load seed=42 from the checkpoint
assert first == second     # same visitor ID: one continuous visitor across the restart
```

The two IDs match because the whole fingerprint matched, not because one field was pinned.
That is the same property the [reproducible-fingerprint quickstart](quickstart.md) leans on
for debugging, used here to keep a resumed job continuous. If you need one specific field
held constant while the rest stays seed-derived - a fixed GPU model, a fixed screen - that
is what [pinning fingerprint fields](pinning.md) is for.

## Keep the identity attached to the run, not to the process

A seed pins the fingerprint. It does not pin the parts of a real returning visitor that live
outside the fingerprint: cookies, local storage, the login session. If those reset on every
restart while the fingerprint stays constant, you get a different contradiction - the same
machine that has apparently never been logged in before. For a job that is supposed to look
like a returning visitor, persist that state too, with a
[persistent profile on disk](persistent-profiles.md), and resume it alongside the checkpoint.

The general rule: everything that identifies the visitor should be reloaded together on
resume. The seed reloads the fingerprint, the profile reloads the browser state, the
checkpoint reloads your position in the work. Reload one without the others and the seams
show.

## The honest caveat: do not resume a burned identity forever

Reusing the same seed is right when the interruption was mechanical - a crash, a reboot, a
dropped link. It is the wrong move when the interruption was the site itself.

If a run stopped because that identity was flagged or challenged, resuming with the same
seed resumes straight back into the same wall, because you are presenting the exact
fingerprint that was flagged. And an identity that persists unchanged across a very large
number of sessions is itself linkable over time, which is a different kind of tell from the
one this page solves.

So the decision is two-branch, and worth recording in the checkpoint. If the stop was
mechanical, resume the same seed. If the stop was a block, rotate to a new seed for a fresh
identity and treat the blocked segment as a new visitor, not a continuation. A block is
about more than the fingerprint anyway - the exit IP and behaviour matter as much - and the
[checklist for a session getting detected](playwright-detected-as-bot.md) is the order to
work that in.

## Conclusion

A resumable scrape is three durable records reloaded together: your position in the work
(the checkpoint, written incrementally and atomically, with the boundary item re-validated
on resume), the browser state (a persistent profile), and the identity (a fixed seed). Get
the checkpoint right and one interruption costs one item instead of the whole run. Get the
seed right and the restart looks like one visitor returning rather than a new one appearing
every few minutes - which is the difference between a job that recovers quietly and one that
announces every crash to the site it is crawling.

## Short answers to the questions that lead here

**How do I resume a Playwright scrape after a crash?** Write a small checkpoint (last page
or cursor, plus the IDs already done) after each unit of work, atomically. On restart load
it, skip completed work by ID, re-read the boundary item to confirm it, and continue.

**Where should the checkpoint be written?** Anywhere durable that survives the process:
a file replaced atomically, a database row, a queue offset. The key is writing it
incrementally as you go, not once at the end.

**Why re-validate the boundary item instead of just skipping past it?** Because the last
item you recorded may have been half-written when the process died. Re-reading it and
deduplicating by ID means a resume costs a re-fetch, never a duplicate or a gap.

**Does restarting the job give me a new fingerprint every time?** Only if you let it. Pass
the same `seed` on resume and the fingerprint is identical, so the site sees one returning
visitor instead of a new one each restart. Store the seed in the checkpoint.

**Should I always resume with the same seed?** Resume the same seed after a mechanical stop
(crash, reboot). After a block, rotate to a new seed - resuming the flagged identity just
resumes into the same block.

**Do I need to persist cookies and login state too?** Yes, if the visitor is supposed to be
logged in or returning. A fixed fingerprint with a wiped session is its own contradiction;
persist the profile alongside the checkpoint.

## Sources

- The product's reproducible-fingerprint behaviour: the same seed yields the same GPU,
  canvas hash, audio context, fonts and screen every run, verified by reading a
  FingerprintJS visitor ID before and after a relaunch and confirming it matches.
- This project's own debugging practice of pinning the identity across runs so a failure is
  replayable rather than a fresh random draw each time.
- FingerprintJS's own open-source library, [`fingerprintjs/fingerprintjs`](https://github.com/fingerprintjs/fingerprintjs)
  on GitHub, retrieved 2026-08-28, the visitor-ID check read in the verification example
  above.
- Playwright's own documentation, [`Page.wait_for_function()`](https://playwright.dev/python/docs/api/class-page),
  retrieved 2026-08-28, used to poll for the visitor ID before reading it.

**See also:** [retrying failed requests without restarting the run](how-to-retry-failed-requests-playwright.md),
[persistent profiles on disk](persistent-profiles.md), and
[pinning fingerprint fields](pinning.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The checkpoint-and-same-seed
pattern is how a long run recovers from a crash without looking like a new visitor each
time it restarts.*
