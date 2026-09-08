---
title: "Arkose Labs (FunCaptcha), Explained"
description: "FunCaptcha's 3D puzzles are the visible part of a risk engine that decides difficulty before you ever see a puzzle. What Arkose's own materials say, and what independent reverse engineering has worked out about the telemetry behind it."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 32
---


# Arkose Labs (FunCaptcha), Explained

FunCaptcha looks different from the CAPTCHAs it competes with. Instead of distorted text or a grid of storefronts, Arkose Labs presents a small interactive puzzle: rotate a 3D object to the right orientation, match a shape, answer a short audio or logic prompt. Arkose maintains a large, growing library of these, over 1,250 puzzle variants by one practitioner count.

That puzzle is the part users see. It is also, deliberately, not the main event. Before a puzzle ever renders, Arkose's telemetry has already decided how hard that puzzle should be, and how many of them a given session will have to solve.

This is one of the vendors in this corpus closer to an actual CAPTCHA than to a silent background check, worth being direct about upfront: nothing on this page is written as, or should be read as, a description of how to solve Arkose's puzzles for someone. It is about the risk mechanism that decides whether you see one, and how hard.

## What Arkose's own materials say, and where they stop

Arkose's own public site describes its detection in terms of scale and adaptivity, not mechanism: "225+ risk signals," "dynamic challenges that evolve in real time to counter emerging attack vectors," and device identification that combines "deterministic methods with machine learning similarity detection." Its own numbers past that point are business outcomes, not technical detail, customer satisfaction figures, dollar savings, account-growth multipliers, not the field names or scoring weights behind the 225 signals.

That is a real limit on what this page can say with a straight face. Arkose does not publish, in the materials checked for this page, the specific list of what those 225+ signals are or how the risk engine weighs them. What follows past this point comes from independent, practitioner-level reverse engineering, not Arkose's own documentation, and is marked as such.

## What practitioner reverse engineering has converged on

Multiple independent writeups, none of them Arkose's own source, describe a broadly consistent picture of what the client-side script collects before a puzzle appears: device and browser fingerprint (screen resolution, installed fonts, navigator properties), canvas, WebGL and AudioContext rendering output, the same category of signal [CreepJS](creepjs-explained.md) and [BotD](botd-explained.md) read, and continuous mouse-movement telemetry once the puzzle itself is on screen.

The mouse-movement layer is where the writeups get specific enough to be worth quoting carefully, with the caveat that this is reconstructed from the outside, not confirmed by Arkose: one practitioner account describes the script computing cursor acceleration ahead of a click, flagging a straight-line `mouse.move()` path (the kind a naive automation script produces) as an anomaly, and treating a press-to-release interval under roughly 50 milliseconds as evidence of programmatic input, on the reasoning that human physiology puts a real click closer to 70 to 100 milliseconds. None of these specific thresholds appear in Arkose's own public materials as retrieved for this page; they are practitioner reconstructions and should be weighed accordingly, the same caveat this corpus applies to reconstructed internals for [Kasada](kasada-explained.md) and [DataDome](datadome-explained.md).

A separate, independent piece of evidence that this fingerprint surface is real and specific enough to document: a public GitHub repository, `AzureFlow/arkose-fp-docs`, exists specifically to catalog ArkoseLabs' FunCaptcha fingerprinting payload. Its existence corroborates that the payload is large and structured enough to be worth documenting field by field, the same kind of evidence [a public interstitial-payload tool corroborates for DataDome](datadome-explained.md#the-cookie-and-what-happens-when-it-is-missing); this page did not extract its full field list, so it is cited for what it demonstrates (a real, mapped fingerprint surface exists) rather than for its specific contents.

## Risk tiers, and how a puzzle escalates

The reverse-engineering writeups agree on the shape of the escalation even where they disagree on exact thresholds: telemetry collected before and during interaction feeds a risk classification, and sessions read as low-risk may see a simpler puzzle or none at all, while sessions read as high-risk face more puzzle steps, a harder variant, or an outright block. A session that appears to be a script calling Arkose's endpoints directly, without a full browser context behind it, reads as an anomaly on arrival: unusual timing, missing events a real interaction would produce, which the writeups describe triggering escalation before a single puzzle attempt has even happened.

Time pressure is part of the same mechanism by the same accounts: a stalled challenge, one sitting unsolved for roughly fifteen seconds, gets swapped out or escalated rather than waiting indefinitely, which by itself works against slow, deliberate automation as much as it works against distraction.

## What an engine answers honestly, and what the puzzle itself is not

Two different layers are worth separating cleanly here, because collapsing them is exactly the framing this page is built to avoid.

The **fingerprint layer**, canvas, WebGL, AudioContext, navigator properties, is the same category of check covered throughout this corpus: a genuinely real browser engine, patched below the JavaScript layer rather than overridden from a content script, produces these values honestly because it is not simulating them, it is generating them the normal way. That argument holds here exactly as it does for [CreepJS](creepjs-explained.md) and [PerimeterX](perimeterx-explained.md), and it says nothing puzzle-specific: it only affects whether the fingerprint half of Arkose's risk read looks honest or contradictory.

