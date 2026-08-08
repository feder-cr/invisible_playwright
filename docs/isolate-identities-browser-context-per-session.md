---
title: "Isolate identities with a browser context per session"
description: "Run one browser context per account or task to keep cookies, storage and cache from bleeding between sessions, and the caveat that storage isolation is not IP isolation."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 39
---


# Isolate identities with a browser context per session

If two automated sessions share cookies, local storage or cache, they are the same
session as far as a site is concerned, no matter how careful the rest of your code is.
A browser context is the unit that fixes this. Each one is a fresh cookie jar, its own
storage and its own cache, with no shared state, so running one context per account or
per task keeps their sessions from bleeding into each other.

This page is what a context isolates, how to run one per session in real code, and the
sharp caveat that trips people up: isolating storage is not isolating your network
identity. Two contexts in one process still share the machine and, unless you say
otherwise, the same exit IP.

## What a browser context actually isolates

A context is Playwright's boundary for browser state. Open two of them and you get two
independent sets of:

- **Cookies.** A login in one context does not appear in the other.
- **Local storage, session storage and IndexedDB.** The DOM-side stores are separate.
- **The HTTP cache and service workers.** Nothing one context fetched is served to the
  other from cache.

That is exactly the state a site uses to decide "I have seen this session before". Keep
it separate and each context looks like a session that started clean, because from a
storage standpoint it did. This is why one context per account or per task is the right
default: a task that logs in, does its work and closes its context leaves nothing behind
for the next one to inherit.

A single page opened with `browser.new_page()` lives in a default context, which is fine
for one session. The moment you run more than one session in the same process, make the
context explicit so the boundary is one you drew rather than one you assumed.

## One context per session, in code

Switching from plain Playwright is a two-line change, and after that every method is the
standard Playwright API. The `browser` object is a real Playwright `Browser`, so
`new_context()` and everything on the context object work exactly as documented upstream.

```python
from invisible_playwright import InvisiblePlaywright

# one browser process, one context per task, each with a clean cookie jar
with InvisiblePlaywright(seed=42) as browser:
    for task in ("task-a", "task-b", "task-c"):
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://example.com/")
        # ... do the work for this task ...
        context.close()   # cookies, storage and cache for this task go with it
```

Each `new_context()` call above starts from nothing: no cookie set by an earlier task,
no cached response, no storage key. Closing the context discards all of it. If task B
must not be recognisable as the same visitor that ran task A, this is the mechanism that
keeps them apart at the storage layer.

The async API is identical in shape:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://example.com/")
    await context.close()
```

## Storage isolation is not network identity isolation

Here is the part that matters most and gets skipped most often.

Separate contexts in one process still share the host machine and, unless you set a
per-context proxy, the same exit IP. Isolation of storage is not isolation of network
identity. Two contexts with empty, unrelated cookie jars can still be tied together
because they:

- **Leave from the same IP address.** Two "different" sessions arriving from one exit,
  seconds apart, are one network identity regardless of their cookies.
- **Share the same host fingerprint.** They run in one browser process on one machine, so
  the GPU, audio device, fonts and screen are identical across both. A page that reads
  those surfaces sees the same machine twice.

So the rule to carry off this page: **a fresh context makes two sessions look like two
clean starts, not like two different people.** If the identities only need to not share a
login, a context per task is enough. If they need to look unrelated to a site that
correlates across sessions, storage isolation alone will not get you there.

## When identities must look unrelated: a seed and a proxy each

When the identities have to look like different people, pair a distinct context with a
distinct exit and a distinct fingerprint.

The exit is the cheap half. Playwright lets a context carry its own proxy, so you can
route each context through a different egress:

```python
with InvisiblePlaywright(seed=1) as browser:
    ctx_a = browser.new_context(proxy={"server": "socks5://gate-a.example.com:1080",
                                        "username": "u", "password": "p"})
    ctx_b = browser.new_context(proxy={"server": "socks5://gate-b.example.com:1080",
                                        "username": "u", "password": "p"})
```

The fingerprint is the other half, and it is set when the browser launches: every session
is generated from a seed, and the same seed gives the same GPU, canvas hash, audio context,
fonts and screen every run. To give each identity its own machine as well as its own exit,
launch a separate `InvisiblePlaywright` per identity, each with its own seed and proxy:

```python
def run_identity(seed, proxy):
    with InvisiblePlaywright(seed=seed, proxy=proxy) as browser:
        page = browser.new_page()
        page.goto("https://example.com/")
        # ... this identity's work, on its own machine and its own exit ...

