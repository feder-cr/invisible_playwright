---
title: "Can a website detect Clipboard API access?"
description: "A page can see navigator.clipboard and query clipboard-read permission state, but the async Clipboard API is a gesture gate, not a value fingerprint you leak."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 21
---


# Can a website detect Clipboard API access?

Short version: a page can see that `navigator.clipboard` exists, and it can ask the
Permissions API about `clipboard-read`, which answers with a state on Chromium and throws
on Firefox, so either way it can observe your relationship with the clipboard. What it
mostly cannot do is turn that into a stable fingerprint of your machine. The Clipboard API
is interesting to a detector for a different reason than canvas or WebGL are: it is not a
value you leak, it is a gate that only opens for a real user gesture, and the way the
Permissions API answers for it has to match the engine you claim to be.

This page is what the async Clipboard API actually exposes, why it behaves as a gate
rather than a fingerprint, the cross-check a detector runs against it, how to drive it
with a real trusted click, and the honest limit of what a patched browser does here.

## What the async Clipboard API actually exposes

The modern surface is the [Clipboard API](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API)'s
`navigator.clipboard`, with two methods that matter for detection:
`writeText()` and `readText()` (plus their richer `write()`/`read()` cousins). Both return
promises, and both are guarded before they ever touch the system clipboard.

A page can read three things without any special access:

- **Whether `navigator.clipboard` is present at all.** A secure context (HTTPS) in a
  modern browser has it; its absence under a modern user agent is itself a mismatch.
