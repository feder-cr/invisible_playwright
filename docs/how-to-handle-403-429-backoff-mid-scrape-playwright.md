---
title: "Handle 403 and 429 backoff mid-scrape in Playwright"
description: "Read 403 and 429 off Playwright's response event, honor Retry-After, back off on 429 and slow a 403 on the same identity instead of churning a new fingerprint."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 76
---


# Handle 403 and 429 backoff mid-scrape in Playwright

To handle a 403 or 429 mid-scrape in Playwright, read the status off the `response`
event, honor any `Retry-After` header, exponentially back off a 429 and pause-then-slow
a 403 - all on the same identity - and rotate the fingerprint only as a deliberate
between-session decision, never as a per-request reflex.

A 403 or a 429 that arrives in the middle of a crawl is not a transient error like a
dropped connection or a slow DNS lookup. It is a state change. The site has looked at
this session and made a decision about it, and that decision does not un-make itself
because you sent the same request again a second later.

This is the distinction that most retry code gets wrong. A timeout is worth retrying
unchanged, because nothing decided anything. A 403 is a verdict, and retrying a verdict
on the same identity, at the same rate, from the same address, only confirms to the
other side that the thing it just flagged is still here and still hammering.

This page is how to read that status off Playwright's own events, how to honor a
`Retry-After` header, why 429 gets exponential backoff while a hard 403 gets a pause and
a slowdown, and why the fix is almost never to spawn a fresh identity per request.

## Read the status off the response, not off exceptions

Playwright does not raise on a 403 or a 429. `page.goto()` returns a `Response` object
whether the server answered 200 or 429, and for the sub-requests a page fires, the status
only exists on the `response` event. So the first job is to actually look at it.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    # every response the page receives, main document and sub-requests alike
    page.on("response", lambda r: print(r.status, r.url) if r.status in (403, 429) else None)

    response = page.goto("https://example.com/listing")
    print("main document:", response.status)
```

The `response` event is the reliable place to catch a mid-crawl block, because a site
that lets the HTML through and then blocks an XHR the page depends on will hand you a 200
on `goto()` and a 429 three requests later. If you only inspect the return value of
`goto()`, you never see it.

The `seed=42` above is deliberate: it pins the fingerprint so the whole run is one stable
identity. That matters here for a reason the next sections build on, and it is the same
reason a [reproducible seed makes a failing run debuggable](quickstart.md) instead of a
guess.

## Honor Retry-After before you invent your own delay

When a server sends 429, it very often tells you exactly how long to wait, in the
`Retry-After` header. That header is either a number of seconds or an HTTP date. Reading
it costs nothing and it is the single most useful signal in the response, because it is
the site telling you the pace it will accept instead of you guessing.

```python
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone


