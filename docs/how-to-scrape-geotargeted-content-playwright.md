---
title: "How to scrape geotargeted content with Playwright"
description: "Scrape geotargeted content with Playwright: a proxy exit IP is one surface. Timezone, locale, number format and geolocation must all agree with it too."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 24
---


# How to scrape geotargeted content with Playwright

Geotargeted content is content a site serves differently by region: prices in a local
currency, availability that changes by country, copy in a local language. To scrape the
version a real visitor in that region sees, your session has to look like it comes from
that region. Most guides stop at "use a proxy in the target country", which is the easy
ten percent. The exit IP is one value. The problem is everything else the browser says
about where it is, and whether those values agree with the IP.

This page is about the surfaces that have to match the exit, why matching only one of
them makes things worse rather than better, and how to drive the whole thing from stock
Playwright.

## Geotargeting is not just the IP

Set a proxy in the target country and you have changed exactly one thing: the address the
request leaves from. You have not changed the browser's timezone, its language list, the
way it formats numbers and dates, or what its geolocation API would report. Those come
from the machine the browser runs on, and your machine is wherever your automation runs,
not where the proxy exits.

So now you have a session that leaves from one country and describes itself as being in
another. A site does not need any single value to look strange to notice this. It needs
two values that should agree to disagree. An IP in one country next to a timezone from
your own is precisely the shape of thing a region check looks for, and it is a shape you
created by fixing the IP and nothing else.

The correct mental model is that the exit IP is the anchor, and every locale-bearing
surface in the browser is a claim that has to point back at that same anchor.

## The surfaces that must agree with the exit

The timezone is not one value, and neither is the locale. Read them all on any page and
you find close to a dozen separate answers, fed by different mechanisms inside the
browser, that a detector can compare with each other and with the exit IP:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    surfaces = page.evaluate("""() => ({
        timeZone:  Intl.DateTimeFormat().resolvedOptions().timeZone,
        offset:    new Date().getTimezoneOffset(),
        dateStr:   new Date().toString(),
        language:  navigator.language,
        languages: navigator.languages,
        number:    new Intl.NumberFormat().format(1234.5),
        localized: new Date().toLocaleString(),
    })""")
    print(surfaces)
```

Add to that list two things that are not in JavaScript at all: the
[`Accept-Language` header and its `navigator.languages` twin](accept-language-navigator-languages.md)
the requests actually carry, and the country of the IP the request left from,
which the browser also reports independently through WebRTC. And
[the geolocation API a page can query directly](geolocation-api-vs-ip-location-consistency.md)
has to fall in the same country too.

Tabulated, that is the set of surfaces a region check reads and the mechanism each is
fed by:

| Surface | Where a page reads it | Should be derived from |
|---|---|---|
| Timezone (IANA name) | `Intl.DateTimeFormat().resolvedOptions().timeZone` | egress IP |
| UTC offset | `new Date().getTimezoneOffset()` | the live zone |
| Interface language | `navigator.language` / `navigator.languages` | egress IP |
| `Accept-Language` header | the HTTP request itself | egress IP |
| Number and date format | `Intl.NumberFormat`, `toLocaleString()` | egress IP |
| Geolocation country | the Geolocation API | egress IP |
| Reported network address | WebRTC | exit IP |
| Exit IP country | the proxy exit | the anchor everything matches |

That is a set of surfaces, not a value. Geotargeting a region means every one of them has
to point at the exit. Setting the timezone by hand fixes one of them and leaves the rest
answering the old question, which is why
[setting `timezone_id` and still getting flagged for a mismatch](timezone-proxy-mismatch.md)
is common enough to have its own page.

## A minimal geotargeted scrape

The design decision that makes this tractable is to stop treating region as configuration
and start treating it as a consequence of the proxy. By default the browser timezone is
auto-derived from the egress IP: the proxy exit if a proxy is set, otherwise the host's
own public IP. The same lookup that finds the egress country feeds the locale and the
address WebRTC reports, so those surfaces come from one answer and cannot drift apart.

In practice that means the geotargeted scrape is the ordinary scrape. You set the proxy,
and the region surfaces follow it:

```python
from invisible_playwright import InvisiblePlaywright

# A residential exit in the target country. The timezone, locale and
# number format are derived from where this exit actually lands.
proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

sf = InvisiblePlaywright(seed=42, proxy=proxy)
with sf as browser:
    print("seed =", sf.seed)          # log it to replay this exact identity later
    page = browser.new_page()
    page.goto("https://example.com/region-specific-page")
    price = page.inner_text("#price")
    print(price)
```

The `browser` object is a real Playwright `Browser`, so everything after the launch is
plain Playwright with no wrapped subset to learn. The only geotargeting-specific choice
you made was which country the proxy exits in.

If you have a reason to force a specific zone despite the exit, an explicit IANA
identifier always wins:

```python
# Explicit zone overrides the auto-derivation. Use only when you are
# certain it agrees with the exit country, or you are re-creating the
# mismatch on purpose.
with InvisiblePlaywright(seed=42, proxy=proxy, timezone="America/New_York") as browser:
    ...
