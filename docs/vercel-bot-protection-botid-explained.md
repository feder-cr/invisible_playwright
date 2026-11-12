---
title: "Vercel Bot Protection (BotID), Explained"
description: "BotID is Vercel's managed bot check for high-value routes: a client-side challenge plus an optional Kasada-powered Deep Analysis tier. Read from Vercel's own docs, including the false positives real teams have reported."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 38
---


# Vercel Bot Protection (BotID), Explained

BotID is Vercel's own bot-detection product, sold as an "invisible CAPTCHA" you drop
in front of a specific route rather than a whole site. It shows nothing to a real
visitor in the common case, runs a client-side challenge in the background, and lets
your server code ask for a verdict before it does anything expensive. It launched in
2025 as a partnership between Vercel and Kasada, and reached general availability
across frameworks by mid-2026.

This page is what BotID actually checks and how, read from Vercel's own
documentation, plus what real teams running it in production have reported when it
gets a verdict wrong. Vercel's own docs are unusually candid about who this is aimed
at: they name Playwright and Puppeteer directly as the kind of tooling BotID exists
to catch, which is worth being upfront about before going further.

## What it protects, and why Vercel built it there

BotID sits in front of what Vercel's own docs call "high-value routes... that are
common targets for bots imitating real users": checkouts, signups, logins, and
increasingly, LLM-backed API endpoints where every request costs real inference
money. The framing in Vercel's docs is explicit about the threat model it targets:
"Sophisticated bots are designed to closely mimic real user behavior. They can run
JavaScript, solve CAPTCHAs, and navigate interfaces in ways that closely resemble
humans. Tools like Playwright and Puppeteer automate these sessions." The product is
built against exactly this project's category, a real or automated browser engine
driving a page, not against the older generation of scripted `curl` bots that never
executed JavaScript in the first place.

## The validation flow

Per Vercel's own documentation, a BotID check runs in six steps:

1. A client-side challenge is sent to the browser.
2. The browser solves the challenge and includes the solution in requests to the
   protected endpoint.
3. Your server-side code calls `checkBotId()`.
4. Vercel validates the integrity of the challenge response.
5. If Deep Analysis is configured, a machine-learning model analyzes the client-side
   signals collected.
6. Your server-side code receives the analysis result and decides what to do with it.

The challenge itself runs invisibly and requires no user action, which is the
"invisible" half of Vercel's own name for it, and it happens whether or not the
route ever gets abused, since the cost only lands when your code actually calls
`checkBotId()`.

## Two tiers, one free and one paid per call

BotID has two check levels, and the second runs only after the first passes.

**Basic** validates the integrity and correctness of the challenge response, and
Vercel provides it free on every plan. Vercel's own framing is that it catches "many
less sophisticated bots," which reads as an honest ceiling: it verifies the response
is well-formed and internally consistent, not that thousands of behavioral signals
line up.

**Deep Analysis**, available on Pro and Enterprise plans at $1 per 1,000
`checkBotId()` calls that invoke it, is powered by Kasada, the same vendor behind
[the bot-detection sensor covered on its own page](kasada-explained.md). Vercel's own
docs describe Kasada as analyzing "thousands of client side signals" and changing
"detection methods on every page load to prevent reverse engineering," the same
moving-target design philosophy Kasada runs in its standalone product.

## What real deployments have reported

Vercel's own community forum carries reports from developers who got flagged by
their own BotID deployment while testing it themselves, one thread titled plainly
"Bot ID false positive, and it's myself," describing being rejected with no clear
reason surfaced back to them. A separate community thread from a team running Deep
Analysis in production reports it "works well for most users" but that some real,
paying customers were unable to log in because of false positives, a genuinely
costly failure mode for a check sitting in front of an authentication flow rather
than a low-stakes page view. Discussion on Hacker News around the launch also raised
a separate concern, unrelated to accuracy: that a "free basic anti-bot" tier collects
telemetry Vercel then uses, which is a privacy and vendor-lock-in question rather
than a detection one, but a fair thing to weigh before wiring a business-critical
route through a third party's invisible check.

None of this is unusual for a machine-learned detector, and it lines up with the
broader pattern this corpus documents: [a trust score is a probability, not a
verdict on a specific browser](browser-trust-score-explained.md), and any system
scoring "human or not" from a few thousand signals will occasionally score a real
person wrong in either direction. The honest reading of the reports above is that
BotID trades some false positives for the invisibility that makes it usable on a
checkout flow in the first place, and any team wiring it in front of an
authentication path should have a fallback for the visitor it wrongly blocks.

