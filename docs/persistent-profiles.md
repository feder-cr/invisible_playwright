---
title: "Playwright persistent profile: what it fixes and breaks"
description: "A Playwright persistent profile keeps logins but carries three traps: a stale seed, a camera permission disabling WebRTC protection, and an inconsistent age."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 7
---


# Playwright persistent profile: what it fixes and breaks

A persistent profile solves a real problem: logging in once instead of every run. It
also carries three traps, one of which silently switches off protection you believe you
have, and none of them appear in the guides about keeping sessions alive.

This page covers what a profile actually stores, how it interacts with a seeded
fingerprint, the permission that undoes your proxy work, why the age of a profile has to
hold together, the upstream bugs that are not your fault, and what to do about all of
it.

## storageState or a persistent profile: what each one carries

Two mechanisms get conflated constantly and they are not interchangeable.

**`storageState`** captures cookies and local storage and replays them into a fresh
context. It is portable, it is a file you can open and read, and it carries nothing
else: no cache, no permission decisions, no extensions, no profile-level settings.

**A persistent profile** is the browser's own user data directory. It carries all of the
above and keeps accumulating: the cache, the permissions, the extensions, the
site-specific settings, everything a real browser writes down about itself over time.

For "log in once and stay logged in", `storageState` is usually enough and much easier
to reason about. For "this identity should look like it has been used", the profile is
the one that does it, because it accumulates the things you would not think to fake.

The choice breaks down to a few common cases:

- A test suite that needs an authenticated session: `storageState`.
- One long-lived identity you return to over weeks: a profile.
- Many parallel workers: `storageState` per worker, or one profile per worker and never
  a shared directory.

## The pairing rule: the profile stores history, the seed stores the machine

This is the trap specific to any tool that derives a fingerprint from a seed, and it is
easy to hit because the two settings live in different places.

The profile says: this browser has been here before, it has cookies from weeks ago, it
has a cache.

The seed says: this browser runs on a particular machine, with a particular GPU, screen,
font set and audio device.

Pair a stable profile with a **changing** seed and you have described a session whose
history spans weeks and whose hardware changed overnight. Nobody replaces a graphics
card between two visits to the same site while keeping their cookies.

```python
with InvisiblePlaywright(seed=42, profile_dir="/profiles/identity-42") as browser:
    ...
```

One directory, one seed, permanently. Write the pairing down, because the directory
outlives your memory of which number produced it.

The inverse mistake is quieter: a stable seed with a fresh profile every run produces a
machine that has been on the internet many times and never remembers anything, which is
the same thing that makes
[a fresh browser score badly on reCAPTCHA v3](recaptcha-v3-score.md).

## The stored permission that disables WebRTC address protection

A stored camera or microphone permission switches off Firefox's WebRTC address
protections for that origin. It gets its own section because it is invisible, permanent,
and it undoes work you did somewhere else entirely.

Firefox conditions two separate WebRTC privacy behaviours on whether the page has camera
or microphone access:

- restricting ICE gathering to the default route rather than enumerating every local
  interface, the "default route only" mode described in
  [RFC 8828](https://datatracker.ietf.org/doc/html/rfc8828), and
- masking the host candidate behind an mDNS `.local` name instead of the real LAN
  address, the mechanism specified in
  [draft-ietf-mmusic-mdns-ice-candidates](https://datatracker.ietf.org/doc/draft-ietf-mmusic-mdns-ice-candidates/).

Both are switched off together when a permission is present.

The critical detail is that the check counts a **persisted** permission, not only an
active capture. Granting camera access once on an origin, in a directory you then reuse,
turns those protections off for that origin in every future session on that profile,
until the permission is removed.

So a liveness or verification flow that asks for a camera, or a `grant_permissions` call
you made months ago while debugging, quietly undoes
[the WebRTC work described here](webrtc-leak-proxy.md). Nothing warns you; the session
simply starts reporting more than it used to.

Audit the stored permissions on any profile used with a proxy. It takes a minute, and
the failure mode is exactly the one the proxy was there to prevent.

## Profile age has to be internally consistent

A directory created this morning and filled with cookies dated last year is telling two
stories.

Things that carry an age and have to agree:

- Cookie expiry dates, and the issue dates they imply.
- Analytics identifiers that encode a first-seen timestamp.
- The cache, which is either present and plausible or absent.
- Local storage entries recording a first-visit date.
- Whether consent decisions exist for sites the profile claims to have visited.

This matters most if you **seed** a profile rather than grow one. A grown profile is
consistent for free. A written one is consistent only if you were careful, and
[a stale artefact is worse than a missing one](recaptcha-v3-score.md), because absence
has innocent explanations and an anachronism does not.

## Upstream issues that are not your fault

Two open Playwright reports affect anyone doing this:

- Session cookies not persisting through `launch_persistent_context` despite the
  documentation:
  [microsoft/playwright#36139](https://github.com/microsoft/playwright/issues/36139).
- Persistent context failing to read cookies written by a previous session on the same
  directory, plus profile corruption:
  [microsoft/playwright#35466](https://github.com/microsoft/playwright/issues/35466).

If cookies vanish between runs, read those before rewriting your own code.

Add one more that is not a bug report: **never run two browsers against the same profile
directory at once**. A user data directory has no concurrency story, and the failure is
corruption rather than an error.

## Conclusion

A persistent profile fixes history and nothing else. The machine underneath still
answers for itself, so the GPU, the fonts, the screen and the audio device are unchanged
by anything in that directory.

Used well it is one directory paired with one seed, with its permissions audited, never
shared between concurrent runs, and grown rather than written. Used carelessly it is a
way to accumulate contradictions that a fresh profile would not have had.

## Short answers to the questions that lead here

**Does a persistent profile stop me being detected?** It fixes having no history. It
does nothing about the machine: the GPU, fonts, screen and audio device come from where
you run, not from the directory.

**Should I use `storageState` or a persistent profile?** `storageState` for logins, a
profile when the identity should accumulate everything else.

**Why do my cookies disappear between runs?** Possibly one of the upstream issues above,
possibly two processes sharing a directory, possibly a session cookie behaving as one.

**Can I reuse one profile for many identities?** No. The profile is the identity, and
shared cookies link the identities that share them.

**Do I need a different seed per profile?** Yes, and the same seed every time for the
same profile.

**Why would a granted camera permission matter?** Because Firefox conditions its WebRTC
address protections on the absence of one, and a persisted grant counts.

**See also:** [WebRTC leak with a proxy](webrtc-leak-proxy.md), which is what the
permission trap undoes, [reCAPTCHA v3 score](recaptcha-v3-score.md), for why a profile
with a past is worth keeping,
[browser extensions are a fingerprint surface](browser-extension-fingerprint.md), since a
profile is how you install one, and
[why automating the login form is riskier than reusing a session](automating-login-vs-session-reuse.md),
for the `storageState` half of this same identity question, and
[whether you can point Playwright at your real browser profile](can-i-use-my-real-browser-profile-playwright.md),
which is the one profile you should never reuse.

## Sources

- Playwright's own [`launch_persistent_context`
  documentation](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context),
  retrieved 2026-08-28, for the user data directory mechanism this page builds on.
- The two open Playwright issues on cookie persistence in a user data directory:
  [microsoft/playwright#36139](https://github.com/microsoft/playwright/issues/36139) and
  [microsoft/playwright#35466](https://github.com/microsoft/playwright/issues/35466),
  retrieved 2026-08-28.
- [RFC 8828, WebRTC IP Address Handling Requirements](https://datatracker.ietf.org/doc/html/rfc8828),
  retrieved 2026-08-28, and
  [draft-ietf-mmusic-mdns-ice-candidates](https://datatracker.ietf.org/doc/draft-ietf-mmusic-mdns-ice-candidates/),
  retrieved 2026-08-28, the two specs behind the permission-conditioned WebRTC behaviour
  described above.
- Firefox's own conditioning of `default_address_only` and `obfuscate_host_addresses` on
  active-or-permitted capture, which is the mechanism behind the permission trap:
  [Mozilla Bugzilla 1570669](https://bugzilla.mozilla.org/show_bug.cgi?id=1570669),
  retrieved 2026-08-28, the report that added the permission check.
- This project's proxy and profile handling, described in the pages linked above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The permission trap is recorded in our own notes as a
gap with no patch, which is why this page tells you to audit for it rather than promising
it is handled.*
