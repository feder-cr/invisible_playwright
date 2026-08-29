---
title: "navigator.maxTouchPoints and pointer consistency"
description: "Why navigator.maxTouchPoints reads 0 on a spoofed desktop, why the pointer media queries must agree, and how CreepJS cross-checks the pair to catch a spoof."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 18
---


# navigator.maxTouchPoints and pointer consistency

On a spoofed desktop, [`navigator.maxTouchPoints`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/maxTouchPoints) should read `0`, and the CSS
pointer media queries have to agree with it: [`(pointer: fine)`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/pointer) and
`(any-hover: hover)` match, while [`(any-pointer: coarse)`](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/any-pointer) is false. A
fingerprinter does not read either value on its own; it checks whether the two
agree, because they describe the same hardware through different code paths, and
CreepJS records a mismatch as a lie.

`navigator.maxTouchPoints` looks like a throwaway integer. It is one of the
cheapest cross-checks a fingerprinter has, because it does not live alone: it
has to agree with the pointer media queries, and the two are read through
completely different code paths inside the browser. Get them from two different
places and it is very easy to make them disagree.

This page is about that agreement. What the value should be on a desktop, which
media queries it has to match, how to read both from one session, why CreepJS
pairs them, and one boundary value in our own identity generation that once made
them contradict each other on exactly the machines where it mattered.

## Why maxTouchPoints and the pointer media query are one question

A browser exposes its input hardware twice, in two unrelated APIs.

`navigator.maxTouchPoints` is a property on the navigator object: an integer,
the number of simultaneous touch contacts the digitizer supports. A desktop
without a touchscreen reports `0`. A touch laptop reports something like `5` or
`10`.

The CSS pointer media features describe the same hardware to stylesheets.
`(pointer: fine)` means the primary input is precise, like a mouse.
`(pointer: coarse)` means it is imprecise, like a finger. `(any-pointer: coarse)`
means at least one available input is coarse, and `(any-hover: hover)` means at
least one input can hover without committing to a click. A mouse is fine and can
hover; a touchscreen is coarse and cannot.

The two describe one physical fact, so a real machine can only answer in a
handful of consistent ways. A plain desktop says `maxTouchPoints: 0`,
`any-pointer: fine`, `any-hover: hover`, and `any-pointer: coarse` is false. A
touch device says `maxTouchPoints > 0` and `any-pointer: coarse` is true. What
no real machine says is `maxTouchPoints: 0` together with `any-pointer: coarse`,
because a coarse pointer with zero touch points is hardware that does not
exist.

That impossible combination is exactly what a naive spoof produces: force the
navigator integer to `0` and forget that the CSS layer still reports the real
touchscreen underneath.

## What a real Windows desktop actually reports

The claim this whole surface rests on is that the session is a Windows desktop,
so the honest desktop answer is the target on every host, including a Linux
server with no input hardware description worth trusting.

On that profile the correct set is:

- `navigator.maxTouchPoints` is `0`.
- `(pointer: fine)` matches, `(pointer: coarse)` does not.
- `(any-pointer: fine)` matches, `(any-pointer: coarse)` does not.
- `(any-hover: hover)` matches.

invisible_playwright delivers both halves from inside the engine rather than
patching one of them in JavaScript. The navigator integer is forced to the
desktop value, and the pointer media features are answered as fine and hover, so
they come from the same intent even though the browser computes them in
different places. The alternative, patching `maxTouchPoints` from a page script
while leaving the media query on the real digitizer, is the mismatch above and
is trivially detectable.

## Reading both values with invisible_playwright

Read `navigator.maxTouchPoints` and the pointer media queries in a single
`evaluate` call, so you are looking at one consistent snapshot instead of two
separate reads that could drift apart. Switching from stock Playwright is the
usual two lines, and after that the `browser` object is a real Playwright
`Browser`, so `evaluate` works exactly as upstream documents it:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    report = page.evaluate("""() => ({
        maxTouchPoints:    navigator.maxTouchPoints,
        pointerFine:       matchMedia('(pointer: fine)').matches,
        pointerCoarse:     matchMedia('(pointer: coarse)').matches,
        anyPointerFine:    matchMedia('(any-pointer: fine)').matches,
        anyPointerCoarse:  matchMedia('(any-pointer: coarse)').matches,
        anyHover:          matchMedia('(any-hover: hover)').matches,
    })""")

    print(report)
```

On the desktop profile this prints the consistent set, and the coarse queries
are false:

```text
{'maxTouchPoints': 0, 'pointerFine': True, 'pointerCoarse': False,
 'anyPointerFine': True, 'anyPointerCoarse': False, 'anyHover': True}
```

The check that a fingerprinter actually runs is not "is each value plausible"
but "do these agree". You can assert it in one line, and it is a good line to
keep in a smoke test because it fails loudly the moment one half of the pair
drifts:

```python
assert report["maxTouchPoints"] == 0
assert report["anyPointerCoarse"] is False
assert report["anyPointerFine"] and report["anyHover"]
```

Because the identity is seed-derived, the pair is deterministic. Run the same
seed twice, or two different seeds, and the desktop answer is stable rather than
randomised, which is what you want from a value that is supposed to describe
fixed hardware:

```python
from invisible_playwright import InvisiblePlaywright