def retry_after_seconds(response, default=None):
    """Return how many seconds Retry-After asks for, or `default` if absent."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return default
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    try:
        when = parsedate_to_datetime(raw)      # HTTP-date form
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))
    except (TypeError, ValueError):
        return default
```

If `Retry-After` is present, that number wins over any backoff curve you would otherwise
compute. You do not get to negotiate it down. Only when the header is absent do you fall
back to a delay of your own.

## Back off on 429, slow down on 403 - and stay on the same identity

429 and 403 are different messages and deserve different handling: back off
exponentially on a 429, pause and slow the whole crawl on a 403, and keep the same
identity through both, because the identity was rarely the actual problem.

| Status | What it means | How to respond | Same identity? |
|---|---|---|---|
| 429 | Too fast - a rate signal | Honor `Retry-After`, else exponential backoff with jitter | Yes; the pace was the problem, not the identity |
| 403 | Refused - the session was categorized | Pause, back off deeper than a 429, slow the whole crawl | Yes; confirm exit and behavior first, rotate only between sessions |

**429 means "too fast".** It is explicitly about rate, so the response is exponential
backoff: wait, and if it happens again wait longer, up to a ceiling. This is the textbook
case where retrying the same identity is correct, because the identity was never the
problem, the pace was.

**403 means "no".** It is less specific and more serious. A hard 403 mid-crawl says the
session has been categorized. The right move is to stop pushing, back off further than you
would for a 429, and slow the whole crawl down rather than firing the next request into
the same wall.

```python
import time
import random


def crawl_with_backoff(page, urls, max_attempts=5):
    base, ceiling = 2.0, 120.0   # seconds

    for url in urls:
        for attempt in range(max_attempts):
            response = page.goto(url, wait_until="domcontentloaded")
            status = response.status

            if status < 400:
                yield url, response
                time.sleep(random.uniform(1.5, 4.0))   # pace even on success
                break

            wait = retry_after_seconds(response)
            if wait is None:
                # exponential backoff with jitter; 403 starts one step deeper
                step = attempt + (1 if status == 403 else 0)
                wait = min(ceiling, base * (2 ** step)) * random.uniform(0.8, 1.2)

            print(f"{status} on {url}, waiting {wait:.0f}s (attempt {attempt + 1})")
            time.sleep(wait)
        else:
            print(f"giving up on {url} after {max_attempts} attempts")
```

Two things in there are load-bearing. The `random.uniform` jitter keeps a fleet of
workers from retrying in lockstep and rebuilding the exact velocity spike that got them
blocked. And the pause after a *successful* request is not optional politeness: a crawl
that only slows down when it is already blocked is a crawl that spends its whole life at
the edge of the limit. Pacing that you [budget in advance](how-to-rate-limit-your-scraper-playwright.md)
beats backoff you apply after the fact.

Notice what this loop does *not* do: it does not throw the identity away and build a new
one on every block. That instinct is covered next, because it is usually the wrong one.

## Why churning a fresh identity per request is itself a tell

The tempting reaction to a 403 is to conclude the fingerprint is burned and spin up a
brand new one. On a browser that already passes the detector gates, that reaction usually
makes things worse.

This engine is built so that a fresh session produces a full, self-consistent desktop
fingerprint - GPU, audio, fonts, screen, hundreds of fields - that holds up against the
public tampering and consistency suites (CreepJS, BotD, FingerprintJS, sannysoft,
BrowserLeaks). When the fingerprint is already clean, a mid-scrape 403 is far more likely
to be *behavior or rate* than *fingerprint*. It is the velocity, the request pattern, or
the exit address that got categorized, not the shape of the browser.

Rotating a fresh identity per request does nothing for a rate problem, because the rate
is unchanged. What it does do is create a new signal: a stream of requests from one
address, each presenting a different machine, is a pattern real users never produce. One
person is one browser for the length of a session. A thousand distinct browsers behind a
single IP in a minute is a louder tell than the 403 you were trying to escape.

So the ordering is: honor `Retry-After`, back off, slow the crawl, and confirm the exit
is not the actual problem before you touch the identity at all. Identity rotation is a
between-session decision made deliberately, not a per-request reflex. If a manual visit
from the same machine gets the same 403, the browser was never the issue and
[none of this list touches the real cause](playwright-detected-as-bot.md).

## Catch blocks on sub-requests with route interception

For sites where the block lands on a background request rather than the top-level
navigation, the `response` event is enough to observe it, but `route` lets you both
observe and decide per request - useful when you want to pause the whole page the instant
a 429 shows up on any request it makes.

```python
def install_backoff_guard(page, state):
    def handle(route):
        response = route.fetch()          # perform the request, inspect the result
        if response.status in (403, 429):
            wait = retry_after_seconds(response, default=30)
            state["cooldown_until"] = time.time() + wait
            print(f"{response.status} on sub-request, cooling down {wait}s")
        route.fulfill(response=response)

    page.route("**/*", handle)
```

Keep the handler cheap. Intercepting every request has overhead, and a slow handler
becomes a behavioral tell of its own by injecting latency no real network has. Match only
the paths you care about (`page.route("**/api/**", handle)`) rather than `**/*` when you
can. Handling the block cleanly is one half of the job; the other half is
[retrying the failed request without amplifying the pattern](how-to-retry-failed-requests-playwright.md).

## Conclusion

Treat a mid-scrape 403 or 429 as information, not as an error to paper over. Read the
status off the `response` event so you actually see blocks that land on sub-requests.
Honor `Retry-After` when it is there, because it is the site telling you its terms.
Exponential-backoff a 429 and slow down harder on a 403, both on the *same* identity, with
jitter so a fleet does not retry in lockstep. And resist the urge to churn a fresh
fingerprint per request: on a browser that already passes the detector suites, a block is
usually pace or address, and a new identity every request is a pattern of its own. Backoff
and pacing are the fix; identity rotation is a deliberate between-session move, not a
reflex.

## Short answers to the questions that lead here

**Should I retry a 403 the same way I retry a timeout?** No. A timeout decided nothing, so
retry it unchanged. A 403 is a decision about this session, so retrying it identically just
confirms you. Back off and slow down instead.

**Does Playwright throw on a 403 or 429?** No. `page.goto()` returns a `Response` with the
status set, and sub-request blocks only appear on the `response` event. You have to read
the status; nothing raises.

**What is Retry-After and do I have to obey it?** It is the server telling you how long to
wait, in seconds or as an HTTP date. When it is present it should win over any backoff you
compute yourself. Ignoring it just earns the next block faster.

**Should I get a new fingerprint every time I hit a 403?** Almost never. On a browser that
already passes the detector gates, a mid-scrape 403 is usually rate or exit address, not
fingerprint. A new identity per request is a loud, unnatural pattern in its own right.

**How do I tell a rate block from a fingerprint block?** A 429, or a 403 that clears after
you slow down, is rate. A 403 that a manual visit from the same machine and IP also gets is
not about the browser at all. Change one thing at a time and watch which one moves it.

**What backoff numbers should I start with?** A base around 2 seconds doubling to a ceiling
of a minute or two, with plus or minus 20% jitter, and a pause between successful requests
too. Tune from there against what the site actually tolerates.

## Sources

- [Playwright's network guide and `Response` API](https://playwright.dev/python/docs/network),
  covering `page.on("response")` and `page.route()`, read from the upstream documentation
  rather than paraphrased.
- The [HTTP `Retry-After` header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Retry-After)
  definition, both the delay-seconds and HTTP-date forms.
- This project's release gates, which is where the observation lives that a browser passing
  the public tampering and consistency suites turns most mid-scrape blocks into a pacing
  problem rather than a fingerprint one.

**See also:** [rate-limiting your scraper before you get blocked](how-to-rate-limit-your-scraper-playwright.md),
[retrying failed requests without amplifying the pattern](how-to-retry-failed-requests-playwright.md),
and [the broader checklist for scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The habit of reading the
status off the response event instead of only the goto() return value is one that cost a
crawl a full afternoon of silent partial data first.*
