---
title: "Read and set cookies in a Playwright context"
description: "Read the cookie jar with context.cookies() and seed tokens with context.add_cookies(). Caveat: hand-set cookies can look unearned versus real visit cookies."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 36
---


# Read and set cookies in a Playwright context

Cookies in Playwright do not belong to a page. They belong to the browser context,
which is the isolated session a page runs inside. That single fact is why you can
inspect every cookie a site has set with one call, and why you can inject a cookie
before the first navigation instead of clicking through a UI to earn it.

This page covers both directions: reading the jar with `context.cookies()`, seeding a
value with `context.add_cookies()`, and the caveat that a cookie you set by hand can
look unearned in ways a real visit's cookie does not.

## The cookie jar lives on the context, not the page

Every page you open shares the cookie jar of the context that created it. When a page
navigates and the server sends `Set-Cookie`, the value lands in the context. Open a
second page in the same context and it sees that cookie too. Open a page in a fresh
context and it starts with an empty jar.

So the two operations you care about, reading and writing cookies, are both methods on
`BrowserContext`, not on the page:

- [`context.cookies()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-cookies) returns the current jar as a list of dictionaries.
- [`context.add_cookies([...])`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-add-cookies) injects cookies into the jar.

You reach the context either by creating one explicitly with `browser.new_context()`,
or from any page you have already opened via `page.context`.

## Read the jar with context.cookies()

`context.cookies()` returns whatever is in the jar right now: everything the site set
during the session, plus anything you injected yourself. Each entry is a dictionary
with `name`, `value`, `domain`, `path`, `expires`, `httpOnly`, `secure` and
`sameSite`.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://example.com")

    for cookie in context.cookies():
        print(cookie["name"], "=", cookie["value"], "domain", cookie["domain"])
```

Pass a URL or a list of URLs to filter the jar down to what a given origin would send:

```python
    session_cookies = context.cookies("https://example.com")
```

Reading the jar is the fastest way to answer "did the login actually take" without
scraping the page: if the session cookie the server issues on success is present, the
session is authenticated, whatever the page happens to render.

## Seed a cookie before the first navigation with add_cookies()

`context.add_cookies([...])` writes cookies into the jar directly. Call it before your
first `goto` and the very first request already carries the cookie, so you can set a
consent flag or restore an auth token without driving the UI that would normally
produce it.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context()
    context.add_cookies([
        {
            "name": "consent",
            "value": "1",
            "domain": "example.com",
            "path": "/",
            "secure": True,
            "sameSite": "Lax",
        },
    ])

    page = context.new_page()
    page.goto("https://example.com")   # first request already carries `consent=1`
```

Each cookie needs either a `url`, or a `domain` and `path` pair. Give it a `url` and
Playwright derives the domain, path and the `secure` flag from it; give it `domain`
and `path` and you are stating them yourself, which is where the caveat below begins.

A common pattern is to save the jar from one session and replay it into the next:

```python
    # end of session one
    saved = context.cookies()

    # start of session two, a fresh context
    context.add_cookies(saved)
```

That reuses a session the browser already earned rather than re-running the login
flow, which is [why reusing a session is safer than automating the login form](automating-login-vs-session-reuse.md).

## What a hand-set cookie cannot fake

Here is the honest limit. A cookie the server set during a real visit carries
attributes the server chose, at a time the visit actually happened. A cookie you type
into `add_cookies()` carries the attributes you chose, and they are easy to get subtly
wrong.

The mismatches that give a hand-set cookie away:

- **Domain and path** that do not match where the site actually scopes the cookie. A
  session cookie set on a narrower host than you assumed will simply not be sent on the
  requests you expected.
- **`secure` and `sameSite`** that differ from what the site issues. If the real
  cookie is `Secure; SameSite=Lax` and yours is neither, that is a value the site never
  writes.
- **An implausible timestamp.** A cookie with an `expires` far in the future, or a
  freshly injected value that the site's own flow would have paired with several other
  cookies, is a jar that does not look like one a browser built by visiting.

None of this is unique to any one tool. It is a property of injecting state instead of
earning it, and the fix is the same everywhere: prefer a jar captured from a real
visit over one assembled field by field, and when you do set a cookie by hand, copy
the attributes the site actually uses rather than guessing them. A cookie that
[does not match what the site would have set](how-to-scrape-behind-login-playwright.md)
is a weaker signal than one the server issued.

## The same behaviour on invisible_playwright, and what it does not fix

`invisible_playwright` returns a real Playwright `Browser`, so `context.cookies()` and
`context.add_cookies()` behave exactly as they do upstream, with the same dictionary
shape and the same rules. There is nothing new to learn and no wrapped subset of the
cookie API.

What the product does add is underneath the cookie layer. The browser is a Firefox
patched at the C++ level and driven by stock Playwright, so its fingerprint, its TLS
handshake and its driver surface read as a genuine Firefox rather than an automated
one. That is why it passes most fingerprint and driver checks: there is no
`navigator.webdriver` tell, no software renderer, no headless font set. Every field is
derived from a seed, so the same seed gives the same machine on every run, and a
failing session can be replayed exactly.

The caveat is the honest part. Looking like a real browser does not, on its own, fix:

- **IP reputation.** An injected cookie still rides on whatever address the context
  presents. A perfect jar on a known datacenter IP still loses. Supply a clean proxy;
  see [Configuration](configuration.md).
- **Per-account quotas and rate limits.** A restored session cookie does not raise the
  ceiling the account already has.
- **Behaviour and timing.** A cookie set in the first millisecond of a session, then
  used to hammer an endpoint, is a velocity signal no fingerprint hides. Human pacing
  is yours to supply.

In short: `invisible_playwright` makes the browser look real, and you make the session
plausible. A cookie is state, and state has to be consistent with the identity and the
network carrying it. When something still gets flagged with a clean jar, work
[the detection checklist](playwright-detected-as-bot.md) in order rather than assuming
the cookie was the cause.

## Conclusion

`context.cookies()` reads the jar and `context.add_cookies()` seeds it before the
first navigation, both on the context rather than the page, and both behave identically
on `invisible_playwright` because it returns a real Playwright `Browser`. Reading the
jar is the cleanest way to confirm a session is authenticated; seeding it lets you skip
a UI flow. Just remember that a hand-set cookie whose domain, `secure`, `sameSite` or
timestamp do not match what the site would issue is a weaker signal than one earned by
a real visit, and that no cookie changes the IP, the quota or the pace behind it.

## Short answers to the questions that lead here

**How do I read cookies in Playwright?** Call `context.cookies()` on the browser
context. It returns the whole jar as a list of dictionaries; pass a URL to filter it to
one origin.

**Cookies() is not on the page. Where is it?** On the context. Reach it with
`browser.new_context()` or from an open page via `page.context`.

**How do I set a cookie before the page loads?** Call `context.add_cookies([...])`
before your first `goto`, so the first request already carries it. Each cookie needs
either a `url` or a `domain` and `path`.

**Can I save cookies from one run and reuse them in the next?** Yes. Store
`context.cookies()`, then feed the list to `context.add_cookies()` in a fresh context.

**Is a cookie I set by hand as good as one the site set?** Not quite. If its domain,
`secure`, `sameSite` or expiry do not match what the site issues, it looks unearned.
Prefer a jar captured from a real visit.

**Does seeding an auth cookie make me undetectable?** No. The cookie still rides on
your IP, your fingerprint and your pace. `invisible_playwright` handles the browser
fingerprint; the proxy and the timing are yours.

## Sources

- The Playwright `BrowserContext` cookie API (`cookies`, `add_cookies`), read from its
  documented behaviour rather than a rendered example.
- This project's own quickstart and configuration notes for the launch API and the
  proxy and timezone surfaces a cookie rides on.

**See also:** [why reusing a session beats automating the login form](automating-login-vs-session-reuse.md),
[persistent profiles that keep the jar across launches](persistent-profiles.md), and
[the checklist for when a clean session is still detected](playwright-detected-as-bot.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The cookie API is stock
Playwright; the part worth stating twice is that state has to be consistent with the
identity carrying it.*
