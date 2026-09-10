---
title: "Playwright blocked by CORS policy: not bot detection"
description: "The browser enforcing a policy the server set, identically for a human tab. How to tell it apart from a real block, and three legitimate ways past it."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 31
---

# Playwright blocked by CORS policy

A request fails with something like "has been blocked by CORS policy: No
'Access-Control-Allow-Origin' header is present." This is worth stating
plainly first: **this is not bot detection.** It is the browser correctly
enforcing a same-origin policy that exists for every browser, automated or
not, and Playwright is doing exactly what a real browser does here.

## What is actually happening

CORS (Cross-Origin Resource Sharing) is a server-controlled permission system.
When JavaScript running on `page-a.com` tries to fetch a resource from
`api-b.com`, the browser sends the request and then checks whether
`api-b.com`'s response includes headers that explicitly permit that origin. If
the headers are absent or do not list your origin, the browser withholds the
response from your script - even though the request often already succeeded on
the wire.

This applies identically in a real human's Chrome tab and in a Playwright
script. It is not a signal the server is using to detect automation; it is a
security boundary the browser enforces regardless of who or what is driving it.

## How to tell it apart from a real block

**Check the network tab (or `page.on("response")`).** If the request itself
returns a normal status code and body, and the failure is purely a script-side
CORS error, that confirms it: the server answered fine, the browser withheld
the response from your JavaScript. That is CORS, not detection.

**If the request itself never completes, or returns 403/429/a challenge page,**
that is a different problem - the server refused the request outright, which
is a detection or rate-limit question, not a CORS one.
[Why does my Playwright script get blocked](why-does-my-playwright-script-get-blocked.md)
covers that case.

## The three legitimate ways past a real CORS restriction

**Do not fetch it from page-context JavaScript at all.** Playwright can make
the request directly from Node/Python via [`page.request`](how-to-capture-xhr-api-responses-playwright.md)
or an API request context, which is not subject to the browser's CORS
enforcement because it is not a page-originated `fetch()` call. If you just need the data and not a
page-side reaction to it, this is usually the simplest fix and it is not a
workaround - it is a legitimate use of Playwright's API-testing surface.

**Navigate to the resource's own origin.** If `api-b.com` serves a page you
can load directly, do the interaction there instead of cross-origin from
`page-a.com`.

**Check whether the API actually wants to be called this way.** A CORS
restriction is often the API owner saying "call this from your server, not
from a browser tab." If there is a documented server-to-server endpoint,
that is the intended path, and Playwright's `page.request` context or a plain
HTTP client outside the browser context are both appropriate ways to use it.

## What this project does and does not touch

CORS enforcement lives in the browser engine and is unrelated to fingerprinting
or detection countermeasures. Nothing about running a patched, stealth-tuned
Firefox changes CORS behaviour, and nothing here proposes bypassing it - the
same-origin policy is a real security boundary, not a bot-detection heuristic,
and defeating it from a page you do not control is a different and much more
serious thing than working around a fingerprint check.

If what led here is actually a fingerprint or automation-detection problem
that happens to surface alongside a CORS-looking error - a
[Content-Security-Policy block](content-security-policy-blocks-playwright-scripts.md)
reads similarly in the console - separate the two first using the network-tab
check above before assuming either one is the cause of the other.

## Short answers to the questions that lead here

**Does Playwright trigger CORS errors that a real browser would not?** No.
Playwright's browsers enforce the same CORS policy any browser does. If a
human hitting the same cross-origin request in their own browser would also
be blocked, Playwright blocking it is not a Playwright-specific issue.

**Can I disable CORS in Playwright?** You can launch Chromium with
`--disable-web-security` for local testing, which is a real footgun outside a
throwaway test context and does not apply to Firefox. It changes the
browser's own enforcement, not what the target server permits, and using it
against a real third-party site is not something this project recommends.

**Is a CORS error the same as being blocked by anti-bot protection?** No.
Confirm which one you have with the network-tab check above before treating it
as a detection problem.

**Why does the same request work in curl but not in Playwright?** Because
curl is not a browser and is not subject to CORS at all - it is a browser-only
enforcement mechanism. That is also why `page.request` in Playwright, which
does not run in page context, does not hit it either.

**Will a proxy fix a CORS error?** No. CORS is about the origin your script
runs in, not the network path the request takes.

**See also:**
[Why does my Playwright script get blocked?](why-does-my-playwright-script-get-blocked.md),
[Content-Security-Policy blocks Playwright scripts](content-security-policy-blocks-playwright-scripts.md),
and [how to capture XHR/API responses with Playwright](how-to-capture-xhr-api-responses-playwright.md)
for the `page.request` approach applied to real scraping.

## Sources

- [MDN, Cross-Origin Resource Sharing (CORS)](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS), retrieved 2026-09-10, for the mechanism described above.
- [Playwright's documentation on API testing and request contexts](https://playwright.dev/python/docs/api-testing), retrieved 2026-09-10, for the `page.request` path that is not subject to page-context CORS.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
This page exists to say plainly that a CORS error is not what this project
addresses, and to point at the actual fix rather than let it be mistaken for a
fingerprint problem.*
