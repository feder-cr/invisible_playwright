---
title: "hardwareConcurrency, deviceMemory and storage quota"
description: "hardwareConcurrency, deviceMemory and storage quota are three one-line reads that go wrong on a server, and detectors cross-check them against each other."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 2
---


# hardwareConcurrency, deviceMemory and storage quota

Three numbers the browser reports about the machine, all readable in one line each, all
routinely wrong on a server, and all checked against each other rather than on their
own.

```js
navigator.hardwareConcurrency                 // logical CPU cores
navigator.deviceMemory                        // RAM in GiB, rounded, Chromium only
(await navigator.storage.estimate()).quota    // bytes the origin may store
```

Nobody thinks of the third one as a fingerprinting surface, which is exactly what makes
it useful as one.

## What each one actually reports

**[`hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency)**
is the number of logical processors. On real consumer machines it is almost always a
power of two or a small even number: 4, 8, 12, 16. It is
[readable inside a Web Worker](web-workers-fingerprint.md) as well as on the main
thread, which matters below.

**[`deviceMemory`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/deviceMemory)**
is RAM in gibibytes, deliberately coarse. The
[Device Memory specification](https://www.w3.org/TR/device-memory/) rounds to a small
set of values, so you will see 0.5, 1, 2, 4 and 8, and 8 is the cap. A machine with
64 GB reports 8. It is a Chromium API; Firefox does not implement it, which is itself a
signal in the other direction if something claims Chrome and returns `undefined`.

**[`storage.estimate().quota`](https://developer.mozilla.org/en-US/docs/Web/API/StorageManager/estimate)**
is how much the origin is allowed to store, and browsers derive it from free disk space. That makes it a rough, noisy proxy for the size of the
volume, and it varies enormously between a laptop with a half-full terabyte drive and a
container with a small overlay filesystem.

## The combinations that do not exist

Individually none of these is rare. What gets checked is whether they describe one
machine.

- **Two cores and 8 GB reported.** Possible, uncommon. Two cores with a multi-terabyte
  storage quota and a workstation GPU string is a machine nobody assembled.
- **Sixteen cores and a device memory of 0.5.** Contradictory in the obvious direction.
- **A quota that implies a nearly empty enormous disk**, paired with a low core count
  and a software renderer. That is a description of a cloud volume.
- **A core count that changes between page loads** in the same session. Real hardware
  does not gain cores. This happens when a value is randomised per call rather than
  derived once per identity, which is the same mistake as
  [canvas noise applied per read](canvas-fingerprint-noise.md).
- **The main thread and a worker disagreeing.** `hardwareConcurrency` is readable from
  a `Worker`, and a patch applied only to the page's `navigator` leaves the worker
  answering honestly. That is a two-line check and it catches page-level spoofing
  generally, not only here.

That last one is worth sitting with, because it generalises. Any value you override in
the document can be asked for again in a context your override did not reach: a worker,
a fresh iframe, a service worker. An engine-level value has no such gap, because there
is no override to be missing.

## Why the worker check is the important one

The worker check matters most because it detects tampering rather than unusual hardware.
If the main thread and a worker report different core counts, the page has not learned
your hardware, it has learned that something is rewriting values in one execution context
and not another. That is a stronger and more damning finding than any specific number.

```js
// main thread
navigator.hardwareConcurrency;                       // 8, say

// in a worker
new Worker(URL.createObjectURL(new Blob([
  'postMessage(navigator.hardwareConcurrency)'
]))).onmessage = e => console.log(e.data);           // must also be 8
```

When those two disagree, the mismatch itself is the finding, not the specific core count.

The same shape appears in [what sannysoft checks](sannysoft-explained.md), where three
canvas tests are run in the page and again in an iframe purely to compare them. Ask
twice, from two places.

## What a plausible set looks like

Aim for a machine somebody bought:

- **Cores** 4, 8, 12 or 16. Two is a small VM. Anything above 32 is a workstation and
  should come with everything else that implies.
- **Device memory** 4 or 8 for a modern desktop. Remember it is capped at 8, so 8 means
  "8 or more" and is the common answer.
- **Quota** in the tens or low hundreds of gigabytes for a normal machine with a normal
  amount of free space. Suspiciously round or suspiciously enormous both stand out.
- All three consistent with the GPU, the screen and the platform you claim.
- **Stable.** One identity, one machine, every run.

## How this project handles it

All three come from the same seeded profile as the GPU, the screen and the fonts, drawn
from ranges that go together rather than sampled independently. The storage quota in
particular is conditioned on the class of machine the rest of the profile describes, so
a low-end integrated-graphics identity does not report a workstation's disk.

They are set in the engine, so the worker and the page get the same answer from the
same code path, and there is no context where an override could be missing.

## Checking your own

```js
const q = (await navigator.storage.estimate()).quota;
console.log({
  cores: navigator.hardwareConcurrency,
  memory: navigator.deviceMemory,
  quotaGB: Math.round(q / 1e9),
});
```

Run it twice in one session and confirm the values match. Run it in a worker and
confirm they match again. Then relaunch the same identity and confirm they still match.
Three comparisons, and each one catches a different mistake.

## Short answers to the questions that lead here

**Can `navigator.hardwareConcurrency` be spoofed?** The value can be changed. Whether
the change survives being asked from a worker depends entirely on where you changed it.

**Why is `navigator.deviceMemory` undefined?** Because you are in Firefox or Safari,
which do not implement it. If something claiming to be Chrome returns `undefined`, that
is the contradiction rather than the value.

**Why is `deviceMemory` 8 on a machine with 64 GB?** The specification caps it at 8 and
rounds, deliberately, to reduce its value as an identifier.

**Is `storage.estimate()` used for fingerprinting?** Yes. It approximates free disk
space, which distinguishes a container from a personal machine quite well.

**What core count is safest?** A common one that agrees with the rest of your profile,
held stable. Rarity is the problem, not the number.

**Does randomising these help?** Not if it happens per call. A machine whose core count
changes between two reads is a stronger finding than any specific count.

**See also:** [screen size and viewport tells](screen-size-headless-tells.md) and
[Playwright in Docker](playwright-docker-detection.md), which are the same "what the
machine says about itself" family, and
[why a FingerprintJS visitor ID changes](fingerprintjs-visitor-id.md), since all three
of these are components of it.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The three values are conditioned on each other here,
which is the part that is easy to get wrong when they are sampled independently.*
