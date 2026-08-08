---
title: "Record a Playwright trace to debug a failed scrape"
description: "Record Playwright traces with screenshots and DOM snapshots to debug failed scrapes. View in trace.zip viewer and keep debug artifacts out of production."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 33
---


# Record a Playwright trace to debug a failed scrape

A scrape that fails once in twenty runs is the worst kind to debug, because by the
time you read the exception the page is gone. The selector timed out, the process
exited, and all you have is a line number and a stack trace that tells you where the
code gave up, not what the page looked like when it did.

Playwright has a purpose-built answer for this: a trace. You turn it on around the run,
and if the run fails you get a `trace.zip` that plays back every action, every DOM
snapshot and every network call in a viewer, frozen at the moment the selector was not
there. This page is how to record one against invisible_playwright, how to read it, and
the two caveats that decide whether you leave it on.

## What a trace captures that a log does not

A log records what your code decided to print. A trace records what the browser did,
whether you thought to log it or not. With `screenshots` and `snapshots` on, the
`trace.zip` holds:

- A screenshot filmstrip across the whole run, so you can scrub to the exact action
  that failed and see the page as it rendered.
- A full DOM snapshot at every action, which the viewer makes clickable. You can hover
  the selector that timed out and see whether the element was absent, renamed, hidden
  behind a consent layer, or simply not loaded yet.
- Every network request and response, with headers, so a scrape that got the wrong
  page body shows you the request that fetched it.
- The console and the source-linked call log for each step.

The failure modes that a trace resolves in one look are the ones that otherwise cost an
afternoon: a site that served a different page than the manual visit got, a selector
that changed under a redesign, an `"Execution context was destroyed"` that was
[a navigation mid-visit rather than a race](execution-context-destroyed.md), or content
that was still streaming in when the wait gave up. It pairs naturally with a
[reproducible seed](quickstart.md): fix the identity, capture the trace, and a flaky run
becomes a run you can replay and inspect instead of guess at.

## Record a trace around an invisible_playwright run

invisible_playwright returns a real Playwright `Browser`, so tracing is the standard
API with nothing wrapped or renamed. The two lines that launch the browser are the only
difference from stock Playwright; everything after `browser` is upstream Playwright
exactly as documented.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_page().context
    context.tracing.start(screenshots=True, snapshots=True)
    try:
        page = context.pages[0]
        page.goto("https://example.com/listings")
        page.click("#load-more")            # the flaky step
        rows = page.query_selector_all(".row")
    finally:
        context.tracing.stop(path="trace.zip")
```

The `finally` block matters: you want the trace written even when the run raises, since
the failing run is the one you are trying to see. `start` takes `screenshots=True` for
the filmstrip and `snapshots=True` for the clickable DOM; without the second one the
viewer has pictures but no inspectable tree.

If you drive `firefox.launch()` yourself rather than using the class, the call is
identical, because `context.tracing` is a Playwright surface and invisible_playwright
does not touch it: [Playwright's tracing API](https://playwright.dev/python/docs/api/class-tracing)
works exactly as documented upstream, with no wrapping in between.

## Open trace.zip in the trace viewer

You do not unzip it. Point the bundled [trace viewer](https://playwright.dev/python/docs/trace-viewer)
at the file:

```bash
playwright show-trace trace.zip
```

The viewer opens the filmstrip along the top and the action list down the side. Click
the action that failed and it jumps the snapshot to that instant; from there you inspect
the frozen DOM, read the network tab for the request that returned the wrong body, and
confirm whether the element your selector wanted was ever present. This is the same
"read what the page rendered, not what your extractor logged" habit that the
[bot-detection testing method](how-to-test-bot-detection.md) is built on, applied to a
scrape instead of a fingerprint.

For CI, save the artifact only on failure so a green pipeline is not dragging zip files
around:

```python
try:
    run_the_scrape(context)
except Exception:
    context.tracing.stop(path="failure-trace.zip")
    raise
else:
    context.tracing.stop()   # no path: capture nothing on success
