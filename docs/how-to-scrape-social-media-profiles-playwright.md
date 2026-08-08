---
title: "How to scrape social media profiles with Playwright"
description: "Scrape a virtualized profile feed in Playwright: extract each batch before it scrolls out of the DOM, parse abbreviated counts, and reuse a saved session."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 45
---


# How to scrape social media profiles with Playwright

A profile page looks like a simple list of posts. It is one of the harder things to
scrape correctly, for three reasons that are all invisible until you hit them: the feed
is virtualized so most of it is never in the DOM at once, the numbers you want are
abbreviated and lazy-loaded, and almost everything worth reading sits behind a login.

This page is the extraction pattern that survives virtualization, how to parse the
abbreviated counts, how to reuse a logged-in session instead of authenticating every run,
and one honest caveat about what a fingerprint does and does not buy you on these
platforms.

## Why a profile feed is harder than it looks

A profile feed is hard to scrape correctly because of three traps that stay invisible until
you hit them: the feed is virtualized so most posts are never in the DOM at once, the counts
you want are abbreviated and lazy-loaded, and most of the page sits behind a login wall.
Each trap defeats a different piece of naive code, and each has its own fix.

| Trap | Why it breaks naive code | The fix |
|---|---|---|
| Virtualized feed | Only posts near the viewport are mounted; scrolled-past nodes are removed, so a final `query_selector_all` sees about a dozen | Read each batch as it mounts and dedupe on a stable post ID |
| Abbreviated, lazy-loaded counts | `1.2M` is a rounded string painted a beat after first paint, so you cannot sort, sum or threshold on it | Wait for the element, then parse the suffix back to an integer magnitude |
| Login wall | An unauthenticated session gets a truncated page or a wall | Authenticate once, save the session state, and reuse it with the same seed |

Start with the virtualization trap, because it is the one that silently returns wrong data
instead of an obvious error. Open a profile, scroll to the bottom, then count the posts. The
obvious code gets the obvious wrong answer:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/profile/handle")

    # scroll a long way down, then read everything - this is the mistake
    for _ in range(20):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(600)

    posts = page.query_selector_all("article")
    print(len(posts))   # still about a dozen, no matter how far you scrolled
```

The feed is virtualized. Only the handful of posts near the viewport are ever mounted;
as you scroll down, the ones above are removed from the DOM and recycled. So
`query_selector_all` at the end sees the last dozen nodes, not the hundreds you scrolled
past. The posts you wanted were rendered, read by your eyes, and destroyed before your
code asked for them.

Two more things are true of nearly every profile page: the follower and post counts are
lazy-loaded and abbreviated (`1.2M`, not `1203994`), and most of the page returns a login
wall to a session that is not authenticated. Each needs its own handling, below.

## Extract each batch before it scrolls out of the DOM

The fix is to invert the loop. Do not scroll all the way and then read; read what is
currently mounted, then scroll one step, then read again, keeping a running set keyed on a
stable per-post identifier so recycled nodes do not create duplicates.

```python
from invisible_playwright import InvisiblePlaywright


def scrape_feed(page, max_posts=200, patience=3):
    seen = {}
    stagnant = 0
    while len(seen) < max_posts and stagnant < patience:
        batch = page.evaluate(
            """() => Array.from(document.querySelectorAll('article')).map(a => ({
                id: a.getAttribute('data-post-id'),
                text: a.innerText,
            }))"""
        )
        before = len(seen)
        for post in batch:
            if post["id"]:
                seen[post["id"]] = post

        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(800)

        # stop when several scrolls in a row add nothing new
        stagnant = 0 if len(seen) > before else stagnant + 1
    return list(seen.values())


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/profile/handle")
    posts = scrape_feed(page)
    print(len(posts), "posts captured")
