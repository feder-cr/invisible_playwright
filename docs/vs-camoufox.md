---
title: "invisible_playwright vs Camoufox: two patched Firefoxes"
description: "invisible_playwright vs Camoufox: both patch Firefox in C++. Compare how each builds its fingerprint, the surfaces each covers, and whether a run replays."
parent: "Comparisons"
nav_order: 4
---


# invisible_playwright vs Camoufox: two patched Firefoxes

invisible_playwright and Camoufox both compile Firefox from source and set the fingerprint
in C++, so neither is stealthier in the abstract. The real difference is how each produces
the identity: Camoufox rotates a fresh, plausible device per session, while
invisible_playwright derives every field deterministically from one seed, so a failing run
can be replayed exactly. Camoufox currently covers more surfaces, notably geolocation and
`Intl`.

Most comparisons in this space are between things that are not comparable: a page-level
script against a patched binary, or a Chromium tool against a Firefox one. This one is
not. Camoufox and this project take the **same approach**, on the **same engine**, for the
same reason.

So the useful question is not which is stealthier in the abstract. It is what each one
does differently, where each is ahead, and which properties matter for your job.

This page is written from Camoufox's own documentation and from ours. Where I could not
verify something about their implementation, I say so rather than guessing.

## invisible_playwright vs Camoufox at a glance

Both patch the same engine; the table below is where they diverge. Each row is expanded in
its own section below.

| Property | invisible_playwright | Camoufox |
|---|---|---|
| Approach | Compiles Firefox from source, patches the C++ | Compiles Firefox from source, patches the C++ |
| Identity model | Derived deterministically from one seed | Rotated per session from a real-world distribution |
| Optimises for | Reproducibility (replay a failing run) | Variety (a fresh device each time) |
| Replay a failing run exactly | Yes, rerun the seed | Not the design goal |
| Geolocation spoofing | No | Yes (documented) |
| Non-English `Intl` correctness | Known weakness, not closed | Documented `Intl` spoofing |
| Fonts | Real Windows fonts bundled in the binary, host-independent | Spoofing and masking of the host's fonts |
| Works with existing Playwright code | Yes | Yes |

## What the two have in common

Enough that the shared part is worth stating once.

Both **compile Firefox from source and modify the C++**, rather than injecting JavaScript
into the page. That single decision is what separates either of them from the page-level
tools, and the reasons are the same in both cases:
[an override is a function whose source can be printed](tostring-native-code-detection.md),
[a worker asks the browser rather than your patch](web-workers-fingerprint.md), and
[an injected script has to win a race it does not always win](navigator-webdriver-explained.md).

Both **work with existing Playwright code**, so switching is a launch change rather than a
rewrite.

Both cover broadly the same surfaces: navigator, screen and viewport, WebGL parameters and
extensions, canvas, fonts, audio, WebRTC, timezone and locale.

Both are **Firefox**, which means both carry
[the same market-share cost](firefox-vs-chromium-antidetect.md) and both avoid
[the Chromium-is-not-Chrome problem](chromium-is-not-chrome.md) entirely.

If you are choosing between these two and a page-level tool, that choice is already made.
The rest of this page is about a narrower question.

## Where the identity comes from

This is the real architectural difference and everything else follows from it.

**Camoufox** rotates device characteristics using a generator that matches real-world
traffic distributions, per its own description. The emphasis is on producing a fresh,
plausible device each time, drawn from how devices are actually distributed.

**This project** derives every field from a single integer. One seed produces one machine:
the same GPU, the same canvas hash, the same audio profile, the same fonts, every run,
forever. The sampler is conditioned across dimensions so the values agree with each other
rather than being drawn independently.

Neither is better in the abstract. They optimise for different things:

- **Rotation optimises for variety.** A fresh device per session, and no work needed to
  get one.
- **Derivation optimises for reproducibility.** A failing run can be replayed exactly,
  which is the difference between bisecting a problem and guessing at it.

That second property is why this project is built that way, and it is worth being
concrete about what it buys. When a run fails, you rerun the seed and get the identical
browser. When you need a hundred identities, you use a hundred integers and each one is a
stable, returnable machine. When you pair a profile with an identity,
[the seed and the directory stay married](persistent-profiles.md).

If your work is one-shot and volume matters more than replay, that property is worth
nothing to you and rotation is the simpler answer.

## Where Camoufox covers more

Being straight about this, because a comparison that only lists your own advantages is
marketing.

Camoufox's documented surface list includes **geolocation spoofing** and explicit **`Intl`
spoofing**. This project does not do geolocation, and its locale handling has a known
weakness on the `Intl` side that we have not closed.

So on breadth of surface, on the evidence of what each documents, Camoufox covers more.
If your target reads geolocation, that difference decides it.

