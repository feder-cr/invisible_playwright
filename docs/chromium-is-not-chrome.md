---
title: "Chromium is not Chrome, and detectors know the difference"
description: "Playwright ships Chromium, which has no H.264, no AAC and no Widevine. Almost nobody browses with it, and those gaps are compiled-in capabilities that no JavaScript patch can fill."
parent: "Comparisons"
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
      "name": "Comparisons",
      "item": "https://feder-cr.github.io/invisible_playwright/comparisons.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Chromium is not Chrome, and detectors know the difference"
    }
  ]
}
</script>

# Chromium is not Chrome, and detectors know the difference

Every stealth guide for Chromium-based automation quietly assumes the two words mean the
same thing. They do not, and the difference is not cosmetic branding. It is a set of
**compiled-in capabilities** that a page can test for directly and that no amount of
JavaScript patching can produce.

That matters more than any individual fingerprint value, because it means a very large
share of automation is running a browser that essentially no real visitor runs, while
claiming to be one that they do.

This page is what Playwright actually ships, what Chromium is missing, why almost nobody
browses with it, why spoofing the user agent makes the problem worse rather than better,
and why Firefox has no equivalent gap.

## Playwright ships Chromium, not Chrome

From Playwright's own browser documentation: for Google Chrome, Microsoft Edge and other
Chromium-based browsers, it uses **open-source Chromium builds** by default. Recent
versions go further and use a separate headless shell binary for headless runs.

So unless you deliberately opted into a branded channel, the browser your automation is
driving is Chromium. Not Chrome, not Edge, not Brave. Chromium.

## What Chromium is missing, and why it cannot be patched in

The gaps are the proprietary parts Google adds on top of the open-source base:

- **H.264 and AAC**, the codecs behind most video and audio on the web.
- **MP3** in many builds.
- **Widevine**, the DRM used by every mainstream streaming service.
- Google account sync, and the branding.

Two of those are directly testable from a page, in a few lines:

```js
// codec support
const v = document.createElement('video');
v.canPlayType('video/mp4; codecs="avc1.42E01E"');   // Chrome: "probably". Chromium: ""

// DRM support
await navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{
  initDataTypes: ['cenc'],
  videoCapabilities: [{ contentType: 'video/mp4; codecs="avc1.42E01E"' }],
}]);   // Chrome: resolves. Chromium: rejects.
```

Here is the part that matters for anyone building or buying a stealth tool:

> **These are not values. They are capabilities.** A missing decoder is missing machine
> code. You can lie about `navigator.userAgent` because it is a string; you cannot lie
> about whether the build contains an H.264 decoder, because the page can ask it to decode
> something.

This is the same distinction as
[a renderer string against the pixels a rasteriser actually draws](renderer-string-vs-render.md),
and it is the expensive class of problem for the same reason.

[The codec page](codec-fingerprinting.md) covers the surface in general; this is the
sharpest instance of it.

## Almost nobody browses with vanilla Chromium

Look at who is on the other side of the check.

Real people use Chrome, Edge, Brave, Opera, Vivaldi. Every one of those ships the
proprietary codecs and Widevine, because a browser that cannot play video is not a
browser anybody keeps. Vanilla Chromium is a developer artefact and a Linux distribution
package.

So the population of "visitors reporting a browser with Chromium's capability set" is
made up overwhelmingly of developers and automation. That is not a fingerprint value being
rare. That is **a cohort whose membership is almost entirely the thing being looked for.**

And unlike most signals in this subject, it needs no model and no scoring. It is a
capability check with a yes or no answer.

## Spoofing the user agent makes this worse

Here is where it turns from a weak signal into a contradiction.

A stealth tool sets the user agent to claim Google Chrome. Now the page has:

- a user agent saying **Chrome**,
- a codec set saying **Chromium**,
- a Widevine check saying **Chromium**,
- and in Chromium, `Sec-CH-UA` brands that
  [have their own copy of the same claim](client-hints-sec-fetch.md).

Before the spoof, the browser was unusual. After it, the browser is lying, and the lie is
provable from two independent capabilities rather than inferred from a rare value. That
is a categorically worse position, and it is
[the same mistake the user agent page is about](playwright-user-agent.md), in its most
concrete form.

## The `channel` escape, and what it costs

Playwright does let you opt into a branded browser:

```python
p.chromium.launch(channel="chrome")
```

That launches the real Chrome installed on the machine, with the real codecs and the real
Widevine. It genuinely closes this gap.

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

This is the structural point, and it is the reason it belongs in a comparison rather than
in a list of tips.

Firefox has no proprietary-versus-open split of this kind. There is no "Firefox" and
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

## Checking your own

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

- If the user agent says Chrome and H.264 comes back empty, you have the contradiction
  described above.
- If Widevine rejects while the user agent says Chrome, same.
- If both are consistent with the claim, this particular problem is not yours.

## Conclusion

The most repeated advice in Chromium-based stealth is to make the browser look like
Chrome. The browser is not Chrome, and two of the differences are capabilities rather
than values, which puts them out of reach of every technique that works on the others.

The choices that actually address it are to run real Chrome through a branded channel and
accept that you no longer control the binary, or to use an engine that does not have the
gap in the first place.

What does not address it is a better user agent string, which converts an unusual browser
into a demonstrable lie.

## Short answers to the questions that lead here

**Does Playwright use Chrome or Chromium?** Chromium by default, per its own
documentation, with branded channels available as an opt-in.

**How can a website tell Chromium from Chrome?** By asking for something only Chrome has:
H.264 or AAC support, or Widevine DRM. Both are one line.

**Can I patch H.264 support into Chromium?** Not from JavaScript. A missing decoder is
missing code, not a missing property.

**Is using vanilla Chromium suspicious?** It is unusual in a specific way: almost the only
visitors with that capability set are developers and automation.

**Should I spoof the user agent to say Chrome?** No. It turns a rare browser into one that
contradicts itself on two independent checks.

**What about `channel="chrome"`?** It fixes this gap and hands you a browser you do not
ship, do not version and cannot always install.

**Does Firefox have the same problem?** No. There is no widely-used stripped Firefox that
automation runs and people do not.

**See also:** [Codec fingerprinting](codec-fingerprinting.md) for the wider surface,
[why you should not set the user agent](playwright-user-agent.md),
[Client Hints and Sec-Fetch](client-hints-sec-fetch.md) for the third copy of the same
claim, and
[Firefox or Chromium for anti-detect](firefox-vs-chromium-antidetect.md) for the argument
in both directions.

## Sources

- Playwright's browsers documentation, which states it uses open-source Chromium builds by
  default and offers branded channels as an opt-in.
- The documented differences between Chrome and Chromium: proprietary codecs including
  H.264 and AAC, Widevine DRM, sync and branding.
- The Encrypted Media Extensions API, for the Widevine capability check above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. This is the argument for the engine choice that we
find most convincing, and it is the one most comparisons leave out.*