```

The pattern that makes this work:

- **Read the mounted nodes every iteration**, not once at the end. Each `evaluate` call
  captures the batch that exists right now.
- **Key on a stable identifier.** A post ID attribute survives node recycling; array
  position does not. Deduplicating on the ID is what turns overlapping batches into one
  clean list.
- **Scroll in modest steps.** A single jump to the bottom can skip a whole batch that is
  mounted and unmounted between two frames, so you never see it. Smaller deltas keep every
  batch on screen long enough to read.
- **Stop on stagnation, not on a fixed count.** When several consecutive scrolls add no
  new IDs, you have reached the end of the loaded feed. This is the general shape of
  [scraping any infinite-scroll list](how-to-scrape-infinite-scroll-playwright.md), and
  profile feeds are just an infinite-scroll list with an ID attribute to dedupe on.

Because the whole identity is derived from `seed=42`, a run that misses a batch is
reproducible: rerun the same seed and you get the same machine and the same feed order, so
you can tell a virtualization bug in your loop from the site simply changing between runs.

## Parse the abbreviated counts

Follower counts, post counts and like counts render abbreviated. `948`, `12.3K`, `1.2M`,
`3.4B`. You cannot sort, sum or threshold on those strings, so parse them back to integers
on the way in:

```python
def parse_count(text):
    text = text.strip().upper().replace(",", "")
    multiplier = 1
    if text.endswith("K"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("M"):
        multiplier, text = 1_000_000, text[:-1]
    elif text.endswith("B"):
        multiplier, text = 1_000_000_000, text[:-1]
    return int(float(text) * multiplier)


parse_count("948")     # 948
parse_count("12.3K")   # 12300
parse_count("1.2M")    # 1200000
```

Two things to know before you trust the number. The abbreviated form is lossy: `1.2M` is
anything from 1,150,000 to 1,249,999, so treat a parsed abbreviation as a magnitude, not
an exact figure. And the counts are frequently lazy-loaded, painted a beat after the rest
of the header, so read them only after they are present rather than at first paint:

```python
page.wait_for_selector("[data-testid='follower-count']")
raw = page.inner_text("[data-testid='follower-count']")
followers = parse_count(raw)
```

If you need the exact figure rather than the rounded one, it sometimes rides in a title
attribute or a JSON blob in the page source; read that when it exists and fall back to the
parsed abbreviation when it does not.

## Reuse a logged-in session instead of logging in each run

Most of a profile is gated. An unauthenticated session gets a truncated page or a wall,
so you need to be logged in, and you do not want to drive the login form on every run. The
login form is the most heavily monitored flow on these sites, and
[reusing a saved session avoids it entirely](automating-login-vs-session-reuse.md).

Authenticate once and save the session state:

```python
# run once, interactively, using the identity you intend to keep
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/login")
    input("log in in the window, then press Enter here...")
    context.storage_state(path="session.json")
```

Then every scraping run starts already authenticated, with no username field, no password
field and no submit click:

```python
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(storage_state="session.json")
    page = context.new_page()
    page.goto("https://example.com/profile/handle")
    posts = scrape_feed(page)
```

The condition that makes this safe is that the fingerprint stays paired with the session.
A cookie proves who logged in; it does not prove the request came from the same browser.
Replaying `session.json` from `seed=99` describes an account whose GPU, fonts and canvas
output changed overnight, which is exactly the cross-session inconsistency detectors look
for. Keep the seed that created the session, or go one step further and
[pin one profile directory to one seed](persistent-profiles.md) so the identity that
logged in is byte-for-byte the identity that scrolls the feed later. For the broader gating
question, see [how to scrape content behind a login](how-to-scrape-behind-login-playwright.md).

## Make the scrolling look human, the honest caveat

A good fingerprint removes the automated-browser tell, but it does not make robotic
behaviour look human. These platforms score behaviour separately from the fingerprint, so
realistic hardware still needs a realistic scroll cadence on top of it. This is where the
product's boundary has to be stated plainly, because a page that promotes by demonstration
should also tell you what the demonstration does not cover.

Social platforms run the strictest detection of anywhere on the web. A seed-reproducible,
C++-level fingerprint driven by stock Playwright removes the automated-browser tell: the
engine answers CreepJS, BotD and the rest as an ordinary Firefox on ordinary hardware, and
because the machine is derived from a seed it is internally consistent rather than a set of
plausible values that disagree with each other. That is what keeps a session from being
flagged the moment it starts scrolling a stranger's profile.

What it does not do is make robotic behaviour look human. These platforms also score
behaviour, and a loop that scrolls a uniform 3000 pixels every 800 milliseconds is a
metronome no person produces. Vary the deltas and the dwell, and let the pointer travel
rather than teleport:

```python
import random

def human_scroll(page, steps):
    for _ in range(steps):
        page.mouse.wheel(0, random.randint(1800, 3200))
        page.wait_for_timeout(random.randint(500, 1500))
```

`InvisiblePlaywright` already arcs the pointer on a Bezier curve when you click, so the
cursor tells the right story on interaction; the scroll cadence and the dwell between
posts are still yours to shape. See [human-like pointer motion](human-mouse-movement.md)
for what the behavioural layer checks. And no amount of realism repeals account-level
limits: request volume, session age and how many profiles one account views in an hour are
scored per account, independently of how real the browser looks.

## Conclusion

A profile feed is three problems wearing one page. Virtualization means you read each
batch as it mounts and dedupe on a stable ID, never scroll-then-read. Abbreviated counts
mean you parse `1.2M` back to a magnitude and wait for the lazy load. The login wall means
you save a session once and reuse it, with the fingerprint kept paired to it.

The fingerprint removes the automated-browser tell and keeps it removed across runs
because it comes from a seed. The behaviour and the account limits are still yours to
respect. Get the extraction pattern right and the seed does the reproducibility, so a run
that fails is a run you can replay exactly rather than guess at.

## Short answers to the questions that lead here

**Why does querySelectorAll only return a dozen posts after I scroll?** The feed is
virtualized. Only the posts near the viewport are mounted; the rest are removed from the
DOM as you scroll. Read each batch as it appears instead of reading once at the end.

**How do I get all the posts, not just the visible ones?** Loop: read the mounted nodes,
scroll one step, read again, keeping a set keyed on a stable post ID. Stop when several
scrolls in a row add nothing new.

**How do I convert "1.2M" to a number?** Strip the suffix, multiply by 1,000 / 1,000,000 /
1,000,000,000, and treat the result as a magnitude - the abbreviation is rounded, so it is
not exact.

**Do I have to log in for every run?** No. Authenticate once, save `storage_state`, and
load it into each run. Keep the same seed, because the session and the fingerprint that
created it are a pair.

**Will a good fingerprint stop me being flagged?** It removes the automated-browser tell
and keeps the machine consistent across runs. It does not make robotic scrolling look
human, and it does not lift account-level rate limits.

**Why reproduce the run with a seed?** Because a virtualization bug in your loop and the
site changing between visits look identical unless the identity is fixed. Same seed, same
machine, same feed order, so a failure is replayable.

## Sources

- Playwright's own [`query_selector_all`](https://playwright.dev/python/docs/handles),
  [`evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate),
  [`mouse.wheel`](https://playwright.dev/python/docs/api/class-mouse#mouse-wheel) and
  [`storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state)
  APIs, used exactly as documented upstream.
- This project's seed-derived fingerprint and Bezier pointer motion, described in
  [Quickstart](quickstart.md) and [Configuration](configuration.md).
- The session-reuse and persistent-profile pages linked throughout, for the identity half
  of a logged-in scrape.

**See also:** [scraping an infinite-scroll list](how-to-scrape-infinite-scroll-playwright.md)
for the general form of the extract-as-you-go loop,
[why reusing a session beats automating the login form](automating-login-vs-session-reuse.md)
for the authentication half, and [human-like pointer motion](human-mouse-movement.md) for
the behaviour these platforms score.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The virtualized-feed trap is
one we hit reading our own measurements before we wrote it down here.*