run_identity(101, {"server": "socks5://gate-a.example.com:1080", "username": "u", "password": "p"})
run_identity(202, {"server": "socks5://gate-b.example.com:1080", "username": "u", "password": "p"})
```

By default, with no explicit `timezone=`, the browser timezone is auto-derived from each
proxy's egress IP, so a distinct exit brings a matching timezone with it rather than a
zone from your own machine. The result is three things that agree per identity: storage,
network exit and fingerprint. That combination is what looks unrelated. A shared exit with
different cookies does not.

If you need this at scale across many workers, the mechanics of one internally consistent,
reproducible fingerprint per worker are covered in
[run parallel browser agents with distinct fingerprints](parallel-browser-agents-distinct-fingerprints.md),
which states the same honest limit: one shared exit still links them.

## What a context per session does not fix

invisible_playwright is designed to look like a real browser driven by a real person, and
that is why it passes most detection checks: the fingerprint, the TLS handshake and the
driver layer read as a genuine Firefox rather than as automation. Context isolation adds
clean, separate state on top of that. Neither one, on its own, fixes the parts of a
session that are not browser properties at all:

- **IP reputation.** A clean context on a known, flagged or datacenter address is still on
  that address. See [can websites detect a datacenter proxy IP](can-websites-detect-a-datacenter-proxy-ip.md).
- **Per-account quotas and rate limits.** Isolating storage does not raise a limit the
  account already hit. Two contexts hammering one endpoint from one IP create the velocity
  signal you were trying to avoid.
- **Behaviour and timing.** Pointer motion, typing rhythm and the pace of your requests are
  yours to supply. Human pacing between actions is a thing you add, not a thing a context
  gives you.

You supply those: a clean proxy, sane per-account limits, and pacing that is not uniform.
The browser being convincing is necessary, not sufficient, and this page is about one
specific necessary piece: keeping sessions from sharing state they should not.

## Conclusion

A browser context is the state boundary. One per account or per task gives every session a
clean cookie jar, its own storage and its own cache, which keeps their sessions from
bleeding into each other. That is real isolation, and it is the correct default whenever
you run more than one session in a process.

Just do not read it as more than it is. Storage isolation is not network identity
isolation. When the identities must look unrelated, pair each one with its own exit and its
own seed-derived fingerprint, and remember that the IP reputation, the account limits and
the pacing are still yours to get right.

## Short answers to the questions that lead here

**Does a new browser context clear cookies and storage?** Yes. Each context is a fresh
cookie jar with its own local storage, session storage, IndexedDB and cache, and closing
it discards all of them.

**Is one context per account enough to keep them separate?** For state, yes: neither
account sees the other's login or cache. For looking unrelated to a site that correlates
across sessions, no, because both contexts still share the exit IP and the host machine.

**Can each context use its own proxy?** Yes. Playwright accepts a per-context proxy, so you
can route each context through a different exit. Storage isolation without a per-context
proxy leaves both on the same IP.

**Context per session or a separate browser per identity?** Use a context per session for
state isolation within one identity. Use a separate seeded `InvisiblePlaywright` per
identity when each one also needs its own fingerprint and exit.

**Does isolating storage hide that two sessions are the same machine?** No. Contexts in one
process share the GPU, audio, fonts and screen, so a page reading those surfaces sees the
same machine. Different seeds on separate launches are what change the machine.

**Will this stop me being blocked?** It stops sessions leaking state into each other. It
does not fix a bad IP, an account over its quota, or robotic timing, which you supply
separately.

## Sources

- The real product API in [Quickstart](quickstart.md) and [Configuration](configuration.md):
  the two-line launch, the seed, per-launch proxy and the auto-derived timezone.
- Playwright's own [browser context documentation](https://playwright.dev/python/docs/browser-contexts)
  for the isolated, incognito-like storage model described above, and its
  [network documentation](https://playwright.dev/python/docs/network) for setting a
  proxy per context.
- This project's parallel-agents notes for the shared-exit limit that context isolation
  does not remove.

**See also:** [run parallel browser agents with distinct fingerprints](parallel-browser-agents-distinct-fingerprints.md),
[Playwright persistent profile: what it fixes and breaks](persistent-profiles.md), and
[why automating the login form is riskier than reusing a session](automating-login-vs-session-reuse.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A context keeps sessions
apart; the proxy and the pacing are still yours to get right.*
