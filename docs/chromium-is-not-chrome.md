---
title: "Chromium is not Chrome, and detectors know the difference"
description: "Chrome for Testing, Playwright's default since 1.57, plays H.264 but still fails the Widevine DRM check - a compiled-in gap that no JavaScript patch can close."
parent: "Comparisons"
nav_order: 3
---


# Chromium is not Chrome, and detectors know the difference

**Short answer:** no, they are not the same, and the gap moved in 2026 rather than closing.
Playwright's default Chromium - Chrome for Testing since v1.57 - now plays the same
H.264/AAC codecs as real Chrome, checked directly in a live session. It still fails
Chrome's [Widevine DRM check](https://developer.mozilla.org/en-US/docs/Web/API/Encrypted_Media_Extensions_API), and that part cannot be patched from JavaScript because it is
a compiled-in capability, not a value a page reads off a string.

Every stealth guide for Chromium-based automation quietly assumes "Chromium" and "Chrome"
mean the same thing, or that the gap between them is fixed and permanent. Neither is true.
The gap is real, it moved in 2026, and one part of it is still a **compiled-in capability**
that a page can test for directly and that no amount of JavaScript patching can produce.

This page is what Playwright actually ships today, what changed and what didn't, why the
part that remains cannot be patched, why spoofing the user agent makes it worse rather than
better, and why Firefox has no equivalent gap.

## What Playwright ships today, checked directly rather than assumed

Playwright's own browser documentation used to be the whole answer: the default managed
Chromium was an open-source build, missing the proprietary parts Google adds on top. As of
[Playwright 1.57](https://playwright.dev/docs/release-notes), that changed. The default managed binary - launched with no `channel` set
at all - is now **Chrome for Testing**, Google's own dedicated distribution for automated
testing, pinned to the Playwright version rather than auto-updating.

Chrome for Testing is not the stripped-down build the old default was. It ships the same
proprietary codec support as the Chrome a real visitor runs. Checked directly, in a live
Playwright session with no `channel` argument and no stealth layer applied: H.264 and AAC
both play. The codec half of this page's original argument no longer holds for a current,
default Playwright launch - it holds for whatever version you're actually pinned to, and it
is worth checking your own rather than assuming either answer.

## The Widevine gap that survives the switch

Checked in the same session: Widevine still rejects.

```js
await navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{
  initDataTypes: ['cenc'],
  videoCapabilities: [{ contentType: 'video/mp4; codecs="avc1.42E01E"' }],
}]);
// current default Playwright (Chrome for Testing): rejects, NotSupportedError
```

Chrome for Testing carries Chrome's codecs but not its Widevine CDM (Content Decryption
Module) - DRM distribution has its own licensing path separate from the codec libraries,
and Google's dedicated testing distribution does not carry it. That gap is the one worth
building an argument around, because unlike the codec question, it is not a moving target
tied to which Playwright version happens to be pinned - it is a deliberate omission from a
distribution built for automation in the first place.

Here is the part that matters for anyone building or buying a stealth tool:

> **This is not a value. It is a capability.** A missing DRM module is missing machine
> code. You can lie about `navigator.userAgent` because it is a string; you cannot lie
> about whether the build can actually negotiate a Widevine session, because the page can
> ask it to and read the real answer.

This is the same distinction as
[a renderer string against the pixels a rasteriser actually draws](renderer-string-vs-render.md),
and it is the expensive class of problem for the same reason.

[The codec page](codec-fingerprinting.md) covers the wider surface; this is the sharpest
surviving instance of it, even after the part that used to accompany it closed.

## Almost nobody browses with a Widevine-less build

Almost nobody outside development, CI and automation runs a browser without Widevine.

Real people use Chrome, Edge, Brave, Opera, Vivaldi, and every one of those ships Widevine,
because a browser that cannot play protected video from a mainstream streaming service is
not a browser anybody keeps as their daily one. A build that answers "no" to the Widevine
check is a developer artefact, a CI image, or automation - not a real visitor's machine.

