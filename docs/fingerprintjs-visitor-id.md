---
title: "Why a FingerprintJS visitor ID changes"
description: "A FingerprintJS visitor ID is a hash of 41 components. Everything about why it changes, or why it stays the same, follows from that one fact."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 4
---


# Why a FingerprintJS visitor ID changes

People run FingerprintJS, get a `visitorId`, run it again a week later and get a
different one. Or they run it in two tabs and get the same one and assume it is
tracking something durable. Both reactions come from not knowing how the value is
made.

It is a hash. Everything else follows from that one fact.

Read from source on 2026-07-27 (`fingerprintjs/fingerprintjs`, `src/sources/` and
`src/agent.ts`).

## How the ID is produced

```ts
export function hashComponents(components: UnknownComponents): string {
  return x64hash128(componentsToCanonicalString(components))
}
```

Roughly forty-one component sources are collected, serialised in a canonical order,
and hashed. The identifier is that hash.

So there is no notion of a partial match. **Change one component and the entire ID
changes**, exactly as if everything had changed. There is a separate `confidence`
score, but the ID itself is all or nothing.

## The components, and which of them move

The full set, from the source directory:

```
canvas          webgl            audio             audio_base_latency
fonts           font_preferences  screen_resolution screen_frame
color_depth     color_gamut       hdr              contrast
inverted_colors forced_colors     reduced_motion   reduced_transparency
monochrome      timezone          date_time_locale languages
platform        os_cpu            architecture     cpu_class
hardware_concurrency  device_memory  touch_support  vendor
vendor_flavors  user_agent_data   apple_pay        pdf_viewer_enabled
plugins         dom_blockers      indexed_db       local_storage
session_storage open_database     cookies_enabled  math
private_click_measurement
```

Group them by how likely they are to move under a real person, and the instability
explains itself:

**Moves whenever the hardware setup changes.** `screen_resolution` and
`screen_frame` change when a laptop is docked, an external monitor is plugged in, or
the taskbar moves. [`device_memory`, `hardware_concurrency`](hardware-concurrency-device-memory.md)
and `architecture` change with the machine.

**Moves with the operating system's own settings.** `forced_colors`,
`inverted_colors`, `reduced_motion`, `reduced_transparency`, `contrast`,
`monochrome`, `color_gamut` and `hdr` all read accessibility and display
preferences. Someone turning on dark mode or reducing motion changes their ID.

**Moves with ordinary software changes.** `fonts` and `font_preferences` change when
any application installs a font. `plugins`, `vendor_flavors` and `user_agent_data`
change when the browser updates. `dom_blockers` changes when someone installs or
configures an ad blocker.

**Moves with travel.** `timezone`, `date_time_locale`, `languages`.

That is why a real user's ID is not stable across weeks. It is not a flaw in the
library, it is what hashing forty-one volatile things produces.

## What this means if you are automating

Two things, and they point in opposite directions from what people assume.

**An ID that changes between runs of your own tool is a signal, not safety.** If your
setup produces a different visitor ID on every launch of the same configuration, some
component is being randomised per call, not per identity. Real machines do not
do that. A tool adding canvas noise on every read, instead of deriving stable noise
from a fixed seed, produces exactly this and is detectable by the instability itself.

**An ID that is stable per identity and different per identity is the target.** Not a
stable ID across all your sessions, which is one machine doing everything, and not a
new ID every request, which is a machine that transforms between page loads. One
consistent machine per identity, for as long as that identity lives.

Deriving the fingerprint from a seed is how you get that. The same seed
gives the same forty-one components, so the same hash, run after run; a different
seed gives a different hash that is equally ordinary. You can verify it in a minute:
run FingerprintJS twice with one seed and confirm the ID matches, then change the
seed and confirm it does not.

## The Pro version is a different thing

The open-source library computes the hash in the browser. Fingerprint Pro adds
server-side signals and identification that this article does not cover, and it
deliberately survives some of the changes listed above instead of breaking on them.

So "I pass the open-source one" and "I pass the commercial one" are separate claims,
and the second does not follow from the first. Anyone telling you a tool defeats
browser fingerprinting, without saying which of the two they measured, has not told
you anything.

## Checking your own

Run it twice in the same configuration and compare. Then run it in a fresh profile
with the same configuration and compare again. Then change one thing you would expect
to matter, the seed if you have one, and confirm the ID moves.

If it moves when it should not, read the components, not the ID: the library
exposes them, and the one that differs between two runs is your problem.

## Short answers to the questions that lead here

**Why does my FingerprintJS visitor ID keep changing?** Because it is a hash of many
components, so any one of them moving changes the whole value. It is not tracking you
less, it is being fed different input.

**Is a changing ID good for privacy?** Less than it sounds. An ID that changes every
single request describes a machine that transforms between page loads, which no real
machine does, and that pattern is itself detectable.

**What makes the ID stable?** The same components answering the same way every time.
Deriving them from a fixed seed does that by construction.

**Does the open-source version equal the commercial one?** No. The commercial one adds
signals the library does not have and deliberately survives some of the changes that
break the open-source ID.

**Does canvas noise help?** Only if it is stable per identity. Noise added per call
makes two reads in one session disagree, which is the cheapest tampering check there
is.

**See also:** [which fingerprint fields can be pinned](pinning.md), if you want
specific components held still while the rest stays seed-derived,
[what BotD detects](botd-explained.md), from the same team but aimed at
a different question, and [why headless renders different fonts](headless-fonts-differ.md),
since `fonts` and `font_preferences` are two of the forty-one.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
Both the open-source library and the commercial one are release gates for this
project, and the difference between them cost us enough time to be worth spelling
out.*
