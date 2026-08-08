---
title: "prefers-reduced-motion and other OS-setting tells"
description: "CSS media queries leak OS accessibility settings via prefers-reduced-motion, prefers-contrast, forced-colors, and prefers-reduced-transparency."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 33
---


# prefers-reduced-motion and other OS-setting tells

Everyone spends their fingerprinting effort on the JavaScript surface: `navigator`,
canvas, WebGL, audio. Meanwhile a stylesheet can read a cluster of your operating
system's accessibility settings, with no script running at all, and report the answer
back to a server before your first injected line executes.

[`prefers-reduced-motion`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion) is the best known of the group, but it travels with three
siblings that behave the same way: [`prefers-contrast`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-contrast), [`forced-colors`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/forced-colors), and
[`prefers-reduced-transparency`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-transparency). All four are pure CSS media features that mirror an OS
accessibility setting, and all four are exfiltrable with a background-image branch and
no JavaScript. This page covers what they expose, why the default value is the only
safe one, how to check yours, and the one honest limit of fixing them.

## The accessibility-media-feature cluster

These are not display-hardware features like `resolution` or `color-gamut`. They are
user-preference features: each one reflects a toggle in the operating system's
accessibility or personalisation settings, and the browser reports whatever the OS
tells it.

| Media feature | OS setting it mirrors | Default on a fresh Windows install |
|---|---|---|
| `prefers-reduced-motion` | Show animations / reduce motion | `no-preference` |
| `prefers-contrast` | High-contrast preference | `no-preference` |
| `forced-colors` | High-contrast mode active | `none` |
| `prefers-reduced-transparency` | Transparency effects off | `no-preference` |

The pattern to notice: the overwhelming majority of real desktop users never touch any
of these. They ship at their defaults and stay there. That is what makes a non-default
value informative. A user who has turned on reduce-motion is rare, so reporting
`reduce` narrows you to a small slice of the population before anything else is
measured. The trap is not reporting an "unusual" value in the abstract. It is reporting
a value that no untouched Windows install would report, which reads as a machine that
was configured by hand.

## Why no page-level layer can touch them

The mechanism is the same one that makes all of
[CSS media-query fingerprinting](css-media-query-fingerprinting.md) reach below the
scripting layer. A stylesheet asks the question and encodes the answer as a network
request:

```css
@media (prefers-reduced-motion: reduce) {
  body { background-image: url("/p?rm=reduce"); }
}
@media (prefers-reduced-motion: no-preference) {
  body { background-image: url("/p?rm=none"); }
}
@media (forced-colors: active) {
  body { border-image: url("/p?fc=active"); }
}
```

Only the branch that matches loads its image, so the server learns the setting from
which URL is requested. No `matchMedia` call, no property to override, no object to
inspect. There is no JavaScript function for a page-level stealth patch to intercept,
which is precisely why a page-level layer cannot fix this at all. The value has to be
correct at the source, in the browser's own media-feature evaluation, or it is wrong on
the first paint.

That is also why the values have to be baked into the engine rather than injected. By
the time any script you add could run, the stylesheet has already fired its request.

## What invisible_playwright reports here

The short version: this project ships these four features at their stock Windows
defaults, so a fresh session reads exactly like an untouched Windows install and none
of them narrows you to a configured minority. The launch is the ordinary two lines, and
the browser you get is a real Playwright `Browser`:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    values = page.evaluate("""() => ({
        motion:  matchMedia('(prefers-reduced-motion: reduce)').matches,
        contrast: matchMedia('(prefers-contrast: more)').matches,
        forced:  matchMedia('(forced-colors: active)').matches,
        transp:  matchMedia('(prefers-reduced-transparency: reduce)').matches,
    })""")
    print(values)   # {'motion': False, 'contrast': False, 'forced': False, 'transp': False}
