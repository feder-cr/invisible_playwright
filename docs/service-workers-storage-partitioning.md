---
title: "Service workers, storage partitioning and automation"
description: "A service worker is state that survives clearing cookies, and blocking service workers is a capability a real browser has. What partitioning changed, and what a browser context actually separates."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 14
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Guides",
      "item": "https://feder-cr.github.io/invisible_playwright/guides.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Browser Identity",
      "item": "https://feder-cr.github.io/invisible_playwright/guides-browser-identity.html"
    },
    {
      "@type": "ListItem",
      "position": 4,
      "name": "Service workers, storage partitioning and automation"
    }
  ]
}
</script>

# Service workers, storage partitioning and automation

Two things about service workers matter for automation and neither appears in the privacy
write-ups that rank for this topic.

The first is that a service worker is **state that outlives the thing you used to clear
state**. Clearing cookies does not unregister it. The second is that turning them off,
which several automation stacks do by default, removes a capability every real browser
has, and [suppression is its own signal](creepjs-explained.md).

This page is what partitioning changed, why a registered worker is stickier than a
cookie, what blocking costs, what a browser context does and does not separate, and how
cache timing turns into an identity bridge.

## What storage partitioning actually changed

Browsers used to key storage by the origin that owned it. An embedded third party got the
same storage everywhere it was embedded, which is what made cross-site tracking through
storage work.

Modern browsers key it by **the pair**: the top-level site plus the embedded origin. The
same third party embedded on two different sites now gets two separate storages. Service
worker registrations, IndexedDB, SharedWorkers, BroadcastChannel and Web Locks are all
partitioned this way in third-party contexts.

Firefox and Safari partition by default and have for years; Chromium arrived later. The
approaches differ in strictness, which is one more way in which claiming one browser while
behaving like another can go wrong.

For automation the practical consequence is narrow but real: **partitioning is not
isolation between your sessions.** It separates a third party's view across sites. It
does nothing about two of your own sessions on the same profile.

## A service worker outlives clearing cookies

This is the part that catches people, and it is easy to verify.

```python
context.clear_cookies()          # cookies gone
# the service worker registered by that origin is still registered
```

A registration lives in the profile alongside its cache. Clearing cookies does not touch
it, and neither does clearing local storage. It is a separate store with a separate
lifetime, and in a persistent profile it persists exactly as long as the profile does.

That matters in two directions:

**As a leak.** A profile you reuse across identities carries service workers registered by
sites the previous identity visited, along with whatever they cached. If your isolation
model was "clear the cookies between runs", it did not cover this.

**As an asset.** A profile that has genuinely been used has registered workers, which is
one more small way in which it looks used rather than fresh. The same argument as
[having cookies at all](recaptcha-v3-score.md).

To actually remove them you clear the profile's storage properly, or you use a fresh
profile. [Which is one of the reasons to pair a profile with an identity](persistent-profiles.md).

## Blocking service workers is visible

Playwright exposes a context option to block them, and several stacks built on it set it
by default because a worker intercepting requests makes network interception harder to
reason about. It is a sensible engineering default.

It also produces a browser in which service workers never register. On a site that uses
one, that is observable in the page:

```js
navigator.serviceWorker.getRegistration().then(r => console.log(r));   // undefined, always
```

A real browser on a site with a service worker has a registration after the first visit.
A browser that never does is either brand new every time, which is
[its own signal](recaptcha-v3-score.md), or is refusing to register them.

This is the same shape as every other suppression in this subject: the value you removed
was less interesting than the absence you created. If you can leave them enabled, leave
them enabled, and handle the interception complexity rather than trading it for a signal.

## What a browser context does and does not separate

Worth stating precisely, because "context" gets used as if it meant "separate browser".

A context separates **storage**: cookies, local storage, IndexedDB, cache, service worker
registrations, permissions. Two contexts do not share a login.

A context does not separate **anything about the machine**: the canvas hash, the GPU, the
fonts, the audio profile, the screen. Those come from the browser process, and there is
one.

[The proxy page](playwright-proxy-per-context.md) is the longer version, and it matters
here because the instinct is to treat a fresh context as a fresh identity. For storage it
is. For everything a fingerprint is made of, it is not.

## Cache timing as an identity bridge

The subtler use, and the reason partitioning exists at all.

A service worker can serve requests from its cache, which is much faster than the network.
A page that measures how long a resource takes can infer whether it was cached, and
therefore whether this browser has been to that resource before. No cookie is involved and
nothing is read.

Partitioning limits this to a single top-level site, which is most of the value. The
residual gaps are documented in the research: workers that reach unpartitioned storage,
and redirect-based flows that touch first-party storage before partitioning applies.

For automation the takeaway is not to defend against this specifically. It is that
**timing is a readable side channel**, which is the same reason
[an attached debugger is detectable through how long JavaScript takes](debugger-timing-detection.md).

## Conclusion

Service workers are a small surface with two practical consequences. They are state that
survives the operation most people think of as clearing state, so a reused profile carries
more history than its cookie jar suggests. And switching them off, which automation stacks
do for good reasons, produces a browser that never registers one, which is visible to any
site that uses them.

Storage partitioning is worth understanding so that you do not mistake it for isolation
between your own sessions. It separates a third party's view across sites, and it has
nothing to say about two of your identities sharing a profile.

## Short answers to the questions that lead here

**Does clearing cookies remove service workers?** No. The registration and its cache are
separate stores with separate lifetimes.

**Should I block service workers in automation?** Only if you must. Blocking makes network
interception simpler and produces a browser that never registers one.

**Does storage partitioning isolate my sessions from each other?** No. It partitions a
third party's storage by top-level site. Two of your sessions on one profile still share
everything.

**Do separate browser contexts have separate service workers?** Yes, along with the rest
of their storage. They still share the machine's fingerprint.

**Can a service worker be used for tracking?** Its cache creates timing differences that
reveal whether a resource was fetched before, which is why registrations are partitioned.

**Do Firefox and Chrome partition the same way?** Not identically. Firefox and Safari
partitioned by default earlier and more strictly.

**See also:** [Playwright proxy per context](playwright-proxy-per-context.md), for what a
context does not separate,
[what a persistent profile fixes and breaks](persistent-profiles.md), and
[why an attached debugger is detectable](debugger-timing-detection.md), for the timing
side channel in another place.

## Sources

- Browser documentation on storage partitioning in Chromium, and WebKit's tracking
  prevention write-ups, for the partitioning model and its history.
- Published measurement work on evasion of partitioned storage, for the residual gaps
  named above.
- Playwright's context option for blocking service workers, and the stacks that set it by
  default.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The service-worker option is exposed rather than
forced here, because blocking is the right call for some jobs and a signal for others.*
