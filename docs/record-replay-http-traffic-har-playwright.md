---
title: "Record and replay HTTP traffic with HAR in Playwright"
description: "Record HTTP traffic to a HAR file and replay it offline with routeFromHAR in Playwright: why replay is a stale fixture and a HAR holds real session data."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 32
---


# Record and replay HTTP traffic with HAR in Playwright

A HAR file is a plain JSON log of every request and response a page made: URLs,
headers, status codes and, if you ask for it, the response bodies. Playwright can
write one for you while a session runs, and then serve the whole thing back to a
later run without touching the network. That second half is the useful part: a test
that reads from a frozen HAR is deterministic, offline, and does not depend on a
site being up or a proxy being clean.

This page shows the record step, the replay step, both on the patched Firefox this
project ships, and then the caveat that decides where the technique belongs. A
replayed HAR is a fixture, not a disguise, and the file you captured is not safe to
share.

## Record every request to a HAR

Point a context at a path with [`record_har_path`](https://playwright.dev/python/docs/api/class-browser#browser-new-context)
and everything it fetches lands in that file when the context closes. The two-line launch is the same one from the
[Quickstart](quickstart.md); the browser it returns is a real Playwright `Browser`,
so `new_context` and every option it takes work exactly as upstream documents them.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(
        record_har_path="capture.har",
        record_har_mode="full",     # "minimal" drops bodies and timings
        record_har_content="embed",  # response bodies inline in the JSON
    )
    page = context.new_page()
    page.goto("https://example.com")
    page.click("#load-more")
    context.close()  # capture.har is flushed here, not before
```

`record_har_mode="full"` keeps timings and sizes; `"minimal"` keeps only what is
needed to replay. `record_har_content="embed"` writes each body into the JSON,
which is what makes the file self-contained and portable. The file does not exist
until the context closes, so a crash before `context.close()` leaves you nothing.

## Replay the HAR offline with routeFromHAR

The mirror image is [`route_from_har`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route-from-har).
Register it on a fresh context and every matching request is answered from the file
instead of the network.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context()
    context.route_from_har("capture.har", not_found="abort")
    page = context.new_page()
    page.goto("https://example.com")  # served from capture.har, no network
    print(page.title())
    context.close()
```

`not_found="abort"` makes any request the HAR does not contain fail loudly, which
is what you want in a test: a silent live fallback would hide the fact that your
fixture is incomplete. Use `not_found="fallback"` only when you deliberately want
unmatched requests to hit the real network. There is also an `update=True` mode on
`route_from_har` that refreshes the file from live traffic instead of serving it,
handy for regenerating a capture without rewriting the record step.

The async API is identical in shape; import `InvisiblePlaywright` from
`invisible_playwright.async_api` and `await` the context and page calls, exactly as
shown in the [Quickstart](quickstart.md).

## Why the same seed matters for a replay test

A HAR freezes the network. It does nothing for the browser identity, and a test is
only fully deterministic when both halves are pinned. That is what the `seed=42`
argument does above: the same seed yields the same GPU string, canvas hash, audio
context and font set on every run, so a page that renders differently between two
replays is telling you about your own code rather than about a random fingerprint
draw. [Pinning fingerprint fields](pinning.md) covers forcing individual values
while leaving the rest seed-derived.

Pair the frozen network with a frozen identity and a failing replay is reproducible
down to the pixel. That combination is the whole reason to record a HAR in the first
place: not to go faster, but to make a flaky test stop lying to you.

## The caveat that decides where this belongs

A replayed HAR is a stale fixture. It serves the bodies that were captured, at the
moment they were captured, and it never touches the live site again. That has two
consequences worth stating plainly.

First, replay does not help you against a live server. Nothing in a `route_from_har`
run reaches the internet, so it cannot pass a real check, defeat a challenge, or
make a session look human to anyone. It is a tool for testing your own code offline,
full stop. This project is built to look like a genuine Firefox driven by a real
person, and that is why it clears most fingerprint, TLS and driver-layer checks: the
engine is real, not patched over in JavaScript. But looking real is a live-session
property. It has nothing to do with replaying a file, and replaying a file gives you
none of it.

Second, the HAR itself is sensitive. It was recorded through whatever exit your
session used, so it contains real, IP-tied responses: cookies, tokens, session
identifiers, personalised bodies, and headers that name your egress. `record_har_mode`
and content settings change how much lands in the file, but a full capture with
embedded content is a complete record of a real session. Treat `capture.har` the way
you would treat a credential file: keep it out of the repo, scrub it before it is
shared, and do not commit one you have not read.

## What HAR replay does not fix

The record-and-replay loop is orthogonal to the reasons a live session gets blocked.
It does not improve IP reputation, it does not extend a per-account quota, it does
not change your request pacing, and it does not alter behaviour or timing. Those are
supplied by you: a clean exit and human-shaped interaction. If a live run is being
detected, a HAR fixture is the wrong tool; work the
[detection checklist](playwright-detected-as-bot.md) instead, and if a clean
fingerprint still gets blocked, [there is a page for that](why-blocked-with-a-clean-fingerprint.md).

Where HAR replay earns its place is the test suite. A regression test that must run
in CI with no network, a golden-file comparison of how your parser handles a fixed
response, a reproduction attached to a bug report: all of these want a frozen
capture and none of them want the live site. Keeping that boundary clear is the
difference between a fixture and a false sense of security.

## Conclusion

`record_har_path` captures a session to a self-contained JSON log, and
`route_from_har` serves it back offline so a test is deterministic and network-free.
Both work as-is on the patched Firefox this project ships, driven by stock
Playwright. Pin the seed alongside the file and a failing replay is fully
reproducible. Just remember what the capture is: a stale fixture that cannot face a
live site, and a file full of real session data that has to be handled like one.

## Short answers to the questions that lead here

**Does replaying a HAR make my automation look more human?** No. Replay never
touches the live network, so it cannot influence any live check. Looking real is a
property of a real live session, which is what the engine provides separately.

**Can I use a HAR to bypass a check on a real site?** No, and it is the wrong mental
model. A replayed request is answered from your file; the real server never sees it.
Use it for offline tests, not against live endpoints.

**Is it safe to commit the .har file?** Treat it as sensitive. A full capture holds
cookies, tokens and IP-tied responses from a real session. Read it, scrub it, and
keep it out of public repos.

**Why is my capture.har empty or missing?** The file is written when the context
closes. If the process crashed or you never called `context.close()`, nothing was
flushed.

**How do I make unmatched requests fail instead of hitting the network?** Pass
`not_found="abort"` to `route_from_har`. The default fallback silently lets
unrecorded requests reach the live site, which hides gaps in your fixture.

**Does HAR replay speed up or fix a blocked scraper?** No. It has no effect on IP
reputation, quotas, rate limits or pacing. Those need a clean exit and human timing,
which you supply.

## Sources

- Playwright's own API reference for [`new_context`'s `record_har_path` /
  `record_har_mode` / `record_har_content`](https://playwright.dev/python/docs/api/class-browser#browser-new-context)
  and for [`route_from_har`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route-from-har),
  read from the reference rather than a rendered tutorial.
- This project's release gates, which run offline replay fixtures on the patched
  Firefox as part of the deterministic test set.

**See also:** [Configuration](configuration.md) for proxies and the egress-derived
timezone that shaped whatever you captured, [the detection checklist](playwright-detected-as-bot.md)
for a live block a fixture cannot help with, and [how to test bot detection without a
false pass](how-to-test-bot-detection.md) for asserting the right signal rather than
the absence of a wrong one.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A HAR makes a test
honest; it does not make a session real.*
