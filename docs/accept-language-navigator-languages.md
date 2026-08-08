---
title: "Accept-Language header vs navigator.languages"
description: "Accept-Language and navigator.languages must agree: one Firefox pref feeds both, so an injection-only spoof that moves one and not the other is a clear tell."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 21
---


# Accept-Language header vs navigator.languages

A real browser reports its preferred languages in two completely separate places. It
sends an `Accept-Language` header on every HTTP request, decided by the network stack
before any page runs. And it exposes `navigator.language` and `navigator.languages` to
JavaScript, read from the page after it loads. Two surfaces, two code paths, and a
detector that reads both expects them to tell the same story.

They do, in a stock browser, because both come from one setting. The interesting failure
is what happens when a stealth layer touches only one of them.

## One preference feeds both surfaces

In Firefox the language preference lives in a single about:config string,
`intl.accept_languages`, with a value like `en-US,en`. That one string is the source for
two different consumers:

- The **network stack** formats it into the `Accept-Language` request header, adding the
  quality values (`en-US,en;q=0.5`) a browser normally attaches.
- The **JavaScript engine** parses the same string into the `navigator.languages` array
  and its first element into `navigator.language`.

Because both read the same preference, they cannot disagree. Change the preference and
the header changes and the array changes, together, in the same load. There is no way,
inside a genuine browser, to make the header say one thing and the property say another,
because there is only one value and both surfaces quote it.

That single-source property is worth stating plainly, because it is exactly the invariant
a naive spoof breaks.

## Why an injection-only spoof splits the two

A large class of stealth tooling works by running JavaScript in the page before the
site's own code: it redefines `navigator.languages` with `Object.defineProperty`, or it
patches the getter. That reaches exactly one of the two surfaces. It can make
`navigator.languages` return `["en-US", "en"]` to any script that asks.

It never touches the header. The `Accept-Language` header was formatted and sent by the
network layer, which is compiled C++ and does not read the JavaScript you injected. So
the request that fetched the page carried whatever the underlying browser's real
preference was, while the property in the page now claims something else.

The split lines up field by field like this:

| Surface | Real browser | Injection-only spoof |
|---|---|---|
| `Accept-Language` header | formatted from the language preference | unchanged real value (sent by the network layer) |
| `navigator.languages` | parsed from the same preference | overridden in the page |
| Do the two agree? | yes, one shared source | no, they diverge |

A detector does not need to be clever to catch this. It reads the `Accept-Language` it
received on the request, reads `navigator.languages` from a script it served, and
compares the two strings. When the header says one language set and the property says
another, that contradiction is the tell. It is the same shape of mistake as
[running two spoofers that answer the same question differently](playwright-detected-as-bot.md):
one value moves, its twin does not, and the gap between them is louder than either value
alone.

The same reasoning extends to the request headers a browser sends about itself, which is
why [the Sec-Fetch and client-hint headers](client-hints-sec-fetch.md) have to line up
with the rest of the identity for the same structural reason: a header the page cannot
see is a header an injection cannot fix.

## How this project keeps header and property in agreement

Because the fingerprint is applied at the browser level rather than in the page, the
language is set through the same native preference a real profile uses, so both surfaces
read from it. There is nothing to inject into the page and nothing to keep in sync: the
header and the array are the same value formatted twice, by the browser, the way they
always are.

The code that drives it is stock Playwright. Switching a normal Playwright script over is
the launch line and nothing else:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    langs = page.evaluate("navigator.languages")
    print("navigator.languages =", langs)
