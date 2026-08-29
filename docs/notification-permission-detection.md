---
title: "Notification.permission as a bot-detection signal"
description: "Detectors cross-check navigator.permissions.query against Notification.permission to catch inconsistencies; real browsers report one coherent 'default' state."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 18
---


# Notification.permission as a bot-detection signal

There is a cheap, quiet check that a lot of detection scripts run and almost nobody
talks about. It asks the browser the same question twice, through two different APIs,
and looks at whether the two answers agree. On a real browser they always do. On many
headless or over-patched setups they do not, and the disagreement is the tell.

The two APIs are [`Notification.permission`](https://developer.mozilla.org/en-US/docs/Web/API/Notification/permission_static) and
`navigator.permissions.query({name: 'notifications'})`, part of the [Permissions API](https://developer.mozilla.org/en-US/docs/Web/API/Permissions_API). This page is what each one
returns, why they can drift apart, why a genuine browser keeps them consistent, and how
to read them yourself. It closes with the honest limit: a coherent permission state is
one signal among many, and on its own it proves very little.

## Two APIs, one underlying state

Web notifications expose their permission through two separate surfaces that were added
to the platform at different times.

The older one is a plain string:

```javascript
Notification.permission   // "default" | "granted" | "denied"
```

The newer one is the general Permissions API, which returns a status object
asynchronously:

```javascript
const status = await navigator.permissions.query({ name: "notifications" });
status.state   // "prompt" | "granted" | "denied"
```

Both are reading the same underlying decision: has the user allowed this origin to show
notifications, refused, or not been asked yet. The vocabularies differ by one word.
`Notification.permission` calls the never-asked state `"default"`; the Permissions API
calls it `"prompt"`. That mapping is fixed and known: `"default"` corresponds to
`"prompt"`, `"granted"` to `"granted"`, `"denied"` to `"denied"`.

Because both read one state, a browser that has not been asked reports `"default"` from
the first and `"prompt"` from the second, every time, with no contradiction. The pair is
coherent because there is nothing to make it otherwise: there is a single genuine
permission value and both APIs are just formatting it.

## How a detector turns that into a signal

A detection script does not need the notification feature at all. It needs the two
answers, and it compares them.

```javascript
async function notificationConsistency() {
  const legacy = Notification.permission;                 // "default" on a fresh origin
  const modern = (await navigator.permissions
    .query({ name: "notifications" })).state;             // "prompt" on a fresh origin

  const expected = { default: "prompt", granted: "granted", denied: "denied" };
  return {
    legacy,
    modern,
    coherent: expected[legacy] === modern,
  };
}
```

On an untouched browser the object comes back `{ legacy: "default", modern: "prompt",
coherent: true }`. The interesting outputs are the incoherent ones, and there are a few
recognisable shapes:

- **The two APIs disagree.** `Notification.permission` says `"default"` while the
  Permissions API says `"denied"`, or vice versa. That combination cannot happen from a
  real permission decision; it happens when one API is patched, stubbed or shimmed and
  the other is not.
- **The state is forced to `"denied"`.** Some headless configurations and some privacy
  layers hard-deny notifications globally, so a brand-new origin that no user ever
  refused already reports `"denied"` on both APIs. A real first visit is `"default"`, not
  `"denied"`, so a universal `"denied"` on untouched origins is itself unusual.
- **One API is missing or throws.** `navigator.permissions` absent, or the query
  rejecting for the `notifications` name, is an older or stripped environment answering.

The check is popular for exactly the reasons that make good fingerprinting: it is a few
lines, it needs no permission grant, it triggers no prompt, and it reads a state that is
hard to fake consistently once you have started patching one of the two APIs.

The general lesson is the one that runs through most of these notes.
[Detectors rarely ask whether a single value is unusual](playwright-detected-as-bot.md);
they ask whether two values that must agree actually do. This is that pattern applied to
one small corner of the platform.

## Why a real browser stays consistent, and why patched ones drift

The reason a genuine Firefox passes this is boring, which is the point: the permission
state is real. There is one stored decision for the origin, `Notification.permission`
reads it, the Permissions API reads it, and the browser maps `"default"` to `"prompt"`
because the specification says to. Nobody wrote code to keep them in sync; they are in
sync because they are the same fact viewed through two windows.

Drift appears when something sits between the script and that fact. A page-level stealth
plugin that overrides `Notification.permission` to a friendly `"default"` but leaves
`navigator.permissions.query` returning whatever the headless engine underneath actually
holds. A container that globally denies notifications, so the never-asked state is
already `"denied"`. A shim that patches one API and forgets the other exists. Each of
these produces a pair that a real browser cannot produce.

This is the same failure mode as running two disguises at once, and it is worth stating
plainly because it changes how you should use a tool like this one. If the engine already
answers these questions as a genuine browser, adding a JavaScript layer that also answers
them is how you manufacture the contradiction. When the browser is real,
[the extra spoofing layer is what creates the mismatch](playwright-detected-as-bot.md),
not what hides it. The approach invisible_playwright takes is to make the underlying
browser genuine so there is one coherent state to read, rather than to intercept the APIs
and try to keep several lies aligned. That is also why
[a real fingerprint holds together under cross-checks](resist-fingerprinting.md) that a
property-by-property override does not.

## Reading the pair yourself

You can measure this directly. The two-line launch below returns a real Playwright
`Browser`, so `page.evaluate` and every other method work exactly as they do upstream.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")   # notifications state is per-origin, so load a real one

    result = page.evaluate("""async () => {
        const legacy = Notification.permission;
        const modern = (await navigator.permissions
            .query({ name: 'notifications' })).state;
        const expected = { default: 'prompt', granted: 'granted', denied: 'denied' };
        return { legacy, modern, coherent: expected[legacy] === modern };
    }""")

    print(result)   # {'legacy': 'default', 'modern': 'prompt', 'coherent': True}
```

Passing `seed=42` fixes the whole identity, so if you want to rule this check in or out
while debugging something else, you can replay the exact same browser run after run
rather than chasing a value that moves on its own. Read the result, then read it in a
stock Firefox on the same machine and confirm the two match. Comparing against a real
browser instead of trusting a lone `coherent: true` is the method the rest of these notes
keep coming back to in [how to test whether your browser is detected](how-to-test-bot-detection.md):
a value that looks right in isolation and disagrees with a reference browser is exactly
what a verdict misses.

One practical note: notification permission is scoped per origin, so run the check against
a page you actually loaded, not about:blank. A fresh origin you have never interacted with
is the case detectors care about, because `"default"` on a never-asked origin is the
honest answer a real first visit gives.

## Where this sits, and the honest limit

It would be easy to oversell a check this clean. So, plainly: a coherent notification
permission state is one signal among hundreds, and passing it establishes almost nothing
by itself. It says the two notification APIs are reading the same genuine state. It says
nothing about your address, your pacing, or the several hundred other fields a serious
system weighs.

invisible_playwright is built to look like a real Firefox driven by a real person, which
is why it passes most in-browser checks of this kind: the fingerprint, the TLS handshake
and the driver layer read as a genuine browser rather than a patched one, so there is a
single coherent state for a check like this to find. That is a real advantage and it is
also a bounded one. It does not fix a datacenter IP with a bad reputation, a proxy address
that is already on a blocklist, a per-account quota, a rate limit, or behaviour that moves
a pointer in straight lines and types at a metronome. Those you supply: a clean residential
exit and human pacing. The browser being genuine is necessary and nowhere near sufficient,
and any page that tells you otherwise is selling something.

So treat this the way a good detector treats it: as one coherent reading that removes one
reason to distrust the session, not as a pass. [The full checklist for a session that gets
blocked](playwright-detected-as-bot.md) puts the browser layer where it belongs, several
steps below the IP and the behaviour.

## Conclusion

The Permissions-API-versus-`Notification.permission` check is a small, quiet cross-check:
ask the same question through two APIs and see whether the answers agree. A real browser
answers `"default"` and `"prompt"` on a never-asked origin, coherently, every time,
because both APIs read one genuine state. Over-patched and headless setups drift, force a
universal `"denied"`, or throw, and the disagreement is the signal. A genuine engine keeps
the pair consistent for free; a page-level layer that patches one API is how you break it.
Measure it against a stock browser, and remember it proves one thing among many.

## Short answers to the questions that lead here

**What state should notifications report on a normal browser?** On an origin nobody has
been asked about, `Notification.permission` is `"default"` and
`navigator.permissions.query({name:'notifications'}).state` is `"prompt"`. Those two
correspond, so the pair is coherent.

**Why does my automated browser report `"denied"` for notifications?** Some headless
configurations and privacy layers hard-deny notifications globally, so a brand-new origin
shows `"denied"` before any user ever refused. A real first visit is `"default"`, so a
universal `"denied"` is itself a mild tell.

**How do detectors use notification permission?** They read both APIs and check that the
answers map to each other. They do not need to show a notification or get a grant; the
inconsistency between the two reads is the whole signal.

**Does a consistent permission state mean I will not be detected?** No. It is one signal
among many. It says the two notification APIs agree; it says nothing about your IP,
your pacing, or the rest of the fingerprint.

**Should I patch `Notification.permission` to make it look normal?** If the browser
underneath is genuine, patching one API is how you create a mismatch with the other. The
consistent state comes from a real permission value, not from an override.

**Does invisible_playwright handle this?** The underlying Firefox is genuine, so both APIs
read one real state and the pair stays coherent without an interception layer. That covers
the browser side; the IP and the behaviour are still yours to supply.

## Sources

- The [Notification API](https://notifications.spec.whatwg.org/) and the
  [Permissions API](https://www.w3.org/TR/permissions/) as specified: the
  `"default"`/`"granted"`/`"denied"` string and the `"prompt"`/`"granted"`/`"denied"`
  status, and the mapping between them.
- This project's own realness gates, which read permission state as one field among many
  cross-checked against a stock Firefox rather than as a standalone pass.

**See also:** [how CreepJS decides you are lying](creepjs-explained.md) for the same
truth-versus-report logic at scale, [resist fingerprinting the honest way](resist-fingerprinting.md)
for why a genuine state beats a patched one, and [the checklist for being detected on one
site](playwright-detected-as-bot.md) for where the browser layer sits.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The consistency this page
describes is a byproduct of the browser being real, not a trick layered on top of it.*
