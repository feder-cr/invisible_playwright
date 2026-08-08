---
title: "Can a website detect Clipboard API access?"
description: "A page can see navigator.clipboard and query clipboard-read permission state, but the async Clipboard API is a gesture gate, not a value fingerprint you leak."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 21
---


# Can a website detect Clipboard API access?

Short version: a page can see that `navigator.clipboard` exists and it can ask the
Permissions API what state `clipboard-read` is in, so yes, it can observe your
relationship with the clipboard. What it mostly cannot do is turn that into a stable
fingerprint of your machine. The Clipboard API is interesting to a detector for a
different reason than canvas or WebGL are: it is not a value you leak, it is a gate that
only opens for a real user gesture, and its permission state has to agree with a second
API that reports the same thing.

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
- **The permission state for `clipboard-read`**, by asking the
  [Permissions API](https://developer.mozilla.org/en-US/docs/Web/API/Permissions_API)'s
  `navigator.permissions.query({name: 'clipboard-read'})`. That returns `granted`,
  `denied` or `prompt` without prompting anyone.
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
2. **A permission.** Reading additionally requires the `clipboard-read` permission;
   writing is allowed under a gesture in most configurations but can also be governed by
   `clipboard-write`.

For an automation setup this is exactly the kind of check that separates a real browser
from a spoofed one, and it separates them on *behaviour*, not on a leaked value. A script
that dispatches its own synthetic `click` event to satisfy the gesture requirement fails,
because a page-built event carries `isTrusted=false` and does not count as activation.
This is the same boolean that decides
[whether an automated click reads as real input](playwright-clicks-istrusted.md): the
clipboard gate sits directly on top of it.

So the detector question is not "what is your clipboard value". It is "does the clipboard
open for you the way it opens for a person", and the answer depends on whether your
clicks are trusted.

## The cross-check: permission state must agree with the Permissions API

The sharper check does not call the clipboard at all. It reads two answers to the same
question and looks at whether they line up.

`navigator.clipboard` being present implies a certain permission model. The Permissions
API is then asked directly: `query({name: 'clipboard-read'})`. On a coherent, real
browser those two facts describe one consistent policy. On an over-patched setup, where
someone bolted on `navigator.clipboard` or forced a permission state from JavaScript
without moving the other half, the two disagree, and the disagreement is the tell.

It is the same shape as
[the notifications permission mismatch](permissions-api-consistency.md), where
`Notification.permission` and `navigator.permissions.query({name: 'notifications'})` are
expected to give one coherent answer and a headless browser gives two. Clipboard adds a
third instance of the pattern, alongside
[the notification-state cross-check detectors already run](notification-permission-detection.md).
Nobody has to recognise automation directly; they just ask the same thing twice through
different code and compare.

A patched-at-the-engine build does well here for an unglamorous reason: it is a real
Firefox. The permission plumbing is the browser's own, so the Permissions API answer and
the clipboard's actual behaviour come from one source and cannot contradict each other the
way a page-level shim does. There is nothing to keep in sync because nothing was bolted
on.

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

    # Inspect the surface the way a detector would: presence plus the
    # permission state that has to agree with it.
    report = page.evaluate("""async () => {
        const present = 'clipboard' in navigator;
        let readState = 'unavailable';
        try {
            const s = await navigator.permissions.query({ name: 'clipboard-read' });
            readState = s.state;               // 'granted' | 'denied' | 'prompt'
        } catch (e) {
            readState = 'query-failed';
        }
        return { present, readState };
    }""")

    print(report)   # e.g. {'present': True, 'readState': 'prompt'}
```

The values you get back are the ones a genuine Firefox returns: `navigator.clipboard`
present in the secure context, and a `clipboard-read` state that matches the browser's
real policy rather than a hand-forced constant. If you need to grant clipboard permissions
explicitly for a `readText()` flow, use Playwright's own
`context.grant_permissions([...])` on the browser context, exactly as you would with
upstream Playwright; there is no separate API to learn here.

The measurable point is the one from the section above: the presence flag and the
permission state agree, and a click-gated `writeText()` succeeds because the gesture was
trusted. That agreement is what the cross-check is looking for, and it is a property of
being a real browser rather than of any value we inject.

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

A website can absolutely detect that you are touching the Clipboard API, and it can read
your `clipboard-read` permission state without prompting. But the async Clipboard API is a
gate, not a value fingerprint: access opens only for a real trusted gesture, and the
permission it reports has to agree with the Permissions API's answer for the same thing.
A patched-at-the-engine Firefox driven by stock Playwright clears both because the gesture
is genuinely trusted and the permission plumbing is the browser's own. That handles the
browser side. The exit and the pacing are still yours to get right.

## Short answers to the questions that lead here

**Can a website tell if I read the clipboard?** It can see that `navigator.clipboard`
exists and query the `clipboard-read` permission state. A silently rejected read also
tells it which guard tripped. It cannot turn any of that into a stable machine
fingerprint.

**Is the Clipboard API a fingerprinting surface?** Barely, in the value sense. It is a
permission-and-gesture gate, so its interest to a detector is consistency and access
behaviour, not a hash that varies between machines.

**Why does `readText()` reject in my automation?** Most often because there was no trusted
user gesture, or the `clipboard-read` permission was not granted. A script-dispatched
click does not count as a gesture.

**Do Playwright clicks satisfy the gesture requirement?** Yes. They go through the native
input path and arrive as trusted events, so the clipboard gate opens as it would for a
real user.

**What is the clipboard cross-check?** A detector compares `navigator.clipboard` presence
against the Permissions API state for `clipboard-read`. On a real browser they describe
one policy; a page-level shim that forces one without the other makes them disagree.

**Does a coherent clipboard mean I will not get blocked?** No. It is one browser-side
signal. IP reputation, per-account quotas and behaviour are separate and are yours to
handle.

## Sources

- The WHATWG and W3C definitions of the async Clipboard API and the Permissions API,
  including the user-activation and `clipboard-read`/`clipboard-write` requirements, read
  from the specifications rather than from a blog summary.
- This project's own permission and gesture gates, which check that a click-gated
  operation succeeds under a trusted event and that the permission state a page can query
  agrees with the browser's real policy.

**See also:** [the trusted-click boolean the clipboard gate sits on](playwright-clicks-istrusted.md),
[the permission answers that must agree](permissions-api-consistency.md), and
[the notification cross-check detectors already run](notification-permission-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The clipboard opens for it
because the gesture is genuinely trusted, not because a value was faked.*
