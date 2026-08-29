---
title: "Offline timezone resolution from a proxy exit IP"
description: "Resolve the Playwright browser timezone from a proxy exit IP offline, using a self-updating local database and no per-launch geolocation API call."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 18
---


# Offline timezone resolution from a proxy exit IP

The wrapper resolves the browser timezone offline: it reads the proxy exit IP once, over
the proxy it was already using, and maps that IP to an [IANA timezone](https://www.iana.org/time-zones) against a locally
cached geolocation database, with no per-launch call to a geolocation service. That keeps
the browser's timezone in agreement with its exit without adding a request a real browser
never makes.

The problem this solves is a simple one. If your browser reports a timezone that does not
match the country its traffic comes out of, that disagreement is a cheap, decisive flag.
The obvious fix is to look up the exit IP against a geolocation service at launch and set
the timezone to whatever it returns. That fix quietly creates a second problem: the lookup
itself is a request that does not look like browsing.

This page is about resolving the zone from the exit without phoning a geolocation API on
every launch, why that matters, and the failure behaviour that decides whether a bad
lookup breaks your run or silently ships a mismatch.

## Why the online lookup is itself a tell

A real browser, on a real machine, does not contact a geolocation endpoint before it
loads a page. Its timezone is already set, by the operating system, from a value the user
configured once. So a session that fires an outbound request to a public "what is my
location" service at the exact moment it starts is doing something no ordinary browser
does, and it is doing it from the same address it is about to browse from.

That request is visible. If it goes out over the same proxy, it lands in the exit's logs
next to your real traffic, at a predictable moment, to a category of host that free
scripts use and people do not. If it goes out over the host network instead, it defeats
the whole point: it reads the host's public IP, not the exit's, so the timezone you set
is the timezone of the wrong location.

The development harness for this project did exactly the first thing for a long time: it
called an online geolocation API on every launch to resolve the zone. That is fine for a
harness. It is not something you want in front of a real session, and the shipped wrapper
does not do it.

## What the shipped wrapper does instead

The wrapper resolves the zone offline. The only network step it needs is one it was going
to make anyway: reading the exit IP, over the proxy, so it knows which address the session
actually presents. That IP is then mapped to an IANA timezone using a geolocation database
kept in a local cache, with no call to any geolocation API.

The result is that a session behind a proxy comes up with a timezone that agrees with its
exit, and the only thing an observer at the exit sees is the browsing itself. The zone is
derived, not announced.

Two behaviours make this practical rather than fragile, and they are the rest of this
page: the database keeps itself current without a per-launch API call, and a failed
resolution degrades in a direction that does not silently ship a
[timezone that disagrees with the proxy](timezone-proxy-mismatch.md).

## Deriving the zone from the exit

The whole mechanism sits behind the default. `timezone="auto"` is not an opt-in; it is
what you get if you say nothing, and it resolves from the proxy exit when a proxy is set.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "u",
    "password": "p",
}

# timezone defaults to "auto": the zone is derived from the proxy exit,
# offline, with no per-launch call to a geolocation service.
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    tz = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    print("browser timezone:", tz)   # e.g. America/Chicago for a US exit
```

The `browser` object is a real Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser),
so every method works as documented upstream. Nothing about the timezone path changes the
API surface.

If you already know the zone you want, pass an explicit IANA identifier and the resolution
step is skipped entirely. This is the right choice when the exit is fixed and you would
rather not spend even the one IP-reading request on it:

```python
with InvisiblePlaywright(seed=42, proxy=proxy, timezone="America/Chicago") as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

An explicit IANA string is the only way to override the derived value. Everything else,
including the empty string, means "resolve from the exit". The full set of accepted values
is in [Configuration](configuration.md).

## Keeping the database current without an API call

The offline database stays current without an API call. On each launch the wrapper makes
one cheap, unmetered check against a permalink that always points at the newest published
build, and downloads only when the local copy is behind. There is no metered version check
and no fixed multi-day recheck window.

An offline database has an obvious failure mode: it goes stale, IP ranges get reassigned,
and eventually the exit maps to the wrong country. The naive fix is a version check against
a package index or a release API on every launch, which puts a metered request back into
the launch path and, on a busy host, can hit a rate limit.

The wrapper avoids the metered request. On each launch it makes one cheap, unmetered check
against a permalink that always points at the newest published build of the database. That
check is a lightweight HEAD whose redirect carries the current version in its target, so a
single response tells the wrapper whether the local copy is behind, with no API call and no
rate limit to hit. Only when the permalink names a newer build than the cache does it
download; otherwise it uses what it has. Old versions are pruned so the cache does not grow.

The upstream database is rebuilt on its own weekly cadence. The per-launch check is not a
weekly timer on our side: it runs every launch and simply does nothing on the common path
where the cache is already current. That is deliberate. A timer that only rechecks every N
days is a window in which a freshly published fix does not reach you, and the HEAD is cheap
enough that there is no reason to open that window.

## What happens when the lookup fails

When resolution fails, the fallback depends on whether a proxy is in play, and the two
cases have opposite risks. Behind a proxy the wrapper fails early at launch rather than
ship a mismatch; without a proxy it falls back to the host timezone and the session
continues. The interesting part of any resolution step is what it does when it cannot
resolve, so both cases are set out below.

