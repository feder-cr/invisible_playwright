---
title: "Fastly Bot Management, Explained"
description: "Fastly scores bot traffic at the CDN edge, before caching and again before your origin, combining server-side header analysis with an opt-in JavaScript probe aimed specifically at headless browsers. Read from Fastly's own documentation."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 30
---


# Fastly Bot Management, Explained

Fastly is a content delivery network, and its bot detection runs where the rest of its business runs: at the edge, before a request reaches whatever origin server it is actually addressed to. That positioning matters more than it sounds. It means Fastly can act on a request before your own application does anything, and it means the detection Fastly publishes is split cleanly into what happens automatically, on every request, and a specific opt-in JavaScript check aimed at one thing: headless browsers.

Read from Fastly's own developer documentation on 2026-08-30.

## Two inspection points, and what runs at each

Fastly's documentation describes Bot Management working at two points in the request lifecycle:

**Pre-cache**, inspecting traffic "before they reach your cache layer, allowing you to control bot access to both cached and uncached resources." This is where Fastly's baseline, server-side detection runs on every request regardless of whether the client executes any JavaScript at all: analyzing "HTTP header anomalies and `User-Agent` spoofing," protocol-level signals a request carries whether or not a browser is behind it.

**Post-cache**, working "alongside the Next-Gen WAF to analyze cache-miss requests, protecting your origin from bot attacks." This is where the client-side signals described below feed into rules, and where a request that already passed the pre-cache check gets a second look before it reaches your actual application.

Fastly is explicit that its detection is not meant to lean on attributes that are trivial to fake: its own materials state the approach "does not depend on easily spoofed attributes such as IP addresses or user-agent strings," instead correlating "behavioral and client signals over time" so that IP rotation or a copied user-agent string alone does not clear a session.

## The opt-in layer: advanced client-side detections

The baseline server-side analysis runs without any change to your pages. Catching headless browsers specifically requires opting in to what Fastly calls **advanced client-side detections**, enabled by adding one line to a page's `<head>`:

```html
<script src="/_fs-ch-1T1wmsGaOgGaSxcX/assets/script.js"></script>
```

Fastly's own documentation recommends placing this "above all other scripts in your HTML," and the script's filename is configurable as long as it ends in `.js`, presumably so the endpoint does not read as an obvious bot-detection asset to a client deciding whether to load it. Fastly states this specific feature exists to "detect bots that leverage headless browsers such as headless Chrome."

Once loaded, the script's execution feeds three system signals a site owner can build rules against:

| Signal | What it indicates |
|---|---|
| `SUSPECTED-BOT.HEADLESS` | Probable headless browser activity |
| `SUSPECTED-BAD-BOT.HEADLESS` | Headless activity Fastly's model treats as malicious, not merely automated |
| `CLIENTSIDE-COOKIE-VALID` | The client-side script ran and produced a cookie, i.e. it is a real, executing browser environment |

The cookie itself is named `_fs_cd_cp_` in Fastly's own docs, and its presence is how a site owner or a Fastly rule confirms the script actually executed rather than being blocked or skipped.

