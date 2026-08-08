---
title: "How to make Linux and macOS report real Windows fonts"
description: "To make Linux and macOS report real Windows fonts, bundle the real font files and read only from them. Filtering a list leaves the host's rendering underneath."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 6
---


# How to make Linux and macOS report real Windows fonts

To make Linux and macOS report real Windows fonts, ship the real Windows font files
inside the browser and read only from that bundle on every platform. Filtering a host's
font list is not enough: it changes what gets reported, not what gets rendered - the
list says Windows, the pixels say something else. This page is what it took to make that
bundle hold on three unrelated font backends, and where it still leaks.
[Why headless browsers render different fonts](headless-fonts-differ.md) covers the
detection side of the same problem, if you want that first.

## Why filtering the host was never going to be enough

The first instinct is to filter: hide the host's real fonts, report a Windows-shaped
list instead. We tried a version of this and reverted it, because filtering only
changes what gets reported, not what gets rendered. The host still draws the glyphs,
still measures the widths, still rounds however its rasteriser rounds. A filtered font
*name* sitting on top of Linux font *rendering* is a different, subtler version of the
same contradiction: the list says Windows, the pixels say something else.

The fix that actually holds is upstream of the whole problem: ship the real font
files inside the browser, and read only from that bundle, on every platform. This is
the same direction Camoufox took first, and for the same reason - a real file produces
real glyphs, real metrics, and a real hash, because nothing about it is simulated.

## One manifest, so three backends can't disagree with each other

Firefox already has a mechanism for carrying fonts inside the binary,
`MOZ_BUNDLED_FONTS`, on by default on Windows and Linux and available on macOS with a
build flag. The harder problem isn't shipping the files, it's making Windows'
DirectWrite, Linux's fontconfig and macOS's CoreText - three font backends that know
nothing about each other and enumerate host fonts in three different ways - report the
*same* family list from the *same* files.

The fix is a single manifest, generated offline from the font files themselves and
checked in as data, listing every family and which file and face index backs it. All
three backends read the same manifest instead of asking their platform's normal font
enumeration, so instead of three per-OS filters that can quietly drift apart, there is
one source of truth for what a family is called and where its data lives.

Two details in that manifest turned out to matter more than the format itself:

**Naming has to follow the same precedence a real OS uses**, or the family names
themselves become the tell. Font files carry more than one candidate name - a
typographic name, a GDI-compatible name, sometimes a WWS name - and which one Windows
actually shows depends on a precedence order. Getting that order wrong produces a
family list that is real but *shaped* wrong: a font's marketing name where a real
Windows machine would show its GDI name, which is exactly the kind of small
inconsistency [consistency-based detection](playwright-detected-as-bot.md) is built to
catch.

**Multi-face font files can't be indexed positionally across platforms.** A single
`.ttc` file can carry several unrelated faces - regional CJK variants, for instance -
and the safe way to pick the right one turned out to differ by backend: an index that
is reliable on one platform is not guaranteed to point at the same face on another,
because not every platform documents face order as stable. Matching by the face's own
embedded name instead of its position avoids picking the wrong face silently.

## Making three backends refuse to fall back to the host

A manifest is only half the fix. Each backend still has its own default behaviour when
something isn't in its bundle-only list, and each of those defaults quietly reaches for
the host if you don't close it explicitly:

- One backend's safety fallback, when the bundle is unreadable, is to fail toward an
  empty list rather than the host - which breaks rendering visibly instead of leaking
  silently, the correct failure direction for a fingerprinting surface.
- Another's default path, taken naively, falls straight through to a live host
  enumeration call the moment the bundle-only condition isn't satisfied.
- The third had no bundle-only mode at all going in; it enumerates host and bundled
  fonts through the same single API call, so keeping the host out requires filtering
  every result rather than skipping a call.

Closing all three took a different patch per backend, because "don't enumerate the
host" means something different to DirectWrite, to fontconfig, and to CoreText. There
is no single flag that does this once for all platforms.

## The macOS gap: two layers, and only one was visible without a Mac

CoreText's version of this fix shipped later than Windows' and Linux's, and not
because it was harder to write. It was harder to *know was wrong*, because nobody
doing the work had a Mac to run it on.

**Layer one, found by reading the code rather than running it:** the block-at-birth
hook that stops each backend from falling back to a live host enumeration had been
wired into the Windows and Linux font backends, and simply never ported to CoreText.
Reading the three backends side by side made that omission visible without running
anything - a Mac host would enumerate its own system fonts right alongside the bundle,
leaking the host OS on the one platform where nobody could see it happen locally.

**Layer two surfaced only when the first fix actually ran in CI.** Porting the hook
and shipping it should have closed the gap. It didn't: the very next automated
macOS run still showed most of the bundle missing and several real host fonts
leaking through, on a build that had compiled cleanly and looked, from the source,
like a complete fix. The actual cause was one level down - the build configuration
that turns bundled fonts on at all was set to enable itself only on two of the three
target platforms. The third silently defaulted off, which also compiled out the very
hook layer one had just added, since that code only exists when bundled fonts are
built in. A hook that is correct and a build that never includes it produce the same
observed nothing.

Both layers are closed now, and CoreText enumerates the identical family set the
other two backends do. The reason this is worth telling as one story rather than two
separate fixes: the first fix looked complete by every check available without a
real Mac, and was not. The only thing that caught the second layer was an automated
run on the actual operating system, checking the actual output, after the fix that
was supposed to be the whole answer. A gate that runs somewhere other than the real
target platform is checking a different, easier question.

## What this validates, and what it costs

