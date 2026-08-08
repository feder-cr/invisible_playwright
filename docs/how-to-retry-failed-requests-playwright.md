---
title: "How to retry failed requests when scraping Playwright"
description: "Retry failed Playwright requests with a total time-and-attempt budget, not naive per-attempt backoff, and see why aggressive retries raise a velocity signal."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 17
---


# How to retry failed requests when scraping Playwright

To retry a failed request in Playwright without turning your scraper into one that hangs,
bound the total time and the total number of attempts, not just each attempt, and hand
each attempt whatever is left of the total budget. That one change is the whole point of
this page.

A retry loop is the first thing everyone adds and the last thing anyone measures. The
usual shape is a `for` loop with a per-attempt timeout and an exponential sleep between
tries, and it looks correct because every individual piece is bounded. The whole loop
often is not, and that gap is where a "retry" quietly turns into a request that hangs for
minutes and a scraper that never finishes.

The rule comes from a bug we shipped and fixed, and this page ends with the reason a more
aggressive retry policy can get you blocked faster rather than scraped faster.

## The bug that is really a retry lesson

We once had an intermittent slow launch: one launch in six spent up to 35 seconds on a
step that was supposed to be quick. The step resolved a value by trying three network
endpoints in sequence, each with a 10 second timeout. Every one of those timeouts was
respected. Not one request ran a millisecond past its limit.

The step around them had no limit at all. Three endpoints at 10 seconds each is a 30
second worst case before handshakes, and nothing capped the sum. A per-request timeout
answers "how long do we wait for this server"; it cannot answer "how long is the caller
willing to wait", and only the second question is the one a launch, or a scrape, actually
cares about. [The full write-up of that launch bug](slow-browser-launch-timeout-budget.md)
is a good companion to this page, because it is the same mistake in a different place.

The fix was a whole-operation budget. A total deadline sits alongside the per-request
timeout, and each request is handed `min(timeout, remaining)`. A slow first endpoint now
shortens the second instead of adding to it, and the step returns or raises inside the
budget no matter how many endpoints the list grows to. A retry loop is exactly this shape:
a sequence of bounded attempts whose sum nobody bounded. Give it a total budget and the
same fix applies.

## What a retry loop should count

Three things need a ceiling, and most loops set only the first:

- **Per-attempt timeout.** How long one try may take. Nearly everyone sets this.
- **Total attempts.** How many tries the whole operation may make before giving up.
- **Total time.** The wall-clock deadline across all attempts and all the sleeps between
  them. This is the one that gets forgotten, and it is the one that stops a "retry" from
  becoming an unbounded stall.

The insight from the launch bug is that the per-attempt timeout and the total deadline are
different questions, and satisfying the first tells you nothing about the second. An
attempt that respects a 15 second timeout, retried five times with growing backoff, can
still burn well over a minute, and if a page-load timeout is what keeps triggering the
retry, that minute is mostly dead waiting.

## A budgeted retry in real Playwright

