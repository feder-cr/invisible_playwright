---
title: "CSS fingerprinting: what media queries reveal"
description: "Media features and CSS system colours identify a machine with no script at all, which puts them out of reach of every page-level stealth layer. What they expose, and the two consistency traps."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 9
---


# CSS fingerprinting: what media queries reveal

Almost everything written about browser fingerprinting assumes JavaScript. Disable it,
block it, patch it, and the discussion is about what a script can read.

CSS does not need a script. A stylesheet can ask what kind of pointer you have, whether
your system is in dark mode, how many colours your display shows, and how large it is,
and it can report the answer by loading a different background image per branch. No
JavaScript runs, so **no page-level stealth layer is involved at all**.

This page covers what the features expose, the two places where CSS and JavaScript can
disagree about the same machine, the system-colour surface almost nobody knows about,
and what a consistent answer looks like.

## How a stylesheet exfiltrates without a script

The mechanism is a few lines and it has been known for years:

```css
@media (pointer: fine)   { body { background-image: url("/p?fine"); } }
@media (pointer: coarse) { body { background-image: url("/p?coarse"); } }
@media (prefers-color-scheme: dark) { body { border-image: url("/c?dark"); } }
```

Only the matching branch loads its image, so the server learns the answer from which
request arrives. Repeat across a dozen features and you have a fingerprint assembled
entirely from network requests.

Two consequences worth being explicit about:

- **Blocking or patching JavaScript does nothing here.** There is no function to
  override and no object to inspect.
- **It works on the first paint**, before any script you inject has a chance to run,
  which sidesteps the timing problem that
  [page-level patches have anyway](navigator-webdriver-explained.md).

Estimates of how much entropy this reaches vary, and the useful number is not the total.
It is that these values are *free* to collect and are rarely part of anyone's checklist.

## The media features that actually carry information

Not all of them are interesting. These are:

**Pointer and hover.** `pointer`, `any-pointer`, `hover`, `any-hover`. A desktop with a
mouse reports `fine` and `hover`. A phone reports `coarse` and `none`. A touchscreen
laptop reports both, through the `any-` variants, which is how a device with two input
methods is distinguished from one with a single method.

**Colour scheme.** `prefers-color-scheme` is the operating system's dark mode setting,
and it is close to a coin flip across real users, so it is a genuine bit rather than a
constant.

**Display characteristics.** `resolution` and `color-gamut` describe the panel.
`color-gamut: p3` says a modern, better-than-average display.

**Accessibility preferences.** `prefers-reduced-motion`, `prefers-contrast`,
`forced-colors`, `inverted-colors`. Almost everyone leaves these at the default, which
is exactly why a non-default value is informative: it is rare.

**Size.** `width`, `height`, `device-width`, `device-height`, and the interaction with
`resolution`. Which brings us to the trap.

## Trap one: CSS and JavaScript can disagree about the screen

This is the mistake that is specific to spoofing, and it is invisible unless you look
for it.

In Firefox, the size a media query resolves against and the size
`screen.width` reports **come from different code inside the browser**. They are two
separate paths to the same physical fact.

Spoof only the JavaScript side and you get a browser where:

```js
screen.width                                  // 1920
matchMedia('(device-width: 1920px)').matches  // false, the real panel is something else
```

One line of CSS and one line of JavaScript, and they disagree about the machine they are
running on. That is not an unusual value, it is a contradiction, and contradictions are
what the checks in this whole subject are built on.

The same applies to the pointer capabilities: a touchscreen laptop reporting `coarse` to
CSS while the JavaScript side claims a desktop persona is the same class of mismatch.

This project handles both in the browser's own media-feature code, so the CSS device
size comes from the same profile as `screen`, and the pointer capabilities report a
mouse rather than whatever hardware the host happens to have.

### How to check it yourself

