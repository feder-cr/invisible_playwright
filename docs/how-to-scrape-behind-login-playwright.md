---
title: "How to scrape data behind a login with Playwright"
description: "Log in once with a fixed seed, save storage_state, and reuse it every run. The login form is the highest-scrutiny flow on most sites - skip it, don't script it."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 5
---


# How to scrape data behind a login with Playwright

To scrape a page behind a login with Playwright, log in once, save the session as
`storage_state`, and reuse it on every later run instead of scripting the login form
again. That single change moves your automation off the highest-scrutiny flow on most
sites and onto the ordinary path a returning visitor already takes - a saved cookie,
not a retyped password.

The obvious way to scrape a page that requires an account is to script the login form
every run: fill the username, fill the password, click submit, then go get the data.
That obvious way is also the worst one, because the login form is the single most
heavily instrumented sequence on most sites - it is where account-takeover and
credential-stuffing defenses concentrate, precisely because that is where they matter
most to the operator.

This page is the alternative: log in once, save the session, reuse it on every run
after that, and the two things that go wrong once you do - a permission that quietly
disables a protection you think you have, and a saved session tied to a machine that
then changes underneath it.

## Why logging in every run is the wrong default

Every value a login form checks - the pointer travelling to the field instead of
teleporting, the pause before typing, the rhythm between filling the form and hitting
submit, whether a field was focused before it received input - is checked hardest at
exactly that page, more than anywhere else on the site. Running that sequence once is
a risk. Running it on every scheduled job is running the highest-scrutiny flow on a
timer.

[The full argument for skipping the flow entirely, and what it depends on, is here](automating-login-vs-session-reuse.md).
The short version: a real returning visitor does not retype a password every visit,
they carry a session cookie. An automated session that does the same thing is not
imitating unusual behaviour, it is doing what most non-first-time sessions already do.

## Step 1: log in once and save the session