```

That override is a foot-gun for geotargeting specifically, because it lets you pin a zone
that disagrees with the IP. The default exists so you do not have to.

## Why setting timezone_id alone still gets flagged

Setting `timezone_id` fixes the timezone and nothing else: `navigator.languages`, the
`Accept-Language` header, and the locale-driven number and date formatting stay pointed at
the host machine. That gap is why the "just set the timezone" advice keeps producing
detected sessions.

Stock Playwright exposes `timezone_id`, and it works: it changes the timezone the browser
reports. But a session with an exit in one country, `timezone_id` set to match, and
everything else left at the host's default now has a timezone that agrees with the IP and
a language list that does not. You have moved the contradiction, not removed it.

Measured on the surface set above: a proxy exit plus `timezone_id` alone leaves the
language list and the number format still describing the host machine, so two of the
roughly nine cross-checked values disagree with the exit. Deriving all of them from one
egress lookup takes that count to zero. The point of the auto-derivation is not that it is
clever; it is that it reads every surface from a single answer, so there is no second
answer to contradict the first.

The offset is a second, subtler trap even when the zone is right. `America/New_York` is
-300 minutes in January and -240 in July, so a zone that resolves the offset live is
correct year round while a hardcoded offset is right for half the year. Deriving the zone
rather than the offset avoids that class of error entirely.

## Rotating exits without contradicting yourself

Geotargeting at scale usually means more than one country, and a rotating proxy pool is
where hardcoded region values do the most damage. If you pin `America/New_York` and your
pool rotates through three countries, you are wrong two thirds of the time, and wrong in
exactly the way a region check looks for.

The rule that keeps a rotation honest is that region is a property of the exit, resolved
per session, not a constant set once. Because the timezone and locale follow the egress IP
by default, a new session on a new exit gets a new, self-consistent region for free:

```python
from invisible_playwright import InvisiblePlaywright

exits = [
    {"server": "socks5://gate-a.example.com:1080", "username": "u", "password": "p"},
    {"server": "socks5://gate-b.example.com:1080", "username": "u", "password": "p"},
]

for i, proxy in enumerate(exits):
    # A distinct seed per exit gives each session its own machine as well
    # as its own region, so two sessions never share a fingerprint.
    with InvisiblePlaywright(seed=1000 + i, proxy=proxy) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        # region surfaces here already match this exit's country
```

The mechanics of rotating cleanly, including reusing a context versus launching fresh, are
in [how to rotate proxies with Playwright](how-to-rotate-proxies-playwright.md). The point
for geotargeting is narrower: never carry a region value from one exit to the next. Let
each exit set its own.

## Verifying every surface points at the same country

Do not trust that it worked because it launched. Verify the same way a detector would, by
reading every surface and confirming they agree, through the proxy you actually deploy
with:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    report = page.evaluate("""() => ({
        timeZone:  Intl.DateTimeFormat().resolvedOptions().timeZone,
        offset:    new Date().getTimezoneOffset(),
        language:  navigator.language,
        languages: navigator.languages,
        number:    new Intl.NumberFormat().format(1234.5),
    })""")
    for k, v in report.items():
        print(k, "=", v)
```

Then check three things the snippet above cannot tell you on its own:

- Confirm the country of the exit IP separately, because that is the anchor the rest has
  to match.
- Confirm the offset is right for today's date, not in general.
- Read the same values inside a cross-origin iframe, because a region override that is
  applied to the top page but not to a later realm is a partial application that shows up
  only there.

The most reliable check of all is to run the same page on a stock browser on a machine
actually in the target country and diff the two reports field by field, which is the
method described in
[how to test whether your browser is detected](how-to-test-bot-detection.md).

Remember also that WebRTC reports the exit country independently of all of this, so a
[WebRTC path that reports the wrong address or nothing at all](webrtc-leak-proxy.md) will
contradict a perfectly matched timezone. It is part of the same surface set, not a
separate concern.

## Conclusion

Geotargeting is an agreement problem, not a proxy problem. The exit IP is the easy part
and the part every guide covers; the timezone, the locale, the number format, the
`Accept-Language` header and the geolocation country are the parts that get scrapes caught,
because a region check does not look for a strange value, it looks for two values that
should agree and do not. Derive all of them from the exit instead of pinning any of them by
hand, verify them together rather than one at a time, and let each rotated exit set its own
region. Do that and a country-targeted session looks like what it claims to be, all the way
down.

## Short answers to the questions that lead here

**Is a proxy in the target country enough to scrape geotargeted content?** No. It changes
the exit IP and nothing else. The timezone, locale, number format and geolocation still
describe your own machine unless you derive them from the exit too.

**Why do I still get the wrong region after setting `timezone_id`?** Because the timezone
is not the only region surface. `timezone_id` does not touch `navigator.languages`, the
`Accept-Language` header, or the locale-driven formatting, so those still point at your
host and the pair disagrees.

**How many values actually have to match the exit?** Close to a dozen once you count the
timezone, the offset, the language list, the `Accept-Language` header, the number and date
formatting, the geolocation country and the address WebRTC reports.

**Do I have to set the timezone manually for each proxy country?** No, and you should not
for a rotating pool. Let it auto-derive from the egress IP so each exit gets its own
consistent region; hardcoding one zone is wrong for every other country in the pool.

**How precise does the region have to be?** Country level. IP-to-location data is reliable
by country and much less so below it, and region checks look at the country, not the city.
Pick a plausible zone for the country and stop.

**How do I confirm the region is consistent before I trust the scrape?** Read every surface
in one pass, check the exit IP country separately, confirm the offset for today's date,
read the values again inside a cross-origin iframe, and diff against a stock browser in the
target country.

## Sources

- This project's configuration behaviour for proxy schemes and the timezone that is
  auto-derived from the egress IP, read from the shipped documentation rather than from
  memory.
- The nine-surface cross-check and the Windows `TZ` behaviour, from this set's own notes on
  timezone and proxy mismatch, each measured through the proxy rather than on localhost.

**See also:** [why the timezone does not match the proxy IP](timezone-proxy-mismatch.md)
for the full surface list, and [client hints and Sec-Fetch headers](client-hints-sec-fetch.md)
for the request-header side of the same agreement problem.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The region surfaces above are
derived from one egress lookup so they cannot drift apart, which is the whole reason a
geotargeted session holds together.*
