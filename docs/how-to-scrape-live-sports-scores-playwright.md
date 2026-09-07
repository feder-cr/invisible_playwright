---
title: "How to scrape live sports scores with Playwright"
description: "Scrape live in-play sports scores with Playwright: read match state alongside the score, timestamp every update against its own clock, and accept that a score can go down after a VAR review."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 139
---


# How to scrape live sports scores with Playwright

To scrape live sports scores with Playwright, treat the score as one field inside a
larger, time-stamped update instead of a number you read off the page: capture match
state alongside the score so a 0-0 reading means something, timestamp each update against
the feed's own clock instead of your scrape time, allow the score to decrease after a
video review instead of rejecting it as bad data, and match commentary events to the
score using the event's own minute, not the moment you happened to scrape. Do that and a
90-minute match becomes an ordered, self-consistent log instead of a pile of numbers you
have to guess the order of.

This is a narrower problem than reading a scoreboard once or pulling post-match stats.
[Scraping sports stats and box scores](how-to-scrape-sports-scores-and-stats-playwright.md)
covers reading the feed transport itself, switching parsers between live and finished
formats, and holding a long session without tripping a rate check. This page assumes you
have already solved that part and asks the harder question underneath it: once you are
receiving updates, what makes an in-play score reading correct, not merely recent.

## Why a live score is a moving target, not a snapshot

A screenshot-style read, one DOM query at one instant, captures whatever the page
happened to be showing at that microsecond. The problem is that the number on screen is
built from an update cycle you do not control. A poll or a socket delivers new state
every few seconds, the page repaints on receipt, and your read lands somewhere inside
that gap. If the authoritative feed just ticked from 1-0 to 2-0 and the page has not
repainted yet, you read 1-0 and it is already wrong. If you read a beat later, you catch
the 2-0 but you have no way to know whether you caught it one second after it changed or
twenty.

The fix is not a faster poll. It is treating every value you read as "what the page
showed at time T", never as "the true state of the match", and carrying T alongside the
score everywhere downstream. Anything that discards the read timestamp turns a
reconstructible sequence into a bag of numbers with no reliable order.

```python
import time
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/live")

    readings = []
    for _ in range(120):
        score = page.locator("[data-live='score']").inner_text()
        # the read timestamp, not the match clock, is what you actually know
        readings.append({"read_at": time.time(), "score": score})
        page.wait_for_timeout(15_000)

    print(readings[-1])
```

That `read_at` field looks redundant until two readings disagree with what the commentary
feed says happened, and it is the only thing that lets you figure out which side was
stale.

## Read match state before you trust the score field

A score is meaningless without knowing what phase of the match produced it, and the
phase is a small, closed set: not started, live, half-time, finished, postponed,
abandoned. Every one of those states can show `0-0`, and they mean entirely different
things. A match that has not kicked off yet and a match abandoned after ten minutes both
read `0-0`, and a scraper that only looks at the score field cannot tell them apart.

Read the state field on every poll, next to the score, and gate what you do with the
score on it. A `0-0` under `not_started` is not data yet. A `0-0` under `abandoned` is
final and will never move again. Treating them the same corrupts anything downstream
that assumes a live match keeps updating.

```python
from invisible_playwright import InvisiblePlaywright

# the enum this feed actually uses; confirm it against the real page before trusting it
LIVE_STATES = {"live", "half_time"}
TERMINAL_STATES = {"finished", "postponed", "abandoned"}

def read_match(page):
    state = page.locator("[data-match-state]").get_attribute("data-match-state")
    home = page.locator("[data-live='home-score']").inner_text()
    away = page.locator("[data-live='away-score']").inner_text()
    return {"state": state, "home": home, "away": away}

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/live")

    record = read_match(page)
    if record["state"] in TERMINAL_STATES:
        print("match is over or void, score is final:", record)
    elif record["state"] in LIVE_STATES:
        print("match is live, keep polling:", record)
    else:
        print("nothing to read yet:", record)
```

Do not infer state from the score changing. A score that has not moved in twenty minutes
could be a defensive stalemate or a match that already ended and your parser did not
notice. State is a separate field for exactly this reason, and skipping it is how a
scraper reports a phantom goalless draw for a match that finished an hour ago.

## Added time breaks any fixed-duration assumption

