---
title: "Does clearing cookies stop fingerprint tracking?"
description: "No. Cookies are stateful and clearable, but fingerprinting is stateless and rebuilds the same identity from canvas, WebGL, fonts, audio and TLS every clear."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 27
---


# Does clearing cookies stop fingerprint tracking?

Short version: no. Clearing cookies deletes something the site handed you and asked
you to hold. Fingerprinting never handed you anything to delete. It reads what your
browser is, every time you show up, and rebuilds the same identifier from scratch.
That is not an accident of the design, it is the whole point of it: fingerprinting
exists precisely because cookies can be cleared and it wanted an identifier that
could not.

This page explains the difference between the two, why the second one ignores the
first, what changes the identity, and where the honest limits of the fingerprint
layer are.

## What a cookie is, and what a fingerprint is

A cookie is state. The site writes a value into your browser, your browser stores it,
and it sends it back on the next request. Because it is stored, it can be deleted, and
when you clear it the site has to start over. This is a real, working privacy control
for the thing it controls.

A fingerprint is not stored anywhere on your machine for you to clear. It is computed
on the spot from properties your browser exposes: how it draws a
[canvas](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL),
which GPU and renderer string it reports through
[WebGL](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info),
which fonts it can load, how its
[audio stack](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)
processes a signal, and, below the page entirely, how its TLS handshake looks. The
site hashes those together into an identifier. There is no file to remove because none
of it was ever written down on your side.

So the two answer different questions. A cookie asks "did I give you a token before?"
A fingerprint asks "are you the same machine as before?" Clearing the first has no
effect on the second.

## Why fingerprinting survives the clear

Stateful versus stateless is the entire story.

Clear your cookies, clear local storage, open a private window, and then reload a
fingerprinting page. The canvas still draws the same pixels, so it hashes to the same
value. WebGL still reports the same renderer. The font probe still finds the same
list. The audio path still returns the same numbers. Combine those and you get the
same identifier you had five minutes ago, because none of the inputs changed. Canvas,
WebGL, fonts and audio are four of the roughly forty-one components
[a FingerprintJS visitor ID hashes together](fingerprintjs-visitor-id.md), and a hash
of unchanged inputs is unchanged output.

This is why "clear cookies between sessions" does not rotate you into a new identity. It
rotates the one thing that was trivial to rotate and leaves untouched every signal
that was hard to. A detector that leans on the fingerprint rather than the cookie is
built specifically to not care what you did to your storage.

The corollary matters for automation: if you run a loop that clears cookies between
iterations expecting to look like a fresh visitor each time, every iteration presents
the identical fingerprint. You have not created many visitors, you have created one
visitor who keeps deleting their cookies, which is itself a pattern.

## What actually changes the identity

If the identifier is a hash of canvas, WebGL, fonts, audio and TLS, then the only way
to present a different identity is to change those underlying values so they hash
differently, and to change them together so they still agree with one another. A canvas
that says one machine next to a font list that says another is not a new identity, it
is a contradiction, and [contradictions are what the tampering-focused suites look for](creepjs-explained.md).

That is a much harder thing to do than emptying a cookie jar, which is the reason a
cookie clear can never do it. The cleared browser still renders canvas the same way and
still reports the same fonts. Changing those values in a self-consistent way is what a
fingerprint-management layer is for, and it is what invisible_playwright does per seed.

## Doing it with invisible_playwright

The product gives each seed a distinct, internally self-consistent fingerprint: a
different GPU, canvas hash, audio context, font set and screen, all derived together
so they agree with each other. Switching seeds is what a cookie clear is trying and
failing to be.