`InvisiblePlaywright` returns a real Playwright `Browser`, so every method below is the
stock API, including [`page.goto`'s own `timeout` and `wait_until` parameters](https://playwright.dev/python/docs/api/class-page#page-goto).
The only project-specific line is the launch. The helper takes a deadline in seconds and
never lets the sum of attempts cross it; each attempt gets whatever time is left, so a slow
attempt shortens the next one instead of extending the total.

```python
import time
from invisible_playwright import InvisiblePlaywright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


def fetch_with_budget(page, url, total_budget=45.0, max_attempts=4):
    """Retry a navigation, bounded by BOTH a total deadline and an attempt count."""
    deadline = time.monotonic() + total_budget
    attempt = 0
    last_error = None

    while attempt < max_attempts:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempt += 1

        # per-attempt timeout is never larger than what the budget has left
        per_attempt_ms = int(min(15.0, remaining) * 1000)
        try:
            response = page.goto(url, timeout=per_attempt_ms, wait_until="domcontentloaded")
            if response is not None and response.ok:
                return response
            last_error = f"HTTP {response.status if response else 'no response'}"
        except PlaywrightTimeoutError as exc:
            last_error = f"timeout: {exc}"

        # back off, but never sleep past the deadline
        backoff = min(2.0 ** (attempt - 1), max(0.0, deadline - time.monotonic()))
        if backoff > 0:
            time.sleep(backoff)

    raise RuntimeError(
        f"gave up after {attempt} attempt(s) within {total_budget}s budget; last: {last_error}"
    )


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    resp = fetch_with_budget(page, "https://example.com")
    print("final status:", resp.status)
```

Two details carry the whole idea. `per_attempt_ms` is `min(15.0, remaining)`, the direct
translation of `min(timeout, remaining)` from the launch fix, so the last attempt cannot
overshoot the deadline waiting for a server that will not answer before Playwright's own
[`TimeoutError`](https://playwright.dev/python/docs/api/class-timeouterror) fires. And the backoff sleep is
clamped to `deadline - time.monotonic()`, because a retry loop that sleeps past its own
deadline has simply moved the unbounded wait from the request into the `sleep`.

The failure message says how many attempts were made and why the last one failed, which is
the difference between an operator knowing the site is down and an operator guessing the
budget was just too tight. A silent give-up is the retry-loop equivalent of the launch bug
hiding inside a mismeasurement.

## Retry the right failures, and pin the seed while you debug them

A budget decides how long you try. It does not decide what is worth trying again. A
timeout or a connection reset is usually transient and worth a retry. A page that loads
cleanly but returns a challenge, a short body, or a different layout than a human gets is
not a transient failure, it is
[the difference between a normal failure and detection](playwright-detected-as-bot.md),
and retrying it harder just raises your request rate against a site that already decided
something about you. Classify before you retry: retry the network, investigate the block.

When you are debugging which case you are in, a moving fingerprint makes it impossible. If
every attempt draws a new identity, a failing attempt tells you nothing, because you cannot
separate the site changing its answer from your browser changing its question. Passing a
seed pins the identity so every retry is the same machine, and a failing run can be
replayed exactly instead of hoped for again:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    # same GPU, same canvas hash, same fonts on every retry and every rerun
    resp = fetch_with_budget(page, "https://example.com/flaky-endpoint")
```

## The velocity cost nobody budgets for

Here is the honest caveat, and it is the reason a bigger retry count is not a free win.
Retries raise your request velocity, and volume is its own fingerprint. Hammering one
endpoint from one address at machine speed produces a signal that no per-request disguise
hides, and it is a behaviour-layer tell that a site can score against you independently of
how real each individual request looks. We once flagged our own product for exactly this,
and the flag belonged to the test harness, not the browser.

That reframes the total budget as more than a safety limit. A tight attempt cap and a
real deadline are also a rate limiter, because they put a hard ceiling on how fast a
failing endpoint can be retried. It is the same volume trap that makes
[AI agent retry loops trip rate limits rather than fingerprint checks](agent-retry-loops-rate-limits.md):
a loop that re-requests on every failure multiplies requests no matter how real each one
looks. The advice in
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md) is to
space requests and keep one identity coherent rather than churning through many shallow
ones; an aggressive retry loop does the opposite of both at the worst possible moment,
right after a failure, which is exactly when a site is most likely to be watching. Back
off with real delay, cap the attempts, and treat a repeated failure on one URL as a
reason to slow down rather than a reason to try five more times in two seconds.

## Conclusion

A retry loop is a sequence of bounded attempts, and bounding each attempt does not bound
the sequence. That is the whole lesson from a launch bug where every timeout was respected
and the step still ran for 35 seconds. Give the loop a total time budget and a total
attempt count, hand each attempt `min(timeout, remaining)`, clamp the backoff to the same
deadline, and classify failures so you retry the network and investigate the block. Then
remember that every retry costs velocity, so the budget that keeps your scraper responsive
is the same budget that keeps it quiet.

## Short answers to the questions that lead here

**How do I retry a failed request in Playwright?** Wrap `page.goto` in a loop bounded by
both a total deadline and an attempt count, give each attempt `min(per_attempt, remaining)`
so it cannot overshoot the deadline, and clamp the backoff sleep to the same deadline.

**Why does my retry loop hang for minutes when every timeout is set?** Because a
per-attempt timeout does not bound the sum of attempts plus the sleeps between them. Add a
total wall-clock budget across the whole loop, not just each try.

**How many times should I retry?** Fewer than feels safe. Every retry raises your request
velocity, which is a behaviour signal a site can score, so cap attempts low and back off
with real delay rather than retrying instantly.

**Should I retry when I get a challenge page or a short body?** No. That is a block, not a
transient failure, and retrying it harder just raises your rate. Retry timeouts and resets;
investigate anything that loaded cleanly but looks wrong.

**Does retrying make me look more like a bot?** It can. A burst of fast retries right after
a failure is exactly the velocity pattern detectors watch for, and it is the moment a site
is most attentive. Space them out.

**How do I make a flaky failure reproducible?** Pin the identity with a seed so every retry
and every rerun is the same machine. A moving fingerprint means a failing run cannot be
replayed, only guessed at again.

## Sources

- This project's own launch-timeout fix, in which a step made of individually bounded
  network requests was itself unbounded, and the fix was a whole-operation budget handing
  each request `min(timeout, remaining)`.
- This project's release gates, including the velocity flag that turned out to be the test
  harness generating the exact request-rate signal it was measuring.
- Playwright's own documented [`page.goto` timeout and `wait_until` behaviour](https://playwright.dev/python/docs/api/class-page#page-goto)
  and its [`TimeoutError`](https://playwright.dev/python/docs/api/class-timeouterror), both
  used verbatim in the budgeted retry example above.

**See also:** [why a per-request timeout did not fix a slow launch](slow-browser-launch-timeout-budget.md)
for the bug this page generalizes, [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for the velocity layer, and [the checklist for when one site detects you](playwright-detected-as-bot.md)
for telling a block apart from a transient failure before you retry it.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The 35-second launch tail
that motivated the total-budget rule was a bug I shipped before I fixed it.*
