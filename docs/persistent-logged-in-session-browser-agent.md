---
title: "Give a browser agent a persistent logged-in session"
description: "Reuse saved storage_state or profile with a pinned seed to keep agents logged in and looking like the same returning device, plus honest limits."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 12
---


# Give a browser agent a persistent logged-in session

An agent that authenticates at the start of every task is doing the one thing most
likely to get an account flagged: logging in, over and over, from a session that
remembers nothing about the last time. Each login is a fresh risk event, and a fresh
risk event on a returning account reads worse than no event at all.

The fix is to log in once, save the session, and hand it back to the agent on every
later run. Pair that saved session with a fixed seed and the returning agent looks like
the same device coming back, not a new one that happens to have your cookies. This page
shows the two-mechanism approach with the real API, and it is honest about the part a
fingerprint does not touch: the login itself.

## Why a returning session beats logging in every task

Logging in is the highest-scrutiny moment in a session. It is where a step-up prompt can
appear, where a one-time factor gets asked for, and where first-login risk scoring runs.
Doing it once and reusing the result means the agent spends almost all of its time as an
already-authenticated visitor, which is a far quieter thing to be.

Reuse also removes a whole class of brittle automation. An agent that does not have to
find the login form, fill it, and survive whatever the form throws back is an agent with
fewer places to break. The login flow is worth
[treating as riskier than the session reuse it replaces](automating-login-vs-session-reuse.md),
and the cheapest way to run it rarely is to not run it at all.

## Reuse the login state with storage_state

`storage_state` is the portable half of this. It captures cookies and local storage from
an authenticated context and replays them into a fresh one. It is a plain JSON file you
can open and read, and it carries nothing else: no cache, no permission decisions, no
extensions.

Authenticate once and write the file:

```python
from invisible_playwright import InvisiblePlaywright

# First run: log in once, then save the session to disk.
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com/login")
    # complete the login however the site requires, then:
    context.storage_state(path="session.json")
```

Every later run restores it and skips the login entirely:

```python
from invisible_playwright import InvisiblePlaywright

# Later runs: same seed, restore the saved session, no re-login.
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(storage_state="session.json")
    page = context.new_page()
    page.goto("https://example.com/dashboard")   # already authenticated
```

The `browser` object is a real Playwright `Browser`, so `new_context(storage_state=...)`
and `context.storage_state(path=...)` are the standard upstream methods,
[documented](https://playwright.dev/python/docs/auth) and unchanged. The wrapper adds a
consistent fingerprint underneath them, not a new API to learn.

## Pin the seed so the returning session is the same device

`storage_state` restores who you are logged in as. The seed restores what machine you are
logged in from. You want both to be stable, and stable together.

Every session in this engine is generated from a seed: GPU, canvas hash, audio context,
fonts, screen and the rest, roughly four hundred fields. Leave the seed out and each run
draws a new device. Pass the same seed and every one of those fields comes back identical:

```python
with InvisiblePlaywright(seed=42) as browser:
    ...   # same GPU, same canvas hash, same audio context, every run
```

Now put the two together. If the saved session says "this account was here last week" and
the seed changes between runs, you have described an account whose hardware was swapped
overnight while its cookies stayed put. Nobody replaces a graphics card between two visits
and keeps their session. One saved session file, one seed, held together for the life of
that identity. This is the same reasoning behind
[deriving a stable agent identity from a seed](ai-browser-agents-stealth.md): the returning
device has to match the device that left.

## What the engine does not do at login

This is the honest boundary, and it is worth stating plainly because the reuse approach is
built precisely to route around it.

A fingerprint decides what your browser looks like. It does not decide any of the friction
that lives at the login step itself:

- **A one-time factor or step-up prompt** is asked of the account, not the browser. A
  matching device can make the prompt less likely to appear, but when it does appear, the
  engine has nothing to say to it. That is your automation's problem to handle, or your
  reason to log in by hand once and save the state.
- **First-login risk scoring** weighs the account's history, the exit address and the
  timing, none of which a canvas hash changes.
- **Behaviour during authentication** - the rhythm of typing a password, the pause before
  a submit - is watched on some flows, and a fingerprint does not pace your agent for you.

And the caveats that apply to any session apply here too. Looking like a real browser does
not fix a datacenter IP with a bad reputation, per-account quotas, rate limits, or the
timing tells of an agent that acts faster than a person. Those are yours to supply: a clean
exit, human pacing, and requests spaced the way a person spaces them. The engine makes the
browser genuine; it does not make the account or the network behave.

## A profile when the identity should accumulate more than cookies

`storage_state` is enough for "log in once and stay logged in". When you want the identity
to look used - a cache that has grown, permissions that were decided, settings that
accumulated - a persistent profile directory is the mechanism, because it carries the
things you would never think to fake:

```python
with InvisiblePlaywright(seed=42, profile_dir="/profiles/identity-42") as browser:
    ...
```

The pairing rule is the same and stricter: one directory, one seed, permanently, and never
two processes against the same directory at once. A profile carries traps that a plain JSON
state file does not, including a stored camera permission that can quietly switch off WebRTC
address protection, so read
[what a persistent profile fixes and breaks](persistent-profiles.md) before you grow one.
If your instinct is to point the engine at the Chrome or Firefox profile on your own laptop,
[that is a different question with a different answer](can-i-use-my-real-browser-profile-playwright.md).

## Conclusion

Persistence for an agent is two files doing two jobs: a saved session that says who is
logged in, and a seed that says which device is logging in. Keep them paired and the
returning agent is the same authenticated device coming back, which is the quiet case a
detector has the least to say about.

What it buys you is that the login stops happening on every task. What it does not buy you
is the login itself: the prompts, the risk scoring and the account-level limits are outside
what any fingerprint changes, and a clean proxy and human pacing are still yours to bring.
Reuse the state, pin the seed, and spend the rest of your effort on the two things the
engine was never going to do for you.

## Short answers to the questions that lead here

**How do I keep a browser agent logged in across runs?** Save `storage_state` after the
first login and restore it with `new_context(storage_state=...)` on every later run, with
the same seed each time.

**Does reusing the session hide the agent?** It removes repeated logins, which is the
scrutinised moment. It does not change the machine, the IP or the pacing.

**Why pin the seed as well as saving the session?** So the returning account looks like the
same device. A stable session with a changing device is a contradiction no real user
produces.

**Does the engine solve MFA or a login challenge?** No. A one-time factor and step-up
prompts are asked of the account, not the browser. Handle them in your automation, or log
in by hand once and reuse the saved state.

**storage_state or a persistent profile?** `storage_state` for logins, a profile when the
identity should also accumulate cache, permissions and settings over time.

**Will a good fingerprint fix my blocks after login?** Not on its own. IP reputation,
per-account quotas, rate limits and behaviour timing are separate, and they are the reader's
to supply.

## Sources

- This project's quickstart and configuration pages for the seed, proxy and session API
  shown above, read from the shipped wrapper rather than from memory.
- Standard [Playwright `storage_state` and persistent-context behaviour](https://playwright.dev/python/docs/auth),
  which the wrapper exposes unchanged because the returned object is a real Playwright `Browser`.
- This project's own notes on the profile-and-seed pairing and the stored-permission trap,
  linked below.

**See also:** [automating the login form versus reusing a session](automating-login-vs-session-reuse.md),
[what a persistent profile fixes and breaks](persistent-profiles.md), and
[scraping behind a login with Playwright](how-to-scrape-behind-login-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine makes the browser
genuine; the login friction and the network are still yours to handle.*
