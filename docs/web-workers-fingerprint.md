---
title: "Web Workers: where page-level fingerprint patches fail"
description: "A worker is a separate realm with its own navigator, and OffscreenCanvas lets a page fingerprint the canvas from inside one. Patches applied to the document never run there."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 15
---


# Web Workers: where page-level fingerprint patches fail

If you want to understand why patching the page has a ceiling, workers are the clearest
demonstration available. A worker is a **separate JavaScript realm**. Your init script ran
in the document. The worker did not inherit it, cannot see it, and answers every question
from the browser's own code.

So a page that wants the truth does not have to defeat your patch. It can ask somewhere
your patch never went.

This page is what a worker exposes, the canvas path that bypasses page-level protection
entirely, why this is the sharpest case against that layer, what an engine-level value
does differently, and how to check your own.

## A worker is a separate realm with its own navigator

```js
const w = new Worker(URL.createObjectURL(new Blob([`
  postMessage({
    ua: navigator.userAgent,
    cores: navigator.hardwareConcurrency,
    lang: navigator.language,
    platform: navigator.platform,
    mem: navigator.deviceMemory,
  });
`])));
w.onmessage = e => console.log(e.data);
```

That runs in a context created by the browser, from the browser's own values. If you
redefined `navigator.hardwareConcurrency` on the document, this reports the real number,
and now the page has two different answers to one question.

The comparison is the finding, not either value. An unusual core count is a weak signal. A
browser that reports one number in the document and another in a worker has told the page
that something is rewriting values in one context and not another, which is a tampering
result rather than a hardware result.

### What a worker can read

`WorkerNavigator` is a subset of `Navigator`, and the subset is exactly the part that
matters here: `userAgent`, `platform`, `language` and `languages`,
`hardwareConcurrency`, `deviceMemory` where implemented, `onLine`, and the storage
estimate. Plus `performance.now()` for timing.

That is most of what a cheap fingerprint is made of, available in a place page-level
patches do not reach.

## OffscreenCanvas: the canvas path that bypasses the patch

This is the strongest version of the problem, and it is not theoretical.

`OffscreenCanvas` lets a worker create and draw to a canvas with no DOM element involved,
then read the pixels back:

```js
// inside a worker
const c = new OffscreenCanvas(300, 60);
const ctx = c.getContext('2d');
ctx.font = '14px Arial';
ctx.fillText('abcdefghijklmnop', 2, 20);
const bmp = c.transferToImageBitmap();     // or convertToBlob()
```

Two things follow, and both are documented in the wild:

- **Protection that hooks `HTMLCanvasElement.prototype` misses this entirely**, because no
  `HTMLCanvasElement` is involved. Most bypass and protection scripts hook the element
  methods and not the `OffscreenCanvas` equivalents, so a fingerprint taken this way comes
  back unmodified.
- **It runs off the main thread**, so it does not block anything and there is no timing
  cost to doing it.

Browsers have had to catch up here too. Canvas protections in at least one engine did not
apply inside shared and service workers for a period, and Firefox tracked its own
OffscreenCanvas gap as a bug to be fixed in a specific version. The surface is young
enough that "it is covered" is a claim worth testing rather than assuming.

## Why this is the sharpest case against page-level patching

Every argument on [the three levels page](playwright-stealth-levels.md) applies here, and
this is the example that makes it concrete.

A page-level patch is code running in one realm. Workers are additional realms, created on
demand, as many as the page likes. To cover them a patch would have to:

- intercept worker creation, including from a blob URL and from a data URL,
- inject itself into each one before the worker's own code runs,
- cover `SharedWorker` and [`ServiceWorker`](service-workers-storage-partitioning.md) as
  well as `Worker`,
- and patch the worker-side APIs, including the `OffscreenCanvas` methods, not only the
  document-side ones.

Each of those is possible and each is another surface that can be inspected, which is
[the same treadmill as patching `toString`](tostring-native-code-detection.md).

Meanwhile a value decided in the engine is simply the same value everywhere, because every
realm asks the same compiled code. There is no injection, no ordering, and no list of
contexts to remember.

That is the whole argument for the level, stated once: **not that patching cannot work,
but that it has to be complete, and completeness here is an open-ended list.**

## What an engine-level value does differently

In this project the worker-side navigator values come from the same profile as the
document-side ones, set in the browser's own worker navigator code rather than injected.
So the two answers agree because they are the same answer.

The canvas path is handled the same way: the substitution happens where pixels are read
back inside the engine, so it applies wherever the readback happens, in the document or in
a worker, through an element or through `OffscreenCanvas`.

Being straight about the limit, as everywhere: this makes the values consistent. It does
not make them plausible. A worker reporting the same low core count as the document is
consistent and still describes a small virtual machine, which is
[a different problem](hardware-concurrency-device-memory.md).

## Checking your own

Two comparisons, both short.

```js
// 1. does the worker agree with the document?
const w = new Worker(URL.createObjectURL(new Blob([`
  postMessage([navigator.hardwareConcurrency, navigator.userAgent,
               navigator.platform, navigator.language]);
`])));
w.onmessage = e => console.log('worker:', e.data,
  '\ndocument:', [navigator.hardwareConcurrency, navigator.userAgent,
                  navigator.platform, navigator.language]);
```

```js
// 2. does a canvas drawn in a worker match one drawn in the document?
// draw the same shapes both ways and compare the resulting bytes
```

What you want: identical answers from both places, and a canvas that is treated the same
way in both. A difference in either is not a weak signal, it is a direct statement that
something is intervening.

Then do the same in a stock browser to see what agreement is supposed to look like, which
is [the comparison method](how-to-test-bot-detection.md).

## Conclusion

Workers are not an exotic corner. They are a normal part of the platform, trivial to
create, and they answer from the browser rather than from your patch. `OffscreenCanvas`
extends that to the single most-used fingerprinting surface there is.

If you are evaluating a stealth tool, this is the cheapest meaningful test you can run: ask
it something in the document, ask the same thing in a worker, and see whether the answers
match. If they do not, you have learned where its ceiling is in two lines.

## Short answers to the questions that lead here

**Can a website fingerprint me from a Web Worker?** Yes. `WorkerNavigator` exposes the
user agent, platform, languages, core count and memory, and `OffscreenCanvas` allows
canvas fingerprinting from inside one.

**Do page-level stealth patches cover workers?** Not unless they explicitly intercept
worker creation and inject into each one. Most do not.

**Does OffscreenCanvas bypass canvas protection?** It bypasses protection that hooks the
HTML element's methods, which is most of it.

**Why does my worker report a different value than the page?** Because your override was
applied to the document's realm and the worker has its own.

**Is this a real technique or a theoretical one?** Real. Engines have shipped fixes for
their own gaps in worker canvas protection, which is evidence that it was being used.

**How do I test a stealth tool for this?** Read the same value in both places and compare.
Two lines.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
[Function.prototype.toString and the native code check](tostring-native-code-detection.md),
[canvas fingerprint noise](canvas-fingerprint-noise.md), and
[hardwareConcurrency, deviceMemory and storage quota](hardware-concurrency-device-memory.md),
whose worker check is this idea applied to one value.

## Sources

- MDN for `OffscreenCanvas`, `WorkerNavigator` and the worker types.
- Public reports of canvas protections not applying inside shared and service workers in
  one engine, and Firefox's own tracked OffscreenCanvas gap, for the claim that this is
  actively used.
- This project sets the worker-side navigator values in the engine's worker navigator
  code, and applies canvas substitution at readback rather than at the element.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The two-line worker comparison is the test we would
run first on anything claiming to be undetectable, including ours.*