- **What the [Permissions API](https://developer.mozilla.org/en-US/docs/Web/API/Permissions_API)
  says about `clipboard-read`**, by asking it directly:
  `navigator.permissions.query({name: 'clipboard-read'})`. Chromium answers with
  `granted`, `denied` or `prompt`, no dialog involved. Firefox throws instead, because it
  has never implemented that permission name.
- **What happens when it calls a method.** A rejected promise, and the shape of the
  rejection, tells the page which guard tripped.

None of those three is a serial number. They describe policy, not hardware, and that is
the whole point of this surface.

## Why clipboard is a gate, not a value fingerprint

Canvas gives back a hash. WebGL gives back a renderer string. The clipboard gives back
almost nothing that varies from one honest machine to the next, because access is gated
on two conditions the specification requires:

1. **A user gesture.** `readText()` and `writeText()` are only allowed while the document
   has [transient activation](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/User_activation),
   meaning a real interaction (a click, a key press) happened
   recently. Call either one from a script with no gesture behind it and the promise
   rejects.
2. **A second gate that differs by engine.** Chromium backs both operations with a real
   `clipboard-read`/`clipboard-write` permission a page can query through the Permissions
   API. Firefox implements no such permission at all: the identical query throws, and a
   read that does not already satisfy the gesture rule falls back to an ephemeral,
   one-shot "Paste" item in a native context menu instead of a stored grant. Either shape
   is policy the page can observe, and neither hands back a value that varies from
   machine to machine.

For an automation setup this is exactly the kind of check that separates a real browser
from a spoofed one, and it separates them on *behaviour*, not on a leaked value. A script
that dispatches its own synthetic `click` event to satisfy the gesture requirement fails,
because a page-built event carries [`isTrusted=false`](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
and does not count as activation.
This is the same boolean that decides
[whether an automated click reads as real input](playwright-clicks-istrusted.md): the
clipboard gate sits directly on top of it.

So the detector question is not "what is your clipboard value". It is "does the clipboard
open for you the way it opens for a person", and the answer depends on whether your
clicks are trusted.

## The cross-check: the Permissions API answer must match the engine

The sharper check does not call the clipboard at all. It reads what the Permissions API
says about `clipboard-read` and checks whether that answer matches the engine the rest of
the page claims to be.

A modern Firefox user agent implies a specific, boring answer:
`navigator.permissions.query({name: 'clipboard-read'})` throws, because Firefox has never
implemented that permission name. Chromium, by contrast, answers with a real
`granted`/`denied`/`prompt` state. A detector that sees a Firefox user agent and a
Chromium-shaped answer to that one query has found a browser lying about what it is:
either the engine underneath is not really Firefox, or something patched the Permissions
API on top of one without reproducing the one answer a real Firefox has always given.

It is the same family as
[the notifications permission mismatch](permissions-api-consistency.md), where
`Notification.permission` and `navigator.permissions.query({name: 'notifications'})` are
expected to give one coherent answer and a headless browser gives two, and it sits
alongside [the notification-state cross-check detectors already run](notification-permission-detection.md).
Clipboard is a blunter version of the same idea: the expected answer for a real Firefox is
not a state to match, it is a specific failure to reproduce. Nobody has to recognise
automation directly; they just ask the same question the browser already answers for free
and see if the answer fits.

A patched-at-the-engine build does well here for an unglamorous reason: it is a real
Firefox, so the Permissions API answers exactly the way any Firefox does, including by
throwing on a permission name Firefox never implemented. There is nothing to keep in sync
because nothing was bolted on.

## Reading the clipboard with invisible_playwright

Because Playwright's clicks go through the native input path, they arrive as trusted
gestures, so the clipboard gate opens exactly as it would for a hand on the mouse. The
launch is the usual two lines, and after that everything is stock Playwright:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # A page button whose handler calls navigator.clipboard.writeText(...).
    # The Playwright click is a trusted gesture, so the write is permitted.
    page.click("#copy-button")

    # Inspect the surface the way a detector would: presence plus how the
    # Permissions API answers for clipboard-read on this engine.
    report = page.evaluate("""async () => {
        const present = 'clipboard' in navigator;
        let readState = 'unavailable';
        try {
            const s = await navigator.permissions.query({ name: 'clipboard-read' });
            readState = s.state;               // Chromium: 'granted' | 'denied' | 'prompt'
        } catch {
            readState = 'query-failed';        // Firefox: always lands here
        }
        return { present, readState };
    }""")

    print(report)   # e.g. {'present': True, 'readState': 'query-failed'}
```

The values you get back are the ones a genuine Firefox returns: `navigator.clipboard`
present in the secure context, and a `clipboard-read` query that fails exactly the way it
fails on every real Firefox, rather than answering with a hand-forced constant.
[`context.grant_permissions([...])`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-grant-permissions)
works for the permissions Firefox actually implements through Playwright, such as
`geolocation`, but not for `clipboard-read` or `clipboard-write`: pass either one on a
Firefox context and Playwright itself raises `Unknown permission`, on stock Playwright as
much as on this one. There is no separate API and no workaround to learn here, because the
browser has nothing to grant; a `readText()` flow on Firefox depends on the trusted
gesture, not on a permission you can pre-approve.

The measurable point is the one from the section above: the presence flag reads true, the
`clipboard-read` query fails exactly the way it does on every real Firefox, and a
click-gated `writeText()` succeeds because the gesture was trusted. That match is what the
cross-check is looking for, and it is a property of being a real browser rather than of
any value we inject.

## What this does not fix

Passing the clipboard cross-check is a fingerprint-and-driver-layer win, and it is worth
being blunt about where that win stops.

- **IP reputation.** The clipboard gate has nothing to do with your exit address. A
  coherent clipboard on a datacenter IP is still on a datacenter IP. Supply a clean exit;
  see [Configuration](configuration.md) for the proxy setup.
- **Per-account quotas and rate limits.** Looking like a real browser does not raise a
  limit that is counted per account or per address. Those are policy on the other side,
  not a browser property.
- **Behaviour and timing.** The gate cares that a gesture is trusted, not that your pacing
  is human. A trusted click fired in a machine-perfect rhythm is still a rhythm a watcher
  can measure.
- **Clipboard prompts by design.** Some `readText()` flows are meant to prompt a human.
  Automating your way around a deliberate consent step is a different problem from looking
  real, and this page is only about the second.

invisible_playwright is built to look like a real browser driven by a real person, which
is why the fingerprint, the TLS handshake and the driver layer read as a genuine Firefox
and why checks like this one pass. It does not launder a bad IP or manufacture pacing.
Those you supply: a clean proxy and human-shaped behaviour.

## Conclusion

A website can absolutely detect that you are touching the Clipboard API, and it can ask
the Permissions API about `clipboard-read` without ever showing you a prompt. But the
async Clipboard API is a gate, not a value fingerprint: access opens only for a real
trusted gesture, and the way the Permissions API answers for `clipboard-read` has to match
how the engine you claim to be actually answers it. A patched-at-the-engine Firefox driven
by stock Playwright clears both because the gesture is genuinely trusted and the
permission plumbing is the browser's own, throw and all. That handles the browser side.
The exit and the pacing are still yours to get right.

## Short answers to the questions that lead here

**Can a website tell if I read the clipboard?** It can see that `navigator.clipboard`
exists and query `clipboard-read` on the Permissions API, which answers with a state on
Chromium and throws on Firefox. A silently rejected read also tells it which guard
tripped. It cannot turn any of that into a stable machine fingerprint.

**Is the Clipboard API a fingerprinting surface?** Barely, in the value sense. It is a
permission-and-gesture gate, so its interest to a detector is consistency and access
behaviour, not a hash that varies between machines.

**Why does `readText()` reject in my automation?** Almost always a missing trusted user
gesture. On Firefox specifically, a read that does not already satisfy that gesture rule
falls back to an ephemeral native "Paste" menu item a script cannot click. A
script-dispatched click never counts as the gesture in the first place.

**Do Playwright clicks satisfy the gesture requirement?** Yes. They go through the native
input path and arrive as trusted events, so the clipboard gate opens as it would for a
real user.

**What is the clipboard cross-check?** A detector checks whether querying the Permissions
API for `clipboard-read` behaves the way the claimed engine actually behaves: a real state
on Chromium, a specific unsupported-permission error on Firefox. A shim that answers with
a clean state on a Firefox user agent is the mismatch.

**Does a coherent clipboard mean I will not get blocked?** No. It is one browser-side
signal. IP reputation, per-account quotas and behaviour are separate and are yours to
handle.

## Sources

- W3C, [Clipboard API and events](https://www.w3.org/TR/clipboard-apis/), retrieved
  2026-08-28, for the async Clipboard API and its `clipboard-read`/`clipboard-write`
  permission requirements, read from the specification rather than from a blog summary.
- W3C, [Permissions API](https://www.w3.org/TR/permissions/), retrieved 2026-08-28, for
  the `navigator.permissions.query()` behavior the cross-check relies on.
- MDN, [Clipboard API: Security considerations](https://developer.mozilla.org/en-US/docs/Web/API/Clipboard_API#security_considerations),
  retrieved 2026-08-28, for the browser divergence this page relies on: Chromium backs
  `clipboard-read`/`clipboard-write` with a real, queryable Permissions API permission;
  Firefox and Safari implement neither and gate access by transient activation instead.
- Mozilla Bugzilla, [bug 1560373](https://bugzilla.mozilla.org/show_bug.cgi?id=1560373),
  retrieved 2026-08-28, for the `TypeError` Firefox throws on a `clipboard-write` Permissions
  API query instead of returning a state, and for the WONTFIX resolution confirming Firefox
  never planned to add that permission name. The same missing-enum gap is why a
  `clipboard-read` query fails the identical way.
- microsoft/playwright, [issue 19888](https://github.com/microsoft/playwright/issues/19888),
  retrieved 2026-08-28, for the `Unknown permission: clipboard-read` error `grant_permissions`
  raises in stock Playwright. This project's own testing reproduces the identical failure
  on a Firefox context, independent of anything this project changes.
- WHATWG HTML Standard, [Tracking user activation](https://html.spec.whatwg.org/multipage/interaction.html#tracking-user-activation),
  retrieved 2026-08-28, for the transient-activation gesture requirement the clipboard
  gate sits on.
- This project's own permission and gesture gates, which check that a click-gated
  operation succeeds under a trusted event and that what a page can observe from the
  Permissions API matches what the real browser being claimed actually does.

**See also:** [the trusted-click boolean the clipboard gate sits on](playwright-clicks-istrusted.md),
[the permission answers that must agree](permissions-api-consistency.md), and
[the notification cross-check detectors already run](notification-permission-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The clipboard opens for it
because the gesture is genuinely trusted, not because a value was faked.*
