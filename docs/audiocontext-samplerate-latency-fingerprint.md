---
title: "AudioContext sampleRate and latency as a fingerprint"
description: "AudioContext.sampleRate, outputLatency and maxChannelCount are read from the audio driver, so a headless server leaks. How a seeded browser keeps them coherent."
parent: "Canvas, WebGL, Fonts and Audio"
grand_parent: "Guides"
nav_order: 13
---


# AudioContext sampleRate and latency as a fingerprint

An `AudioContext` fingerprints a machine before it renders a single sample: `sampleRate`,
`outputLatency` and `destination.maxChannelCount` are read straight from the operating
system's audio driver, so a headless server with no sound hardware answers them with
tell-tale defaults. The defence is not to spoof each number, but to return all three as
one coherent device profile that a real machine could actually have.

Most writing about audio fingerprinting is about the rendered buffer: you run an
`OfflineAudioContext`, hash the samples it produces, and treat that hash as the signal.
That is a real surface and it has [its own page here](audiocontext-fingerprinting.md).

This page is about the other half, the part that needs no rendering at all. Before a
single sample is generated, an `AudioContext` already exposes three plain numbers that
come straight from the operating system's audio stack: `sampleRate`, `outputLatency`,
and `destination.maxChannelCount`. They are cheaper to read than any buffer hash, they
are stable within a session, and on a headless server they are one of the clearest
tells that no real sound hardware is present.

## The three numbers a real audio stack exposes

Open an `AudioContext` on a normal desktop and read four fields:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    profile = page.evaluate("""() => {
        const ctx = new AudioContext();
        const out = {
            sampleRate:      ctx.sampleRate,
            outputLatency:   ctx.outputLatency,
            baseLatency:     ctx.baseLatency,
            maxChannelCount: ctx.destination.maxChannelCount,
        };
        ctx.close();
        return out;
    }""")
    print(profile)
```

Each of those fields is a question to the driver, not to the browser:

- **`sampleRate`** is the rate the audio device is running at, almost always `44100` or
  `48000` Hz. It is decided by the sound card and its driver, not by the page.
- **`outputLatency`** is roughly how long it takes a sample to travel from the graph to
  the speaker. It reflects the buffer size the driver negotiated, and it differs between
  onboard audio, a USB interface, and a surround setup.
- **`maxChannelCount`** on the destination node is how many output channels the device
  can drive: `2` for ordinary stereo, `6` for a 5.1 setup, `8` for 7.1.

None of these is an automation flag. They are hardware facts, and that is exactly what
makes them useful to a detector: JavaScript cannot invent a sound card it does not have.

## Why the values have to agree with each other

The trap here is not any single value being unusual. Every one of these numbers, taken
alone, is common. The signal is in the combination.

Real devices come in a small number of shapes. Onboard laptop audio tends to run stereo
at 48000 Hz with a modest latency. A cheap USB headset might report 44100 Hz. A discrete
sound card feeding a 5.1 receiver reports six channels and a latency in a range that
matches a card built for that job. What you never see on real hardware is the
cross-product: a `maxChannelCount` of 6 paired with the sample rate and latency of a
thin laptop codec, because no such device exists.

That cross-value contradiction is precisely what a consistency-minded detector looks
for, and it is the same class of check that catches a Windows user agent over a Linux
font set, or [hardware concurrency and device memory](hardware-concurrency-device-memory.md)
that pair oddly with the machine being claimed. A fingerprint is not a list of values,
it is a set of relationships, and the relationships are where a naive spoof falls apart.

## What a headless container reports instead

Run the same three reads inside a server container with no sound hardware and you get one
of two failure shapes, both of them loud.

The first is a null audio backend: the context comes back with a default sample rate and
a latency that is suspiciously round or zero, the same on every container image, because
there is no driver underneath negotiating anything. A value that is byte-identical across
thousands of visitors is not neutral, it is a cohort.

The second is worse. Some headless setups fail to construct the context at all, or
suspend it immediately, so the page sees a `state` that never reaches `running`. As with
every other surface, [an empty or blocked result is itself a signal](how-to-test-bot-detection.md),
not a clean pass. A detector that records suppression by name counts the absence against
you exactly as it would count a wrong value.

This is the same reason [an empty speech-voice list](speech-synthesis-voices.md) gives a
server away: both audio output and installed voices are reads into the operating system,
and a datacenter answers them with defaults that say "datacenter".

## How this browser keeps the profile coherent

The fix is not to pick three plausible numbers independently, because independent draws
are what produce the impossible device. The engine returns all three together as one
coherent profile, chosen for the session from a small set of real device archetypes, so
the values always describe a machine that could actually exist:

| Device archetype | `sampleRate` | `outputLatency` | `maxChannelCount` |
|---|---|---|---|
| Plain stereo desktop | 44100 Hz | around 40 ms | 2 |
| USB audio interface | 48000 Hz | lower, near 30 ms | 2 |
| 5.1-capable sound card | 48000 Hz | near 40 ms | 6 |

Each row is a machine that exists; the impossible combinations between rows are the ones a
detector is looking for.

Because the whole profile is keyed off the session seed, a six-channel destination never
lands next to a laptop's latency, and the same seed reproduces the same audio device on
every run. The three fields move as a unit, which is the property the detector is testing
for. This is the general principle these patches follow: the goal is to look like a real
machine, not merely to avoid an obvious default, and a suppressed or arbitrary value
fails that goal as surely as a wrong one.

## Measuring it yourself with a fixed seed

The point of a seed is that a finding is reproducible. Read the profile under two seeds
and confirm two things at once: that each profile is internally coherent, and that the
same seed returns the same device every time.

```python
from invisible_playwright import InvisiblePlaywright

