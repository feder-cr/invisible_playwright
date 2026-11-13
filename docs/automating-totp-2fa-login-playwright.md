---
title: "Automating TOTP-Based 2FA Login with Playwright"
description: "Generate a time-based one-time code with pyotp from the same shared secret an authenticator app would scan as a QR code, and feed it straight into the login form. No inbox, no polling, no IMAP - for testing your own application's 2FA flow."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 147
---


# Automating TOTP-Based 2FA Login with Playwright

This page is about a specific, ordinary testing need: your own application, or an
account you control, has authenticator-app two-factor authentication turned on,
and a test or a script needs to complete the login end to end without a human
opening Google Authenticator or Authy on a phone. The mechanism is cryptographic,
not network-based: a time-based one-time password (TOTP) is computed on the spot
from a shared secret you already hold, the same secret an authenticator app would
have scanned as a QR code the day 2FA was set up. Nothing here assumes or requires
access to an account that is not yours, and the whole point of writing this down
is to keep the automation scoped to accounts you actually control.

## This is not the email-OTP page, on purpose

If you came here from [automating an email OTP or magic-link
login](automating-email-otp-verification-login-playwright.md), the two pages solve
the same category of problem, "get past a second login step," with genuinely
different mechanics, and it is worth being explicit about which one your situation
actually needs.

| | Email OTP / magic link | TOTP authenticator |
|---|---|---|
| Where the code comes from | The site generates and emails it *after* you trigger the send | You generate it yourself, locally, from a secret you already hold |
| What you need on hand | IMAP credentials, or a test-inbox API | Nothing but the shared secret and a library |
| Timing model | Poll a real mailbox against a deadline; codes can take seconds to arrive and can expire in minutes | Instant; no network call, no waiting, no inbox at all |
| Failure mode | Mailbox rate limits, expired links, stale messages from old runs | Clock drift between your machine and the server, a mismatched digit/period setting |

The email-OTP page spends most of its length on `imaplib`, timestamps, and polling
loops, because the hard part there is coordinating with a mailbox on the network.
None of that exists here. If your two-factor step involves opening a mail client,
you want that page. If it involves an authenticator app and a 6-digit code that
refreshes every 30 seconds, you want this one.

## The shape of the flow

```python
import pyotp
from invisible_playwright import InvisiblePlaywright

SEED = 42
TOTP_SECRET = "JBSWY3DPEHPK3PXP"   # the same secret your authenticator app holds

with InvisiblePlaywright(seed=SEED) as browser:
    page = browser.new_page()
    page.goto("https://example.com/login")
    page.fill("#email", "you@example.com")
    page.fill("#password", "your-password")
    page.click("#sign-in")

    page.wait_for_selector("#totp-code")          # the 2FA step actually appeared
    code = pyotp.TOTP(TOTP_SECRET).now()
    page.fill("#totp-code", code)
    page.click("#verify")
    page.wait_for_url("https://example.com/account")
```

There is no step 2 that waits on anything external. `pyotp.TOTP(TOTP_SECRET).now()`
returns a code synchronously, in microseconds, because it is a computation, not a
request.

## Where the secret comes from

The shared secret is not something you invent; it is the same value your own
application generated when you (or a test account you control) turned on
authenticator-app 2FA. During that setup flow, most applications show a QR code
and, next to it, a plain-text fallback string for anyone whose phone camera does
not work. That fallback string is a base32-encoded secret, and it is exactly what
an authenticator app decodes out of the QR code before storing it. Google's own
specification for the format authenticator apps read, the `otpauth://` URI scheme,
states it directly: the QR code encodes a URI whose `secret` parameter is "an
arbitrary key value encoded in Base32," and that URI is "what get[s] converted
into QR codes during the setup process for authenticator applications." Capture
that string once, during your own account's enrollment, store it the way you would
store any other test credential, and `pyotp.TOTP(secret)` consumes it identically
to how a phone's authenticator app would.

```python
import pyotp

# provisioning_uri() is what you would render as a QR code for a human;
# reading the same secret directly is what a script needs instead.
secret = pyotp.random_base32()
uri = pyotp.totp.TOTP(secret).provisioning_uri(name="you@example.com", issuer_name="Example App")
print(uri)     # otpauth://totp/Example%20App:you%40example.com?secret=...&issuer=Example%20App
```