for seed in (42, 1000, 7777):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto("https://example.com")
        touch = page.evaluate("() => navigator.maxTouchPoints")
        coarse = page.evaluate(
            "() => matchMedia('(any-pointer: coarse)').matches")
        print(seed, touch, coarse)   # every row: 0 False
```

## The consistency CreepJS is checking

[CreepJS](creepjs-explained.md) does not ask whether your touch count is
unusual. It asks whether it contradicts something else you reported, and this
pair is one of its cleaner contradictions because the two values come from
different corners of the browser and a spoof that touches one often forgets the
other.

Concretely, a coarse `any-pointer` next to `maxTouchPoints: 0` is a
self-inconsistency, and CreepJS treats a self-inconsistency as a lie regardless
of how ordinary each individual value looks. This is the same style of check
described in [how detection suites cross-reference fields rather than reading
them](how-to-test-bot-detection.md): the danger is never one odd number, it is
two numbers that cannot both be true.

It is worth reading the pointer pair the way you already read
[the CSS media-query surface](css-media-query-fingerprinting.md) and
[hardware concurrency against device memory](hardware-concurrency-device-memory.md):
none of these are automation flags on their own, they are agreement checks
between values that a real machine derives from one underlying fact.

## The boundary value that once broke both

This section is about a bug we shipped and then fixed, because the shape of it
is instructive and the fix is the interesting part.

Both halves of the pair, the navigator integer and the pointer media features,
are gated on the same internal switch: the seed that drives the session's
fingerprint. When that seed is active, the touch count is forced to the desktop
`0` and the pointer features are answered as fine and hover. When it is not
active, both fall through to the real hardware at once.

For a period, one boundary value in the pool the identity seed is drawn from sat
exactly on the "not active" side of that gate. A small fraction of seeds landed
on it, and for those sessions every part of the spoof that shared the same gate
switched off together: the touch count came back as the real digitizer value,
the pointer media query went coarse to match it, and the audio-side noise went
quiet in the same instant. On a plain server none of that was visible, because
the server has no touchscreen. On a touch-capable Windows host it was very
visible, and it was visible as precisely the contradiction this page is about,
plus [an audio fingerprint that changed shape at the same moment](audiocontext-fingerprinting.md).

The fix had to repair the affected sessions without disturbing any of the others.
A careless change to how identities are generated can shift every other session's
fingerprint as a side effect, which trades a large regression for a small repair.
This one was made so that only the affected sessions changed and every other
identity stayed exactly where it was, and it is now covered by a test that asserts
the gate is active for every identity the generator can produce, so a `0` touch
count can no longer travel with a coarse pointer.

The general lesson survives the specific bug: when many outputs hang off one
switch, the failure is never one field, it is all of them at once, and the
repair has to preserve everything that was already correct.

## Conclusion

`navigator.maxTouchPoints` is not interesting because of its value. It is
interesting because a second API describes the same hardware, and a fingerprinter
compares the two rather than reading either. On a desktop profile the whole pair
has one correct answer: zero touch points, a fine and hovering pointer, no coarse
input anywhere. invisible_playwright answers both from the same intent inside the
engine, so they cannot disagree, and pins them to the seed so they are stable
across runs. Read them together, assert the agreement rather than the individual
numbers, and you close a check that a one-property patch leaves wide open.

## Short answers to the questions that lead here

**What should navigator.maxTouchPoints be for a desktop?** `0`. A machine
without a touchscreen reports zero simultaneous touch contacts.

**Why does maxTouchPoints: 0 with any-pointer: coarse get flagged?** Because it
is hardware that does not exist. A coarse pointer implies touch input, and zero
touch points denies it. The two must agree.

**Which media queries have to match the touch count?** `pointer`, `any-pointer`
and `any-hover`. A desktop is `pointer: fine`, `any-hover: hover`, and
`any-pointer: coarse` is false.

**Can I just override maxTouchPoints from a page script?** You can, and it is
the wrong fix on its own. The pointer media query still reflects the real
hardware, so patching only the navigator integer creates the exact contradiction
detectors look for.

**Does the value change between runs with the same seed?** No. It describes fixed
hardware, so it is deterministic per identity and stable across sessions.

**Why did a small share of sessions once report the real touch count?** One
generated identity disabled the shared gate for those sessions. It was fixed so
that only those sessions changed, and the touch count and the pointer media query
are now forced to agree for every identity.

## Sources

- W3C, [Pointer Events](https://www.w3.org/TR/pointerevents/), retrieved
  2026-08-28, for the specification that extends the `Navigator` interface with
  `maxTouchPoints`.
- W3C, [Media Queries Level 4](https://www.w3.org/TR/mediaqueries-4/), retrieved
  2026-08-28, for the `pointer`, `any-pointer`, `hover` and `any-hover`
  interaction media features this page checks against the touch count.
- [CreepJS](https://github.com/abrahamjuliot/creepjs), retrieved 2026-08-28, read
  from its own source, for how it records a self-inconsistent pair as a lie.
- This project's release gates, including the test that now asserts the shared
  gate is active for every seed the identity pool can produce.

**See also:** [the CSS media-query surface](css-media-query-fingerprinting.md)
for the rest of what stylesheets can read about your machine,
[what CreepJS actually proves](creepjs-explained.md) before you trust a high
score, and [how to compare fields instead of reading verdicts](how-to-test-bot-detection.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The boundary value
in this page is a bug we shipped, found on a touch laptop, and fixed in place.*