Log in interactively or with a script you only run once, then capture the context's
[`storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state)
immediately afterward. This is standard Playwright - the object
`InvisiblePlaywright` returns is a real `Browser`, so nothing about session capture is
special-cased, and the API-level details are in
[save and reuse a login with storage_state](save-reuse-login-storage-state-playwright.md):

```python
from invisible_playwright import InvisiblePlaywright

SEED = 42  # fixed on purpose - see the next section

with InvisiblePlaywright(seed=SEED) as browser:
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://example.com/login")
    page.fill("#username", "your-account")
    page.fill("#password", "your-password")
    page.click("#submit")
    page.wait_for_url("https://example.com/account")

    # capture cookies + local storage while the session is fresh
    context.storage_state(path="session.json")
```

Run this once, confirm `session.json` was written, and do not run the login form
again unless the saved session actually expires.

## Step 2: reuse the saved session on every later run

Every subsequent run loads `session.json` into a fresh context instead of touching the
login form at all:

```python
from invisible_playwright import InvisiblePlaywright

SEED = 42  # must match the seed used when the session was created

with InvisiblePlaywright(seed=SEED) as browser:
    context = browser.new_context(storage_state="session.json")
    page = context.new_page()

    page.goto("https://example.com/account")
    # already authenticated - no username field, no password field, no submit click
    data = page.text_content("#account-data")
    print(data)
```

No credentials typed, no submit click, none of the scrutiny attached to that specific
sequence. This is the pattern from
[why automating the login form is riskier than reusing a session](automating-login-vs-session-reuse.md),
applied end to end.

## Step 3: keep the same seed, not just the same cookies

The part that is easy to skip and breaks everything: the saved session has to be
replayed by the same machine that created it, not just by a browser holding the right
cookie.

A session is created by a specific fingerprint at the moment of login - a specific
GPU string, canvas hash, audio context and font list, all derived from the seed. Some
sites bind the session to more than the cookie, checking the fingerprint against what
was recorded at login. Replay the same cookie from a different fingerprint and you are
asking the site to believe the same identity changed its GPU and font set between one
request and the next. Passing a fixed `seed=42` to `InvisiblePlaywright` on both the
login run and every reuse run is what keeps the "machine" identical across the saved
session - the same reproducible-identity property described on the
[quickstart](quickstart.md) page, applied here to a saved session instead of a
debugging run.

Write the pairing down somewhere durable - the seed used for a given saved session -
because the file on disk carries no note of which seed created it.

## The gotcha: a stored permission that quietly disables WebRTC protection

Firefox turns off two WebRTC privacy behaviours - restricting ICE candidates to the
default route, and masking the host address behind an mDNS name - the moment a camera
or microphone permission is present for that origin, and the check counts a
**persisted** grant, not only an active capture. That matters here if the site's login
or verification flow ever asked for camera or microphone access, and you are replaying
that session against a **persistent profile** rather than a bare `storage_state` file:
check what got granted.

A permission accepted once, in a profile directory you keep reusing, disables that
protection for every future session on the same profile until the permission is
removed. Nothing warns you when this happens; the session just starts reporting more
than it used to. The full mechanism, and the audit step to run against any reused
profile, is in
[Playwright persistent profile: what it fixes and breaks](persistent-profiles.md).

`storage_state` alone does not carry permissions - it is cookies and local storage
only - so this specific trap is a persistent-profile problem, not a
`storage_state` problem. But it matters here because the two mechanisms get combined
in practice: a profile for long-lived identity plus a saved session for the login
itself. If you go that route, audit the profile's stored permissions before trusting
its WebRTC output again.

## The other gotcha: storage_state versus a persistent profile

`storage_state` is a portable file: cookies and local storage, nothing else. A
persistent profile is the browser's own user data directory, and it keeps
accumulating - cache, permissions, extensions, site settings - which is also why it can
corrupt or silently drop cookies between runs. Two open Playwright issues cover exactly
that:
[session cookies not persisting through `launch_persistent_context`](https://github.com/microsoft/playwright/issues/36139)
and
[a persistent context failing to read cookies from a prior session on the same directory](https://github.com/microsoft/playwright/issues/35466).
Never point two browsers at the same profile directory concurrently either - there is
no concurrency story for a user data directory, and the failure is corruption, not an
error message.

For most scraping-behind-a-login jobs, `storage_state` is the right tool: it is
simpler, it is a file you can inspect, and it does not accumulate the things that go
wrong quietly. Reach for a full persistent profile only when the identity genuinely
needs a browsing history, not just a login. The tradeoff in full is worked through in
[Playwright persistent profile: what it fixes and breaks](persistent-profiles.md).

## When the saved session stops working

Treat expiry as certain, not exceptional. Sessions end - some on a timer, some the
first time the site notices something it does not like. Build the re-login path once,
using the same seed and the same proxy country as the original login, rather than
improvising a fresh login under a different identity the first time the saved session
fails. A re-login from a new, unrelated fingerprint reintroduces the exact scrutiny
this whole approach exists to avoid, and if the block is not actually about the session
at all, work through
[the checklist for being detected on one site](playwright-detected-as-bot.md) before
assuming the saved state is the problem.

## Conclusion

Scraping behind a login is not a login-automation problem, it is a session-reuse
problem wearing a login-automation costume. Log in once, capture `storage_state`
immediately, and replay it from the same seed on every later run. The saved cookie
and the fingerprint that created it are a pair - reusing one without the other is what
turns a clean scrape into a flagged one, and it is a cheaper fix than anything on the
automation side of the login form itself.

## Short answers to the questions that lead here

**Do I have to log in every time I scrape a page behind an account?** No. Log in once,
save `storage_state`, and load it into every later context. That is the entire point
of session reuse.

**Is storage_state or a persistent profile better for staying logged in?**
`storage_state` for a login session - it is simpler and does not accumulate anything
you did not put there. A persistent profile is for an identity that needs a real
browsing history, not just a login.

**Why does the saved session get flagged if the cookie itself is valid?** Because some
sites check the fingerprint that is replaying the session against the one that created
it, independent of whether the credential is genuine. A cookie proves who logged in,
not that the same browser is asking again.

**Can I reuse one saved session with a different proxy or a fresh identity?** Not
safely. The session, the seed, and the exit country were a set at the moment of
login, and changing one while keeping the others is the same kind of mismatch as a
timezone that disagrees with the proxy.

**Why is a camera permission relevant to a login flow?** Because Firefox disables part
of its WebRTC address protection the moment an origin has a camera or microphone
permission on record, and a persisted grant from months ago still counts. It only
applies if you are pairing the saved session with a persistent profile.

**What do I do when the saved session expires?** Re-authenticate through the same
seed and the same identity that created the original session, not a new one
improvised on the spot.

**See also:** [why automating the login form is riskier than reusing a session](automating-login-vs-session-reuse.md)
for the full case this page implements, [persistent profiles: what they fix and
break](persistent-profiles.md) for the permission trap and the profile-corruption
issues above, and [how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md)
for the layers this page does not cover.

## Sources

- Playwright's own [`storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state) /
  `storageState` and [`new_context`](https://playwright.dev/python/docs/api/class-browser#browser-new-context) APIs,
  for the session capture and reuse mechanism used throughout.
- The two upstream Playwright issues on persistent-context cookie handling linked
  above.
- This project's own notes on seed-derived fingerprints and the WebRTC permission
  trap, linked from the sections above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, driven by stock Playwright. The login form is the
one place on most sites where automating it at all is the expensive choice.*