Do not try to re-derive the secret from a screenshot of the QR code on every test
run. Capture it once, during setup, the same way you would capture a password.

## Generating the code

[PyOTP's own documentation](https://pyotp.readthedocs.io/en/stable/) describes the
library plainly: it generates and verifies one-time passwords for two-factor and
multi-factor authentication. The call that matters is one line:

```python
import pyotp

totp = pyotp.TOTP(secret)
code = totp.now()      # e.g. "492039"
```

`.now()` computes the code for the current 30-second window and returns it as a
string. There is no client-server round trip anywhere in this call; the entire
value is derived from the secret and the current time.

### The algorithm, briefly

TOTP is defined in [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238) as a
time-based variant of HOTP (RFC 4226), the counter-based one-time-password
algorithm. The RFC states the relationship directly: `TOTP = HOTP(K, T)`, where
`T` is a moving factor computed as `(Current Unix time - T0) / X`, with `X` the
time step, 30 seconds by default, and `T0` the Unix epoch. In plain terms: instead
of HOTP's incrementing counter, TOTP uses "which 30-second slice of time is it
right now" as the counter, and HMACs that against the shared secret the same way
HOTP always has. Both your script and the server computing the same thing from the
same secret and the same clock arrive at the same 6-digit code, which is the whole
mechanism 2FA setup relies on, and the whole reason no network call is involved on
either generation or verification.

### Matching digits, algorithm and period to what the site actually issued

`pyotp.TOTP()` defaults to 6 digits, SHA1, and a 30-second period, which matches
the overwhelming majority of sites' authenticator-app implementations. If your
target application's enrollment page shows different values, in an advanced setup
screen or in the raw `otpauth://` URI, pass them explicitly:

```python
import hashlib

totp = pyotp.TOTP(secret, digits=8, digest=hashlib.sha256, interval=60)
```

A code generated with the wrong digit count, algorithm or interval will not match
what the server expects, and this looks identical to "the automation is broken"
from the outside. Read the parameters out of the actual `otpauth://` URI your
application issued rather than assuming the defaults.

## Clock drift and reuse, the two real failure modes

Two practical issues account for nearly every TOTP automation failure that is not
a plain wrong secret.

**Clock drift.** TOTP's whole security model rests on both sides agreeing what
time it is. If the machine running your script and the server verifying the code
disagree by more than roughly one time step, the code you generate is for the
wrong window and gets rejected as invalid even though the secret is correct.
`pyotp.TOTP.verify()` accepts a `valid_window` argument specifically for this: it
checks the current window plus a configurable number of windows on either side,
which is the standard tolerance most real verifiers apply server-side too. If
you are generating and submitting the code yourself rather than verifying it, the
practical fix is keeping the machine running the automation on synced NTP time,
not widening a window you do not control on the server end.

**Code reuse inside the same window.** Some applications reject submitting the
same TOTP code twice, even if it is still numerically valid, as a replay defense.
If a test suite runs the login flow twice in quick succession and happens to land
in the same 30-second window both times, the second attempt can fail for a reason
that has nothing to do with the code being wrong. This is intermittent by nature,
tied to wall-clock timing rather than your code, and the fix is either spacing
runs apart or, if the site's session allows it, not re-authenticating a fresh TOTP
challenge more than once per window in automated test loops.

## Storing the secret safely

The shared secret is a long-lived credential, not a one-time value; anyone who has
it can generate valid codes for as long as 2FA stays configured with it. Treat it
the way you would treat a password: an environment variable or a secrets manager
in CI, never a literal string committed to a repository. This is the same
housekeeping any other test credential needs, and it is worth stating because a
TOTP secret is easy to underestimate: unlike a password, it never gets rotated by
a failed-login lockout, so a leaked one stays valid indefinitely.

## Conclusion

TOTP-based 2FA automates in two lines once you have the secret: `pyotp.TOTP(secret).now()`
to compute the current code, and a `page.fill()` into whatever field the login
form shows for it. The mechanism is entirely local and cryptographic, RFC
6238's HMAC-over-time-step algorithm, with no inbox, no IMAP connection, and no
polling loop anywhere in it, which is the whole difference from [the email-OTP
flow](automating-email-otp-verification-login-playwright.md) this page is most
often confused with. The parts worth being careful about are capturing the secret
once during your own account's real enrollment rather than trying to re-scan a QR
code every run, matching the digit count, algorithm and period the site actually
issued, and keeping the automation's clock in sync so the code you compute lands
in the same window the server checks against.

## Short answers to the questions that lead here

**How do I automate a login protected by an authenticator app?** Get the shared
secret from your own account's 2FA enrollment (the base32 string next to the QR
code), then call `pyotp.TOTP(secret).now()` to generate the current code and fill
it into the login form, in the same script, right after triggering the login.