So the population of "sessions reporting no Widevine support" is made up overwhelmingly of
developers and automation. That is not a fingerprint value being rare. That is **a cohort
whose membership is almost entirely the thing being looked for.**

And unlike most signals in this subject, it needs no model and no scoring. It is a
capability check with a yes or no answer.

## Spoofing the user agent makes this worse

Setting the user agent to claim Chrome does not close the Widevine gap - it turns a merely
missing capability into a contradiction the page can check directly.

A stealth tool sets the user agent to claim Google Chrome. On a current, default Playwright
launch the codec set now agrees with that claim - that particular contradiction closed along
with the codec gap. The Widevine one didn't. Now the page has:

- a user agent saying **Chrome**,
- a Widevine check saying **no such CDM**,
- and in Chromium, `Sec-CH-UA` brands that
  [have their own copy of the same claim](client-hints-sec-fetch.md), agreeing with the
  user agent while the DRM check contradicts both.

Before the spoof, the browser was merely missing a capability real visitors have. After it,
the browser is lying about being able to do something it provably cannot, and the lie is
checkable in one line rather than inferred from a rare value. That is
[the same mistake the user agent page is about](playwright-user-agent.md), in its most
concrete remaining form.

## The `channel` escape, and what it costs

Playwright does let you opt into a branded browser:

```python
p.chromium.launch(channel="chrome")
```

That launches the real Chrome installed on the machine, with the real Widevine. It
genuinely closes the remaining gap - the one thing a current default launch, running Chrome
for Testing, still doesn't give you.

The costs are real and worth being explicit about:

- **You do not ship it.** It uses whatever Chrome is installed, at whatever version, so
  your fleet is only as consistent as your machine images.
- **It updates underneath you**, on Google's schedule rather than yours.
- **It is not available everywhere**, particularly in slim containers, which is exactly
  where automation runs.
- **It is still Chrome with automation attached**, so every other layer of this subject
  applies unchanged.

It is the right call when you can take those costs. It is not a solved problem, it is a
traded one.

## Firefox has no equivalent gap

Firefox has no capability gap like Widevine to exploit, because it has no proprietary-versus-open
split of this kind in the first place - which is the structural reason this belongs in a
comparison rather than a list of tips.

There is no "Firefox" and
"Firefox-without-the-codecs" pair where one is what people run and the other is what
automation runs. The build Mozilla ships and a build compiled from the same source with
the release configuration have the same feature set.

So a patched Firefox is **Firefox**, in the ways a capability check can test. A patched
Chromium is **Chromium claiming to be Chrome**, and the claim is contradicted by things
that are not strings.

That is not an argument that Firefox wins overall.
[The argument against Firefox is its share of real traffic](firefox-vs-chromium-antidetect.md),
and it is serious. But on this specific axis the two engines are not in comparable
positions, and most comparisons never mention it.

## Codec and DRM support, by build

