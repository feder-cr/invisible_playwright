---
title: "Playwright proxy per context: what it does not isolate"
description: "One browser with a proxy per context gives every session a different IP and the same canvas, fonts, GPU, audio and timezone. That is one machine appearing from several countries, not several users."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 3
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
      "name": "Network, Proxy and WebRTC",
      "item": "https://feder-cr.github.io/invisible_playwright/guides-network-proxy-webrtc.html"
    },
    {
      "@type": "ListItem",
      "position": 4,
      "name": "Playwright proxy per context: what it does not isolate"
    }
  ]
}
</script>

# Playwright proxy per context: what it does not isolate

The recommended pattern for rotating IPs in Playwright is one browser with a proxy per
`BrowserContext`. It is in every guide, it is efficient, and for many jobs it is right.

It also has a consequence nobody states: **a context isolates storage, not hardware.**
Every context in that browser reports the same canvas hash, the same GPU, the same fonts,
the same audio profile and the same screen, because they all come from one browser
process on one machine.

So five contexts on five proxies are not five users. They are one machine appearing from
five countries at once, and the constant fingerprint links them to each other.

This page is what a context actually separates, what it does not, the timezone problem
the pattern creates, when it is nonetheless the right choice, and what to do when it is
not.

## What a BrowserContext genuinely isolates

Real isolation, and it is useful:

- Cookies and their jars.
- Local storage, session storage, IndexedDB.
- Cache and service workers.
- Permissions and their grants.
- The proxy, when set per context.

Two contexts do not share a login, and that is exactly what the feature is for. If your
job is "log into ten accounts without them seeing each other's cookies", contexts are the
correct tool and this page is not a warning about your use case.

## What it does not isolate

Everything the machine reports about itself, because there is one machine:

- **The canvas hash**, identical across contexts.
- **The WebGL renderer and its numeric parameters.**
  [Which do not vary by GPU anyway](webgl-parameters-are-identical.md).
- **The font set**, which belongs to the host.
  [And is a comparison, not a count](headless-fonts-differ.md).
- **The audio profile**, the speech voice list, the codec support.
- **The screen dimensions** and their relationships.
- **`hardwareConcurrency`, `deviceMemory`, the storage quota.**
- **The TLS handshake**, which is per connection and identical in shape.
  [And decided below all of this](ja3-ja4-tls-fingerprint.md).

Any single one of those, collected on two of your contexts, says they are the same
machine. Collected by two different sites that share data, it says the same thing across
sites.

The pattern therefore produces the worst of both: **you paid for several IPs and got one
identity**, and the identity now has a suspicious property, which is that it is in
several countries simultaneously.

## The timezone problem the pattern creates

This one is specific and it bites the careful.

A timezone resolved from the exit address has to be resolved **per exit**. If your tool
resolves it at browser launch, it resolved it for the launch-time exit, and every context
afterwards carries that zone regardless of where its own proxy leaves from.

Context one leaves from Germany with a German timezone. Context two leaves from Japan
with a German timezone. The second one is the mismatch that
[the timezone page](timezone-proxy-mismatch.md) is entirely about, and the recommended
proxy pattern created it.

The same applies to the locale, and to the public address reported through WebRTC, which
[has its own failure mode behind a proxy](webrtc-leak-proxy.md).

If you use per-context proxies, you have to set `timezone_id` and `locale` per context to
match each proxy's country, by hand, and keep that mapping correct. Nothing does it for
you, because the tool that resolves it does so once.

## When per-context proxies are still right

Be fair to the pattern. It is the correct choice when:

- **The sites do not fingerprint**, which covers a great deal of ordinary work.
- **You need many logins, not many identities.** Ten accounts on one machine is a normal
  thing for a real person to have, and cookie isolation is the actual requirement.
- **You are optimising cost.** One browser process is far cheaper than ten, and if
  nothing is comparing hardware across sessions the saving is free.
- **The IP is the only thing being rate limited**, which is common.

The mistake is not using the pattern. It is using it while believing it produces separate
users.

## What to do when you need separate identities

One identity is one machine, which means one browser process with its own seeded
fingerprint:

```python
# separate identities: separate processes, separate seeds, separate proxies
with InvisiblePlaywright(seed=42, proxy=proxy_de) as browser: ...
with InvisiblePlaywright(seed=99, proxy=proxy_jp) as browser: ...
```

Each launch resolves its own timezone and locale from its own exit, and each carries a
different canvas, GPU, font set and audio profile because those come from the seed.

It costs more. Several browser processes use more memory than several contexts in one,
and that is the real trade. The question to ask is which resource you are short of: if it
is memory, contexts; if it is identities, processes.

### A middle path

Contexts inside one process, one proxy for the whole process, and a separate process per
country. You get cookie isolation cheaply, and the sessions that share a fingerprint also
share an address, so nothing contradicts anything.

## Conclusion

A context is a storage boundary. It was designed to keep cookies apart, it does that
well, and it was never designed to make one machine look like several.

If your target only counts requests per IP, rotate proxies per context and enjoy the
savings. If your target compares sessions, rotate processes, because the fingerprint is
what it is comparing and the fingerprint does not change between contexts.

And whichever you choose, make the timezone and locale follow the exit each session
actually leaves from, since that is the mismatch this pattern most reliably creates.

## Short answers to the questions that lead here

**Can I set a different proxy per context in Playwright?** Yes, that is the standard way
to rotate. The proxy is fixed when the context is created and cannot be swapped
afterwards.

**Do separate contexts have separate fingerprints?** No. They share the browser process,
so canvas, WebGL, fonts, audio and screen are identical.

**Is one browser with many contexts detectable?** Not by itself. It becomes detectable
when the contexts claim to be in different places while reporting the same hardware.

**How do I rotate IPs properly then?** Per context if you only need a different address.
Per process if you need a different identity.

**Why is my timezone wrong on some contexts?** Because it was resolved once, at launch,
for the launch-time exit. Set it per context to match that context's proxy country.

**Does a new context reset the fingerprint?** No. Only a new browser with a different
seed does.

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md),
[why you should not set the user agent](playwright-user-agent.md), which is the same
mistake on a different value, and
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).

## Sources

- Playwright's proxy option at browser and context level, and its behaviour of fixing the
  proxy at creation time.
- The surfaces listed above are each documented on their own page, linked in place.
- This project resolves the timezone, locale and WebRTC exit address from one lookup per
  launch, which is why one identity is one launch here.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The per-context pattern is fine and widely correct;
this page exists because it is recommended without its consequence attached.*