```

## The two caveats: overhead and content leak

A trace is a local debugging artifact, and it costs something on both ends.

**Overhead.** Screenshots and per-action snapshots add work to every step and grow the
zip as the run gets longer. On a short reproduction that is nothing; across a large
production crawl it is measurable latency and disk you did not budget for. Keep tracing
off in production runs and turn it on only around the failure you are chasing. The
`else: stop()` with no path above is one way; a config flag that starts tracing only in
a debug profile is another.

**Content leak.** The snapshots embed page content and the network tab embeds request
and response headers. A `trace.zip` therefore carries whatever the session saw:
logged-in page text, tokens in headers, the exit address a detector page painted on
screen. It leaks the same class of data a screenshot would, which is exactly why our
own release checklist treats a shared image as something to open and inspect before it
goes anywhere. Treat a trace the same way: it is fine on your machine, it is a data
disclosure the moment you attach it to a public issue. Scrub or redact before sharing,
and never commit one to a repository.

## Where a trace stops helping, and what invisible_playwright does not fix

A trace shows you what the browser and the page did. That is the whole of what it sees,
and it is worth being clear about the boundary.

invisible_playwright is built to look like a real Firefox driven by a real person: the
fingerprint, the TLS handshake and the driver layer read as a genuine browser, which is
why it clears most in-page detection. That is a demonstrated property, not a slogan, and
it is also not the whole session. On its own it does not fix IP reputation, per-account
quotas, rate limits, or behaviour and timing. Those you supply: a clean residential
exit, human pacing, sensible concurrency.

A trace helps you tell those layers apart. If the filmstrip shows a real, correct page
that your selector missed, the bug is in your code. If it shows a challenge or a
different body than a manual visit gets, the trace has done its job and handed you off to
the [detected-on-one-site checklist](playwright-detected-as-bot.md), where the exit and
the pacing live. What a trace will never show is the network handshake below the page or
the reputation of the address you came from, because those are decided before a snapshot
exists.

## Conclusion

Record a trace around the run that fails, not the code that fails. Start it with
`screenshots=True, snapshots=True`, stop it into a `trace.zip` in a `finally`, and open
that zip in the viewer to see the page frozen at the failing action. Keep it off in
production for the overhead, and treat the zip as sensitive because it embeds the same
content a screenshot would. It turns "the selector timed out sometimes" into a recording
you can scrub, which is most of the distance between debugging a scrape and guessing at
one.

## Short answers to the questions that lead here

**How do I see what the page looked like when my selector failed?** Record a trace with
`context.tracing.start(screenshots=True, snapshots=True)`, stop it to `trace.zip`, and
open it with `playwright show-trace trace.zip`. The viewer freezes the DOM at the failing
action.

**Do I need special API for invisible_playwright?** No. It returns a real Playwright
`Browser`, so `context.tracing` is the standard upstream call with nothing changed.

**Should I leave tracing on in production?** No. It adds per-action overhead and grows
the zip. Turn it on around a failure and off otherwise; stop with no `path` on success to
capture nothing.

**Is it safe to attach a trace.zip to a public bug report?** Not without checking. The
snapshots embed page content and the network tab embeds headers, so a trace leaks the
same data a screenshot would. Scrub before sharing.

**Will a trace tell me why a site blocked me?** It shows whether you got a challenge or a
wrong page, which is a real start, but it cannot see the TLS handshake or the IP
reputation. Those live in the exit and the pacing you supply.

**Can I capture a trace only when a run fails?** Yes. Wrap the run in `try/except`, call
`stop(path=...)` in the `except` and re-raise, and `stop()` with no path in the `else`.

## Sources

- Playwright's [tracing API](https://playwright.dev/python/docs/api/class-tracing)
  (`context.tracing.start` / `stop`) and the [`show-trace` viewer](https://playwright.dev/python/docs/trace-viewer),
  read from the upstream documentation.
- This project's own release checklist, whose rule to open and inspect any screenshot
  before sharing is the same content-leak concern applied to a trace.

**See also:** [the Quickstart](quickstart.md) for the two-line launch and the seed you
pin before capturing a trace, [Configuration](configuration.md) for the proxy and
timezone the trace cannot see, and [the detected-on-one-site checklist](playwright-detected-as-bot.md)
for where to go once the trace shows a challenge instead of a page.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A trace shows you the page;
it cannot show you the address you arrived from.*
