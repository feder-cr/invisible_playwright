---
title: "What privacy.resistFingerprinting actually does"
description: "Firefox's privacy.resistFingerprinting mode is one preference away, and turning it on is usually the wrong move for automation. What it actually changes, what it breaks, and why this project leaves it off."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 10
---


# What privacy.resistFingerprinting actually does

Firefox ships an anti-fingerprinting mode, and turning it on is one preference away.
This is what `privacy.resistFingerprinting` actually changes, what it breaks, and why
an automation tool is better off leaving it switched off.

```
privacy.resistFingerprinting = true
```

If you are automating a browser and trying not to be fingerprinted, turning it on
looks like the obvious first move. It is usually the wrong one, and the reason is
worth understanding even if you never touch the setting, because it is the clearest
example of two goals that sound identical and are opposites.

This project sets it to `false`. On purpose. Here is the reasoning.

## What it changes

`resistFingerprinting`, RFP, came out of the Tor Uplift project: work done for Tor
Browser and merged into Firefox. Its job is to make every user of it report the same
values, so that no individual can be told apart from the others.

Roughly, with the caveat that the exact list moves between Firefox versions:

- **Timezone** is reported as UTC, whatever your machine says.
- **Screen and window** dimensions are rounded, and letterboxing pads the viewport to
  a standard size so the window itself carries no entropy.
- **The user agent** and `navigator.platform` are generalised, and the minor version
  is dropped.
- **[`hardwareConcurrency`](hardware-concurrency-device-memory.md)** reports a fixed low number instead of your core count.
- **Timer precision** is reduced, which blunts timing side channels.
- **Canvas reads** are blocked or prompt, so a canvas hash cannot be taken silently.
- **Locale** is forced toward `en-US` for the APIs that expose it.
- Various smaller surfaces: media queries like `prefers-color-scheme`, some WebGL
  parameters, keyboard layout, and more each release.

That is a real and well-engineered privacy feature. The complaints about it breaking
sites are also real, and they follow directly from the list: a calendar that believes
you are in UTC shows the wrong times, a layout that reads the viewport gets bars
around it, and anything that needs a canvas read either prompts or fails.

## Why it is the wrong tool for automation

Because of what "not fingerprintable" means here.

RFP does not make you look like a typical user. It makes you look **identical to
every other RFP user**. That is exactly right when the population is large and the
goal is to disappear into it: this is the Tor Browser model, where uniformity is the
whole point and the anonymity set is everybody else running the same build.

Now count the population on the open web. Almost nobody has RFP on. So the signature
it produces, UTC timezone regardless of IP, a rounded screen, a suspiciously low core
count, a generalised user agent, is not a hiding place. It is a small, sharply
defined, easily recognised group, and a site that wants to treat that group
differently can do so with one check.

Worse for an automated session: the RFP signature actively contradicts the rest of
what you are doing. You are coming from a residential IP in one country and reporting
UTC. Your proxy says one locale and your `Intl` says another. Uniformity with a tiny
population plus a plausible IP is not two protections, it is a contradiction, and
contradiction is the thing detectors are built to find.

## The distinction in one line

**RFP optimises for anonymity within a set. An automation session needs plausibility
as an individual.**

Those are different objectives and they want opposite implementations. Anonymity
wants everyone identical; plausibility wants you to be one specific, unremarkable,
internally consistent machine, different from the next session, and consistent with
the network you appear to be on.

If your threat model is a website building a profile of a human across visits, RFP is
a good answer and you should use it. If your problem is a site deciding whether this
session is a person, RFP hands it an easier question, not a harder one.

## What to do instead

Pick a specific machine and be it, completely and consistently.

That means every surface derives from the same source rather than being set
independently: the user agent, the platform, the screen, the GPU strings, the font
set, the audio stack, the timezone, the language list. And it means the timezone and
locale follow the IP you are actually leaving from, and not either your host's
values or a fixed UTC.

It also means not layering. Turning RFP on *and* setting a user agent *and* running a
stealth plugin gives three components independently answering the same questions.
They will not agree, and the disagreement is louder than any single wrong value.

Concretely, in this project: the five preferences above are all set to `false`, and
the identity comes from a seeded profile instead. Same seed, same machine, every run.
Different seed, a different machine that is equally unremarkable.

## When RFP is right and this is not

If you are a person who wants privacy from ad networks, use Firefox with RFP on, or
Tor Browser, and accept the breakage. That is the tool doing its job, and no
automation library is a substitute for it.

The advice here is narrow: it applies when the thing on the other side is asking
whether you are a browser and a person, and where being unusual costs you more than
being identifiable.

## Short answers to the questions that lead here

**Should I turn on `privacy.resistFingerprinting` for automation?** No. It makes you
uniform with other users of that mode, which is a small and recognisable population,
and detectors identify the mode itself.

**What does it actually change?** Timezone forced to UTC, a rounded viewport, a
reduced timer precision, a fixed `hardwareConcurrency`, canvas and WebGL readings
altered, and a restricted font set, among others.

**So it is useless?** Not at all. It is the right tool for a person who wants privacy
from ad networks, and Tor Browser builds on it. It is the wrong tool for looking like
an ordinary user.

**Why does it break sites?** Because it lies to the page about things the page uses for
layout and timing. That is the trade it exists to make.

**What does this project do instead?** Sets the values to a plausible, consistent
machine derived from a seed, and leaves the mode off.

**See also:** [how CreepJS decides you are lying](creepjs-explained.md), whose `resistance` module exists to identify this mode, and [the three levels](playwright-stealth-levels.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The preference values quoted are the ones it actually writes, which you can print
yourself with `get_default_stealth_prefs`.*