```

The `browser` returned is a real Playwright `Browser`, so every method you already use
works unchanged. The seed makes the whole identity reproducible: the same seed yields the
same language set, the same GPU, the same screen, run after run, which is what lets a
failure be replayed instead of guessed at.

## Prove they match, do not assume it

The point of this page is a check you can run rather than a claim to trust. Capture the
`Accept-Language` header the browser actually sent, read `navigator.languages` from the
loaded page, and compare the two directly. Use any endpoint that echoes request headers
back; the snippet below reads the header off the outgoing request instead, so it needs no
particular server:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    sent_header = {}
    def capture(request):
        if "accept-language" in request.headers:
            sent_header["value"] = request.headers["accept-language"]
    page.on("request", capture)

    page.goto("https://example.com")

    header = sent_header.get("value", "")
    array = page.evaluate("navigator.languages")

    print("Accept-Language header :", header)
    print("navigator.languages    :", array)

    # The header's primary tag and the array's first entry must be the same language.
    header_primary = header.split(",")[0].split(";")[0].strip()
    assert header_primary == array[0], (header_primary, array[0])
    print("header and property agree")
```

Follow the method from
[how to test whether your browser is detected](how-to-test-bot-detection.md): assert the
values are present and agree, rather than asserting nothing looks wrong. A property that
comes back empty, or a header your capture never saw, is a failure and not a pass. Run it
against a stock browser on the same machine too, and the header and array should match
there in exactly the same way.

## Match the language to the exit, the same way you match the timezone

Header and property agreeing with each other is necessary but not sufficient. Both of
them also have to agree with where the request appears to come from.

A session whose exit address resolves to one country while the browser advertises the
language of another is a contradiction of the same family as
[a timezone that does not match the proxy](timezone-proxy-mismatch.md). The two values
are individually plausible and jointly wrong, which is precisely what field-by-field
comparison catches and a single-value check does not. If you
[rotate through several exits](how-to-rotate-proxies-playwright.md), the language set is
one more field that should track the exit rather than staying pinned to whatever the host
machine happens to prefer. Pass the proxy at launch and let the identity be built to fit
it, rather than setting the language by hand and hoping it lines up:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print("languages =", page.evaluate("navigator.languages"))
```

## Conclusion

The `Accept-Language` header and `navigator.languages` are read from one browser
preference, so in a genuine browser they cannot diverge. That is the whole reason the
pair is worth checking: a stealth layer that lives in the page can move the property and
never the header, and the gap it opens is a cleaner signal than any single value. Set the
language where the browser itself reads it, keep it consistent with the exit, and verify
by comparison rather than assumption.

## Short answers to the questions that lead here

**Do Accept-Language and navigator.languages have to match?** Yes. In a real browser both
are formatted from one preference, so a mismatch is a contradiction that only a spoof
produces.

**Why does setting navigator.languages in JavaScript not work?** Because it reaches only
the page-facing property. The `Accept-Language` header was already sent by the network
layer, which does not read your injected script, so the header still carries the old
value.

**How do I set the browser language in Playwright?** Let the fingerprint layer set the
native preference so both surfaces read from it, rather than overriding the property in
the page. Here the language comes with the seeded identity.

**Should the language match the proxy country?** It should be consistent with the exit,
the same way the timezone has to be. A language from one continent on an exit in another
is a mismatch a detector can pair up.

**How do I check the two agree?** Capture the `Accept-Language` header the browser sent,
read `navigator.languages` from the loaded page, and compare the primary language tag of
each. Do it against a stock browser as well.

**Does the q-value in the header matter?** It should be present. A real browser attaches
quality values like `;q=0.5`; a header that is a bare comma list with none is itself
slightly unusual.

## Sources

- Firefox's `intl.accept_languages` preference, which is the single value both the request
  header and the `navigator.languages` array are formatted from.
- [MDN: `Navigator.languages`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/languages)
  and [MDN: `navigator.language`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/language),
  the page-facing properties an injection redefines.
- [MDN: the `Accept-Language` request header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Accept-Language),
  including the quality (`q`) values a browser attaches, sent by the network stack before any page script runs.
- This project's own realness gates, which compare request headers against in-page
  properties field by field rather than reading either in isolation.

**See also:** [when the timezone does not match the proxy](timezone-proxy-mismatch.md),
[when the TLS fingerprint and the user agent disagree](tls-fingerprint-user-agent-mismatch.md),
[the checklist for being detected on one site](playwright-detected-as-bot.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The header and the array
come from one preference, which is exactly what an injection-only spoof gets wrong.*
