---
title: "AI agent retry loops trip rate limits, not fingerprints"
description: "Why AI agent retry loops multiply requests into a volume signal that trips rate limits, and why throttling belongs in the agent loop, not the browser engine."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 17
---


# AI agent retry loops trip rate limits, not fingerprints

An agent that gets a browser to look like a real person will still get blocked if it
asks for a page thirty times in ten seconds. That block has nothing to do with the
fingerprint. It is a counter.

This page is about the failure mode that a perfect disguise does not touch: an agent
retries and re-plans on failure, every retry is another request, and a stack of retries
per second is a volume and behaviour signal that trips rate limits and quotas long
before any fingerprint check runs. The fix does not live in the browser. It lives in
the loop that drives it.

## Why an agent makes more traffic than a scraper

A hand-written scraper visits a known list of URLs once. An agent does not. It observes
a page, decides a next action, acts, and when the action fails it re-plans and tries
again. That control loop is the whole point of an agent, and it is also a request
multiplier.

Three things stack up:

- **Retries.** A transient error, a slow load, a missing element, and the agent tries
  the same step again. One logical action becomes three or five HTTP requests.
- **Re-planning.** When a step fails the agent often re-reads the page, or reloads it,
  or navigates back and forward to re-orient. Each of those is a fresh visit to a URL a
  human would have loaded once.
- **Exploration.** An agent that does not know the site's layout probes it: opens a
  page to see what is there, backs out, opens another. A person who knows what they want
  does not.

The result is that a single high-level task ("find the price of item X") can produce
ten or twenty page loads, most of them to the same few URLs, in a burst measured in
seconds. That pattern is trivial to count, and counting is exactly what a rate limiter
does.

## Rate limits read volume, not the browser

A fingerprint check and a rate limit answer two different questions, and a convincing
disguise only answers one of them: one asks whether this browser is real, the other only
counts how many requests just arrived.

A fingerprint check reads properties of the browser: the GPU string, the canvas hash,
the font list, the TLS handshake. It asks "is this a real browser". invisible_playwright
is built to make that answer yes, which is why it passes most of that class of check:
the engine is a genuine Firefox patched at the C++ level, so the values are real rather
than spoofed on top of a headless build.

A rate limit reads something completely different: how many requests arrived from this
address, this account, or this session, in this window. It does not open the browser at
all. It increments a counter and compares it to a threshold. The request could come from
the most convincing browser ever built and the counter would go up by exactly one.

So the two defences are orthogonal:

| Signal | What it reads | Does a real-looking browser help |
|---|---|---|
| Fingerprint / driver / TLS | Properties of the client | Yes - this is what the engine is for |
| Rate limit / quota | Count of requests over time | No - N requests is N requests |
| Per-account quota | Actions tied to one identity | No - the account is the unit, not the browser |
| Behaviour / velocity | Timing and shape of the traffic | No - the loop decides the timing |

The bottom three rows are not something an engine can fix, because the engine only
controls what one request looks like, not how many of them you send or how fast. That is
a property of the loop.

## Put the budget in the loop, not the engine

The consequence is a design rule: **backoff, throttling and a request budget belong in
the agent loop.** No browser setting throttles an agent that has been told to retry
until it succeeds.

Three controls cover most of it:

- **Exponential backoff on retry.** When a step fails, wait, and wait longer each time,
  instead of retrying immediately. A failed request answered by an instant identical
  request is the clearest velocity signal there is.
- **A hard request budget.** Cap the number of requests per task and per unit of time.
  When the budget is spent, stop and surface the failure rather than letting the loop
  spend the site's patience for you.
- **A cap on re-plans.** Bound how many times the agent may re-read or reload the same
  URL before it gives up. An unbounded re-plan loop is an accidental refresh storm.

The unit to think in is requests per minute from one address and one account, not
requests per browser. Ten agents behind one exit IP each politely pacing themselves are,
to the counter, one very busy client.

## A retry loop that backs off

Here is the two-line launch plus a retry wrapper that carries a budget and backs off.
The browser is a stealth Firefox; the throttling is plain Python, because that is where
it has to be.

