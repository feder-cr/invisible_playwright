---
title: "Combine invisible_playwright with httpx for speed"
description: "Clear the fingerprint-gated entry in a real browser, export storage_state, then hand the cookies to httpx for cheap high-volume follow-up requests - and the sharp limit."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 89
---


# Combine invisible_playwright with httpx for speed

A browser is expensive. It starts a process, renders a page, runs the site's
JavaScript, and holds memory the whole time. If you need one page a minute that is fine.
If you need a thousand JSON responses from an endpoint the site already exposes, paying
browser cost for each one is the slow way to do it.

The fast way is a split: use the browser for the part that needs a browser, and use a
bare HTTP client for the part that does not. This page shows the handoff concretely -
export the session the browser established, hand it to `httpx`, and let `httpx` do the
bulk - and it is equally clear about the one boundary where the trick stops working,
because that boundary is the whole skill.

## Why the browser clears the door and httpx does not

`invisible_playwright` is a Firefox patched at the C++ level and driven by stock
Playwright. It is built to look like a real browser driven by a real person: the
fingerprint, the TLS handshake and the driver layer all read as a genuine Firefox, which
is why it clears the checks that turn plain automation away at the door. That is the
part you cannot fake with a bare HTTP client. An endpoint that inspects the JavaScript
fingerprint, or reads the TLS handshake, expects a browser to be on the other end.

`httpx` is the opposite tool on purpose. It is a fast, async-capable Python HTTP client
with no JavaScript engine and no browser TLS stack. It sends a plain client handshake and
whatever headers you give it, and nothing else. That is exactly why it is cheap, and
exactly why it cannot pass a fingerprint check. It has no fingerprint to present.

So the division is not a hack, it is the honest shape of the problem. The browser is for
the fingerprint-gated fetches. `httpx` is for the unguarded bulk. Knowing which endpoint
is which is the entire exercise, and most of this page is about telling them apart.

## Establish the session in the browser

First, do the guarded part properly: launch the real browser, clear whatever gates the
entry (a login, a challenge page, a first navigation that sets a session cookie), and let
it settle. Switching from plain Playwright is two lines, and the object you get back is a
real Playwright `Browser`, so every method below is the stock API:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, proxy={
    "server": "socks5://gate.example.com:1080",
    "username": "u", "password": "p",
}) as browser:
    page = browser.new_page()
    page.goto("https://example.com/app")
    # ... do whatever establishes the session: submit a form, pass an
    # interactive check, land on the page that issues the session cookie ...
    page.wait_for_load_state("networkidle")
```

The seed is not decoration here. It pins the identity, so the fingerprint that created
this session is reproducible - which matters the moment you reuse the session, because a
reused session is still tied to the machine that created it. That coupling is the subject
of [why reusing a session beats re-automating the login form](automating-login-vs-session-reuse.md).

## Export storage_state and hand it to httpx

Playwright already has the export you need.
[`context.storage_state()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state)
returns the cookies and local storage the session accumulated, in a plain dict. Capture it
while the browser is still open:

```python
    state = page.context.storage_state()
    # state == {"cookies": [{"name": ..., "value": ..., "domain": ...}, ...],
    #           "origins":  [{"origin": ..., "localStorage": [...]}, ...]}
```

Now the browser has done its job. Everything after this can happen in a cheap client. Map
the cookies into an `httpx.Client` and fire the follow-up requests concurrently:

```python
import httpx

cookies = httpx.Cookies()
for c in state["cookies"]:
    cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

# route httpx through the SAME exit the browser used, see the caveat below
proxy = "socks5://u:p@gate.example.com:1080"

with httpx.Client(cookies=cookies, proxy=proxy, http2=True,
                  headers={"User-Agent": page.evaluate("navigator.userAgent")}) as client:
    for page_num in range(1, 200):
        r = client.get(f"https://example.com/api/items?page={page_num}")
        r.raise_for_status()
        handle(r.json())
```

Two hundred JSON pages just came back without starting two hundred browsers. If the bulk
endpoint returns the same data the page renders, this is also the moment to stop parsing
HTML and read the response directly, the same idea covered in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md).

## The boundary: any endpoint that re-checks the fingerprint rejects the httpx leg

Here is the sharp edge, and it is not negotiable. `httpx` carries the cookies but sends
no browser fingerprint. It presents a plain TLS handshake and no JavaScript surface at
all. So the handoff works on exactly one kind of endpoint: one that trusts the cookie and
does not re-inspect the caller.

Plenty of endpoints are like that. A paginated data API behind a session cookie, a
download URL, an internal JSON route the page itself calls - these usually check that you
hold a valid session and nothing more. The `httpx` leg sails through.