```js
console.log(screen.width, screen.height);
console.log(matchMedia(`(device-width: ${screen.width}px)`).matches);   // want true
console.log(matchMedia('(pointer: fine)').matches,
            matchMedia('(any-pointer: coarse)').matches);
```

If the second line is `false`, something is spoofing one side only.

## Trap two: CSS system colours give away the operating system

This one is genuinely obscure and it is checked in production.

CSS has keywords that resolve to the operating system's own interface palette:
`ButtonFace`, `ButtonText`, `Canvas`, `CanvasText`, `Highlight`, `HighlightText`,
`Menu`, `Field`, and a few dozen more. A page can read them with no permission and no
script beyond one call:

```js
const d = document.createElement('div');
d.style.color = 'ButtonText';
d.style.backgroundColor = 'ButtonFace';
document.body.appendChild(d);
getComputedStyle(d).color;            // an RGB triple from the OS theme
```

Those values are the actual theme colours. On Windows they come from the Win32 palette.
On Linux they resolve through the desktop theme, and the RGB values are different, even
when the user agent says Windows.

So a browser claiming Windows and returning a Linux theme's palette has answered the
platform question twice with two different answers, from a surface most people have
never enumerated. A commercial fingerprinting service hashes exactly this set.

This project pins the full palette to the Windows default so it matches the claimed
platform, with one exception worth knowing: when the persona is a dark-theme user, the
palette is skipped and the dark-mode preference is set instead, because pinning a light
palette onto a dark-mode profile would create the contradiction it exists to prevent.

## What a consistent CSS answer looks like

Working from all of the above:

- **Pointer and hover describe one plausible input device**, and agree between the
  `any-` variants and the plain ones.
- **The CSS device size matches `screen`**, exactly.
- **`prefers-color-scheme` matches the system-colour palette.** Dark mode with a light
  palette is a contradiction.
- **Accessibility preferences are at their defaults** unless you have a reason, because
  non-default values are rare and therefore identifying.
- **`color-gamut` and `resolution` suit the display you claim.**
- **Everything is stable for the session.** A colour scheme that flips mid-session is a
  browser nobody has.

## Conclusion

CSS fingerprinting matters not because it is the strongest signal but because it is the
one that operates below every layer people normally work at. There is no script to
patch, so the values have to be right at the source or not at all.

The two traps are the practical takeaway. CSS and JavaScript reach the screen size by
different routes inside the browser, and the system-colour palette is a platform tell
sitting in plain sight, readable with one `getComputedStyle` call.

## Short answers to the questions that lead here

**Can a website fingerprint me with CSS alone?** Yes. Media queries can load different
resources per branch, so the server learns the answer without any script running.

**Does blocking JavaScript stop CSS fingerprinting?** No. That is the point of it.

**What is `prefers-color-scheme` worth to a tracker?** Roughly one bit, and a reliable
one, because real users are split between the two settings.

**Why would CSS and `screen.width` disagree?** Because the browser resolves them through
different code, so spoofing one and not the other leaves them inconsistent.

**Are CSS system colours really used for fingerprinting?** Yes, by hashing the resolved
values of the system keywords. They also reveal the operating system independently of
the user agent.

**Should I set unusual accessibility preferences?** No. Almost everyone is at the
default, so anything else narrows you down rather than hiding you.

**See also:** [screen size and viewport tells](screen-size-headless-tells.md), which is
the JavaScript half of the size question,
[why headless browsers render different fonts](headless-fonts-differ.md), another
platform tell that no script controls, and
[the checklist for being detected on one site](playwright-detected-as-bot.md).

## Sources

- MDN's reference for the `@media` at-rule and the individual media features named
  above.
- Published work on CSS-based fingerprinting, which is where the load-an-image-per-branch
  technique is described.
- This project's own media-feature and system-colour handling, and the reason the palette
  is skipped for dark-theme personas.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The system-colour palette is pinned here because a
commercial detector was hashing it and catching the host operating system through it.*