It also ships its own fingerprint generation as a first-class, separately usable
component, which is a real engineering asset and a reason people build on it.

## Where the approaches differ on fonts

Both do something about fonts, and the mechanisms are not the same.

Camoufox describes font spoofing and anti-fingerprinting, which is a filtering and
masking approach applied to what the host has.

This project **ships real Windows fonts inside the binary** and reads only from that
bundle, so the font list and the rendered glyph metrics are the same on a Linux container
and on a Windows desktop. It is a heavier binary in exchange for a font surface that does
not depend on the host at all.

[Why fonts are a comparison rather than a count](headless-fonts-differ.md) explains why
this surface is worth the weight either way.

## Where I could not verify a difference

Two areas where our documentation says something and I could not confirm the equivalent
on their side, so I am not claiming an advantage.

**Pointer event fields.** Both projects advertise human-like mouse movement. This project
sets `pointerType`, `pressure` and the trusted flag in the browser's own input handling,
so the events carry what a real device produces rather than what a synthesiser guessed,
which is [a different thing from drawing a good curve](human-mouse-movement.md). Whether
Camoufox does the same at that level is not something their front page states and I have
not read their source for it.

**The automation layer's own artefacts.** We found and fixed four leaks in the driver
rather than the engine: JIT-disabling timing, driver frames in stack traces, evaluated
code labelled as debugger evaluation, and a helper running in the page realm before the
page. [Those are here](debugger-timing-detection.md). Any Playwright-driven Firefox has
the same layer and therefore the same starting position, and I do not know which of them
they have addressed.

If you are choosing seriously, those are the two things worth testing yourself rather than
reading about.
[The method for testing them](how-to-test-bot-detection.md) is the same for both.

## How to actually choose

Working from the above rather than from claims:

- **Need geolocation, or non-English `Intl` correctness?** Camoufox, today.
- **Need to replay a failing run exactly?** A seeded derivation gives you that by
  construction.
- **Need a font surface that is identical on a container and a desktop?** The bundled
  approach does that at the cost of binary size.
- **Need Chromium?** Neither. Both are Firefox and
  [the engine question is separate](firefox-vs-chromium-antidetect.md).
- **Undecided?** Run both against your actual target, ten times each, and compare the
  reports rather than the verdicts. Two hours of that beats any comparison page,
  including this one.

## Conclusion

These two tools agree about the hard part. Patching the engine rather than the page is
the expensive, correct decision, and both made it.

What is left is a genuine difference in philosophy: rotate a plausible device, or derive
one reproducibly from a number. Plus a real difference in coverage, currently in their
favour on geolocation and `Intl`, and a real difference in how fonts are handled.

Pick on the property your work needs. And whichever you pick, the machine you run it on
still answers for its own GPU, and neither project changes that.

## Short answers to the questions that lead here

**Is Camoufox or invisible_playwright better?** They take the same approach on the same
engine. Camoufox currently covers more surfaces; this project derives everything from a
seed so a run can be replayed exactly.

**Do both patch Firefox at the C++ level?** Yes. That is the shared decision and the thing
that separates both from page-level tools.

**Can I use my existing Playwright code with either?** Yes, both are launch-level changes.

**Which has better fingerprints?** They are produced differently rather than better: one
rotates from a real-world distribution, one derives deterministically from a seed with the
dimensions conditioned on each other.

**Does either fix my container's missing GPU?** No.
[Nothing at the browser level does](playwright-docker-detection.md).

**Should I run both?** Never at the same time. Two components deciding the same values
contradict each other, which is worse than either alone.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
for why both of these sit at the same level,
[Firefox or Chromium for anti-detect](firefox-vs-chromium-antidetect.md), for the cost
they share, and [how to test whether your browser is detected](how-to-test-bot-detection.md),
for how to settle this yourself.

## Sources

- Camoufox's own documentation for its stated mechanism and surface list, read
  2026-07-28.
- [MDN's `Intl` reference](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl)
  and the [MDN Geolocation API overview](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation_API),
  for what each documented surface actually covers on the web-platform side.
- [MDN's `PointerEvent.pointerType`](https://developer.mozilla.org/en-US/docs/Web/API/PointerEvent/pointerType),
  [`PointerEvent.pressure`](https://developer.mozilla.org/en-US/docs/Web/API/PointerEvent/pressure) and
  [`Event.isTrusted`](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted) references, for
  what the input event fields this project sets are specified to carry.
- This project's patch catalogue for the seeded sampler, the bundled font architecture,
  the input event fields and the four automation-layer fixes.
- Where a claim about their implementation could not be checked against their source, the
  page says so instead of asserting it.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Camoufox got here first and the section where it
covers more is the one we would want a comparison of ours to include.*