```python
import time
from invisible_playwright import InvisiblePlaywright

def fetch_with_budget(page, url, *, max_attempts=4, budget):
    """Load url with exponential backoff. Raises when the budget is spent."""
    for attempt in range(max_attempts):
        if budget["spent"] >= budget["limit"]:
            raise RuntimeError("request budget exhausted; stopping instead of retrying")
        budget["spent"] += 1
        try:
            page.goto(url, wait_until="domcontentloaded")
            return page
        except Exception:
            if attempt == max_attempts - 1:
                raise
            backoff = 2 ** attempt          # 1s, 2s, 4s, ...
            time.sleep(backoff)
    return page

budget = {"spent": 0, "limit": 30}          # hard cap for the whole task

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    fetch_with_budget(page, "https://example.com", budget=budget)
    # ... agent observes, decides, acts; every navigation goes through the budget
```

The `browser` returned is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so every standard
method works unchanged - `new_page`, `goto`, `click`, `wait_for_selector`. The stealth is
in the engine; the pacing is in `fetch_with_budget`. Nothing about `seed=42` slows the loop
down, and nothing about the loop changes the fingerprint. They are separate jobs.

Pacing the actions themselves matters too, because uniform, machine-fast timing is its
own tell independent of volume. That is the subject of
[the pause shaped like model latency](ai-browser-agents-stealth.md), and it is worth
reading alongside this page: one is about how many requests, the other about their
shape.

## What invisible_playwright fixes, and what it does not

The honest split, because overclaiming here is both wrong and a way to get someone
blocked while they trust a promise that was never true.

**What the engine handles.** The fingerprint, driver and TLS layers read as a genuine
Firefox, so the class of check that asks "is this a real browser driven by a real
person" mostly answers yes. That is measurable: on the public tampering suites a session
comes back internally consistent rather than flagged, and the same seed produces the
same machine every run so a failure is reproducible.

**What you supply.** The engine does not change your IP's reputation, it does not create
per-account quota out of nothing, it does not slow your loop down, and it does not shape
your timing. Those are yours to bring:

- A clean exit, because a real-looking browser on a
  [datacenter IP is still on a datacenter IP](can-websites-detect-a-datacenter-proxy-ip.md),
  and a counter on the address does not care how good the browser is.
- Human pacing, from the loop, using the controls above.
- Respect for the account's own limits, which are counted against the identity and not
  the browser.

Put plainly: invisible_playwright makes each request look like it came from a real
person. It does not make a thousand requests look like one. That second job is the
loop's, and no engine setting will do it for you. When a clean fingerprint still gets
blocked, volume and address are the usual reasons -
[which is a page of its own](why-blocked-with-a-clean-fingerprint.md).

## Conclusion

Retries and re-planning are what make an agent an agent, and they are also what turn one
task into a burst of requests. A burst is counted, and a counter is a different defence
from a fingerprint check: the most convincing browser in the world still increments it by
one per request. So the throttle cannot live in the engine. Give the loop a budget, back
off on failure, cap the re-plans, and put a clean exit under it. The browser's job is to
make each request real; keeping the requests few and well-spaced is yours.

## Short answers to the questions that lead here

**Will a stealth browser stop my agent from being rate limited?** No. Rate limits count
requests; they do not inspect the browser. A real-looking fingerprint helps with
detection, not with volume.

**Why does my agent get blocked when a manual visit works fine?** Almost always because
the agent sent many more requests, much faster, than the person did. Retries and reloads
multiply traffic that a human never generates.

**Where should backoff and throttling live?** In the agent loop, not the engine. No
browser setting paces an agent that was told to retry until it succeeds.

**Does the seed or fingerprint affect how fast I can go?** No. The seed controls the
identity, not the timing. Speed and request count are decided entirely by your loop.

**Is a per-account quota a fingerprint problem?** No. A quota is counted against the
identity or the account, not the browser, so a better fingerprint does nothing for it.
Budget the actions instead.

**How many requests per minute is safe?** There is no universal number; it depends on
the site. The safe habit is a hard budget per task and per window, plus backoff, so a
failing loop stops itself rather than accelerating.

## Sources

- This project's own velocity flag, raised against invisible_playwright during testing,
  which turned out to be the test harness hammering one scoring endpoint from one
  address - the same volume signal described here, produced by our own gate.
- The wrapper's documented API (`InvisiblePlaywright`, seed reproducibility, a real
  [Playwright `Browser`](https://playwright.dev/python/docs/api/class-browser) return)
  as shipped, used verbatim in the example above.

**See also:** [the pause shaped like model latency](ai-browser-agents-stealth.md) for
the timing side of the same problem, [how to rate limit your scraper](how-to-rate-limit-your-scraper-playwright.md)
for the pacing mechanics in detail, and [what actually detects an agent](browser-use-detection.md)
across the layers a fingerprint does not cover.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The velocity flag that
started this page was ours, and it belonged to the harness, not the browser.*
