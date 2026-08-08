---
title: "Permissions API: the two answers that must agree"
description: "The Permissions API and Notification.permission answer one question two ways, and a mismatch long flagged headless browsers. What a real browser shows."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 8
---


# Permissions API: the two answers that must agree

**The [Permissions API](https://developer.mozilla.org/en-US/docs/Web/API/Permissions_API) and [`Notification.permission`](https://developer.mozilla.org/en-US/docs/Web/API/Notification/permission_static) describe the same permission state
through two code paths, and a real browser gives the same answer both ways.** When they
disagree - classically `Notification.permission` says `denied` while
`navigator.permissions.query({name: 'notifications'})` says `prompt` - the browser is
almost certainly headless or automated. The whole set of permission states is a second
signal: granting everything to silence dialogs produces a browser almost nobody has.

There is a headless check that has been in every detection suite for years and takes two
lines:

```js
const a = Notification.permission;                                        // "denied"?
const b = (await navigator.permissions.query({name: 'notifications'})).state;  // "prompt"?
a === 'denied' && b === 'prompt'    // historically: headless
```

Two APIs, one question, and a browser where they disagree. It is the same shape as
everything else in this subject: nobody had to recognise a headless browser, they only
had to ask twice.

This page is why the mismatch exists, why the whole permission set is a fingerprint of
its own, what automation frameworks do to it, the grant that quietly disables protection
elsewhere, and what a normal browser looks like.

## Why the two answers can differ

The two answers can differ because they reach the same permission state through separate
code: `Notification.permission` is a property on the older Notification API, while the
Permissions API is a newer interface built to query any permission the same way. A browser
that implements one path fully and the other partially - or an environment where
notifications are not really available while the permission machinery still answers - ends
up with two different answers to the same question. Historically that is what headless
builds did.

The lesson generalises past this pair, and it is the reason this check keeps working
after the specific bug is fixed:

> **Wherever a browser exposes the same fact through two interfaces, they are a
> consistency check.** You do not need to know which one is correct. You only need them to
> disagree.

The same idea appears in
[the canvas run in a page and in an iframe](sannysoft-explained.md),
[hardwareConcurrency read from a worker](hardware-concurrency-device-memory.md), and
[a fresh copy of a built-in taken from an iframe](tostring-native-code-detection.md).

## The permission set is a fingerprint

Beyond the mismatch, the states themselves carry information. The Permissions API is
enumerable in practice: you query the names you care about and collect the answers.

```js
const names = ['notifications', 'geolocation', 'camera', 'microphone',
               'clipboard-read', 'clipboard-write', 'persistent-storage',
               'midi', 'background-sync'];
```

What that set says about you:

- **A browser where everything is `prompt`** is the overwhelmingly common case, because
  most people never answer a permission dialog on most sites.
- **A browser where everything is `granted`** is rare, and it is what happens when
  automation grants everything to avoid dialogs blocking a script.
- **A browser where everything is `denied`** is also rare, and it is what happens when
  someone sets a blanket deny to avoid prompts.
- **A specific pattern of grants** is more identifying than any single one, because it
  describes a history of decisions.

So the instinct to grant everything, which is very common in automation because it stops
dialogs interrupting a flow, produces an unusual browser. It also
[has a specific consequence for WebRTC](#the-grant-that-turns-off-something-else) that is
worse than the dialog you avoided.

## What automation does to permissions

Automation touches permissions two different ways, and only one of them causes lasting
trouble: a scoped grant at the context level that disappears with the context, and a grant
made through the browser's own UI or profile that survives into future sessions.

**Granting at the context level.** Most frameworks let you
[pre-grant permissions when you create a context](set-geolocation-permissions-per-playwright-context.md).
This is scoped to that context and disappears with it, which is the tidy version.

**Granting through the browser's own UI or profile.** A permission granted this way is
written into the profile and survives. In a persistent profile it survives forever, or
until something removes it.

Both change what the Permissions API reports, but only the profile grant follows you into
future sessions.

### The grant that turns off something else

This is the part worth knowing even if you never think about permissions.

Firefox conditions two separate WebRTC address protections on the absence of camera or
microphone access: restricting ICE gathering to the default route, and masking the host
candidate behind an mDNS name. A granted permission switches both off together.

The check counts a **persisted** grant, not only an active capture. So a camera permission
granted once, in a profile you reuse, disables those protections for that origin in every
future session on that profile.

[The persistent profile page](persistent-profiles.md) covers the audit, and
[the WebRTC page](webrtc-leak-proxy.md) covers what is being switched off. The short
version: if you use a persistent profile with a proxy, do not grant camera or microphone
unless you mean it, and check what is already stored.

## What a normal browser looks like

Working backwards from all of the above:

- **The two notification answers agree.** Whatever the state is, both APIs report it.
- **Most states are `prompt`.** That is what not having answered dialogs looks like.
- **Grants are specific and few**, matching the sites the profile has actually used.
- **Nothing is granted that the session never asked for**, especially camera and
  microphone.
- **The set is stable** for the identity, because permission decisions do not evaporate.

The general rule is the same as elsewhere: aim for the state a real browser would be in,
not for the state that is most convenient for your script.

## Checking your own

```js
const names = ['notifications', 'geolocation', 'camera', 'microphone',
               'clipboard-read', 'persistent-storage'];
const out = {};
for (const name of names) {
  try { out[name] = (await navigator.permissions.query({name})).state; }
  catch (e) { out[name] = 'unsupported'; }
}
out['Notification.permission'] = Notification.permission;
console.table(out);
```

What to look for:

- `notifications` from the Permissions API and `Notification.permission` say the same
  thing.
- Most entries are `prompt`.
- `camera` and `microphone` are not `granted` unless you intended that.
- Nothing throws that a real browser answers, and nothing answers that a real browser does
  not implement, since a name your claimed browser does not support should be
  `unsupported` there too.

Then run the same snippet in a stock browser on the same machine and compare the shape,
which is
[the method that catches what verdicts miss](how-to-test-bot-detection.md).

## Conclusion

The permission surface is small, cheap to read and easy to get wrong in the direction
that feels helpful. Granting everything to stop dialogs interrupting a script produces an
unusual browser and, on Firefox with a persistent profile, quietly removes protection you
set up somewhere else.

The two-answer check is old and still worth running, not because the original headless
bug is common now, but because the pattern it belongs to is how most of this works: ask
the same question twice and see whether the answers match.

## Short answers to the questions that lead here

**Why do `Notification.permission` and `navigator.permissions.query` disagree?** They
reach the same state through different code, and some environments implement one path more
completely than the other. Headless builds historically disagreed.

**Is that still used to detect headless?** The specific bug is largely fixed. The check
costs two lines, so it is still run.

**Should I grant all permissions in automation?** No. It stops dialogs and produces a
browser whose permission set almost nobody has, and on Firefox a camera grant disables
WebRTC address protections.

**What should the permission states be?** Mostly `prompt`, with the few specific grants a
real profile would have accumulated.

**Do granted permissions persist?** Context-level grants disappear with the context.
Profile-level grants persist, including into future sessions on a reused profile.

**Can a page enumerate my permissions?** It can query the names it knows, which in
practice is the same thing.

**See also:** [what a persistent profile fixes and breaks](persistent-profiles.md),
[the same pair read as a bot-detection signal](notification-permission-detection.md),
[WebRTC leak with a proxy](webrtc-leak-proxy.md), and
[what sannysoft actually checks](sannysoft-explained.md), whose permissions row is this
check.

## Sources

- MDN for `Permissions.query()` and `Notification.permission`.
- The long-standing headless detection suites that compare the two, where this check has
  lived for years.
- Firefox's conditioning of `default_address_only` and `obfuscate_host_addresses` on
  active-or-permitted capture, which is the mechanism behind the grant described above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The permission trap is in our notes as a gap with no
patch, which is why this page tells you to audit rather than promising it is handled.*
