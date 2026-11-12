# Profile P4: the user journey

`scripts/run_fakeweb.py --profile p4 --binary PATH` walks 17 steps a person would make (click, hover, scroll, back,
forward, a popstate filter, a `target=_blank` tab, a download, a keyboard search, a sign-in, sign out, "load more") over a
small multi-page site (`journey_site.py`), with stock Firefox first and our engine right after, both driven by the stock
Playwright client. The verdict (`profile_p4.verdict`) compares, step by step, ok, url, title, marker, cookie names,
history length, open pages, `networkidle` and the step's own values. Chromium is not part of it. Time is never compared.

The two origins are two different sites: `127.0.0.1:<port>` (the site; cookies and sign-in) and `localhost:<port>` (partner
page and ad frames). Two ports of one host would be one site, and an "other origin" frame of the same site would never leave
the page's process.

## The noise is deterministic

Every slow response is held by the server until an event releases it (a `/release` from the page, or the walk reaching a
step), never by a timer; nothing is aborted or removed while its request is in flight; the page's own fetch chain does not
use timers. Stock Firefox then gives the same `networkidle` vector on every run. Two pages stay "not idle" on purpose, and
pages.json (`journey_p4.not_idle_steps`) declares which steps: the category page holds a fetch until step 9 (steps 7, 8), the
search results hold a KEEPALIVE fetch until step 13 (step 12): a client that does not count a keepalive request as in flight
is red there.

## Known difference in our favour

A fetch that the page aborts can end on stock Firefox without a `requestfinished` and without a `requestfailed` event: it
stays in flight for ever and holds `networkidle` back. Our client closes such a fetch with `requestfailed`. This is kept as
it is (the correction of a standard API); the noise avoids the case (its aborts cannot race: the server has sent the headers
before the page aborts), so the verdict does not depend on it.

## On the engine of a series run

`.github/workflows/fakeweb-p4.yml` runs the journey in one job on `ubuntu-24.04`: the Linux engine of a run of the series
repository, the core that run was built with, the wrapper of the pushed branch, stock Firefox of the pinned Playwright; stock
first, ours second, three walks (`--repetitions 3`). The step summary has one table per walk (step, stock ok / idle, ours ok /
idle, equal or different), the FAIL lines and whether the control is sound (`scripts/fakeweb_p4_report.py journey`). The job is
red when ours differs on a step of a walk whose control is sound, and red with another message when a control is broken. A second
step walks once with `--trace` (ours only) and prints, from `scripts/fakeweb_p4_report.py trace`, the resource types the client
reported for `sendBeacon` and how many keepalive requests the client reported against the server's count.
