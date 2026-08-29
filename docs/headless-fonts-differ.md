---
title: "Why headless browsers render different fonts"
description: "Headless and headful Firefox render different fonts because of OS font configuration and rasterising - the same causes behind canvas fingerprints."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 5
---

# Why headless browsers render different fonts

Headless and headful Firefox render the same page with different fonts because font
rendering comes from the operating system's font configuration and rasteriser
settings, not from the browser - and a headless environment usually has less of both.
Run the same page both ways on the same machine and the text is not identical:
different widths, different antialiasing, sometimes a different typeface entirely.
Screenshot tests that pass locally fail in CI for this reason, and the same difference
is a fingerprinting signal.

Both problems - flaky screenshots and a fingerprinting tell - trace back to the same
three causes.

## Cause one: headless has no font configuration, not no fonts

The common belief is that a headless browser ships fewer fonts. It usually ships the
same ones. What it lacks is the desktop environment that tells it which font to use
when a page asks for something that is not installed.

On Linux that is fontconfig. A desktop session has a populated fontconfig with
aliases, so a request for Helvetica resolves to Nimbus Sans or Liberation Sans, and
generic families like `sans-serif` map to something reasonable. A bare container
often has a minimal fontconfig and a handful of fonts, so the same request falls
through to whatever DejaVu is present.

The page did not change. The resolution of "the font you asked for is missing" did.

## Cause two: the rasteriser is configured differently

Even with identical fonts, glyphs are drawn differently depending on subpixel
antialiasing, hinting level, and whether a GPU is doing the work.

A desktop session usually has subpixel rendering on, with hinting settings from the
user's preferences. A headless container has neither, so it falls back to grayscale
antialiasing and default hinting. Same glyph, same size, different pixels, and
therefore a different canvas hash for any text you draw.

This is why a canvas fingerprint can differ between two machines that report an
identical font list. The list is not the whole story: the rendering is.

## Cause three: measurement differs from rendering

