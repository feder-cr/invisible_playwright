---
title: "How CreepJS decides you are lying"
description: "CreepJS mostly asks whether a browser is telling the truth rather than what it reports. Read from its own source, module by module, for the techniques it uses to catch a lie."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 2
---


# How CreepJS decides you are lying

Most fingerprinting tests ask what your browser reports. CreepJS mostly asks whether
your browser is *telling the truth*, which is a different question and a much harder
one to pass by spoofing.

Read from the source on 2026-07-27 (`abrahamjuliot/creepjs`, `src/lies/index.ts` and
the module layout) and not from the rendered page, because the interesting part
is the method instead of the numbers it prints.

## The shape of it

The project is organised into modules that map onto surfaces: `canvas`, `webgl`,
`audio`, `fonts`, `screen`, `timezone`, `intl`, `media`, `speech`, `webrtc`,
`navigator`, `window`, `worker`, `css`, `cssmedia`, `svg`, `domrect`, `math`,
`engine`, `features`.

Then there are four that are not surfaces at all, and they are the point:

- **`lies`**, which detects tampering.
- **`trash`**, which collects values that are junk or impossible.
- **`errors`**, which records what threw when it should not have.
- **`resistance`**, which identifies privacy tooling, including Firefox's own
  `resistFingerprinting` mode.

A high score is not "your fingerprint is rare". It is "nothing here contradicts
anything else here, and nothing looks tampered with".

## How it finds tampering

Four techniques, all visible in `src/lies/index.ts`.

**A pristine reference.** It creates fresh iframes and reaches into
`contentWindow` to get a copy of the built-ins that your page-level patches never
touched, because the patch ran in the parent document. Then it compares. This is the
single most effective move against injected stealth scripts, and it is about fifteen
lines of code.

**Stack-trace inspection.** It matches against `/at Function\.toString /` and
`/at Object\.toString/`. When you replace a native function and then patch
`Function.prototype.toString` to hide it, the way your replacement is called leaves a
different frame in the stack than a genuine native call would. The disguise is
detected by how the disguise itself behaves.

**Descriptor and prototype walking.** For each API it takes
`Object.getOwnPropertyDescriptor(proto, name)`, checks whether `descriptor.value` is
unexpectedly `undefined`, pulls the getter out, and compares
`Object.getPrototypeOf(apiFunction)` against the native prototype. An override moved
from the prototype to the instance, or a getter that is a plain function rather than
a native one, shows up here without any cleverness.

**Blocking the probe is itself recorded.** Among the lie names in the source are
`client blocked phantom iframe` and `client blocked behemoth iframe`. If your
environment prevents CreepJS from creating its reference iframes, that prevention is
written down as a lie.

That last one is the principle worth taking away, and it generalises far beyond this
tool: **suppressing a signal is a signal**. A browser that refuses to answer is not
quieter than one that answers plainly, it is louder, because refusing is rare and
answering is universal.

## Why uniform spoofing scores badly

Consider the two ways people try to pass this.

**Override everything from JavaScript.** Every override is a candidate for all four
techniques above. The more surfaces you patch, the more lie records you generate, and
a page with many detected lies scores worse than one with an unusual but honest
fingerprint. Effort spent hiding actively hurts.

**Turn on maximum privacy protection.** The `resistance` module exists precisely to
identify this. Firefox's `resistFingerprinting` produces a recognisable pattern, and
being identified as a privacy-tool user is its own classification.
[There is a longer page on why that is the wrong tool for automation](resist-fingerprinting.md).

There is a third option that scores well and is much more work: do not lie in
JavaScript at all. If a value is decided below the JavaScript layer, in the engine,
then there is no override to find, no prototype moved, no `toString` to hide, and no
difference between what the page sees and what a fresh iframe sees. The lie detector
finds nothing because nothing lied.

That is not a claim about any particular tool being undetectable. It is a claim about
where the detection pressure lands: on the seam between the patch and the runtime.
Remove the seam and this whole category of check has nothing to bite on. Everything
else, the plausibility of your GPU, your fonts, your timezone against your IP,
remains exactly as hard as it was.

## What one real, unmodified session actually scored

Run directly against a live Playwright-driven Chromium session with no stealth
layer of any kind applied - no injected patches, no engine-level work, nothing
this project or any comparable tool does - CreepJS's own headless module returned
0% headless and 0% stealth, with a 25% "like headless" partial-similarity score
alongside them. Worth stating plainly what that does and does not mean: it says
this one specific, directly-checked session did not trip CreepJS's headless
heuristics that day, not that headless or automation detection in general is
solved, and not that every Playwright launch configuration scores the same way.
The rest of the report - the parts covered throughout this page, the lie
detection, the consistency checks, the machine-level surfaces - is where the
actual work happens regardless of what this one narrow module reports.

## Reading your own report

Two things worth doing when you open it:

1. **Read the lies section before the score.** A number tells you where you landed.
   The lie list tells you what to fix, and it names the API.
2. **Compare the same session twice, and two sessions with the same configuration.**
   Consistency across a reload and across a fresh launch is what a real browser has.
   If your fingerprint moves between reloads, something is adding noise per call
   rather than per identity, and that is its own signature.

## Short answers to the questions that lead here

**What does the CreepJS score actually mean?** Not "your fingerprint is rare". It means
nothing here contradicts anything else here, and nothing looks tampered with.

**Why does it detect my stealth plugin?** Four ways, and each is cheap: a pristine copy
of the built-ins from a fresh iframe, stack-trace inspection, descriptor and prototype
walking, and recording a blocked probe as a lie.

**Should I block CreepJS from creating its iframes?** No. Blocking is written down as a
lie, by name, in its source. Suppressing a signal is a signal.

**Why does more patching score worse?** Because every override is another candidate for
those four techniques. A page with many detected lies scores worse than one with an
unusual but honest fingerprint.

**What passes it?** Not lying in JavaScript at all. If the value is decided below the
JavaScript layer there is no override to find and no seam to detect.

**See also:** [what sannysoft checks](sannysoft-explained.md), whose canvas-in-iframe test is the same idea in miniature, [the three levels](playwright-stealth-levels.md), and [AudioContext fingerprinting](audiocontext-fingerprinting.md), where its silent-buffer check is worked through in detail.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
CreepJS is a release gate here. The lie names above are quoted from its source and
not paraphrased, because the exact strings are what you will see in a report.*
