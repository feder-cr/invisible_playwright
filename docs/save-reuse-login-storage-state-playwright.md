---
title: "Save and reuse login with storage_state in Playwright"
description: "Save cookies and localStorage with storage_state, restore with new_context(storage_state=...), skip login forms on later runs, but seed and exit IP must match."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 35
---


# Save and reuse login with storage_state in Playwright

Logging in on every run is slow, and it drives the one flow on most sites that gets
watched hardest. Playwright already ships the tool that skips it:
[`storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state)
dumps the cookies and local storage from a logged-in context to a file, and a later
context loads that file and starts already authenticated. It works here unchanged,
because the browser this project returns is a real Playwright `Browser`.

This page is the concrete API - how to save, how to restore, and the one caveat that
turns a saved session from a shortcut into a flagged request: the session is bound to the
fingerprint and the exit IP that created it.

## What storage_state saves, and what it does not

`context.storage_state(path="state.json")` writes two things and only two things: the
cookies on that context and its local storage. It does not write cache, permissions,
extensions, or any profile-level setting. That is what makes it a portable file you can
open and read, and it is the reason it is the right tool for "stay logged in" rather than
"look like a browser with a history". The heavier alternative, a full
[persistent profile](persistent-profiles.md), carries all the rest and accumulates it
between runs.

Because it is cookies plus local storage, `storage_state` also does not carry the camera
or microphone permission that would otherwise disable part of Firefox's WebRTC address
protection - that trap belongs to persistent profiles, not to a saved-state file.

## Save the session once

Log in one time, from a session using the exact identity you intend to keep, then capture
the state immediately while the session is fresh. The two-line launch is the only thing
that differs from stock Playwright:

```python
from invisible_playwright import InvisiblePlaywright

SEED = 42  # fixed on purpose - the next section explains why

with InvisiblePlaywright(seed=SEED) as browser:
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://example.com/login")
    page.fill("#username", "your-account")
    page.fill("#password", "your-password")
    page.click("#submit")                       # mouse arcs to the button on a Bezier curve
    page.wait_for_url("https://example.com/account")

    # cookies + local storage to disk, while the session is live
    context.storage_state(path="state.json")
```

Run this once, confirm `state.json` exists, and do not run the login form again unless
the saved session actually stops working. Every method here is stock Playwright: the
object `InvisiblePlaywright` returns is a real `Browser`, so `new_context`, `new_page`
and `storage_state` behave exactly as documented upstream.

## Restore it on every later run

Every run after the first loads the file into a fresh context and never touches the login
form:

```python
from invisible_playwright import InvisiblePlaywright

SEED = 42  # MUST match the seed that created state.json

with InvisiblePlaywright(seed=SEED) as browser:
    context = browser.new_context(storage_state="state.json")
    page = context.new_page()

    page.goto("https://example.com/account")
    # already authenticated - no username, no password, no submit click
    print(page.text_content("#account-data"))
```

The async surface is identical, with `await` in front of the page calls:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    context = await browser.new_context(storage_state="state.json")
    page = await context.new_page()
    await page.goto("https://example.com/account")
```

That is the whole API. No credentials typed, no submit click, and none of the scrutiny
attached to that specific sequence, which is the [full case for reusing a session instead
of automating the form](automating-login-vs-session-reuse.md).

## The binding you cannot see: seed and exit IP

Here is the part that is easy to skip and breaks everything. A session cookie is not just
a token the site hands back; on many sites it is bound to the fingerprint and the exit IP
that were present at the moment of login. Restoring `state.json` into a context with a
different seed, or behind a different exit IP, is exactly the mismatch that invalidates
it.

The seed is why the launch above pins `seed=42` on both the login run and every reuse
run. The seed derives the GPU string, the canvas hash, the audio context and the font
list - the whole machine - so a fixed seed replays the same machine that minted the
cookie. Change the seed and you are asking the site to believe one identity swapped its
graphics card and font set between two requests while keeping the same cookie, which is
the kind of cross-session inconsistency [a detector is built to catch](timezone-proxy-mismatch.md),
whether or not the token itself is still valid.

The exit IP is the other half. A cookie created behind one country and replayed from
another tells two stories at once. Keep the same proxy for the reuse runs that you used
for the login, and let the [browser timezone follow the egress IP](configuration.md) so
the exit and the browser keep agreeing. The file on disk records neither the seed nor the
proxy, so write the pairing down somewhere durable - the state file cannot remind you
which identity created it.

```python
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

# login run and every reuse run: same seed, same proxy
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    context = browser.new_context(storage_state="state.json")
    ...
```

## What reusing a session does and does not fix

This is worth stating plainly, because a saved session is often sold as more than it is.
invisible_playwright is built to look like a real Firefox driven by a real person: the
fingerprint, the TLS handshake and the driver layer read as a genuine browser, which is
why it passes most in-page detection. Reusing `storage_state` on top of that skips the
highest-scrutiny flow on the site entirely, and does it the way a real returning visitor
does - carrying a session cookie rather than retyping a password.

What it does not do, on its own:

- **IP reputation.** A perfect browser holding a valid cookie on a datacenter or
  already-blocked address still loses. You supply the clean exit.
- **Per-account quotas and rate limits.** These are counted server-side, per account and
  per address. A saved session does not raise the ceiling; it makes it easier to hit it
  faster.
- **Behaviour and timing.** A session that only ever loads one URL and never scrolls or
  moves is its own signal. Reusing a cookie does not supply human pacing - you do.

A saved session is a real advantage on the fingerprint and the flow. It is not an
absolute-evasion switch, and anything sold as "undetectable" is selling the one claim
that is both false and a liability.

## Treat state.json as a live credential

The file is not a config artefact. It holds the cookies of a logged-in account, so
anyone who reads it can act as that account until the session expires. Handle it the way
you would handle a password: keep it out of the repository, out of build logs, and out of
any image you push. Give each identity its own file, never a shared one, and delete it
when the identity is retired. And treat expiry as certain rather than exceptional -
sessions end, some on a timer and some the first time the site dislikes something, so
build the re-login path once, through the same seed and the same proxy, instead of
improvising a fresh login under a new identity the moment the saved one fails.

## Conclusion

`context.storage_state(path="state.json")` and
`browser.new_context(storage_state="state.json")` are the entire mechanism, and they work
here with no changes because the browser is a real Playwright `Browser`. The one thing
that separates a clean reuse from a flagged one is off the page: the cookie, the seed
that created the fingerprint, and the exit IP are a set, and reusing one without the
others is the mismatch that invalidates the session. Log in once, save immediately,
replay from the same seed behind the same proxy, and keep the file as carefully as the
password it stands in for.

## Short answers to the questions that lead here

**How do I save a login session in Playwright?** Call
`context.storage_state(path="state.json")` on a logged-in context. It writes the cookies
and local storage to that file.

**How do I reuse it?** Pass it back in with
`browser.new_context(storage_state="state.json")`. The new context starts authenticated,
with no login form to run.

**Does this work with invisible_playwright?** Yes, unchanged. The returned `browser` is a
real Playwright `Browser`, so `storage_state` and `new_context` behave exactly as
upstream.

**Why does my restored session get flagged even though the cookie is valid?** Because many
sites bind the session to the fingerprint and exit IP that created it. Restore it under a
different seed or a different country and the cookie is asking the site to believe the
same identity changed machines. Reuse the same seed and the same proxy.

**Is storage_state safe to commit or share?** No. It is a live credential for the account
that logged in. Keep it out of version control and logs, one file per identity, and
delete it when done.

**Does a saved session stop me from getting blocked?** It helps with the fingerprint and
skips the login flow. It does nothing about IP reputation, account quotas, rate limits or
robotic timing - those you still have to supply.

## Sources

- Playwright's
  [`storage_state`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state)
  API reference, which documents exactly what the saved file contains.
- Playwright's own guide to
  [reusing signed-in state](https://playwright.dev/python/docs/auth), the same
  save-once-restore-later pattern with `new_context` used throughout this page.
- This project's seed-derived fingerprint model, described on the
  [quickstart](quickstart.md) page, applied here to a saved session rather than a
  debugging run.

**See also:** [why automating the login form is riskier than reusing a session](automating-login-vs-session-reuse.md)
for the full case behind this API, [how to scrape data behind a login with Playwright](how-to-scrape-behind-login-playwright.md)
for the same pattern applied end to end, and [persistent profiles: what they fix and
break](persistent-profiles.md) for when a saved file is not enough.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The cookie and the machine
that minted it are a pair; this page is mostly about keeping them together.*
