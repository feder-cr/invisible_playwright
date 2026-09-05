---
title: "Queue-it virtual waiting rooms, explained for automation"
description: "Queue-it virtual waiting rooms explained: how the redirect, the return token and the JS-vs-server split work, and why automation has nothing to bypass."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 45
---


# Queue-it virtual waiting rooms, explained for automation

Queue-it is a virtual waiting room: a SaaS admission-control layer a site turns on when
demand exceeds what its backend can serve. It redirects overflow visitors to a separate
queue domain, shows each one a position and an estimated wait, then sends them back with a
signed token once their turn arrives. It is not a bot detector.

## What problem Queue-it actually solves

Queue-it does not decide whether you are a bot. It decides whether your request arrives
before, during, or after a demand spike the backend cannot handle at once: a ticket
release, a product drop, a public booking window. The queue admits visitors in order
instead of letting all of them hit the backend at the same moment, an availability
decision, not a security one. Confusing the two wastes most of the debugging time this
vendor produces.

## How the redirect and the return token actually work

When the queue is open, a request for the protected page never reaches the origin. It gets
redirected to a separate queue-hosting domain, typically a subdomain under `queue-it.net`
, where a page shows a position and an
estimated wait, updated by the page's own script polling a status endpoint. The position
comes from a server-side counter tied to your session, not from how you rendered the page.

When your turn comes, the queue page redirects back to the original URL with a signed
token attached to the query string, proving you went through the queue in order and within
a valid window. Whatever receives the request next, the origin or an edge rule, is meant to
check that token before granting access, using a server-side library Queue-it generally
calls KnownUser, published as connector SDKs in several languages. Skip that check and the queue
becomes advisory: a guessed or replayed token reaches the page directly, a site integration
mistake this page is not describing how to reproduce.

## The JavaScript connector and the server check do different jobs

The JS snippet a site embeds decides, client-side, whether a visitor should be sent to the
queue, and renders the waiting-room page once they are there: a user-experience layer,
often the first piece a site adds since it needs no server changes. What actually enforces
anything is the server-side or edge validation of the returned token. A page carrying only
the JS connector, with nothing checking the token on the way back, is locking a gate that
was never shut. Assume enforcement lives on the receiving end, never in the page source.

## This is not bot detection, and there is nothing here to defeat

A waiting room runs nothing like [the obfuscated bytecode VM Kasada uses to interrogate
your JS engine](kasada-explained.md). It is a position counter and a clock, not a check on
whether `navigator.webdriver` lies. Some deployments layer bot mitigation on top, aimed at
scripts opening many parallel sessions to grab more than one spot in line
, worth knowing so you do
not mistake it for a fingerprint problem, but a separate concern this project does not try
to get around.

## What a script should actually do at a queue

Recognize the redirect by checking the page's hostname, then wait for the one signal that
matters: the URL changing back with the token attached, the same principle as [choosing a
wait that matches the real signal instead of a fixed sleep or a `networkidle`
guess](how-to-wait-for-page-load-playwright.md), applied to a wait that can run for
minutes.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/high-demand-page")

    if "queue-it.net" in page.url:
        # In line: wait for the site's own redirect back, not a fixed sleep.
        # This can legitimately take minutes. Do not race the poll interval.
        page.wait_for_url("https://example.com/**", timeout=0)

    print(page.title())
```

That shape was exercised on 5 September 2026 against a locally served page that behaves
like a waiting room: it holds the visitor, then redirects to the destination with a
`queueittoken` in the query string. `wait_for_url` woke on the redirect, and the token
was present on arrival, which is the signal that the connector on the other side will
validate and then strip.

Do not poll on your own timer faster than the waiting-room page already does. That looks
like the abuse pattern above, and buys nothing.

## What breaks a script mid-queue

The queue position is tied to a specific session, not to a URL. Restart the browser
context, open a second tab for "the same" visit, or relaunch Playwright mid-wait, and the
new context reads as a brand-new visitor: back of the line at best, a second attempt
flagged at worst. Hold the same context open for the whole wait, and never treat a long
wait as a hung script.

## Where this project draws the line

invisible_playwright does not attempt to skip a Queue-it wait. Getting through faster than
a site's own admission control allows does not remove the capacity problem, it moves it
onto whoever's request lands after yours, and it is likely a terms violation on top of
accomplishing nothing. If you suspect a queue is actually a block in disguise, [the
four-layer model for a block that survives a clean
fingerprint](why-blocked-with-a-clean-fingerprint.md) does not include one at all: it is a
fifth thing, and the fix for a real queue is to wait, not to debug.

## Short answers to the questions that lead here

**Is Queue-it a bot detector?** No. It is admission control for demand that exceeds
capacity, a position and a clock, not a fingerprint check. Some deployments add bot
mitigation against queue abuse specifically, separate from the waiting room mechanism.

**Can automation skip a Queue-it wait?** No, not without defeating the point of a capacity
queue, and this project does not attempt it. The correct behavior for a script is the same
as for a person: wait, holding the same session, until the redirect back arrives.

**Why did my script lose its place in the queue?** Almost always because the browser
context was restarted, or a second context was opened for the same visit, while a position
was still pending. The queue reads that as a new visitor, not a continuation.

**See also:** [how Kasada's actual bot-detection VM works, for contrast](kasada-explained.md),
[choosing the wait that matches the real signal instead of a fixed
sleep](how-to-wait-for-page-load-playwright.md), and [the four-layer model for a block that
is not the fingerprint](why-blocked-with-a-clean-fingerprint.md).

## Sources

- Queue-it developer pages, https://www.queue-it.com/developers/ - Queue-it's own
  description of its "suite of Connector SDKs and REST-based APIs that gives you control
  over the visitor flow and behavior", and the three integration routes: connectors, the
  REST API, and themed waiting-room pages. Read 5 September 2026.
- Queue-it KnownUser connector, https://github.com/queueit/KnownUser.V3.JavaScript - the
  request-by-request validation, the redirect to the queue when no valid token is
  present, and the removal of the token from the query string on return, which the
  README explains is "to avoid sharing of user specific token". The same README states
  the check must run on all requests except those for static and cached pages. Read
  5 September 2026.
- This wiki's own [kasada-explained.md](kasada-explained.md), for what a client-side VM
  checks, used only as a contrast.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
A capacity queue is not a challenge worth solving faster than anyone else: wait.*
