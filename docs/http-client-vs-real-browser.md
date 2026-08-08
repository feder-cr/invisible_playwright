---
title: "When to use an HTTP client vs a real browser"
description: "A decision flowchart for scraping: an HTTP client with TLS impersonation when the data is already in the response, a real browser when JavaScript builds the page."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 41
---


# When to use an HTTP client vs a real browser

Reaching for a full browser is the most expensive tool choice you can make, and most
scrapes do not need it. The question is not "which is more capable" - a browser wins
that every time - but "does this specific page hand me the data without one". A plain
HTTP request that already returns the answer is roughly two orders of magnitude cheaper
per page than driving a browser to render the same answer, and that gap decides whether
your job finishes in an hour or a week.

This page is a decision procedure, not a product pitch. You run one two-minute test,
land in one of two tiers, and only climb to the browser tier when the page forces you
to. When it does, invisible_playwright is what the browser tier looks like here, and the
last two sections are honest about what it fixes and what it leaves to you.

## The two-minute test that decides the tier

Before writing any code, find out where the data actually lives. There are two cheap
checks and they take longer to describe than to run.

**Check one: view-source.** Open the target URL and look at the raw HTML the server
sent, before any script runs. In a browser that is View Source (not Inspect, which
shows the DOM after JavaScript). From a shell it is one request:

```bash
curl -s "https://example.com/listing" | grep -i "the-value-you-want"
```

If the value you want is already in that response, you are in tier one and you may never
need a browser at all.

**Check two: devtools network.** If view-source does not contain the data, open the
browser devtools, go to the Network tab, reload, and filter to XHR/Fetch. Very often the
page is empty HTML that immediately calls a JSON endpoint, and that endpoint returns the
data in a clean structured form. If so, you are still in tier one: you call that endpoint
directly and skip the page entirely.

You only fall through to tier two when the data appears in neither place - it is
assembled in the DOM by client-side JavaScript, gated behind a challenge script that has
to execute, or only exists after an interaction like a click or a scroll.

## Tier one: an HTTP client, when the data is in the response

If the value is in the initial HTML or a JSON endpoint, an HTTP client is the right tool,
and the only thing standing between you and the data is usually the handshake, not the
markup.

A stock `requests` or `httpx` call ships a TLS and HTTP/2 fingerprint that does not
resemble any browser, and a growing number of servers read that before they read your
User-Agent. That is a solved problem: a client with browser TLS impersonation makes the
handshake look like a real Chrome or Firefox, and for a page that already contains its
data that is frequently the entire fix. The mechanics of why the plain library gets
blocked, and which library layer to reach for, are in
[why requests gets blocked on a TLS fingerprint](web-scraping-tls-fingerprint-requests-blocked.md)
and [the trade-offs of curl_cffi specifically](vs-curl-cffi.md).

Tier one is fast, it is cheap, and it is easy to run at high concurrency because a single
request holds almost no state. Exhaust it before you climb. A job that can run as ten
thousand HTTP requests an hour becomes a very different job the moment each one needs a
browser.

## Tier two: a real browser, when JavaScript builds the page

You climb when the response does not contain the answer. Three shapes force this:

- **Client-side rendering.** The HTML is a shell and the content is written into the DOM
  by JavaScript after load. There is no endpoint you can call cleanly, or the endpoint is
  signed by script you would have to reimplement.
- **A challenge script.** The page runs code that must execute in a real engine, reads
  properties of the environment, and only then reveals the content. An HTTP client cannot
  run it, so it never gets past the gate.
- **Interaction.** The data only appears after a click, a scroll, a form submission, or a
  step that depends on the previous one having rendered.

For all three you need something that executes JavaScript and builds a real DOM, which
means a browser. The catch is that most automated browsers announce themselves - the
[driver flag](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver), a
mismatched TLS handshake, a datacenter-shaped machine with no GPU or fonts. That is the
layer invisible_playwright works on: a Firefox patched at the C++ level so the
fingerprint, the TLS handshake and the driver surface read as a genuine browser, driven
by stock Playwright with no API to relearn. If you are unsure which shape you are facing,
[how websites actually detect bots](how-do-websites-detect-bots.md) maps the signals to
the tier that answers them.

## The resource cost that pushes you back down a tier

Here is the number that should make you re-run the two-minute test before committing to a
browser: a real browser costs roughly two orders of magnitude more CPU and memory per
page than an HTTP request. It launches an engine, parses and executes the page's
JavaScript, lays out and paints a DOM, and holds all of that in memory for the life of
the page. An HTTP client sends bytes and parses the reply.

That cost is not just your cloud bill. It is throughput, and throughput is a detection
surface. A browser fleet is slower per page, so to hit the same volume you run more of
them in parallel from the same handful of exits, and that concentration is exactly the
velocity signal a rate limiter is built to catch. Using a browser where a request would
have worked does not only make the scrape more expensive - it makes it easier to
rate-limit.

