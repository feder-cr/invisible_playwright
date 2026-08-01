---
title: "Codec fingerprinting: canPlayType and MediaCapabilities"
description: "What codecs a browser claims to play identifies the build and the platform, and whether decoding is power efficient identifies the machine. A GPU-less server answers differently from a desktop."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 7
---


# Codec fingerprinting: canPlayType and MediaCapabilities

Ask a browser what video formats it can play and the answer is not about the person
using it. It is about the build, the operating system underneath, and increasingly the
hardware, which makes it three separate fingerprinting surfaces wearing one API.

It is also one of the surfaces nobody checks after spoofing everything else, which is
what makes it worth a page.

## What the two APIs actually return

The old one is deliberately vague:

```js
const v = document.createElement('video');
v.canPlayType('video/mp4; codecs="avc1.42E01E"');   // "probably", "maybe", or ""
v.canPlayType('video/webm; codecs="vp9"');
v.canPlayType('audio/mpeg');
```

Three possible answers: `probably`, `maybe`, or an empty string. The vagueness is
historical, and it does not reduce the information much, because what matters is the
**pattern across a list of types**, not any single verdict.

The modern one is far more revealing:

```js
await navigator.mediaCapabilities.decodingInfo({
  type: 'file',
  video: { contentType: 'video/mp4; codecs="avc1.42E01E"',
           width: 1920, height: 1080, bitrate: 3000000, framerate: 30 }
});
// { supported: true, smooth: true, powerEfficient: true }
```

Note the third field. That is the interesting one and the rest of this page is largely
about it.

## Codec support identifies the build and the platform

Which codecs a browser supports depends on how it was compiled and what the operating
system provides. Some formats are handled by the browser's own decoders, others are
delegated to the platform, and licensing means builds differ.

The practical consequences:

- **Different browsers give different patterns**, which is how codec probing is used for
  plain browser identification, independently of the user agent.
- **The same browser version on different operating systems gives different patterns**,
  because the platform decoders differ. A Linux build and a Windows build of the same
  Firefox are not identical here.
- **Stripped builds differ from consumer builds.** A minimal container image may lack
  system codecs that a desktop install has.

That third point is the one that bites automation. Claiming Windows while returning the
codec profile of a slim Linux container is the same class of contradiction as
[claiming Windows with a Linux font set](headless-fonts-differ.md), and it is checked
the same way: by comparison, not by rarity.

## powerEfficient is a hardware question, not a codec question

Here is the part that turns this from a browser-identification surface into a machine
one.

`powerEfficient` reports whether decoding that stream would use hardware acceleration.
On a desktop with a modern GPU, common formats decode in dedicated silicon and the
answer is `true`. On a machine with no GPU, everything decodes on the CPU and the
answer is `false` for formats a real desktop would accelerate.

So this API answers, in one field, the same question as
[a software WebGL renderer](webgl-renderer-strings.md),
[an empty speech voice list](speech-synthesis-voices.md) and
[a missing audio device](audiocontext-fingerprinting.md): is this a person's computer or
a server.

And `smooth` adds a second dimension, since it reflects whether the machine can decode
that resolution and framerate in real time.

Nobody lists `decodingInfo` in a stealth checklist. It costs one `await`.

## Why randomising codec support is the wrong instinct

The reflex, having read the above, is to vary the answers per session. That is the same
mistake as
[randomising WebGL numeric parameters](webgl-parameters-are-identical.md), and it fails
for the same reason.

Codec support is not a per-user setting. Everyone running a given browser build on a
given platform reports the same pattern, which means the pattern is a **cohort**, and the
crowd is the disguise. A browser reporting a combination of supported and unsupported
formats that no real build produces is not blending in, it has invented a build that does
not exist.

Nor should the values move between page loads. A browser that supports AV1 on one load
and not the next is describing software that reinstalled itself mid-session.

The defensible approach is the one used for the other hardware-shaped values in this
project: sample the codec profile **together with the machine class**, from combinations
that occur, so a low-end integrated-graphics identity does not claim the codec profile of
a workstation. It is one of four dimensions sampled jointly rather than independently,
alongside the audio profile, the storage quota and the antialiasing level, precisely so
they cannot contradict each other.

## Checking your own

```js
const v = document.createElement('video');
const types = [
  'video/mp4; codecs="avc1.42E01E"',
  'video/mp4; codecs="hev1.1.6.L93.B0"',
  'video/webm; codecs="vp8"',
  'video/webm; codecs="vp9"',
  'video/mp4; codecs="av01.0.05M.08"',
  'audio/mpeg', 'audio/ogg; codecs="opus"', 'audio/mp4; codecs="mp4a.40.2"',
];
console.table(Object.fromEntries(types.map(t => [t, v.canPlayType(t) || 'no'])));

const info = await navigator.mediaCapabilities.decodingInfo({
  type: 'file',
  video: { contentType: 'video/mp4; codecs="avc1.42E01E"',
           width: 1920, height: 1080, bitrate: 3000000, framerate: 30 },
});
console.log(info);   // watch powerEfficient
```

What you want to see:

- The pattern matches a stock browser of the same version **on the platform you claim**,
  not on the platform you are running.
- `powerEfficient` is `true` for mainstream formats if you are claiming a desktop.
- The answers are identical on a reload and on a relaunch of the same identity.
- Nothing exotic is supported that the claimed build does not support.

The first and second are the ones that fail on a server, and neither is fixed by anything
at the JavaScript layer.

## Conclusion

Codec probing is cheap, unglamorous and rarely audited. It carries three different kinds
of information at once: which browser you are, which platform you are on, and whether
your machine has a video decoder.

The first two are fixed by making the answers match the build and platform you claim.
The third is the familiar one: a machine with no GPU says so, in yet another place, and
the only real fixes are the same as everywhere else.

## Short answers to the questions that lead here

**Is `canPlayType` used for fingerprinting?** Yes, as a pattern across a list of types
rather than as a single answer. It identifies the browser and the platform.

**What does `powerEfficient` reveal?** Whether decoding would be hardware accelerated,
which is a statement about the machine rather than the codec.

**Should I spoof codec support?** Match it to the build and platform you claim. Do not
randomise it, and do not let it vary between page loads.

**Why do Linux and Windows builds differ?** Because some decoding is delegated to the
platform, and the platforms provide different things.

**Does `privacy.resistFingerprinting` cover this?** Firefox has worked on standardising
these answers under that mode, which is one more way of being identifiable as someone
running that mode. There is
[a longer page on that trade](resist-fingerprinting.md).

**Is this checked in practice?** It is cheap enough that assuming it is checked costs you
nothing, and it is one `await` to verify on your own setup.

**See also:** [screen size and viewport tells](screen-size-headless-tells.md),
[hardwareConcurrency, deviceMemory and storage quota](hardware-concurrency-device-memory.md)
and [Playwright in Docker](playwright-docker-detection.md), which are the rest of the
"what the machine says about itself" family.

## Sources

- MDN's references for `HTMLMediaElement.canPlayType` and the Media Capabilities API.
- Firefox's own tracking of standardising these answers under resist-fingerprinting.
- This project's codec profile, sampled jointly with the machine class rather than
  independently, for the reason given above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The joint sampling is the same decision made for
audio, storage quota and antialiasing, and for the same reason: independently sampled
dimensions contradict each other.*
