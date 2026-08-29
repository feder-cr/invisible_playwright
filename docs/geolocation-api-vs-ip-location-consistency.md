---
title: "Geolocation API vs IP location: keep them consistent"
description: "Three location signals a site cross-checks: Geolocation API coordinates, timezone and exit IP country. How to keep all three consistent in the same region."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 26
---


# Geolocation API vs IP location: keep them consistent

A site that cares where you are does not read one location. It reads three, from three
different mechanisms, and then checks whether they agree. Grant a page precise
coordinates in one country while your traffic leaves from another, and you have handed
that site a contradiction that a single honest browser never produces.

This page separates the three signals, shows how a granted-coordinates prompt in the
wrong place contradicts your exit, and gives a short runnable example that keeps all
three in one region. It also draws the honest line: the browser can set the timezone
and answer the permission prompt, but the country your traffic actually leaves from is
the proxy's to decide, not the browser's.

## The three location signals a site cross-checks

They sound like one fact about you. They are not. They come from three unrelated places
and can each say something different.

| Signal | What reports it | Who controls it |
|---|---|---|
| Geolocation API coordinates | [`navigator.geolocation.getCurrentPosition()`](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/getCurrentPosition) | The browser, only after a permission prompt |
| Timezone | [`Intl.DateTimeFormat().resolvedOptions().timeZone`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat) and the UTC offset | The browser, set from the OS or an override |
| IP location | The country of the address the request left from | The network exit - your proxy |

A detector does not need any one of these to look strange. It needs two of them to
disagree. Coordinates in the middle of Europe, a North American timezone, and an exit
IP in a third country is three answers to one question, and no real machine gives three
answers.

The Geolocation API is the sharpest of the three because it is the most precise. A
timezone puts you on a continent; a country-level IP lookup puts you in a country; a
granted coordinate puts you on a street. If that street is two thousand kilometres from
where your packets emerge, the gap is trivial to compute and decisive to score.

## Where each signal comes from, and which ones you control

The confusion that produces mismatches is treating all three as things the browser sets.
Only two of them are.

**The timezone is the browser's to set, and this wrapper sets it for you.** With the
default `timezone="auto"`, the zone is derived from the proxy exit IP, resolved offline
against a local database, so it agrees with the country your traffic leaves from without
any per-launch call to a location service. The mechanics, and the reason the lookup is
done offline, are in
[offline timezone resolution from a proxy exit IP](offline-geoip-timezone-proxy.md).