**Is this the same as automating an email OTP login?** No. [Email OTP](automating-email-otp-verification-login-playwright.md)
requires triggering a send and polling a real mailbox over IMAP until the code
arrives. TOTP requires no network call at all: you already hold the secret, and
the code is computed instantly and locally from it and the current time.

**Where do I get the TOTP secret in the first place?** From your own account's 2FA
setup screen, the same base32 string an authenticator app decodes out of the QR
code. Capture it once during enrollment and store it like any other credential;
do not try to re-derive it from a screenshot on every run.

**Why does the generated code get rejected even though the secret is right?**
Almost always clock drift between the machine generating the code and the server
verifying it, or a mismatch in digits, hash algorithm or time-step length against
what the site actually issued. Check both before assuming the secret itself is
wrong.

**Can I run the login twice in a row with the same code?** Some applications
reject reusing a TOTP code inside the same 30-second window as a replay defense,
independent of whether the code is still numerically valid. Space repeated
automated logins apart if you see intermittent rejections tied to timing.

**Is this a way to get into an account that is not mine?** No. It only works
against an account whose 2FA you set up yourself and whose secret you hold. The
technique automates a flow you already have legitimate access to, the same
category as reusing a saved session or automating an email OTP for your own
inbox; it does not solve, guess or bypass anything, and it is worthless against a
secret you were never given.

## Sources

- [PyOTP's own documentation](https://pyotp.readthedocs.io/en/stable/) and its
  [GitHub repository](https://github.com/pyauth/pyotp), for the `TOTP` class,
  `.now()`, `.verify()` and `valid_window`, and `provisioning_uri()`, retrieved
  2026-08-30.
- [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238), "TOTP: Time-Based
  One-Time Password Algorithm," for the `TOTP = HOTP(K, T)` definition and the
  `T = (Current Unix time - T0) / X` moving-factor formula, retrieved 2026-08-30.
- [RFC 4226](https://datatracker.ietf.org/doc/html/rfc4226), the HOTP algorithm
  TOTP extends with a time-derived counter in place of an incrementing one.
- Google's [Key Uri Format](https://github.com/google/google-authenticator/wiki/Key-Uri-Format)
  specification, for the `otpauth://` URI scheme, the base32 `secret` parameter,
  and its role as what actually gets encoded into a 2FA setup QR code, retrieved
  2026-08-30.
- Checkly's own [How to Bypass Time-Based 2FA Login Flows With
  Playwright](https://www.checklyhq.com/docs/learn/playwright/bypass-totp/),
  verified live this session, for a real, independent walkthrough of the same
  generate-then-fill pattern (using the JavaScript `otpauth` library against a
  personal GitHub account's own TOTP secret, explicitly scoped to an account the
  reader already controls).
- Playwrightsolutions.com's [Playwright Login Test With Two Factor Authentication
  (2FA) Enabled (TOTP)](https://playwrightsolutions.com/playwright-login-test-with-2-factor-authentication-2fa-enabled/),
  verified live this session, for another independent practitioner writeup of the
  same mechanism.

**See also:** [Automating an Email OTP / Verification-Link Login with
Playwright](automating-email-otp-verification-login-playwright.md) for the
network-polling variant of "get past a second login step" this page deliberately
does not overlap with, [why automating login is riskier than reusing a
session](automating-login-vs-session-reuse.md) for the fingerprint-consistency
argument that applies to any login automation, and [how to scrape data behind a
login with Playwright](how-to-scrape-behind-login-playwright.md) for the
session-reuse pattern this flow feeds into once the 2FA step is past.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. Generating a code from a secret you already
hold is arithmetic, not automation trickery; the browser's realness has nothing to
do with whether the math is correct, and the whole flow is worthless without a
secret that was legitimately yours to begin with.*