The obvious model, "the match runs from minute 0 to minute 90, poll for that long and
stop," fails against almost every real match, because stoppage time is not fixed and is
not known ahead of time. The referee announces it at the end of each half, it commonly
runs from one to ten minutes, and it can extend further for a long injury stoppage. A
scraper that stops polling at minute 90 misses however many minutes get added, which in
practice means it misses a meaningful share of late goals.

The running clock and the added-time figure are usually two separate fields on the page,
not one combined number, so read both and let the state field, not a fixed budget, decide
when to stop.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/live")

    while True:
        state = page.locator("[data-match-state]").get_attribute("data-match-state")
        if state in ("finished", "postponed", "abandoned"):
            break

        clock = page.locator("[data-clock]").inner_text()          # e.g. "45"
        added = page.locator("[data-added-time]").inner_text()     # e.g. "+3", empty until announced
        print(clock, added)

        page.wait_for_timeout(15_000)
```

The `data-added-time` field is often empty for most of a half and only populated a few
minutes before the half's nominal end, because that is when the referee actually
communicates it to the broadcast feed. An empty added-time field is not missing data,
it is "not announced yet," and the loop above keeps running past 90 or 45 minutes for
exactly that reason: the state field, not the clock, is the authority on when the match
is over.

## A score can go down, and that is not corrupted data

It is tempting to write a validator that rejects any score decrease as a parsing error,
because in almost every other domain a monotonically increasing counter that drops is a
bug. Sports scores are the exception. A goal can be given, shown on the board, and then
disallowed after a video review, and when that happens the authoritative feed corrects
the score downward. A validator built on the "scores only go up" assumption will flag
every one of those corrections as bad data and either drop the correct value or alert on
a non-event, both of which are wrong.

The right invariant is not "the score never decreases." It is "a decrease has to be
paired with a state signal or an event entry that explains it," typically a
`var_review` or `goal_disallowed` marker in the same update or the commentary stream
around it. Validate the pairing, not the direction.

```python
def apply_update(history, new_reading):
    # scores read off a page are strings, and "10" < "9" lexicographically:
    # compare as numbers or every two-digit score reads as a decrease
    if history and int(new_reading["home"]) < int(history[-1]["home"]):
        # a drop with no explanation is suspicious; a drop next to a review flag is not
        if not new_reading.get("var_review"):
            print("unexplained score decrease, flag for review:", new_reading)
        else:
            print("goal overturned on review, accepting correction:", new_reading)
    history.append(new_reading)
    return history
```

Anything downstream, a running total, a live-odds model, a simple dashboard, has to be
built to accept a correction rather than assume the score field is append-only. Rejecting
decreases outright is the more common mistake, but silently accepting every decrease with
no check at all is the opposite failure: it lets a genuine transport glitch through as if
it were a real overturned goal.

## Match commentary events to their own clock, not your scrape time

Goals, cards and substitutions usually arrive on a separate timestamped stream from the
scoreboard number itself, an event feed instead of the score endpoint. Each event
carries its own minute, and that minute is what ties it to the score, not whatever time
your scraper happened to read either stream.

The mistake is joining the two streams on wall-clock scrape time: "I read the score
update and the event update within the same second, so they must be the same goal." They
frequently are not, because the two feeds can lag each other by a few seconds and your
poll interval adds its own slop on top. Join on the event's own declared minute instead.

```python
from invisible_playwright import InvisiblePlaywright

def read_events(page):
    # each event row carries its own match-minute, independent of when you scraped it
    rows = page.locator("[data-event-row]").all()
    events = []
    for row in rows:
        events.append({
            "minute": row.get_attribute("data-minute"),
            "type": row.get_attribute("data-event-type"),   # goal, card, substitution
            "team": row.get_attribute("data-team"),
        })
    return events

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/match/live")

    events = read_events(page)
    goals = [e for e in events if e["type"] == "goal"]
    # attach a score reading to a goal by the goal's OWN minute, never by scrape order
    for goal in goals:
        print(f"goal at minute {goal['minute']} by {goal['team']}")