**The Geolocation API coordinates are the browser's to set, but only if you ask.** A
real browser does not hand out a position silently. It shows a permission prompt, and
until the user grants it, `getCurrentPosition()` calls the error callback. In automation
you grant the permission and supply the coordinates yourself, through
[stock Playwright's context options](https://playwright.dev/python/docs/api/class-browser#browser-new-context).
Nothing derives them from your exit for you - if you do not place them,
either the prompt blocks the call or you supply a coordinate that came from somewhere
else entirely.

**The IP location is not the browser's at all.** The country of your exit address is
decided by the proxy, and no browser setting changes it. This is the honest floor of the
whole page: you can align the two signals the browser owns to whatever the third one
says, but you cannot make the third one say something else from JavaScript. If the exit
is in the wrong country, the fix is a different exit, not a different coordinate.

So the alignment task is one-directional. Read what the exit is, then place the timezone
and the coordinates to match it. The wrapper already does the timezone half; the
coordinates are the half you have to place on purpose.

## Granting coordinates in the wrong country

Here is the mistake, concretely. You copied a geolocation snippet from somewhere, it had
a latitude and longitude in it, and you never changed them because the page worked.

```python
from invisible_playwright import InvisiblePlaywright

# Proxy exit is in the United States. timezone="auto" will resolve an American zone.
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    # Coordinates left at a European default - this is the contradiction.
    context = browser.new_context(
        geolocation={"latitude": 48.85, "longitude": 2.35, "accuracy": 50},
        permissions=["geolocation"],
    )
    page = context.new_page()
    page.goto("https://example.com")
```

Every layer here is individually fine. The browser is a genuine Firefox, the permission
is granted the way a real user grants it, the coordinate is a valid point on the map. The
problem is only visible when the three signals are read together: the timezone says
America, the granted position says central Europe, and the IP says America again. Two
against one, and the one that disagrees is the most precise of the three.

The failure mode is quiet. Nothing errors. The page gets its position, the script runs,
and the session simply looks slightly impossible to anything that bothers to subtract one
location from another. It is the same shape of bug as
[a timezone left on the host's value behind a foreign proxy](timezone-proxy-mismatch.md):
a value that is correct in isolation and wrong in company.

## Keeping all three aligned: a worked example

The fix is to place the coordinates in the same country the exit and the timezone already
point at, and then read all three back to prove they agree.

```python
from invisible_playwright import InvisiblePlaywright

# A US exit. timezone="auto" resolves an American zone from this exit, offline.
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    # Coordinates placed in the SAME country as the exit, on purpose.
    context = browser.new_context(
        geolocation={"latitude": 40.71, "longitude": -74.01, "accuracy": 60},
        permissions=["geolocation"],
    )
    page = context.new_page()
    page.goto("https://example.com")

    # 1. The coordinates the page can read, now that permission is granted:
    coords = page.evaluate("""() => new Promise(resolve => {
        navigator.geolocation.getCurrentPosition(
            p => resolve([p.coords.latitude, p.coords.longitude]),
            e => resolve("denied: " + e.message));
    })""")

    # 2. The timezone the wrapper derived from the exit:
    tz = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")

    # 3. The UTC offset, which must agree with that zone for today's date:
    offset = page.evaluate("new Date().getTimezoneOffset()")

    print("coords:", coords, "zone:", tz, "offset(min):", offset)
```

The `browser` object is a real Playwright `Browser` and `context` is a real
`BrowserContext`, so `new_context`, `geolocation`, `permissions` and `getCurrentPosition`
are all stock, documented behaviour - the wrapper adds no new surface here. What it adds
is the timezone half of the alignment: signal 2 already matches the exit before you touch
anything.

Read the three lines of output together, not one at a time. The coordinate country, the
timezone country, and the exit country all have to be the same country. Because every
field is seed-derived, `seed=42` gives the same identity each run, so you can repeat this
against the same exit and confirm the three stay locked together rather than drifting.

If you rotate exits across sessions, this is not a set-once value. Each new exit is a new
country, and the coordinate you grant has to move with it. The timezone moves on its own
because it is derived; the coordinate does not, because you set it.

## What this does and does not fix

invisible_playwright is built to look like a real browser driven by a real person, and
that is why it clears most detection: the fingerprint, the TLS handshake and the driver
layer read as a genuine Firefox rather than as an automation stack wearing a disguise.
Aligning the three location signals removes one more specific contradiction from that
picture.

It does not make you undetectable, and nothing on this page should be read that way.
Aligning geolocation, timezone and exit country fixes exactly one class of tell: the
one where your location signals disagree with each other. It does not touch:

- **IP reputation.** A perfectly consistent location on a datacenter address that a
  thousand other people are using this minute is still a bad address. The coordinates
  agreeing with it does not make it clean.
- **Per-account quotas and rate limits.** These are counted server-side, per identity or
  per address, and no browser property changes the count.
- **Behaviour and timing.** Pointer motion, typing rhythm, and the pace of a session are
  watched independently of where you claim to be. For agents specifically, see
  [the pause shaped like model latency](ai-browser-agents-stealth.md).

Those are the reader's to supply: a clean residential exit, human pacing, and sensible
per-account limits. The browser gets the fingerprint and the location story right; the
network and the behaviour are still yours to get right. When every location signal lines
up and a block persists anyway, the cause is usually one of the three above, which is the
subject of [why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md).

## Conclusion

Location is three signals, not one: the Geolocation API coordinates, the browser
timezone, and the country of the exit IP. The browser owns the first two and the proxy
owns the third, and a site that cares will check that all three tell the same story. This
wrapper derives the timezone from the exit for you, so the one signal people most often
forget is handled by default; the coordinates are the piece you place by hand, and the
only rule is to place them in the same country the exit is already in. Get that right and
you have removed a sharp, cheap-to-compute contradiction. You have not removed the need
for a clean address and human pacing, and no honest tool claims to.

## Short answers to the questions that lead here

**Does setting `geolocation` in Playwright change my IP location?** No. It changes what
`navigator.geolocation` reports to the page. The country of your exit address is the
proxy's, and nothing in the browser moves it.

**Do I have to set coordinates at all?** Only if the site asks for them and you want to
answer. If you never grant the permission, `getCurrentPosition()` calls its error
callback, which is also what a real user who clicks "block" produces. What you must not
do is grant coordinates that sit in a different country than your exit.

**Why does my timezone already match the proxy but my coordinates do not?** Because the
wrapper derives the timezone from the exit automatically with `timezone="auto"`, but it
does not invent coordinates for you. You supply those, so a stale default sits in the
wrong place while the timezone has already moved.

**How far off can the coordinate be before it matters?** Country level is the line that
matters. A site subtracts the granted point from the IP-derived location, and a gap of
another continent is decisive. Being in the right country but the wrong city is not what
gets scored.

**Does this make me undetectable?** No. It removes one contradiction between three
location signals. IP reputation, account quotas, rate limits and behaviour are separate
and unaffected, and a clean location on a burned address still loses.

**How do I confirm all three agree?** Read the coordinates, the timezone and the exit
country in one session and check they name the same country. Reading one and assuming the
others is how the mismatch survives.

## Sources

- This project's runtime timezone path, which resolves `timezone="auto"` from the proxy
  exit against a locally cached database, described in full in
  [offline timezone resolution from a proxy exit IP](offline-geoip-timezone-proxy.md).
- Stock Playwright documentation, [`Browser.new_context`](https://playwright.dev/python/docs/api/class-browser#browser-new-context),
  retrieved 2026-08-28, for the `geolocation` and `permissions` options used unchanged.
- The [Configuration](configuration.md) page for the proxy and timezone settings the
  example above relies on.

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md)
for the other half of "the browser and the network disagree", and
[the WebRTC surface behind a proxy](webrtc-leak-proxy.md), which is a fourth place the
network can contradict the browser.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The wrapper aligns the
timezone with your exit for you; the granted coordinates are the one location signal you
still have to place on purpose.*