So the rule cuts both ways. Climb to the browser tier when the page genuinely renders
with JavaScript or needs interaction. Drop back to tier one the instant you find a JSON
endpoint or the data in the raw HTML. A common and effective shape is a hybrid: a browser
solves the challenge or renders one page, you lift the session cookies or the discovered
endpoint out of it, and the bulk of the run goes back through a cheap HTTP client. That
pattern is worked through in
[combining invisible_playwright with httpx for speed](combine-invisible-playwright-with-httpx-for-speed.md).

## Launching the browser tier with invisible_playwright

When the test lands you in tier two, the switch from plain Playwright is two lines, and
the returned object is a real Playwright `Browser` with every standard method. Reading the
rendered text of a page that an HTTP client could not have built:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")
    page.wait_for_selector("#results")      # content that JS writes after load
    html = page.content()                   # the DOM after scripts have run
    print(page.inner_text("#results"))
```

`seed=42` pins the identity so a failing run is reproducible: the same seed produces the
same GPU, fonts, canvas hash and screen every time, which is the difference between
bisecting a failure and guessing at one. Pass a proxy the same way you would expect:

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/listing")
```

Everything after the `with` line is stock Playwright. If you already have Playwright
scraping code for this page, the extraction logic does not change; only how the browser
is launched does.

## What the browser tier fixes, and what it does not

This is the honest part, and skipping it is how people conclude the tool "does not work"
when they simply pointed it at the wrong problem.

invisible_playwright is designed to look like a real browser driven by a real person, and
that is why it passes most detection checks: the fingerprint, the TLS handshake and the
driver layer read as a genuine Firefox rather than as automation. What it does **not** do,
on its own, is anything above the browser:

- **IP reputation.** A genuine-looking browser on a known datacenter or already-flagged
  address still loses. That is your proxy's job, and the browser cannot fix it.
- **Per-account quotas and rate limits.** These are counted server-side against your
  session or account, not read off your fingerprint. No browser property changes a quota.
- **Behaviour and timing.** invisible_playwright gives you Bezier-curve mouse motion, but
  pacing, dwell time, and the overall shape of a session are yours to make human. A
  perfect fingerprint that clicks every 200 milliseconds is still a tell.

The browser tier makes the client look real. You still supply a clean exit and human
pacing. Treat those as three separate problems and you will debug the right one; expect
the browser to solve all three and you will replace a good proxy trying to fix a
fingerprint that was never the issue.

## Conclusion

The choice is decided by where the data lives, not by which tool is more powerful. Run
the two-minute test: if view-source or a JSON endpoint already holds the answer, stay in
tier one with an HTTP client and browser TLS impersonation, because it is roughly two
orders of magnitude cheaper and harder to rate-limit. Climb to a real browser only when
the page renders with client-side JavaScript, runs a challenge, or needs interaction -
and when you do, invisible_playwright is the browser tier that reads as genuine, provided
you bring the proxy and the pacing yourself.

## Short answers to the questions that lead here

**When should I use requests instead of a browser?** Whenever the data is already in the
raw HTML or in a JSON endpoint the page calls. Check with view-source and the devtools
Network tab before writing any browser code.

**Why not just use a browser for everything to be safe?** Because a browser costs roughly
two orders of magnitude more CPU and memory per page, and the extra parallelism you need
to keep up concentrates traffic into the velocity signal rate limiters look for.

**My HTTP client gets blocked but the data is in the HTML. Do I need a browser?** Usually
not. That is normally a TLS fingerprint problem, fixed by an HTTP client that impersonates
a browser handshake, not by rendering the page.

**How do I know the page needs JavaScript?** If view-source lacks the data and the
devtools Network tab shows the content arriving only after scripts run or after you
interact, the DOM is built client-side and you need a browser.

**Does invisible_playwright make me undetectable?** No, and no tool honestly claims that.
It makes the browser fingerprint, TLS and driver layer read as a real Firefox, which
handles most checks. It does not fix your IP reputation, account quotas, or timing.

**Can I mix the two tiers?** Yes, and it is often the best shape: use the browser to
render one page or clear a challenge, then lift the cookies or the endpoint out and run
the bulk of the job through a cheap HTTP client.

## Sources

- This project's own measurements of per-page resource cost, the basis for the "roughly
  two orders of magnitude" figure comparing a rendered page against a plain request.
- [MDN's `navigator.webdriver` reference](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
  the standard property behind "the driver flag" that automated browsers expose.
- The tier-one companion pages in this set on TLS impersonation and the hybrid
  browser-then-client pattern, linked throughout.

**See also:** [combining invisible_playwright with httpx for speed](combine-invisible-playwright-with-httpx-for-speed.md)
for the hybrid pattern, [why requests gets blocked on its TLS fingerprint](web-scraping-tls-fingerprint-requests-blocked.md)
for the tier-one fix, and [how websites detect bots](how-do-websites-detect-bots.md) for
matching a signal to the tier that answers it.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The two-minute test in the
first section is the one I skip when I am in a hurry and regret every time.*