| Situation | On a failed resolution | Why |
|---|---|---|
| Proxy set | Fails early at launch | A foreign exit paired with the host timezone is a mismatch worth stopping for |
| No proxy | Falls back to the host timezone, continues | The host address is what the session presents, so the host timezone is correct |

**With a proxy**, a failed resolution fails early. The reason is precise: if resolution
fell back to the host's own timezone while the traffic exits through a foreign proxy, the
session would come up with exactly the browser-versus-exit mismatch this whole path exists
to prevent, and it would do so silently. A foreign exit paired with the host timezone is a
`timezone_mismatch` waiting to be scored, so the wrapper refuses rather than ship it. You
find out at launch, not from a block three requests later.

**Without a proxy**, there is no exit to disagree with. The session presents the host's own
address, so the host's own timezone is the correct answer, and a transient failure to read
it should never take the launch down. In that case resolution falls back to the host
timezone and the session proceeds.

So the fallback is not a single policy applied everywhere. It is chosen so that the only
time resolution is allowed to fail loudly is the only time a quiet fallback would have been
a fingerprint bug. This matters most when you
[rotate proxies across sessions](how-to-rotate-proxies-playwright.md) or run
[a different exit per browser context](playwright-proxy-per-context.md), because each new
exit is a fresh resolution, and each one either agrees with its browser or refuses to start.

## Measuring it: one launch, zero geo-API calls

The claim worth checking is not "the timezone is set" but "the timezone was set without an
online geolocation call". You can confirm both from inside the session, using two standard
browser APIs: [`Intl.DateTimeFormat().resolvedOptions()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat/resolvedOptions)
for the zone, and [`Date.prototype.getTimezoneOffset()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/getTimezoneOffset)
for the offset it must agree with.

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=7, proxy=proxy) as browser:
    page = browser.new_page()

    # 1. The zone the page sees, derived from the exit:
    page.goto("https://example.com")
    tz = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")

    # 2. The offset a script would read, which must agree with the zone:
    offset = page.evaluate("new Date().getTimezoneOffset()")

    print("zone:", tz, "offset(min):", offset)
```

For the second half of the claim, watch the process's own outbound connections at launch
with whatever packet or connection tool you already use. Behind a proxy you will see the
session's traffic and a single request to read the exit IP; you will not see a request to a
public geolocation host on every launch, because the IP-to-zone mapping happens against the
local database. That absence is the point. The dev harness produced one such call per launch;
the shipped wrapper produces none.

Because every field is seed-derived, `seed=7` gives the same identity each run, so you can
repeat the measurement and compare it against a stock browser on the same exit. The zone
should match the exit's country and the offset should match the zone. A session that comes
up with the host zone behind a foreign exit is the failure this path is built to turn into a
launch-time error instead of a silent tell. For everything else that has to line up once the
exit and the browser tell the same story, see
[the WebRTC surface behind a proxy](webrtc-leak-proxy.md).

## Conclusion

Matching the browser timezone to the proxy exit is table stakes. Doing it without adding a
per-launch request to a geolocation service is the part that keeps the fix from creating a
new tell, because that request is something a real browser never makes and an exit operator
can see. Deriving the zone from an exit IP the session was already going to read, mapping it
against a local database that keeps itself current with an unmetered check, and failing early
only when a quiet fallback would ship a mismatch, is how the shipped wrapper does it, and all
of it lives behind the default `timezone="auto"`.

## Short answers to the questions that lead here

**How do I set the Playwright timezone to match my proxy?** You do not have to. Leave the
default `timezone="auto"` and the zone is derived from the proxy exit at launch. Pass an
explicit IANA identifier only when you want to override that.

**Does resolving the timezone call an online geolocation API?** No. It reads the exit IP
once, over the proxy, and maps it against a local database. There is no per-launch call to a
geolocation service.

**Will my session break if the timezone lookup fails?** Behind a proxy it fails early, on
purpose, because a foreign exit with the host timezone is a mismatch worth stopping for.
Without a proxy it falls back to the host timezone and the session continues.

**How does the offline database stay current?** Each launch makes one cheap, unmetered check
against a permalink to the newest build and downloads only when the cache is behind. There is
no metered API call and no fixed multi-day recheck window.

**What if the machine is offline?** A cached database is reused, so a launch that cannot reach
the update permalink still resolves from the last copy it downloaded. Only a cold cache with no
network leaves the zone unresolved.

**Why not just set the timezone to my own machine's?** Because behind a proxy your machine and
your exit are in different places, and a browser whose timezone is the host's while its traffic
leaves a foreign exit is the exact disagreement detectors look for.

## Sources

- This project's runtime geolocation path, which resolves `timezone="auto"` from the proxy
  exit against a locally cached database, and the fail-early-with-a-proxy behaviour described
  above.
- The development harness's earlier online-lookup approach, kept for the harness and
  deliberately not shipped in the wrapper.
- IANA, [Time Zone Database](https://www.iana.org/time-zones), retrieved 2026-08-28
- MDN, [Intl.DateTimeFormat.prototype.resolvedOptions()](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat/resolvedOptions), retrieved 2026-08-28
- MDN, [Date.prototype.getTimezoneOffset()](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/getTimezoneOffset), retrieved 2026-08-28

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md) for
everything that has to agree, and [rotating proxies across sessions](how-to-rotate-proxies-playwright.md)
where each new exit is a fresh resolution.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The offline path exists because
the online one, convenient in a harness, is a request a real browser never makes.*
