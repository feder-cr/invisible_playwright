---
title: "Automating an Email OTP / Verification-Link Login with Playwright"
description: "Poll your own inbox over IMAP mid-login, parse the one-time code or magic link, and feed it back into the same browser session that requested it - for testing your own application's login flow."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 146
---


# Automating an Email OTP / Verification-Link Login with Playwright

This page is about a specific, ordinary automation need: your own application, or an
account you control, sends a one-time code or a magic link by email as part of login,
and a test or a script needs to complete that flow end to end without a human
watching an inbox. That is a normal thing to automate, the same category of work as
[reusing a saved session](automating-login-vs-session-reuse.md) or
[scraping behind a login](how-to-scrape-behind-login-playwright.md) - it is not about
getting into an account that is not yours, and nothing here assumes or requires that.

The pattern is three steps done in order, inside one browser session: trigger the send,
poll the mailbox until the message arrives, extract the code or link and hand it back
to the same page that asked for it. The part people get wrong is usually not the code,
it is the timing and the identity: polling too aggressively, or redeeming the code from
a different browser session than the one that requested it.

## The shape of the flow

```python
import time
from invisible_playwright import InvisiblePlaywright

SEED = 42

with InvisiblePlaywright(seed=SEED) as browser:
    page = browser.new_page()
    page.goto("https://example.com/login")
    page.fill("#email", "you@example.com")

    sent_at = time.time()               # mark the moment before triggering the send
    page.click("#send-code")

    code = fetch_code_from_inbox(sent_at)   # poll the mailbox; see below

    page.fill("#otp", code)
    page.click("#verify")
    page.wait_for_url("https://example.com/account")
```

Everything downstream depends on `sent_at`: without a timestamp marking "before the
request," you have no way to tell a fresh code from a stale one already sitting in the
inbox from a previous run.

## Step 1: trigger the send, then start the clock

Nothing unusual here - it is an ordinary form fill and click, the same as any other
login automation. The only detail worth keeping is recording the time immediately
before the click, not after, since network latency on the click itself can matter
when you are about to poll a mailbox on a short timeout.

## Step 2: poll the inbox with imaplib

Python's standard library `imaplib` module is enough for this; nothing extra to
install. Its own documentation describes it plainly:

> "This module defines three classes, `IMAP4`, `IMAP4_SSL` and `IMAP4_stream`, which
> encapsulate a connection to an IMAP4 server and implement a large subset of the
> IMAP4rev1 client protocol as defined in RFC 3501."

Use `IMAP4_SSL` for the connection, and pass an explicit SSL context instead of the
default one - the standard library is direct about why:

> "With the default ssl_context, the connection is encrypted but the server certificate
> and hostname are not verified. To verify them, pass a context created by
> ssl.create_default_context()."

```python
import imaplib
import email
import re
import ssl
import time

def extract_text(msg):
    # the plain-text part of a possibly multipart message
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="replace")
        return ""
    return msg.get_payload(decode=True).decode(errors="replace")

def fetch_code_from_inbox(sent_at, timeout_s=60, poll_every_s=2):
    ctx = ssl.create_default_context()
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        conn = imaplib.IMAP4_SSL("imap.example.com", ssl_context=ctx)
        conn.login("you@example.com", "app-specific-password")
        conn.select("INBOX")

        # UNSEEN keeps this from re-matching an old code from a previous run
        typ, data = conn.search(None, "UNSEEN", "SUBJECT", '"Your verification code"')
        ids = data[0].split()

        for msg_id in reversed(ids):   # newest first
            typ, msg_data = conn.fetch(msg_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])

            received = email.utils.parsedate_to_datetime(msg["Date"]).timestamp()
            if received < sent_at - 5:      # a few seconds of clock slack
                continue

            body = extract_text(msg)
            match = re.search(r"\b(\d{6})\b", body)
            if match:
                conn.logout()
                return match.group(1)

        conn.logout()
        time.sleep(poll_every_s)

    raise TimeoutError("no verification code arrived in time")
```

A few details that matter more than they look like they should:

- **Search `UNSEEN`, and filter by time, not just presence.** A test inbox
  accumulates old codes; matching "the newest message with the right subject" without
  a time check will occasionally hand you yesterday's code, which then fails
  verification for a confusing reason.
