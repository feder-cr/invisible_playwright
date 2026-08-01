---
title: "AudioContext fingerprinting, and why adding noise backfired"
description: "Audio fingerprinting is usually beaten by adding noise. We shipped that defence, measured it, and found it made sessions easier to detect, not harder. What the seven audio values are and why noise fails."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 8
---


# AudioContext fingerprinting, and why adding noise backfired

Audio fingerprinting has a reputation for being hard to defeat. It deserves it, but
not for the reason usually given.

The usual reason is that the audio pipeline is low-level and mathematically involved.
The real reason is simpler and more annoying: **the obvious defence makes you easier
to detect, not harder.** We shipped that defence, measured it, and turned it off. This
page is what the measurements said.

## What is actually being measured

Almost every guide says "audio fingerprinting hashes how your device processes sound",
which is true and useless. Here is the concrete shape.

A fingerprinting script builds a short offline audio graph, usually an oscillator into
a dynamics compressor, renders it without playing anything, and reads the resulting
samples back:

```js
const ctx = new OfflineAudioContext(1, 44100, 44100);
const osc = ctx.createOscillator();
const comp = ctx.createDynamicsCompressor();
osc.connect(comp); comp.connect(ctx.destination);
osc.start(0); ctx.startRendering();
// then: sum the rendered samples, hash the sum
```

But the rendered buffer is only one of the values collected. The full set is wider
than people expect, and this matters because they have to agree with each other:

| Value | What it is |
|---|---|
| `AudioContext.sampleRate` | 44100 or 48000, from the audio device |
| `AudioContext.outputLatency` | the driver's reported latency |
| `AudioContext.destination.maxChannelCount` | stereo, 5.1, whatever the device has |
| the rendered sample sum | the oscillator-compressor output |
| `AnalyserNode` frequency data sum | the same graph seen through an FFT |
| `AnalyserNode` time domain data sum | the same graph in amplitude |
| `DynamicsCompressorNode.reduction` | one float, the applied gain reduction |

Seven values, one machine. A profile claiming a 5.1 sound card with a laptop's latency
and a rendered buffer that belongs to neither is caught by comparison, and no
individual value in it is unusual.

## The mistake: noise that gets asked about twice

The intuitive defence is the canvas one: add a small amount of noise to the rendered
samples so the hash differs per session. We implemented exactly that, seeded so the
same identity always produced the same audio.

Two things went wrong, and the second one is the interesting one.

### Silence is a trap

[CreepJS](https://abrahamjuliot.github.io/creepjs/) has a check called
`hasFakeAudio`. It does not compare your hash to a database. It builds a buffer that
should be **silent**, renders it, and looks at whether anything is in it.

```js
new OfflineAudioContext(1, 100, 44100)   // 100 samples, oscillator frequency 0
```

Nothing should come out of that. On real hardware nothing does. A tool adding noise to
every rendered buffer puts noise into silence, which is physically impossible, and the
check records a lie.

It also inspects the first 100 samples of a normal buffer and counts distinct values
there, for the same reason: the beginning of a render is predictable, so variation
there is manufactured.

The fix is not to stop adding noise, it is to add it only where noise can legitimately
exist: skip buffers short enough to be a probe, skip the opening samples, and skip
channels that carry no input at all. Noise on a silent channel is not subtle. It is a
claim that your sound card produces sound when it is off.

**The general shape, which is worth more than the specific check:** a detector does not
have to recognise your noise. It only has to ask a question whose correct answer is
*nothing*, and see whether it gets something.

### The defence was the signal

Then the part we did not expect.

With the noise correctly guarded and passing the silence checks, a commercial
fingerprinting service still scored the browser as tampered. We isolated the audio
noise with a kill switch and ran it both ways on the same machine and the same
identity:

| Audio noise | tampering score |
|---|---|
| on | 0.4349 |
| off | 0.0564 |

Nearly an order of magnitude, from one feature. The audio noise was not one
contributor among many, it was the dominant one.

So we ship with it **off**. The un-noised audio output is still host-independent,
because the values around it are set from the seed and the render is deterministic, and
it matches what a real Firefox on that configuration produces. Turning the noise off
did not make sessions linkable: it made them identical to the honest answer, which is
what everything else on that machine is claiming anyway.

That inversion is the lesson worth carrying:

> A randomised value is not automatically safer than a fixed one. If everyone with a
> given browser and OS produces the same audio hash, producing that same hash makes you
> one of them. Producing a unique one makes you the only person on the internet whose
> sound card is a snowflake.

Randomising helps where the honest value identifies your specific machine. It hurts
where the honest value identifies your *class* of machine, because there the crowd is
the disguise.

## Getting the details right, if you do add noise

Two things we got wrong on the first attempt and had to measure our way out of.

**Scale is per-surface.** Frequency data is in decibels, roughly -100 to 0. Time domain
data is amplitude, -1 to 1. The same epsilon on both is either invisible on one and
enormous on the other. We ended up around `1e-4` for frequency, `1e-7` for time domain,
and `1e-5` for the compressor's single reduction float.

**The noise has to be a function of the seed and the position, not of a random number
generator.** If it is random per call, reading the same value twice returns two
different answers, and a script that reads it twice is a script anyone can write. Ours
hashes the seed together with the channel index and the sample position, so the same
identity renders the same buffer forever, and two reads agree.

That second point is the one that catches most tools. It is easy to check and it is
checked.

## What to look at on your own setup

```js
const ctx = new AudioContext();
console.log(ctx.sampleRate, ctx.outputLatency, ctx.destination.maxChannelCount);
ctx.close();
```

- **`sampleRate` is 44100 or 48000.** Anything else is rare enough to be interesting.
- **The three device values describe one plausible device.** A 5.1 channel count with a
  laptop latency is two devices in one answer.
- **`outputLatency` is not zero and not identical across machines.** A container with no
  sound device is a real tell here, in the same way that [a machine with no GPU is one
  for WebGL](webgl-renderer-strings.md).
- **Read the rendered sum twice in one session.** It must match. If it does not, your
  noise is per-call, and that is the single cheapest tampering check there is.
- **Render a silent buffer and confirm it is silent.**

## Short answers to the questions that lead here

**Can I just disable AudioContext?** You can, and then you have a browser without the
Web Audio API, which every real browser has. That is a stronger signal than any hash.

**Do the privacy extensions that randomise audio work?** They stop the hash being
stable, which is what they promise. They also generally randomise per call and put
noise in silence, both of which are detectable in one line. Against tracking, they
help. Against a system asking whether you are tampering, they are the thing it finds.

**Is audio fingerprinting stronger than canvas?** It has less entropy on its own. It is
harder to fake convincingly, because it is seven values that have to agree plus a
render that has to look like it came from the device the other six describe.

**Why does my audio fingerprint differ between headless and headed?** Usually the audio
device. A headless container often has none, so the values fall back to defaults that
say so.

**See also:** [why a FingerprintJS visitor ID changes](fingerprintjs-visitor-id.md),
since the audio hash is one of the components that ID is made of,
[why headless browsers render different fonts](headless-fonts-differ.md)
and [your renderer string says NVIDIA, your pixels say software](renderer-string-vs-render.md),
which are the same "the honest answer describes your machine" problem on other
surfaces, and [how CreepJS decides you are lying](creepjs-explained.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The two scores above are our own A/B, on one
machine and one identity, with the audio noise as the only variable. The feature they
killed was one we wrote.*
