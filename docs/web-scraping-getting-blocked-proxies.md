---
title: "Web scraping keeps getting blocked with good proxies"
description: "A residential proxy fixes the IP, not the machine behind it. Scraping gets blocked when the proxy's country disagrees with the browser's timezone or language."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 8
---


# Web scraping keeps getting blocked with good proxies

Web scraping keeps getting blocked even with good proxies because a proxy only
changes the request's IP address, not what the browser reports once a page loads.
When the proxy's exit country disagrees with the browser's timezone, language, and
number formatting, that contradiction is a signal a site can check directly, no matter
how clean or expensive the IP is. Fixing it means aligning the browser side of the
session with the proxy's country, not upgrading the proxy again.

The standard advice for a scraper that keeps getting blocked is some combination of:
better proxies, slower requests, randomized delays, rotate the user agent. All
reasonable, all worth doing, and all aimed at the same half of the problem - how the
requests are paced and where they come from. None of it touches the other half, which
is why good proxies and careful pacing still aren't enough on a site that's actually
paying attention.

## The half nobody's advice covers

A proxy changes one fact about a request: which network it left from. It does not
touch anything the browser itself reports once a page loads - the timezone,
the language list, the fonts, the GPU, the audio stack, the screen. Those come from
the machine running the browser, and a proxy has no way to reach into any of them.

That's fine as long as nothing cross-checks the two halves against each other. A
growing share of sites do exactly that: take the country the request's IP maps to,
and compare it against what the browser side of the same session claims about itself.
A clean, fast, expensive residential IP in Germany, sitting behind a browser that
reports `America/New_York` as its timezone and `en-US` as its language, is not a
network-quality problem. It's an internal contradiction, and it's checkable without
any of the properties involved being individually rare or fake.

## Why this survives every proxy upgrade

Proxy quality was never the variable this mismatch depends on, which is why upgrading
to a better proxy makes no difference on its own. A
[residential IP](residential-datacenter-mobile-proxies-explained.md) with a spotless
reputation still carries a country, and if the browser side of the session isn't adjusted
to match that country - timezone, language, and everything downstream of them - the
mismatch is exactly as visible as it was on a flagged datacenter IP. That's the part
that makes "just get better proxies" feel like it stopped working, even when the
proxies genuinely improved.

Concretely, the fields that have to agree with the proxy's country, not just with each
other:

- `Intl.DateTimeFormat().resolvedOptions().timeZone` and the timezone offset a `Date`
  object reports
- `navigator.language` / `navigator.languages`, and [the `Accept-Language` header the
  requests actually carry](accept-language-navigator-languages.md)
- Number and date formatting (`Intl.NumberFormat`, `toLocaleString`) - a separate
  mechanism from the two above, and one that can silently stay wrong while the others
  get fixed
- What WebRTC reports independently, if it's enabled at all, since it can
  [reveal a real address through a proxy that never touches HTTP](webrtc-leak-proxy.md)

[The full breakdown of which fields these are, and the combinations that get caught
most often, is worked through here](timezone-proxy-mismatch.md).

## Why this applies regardless of which tool is doing the scraping

This mismatch is not specific to any one scraping framework: a proxy plus a
mismatched timezone is the same contradiction whether the request comes from a full
browser under Playwright or Selenium, or from a plain HTTP client with no browser at
all - the only difference is which of the fields above the tool is even capable of
setting. A tool driving a real browser can fix all of them. A bare HTTP client can fix the
`Accept-Language` header and nothing else, because the rest only exist once a browser
is actually rendering a page.

That's also why upgrading the scraping tool without touching this specific alignment
doesn't fix it. The tool decides what's possible to set. It doesn't set anything on
its own.

## What to actually check

If proxy quality has stopped being the bottleneck, the honest next question is
whether the browser side of the session was ever told which country it's supposed to
be in:

1. [Resolve the proxy's exit country from its IP](offline-geoip-timezone-proxy.md),
   independent of anything the scraper itself reports.
2. Read the timezone, language, and number-formatting fields the browser actually
   produces for that session.
3. Confirm they describe the same country the IP does - not just that they're
   internally consistent with each other, which a script can get right while still
   disagreeing with the network.

A mismatch found this way is a configuration gap, not a proxy-quality problem, and no
amount of rotating to a better provider closes it.

## Short answers to the questions that lead here

**I upgraded to residential proxies and I'm still getting blocked. Why?** A proxy
only changes where the request's IP maps to. If the browser's timezone, language, or
number formatting weren't updated to match that country, the mismatch is exactly as
visible as it was before, regardless of the proxy's quality.

**Does rotating user agents fix this?** No, and it can make it worse - a user agent
claiming one platform and a timezone/language pair describing an unrelated country is
a second, independent contradiction on top of the first.

**Is this only a problem for browser-based scrapers?** The fields that can be wrong
scale with how much of a real browser is involved. A plain HTTP client has fewer of
them to get wrong; a full browser session has all of them, and needs all of them set
correctly.

**Do I need to match the timezone exactly, or just the country?** Match it to what a
real device in that location would report, which is usually one specific IANA zone
per country or region, not an approximation.

**See also:** [Playwright timezone does not match the proxy IP](timezone-proxy-mismatch.md),
for the full field-by-field breakdown this page summarizes, and
[Playwright proxy per context: what it does not isolate](playwright-proxy-per-context.md),
for what a proxy changes and what it leaves completely alone.

## Sources

- This project's own testing methodology for proxy/timezone/locale consistency,
  cross-referenced against real IP-to-country resolution.
- MDN Web Docs, [`Intl.DateTimeFormat.prototype.resolvedOptions()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat/resolvedOptions),
  on the `timeZone` a browser reports as an IANA zone name.
- MDN Web Docs, [`Navigator.languages`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/languages),
  on the browser's language list and its link to the `Accept-Language` header.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, where the timezone and locale are derived from the
same proxy resolution the network connection already uses, rather than being a
separate setting someone has to remember.*
