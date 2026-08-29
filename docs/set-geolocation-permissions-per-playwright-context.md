---
title: "Set geolocation and permissions per Playwright context"
description: "Set geolocation coordinates per Playwright context so navigator.geolocation returns your value, and why coordinates must agree with your IP, timezone and locale."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 37
---


# Set geolocation and permissions per Playwright context

A page that calls `navigator.geolocation.getCurrentPosition()` normally triggers a
permission prompt, and an automated run that never answers it just hangs on a dialog no
one clicks. Playwright solves the mechanics of this cleanly: a browser context can be
created with fixed coordinates and with the geolocation permission already granted, so
the call resolves immediately with the position you chose.

Because `invisible_playwright` returns a real Playwright `Browser`, all of that works
here unchanged. What this page adds is the honest half most tutorials skip: a coordinate
is not a value in isolation, it is a claim about where the session is, and a detector
reads it against your exit IP, your timezone and your locale in the same breath. Set one
without the others and you have manufactured a contradiction that scores against you.

## What per-context geolocation actually does

Two independent context options set the position and remove the prompt: `geolocation`
supplies the coordinates, and `permissions=["geolocation"]` pre-grants access so the page
never has to ask. They are independent on purpose.

- [`geolocation={"latitude": ..., "longitude": ...}`](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-geolocation)
  is the position the browser hands to
  [`navigator.geolocation`](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition).
  Add `"accuracy"` in meters if you want to look like a real device rather than a pinpoint.
- [`permissions=["geolocation"]`](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-permissions)
  pre-grants the permission for that context's origin, so the page never sees a prompt and
  never has to time out waiting for one.

Set the coordinates without granting the permission and the API still blocks on a dialog.
Grant the permission without setting coordinates and the API resolves against whatever
default the engine has, which is not what you meant. You almost always want both, scoped
to a context rather than to the whole browser, so different contexts can present
different positions.

## The two-line launch plus the grant

The switch from stock Playwright is the launch line; everything after it is the standard
`Browser` API. Here is the whole operation, coordinates and permission set at context
creation:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(
        geolocation={"latitude": 41.9028, "longitude": 12.4964, "accuracy": 60},
        permissions=["geolocation"],
        locale="it-IT",
    )
    page = context.new_page()
    page.goto("https://example.com")

    pos = page.evaluate(
        """() => new Promise((resolve, reject) =>
            navigator.geolocation.getCurrentPosition(
                p => resolve({lat: p.coords.latitude, lng: p.coords.longitude}),
                err => reject(err.message)
            ))"""
    )
    print(pos)   # {'lat': 41.9028, 'lng': 12.4964}
```

`seed=42` fixes the fingerprint so the run is reproducible; drop it and each session gets
a distinct identity. `browser.new_context(...)`, `context.new_page()` and
`page.evaluate(...)` are all ordinary Playwright, documented upstream, with no wrapped
subset to learn.

## Granting the permission after the context exists

Sometimes you cannot decide the position at context-creation time, or you want to grant
the permission only once a page is already open. Playwright's [`grant_permissions()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-grant-permissions) on the
context does exactly that, and it accepts an `origin` so the grant is scoped to one site
rather than the whole context:

```python
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(
        geolocation={"latitude": 40.4168, "longitude": -3.7038},
        locale="es-ES",
    )
    page = context.new_page()
    page.goto("https://example.com")

    # decide to allow geolocation now, only for this origin
    context.grant_permissions(["geolocation"], origin="https://example.com")

    # move the pin later in the same session
    context.set_geolocation({"latitude": 40.4200, "longitude": -3.7050})
```

[`set_geolocation()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-set-geolocation) updates the coordinates the context reports without a new context, so
a session can move. [`context.clear_permissions()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-clear-permissions) revokes everything you granted if you
want a later page to see the prompt again.

## Why the coordinates have to agree with everything else

A precise coordinate is a strong claim, and a consistency check reads it against three
other things the session already announced: the country and region of your **exit IP**,
the browser **timezone**, and the **locale** and language list. This is the part that
decides whether the disguise holds, and it has nothing to do with Playwright. Coordinates
in one country with an exit IP in another, or a timezone on a different continent, is not
a subtle tell. It is two values that should agree and do not, which is [the exact shape
detectors look for](playwright-detected-as-bot.md): they rarely flag a value for being
unusual, they flag two values for disagreeing.

So pin them together, to the same place:

- **Geolocation** to a point inside the region your exit serves.
- **Timezone** to the IANA zone of that region. In this project the browser timezone is
  [auto-derived from the egress IP](configuration.md) by default, so if your proxy exits
  where your coordinates say, the zone already agrees; pass `timezone=` only to override.
- **Locale and `intl.accept_languages`** to a language plausible for that region.
- **Proxy exit** to that same region in the first place, which is the anchor the other
  three follow from.

The reliable mental model is to choose the exit first and let geolocation, timezone and
locale describe the place that exit is in, rather than setting a coordinate you like and
hoping the network catches up. The failure mode has its own page:
[setting the zone by hand and still getting flagged for a mismatch](timezone-proxy-mismatch.md)
is the same defect one surface over.

## What this fixes and what it does not

`invisible_playwright` is built to look like a real Firefox driven by a real person, and
that is why it clears most detection surfaces on its own: the fingerprint, the TLS
handshake and the driver layer read as a genuine browser, not as automation wearing a
browser. Worth being exact about what geolocation adds on top of that, because it is easy
to overtrust. Per-context geolocation slots into that cleanly, because a real browser
answers `getCurrentPosition()` with a real position and now yours does too, consistent
with the rest of the identity when you pin it correctly.

What it does not do, and cannot: it does not launder a bad exit. A coordinate is a
statement, not proof of location, and if the IP behind it sits in a datacenter range or a
known-blocked pool, a perfect coordinate on a burned address still loses. It also does
not touch per-account quotas, rate limits, or behaviour and timing. Those are yours to
supply, a clean residential exit and human pacing, and no browser flag substitutes for
them. Geolocation makes the location claim coherent; it does not make the network or the
behaviour behind it honest.

## Conclusion

Per-context geolocation in Playwright is genuinely well designed: two context options,
`geolocation` and `permissions=["geolocation"]`, turn a blocking prompt into an immediate,
chosen position, and `grant_permissions()` plus `set_geolocation()` let a session decide
and move later. All of it works unchanged here because the object you drive is a real
Playwright `Browser`.

The one rule that outranks the mechanics: a coordinate is a claim about place, and it only
helps if the exit IP, timezone and locale make the same claim. Pin them together and the
location reads as real. Set one in isolation and you have built the contradiction you were
trying to avoid.

## Short answers to the questions that lead here

**How do I set geolocation in Playwright without the permission prompt?** Create the
context with `geolocation={"latitude": ..., "longitude": ...}` and
`permissions=["geolocation"]`. The coordinates satisfy the API and the pre-grant removes
the dialog.

**Can I set it per context instead of per browser?** Yes, that is the point. Both options
live on `browser.new_context(...)`, so different contexts present different positions in
the same run.

**How do I change the position mid-session?** `context.set_geolocation({...})` updates the
reported coordinates without a new context. `context.clear_permissions()` puts the prompt
back.

**Why do I get flagged even though the coordinate is correct?** Because the coordinate
disagrees with your exit IP, timezone or locale. A detector cross-checks those, and two
values that should agree but do not is the tell. Pin all four to the same region.

**Does grant_permissions work for other permissions too?** Yes, it takes a list; the same
call grants notifications, clipboard read and others. Only grant what the page legitimately
needs, since an unusual grant set is itself a small signal.

**Does setting geolocation make me undetectable?** No. It makes the location claim
coherent. It does nothing for IP reputation, account quotas, rate limits or behaviour, and
a clean coordinate on a burned IP still fails.

## Sources

- Playwright documentation, [the `geolocation` context option](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-geolocation),
  [the `permissions` context option](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-permissions),
  [`grant_permissions()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-grant-permissions),
  [`set_geolocation()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-set-geolocation), and
  [`clear_permissions()`](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-clear-permissions), retrieved 2026-08-28.
- MDN, [`Geolocation.getCurrentPosition()`](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition),
  retrieved 2026-08-28.
- This project's own timezone-derivation behaviour and the consistency failures logged in
  the pages linked above, where a hand-set surface disagreeing with the exit produced a
  flag that the surface alone looked fine.

**See also:** [Configuration](configuration.md) for how the timezone is derived from the
exit, [when the timezone does not match the proxy](timezone-proxy-mismatch.md) for the same
mismatch one surface over, and [the checklist for being detected on one site](playwright-detected-as-bot.md)
for the order to work through when a session is flagged.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The coordinate is the easy
part; making it agree with the exit is the work.*