- **This is polling, not push.** IMAP has no notification mechanism in the basic flow
  above; you are asking the server "anything new?" on an interval.
  [`IMAP4.idle()`](https://datatracker.ietf.org/doc/html/rfc2177) exists for a
  push-like wait on servers that support the `IDLE` extension, but a short poll loop
  is simpler to reason about and is fine for this use case.
- **Most mail providers now require an app-specific password or an OAuth token for
  IMAP access, not the account's normal login password.** That is a one-time setup
  step outside Playwright entirely, and which one your provider needs is worth
  checking before wiring this up, since it is unrelated to anything in this page.
- **A test-email API is a reasonable alternative to a real mailbox**, particularly in
  CI where you do not want a persistent inbox at all. Several providers expose a
  disposable inbox over a plain REST API instead of IMAP - poll an HTTP endpoint for
  new messages the same way the loop above polls IMAP, then apply the same
  time-filtering and parsing logic to whatever it returns.

## Step 3: parse the code or the link

Extracting a numeric code is a regex against the plain-text body, as above. A magic
link needs one extra step: pull the href out of the HTML part rather than the
plain-text alternative, since some providers only put a clickable link in the HTML
version of the message.

```python
from bs4 import BeautifulSoup

def extract_magic_link(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            soup = BeautifulSoup(part.get_payload(decode=True), "html.parser")
            link = soup.find("a", string=re.compile("verify|confirm|sign in", re.I))
            if link:
                return link["href"]
    return None
```

One thing worth checking before trusting the extracted URL: some sending platforms
wrap the real link behind their own click-tracking redirect, so the `href` you pull
out of the HTML is not the destination URL itself, it is a tracking link that
redirects to it. That is fine to navigate directly - Playwright follows redirects the
same as any browser - but do not assume the string you extracted is the final URL if
you are trying to match it against something else, like an expected domain.

## Step 4: feed it back into the same session, not a new one

This is the part that actually matters for the automation to keep working reliably,
and it is easy to get wrong by treating steps 1 through 3 as a separate script from
step 4.

The browser that requested the code and the browser that redeems it have to be the
same session: same context, same seed-derived fingerprint, same proxy exit, ideally
the same page object still sitting on the login form. A login flow that requests a
code from one machine's fingerprint and then submits it from a different one is
exactly the cross-session mismatch a fingerprint-aware login already watches for, the
same argument made in full on
[why automating login is riskier than reusing a session](automating-login-vs-session-reuse.md):
replaying credentials from a different fingerprint asks the site to believe the same
identity's GPU, canvas hash and TLS handshake all changed mid-flow, independent of
whether the code itself is genuine.

In practice this means: do not send the code request, close the browser, run a
separate mail-fetching script, then open a *new* Playwright session to submit the
code. Keep the browser open across the whole thing, poll the inbox in the same
process, and submit the result into the same `page` object, as the first code block on
this page does. If a magic link is what arrived instead of a code, navigate the same
page to it rather than opening it in a fresh context:

```python
page.goto(magic_link)   # same page, same identity that requested it
page.wait_for_url("https://example.com/account")
```

## Timing: the budget you are actually working against

Two failure directions, and you need a plan for both. Poll too fast and you risk
hitting your mail provider's own rate limit on IMAP logins, which is a real and
separate throttling layer from anything Playwright touches. Poll too slow, or set too
generous a total timeout, and you risk the code or link expiring before you use it -
some verification links expire in under two minutes, tighter than the 5 to 15 minutes
common for numeric codes. Set an explicit total timeout, back off between polls rather
than hammering the server every few hundred milliseconds, and treat a timeout as an
expected outcome your code handles, not an exception you were not expecting - the same
budget-first thinking as
[retrying failed requests with a total time-and-attempt cap](how-to-retry-failed-requests-playwright.md).

## Conclusion

An email OTP or magic-link login automates in three ordinary steps: trigger the send
with a timestamp, poll the inbox with `imaplib` (or a disposable-inbox API) filtered
by that timestamp, and feed the result back into the same browser session that
requested it. The mechanics are standard library code; the part worth being careful
about is keeping the request and the redemption in one identity, and budgeting the
poll loop against both a rate limit on one side and an expiring code on the other.
None of this reads or touches an inbox you do not have legitimate access to - it
automates a flow you are testing, the same way a saved session automates skipping a
login form you already have credentials for.

## Short answers to the questions that lead here

**How do I automate a login that emails a one-time code?** Trigger the send, poll your
inbox with `imaplib` filtered to messages received after the request, extract the code
with a regex, and fill it into the same page that requested it.

**Do I need a real mailbox, or can I use a test-email service?** Either works. A real
IMAP mailbox with an app-specific password is the direct route; many providers also
offer a disposable inbox reachable over a plain REST API, useful in CI where you do
not want a persistent account.

**Why did the login get flagged even though the code was correct?** Most likely the
code was requested and redeemed from two different browser sessions with two
different fingerprints. Keep the whole flow, request through redemption, inside one
Playwright session with one seed.

**How long should I poll before giving up?** Set an explicit total timeout rather than
polling forever, and back off between attempts. Codes and links both expire, some
links in under two minutes, so budget for that rather than assuming you have plenty of
time.

**My IMAP login fails even with the right password. Why?** Most mail providers now
require an app-specific password or an OAuth token for IMAP access rather than the
normal account password. That is a setup step outside Playwright.

**How do I handle a magic link instead of a numeric code?** Extract the href from the
HTML part of the message (plain text often omits it), then `page.goto()` that URL in
the same session that requested it.

**Is this the same thing as bypassing a site's verification?** No. This automates a
flow you have legitimate access to, the same category as reusing a saved login session
- it does not solve a challenge meant to keep automation out, and it requires an
inbox and credentials that are actually yours.

## Sources

- The Python standard library's [`imaplib` documentation](https://docs.python.org/3/library/imaplib.html),
  retrieved 2026-08-30, for the module description and the `IMAP4_SSL`,
  `search()` and `fetch()` signatures used above, and for the explicit note that the
  default SSL context does not verify the server certificate or hostname.
- [RFC 3501](https://datatracker.ietf.org/doc/html/rfc3501.html), the IMAP4rev1
  protocol `imaplib` implements, for `UNSEEN` as a defined search key.
- This project's own notes on session and fingerprint consistency, linked throughout,
  for why the request and the redemption belong in one identity.

**See also:** [Automating TOTP-Based 2FA Login with Playwright](automating-totp-2fa-login-playwright.md)
for the other common second-login-step mechanism, a locally computed authenticator
code with no inbox and no polling involved at all, [Why automating login is riskier
than reusing a session](automating-login-vs-session-reuse.md)
for the fingerprint-consistency argument this page depends on,
[how to scrape data behind a login with Playwright](how-to-scrape-behind-login-playwright.md)
for the end-to-end session-reuse pattern this flow feeds into, and
[how to retry failed requests when scraping with Playwright](how-to-retry-failed-requests-playwright.md)
for the same total-budget thinking applied to a poll loop instead of a request retry.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The code arriving is the easy part; keeping the
browser that asked for it and the one that spends it as the same machine is the part
worth writing down.*
