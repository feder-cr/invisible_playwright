---
title: "Can you be fingerprinted in incognito mode?"
description: "Yes: incognito mode clears cookies and site storage but leaves canvas, WebGL, fonts, user agent, timezone and the TLS handshake fully readable and re-linkable."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 23
---


# Can you be fingerprinted in incognito mode?

Yes. This is the single most common misunderstanding about private browsing, and it is
worth stating plainly before the details: incognito mode clears **state**, not
**identity**. It empties cookies and site storage at the end of a session. It does not
change what your browser looks like while the session is running, and that is exactly
the part fingerprinting reads.

This page is what private mode actually does, why stateless fingerprinting is untouched
by it, how a service re-links you across incognito windows anyway, and what changing the
fingerprint values themselves looks like in practice.

## What private mode actually clears

Open a private window and the browser makes one promise: when you close it, the cookies,
`localStorage`, `sessionStorage`, IndexedDB and cache from that session go away. History
is not written to disk. That is the whole feature, and for its intended purpose (a shared
family computer, not leaving a booking half-finished in your history) it does that job.

Notice what is on that list: all of it is **state the site wrote to your machine**. A
cookie is a value the server set and asked you to hand back. Private mode's guarantee is
that you stop handing those back once the window closes.

Nothing on that list is a property of the browser itself. And identification does not
need you to hand anything back.

## Why stateless fingerprinting does not care

A cookie is something you were **given**. A fingerprint is something you **are**. Private
mode only deletes the first kind.

When a page measures your browser, it reads values the browser exposes on request, no
storage involved:

- **Canvas.** The page draws text and shapes to an offscreen canvas and
  [reads the pixels back](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
  as an encoded image. The exact bytes depend on your GPU, drivers and font rasterizer, and
  they are identical in a private window because it is the same GPU. See
  [why canvas output can change every run](canvas-fingerprint-changes-every-run.md) for
  what a real defense against this looks like.
- **WebGL.** The
  [renderer and vendor string](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  and the numeric limits of your graphics stack. Same machine, same values, private or not.
- **Fonts.** The list of installed fonts, measured by rendering and comparing widths. A
  private window renders with the same fonts.
- **User agent, platform, language, timezone.** All reported the same way in incognito.
- **The [TLS handshake](https://datatracker.ietf.org/doc/html/rfc8446).** Decided by the
  network stack before a single line of page JavaScript runs, so no browsing mode touches
  it. [No in-page test can even see it](ja3-ja4-tls-fingerprint.md), and neither can
  incognito.

Read that list again against the previous section. There is no overlap. Private mode
clears cookies and storage; fingerprinting reads GPU, fonts, TLS and the rest. The two
sets do not intersect, which is why a fingerprint survives the mode that was supposed to
protect it.

## How you get re-linked across incognito windows

Here is the part that surprises people. Because the fingerprint is stable and does not
depend on stored state, a service can compute the same identifier in a normal window, in
a private window, and in a second private window you opened an hour later to "start
fresh". [FingerprintJS builds exactly this kind of visitor ID](fingerprintjs-visitor-id.md)
by hashing many of those stateless components together.

So the common workflow of "open an incognito window to look like a new visitor" does not
produce a new visitor. It produces the same visitor with an empty cookie jar. The site
sets a fresh cookie, ties it to the fingerprint it just computed, and the link is
restored on the first request. Clearing the cookie deleted the label on the folder, not
the folder.

This is not a bug in private mode. Private mode never promised a new identity. It
promised no leftover state, and it delivers that.

## Changing the values themselves, not clearing storage

If the problem is that the fingerprint values are stable and readable, the fix is to
change the values, which is the specific thing private mode does not do. That is what
invisible_playwright does: a Firefox patched at the C++ level so that canvas, WebGL,
fonts, screen and the rest report seed-derived values, driven by stock Playwright.

Two lines to launch, and the browser you get back is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser) with every standard
method:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # canvas, WebGL, fonts, screen all report seed-42 values,
    # not this machine's values
```

The seed is the whole point. The same seed produces the same coherent identity every
run, and a different seed produces a different one. So where private mode gives you the
same machine with its storage wiped, a new seed gives you a genuinely different machine
that happens to be internally consistent:

```python
# a different, self-consistent identity - a different GPU, fonts, canvas hash
with InvisiblePlaywright(seed=7) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

You can prove the difference to yourself. Open a public fingerprinting suite with
`seed=42`, note the visitor ID or the canvas hash, close it, reopen with `seed=42`, and
it matches. Change to `seed=7` and it moves. That is the value changing, which is the
axis incognito leaves fixed. For the Firefox `about:config` route to the neighbouring
problem, [`privacy.resistFingerprinting` and what it trades away](resist-fingerprinting.md)
is worth reading before you reach for it.

## The honest caveat: it does not change your IP

There is one thing this shares with private mode: neither touches your IP address.
Incognito does nothing for your address, and neither does changing the fingerprint. Your
exit IP, its reputation, and the ASN it sits in are all still exactly what they were.

This matters because a perfect, distinct, self-consistent browser on a datacenter IP that
a thousand other automated sessions are using this minute is still on that IP. The
fingerprint and the driver layer reading as a genuine Firefox is why most detection
checks pass; it is not why a session passes. The reader supplies the rest:

- **A clean exit.** The fingerprint work is wasted behind an address already on a
  blocklist. See [Configuration](configuration.md) for proxy setup.
- **Human pacing and behaviour.** Timing, pointer motion and typing rhythm are a separate
  surface that no fingerprint change addresses.
- **Per-account quotas and rate limits.** A new fingerprint is not a new account, and it
  does not reset a counter the service keeps on its own side.

Changing the fingerprint fixes the fingerprint. It does not fix the network or the
behaviour, and claiming otherwise would be false.

## Conclusion

Can you be fingerprinted in incognito mode? Yes, identically to a normal window, because
private mode clears the cookies and storage a site wrote to your machine and leaves every
readable property of the browser (canvas, WebGL, fonts, user agent, timezone, TLS)
exactly where it was. Stateless fingerprinting reads those properties, so it works the
same in either mode, and a service can re-link you across incognito windows on the
fingerprint alone.

Changing the values themselves is a different operation from clearing state, and it is
the one incognito does not perform. invisible_playwright performs it per seed, so the
same input gives the same consistent identity and a new input gives a new one. Pair that
with a clean exit and human pacing, because the fingerprint is only one of the surfaces a
session is judged on.

## Short answers to the questions that lead here

**Does incognito mode stop fingerprinting?** No. It clears cookies and site storage at
the end of the session and changes nothing about canvas, WebGL, fonts, user agent,
timezone or TLS, which are what fingerprinting reads.

**Can a site tell it is the same person across two incognito windows?** Yes. The
fingerprint is stable and does not depend on stored state, so the same identifier is
computed in each window and the empty cookie jar is refilled and re-linked on the first
request.

**What does private mode actually protect me from?** Leaving state behind on the machine:
history, cookies and cache from that session. That is a real feature, just not an
anti-fingerprinting one.

**Does incognito change my IP address?** No, and neither does changing the fingerprint.
The exit IP and its reputation are a separate surface you have to handle yourself.

**How do I actually get a different fingerprint?** Change the values the browser reports
rather than clearing what a site stored. invisible_playwright does this per seed, so a
new seed is a new coherent identity.

**Is a new seed the same as a new incognito window?** No. An incognito window is the same
machine with its storage wiped. A new seed is a different machine, self-consistent across
its fields.

## Sources

- Firefox's own documentation of what private browsing clears (cookies, storage, history)
  and, by omission, what it does not.
- The specs behind each stateless signal above:
  [`HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
  for the canvas read-back, [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
  for the GPU renderer/vendor string, and [RFC 8446](https://datatracker.ietf.org/doc/html/rfc8446)
  for where the TLS handshake sits relative to page JavaScript.
- The public detection suites named in this set, read for how each computes an identifier
  from stateless components, each with its own page here.
- This project's seed-to-fingerprint pipeline and release gates, which measure the same
  seed producing the same identity across runs.

**See also:** [how a stable visitor ID is built](fingerprintjs-visitor-id.md),
[what canvas fingerprinting reads and how to change it](canvas-fingerprint-changes-every-run.md),
and [the TLS handshake no browsing mode can hide](ja3-ja4-tls-fingerprint.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Private mode clears what a
site stored on you; this project changes what the browser is, which is the axis private
mode leaves alone.*