The launch is the standard two-line change, and the object you get back is a real
Playwright [`Browser`](https://playwright.dev/python/docs/api/class-browser) with every
method intact:

```python
from invisible_playwright import InvisiblePlaywright

# Seed A: one self-consistent machine
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # canvas, WebGL, fonts, audio all derived from seed 42

# Seed B: a genuinely different machine, not the same one with cookies wiped
with InvisiblePlaywright(seed=1337) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # different canvas hash, different renderer, different fonts
```

Two different seeds produce two different fingerprints. The same seed produces the
same one every run, which is the property that makes a failing run reproducible
instead of a guess:

```python
# Same seed, same identity, every run - the opposite of "clear and hope"
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # identical canvas hash and renderer to any other seed=42 run
```

If you want an identity to persist across separate sessions on purpose, that is a
profile decision rather than a cookie one: see [persistent profiles](persistent-profiles.md)
for keeping the same seed and storage across runs, and [cross-platform canvas and WebGL consistency](canvas-webgl-cross-platform-consistency.md)
for why the drawn pixels have to match the reported hardware on every OS you deploy to.

## The honest caveat: this is the fingerprint layer only

A distinct, self-consistent fingerprint is what makes invisible_playwright read as a
genuine Firefox driven by a real person, and it is why it passes most fingerprint, TLS
and driver-layer checks. It is not a claim that everything about you rotates when the
seed does.

Two things are tracked independently of both cookies and fingerprint, and the reader
supplies the answers to them:

- **Your IP and its reputation.** A brand-new fingerprint arriving from the same
  datacenter address as the last hundred requests is still that address. The exit is
  yours to choose, and a clean one matters as much as the browser does.
- **Account and behaviour state.** Per-account quotas, rate limits, session age and the
  rhythm of what you do are counted against the account and the address, not the
  fingerprint. Change the seed all you like, a burst of identical requests at machine
  speed still reads as a burst. Human pacing is yours to supply.

So the accurate statement is: changing the fingerprint changes the fingerprint. It
helps a lot with the layer that reconstructs identity from canvas and TLS, and it does
nothing on its own for the layer that watches your address and your behaviour. Both
have to be right, and they are separate jobs.

## Conclusion

Clearing cookies removes the identifier the site stored on your side. Fingerprinting
was designed for exactly the world where people clear cookies, so it stores nothing on
your side and recomputes the identity from canvas, WebGL, fonts, audio and TLS every
time. The only way to look like a different visitor is to change those values together,
which a cookie clear cannot do and a per-seed fingerprint can. Just remember that this
is the fingerprint layer alone: your IP and your account behaviour are tracked on their
own, and they need their own clean inputs.

## Short answers to the questions that lead here

**Does clearing cookies stop fingerprinting?** No. Cookies are stored and deletable,
the fingerprint is recomputed from your browser's properties every visit, so a clear
leaves it unchanged.

**Does incognito or a private window give me a new fingerprint?** No. Private mode
clears storage, not the canvas, WebGL, font, audio and TLS values the fingerprint is
built from. Same machine, same fingerprint.

**Then how do I actually change my fingerprint?** By changing the underlying values in
a self-consistent way. In invisible_playwright that is a different seed, which redraws
the GPU, canvas, fonts, audio and screen together so they still agree.

**If I clear cookies in a loop, do I look like many visitors?** No. Every iteration
presents the same fingerprint, so you look like one visitor repeatedly clearing
cookies, which is its own pattern.

**Does a new fingerprint fix a blocked IP?** No. The address and its reputation are
tracked independently of both cookies and fingerprint. A clean exit is a separate
requirement you supply yourself.

**Will a per-seed fingerprint make me undetectable?** No, and be wary of anything that
says it will. It makes the fingerprint, TLS and driver layer read as a real Firefox,
which is most checks but not all of them. Behaviour, pacing and IP are still on you.

## Sources

- This project's fingerprint generator, which derives canvas, WebGL, font, audio and
  screen values together from a single seed so a new identity is internally consistent.
- MDN Web Docs, [`HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL),
  [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info),
  and the [Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API),
  retrieved 2026-08-28, for the canvas, WebGL and audio surfaces referenced above.
- FingerprintJS's own open-source library, [`fingerprintjs/fingerprintjs`](https://github.com/fingerprintjs/fingerprintjs)
  on GitHub, retrieved 2026-08-28, for the visitor ID hash mentioned above.
- Playwright documentation, [`Browser`](https://playwright.dev/python/docs/api/class-browser),
  retrieved 2026-08-28, for the object returned by the launch call shown above.
- The public detection suites named across this set, read for which components feed
  the identifier and which of them a storage clear leaves untouched.
- The release gates that separate the fingerprint layer from IP and behaviour, so a
  clean fingerprint is never mistaken for a clean session.

**See also:** [the FingerprintJS visitor ID](fingerprintjs-visitor-id.md) for what the
hash is actually made of, [persistent profiles](persistent-profiles.md) for keeping one
identity on purpose, and [the JA3/JA4 TLS fingerprint](ja3-ja4-tls-fingerprint.md) for
the layer that no amount of cookie clearing or JavaScript ever reaches.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Every session gets a
distinct, self-consistent fingerprint from its seed, which is the thing a cookie clear
was always trying and failing to be.*