The **puzzle itself is a different kind of gate entirely**, the same structural point [made about Cloudflare Turnstile's checkbox tier](cloudflare-turnstile-explained.md): once risk scoring has decided a session needs a challenge, solving a 3D rotation puzzle is an interactive task, not a value a browser engine reports about itself. No amount of engine-level fidelity upstream makes that requirement disappear, because the requirement was never a fingerprint question in the first place. And the mouse-dynamics telemetry collected during the solve is a property of whatever is driving the pointer, human or scripted, [which has its own separate treatment in this corpus](mouse-dynamics-behavioural-biometrics.md), not a property of the rendering engine underneath.

`invisible_playwright` does not include, and will not add, a puzzle-solving or CAPTCHA-solving capability for Arkose or any comparable vendor. That is a deliberate boundary, not a gap this project is working to close: solving FunCaptcha's puzzles on a user's behalf is a different product category from engine-level stealth, and this corpus's naming policy for anti-bot vendors is explicit that a circumvention claim paired with a protection vendor's name is the one thing it does not make. What honest engine behavior changes here is narrower and more mundane: it keeps the fingerprint half of the risk read from contradicting itself, nothing more.

## Short answers to the questions that lead here

**Does Arkose Labs publish its risk scoring model?** No, not beyond category-level language ("225+ risk signals," device identification combining deterministic and ML methods). The specific field list and scoring weights are not in Arkose's own public materials as checked for this page.

**What decides how hard a FunCaptcha puzzle is?** Per Arkose's own framing and independent reverse engineering, a risk classification computed from device fingerprint, behavioral telemetry and reputation signals before the puzzle is shown, with higher-risk sessions facing more or harder puzzle steps.

**Does mouse movement get scored during the puzzle itself?** Independent writeups describe continuous cursor telemetry during the solve, including acceleration and click-timing checks, though the specific thresholds reported are reconstructed by practitioners, not confirmed by Arkose's own documentation.

**Is there a real public reverse-engineering project for Arkose's fingerprint?** Yes; `AzureFlow/arkose-fp-docs` on GitHub documents ArkoseLabs' FunCaptcha fingerprinting payload, though this page did not extract its full field-level contents.

**Does a real browser engine defeat FunCaptcha?** It answers the fingerprint layer honestly, the same argument that applies to any vendor's canvas/WebGL/navigator checks. It has no bearing on the puzzle itself, which is an interactive challenge, not a fingerprint question.

**Does invisible_playwright solve FunCaptcha puzzles?** No. This project does not include or sell a puzzle-solving or CAPTCHA-solving service for Arkose or any vendor, and this page is not a guide to building one.

**Why does Arkose escalate a stalled puzzle after about fifteen seconds?** By practitioner accounts, an unsolved challenge sitting past that window gets swapped or escalated rather than left open, which works against slow or hesitant automation the same way it works against a distracted human.

**See also:** [How Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md) for the same fingerprint-versus-human-gate distinction at a vendor whose interactive tier is a click rather than a puzzle; [What are mouse-dynamics behavioural biometrics?](mouse-dynamics-behavioural-biometrics.md) for the pointer-telemetry layer in depth; and [How CreepJS decides you are lying](creepjs-explained.md) for the fingerprint-consistency argument this page leans on.

## Sources

- Arkose Labs, [How Arkose Labs Works](https://www.arkoselabs.com/how-arkose-labs-works/), retrieved 2026-08-30,
  for the "225+ risk signals," dynamic-challenge and device-identification language quoted above.
- [`AzureFlow/arkose-fp-docs`](https://github.com/AzureFlow/arkose-fp-docs) on GitHub, retrieved 2026-08-30,
  a real public reverse-engineering project documenting ArkoseLabs' FunCaptcha fingerprint payload, cited for
  its existence as evidence of a mapped fingerprint surface, not for its full field-level contents.
- Practitioner reverse-engineering writeups, retrieved 2026-08-30: ["FunCaptcha (Arkose Labs): Principles of Operation, Features, and Methods for Automated Bypass"](https://medium.com/@kentavr00000009/funcaptcha-arkose-labs-principles-of-operation-features-and-methods-for-automated-bypass-780ef786d7c5)
  and ["Scraping in the Crosshairs of Arkose Labs"](https://medium.com/@koshka00009/scraping-in-the-crosshairs-of-arkose-labs-how-to-bypass-3d-puzzles-browser-fingerprints-and-c5c710091152),
  for the telemetry categories, the mouse-timing thresholds, and the risk-tier escalation behavior described above;
  independent, non-vendor sources whose specific numeric thresholds are not corroborated by Arkose's own documentation.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright), a Firefox
patched at the C++ level. Arkose's own materials stop at category-level claims about its risk engine,
and this page stops there too for anything not independently corroborated. It does not describe, and
this project does not build, a way to solve FunCaptcha's puzzles for you.*