But some endpoints re-run the check on every request. They read the TLS handshake, or
require a token that only the site's JavaScript can mint, or expect a browser-shaped
request signature alongside the cookie. Against those, the cookie is necessary but not
sufficient, and the bare client is refused - often with a `403` or a challenge page
where the browser got JSON. There is no client-side trick that gives `httpx` a browser
handshake; if the endpoint re-checks the fingerprint, that endpoint stays in the browser.

The way you find the boundary is empirical and cheap: send one `httpx` request to the
target endpoint with the exported cookies. If it returns the same body the browser got,
that endpoint is safe to bulk. If it returns a challenge, an error, or a suspiciously
short body, it is a re-checked endpoint and it belongs in the browser leg. Test one
before you loop over a thousand.

## What this speeds up, and what it does not touch

The split is a cost optimization, not a stealth upgrade. It makes a workload cheaper by
moving the unguarded requests off the browser. It does nothing for the four things that
block sessions independently of the fingerprint, and moving traffic to `httpx` can make
some of them worse if you are not careful:

- **IP reputation.** Route `httpx` through the same clean exit the browser used. If the
  bulk requests go out from your datacenter host directly while the browser used a
  residential proxy, you have split the session across two addresses, which is its own
  signal. Same exit for both legs.
- **Rate limits and per-account quotas.** Two hundred requests in two seconds from one
  session is a velocity signal no fingerprint hides. Pace the `httpx` loop. The browser
  being invisible does not raise the account's quota.
- **Timezone and locale consistency.** The session was created with a browser identity
  tied to the exit; keep the follow-up on the same exit so the story stays coherent, the
  same failure mode described in [timezone and proxy mismatch](timezone-proxy-mismatch.md).
- **Behaviour on the guarded leg.** The browser still has to pass the guarded entry
  convincingly. The speed trick only applies after that door is open.

`invisible_playwright` gives you the browser that looks real. You supply the clean exit,
the human pacing, and the judgement about which endpoints can leave the browser. The tool
does one job well and is honest about the rest.

## Conclusion

The hybrid pattern is simple once the boundary is clear. The browser establishes the
session because only a browser presents a real fingerprint and TLS handshake. You export
`storage_state`, hand the cookies to `httpx`, and let the cheap client do the
high-volume follow-up - but only against endpoints that trust the cookie and do not
re-inspect the caller. The moment an endpoint re-checks the fingerprint, that request
goes back into the browser, where the fingerprint lives. Test one request before you loop,
keep both legs on the same clean exit, and pace the bulk. That is the whole method.

## Short answers to the questions that lead here

**Can I do all my scraping with httpx and skip the browser?** Only for endpoints that do
not check a fingerprint. The browser exists to clear the ones that do; `httpx` has no
fingerprint to present, so anything that re-checks one will refuse it.

**How do I move a logged-in session from the browser to httpx?** Call
`page.context.storage_state()` while the browser is open, then set its cookies on an
`httpx.Client`. The cookies carry the session; the handshake does not.

**Why does httpx get a 403 where the browser got JSON?** That endpoint re-checks the
caller, not just the cookie. It reads the TLS handshake or wants a browser-minted token.
Keep that request in the browser.

**Do I need the same proxy for both legs?** Yes. Split the session across two exit
addresses and you have created a mismatch signal. Route `httpx` through the same exit the
browser used.

**Does this make me undetectable?** No, and nothing does. It is a speed optimization. It
does not fix IP reputation, account quotas, rate limits, or behaviour - you supply a
clean exit and human pacing for those.

**How do I know which endpoints are safe to bulk?** Send one `httpx` request with the
exported cookies and compare the body to what the browser got. Same body, safe to loop.
Challenge or short body, keep it in the browser.

## Sources

- The real `invisible_playwright` API as documented in
  [Quickstart](quickstart.md) and [Configuration](configuration.md): the two-line launch,
  the reproducible seed, and the proxy dict both legs must share.
- Playwright's own
  [`context.storage_state()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-storage-state),
  which returns cookies and local storage as a plain dict, working exactly as documented
  upstream because the object handed back is a real Playwright `Browser`.
- This project's own practice of testing one request against the boundary before looping,
  the same empirical habit the rest of these notes are built on.

**See also:** [why reusing a session beats re-automating the login](automating-login-vs-session-reuse.md)
and [how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading the JSON the page already fetches.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The browser is for the
fingerprint-gated fetches, httpx is for the unguarded bulk, and telling them apart is the
whole job.*
