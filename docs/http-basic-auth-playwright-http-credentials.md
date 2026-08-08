---
title: "Handle HTTP basic auth in Playwright (http_credentials)"
description: "How to pass http_credentials in Playwright to handle 401 WWW-Authenticate challenges, why it differs from HTML login forms, and how to keep secrets out of code."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 38
---


# Handle HTTP basic auth in Playwright (http_credentials)

A staging environment or an internal page behind HTTP basic auth answers the very
first request with `401` and a `WWW-Authenticate: Basic` header. In a headed browser
that pops the small native username-and-password dialog. In automation there is no one
to type into it, so the run stalls on a box Playwright cannot reach with a selector,
because it is browser chrome and not part of the page.

The fix is one context option. This page is what `http_credentials` does, the exact
launch that uses it, why it handles the `401` challenge and nothing else, and how to
keep the password out of your repository.

## What http_credentials actually answers

[`http_credentials`](https://playwright.dev/python/docs/api/class-browser#browser-new-context)
supplies the username and password Playwright sends back when a server replies to a
request with `401` and a [`WWW-Authenticate`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/WWW-Authenticate)
challenge. Playwright retries the request with an `Authorization: Basic <base64>`
header, the server accepts it, and the page loads. No dialog, no interaction, nothing
to click.

It is a property of the browser context, so every page and every request in that
context inherits it. You set it once when you create the context, not per navigation.

This is the whole mechanism. It is the same machinery a real browser uses when it
remembers the credentials you typed into that dialog once and stops asking, which is
why it reads as ordinary browser behaviour rather than as an injected header.

## The two-line launch, then a context with credentials

`invisible_playwright` is stock Playwright with a patched Firefox underneath, so the
`browser` object is a real Playwright `Browser` and `new_context` behaves exactly as
documented upstream. The switch from plain Playwright is [the same two lines as
always](quickstart.md); the credentials go on the context you open from it.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(
        http_credentials={"username": "staging", "password": "s3cret"},
    )
    page = context.new_page()
    page.goto("https://staging.example.com/")   # 401 answered automatically
    print(page.title())
    context.close()
```

The `seed=42` keeps the fingerprint reproducible so a failing run can be replayed
exactly; drop it and every session draws a fresh identity. Everything else is upstream
Playwright. The async form is identical with `await` in front of `new_page`, `goto`
and `close`, opened from `invisible_playwright.async_api`.

If you drive `firefox.launch()` yourself rather than using this class, `http_credentials`
still belongs on `new_context` in exactly the same way. It is a Playwright option, not
one of ours, so nothing about the stealth layer changes how you pass it.

## Why this is the 401 challenge, not a login form

This is the distinction that sends people to the wrong tool. Two things both called
"login" work nothing alike.

**HTTP basic auth** happens at the protocol layer, before any HTML exists. The server
refuses the request with `401`, the browser answers with a header, and the page you
asked for is what comes back. `http_credentials` handles this, start to finish, with
no page interaction at all.

**An HTML login form** is a page that already loaded successfully with `200`. It has
`<input>` fields and a submit button, and authentication is the site's own application
logic reacting to what you type. `http_credentials` does nothing here, because there is
no `401` and no `WWW-Authenticate` header to answer. For a form you fill and submit the
fields like any other:

```python
page.goto("https://app.example.com/login")
page.fill("#username", "staging")
page.fill("#password", "s3cret")
page.click("button[type=submit]")
```

The tell for which one you have is the response, not the appearance. If the browser
would show a small grey native dialog on top of a blank page, it is basic auth and
`http_credentials` is the answer. If you see a styled page with form fields, it is
application login and you fill the fields. A single site can even use both, basic auth
to gate a whole staging tier and a form inside it once you are through.

## Keep the credentials out of code and logs

`http_credentials` puts a real username and password in your context configuration.
That value is as sensitive as the account behind it, and the two easy mistakes are
committing it and printing it.

Read it from the environment rather than writing it in the source:

```python
import os
from invisible_playwright import InvisiblePlaywright

creds = {
    "username": os.environ["BASIC_AUTH_USER"],
    "password": os.environ["BASIC_AUTH_PASS"],
}

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(http_credentials=creds)
    page = context.new_page()
    page.goto("https://staging.example.com/")
```

```bash
export BASIC_AUTH_USER=staging
export BASIC_AUTH_PASS=s3cret
```

A few habits that keep it out of the places secrets leak from:

- Never log the context config or the credentials dict. A debug `print` of the whole
  options object is the most common way a password reaches a CI log where it is stored
  and searchable.
- Put the source of the value in your secret manager or CI secrets, not in a `.env`
  that gets committed by accident. Add `.env` to `.gitignore` if you use one locally.
- Remember the value also rides in the `Authorization` header on every request in that
  context. If you dump network traffic while debugging, that header carries it in
  base64, which is encoding, not encryption, and trivially reversible.

## What this does and does not get you

`http_credentials` gets a basic-auth gate out of your way. It is not a stealth feature
and it is worth being precise about what carries the disguise here and what does not.

The reason a run through `invisible_playwright` reads as a genuine visitor is the
engine underneath: a Firefox patched at the C++ level so the fingerprint, the TLS
handshake and the driver layer report as a real browser rather than an automated one.
That is what gets you past the fingerprint and driver checks described in [the
detection checklist](playwright-detected-as-bot.md). Answering a `401` is orthogonal
to all of it; a basic-auth challenge is not a bot check, it is a password prompt.

And the honest boundary, the same one that applies to every page in this set: a real
browser is necessary and not sufficient. `http_credentials` and the stealth engine
together do nothing about your exit IP's reputation, a per-account request quota, a
rate limit, or behaviour that moves faster than a person. A staging site rarely cares
about any of that, which is exactly why basic auth is common there. A production target
behind real defences still needs [a clean proxy and human pacing](why-blocked-with-a-clean-fingerprint.md)
on top of a real browser, and no context option changes that.

## Conclusion

For a page behind HTTP basic auth, `http_credentials` on the context is the whole
answer: Playwright sends the `Authorization` header when the server challenges with
`401`, and the native dialog never blocks the run. It works unchanged under
`invisible_playwright` because the `browser` is a real Playwright `Browser`. Keep it
separate in your head from an HTML login form, which it does not touch, and keep the
credentials in the environment rather than in the code that reads them.

## Short answers to the questions that lead here

**How do I get past a basic-auth dialog in Playwright?** Pass
`http_credentials={"username": ..., "password": ...}` to `browser.new_context(...)`.
Playwright answers the `401` challenge with an `Authorization` header and the page
loads with no dialog.

**Does http_credentials fill in a login form?** No. It answers the protocol-level `401`
challenge only. An HTML form loaded with `200` has no challenge to answer, so you fill
and submit its fields like any other page.

**Where do I set it, on launch or per page?** On the context. It is a `new_context`
option and every page and request in that context inherits it.

**Does it work with invisible_playwright unchanged?** Yes. The returned `browser` is a
real Playwright `Browser`, so `new_context(http_credentials=...)` behaves exactly as
upstream.

**Is basic auth a bot check?** No, it is a password prompt at the HTTP layer. Answering
it says nothing about whether the site trusts the visitor; the stealth engine handles
that separately.

**Will this hide my automation on a protected site?** Only the fingerprint, TLS and
driver layer are handled by the engine, not by this option. IP reputation, quotas, rate
limits and timing are still yours to supply with a clean proxy and human pacing.

## Sources

- [The Playwright browser-context documentation](https://playwright.dev/python/docs/api/class-browser#browser-new-context)
  for `http_credentials`, and [the WWW-Authenticate header reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/WWW-Authenticate)
  for the `401` exchange it answers.
- This project's own launch API, where the returned object is a real Playwright
  `Browser` and every context option passes through unchanged.

**See also:** [the Configuration page](configuration.md) for proxy and timezone
options that ride on the same context, [SOCKS5 proxy authentication](playwright-socks5-proxy-authentication.md)
for the other credential you commonly set, and [the detection checklist](playwright-detected-as-bot.md)
for what actually decides whether a protected site trusts the session.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. http_credentials is
plain Playwright; the realness is the engine underneath, and neither one is a substitute
for a clean exit.*