```

If you need "the score right after this goal," look it up in your own history of
score readings by the closest read timestamp at or after the event's minute, and accept
that it will be approximate by however far apart your polls are spaced. That is a real
limitation of polling a public page, not a bug in your code, and it is worth stating
plainly rather than presenting an approximate join as an exact one.

## Where scraping stops being the right tool

Free public score pages throttle and delay their own updates on purpose, and paid
live-data feeds exist specifically to sell the latency a scraper is trying to claw back
for free. If the thing you are actually building needs sub-second accuracy, guaranteed
ordering, or a contractual freshness bound, a scraper reading a public page is competing
against a feed whose entire business is being faster and more reliable than that page,
and it will lose that race by design, not by a bug you can fix. Read [rate-limiting your
scraper](how-to-rate-limit-your-scraper-playwright.md) for how to hold a polite polling
rate, but recognize that a polite rate and a paid feed's latency are not the same number,
and no amount of stealth closes that gap.

Where scraping the public page is the right tool: a personal dashboard, an archive of
match events for later analysis, a project that tolerates being a few seconds behind. In
those cases everything above, state before score, added time as a variable not a
constant, corrections as normal, events joined on their own clock, is what makes the
result usable instead of misleading.

## Conclusion

A live score is not a number, it is a number attached to a state, a clock, and an
update time, and dropping any of those turns a correct reading into a misleading one.
Read match state before trusting the score field, because the same `0-0` means kickoff
in one state and a void match in another. Treat added time as unknown until the page
announces it, not as a fixed extension of 90 minutes. Let the score decrease when a
review overturns a goal, and validate the explanation rather than the direction. Join
commentary events to score readings by the event's own minute, not by when you happened
to scrape either stream. And know where the free page's own throttling makes scraping
the wrong tool for a job that actually needs a paid feed's latency guarantee.

## Short answers to the questions that lead here

**Why does my scraped score lag behind the actual match?** Because you are reading a
value the page repainted on its own update cycle, and your read can land in the gap
between two updates. Record the read timestamp with every score so you know how stale a
given reading might be, rather than assuming each read is current.

**How do I know if 0-0 means the match has not started or was abandoned?** You cannot
tell from the score alone. Read the match state field, a small enum such as not_started,
live, half_time, finished, postponed, abandoned, on every poll and gate your logic on it
rather than on the score changing.

**My scraper stops at the 90-minute mark and misses late goals. Why?** Because stoppage
time is variable and is not known until the referee announces it, usually shown in a
separate added-time field. Stop polling when the state field reports finished, not at a
fixed clock value.

**A score I scraped went down between two reads. Is my parser broken?** Not necessarily.
A goal disallowed after a video review lowers the authoritative score, and a feed that
reflects that correction is working correctly. Check for a review or disallowed-goal
marker alongside the decrease before treating it as bad data.

**How do I match a goal in the commentary feed to the score at that moment?** Use the
event's own minute, not the time you scraped either stream. The score feed and the event
feed are usually separate and can lag each other by a few seconds, so joining on your
scrape time introduces error the event's own clock does not have.

**Should I just poll faster to beat the lag?** No. A faster poll narrows the gap but
never closes it, and an aggressive poll rate is itself a signal a site can act on. If you
need sub-second accuracy, a scraper reading a public page is the wrong tool; that
latency is what paid live-data feeds are built and sold to provide.

## Sources

- Playwright's [`page.locator`](https://playwright.dev/python/docs/locators),
  [`get_attribute`](https://playwright.dev/python/docs/api/class-locator#locator-get-attribute)
  and [`wait_for_timeout`](https://playwright.dev/python/docs/api/class-page#page-wait-for-timeout),
  used exactly as documented upstream, retrieved 2026-08-28.
- The real `invisible_playwright` API used throughout: `InvisiblePlaywright(seed=...)`
  returns a standard Playwright `Browser`. See [Quickstart](quickstart.md) and
  [Configuration](configuration.md).
- This project's own sibling article on the transport layer underneath a live score,
  [scraping sports stats and box scores](how-to-scrape-sports-scores-and-stats-playwright.md),
  for reading the WebSocket or polling XHR itself.

**See also:** [scraping sports stats and box scores](how-to-scrape-sports-scores-and-stats-playwright.md)
for reading the feed transport and holding the session, [capturing XHR and API
responses](how-to-capture-xhr-api-responses-playwright.md) for the request-level
mechanics both articles rely on, [rate-limiting your scraper](how-to-rate-limit-your-scraper-playwright.md)
for pacing a poll that runs for a whole match, and [waiting for a page to
load](how-to-wait-for-page-load-playwright.md) for replacing a blind sleep with a wait
on a real signal.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of a
score validator here rejected a decrease as a parsing bug; the decrease was a goal
overturned by video review, and the fix was reading the state field, not tightening the
check.*
