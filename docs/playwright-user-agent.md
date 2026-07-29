---
title: "Why you should not set the user agent in Playwright"
description: "Rotating the user agent does not rotate the browser. Everything else keeps answering honestly, so the string you changed now contradicts the fonts, the GPU, the codecs and the TLS handshake."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 5
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
      "name": "Why you should not set the user agent in Playwright"
    }
  ]
}
</script>

# Why you should not set the user agent in Playwright

The standard advice is to set a realistic user agent and rotate it. Search for it and
every result agrees.

It is close to the worst thing you can do, and the reason is one sentence:

> **Rotating the user agent does not rotate the browser.**

Everything else keeps answering honestly. So instead of one browser that is unremarkable,
you now have a browser whose stated identity contradicts a dozen values it did not think
to change, and contradiction is what detection is built on.

This page covers what the string has to agree with, why rotation makes things worse
rather than better, the Client Hints trap in Chromium, the one case where setting it is
correct, and what to do instead.

## What the user agent has to agree with

Change the string to claim Chrome on Windows, and here is what did not change:

- **`navigator.platform`, `navigator.oscpu`, `navigator.appVersion`**, which are separate
  properties with separate values.
- **The font set**, which still belongs to the machine you are on.
  [Which is a comparison, not a count](headless-fonts-differ.md).
- **The WebGL renderer string**, which on Windows has a specific ANGLE shape that a Linux
  build does not produce. [The shape matters](webgl-renderer-strings.md).
- **The WebGL numeric parameters**, which come from the platform's graphics stack.
  [And are identical across GPUs on Windows](webgl-parameters-are-identical.md).
- **The codec support pattern**, which differs by build and platform.
  [Three surfaces in one API](codec-fingerprinting.md).
- **The CSS system colour palette**, which resolves through the host's own theme.
  [Readable with one call](css-media-query-fingerprinting.md).
- **The audio profile**, the speech voice list, the screen relationships.
- **The TLS handshake**, decided by the network stack before any of this.
  [A Firefox handshake under a Chrome user agent is decisive](ja3-ja4-tls-fingerprint.md).

Every one of those is cheaper to check than the user agent is to fake. A browser claiming
Chrome while producing a Firefox handshake and a Linux font set has not disguised itself.
It has volunteered that it is lying.

## Why rotation makes it worse, not better

Rotation is presented as making your traffic look like many users. Think about what it
actually produces.

Session one claims Chrome 141 on Windows. Session two claims Firefox 140 on macOS. Both
have the identical canvas hash, the identical WebGL renderer, the identical font list and
the identical audio values, because those come from the machine and the machine did not
change.

You have not created two users. You have created one machine that changes its mind about
which browser it runs, which is a stronger signal than either session alone. Worse, the
constant values now **link** the sessions together, so rotation has produced a tracking
identifier and a contradiction at the same time.

The instinct behind rotation is right and applied at the wrong layer. What should vary
between identities is the whole machine, coherently:

```python
with InvisiblePlaywright(seed=42) as browser: ...    # one machine
with InvisiblePlaywright(seed=99) as browser: ...    # a different machine, coherently
```

Two identities, each self-consistent, differing in everything rather than in one string.

## The Client Hints trap

Specific to Chromium, and it catches people who did everything else carefully.

Modern Chromium sends the `Sec-CH-UA` family of headers, which carry the brand and
version independently of the classic user agent string. Setting `userAgent` in your
automation tool does not necessarily rewrite them.

The result is a request whose user agent says one version and whose Client Hints say
another, in the same header set, arriving at the same server. That comparison needs no
JavaScript and no fingerprinting library. It is two values that do not match.

Firefox does not send those headers at all, which is its own consistency requirement: a
browser claiming Chrome and sending no Client Hints is making a claim its own request
contradicts.

## The one case where you should set it

If your build genuinely emits something like `HeadlessChrome` in its default user agent,
that string is wrong and fixing it is correct. It is a real leak and it costs nothing to
close.

But notice what the correct fix is. It is not to invent a string. It is to stop running a
browser that announces headlessness, and to derive the user agent from the engine's own
real version so it cannot drift out of date.

That last part matters more than it sounds. A user agent naming a version that does not
exist, or one released years ago, or a patch version nobody shipped, is its own tell.
This project derives the string from the engine's actual upstream version for exactly
that reason: a hand-written user agent goes stale without anyone noticing.

## What to do instead

- **Do not set it.** Let it come from the engine, so it matches the engine.
- **Vary identities, not strings.** One seed is one machine. Change the seed to change
  who you are.
- **Check the other properties agree** rather than assuming: `navigator.platform`,
  `oscpu`, `languages`, the fonts, the renderer.
- **If a framework offers a user agent setting, resist it.** Several put it right beside
  the options you actually want, which is how it gets set by accident.
- **Verify before a long run.** Read the string back from inside the page and from the
  request headers, and confirm they are the same string and name the browser you are
  really running.

## Conclusion

The user agent is the easiest value to change and the least useful one to change, because
it is the only one a page can compare against everything else for free.

Rotating it diversifies nothing. It varies one string on a machine that is otherwise
identical between runs, which produces contradictions between sessions and a stable
fingerprint linking them. If you want to look like different people, change the machine.

## Short answers to the questions that lead here

**Should I rotate the user agent in Playwright?** No. Rotate identities. The string alone
varies nothing a detector cares about.

**Does a random user agent help avoid blocks?** It removes the most obvious wrong value
if your build has one, and creates contradictions with everything that did not change.

**What is the best user agent for scraping?** The one belonging to the browser you are
actually running.

**Why am I still detected after setting a realistic user agent?** Because the fonts, the
GPU, the codecs, the audio and the TLS handshake still describe your real setup.

**What about Client Hints?** In Chromium they carry brand and version separately, and
setting the classic string does not necessarily update them. Mismatched headers are a
free check.

**Does `navigator.userAgent` match the HTTP header?** It should. Verify both, because
tools sometimes change one and not the other.

**See also:** [navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
the same argument applied to the other famous value, and
[the checklist for being detected on one site](playwright-detected-as-bot.md), where
values you overrode yourself are step one because they are the most common cause.

## Sources

- Playwright's `userAgent` context option, and the browser properties listed above.
- The Client Hints headers sent by Chromium and not by Firefox.
- This project derives the user agent from the engine's real upstream version rather than
  writing one, for the staleness reason given above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Every page in this set that says "do not set the user
agent" says it for the reason on this page.*
