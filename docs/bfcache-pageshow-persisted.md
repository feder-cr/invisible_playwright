---
title: "BFCache and pageshow.persisted under browser automation"
description: "Automation drivers disable the back/forward cache, so event.persisted is always false and every back navigation is a full reload. Why that is observable, and what turning it back on costs."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 13
---


# BFCache and pageshow.persisted under browser automation

Press Back in a real browser and the previous page usually comes back instantly,
restored from memory with its JavaScript state intact. The page is told about it: a
`pageshow` event fires with `persisted` set to `true`.

Under automation that almost never happens, because the drivers switch the cache off.
Every back navigation becomes a full reload, `persisted` is always `false`, and a page
that pays attention to the difference is looking at a browser that behaves unlike every
consumer browser of the last decade.

This page covers what the cache does, what automation does to it, why the difference is
visible, what turning it back on breaks in your own tests, and how to decide.

## What the back/forward cache actually does

When you navigate away, the browser can keep the whole page in memory rather than
discarding it: the DOM, the JavaScript heap, the scroll position, the state of your
variables. Navigate back and it is restored rather than rebuilt.

The page finds out through `pageshow`:

```js
window.addEventListener('pageshow', (e) => {
  console.log('restored from bfcache:', e.persisted);
});
window.addEventListener('pagehide', (e) => {
  console.log('kept in bfcache:', e.persisted);
});
```

`persisted` is `true` on a restore and `false` on an ordinary load. It is a single
boolean, available to any script, and it is normally used for legitimate purposes such as
re-running analytics on a restored view.

## What automation does to it

Two different situations, both ending in the same place.

**Firefox under Playwright.** The driver that Playwright uses to control Firefox sets
`disallowBFCache` on every docshell it manages. That is a deliberate choice on its part:
a cached page makes automation less predictable, because state survives navigations that
a test expects to reset.

**Chromium under automation.** The cache is generally not in play either, and there is a
long-standing question on the Playwright tracker about how to enable it at all.

The result is the same in both: `event.persisted` is `false` on every navigation, always,
in a browser where a real user would frequently see `true`.

## Why a page can notice

The difference is not subtle once you look for it, and none of it needs a fingerprinting
library.

**The event itself.** A session that navigates back and forth several times and reports
`persisted: false` every single time describes a browser with the cache disabled.
Disabling it is not something ordinary users do.

**The timing.** A restore is immediate because nothing is rebuilt. A reload fetches,
parses and executes. A page that measures how long its own initialisation took on a back
navigation sees two very different numbers.

**The side effects of a reload.** Scripts run again, timers restart, counters reset,
analytics fire a fresh page view. A page holding in-memory state watches it vanish on
every back navigation, which is behaviour it can compare against what it expects.

None of this is a strong standalone signal. It belongs to the same family as
[the pointer that teleports](human-mouse-movement.md): one more way in which the session
does not behave like a person's browser, cheap to check and rarely thought about.

## How this project handles it, and what it costs you

The behaviour here is inverted relative to the stock driver: the cache is **left enabled
by default**, so a back navigation restores the page and `persisted` comes back `true`,
which is what a real Firefox does. The old behaviour is available behind a preference for
anyone who needs it.

The cost is real and you should know it before you enable this anywhere:

**`go_back()` waiting for `load` can time out.** A bfcache restore does not fire `load`,
because nothing loaded. Code that navigates back and waits for that event will sit there
until the timeout expires.

```python
# fragile once bfcache is enabled
page.go_back()                      # may wait for a load that never comes

# robust either way
page.go_back(wait_until="domcontentloaded")
page.wait_for_selector("#something-on-the-previous-page")
```

Waiting for something you can see is more robust than waiting for a lifecycle event
anyway, and it works whether or not the page was restored.

**State survives navigation.** That is the point of the cache, and it is also the thing
that makes a test suite non-deterministic if the suite assumed a fresh page. If your
tests depend on state being reset by going back, they were depending on the automation
behaving unlike a browser.

## Deciding which behaviour you want

The two cases genuinely differ and it is worth being explicit rather than picking a
default:

- **A test suite against your own application.** Predictability is worth more than
  realism, nothing is watching, and a cache that preserves state between steps causes
  flakiness. Disabling it is reasonable.
- **A session that needs to look like a person's browsing.** Realism wins, and the cost
  is writing navigation waits that do not depend on `load`.

The mistake is not choosing either one. It is not knowing which you have, and then being
surprised by a timeout or by a signal.

## Conclusion

The back/forward cache is a small thing that automation turns off for good engineering
reasons, and turning it off produces a browser whose back button behaves differently from
every consumer browser.

The fix is not complicated. Leave the cache on and stop waiting for `load` after a back
navigation. The second half is what actually costs effort, and it is the reason the stock
drivers made the other choice.

## Short answers to the questions that lead here

**What does `event.persisted` mean?** That the page was restored from the back/forward
cache rather than loaded again.

**Why is `persisted` always false in Playwright?** Because the driver disables the cache,
in Firefox explicitly and in Chromium effectively.

**Can a site detect that bfcache is disabled?** It can observe that back navigations
never restore, which is not how consumer browsers behave. It is a weak signal on its own
and it belongs to a family of weak behavioural signals.

**Why does `go_back()` hang after I enable bfcache?** Because a restore does not fire
`load`. Wait for `domcontentloaded` or for an element instead.

**Should I enable it?** For realistic sessions yes, for deterministic test suites
probably not.

**Does bfcache leak state between pages?** It preserves state within a page across
navigation, which is its purpose. There is an open Playwright issue about globals
appearing to leak between pages because of it, which is worth reading if you see
something odd.

**See also:** [human-like mouse movement](human-mouse-movement.md), the same category of
behavioural difference, and
[the checklist for being detected on one site](playwright-detected-as-bot.md), where
behaviour is step five.

## Sources

- MDN on the back/forward cache and the `pageshow` and `pagehide` events.
- The Playwright tracker's open items on bfcache behaviour and on enabling it under
  Chromium.
- This project's driver, which reads a preference before disabling the cache and leaves
  it enabled by default, with the `go_back` caveat recorded alongside it.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The preference exists because the timeout described
above is real, and a test suite that hits it deserves a way back to the old behaviour.*