Validated cross-platform: Windows and Linux expose the identical family set from the
bundle, zero host fonts leaking through on either,
[generic CSS families](https://www.w3.org/TR/css-fonts-4/) (serif, sans,
monospace, and the per-script CJK generics) resolving to the same bundled fonts rather
than collapsing to whatever the host happens to have. The same check on macOS runs in
CI rather than locally, for the ordinary reason that not every setup has a Mac to test
on, and it has passed there too.

The cost is real and worth naming rather than glossing over. The binary is
meaningfully larger, because it now carries real font files rather than relying on
whatever the OS already had installed. And a fixed bundle also fixes the family list
in time: unlike a live host, the browser cannot pick up a font a real Windows Update
might add later. Both are the price of an answer that has no per-platform seams to find.

## Where it still leaks, named rather than hidden

Consistent with treating a suppressed signal as a fail rather than a pass, the honest
gaps in this approach, found and not yet closed:

- One backend's [CSS `local()`](https://www.w3.org/TR/css-fonts-4/) font lookup path
  isn't wired to the bundle-only mode at all, so a page asking for a font by exact name
  through that specific CSS feature fails to resolve where a real installation would have. A resolved lookup versus a
  failed one is itself a detail a determined check could compare.
- One backend's fallback for a character outside the bundle's family set reaches
  straight for the real host catalogue rather than staying inside the bundle, so an
  uncommon glyph can momentarily surface real host font data.
- One backend's `system-ui` generic keeps a narrow live query against the host font
  service as an extra fallback candidate, a single generic family that didn't get
  folded into the same guard as the rest.
- One platform's font-substitution mechanism reads a live OS-level alias table
  regardless of bundle-only mode, so the set of names that resolve through it still
  varies by the specific machine's own configuration.

None of these are enumeration leaks in the sense the validation above checks for -
that check passed, repeatedly, on real measurement. They are narrower seams: specific
code paths that keep a live dependency on the host font service instead of routing
through the bundle like everything else. Each is a known, open item rather than
something papered over, because a gap you haven't found yet is worse than one you have.

## A closed example of the same class: enumeration passing while rendering was wrong

Before the manifest fixed multi-face naming across platforms, a narrower version of the
same problem shipped and was found and closed on Linux specifically. It is worth
walking through because it is the clearest illustration of why enumeration alone is
never enough of a check.

A `.ttc` file is a collection: several complete faces sharing one set of font tables,
addressed through a `TTCHeader` that starts with the four-byte marker `ttcf` instead of
the single-face `sfnt` header an ordinary font file has. Several bundled CJK families -
regional Chinese, Japanese and a couple of serif/math variants - ship as `.ttc`
collections for exactly that reason: related faces, one file.

The font-table code on Linux never checked for that marker. It read every font file as
if it only had one face, at a fixed offset from the start of the file. For an ordinary
font that offset is correct. For a `.ttc` collection it lands wherever the first face's
data happens to be, regardless of which face was actually requested by name.

The result was a font that enumerated correctly - the family name resolved, the browser
reported it was using the right font - while the glyphs actually rendered came from
whichever face happened to sit at that fixed offset. Measured directly: metrics for
several of these families were wrong on Linux and correct on Windows, same seed, same
manifest, same claimed font name. Enumeration was clean. Rendering was not. That gap is
exactly what a check that stops at "is the font in the list" cannot see.

The fix adds a lookup that reads the `TTCHeader` when the `ttcf` marker is present,
finds the requested face's own offset from the collection's face-directory table, and
reads from there instead of assuming offset zero. After the fix, the same measurement
that had shown a mismatch showed matching metrics on both platforms, for every affected
family.

The general lesson is the same one the four seams above are named for: a signal that
looks closed because the obvious check passed is not the same as a signal that is
closed. This one needed a metrics comparison, not a font list, to be caught at all -
and it was a real, shipped gap for a period, not a hypothetical one.

## Conclusion

Filtering a host's fonts leaves the host's rendering underneath, which is a
contradiction with extra steps. Shipping the real files and reading only from them
removes the contradiction at the source, at the cost of a larger binary, a fixed
family list, and three separate backends that each needed convincing not to reach for
the host on their own. What's left is a short, named list of narrower seams, not a
general claim that the surface is closed.

## Short answers to the questions that lead here

**Can I make a Linux machine report Windows fonts?** Reporting the name is not the
hard part; matching the rendering is. Bundling the actual font files and reading only
from them is what makes the two agree.

**Does installing Windows fonts on Linux fix fingerprinting?** It gets you the right
names. It does not by itself stop the browser's normal font backend from also seeing
whatever else is on the host, and licensing is a separate problem again.

**Why not just spoof the font list in JavaScript?** There's no enumeration API to
intercept; [detection works by measuring rendered text](detect-installed-fonts-javascript.md),
and a script cannot change what the OS actually draws.

**Does this fix cost anything?** A larger binary, and a font list that's frozen at
build time rather than tracking whatever the real OS might add later.

**Is this the same thing Camoufox does?** The same direction - real files, not a
filter - checked independently rather than assumed to work the same way underneath.

**See also:** [why headless browsers render different fonts](headless-fonts-differ.md),
for the detection side this architecture answers; [invisible_playwright vs Camoufox](vs-camoufox.md),
for where this approach sits relative to theirs; and
[WebGL renderer strings](webgl-renderer-strings.md), for the same shape of problem on
different hardware.

## Sources

- This project's own font-bundle architecture and its per-platform integration work,
  including the validated cross-platform measurement (Windows/Linux locally,
  macOS in CI) and the four open gaps listed above.
- Firefox's own bundled-fonts build mechanism, which this architecture builds on
  rather than replaces.
- [CSS Fonts Module Level 4](https://www.w3.org/TR/css-fonts-4/) (W3C), for the generic
  font family keywords and the `local()` src descriptor referenced above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, including three separate font backends convinced not
to ask their host anything.*