| Build | H.264 / AAC playback | Widevine DRM | What it actually is |
|---|---|---|---|
| Bare open-source Chromium (pre-1.57 default, or an explicit unbranded build) | No | No | Missing the proprietary codec and DRM additions Google adds on top of open-source Chromium |
| Chrome for Testing (Playwright's default since 1.57, no `channel` set) | Yes | No | Google's own automation distribution - Chrome's codecs, not Chrome's DRM licensing |
| Real Chrome, launched via `channel="chrome"` | Yes | Yes | The actual installed browser, with the update and install costs described above |
| Firefox, patched at the C++ level | N/A - no split | N/A - no split | One build, not two: there is no stripped variant that only automation runs |

## How to check your own build for the gap

Run this in whatever browser your automation actually launches:

```js
const v = document.createElement('video');
console.table({
  h264: v.canPlayType('video/mp4; codecs="avc1.42E01E"') || 'no',
  aac:  v.canPlayType('audio/mp4; codecs="mp4a.40.2"')   || 'no',
  mp3:  v.canPlayType('audio/mpeg')                       || 'no',
  ua:   navigator.userAgent,
});

navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{
  initDataTypes: ['cenc'],
  videoCapabilities: [{ contentType: 'video/mp4; codecs="avc1.42E01E"' }],
}]).then(() => console.log('widevine: yes'), () => console.log('widevine: no'));
```

Then compare against the browser you are claiming to be, on the same machine.

- If the user agent says Chrome and H.264 comes back empty, your build predates the
  Chrome for Testing default, or you launched with an explicit bare-Chromium build, and you
  have the codec-level version of the contradiction described above.
- If Widevine rejects while the user agent says Chrome, that is the gap that survives even
  on a current, default launch.
- If both are consistent with the claim, this particular problem is not yours - but check
  which one actually changed before assuming why.

## Conclusion

The most repeated advice in Chromium-based stealth is to make the browser look like
Chrome. As of Playwright 1.57's switch to Chrome for Testing as the default, the browser
got measurably closer to actually being Chrome on the codec axis - that part of the old
advice is less wrong than it used to be. Widevine did not follow, and it remains a
capability rather than a value, which puts it out of reach of every technique that works
on the others.

The choices that actually address the remaining gap are to run real Chrome through a
branded channel and accept that you no longer control the binary, or to use an engine that
does not have the gap in the first place.

What does not address it is a better user agent string, which converts a browser that is
merely missing one capability into one that is provably lying about it.

## Short answers to the questions that lead here

**Does Playwright use Chrome or Chromium?** As of Playwright 1.57, the default managed
binary (no `channel` set) is Chrome for Testing - Google's own dedicated automation
distribution, carrying Chrome's codecs but not its Widevine CDM. Earlier Playwright
versions, or an explicit bare-Chromium build, still lack the codecs too. Branded channels
remain available as an opt-in for the rest.

**How can a website tell an automated Chromium-family browser from real Chrome today?**
Mostly by asking for Widevine DRM support, which the codec-carrying default still lacks.
The codec check that used to work for this closed as of the 1.57 default switch.

**Can I patch Widevine support in?** Not from JavaScript. A missing DRM module is missing
code, not a missing property.

**Is using a Widevine-less build suspicious?** Yes, specifically: almost the only sessions
answering "no" to that one check are developers, CI, and automation.

**Should I spoof the user agent to say Chrome?** No. On a current default launch it no
longer contradicts the codec check, but it still contradicts the Widevine one, and that
contradiction is checkable in one line.

**What about `channel="chrome"`?** It closes the remaining gap and hands you a browser you
do not ship, do not version and cannot always install.

**Does Firefox have the same problem?** No. There is no widely-used stripped Firefox that
automation runs and people do not.

**See also:** [Codec fingerprinting](codec-fingerprinting.md) for the wider surface,
[why you should not set the user agent](playwright-user-agent.md),
[Client Hints and Sec-Fetch](client-hints-sec-fetch.md) for the third copy of the same
claim, and
[Firefox or Chromium for anti-detect](firefox-vs-chromium-antidetect.md) for the argument
in both directions.

## Sources

- [Playwright's release notes](https://playwright.dev/docs/release-notes), on the switch
  from custom-compiled open-source Chromium to Chrome for Testing as the default managed
  binary.
- Direct capability checks run in a live Playwright session, 2026-07-30: H.264 and AAC
  playable, Widevine (`com.widevine.alpha`) rejected with `NotSupportedError`, on the
  default managed binary with no `channel` argument set.
- [The Encrypted Media Extensions API](https://developer.mozilla.org/en-US/docs/Web/API/Encrypted_Media_Extensions_API), for the Widevine capability check above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. This is the argument for the engine choice that we
find most convincing, and it is the one most comparisons leave out.*