## What an engine-level browser answers here, and what it does not

Deep Analysis is, per Vercel's own description, a signals-based machine-learning
verdict over "thousands of client side signals," the same category of check this
corpus covers repeatedly: canvas and WebGL output, font enumeration, timing and API
consistency, whether the browser claiming to be one engine actually behaves like it.
A genuinely real Firefox engine, patched at the level that produces those signals
rather than overridden from a content script, answers that category of check the
way any other real instance of the engine does, for the reason [BotD's own detector
list passes for the same category of reason](botd-explained.md#the-second-group-does-your-story-hold-together).

What that does not touch is the Basic tier's integrity check on the challenge
response mechanics themselves, which is closed-source and undocumented in detail by
Vercel, and it does nothing for the false-positive rate real teams have reported,
which is a property of Kasada's model and Vercel's threshold tuning, not of how real
the browser underneath is. Nothing here is a claim that any tool defeats BotID; it
is a description of which category of signal an honest engine answers and which
sits inside a vendor's closed scoring model.

## Short answers to the questions that lead here

**What is Vercel BotID?** A managed bot-detection product for Vercel-hosted apps: an
invisible client-side challenge plus an optional Kasada-powered machine-learning
tier, meant to sit in front of specific high-value routes rather than a whole site.

**Is BotID the same thing as Kasada?** No. BotID's free Basic tier is Vercel's own
challenge-integrity check. Its paid Deep Analysis tier is licensed from Kasada and
described by Vercel as running Kasada's own signal-based model underneath.

**Does BotID show a CAPTCHA to visitors?** Not in the normal case. It is designed to
run silently in the background and only surface friction when a request looks
automated, which is what Vercel means by calling it invisible.

**Has BotID actually produced false positives?** Yes, per Vercel's own community
forum: developers reported being flagged by their own deployment, and at least one
production report describes real customers unable to log in because of it.

**Does invisible_playwright bypass or defeat BotID?** No, and that is not a claim
this project makes. What an honest, engine-level browser answers is the category of
signal Deep Analysis reads from a real JavaScript environment. It has no effect on
Vercel's threshold tuning, the challenge-integrity check itself, or the false
positives real deployments have already reported against real, non-automated
visitors.

**Why does Vercel's own documentation name Playwright specifically?** Because BotID
is built against exactly this category of tool: a real or automated browser engine
capable of running JavaScript and mimicking user interaction, which is a harder
target than a bare HTTP client and the reason Vercel frames the product around it.

## Sources

- Vercel, [BotID documentation](https://vercel.com/docs/botid), retrieved 2026-08-30,
  for the validation flow, the Basic/Deep Analysis tier descriptions, pricing, and
  the direct framing naming Playwright and Puppeteer.
- Vercel, [Introducing BotID](https://vercel.com/blog/introducing-botid), retrieved
  2026-08-30, for the product's original announcement and positioning.
- Vercel Community, ["Bot ID false positive, and it's myself"](https://community.vercel.com/t/bot-id-false-positive-and-its-myself/47076),
  retrieved 2026-08-30, for a real, named report of a developer flagged by their own
  deployment.
- Vercel Community, ["Botid false positive, what now?"](https://community.vercel.com/t/botid-false-positive-what-now/21694),
  retrieved 2026-08-30, for a production report of real customers blocked from
  logging in.
- Hacker News, [discussion thread on BotID's launch](https://news.ycombinator.com/item?id=44422356),
  retrieved 2026-08-30, for the telemetry concern raised about the free tier.
- [Kasada's own site](https://www.kasada.io/), retrieved 2026-08-30, for confirmation
  of the Deep Analysis partnership Vercel's docs describe.

**See also:** [How Kasada's bot detection actually works](kasada-explained.md), [browser
trust scores explained: what the number means](browser-trust-score-explained.md), [what
BotD actually detects, and what it does not](botd-explained.md), and [can websites
detect Playwright?](can-websites-detect-playwright.md) for the automation-layer tells a
product like this one is built to catch.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright).
BotID's own documentation names Playwright as part of what it is built to catch; this
page describes that mechanism honestly rather than claiming any tool clears it, and
the false positives above came from Vercel's own users, not from anyone testing this
project against it.*