Font detection on the web is mostly indirect. You cannot [enumerate installed
fonts](https://developer.mozilla.org/en-US/docs/Web/API/Local_Font_Access_API) from
JavaScript in a modern browser without an explicit, per-site permission grant that
detection scripts never trigger, so scripts measure instead: render a string in a
candidate font with a known fallback, compare the width, and if it differs the font
exists.

That technique inherits every difference above. A machine whose fallback resolution
differs will report a different *set* of fonts as installed, even though nothing was
installed or removed. Two containers from the same image can disagree if their
fontconfig caches were built at different times.

## Why any of it matters beyond flaky screenshots

Fonts are one of the highest-entropy signals a browser exposes, and unlike most of
them they are hard to fake convincingly, for a reason worth stating plainly:

**The claimed platform implies a font set.** Windows means Segoe UI, Calibri,
Cambria, Tahoma and a specific set of CJK fonts. macOS means San Francisco, Helvetica
Neue, Menlo. A generic Linux container means DejaVu and Liberation and not much else.

So a browser announcing Windows in its user agent, on a font set that is plainly the
Linux default, has contradicted itself. No property override fixes this, because
nothing was overridden: the fonts really are the fonts, and the measurement really
does measure them.

This is the same shape as [the WebGL renderer string](webgl-renderer-strings.md) and its
software-rasterizer problem. It is not an automation tell. It is a *this is a server* tell, and [the same distinction runs across
every headless signal](headless-vs-headful.md), not just fonts.

## What the enumeration actually does, and what each platform's set looks like

Since there is no API that lists installed fonts, detection works by measurement.
The technique is the same everywhere:

```js
// render a string in a candidate font, with a known fallback behind it
// if the width differs from the fallback alone, the candidate exists
const probe = (family) => {
  span.style.fontFamily = `"${family}", monospace`
  return span.offsetWidth
}
```

A script does this for a list of a few hundred candidate families and keeps the ones
whose width differs from the fallback. The output is a set, and the set is the
fingerprint.

Which means the answer is decided by what is installed, and installed sets are
strongly platform-shaped:

| Platform | Typical detected font set |
|---|---|
| Windows | Segoe UI, Calibri, Cambria, Candara, Consolas, Constantia, Corbel, Tahoma, Verdana, Georgia, Trebuchet MS, Impact, Comic Sans MS, Franklin Gothic, plus a large CJK set. Office adds a further block that most Windows machines have and most non-Windows machines do not. |
| macOS | San Francisco, Helvetica Neue, Menlo, Monaco, Optima, Palatino, Avenir, Geneva, Lucida Grande. |
| A typical Linux desktop | DejaVu, Liberation, Noto and often Ubuntu or Cantarell, and almost none of the Windows or macOS set. |
| A container | Frequently DejaVu and nothing else, or literally no fonts, in which case every probe returns the fallback width and the detected set is empty. An empty font set is its own strong signal, because no real desktop has one. |

So a browser claiming Windows in its user agent, on a machine whose detected set is
DejaVu and Liberation, has been caught by a comparison instead of by any single
value. This is the same shape as the WebGL renderer problem: the claim and the
hardware disagree, and the disagreement is what gets read.

There is one more wrinkle worth knowing. **The order of the resulting list can carry
information too**, since some implementations report fonts in the order the system
returns them, which reflects how they were installed. Two machines with identical
sets can differ in sequence.

## What actually fixes it

For test flakiness, make the environment explicit rather than hoping it matches: pin
the same fonts and the same fontconfig into the image the tests run in, and accept
that "same as my laptop" is not a thing you get for free.

For fingerprint consistency, the options are worse than people expect:

- **Installing the right fonts** is necessary and not sufficient. The set has to
  match the claimed platform closely enough, including the fonts nobody thinks of,
  and installing Microsoft's fonts on Linux is a licensing question before it is a
  technical one.
- **Hooking the font enumeration in JavaScript** does not work, because there is no
  enumeration to hook. The measurement is rendering, and to change the measurement
  you have to change what gets rendered.
- **Making the font stack self-contained inside the browser** does work, and is what
  this project does: the engine carries its own font bundle and ignores the host's
  entirely, on all three platforms, so the answer is the same whether the machine
  underneath is Windows, Linux or a container with no fonts at all. It is also the
  most work of the three by a wide margin, since each operating system has its own
  font backend to intercept - [the manifest that keeps those three backends in
  agreement](bundled-fonts-cross-platform.md) is its own story.

The short summary is that fonts are cheap to check and expensive to get right, which
is exactly why detectors like them.

## Checking your own

Draw text to a canvas and hash it, on the machine that will run the job, then do the
same on a normal desktop. If the hashes differ, everything above is in play.

Then compare the font set your browser reports against what the platform in your user
agent would have. That single comparison catches more misconfigured setups than any
other check in this area, and it costs one page load.

## Short answers to the questions that lead here

**Why do headless browsers render different fonts?** Because font rendering comes from
the operating system, and a headless container usually has a different, much smaller
font set than a desktop. The browser is not doing anything different.

**Will installing more fonts fix it?** No, and it can make things worse. The set has to
match the platform you claim, not simply be larger.

**What fonts does a real Windows machine have?** A recognisable core set that ships
with the OS, plus whatever applications added. A Linux container typically has DejaVu
and Liberation and little else, which is a one-line check against a Windows user agent.

**Can fonts be spoofed from JavaScript?** The enumeration can be filtered. The metrics
cannot, because measuring text is the OS doing the work, and measurement is what
serious checks use.

**Does this matter if I am not hiding automation?** Yes, if you claim a platform you
are not on. It says nothing about automation and everything about where you run.

## Sources

- [MDN: Local Font Access API](https://developer.mozilla.org/en-US/docs/Web/API/Local_Font_Access_API),
  retrieved 2026-08-28, for why enumerating installed fonts from JavaScript requires
  an explicit, per-site permission grant that detection scripts never trigger.
- This project's own font-bundle architecture, which makes the font stack
  self-contained on all three platforms so the reported set does not depend on the
  host underneath it.

**See also:** [WebGL renderer strings](webgl-renderer-strings.md), which is the same shape of problem on different hardware, [why the speech voice list is empty](speech-synthesis-voices.md), which is the same problem again on a surface nobody checks, and [the checklist for one site failing](playwright-detected-as-bot.md), where fonts are step three.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level that bundles its own fonts on Windows, Linux and
macOS. The font files were never the hard part: this project's own Linux build once kept
reading hinting and antialiasing from the host's fontconfig after the fonts themselves
were bundled, so the same page came back as different pixels depending on the machine
underneath it.*
