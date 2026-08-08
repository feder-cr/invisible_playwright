---
title: "Playwright timezone does not match the proxy IP"
description: "You set timezone_id and still get flagged. Timezone and locale are not one value each: several surfaces a detector cross-checks against your exit IP."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 5
---


# Playwright timezone does not match the proxy IP

You set `timezone_id`. You are still flagged for a timezone mismatch. This page is why
that happens, and it is usually not the setting being ignored.

The short version: **the timezone is not one value, and neither is the locale.** They
are half a dozen surfaces, fed by different mechanisms inside the browser, and a
detector compares them with each other and with your exit IP. Setting one of them
correctly leaves the others answering the old question.

## Everything that has to agree

A timezone match check reads nine values, not one, and it flags you when any two of
them disagree. Run this on any page and read all of it, not the first line:

```js
Intl.DateTimeFormat().resolvedOptions().timeZone   // "America/New_York"
new Date().getTimezoneOffset()                      // 300  (minutes, inverted)
```

[`Intl.DateTimeFormat().resolvedOptions()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat)
and [`getTimezoneOffset()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/getTimezoneOffset)
are two separate mechanisms, the second with a sign convention worth reading before you
trust it:

```js
new Date().toString()                               // "... GMT-0500 (Eastern Standard Time)"
navigator.language                                  // "en-US"
navigator.languages                                 // ["en-US", "en"]
new Intl.NumberFormat().format(1234.5)              // "1,234.5"  or  "1.234,5"
new Date().toLocaleString()                         // month names, order, separators
```

Plus two things that are not in JavaScript at all:

- the **`Accept-Language` header** your requests actually carry
- the **country of the IP** the request left from, which is also what [WebRTC reports independently](webrtc-leak-proxy.md)

Nine values. A mismatch check does not need any of them to be strange. It needs two of
them to disagree.

The combinations that get caught most often:

- **Proxy in one country, timezone from your own machine.** The classic. Your host is
  in Europe, your exit is in the United States, and you never set anything.
- **Timezone set, locale not.** A US exit, `America/New_York`, and `de-DE` in
  `navigator.languages` because that is what the machine had.
- **`Intl` output disagreeing with `navigator.language`.** These are genuinely separate
  mechanisms in the browser, and one can be set while the other is not. Read both.
- **`Accept-Language` disagreeing with `navigator.languages`.** Same list, two places,
  and the header is often set by a different layer than the browser property.
- **The offset not matching the zone on that date.** `America/New_York` is -300 in
  January and -240 in July. A hardcoded offset is right for half the year.

## Why the environment variable does not work

Setting the `TZ` environment variable before launch does not reliably fix this in Firefox
on Windows, because Firefox only honours `TZ` in POSIX form there, not the IANA identifier
you actually want. It is worth knowing precisely when that fails.

On Windows, Firefox ignores `TZ` unless it is in **POSIX** form, like `EST5EDT`. The
IANA identifier you actually want, `America/New_York`, is not recognised there. So the
variable appears to be set, the browser reports something else, and the usual
conclusion is that the setting was ignored.

The mechanism that does work in current Firefox is a per-realm override applied by the
engine, which is what Playwright's
[`timezone_id`](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-timezone-id)
reaches. That is also why the value
has to be re-applied to realms created later: a new page, an iframe on another site, a
worker. Under site isolation each new site can be a new process, and anything not
re-applied there falls back to the build's default.

**If you are debugging this, check the value inside an iframe from a different origin,
not just on the top page.** That is where a partially applied override shows up, and it
is the check most people never run.

## Derive it from the exit, do not configure it

The structural fix is to stop treating the timezone as configuration and start treating
it as a consequence of the proxy.

If you hardcode `America/New_York` and your proxy pool rotates through three countries,
you are wrong two thirds of the time, and you are wrong in the specific way that gets
looked for. The zone has to be resolved from the address the session actually leaves
from, at launch, every time.

How this project does it, because the details are the part that bites:

- One lookup discovers the **egress IP through the proxy**, not from the host. A
  request that does not go through the proxy tells you about your own connection.
- That IP maps to a zone with an **offline database**, so there is no third-party
  request per launch announcing that a new session started.
- The **same** lookup feeds the locale and the WebRTC public address, so those three
  cannot drift apart. They come from one answer, not three.
- **On failure with a proxy configured, it fails the launch** instead of falling back
  to the host timezone. That fallback is the exact mismatch you set out to avoid, and a
  silent fallback to it is worse than an error.

That last one is a small decision that took a while to get right. A fallback that
"keeps things working" is the wrong default when the thing it falls back to is the bug.

## Country-level is as good as it gets, and that is usually fine

IP-to-location databases are reliable at country level and much less so below it. One
country gets one zone, which means a US exit becomes a single American zone rather than
the correct one of six, and Belgium gets one of its two languages.

This is fine and worth saying out loud, because it sets a limit on how much effort is
worth spending. Nobody is checking whether your zone is the right one *within* the
country. They are checking whether it is in the right country at all. Pick a plausible
zone for the country and stop.

## Checking it

Through the proxy you actually deploy with, on a real page:

1. Read all nine values above in one go.
2. Check the country of your exit IP separately.
3. Confirm the offset is correct **for today's date**, not in general.
4. Repeat inside a cross-origin iframe.
5. Run the same page on a stock browser on a machine in that country, and diff.

Step five is the one that catches what you did not think to check.

## Short answers to the questions that lead here

**Does `timezone_id` in Playwright actually work?** Yes, for the timezone. It does not
touch the locale, the `Accept-Language` header, or your exit IP.

**Do I need to set `locale` too?** Yes, and match it to the same country. A US exit
with a German locale is one of the most commonly detected combinations there is.

**Can I just use UTC?** You can, and almost nobody is genuinely in UTC. It is a small
signal on its own and an obvious one in combination with a residential IP.

**Why does it work headed and fail headless?** Usually not headless. Usually the two
runs left from different addresses.

**Why does the offset look inverted?** `getTimezoneOffset()` returns minutes to add to
local time to get UTC, so zones west of Greenwich are positive. It is not a bug and it
is a common source of an hour's confusion.

**Is a VPN better than a proxy here?** For this specific problem they are equivalent, as
long as you resolve the zone from the address you actually exit through.

**See also:** [Firefox preferences that silently do nothing](firefox-prefs-not-applying.md),
if you are here because a setting looks ignored,
[WebRTC leak with a proxy](webrtc-leak-proxy.md), which is the other half
of "the browser and the network disagree",
[the checklist for being detected on one site](playwright-detected-as-bot.md), and
[why web scraping keeps getting blocked even with good proxies](web-scraping-getting-blocked-proxies.md),
the tool-agnostic version of this same argument.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The Windows `TZ` behaviour above is something we found the slow way, after concluding
several times that the setting was being ignored.*
