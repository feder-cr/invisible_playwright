---
title: "Canvas fingerprint changes every run: use a seed"
description: "Canvas, WebGL and audio hashes change every Playwright run because each session draws a fresh identity. Pass a fixed seed to make readbacks byte-identical."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 6
---


# Canvas fingerprint changes every run: use a seed

A canvas fingerprint that changes on every Playwright run is not a leak or a bug. Without
a seed, each session is handed a fresh, self-consistent device, so the canvas, WebGL and
audio readbacks are recomputed at every launch. Pass a fixed seed and all three come back
byte-identical, run to run. That single argument is the whole fix.

You open a fingerprinting page twice from the same automation, and the canvas hash
is different each time. The WebGL hash moves too, and so does the audio one. Nothing
in your code changed between the two runs, and yet the machine looks like a different
machine.

This is one of the most common confusions people hit with a fingerprint-spoofing
browser, and it is almost always a misread rather than a bug. A hash that changes on
every run is not noise leaking into your readback. It is a fresh identity being drawn
for each session, exactly as designed, and the fix is one argument.

## What "changes every run" actually means

Canvas, WebGL and audio are the three surfaces that read back a rendered value: you
draw something, then read the pixels or the samples out and hash them. On a real
machine that hash is stable, because the same GPU, the same font stack and the same
audio pipeline draw the same thing every time.

In this browser, those three readbacks are overwritten before they reach your code.
The value you read is a pure function of one number: an internal hardware seed the
wrapper derives from the identity seed of the session. Same seed in, same bytes out.
Different seed, different bytes.

So when you launch without passing a seed, each session gets a new one, and the three
hashes move together in a self-consistent way. That is not the spoof failing. That is
the spoof drawing a new device for you. The trouble is that a device which looks
brand new on every visit is its own signal, and it is usually not the signal you want.

## Why a per-run identity reads as a rotating device

A stable fingerprint is not automatically a suspicious one. What draws attention is a
fingerprint that behaves in a way real hardware cannot.

Real hardware does not change its canvas hash between two visits. If a site, or a
linkability service like [FingerprintJS, which hands you a visitor ID](fingerprintjs-visitor-id.md),
sees the same cookie or the same login return with a different canvas hash, a
different WebGL renderer and a different audio signature every time, that is a machine
that is repainting itself. Very few legitimate users look like that, and a scorer that
watches for churn will notice.

So the per-session default is the right behaviour for one job and the wrong one for
another:

- **You want a fresh identity per session.** Many short, unrelated visits, nothing
  that should be linked, no returning account. Launch with no seed and take a new
  device each time. This is the default for a reason.
- **You want the same identity every session.** A returning account, a persisted
  profile, or simply a bug you are trying to reproduce. Here a rotating device is a
  liability, and you pin it with a seed.

The mistake is not the rotation. The mistake is leaving it on when the situation calls
for a stable machine.

## Pin the seed and the readbacks stop moving

Pass a seed explicitly and every field it implies comes back identical: the GPU
string, the canvas hash, the WebGL hash, the audio context, the fonts, the screen.
The browser object you get back is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so the rest of your
code does not change.

```python
from invisible_playwright import InvisiblePlaywright

# Same seed -> same canvas / WebGL / audio hash, every run.
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    # read your fingerprint page here; the readbacks are now stable
```

Run that twice and the three readbacks are byte-identical between the two runs,
because they are computed from the same seed both times. Change the number and you get
a different, equally self-consistent machine:

```python
# A different but still internally coherent device.
with InvisiblePlaywright(seed=1001) as browser:
    ...
```

The async surface is the same, one keyword apart:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
```

If you launched without a seed and now want to keep the identity you happened to get,
you do not have to guess it. Every session records the seed it was generated from, so
log it once and you can replay that exact machine later:

```python
sf = InvisiblePlaywright()          # no seed -> a fresh one is drawn
with sf as browser:
    print("seed =", sf.seed)        # write this down to reproduce this device
    page = browser.new_page()
    page.goto("https://example.com")
```

## Prove it: read the same hash twice

Do not take the determinism on faith. The whole point of the earlier troubleshooting
pages is that you [assert the presence of the right value rather than the absence of a
wrong one](how-to-test-bot-detection.md), and this is a case where you can measure the
right value directly.

Draw to a canvas, read it back with
[`toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL),
and compare the hash across two launches with the same seed:

```python
import hashlib
from invisible_playwright import InvisiblePlaywright

DRAW = """() => {
  const c = document.createElement('canvas');
  c.width = 300; c.height = 150;
  const ctx = c.getContext('2d');
  ctx.textBaseline = 'top';
  ctx.font = '16px sans-serif';
  ctx.fillStyle = '#069';
  ctx.fillText('fingerprint probe 12345', 10, 10);
  return c.toDataURL();
}"""

def canvas_hash(seed):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        data_url = page.evaluate(DRAW)
    return hashlib.sha256(data_url.encode()).hexdigest()

a = canvas_hash(42)
b = canvas_hash(42)
c = canvas_hash(1001)

print("same seed match:", a == b)     # True: byte-identical across runs
print("different seed differs:", a != c)  # True: a different device
```

The first comparison is the one that matters. `a == b` being `True` is the readback
proving it is a stable function of the seed, not per-call randomness. This is exactly
what the project's own consistency gate checks on every release: it launches the same
seed more than once and requires an identical visitor ID from a linkability library,
and it fails the build if the two runs disagree. A green gate there means the same seed
is the same machine, run to run.

Because the readback is a function of the seed and not of the host, the same seed also
holds its hash across operating systems, which is
[why a Linux container and a Windows laptop can return the same canvas](canvas-webgl-cross-platform-consistency.md).
If instead the two hashes for one seed disagree within a single machine, that is the
tampering signal to chase, and reading the same value twice in one session is
[the cheapest such check there is](canvas-fingerprint-noise.md).

## Match the seed to the profile

Pinning the seed and pinning the profile are two halves of one decision. If you keep a
[persistent profile so cookies and storage survive between runs](persistent-profiles.md),
give it a stable seed as well. A profile that returns with the same cookies but a
different canvas hash is telling two stories at once: the account remembers you, the
hardware does not, and that contradiction is easier to spot than either half alone.

The clean pairings are the obvious ones. A fresh throwaway profile takes a fresh seed.
A durable identity that comes back tomorrow takes the same profile and the same seed
it had yesterday. Keep the two in step and the machine you present stays coherent over
its whole life instead of only within a single launch.

## Conclusion

A canvas hash that changes on every run is not a leak, it is a new device being drawn
per session, and it is the correct default for unrelated one-off visits. When you need
a machine that stays the same, whether for a returning account, a persisted profile or
a reproducible bug, pass a seed. The three readbacks then become a byte-identical
function of that seed, run to run and across operating systems, and the account and the
hardware finally tell the same story.

## Short answers to the questions that lead here

**Why does my canvas fingerprint change every run?** Because each session without a
seed gets a fresh, self-consistent identity by design. The readback is a function of
the session's seed, and a new session means a new seed.

**How do I make the canvas hash stable?** Pass a fixed seed at launch, for example
`InvisiblePlaywright(seed=42)`. The canvas, WebGL and audio readbacks then come back
identical on every run.

**Is a changing fingerprint a bug?** No. It is deliberate rotation. It becomes a
problem only when you needed a stable machine, for instance behind a returning account
or a persisted profile, and left rotation on.

**Does the same seed give the same hash on a different machine?** Yes. The readback is
computed from the seed, not from the host GPU or fonts, so the same seed holds its
hashes across operating systems.

**How do I reproduce the exact identity from an earlier run?** Read the session's seed
after launch and log it. Passing that number back reproduces the same device.

**Should the seed match my persistent profile?** Yes. A profile that returns with the
same cookies but a different fingerprint is a contradiction. Keep one stable seed per
durable identity.

## Sources

- The project's fingerprint generator, in which the canvas, WebGL and audio readbacks
  are computed as a pure function of a hardware seed derived from the session seed.
- The release consistency gate, which relaunches a fixed seed and requires an
  identical visitor ID from run to run, and fails the build otherwise.
- The [quickstart](quickstart.md) and [configuration](configuration.md) pages in this
  documentation set for the seed argument and the API it belongs to.
- Playwright's own [`Browser` API reference](https://playwright.dev/python/docs/api/class-browser)
  for what `new_page` returns: the object every example above drives is the standard
  Playwright API, not a fork of it.
- MDN's [`HTMLCanvasElement.toDataURL()`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/toDataURL)
  reference for the readback method the proof above uses to pull bytes off the canvas.

**See also:** [how to test whether your browser is detected](how-to-test-bot-detection.md)
for the assert-the-right-value method, and
[the checklist for being detected on one site](playwright-detected-as-bot.md), whose
last step is to pin the identity so a failure is reproducible.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The seed is the same
lever whether you are hiding rotation or reproducing a bug: pin it and the machine
stops moving.*