```

All four come back `False`, which is the reading a default machine gives. The important
part is not any single value. It is that this cluster agrees with the rest of the
identity: it reports the same "plain, untouched Windows desktop" story that the fonts,
the system-colour palette and `prefers-color-scheme` report. A machine that claims a
default light theme and then reports high-contrast forced colours has answered the same
question two ways, and a contradiction is worth more to a detector than any single odd
value.

The `matches` above are stable for the seed. Pass the same seed on the next run and the
cluster reads the same, which is the property that lets you replay a session rather than
guess at it. If you need a specific field held constant while the rest stays
seed-derived, that is what [pinning a fingerprint field](pinning.md) is for.

## The consistency rule: agree with prefers-color-scheme

`prefers-color-scheme` is the fifth member of this family and the one people already
know, because it is close to a coin flip across real users rather than a near-constant.
It is also the anchor the other four have to agree with.

The rule is simple to state and easy to get wrong: the accessibility cluster and the
colour-scheme preference and the system-colour palette all describe one machine, and
they must tell one story. A dark-theme persona that still reports a light system palette
is a contradiction. A machine reporting `forced-colors: active` while claiming an
ordinary default theme is another. When these disagree, the disagreement is the signal,
and it survives every JavaScript patch because none of it lives in JavaScript. Read the
[CSS system-colour palette section](css-media-query-fingerprinting.md) for how the OS
theme colours cross-check against the same preference, and
[the color-gamut and HDR media queries](color-gamut-hdr-media-query-fingerprint.md) for
the display-hardware half of the same surface.

To check your own build against a stock browser, read both and diff them field by
field rather than trusting a score:

```js
[
  'prefers-reduced-motion: reduce',
  'prefers-contrast: more',
  'forced-colors: active',
  'prefers-reduced-transparency: reduce',
  'prefers-color-scheme: dark',
].forEach(q => console.log(q, matchMedia(`(${q})`).matches));
```

On a default Windows install every line is `false` except possibly the colour scheme.
If any accessibility line comes back `true` and you did not intend it, that is the tell.

## The honest limit

This cluster is an OS and configuration surface, and getting it right is exactly the
kind of thing this project is built to do: match what a real Windows browser reports,
field by field, so the fingerprint, the TLS handshake and the driver layer all read as
a genuine Firefox driven by a real person. That is why sessions pass most detection
checks. It is a demonstration, not a slogan: the four features above read as defaults
because the engine ships them as defaults.

What it does not do, and cannot: the build ships stock defaults, and it cannot change
your IP reputation, your per-account quotas, your rate limits, or your behaviour and
timing. A perfect accessibility cluster on a datacenter address that a thousand other
clients are using this minute still loses on the address. You supply the parts that live
outside the browser: a clean residential exit, human pacing, sensible per-account
volume. This page fixes one small OS-setting surface. It does not fix the session. If
your fingerprint is clean and you are still blocked,
[the reason is usually one of the other three layers](why-blocked-with-a-clean-fingerprint.md).

## Conclusion

The accessibility cluster is a small surface with an outsized ability to embarrass a
disguise, because it sits below every layer people normally work at and because its
safe value is boring. Report the defaults a fresh Windows install reports, keep the
whole cluster agreeing with `prefers-color-scheme` and the system palette, and it stops
being a tell. Report a rare setting nobody turned on by hand, or contradict the colour
scheme, and it becomes one of the cheapest signals a page can collect. invisible_playwright
handles the source-level part; the network and the behaviour are still yours to get
right.

## Short answers to the questions that lead here

**Can prefers-reduced-motion really be read without JavaScript?** Yes. A stylesheet
branches on it and loads a different background image per branch, so the server learns
the value from which request arrives. There is no script to block.

**What should a normal Windows machine report?** `no-preference` for reduced motion,
contrast and reduced transparency, and `none` for forced colours. Almost everyone leaves
all four at those defaults.

**Does invisible_playwright spoof these?** It ships them at stock Windows defaults, so a
default session reads like an untouched install and the cluster agrees with the colour
scheme and the system palette rather than contradicting them.

**Why is a non-default value a problem if it is a real setting?** Because it is rare. A
value only a small minority of real users report narrows you to that minority before any
other signal is measured, and a value that does not match your claimed theme is a
straight contradiction.

**If I get these right, will I stop being blocked?** Not on its own. This is one OS
surface. It does not change your IP reputation, your rate limits, your account quotas or
your behaviour, and any of those can block a browser whose fingerprint is perfect.

**Is turning on Firefox's resistFingerprinting a shortcut here?** No.
[That mode is usually the wrong move for automation](resist-fingerprinting.md); it
changes and breaks more than this cluster and tends to create its own tells.

## Sources

- The CSS media-feature specifications for the four preference features named above, and
  their documented default values.
- This project's own release gates, which compare each media feature against a stock
  Windows browser on the same machine rather than reading a verdict.

**See also:** [what media queries reveal about a machine](css-media-query-fingerprinting.md),
[the color-gamut and HDR half of the same surface](color-gamut-hdr-media-query-fingerprint.md),
and [why a clean fingerprint is only one of four layers](why-blocked-with-a-clean-fingerprint.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The accessibility cluster
is easy to forget precisely because its right value is the boring one.*