Handling then splits by inspection point: pre-cache rules can act on a `fastly.bot.category.is_headless` parameter directly in VCL (Fastly's edge configuration language), while post-cache handling goes through Next-Gen WAF rules matching the system signals above.

## Signals beyond headless detection

Fastly's broader materials name additional categories without publishing the same level of implementation detail as the headless script: **client fingerprinting**, to "identify client types and detect bots designed for malicious activities," and **AI bot detection**, aimed specifically at "AI crawlers and fetchers" as their own traffic class. A 2024 addition, the `BOT-ANALYSIS` system signal, is documented only at the level of "a request that was analyzed for bots," without the finer distinctions Fastly gives the headless-specific signals; this page reports that limitation instead of guessing at what `BOT-ANALYSIS` adds beyond the headless triad.

## What an engine answers honestly, and what stays server-side

The headless-detection script's whole premise is that a headless or automation-driven environment behaves detectably differently from a real, rendering browser: missing APIs, timing that does not match real hardware, environment properties a genuine browser always has. A Firefox that is genuinely the engine it claims to be, patched below the JavaScript layer rather than scripted from a content script, answers that probe honestly because there is no simulated environment for the script to catch, the same argument this corpus makes for [CreepJS](creepjs-explained.md) and [BotD](botd-explained.md). The `CLIENTSIDE-COOKIE-VALID` signal in particular is close to a tautology for a real browser: a real engine that runs the script produces the cookie because it actually ran the script, not because anything faked having run it.

What sits outside that: the server-side, pre-cache header and protocol analysis Fastly runs on every request regardless of JavaScript, which reads the connection and headers rather than anything a browser engine renders; the behavioral correlation "over time" Fastly's own materials describe, which is a property of the session and its history rather than the engine underneath any single request; and whatever Fastly's `BOT-ANALYSIS` signal and its underlying model actually weigh, which Fastly has not published in enough detail for this page to describe with confidence.

`invisible_playwright` does not claim to defeat Fastly's bot scoring as a whole. It is a real Firefox that answers the client-side headless-detection layer the way any genuine instance of the engine does, which is one signal among the several Fastly's own documentation says it correlates.

## Short answers to the questions that lead here

**Does Fastly Bot Management run without any script on the page?** Yes, for its baseline server-side detection, which analyzes HTTP headers, user-agent strings and protocol-level signals on every request. The headless-browser-specific detection is a separate, opt-in JavaScript probe.

**What does the `_fs-ch-*` script actually do?** Per Fastly's own docs, it detects headless browsers such as headless Chrome and produces a `_fs_cd_cp_` cookie confirming it executed, alongside the `SUSPECTED-BOT.HEADLESS` and `SUSPECTED-BAD-BOT.HEADLESS` system signals.

**What is the difference between pre-cache and post-cache detection?** Pre-cache runs before Fastly's cache layer and can control access to cached and uncached resources directly in VCL; post-cache integrates with the Next-Gen WAF to inspect cache-miss requests bound for your origin.

**Is IP address or user-agent spoofing enough to pass Fastly?** By Fastly's own framing, no: the detection is built specifically to not depend on "easily spoofed attributes such as IP addresses or user-agent strings," correlating behavioral and client signals over time instead.

**What does `BOT-ANALYSIS` mean?** Fastly's own documentation describes it only as marking "a request that was analyzed for bots," without further detail on what triggers it or how it differs from the headless-specific signals.

**Does a real browser engine defeat Fastly's headless detection?** It answers that specific probe honestly, because a genuinely real, engine-level Firefox is not simulating a browser environment, it is one. That says nothing about the server-side header analysis or the behavioral correlation Fastly runs independently of any single page's JavaScript.

**Does invisible_playwright bypass Fastly Bot Management?** No. This project does not sell or claim a bypass service for Fastly or any comparable vendor.

**See also:** [How do websites detect bots?](how-do-websites-detect-bots.md) for where a CDN-level, pre-cache check sits relative to the other layers; [is Playwright headless detectable](is-playwright-headless-detectable.md) for the general headless-tell problem Fastly's script is built to catch; and [How DataDome's bot detection actually works](datadome-explained.md) for a comparable network-plus-JavaScript-plus-cookie structure at another edge-deployed vendor.

## Sources

- Fastly, [About Bot Management](https://www.fastly.com/documentation/guides/security/bot-management/about-bot-management/),
  retrieved 2026-08-30, for the pre-cache/post-cache architecture, the server-side header/user-agent analysis, and the "does not depend on easily spoofed attributes" framing.
- Fastly, [Using advanced client-side detections](https://www.fastly.com/documentation/guides/security/bot-management/using-advanced-client-side-detections/),
  retrieved 2026-08-30, for the exact script tag, the `_fs_cd_cp_` cookie, the `SUSPECTED-BOT.HEADLESS` / `SUSPECTED-BAD-BOT.HEADLESS` / `CLIENTSIDE-COOKIE-VALID` signals, and the pre-cache/post-cache handling split.
- Fastly, [Added BOT-ANALYSIS signal](https://www.fastly.com/documentation/reference/changes/2024/09/added-bot-analysis-signal/),
  retrieved 2026-08-30, for the limited public description of that signal.
- Fastly, [Bot Management and Protection product page](https://www.fastly.com/products/bot-management),
  retrieved 2026-08-30, for the client-fingerprinting and AI-bot-detection category language.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
Fastly documents its headless-detection script in enough detail to quote the signal names and
cookie directly; its broader behavioral-correlation model and the newer `BOT-ANALYSIS` signal are
not published at the same depth, and this page says so rather than filling the gap with a guess.*