READ = """() => {
    const ctx = new AudioContext();
    const out = [ctx.sampleRate, ctx.destination.maxChannelCount,
                 Math.round((ctx.outputLatency || 0) * 1000)];
    ctx.close();
    return out;
}"""

for seed in (42, 1337):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        rate, channels, latency_ms = page.evaluate(READ)
        print(f"seed={seed}: {rate} Hz, {channels} ch, ~{latency_ms} ms")
```

Two checks turn this into evidence rather than a single reading:

1. **Run each seed twice.** The tuple must be identical across runs of the same seed. If
   it drifts, something is randomising per call, which is
   [the cheapest tampering check a detector has](audiocontext-fingerprinting.md).
2. **Sanity-check the pairing by hand.** Six channels should never appear beside a
   latency that belongs to onboard stereo. If it does, you have found the exact
   contradiction this section exists to prevent.

Compare the output to a stock Firefox on the same machine and you will see the shape of
the guarantee: the stock browser reports your real card, the seeded browser reports a
coherent invented one, and neither reports the round, hardware-free defaults a bare
container does. If you need a specific device rather than a seed-derived one,
[pinning a field](pinning.md) leaves the rest of the identity untouched.

## Conclusion

`sampleRate`, `outputLatency` and `maxChannelCount` are read straight from the OS audio
driver, which means a headless server has nothing truthful to say through them and a
careless spoof says something impossible. The buffer hash gets all the attention, but
these three static numbers are read first and cost nothing to check. Treating them as one
coherent device profile, reproducible from a seed, is what keeps a six-channel count from
ever sitting next to a laptop's latency, and that pairing is the thing a consistency check
is actually built to find.

## Short answers to the questions that lead here

**What is AudioContext.sampleRate and can I change it from JavaScript?** It is the rate
the audio device runs at, `44100` or `48000` Hz, read from the driver. Page JavaScript
cannot change it truthfully; only the engine underneath can decide what the read returns.

**Why does my headless server report the same audio values on every run?** Because there
is no sound hardware, so the context falls back to a fixed default. Identical values
across every container are a cohort, not a clean fingerprint.

**Is outputLatency a reliable fingerprinting signal?** On its own, weakly. In combination
with sample rate and channel count it is strong, because only certain triples correspond
to devices that actually exist.

**What does maxChannelCount reveal?** How many output channels the device can drive: 2 for
stereo, 6 for 5.1, 8 for 7.1. A value that contradicts the latency and rate is the tell.

**How do I make these values consistent with each other?** Do not draw them
independently. Pick a real device archetype and take all three from it, which is what a
seed-derived profile does automatically.

**Will an empty or suspended AudioContext pass a detector?** No. A blocked or suppressed
read is recorded as its own signal, so an absent value fails the same way a wrong one
does.

## Sources

- W3C Web Audio API specification, which defines `sampleRate`, `baseLatency` and
  `outputLatency` on `AudioContext` and `maxChannelCount` on the destination node:
  [www.w3.org/TR/webaudio/](https://www.w3.org/TR/webaudio/).
- MDN Web Docs on the same properties, read from the engine rather than from any single
  detector's rendering of them:
  [AudioContext.sampleRate](https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/sampleRate),
  [AudioContext.outputLatency](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/outputLatency),
  and [AudioDestinationNode.maxChannelCount](https://developer.mozilla.org/en-US/docs/Web/API/AudioDestinationNode/maxChannelCount).
- This project's own audio patches and their release notes, which choose the three static
  audio properties together as one coherent device profile so the values never contradict
  each other.

**See also:** [AudioContext fingerprinting](audiocontext-fingerprinting.md) for the
rendered-buffer side of the same surface, [why getVoices() comes back empty](speech-synthesis-voices.md)
for the other operating-system read that gives a server away, and
[the checklist for being detected on one site](playwright-detected-as-bot.md) for where
the audio device sits in the order you should debug.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The audio device is one of
the machine tells no page-level plugin can reach, which is why it is solved in the engine.*
