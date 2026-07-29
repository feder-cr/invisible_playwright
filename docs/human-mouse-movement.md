---
title: "Human-like mouse movement: Bezier curves are the easy part"
description: "A good Bezier curve is the easy half of human-like mouse movement. The half nobody mentions is that every pointer event carries fields saying where it came from, and a perfect curve can still fail that check."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 5
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Guides",
      "item": "https://feder-cr.github.io/invisible_playwright/guides.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "The Automation Layer",
      "item": "https://feder-cr.github.io/invisible_playwright/guides-automation-layer.html"
    },
    {
      "@type": "ListItem",
      "position": 4,
      "name": "Human-like mouse movement: Bezier curves are the easy part"
    }
  ]
}
</script>

# Human-like mouse movement: Bezier curves are the easy part

Search for this and you get a dozen libraries that draw a nice curve between two
points, with jitter, overshoot and a bell-shaped velocity profile. They are not wrong.
They are solving the visible half of the problem.

The half nobody mentions is that a path is made of events, and an event carries fields
that say where it came from. You can draw a perfect human arc and still deliver it as
a hundred events that no hand ever produced.

## What a real pointer event carries

Move a real mouse and each event arrives with more than coordinates:

| Field | On a real mouse | On a naive synthetic event |
|---|---|---|
| `isTrusted` | `true` | `false` if dispatched from page JavaScript |
| `pointerType` | `"mouse"` | often absent, or `"pen"` / `"touch"` by accident |
| `pressure` | `0` when hovering, `0.5` while a button is down | frequently `0` always, or `1` always |
| `buttons` | a bitmask consistent with the sequence | often inconsistent across down, move and up |
| `movementX` / `movementY` | the delta the OS reported | zero, or recomputed and not matching |
| timestamps | irregular, tied to the input device's rate | perfectly spaced |

A page can read every one of these. Checking `isTrusted` alone is one line, and it
separates "the driver moved the pointer" from "a script pretended to".

## The three layers, and what each can reach

**Page JavaScript.** `element.dispatchEvent(new MouseEvent('mousemove', ...))`. The
path can be as human as you like and the events are `isTrusted: false`, permanently,
because the flag is set by the browser and is not writable. This is the layer most
"humanize" snippets on the web operate at, and it fails the cheapest possible check.

**The automation driver.** `page.mouse.move()` in Playwright, and the equivalents
elsewhere. These produce genuinely trusted events, because the browser generates them
internally rather than the page. This is a real improvement and it is where the
Bezier libraries plug in: they call `mouse.move()` many times along a curve instead of
once at the destination.

What the driver does not fix by itself is the *content* of those events. Whether
`pressure` changes when a button goes down, whether `pointerType` is set, whether the
timing between events looks like a device rather than a loop, all depend on what the
driver fills in.

**The browser's own input path.** Values decided where the browser builds the event.
At this level `pressure`, `pointerType` and the trusted flag are simply what a real
device would have produced, because they are produced in the same place.

## Why the curve alone is not enough, and why it still matters

Be fair to the curve libraries: straight-line teleporting is a real tell and they fix
it. A pointer that jumps from `(0,0)` to a button and clicks, with no intermediate
events at all, is trivially distinguishable from a hand.

But consider what a behavioural check can compare, once you have a curve:

- **Event rate.** A real mouse reports at a device-determined rate, so intervals
  cluster and jitter around it. A loop with `await sleep(10)` produces a distribution
  no hardware makes.
- **Sub-pixel behaviour.** Real movement produces fractional coordinates on scaled
  displays. Integer-only paths are a signature.
- **What happens between actions.** Humans move while reading, drift, and overshoot
  targets they are not going to click. Automation usually moves only when it needs to
  arrive somewhere.
- **The idle case.** A session with no pointer events at all before a click is louder
  than any curve shape.

So the curve is necessary and not sufficient, which is the same shape as every other
part of this subject.

## How this project does it

Two pieces, and the split is deliberate.

The **path** is drawn in the driver from the session seed, so the same seed produces
the same motion and a failure is reproducible. Every click, hover and drag arcs rather
than teleporting, with the timing spread rather than uniform.

The **event fields** are set in the browser's own input handling, so `pointerType`,
`pressure` and the trusted flag are what a real device produces rather than what a
synthesiser guessed. That part is a source patch and it is the reason the feature is
not simply a library you could add on top.

Getting the path itself right was not the end of it, either -
[the path also has to survive the driver's own hit-target check](hover-mouse-movement-bug.md),
which turned out to consume most of it on the exact calls real scripts make.

It is switched on by default and tunable:

```python
with InvisiblePlaywright(seed=42, humanize=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # arcs to the button, with device-shaped events
```

Honest limit, since this page is about what things do not fix: humanised motion is a
behavioural signal and it does nothing for the machine underneath. A convincing
pointer on [a browser announcing a software GPU](webgl-renderer-strings.md) is still a browser announcing a
software GPU.

## Checking your own

```js
document.addEventListener('mousemove', e => {
  console.log(e.isTrusted, e.pointerType, e.pressure, e.movementX, e.timeStamp);
}, { once: false });
```

Drive your automation across the page with that attached and read the stream:

- `isTrusted` must be `true` on every event. One `false` is decisive.
- `pointerType` should say `mouse` consistently.
- `pressure` should be `0` while hovering and non-zero between a down and an up.
- The gaps between timestamps should vary. Identical gaps are a loop, not a hand.
- Coordinates should not advance by a constant step.

Then do the same in a browser you drive by hand and compare the two streams. That
comparison tells you more than any single field.

## Short answers to the questions that lead here

**Does moving the mouse along a Bezier curve avoid bot detection?** It removes the
teleporting-cursor tell, which is real. It does not change what the events say about
where they came from.

**Is `page.mouse.move()` detectable?** The events it produces are trusted, so the
cheapest check passes. What can still stand out is their timing and their fields.

**Why is `isTrusted` false in my script?** Because you dispatched the event from page
JavaScript. That flag is set by the browser and cannot be assigned.

**Do I need a humanisation library?** If your tool teleports the pointer, yes, or
something equivalent. If it already draws paths, the next thing worth checking is the
event fields rather than a better curve.

**Does any of this matter if the site does not watch behaviour?** No. Check whether
the block arrives at the first request or only after an interaction. Late blocks point
here; immediate ones point at the fingerprint or the address.

**Should the movement be random every run?** No. Random per run means a failing run
cannot be reproduced. Derive it from a seed so the motion is varied between identities
and identical when you replay one.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
where behaviour is step five, and
[three ways to make Playwright undetected](playwright-stealth-levels.md), which is the
same layering argument applied to the fingerprint instead of the pointer.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The event fields are set in the engine, which is
why they are not a library.*
