---
title: "F5 Distributed Cloud Bot Defense (Shape), Explained"
description: "The Shape Security engine F5 bought in 2020 for about a billion dollars, read from F5's own documentation: JavaScript and mobile-SDK telemetry, transaction headers, and mitigation that only kicks in once evaluation finishes server-side."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 29
---


# F5 Distributed Cloud Bot Defense (Shape), Explained

F5 Distributed Cloud Bot Defense is not a product F5 built. It is Shape Security, a company F5 acquired in January 2020 for roughly a billion dollars, its single largest acquisition at the time, folded into F5's platform and renamed twice since (Shape Enterprise Defense, then Shape Integrated Bot Defense, now F5 Distributed Cloud Bot Defense). The mechanism underneath is the one Shape built for years of retail, banking and airline traffic before the acquisition.

This page is built from F5's own product and technical documentation, retrieved 2026-08-30. F5's public docs describe the pipeline in operational terms without publishing the internal scoring model, and this page says so wherever that ceiling applies.

## What it collects, and how it gets to the request

F5's documentation is direct about the two collection paths: "JavaScript to collect telemetry from client browsers and a native Mobile SDK to collect telemetry from mobile devices." For a web session, that telemetry is not sent as a separate call the way a fingerprinting library's `getInstance()` behaves: it is "attached in the form of HTTP headers or included in the POST body to the protected requests" themselves. For a mobile app, the embedded SDK "collects telemetry data and information about the endpoints" and is invoked by the app itself to generate headers, which are then attached to the outbound request the app was already making.

The collection code is not a plain script a browser's devtools can read line by line. F5's documentation states the client-side collector uses "sophisticated code obfuscation to prevent adversaries from reverse engineering and tampering with signal collection," the same posture [Kasada's bytecode VM](kasada-explained.md) and [Akamai's encrypted sensor bundle](akamai-bot-manager-explained.md) take: the obfuscation is not incidental minification, it is a stated part of the defense.

## Evaluation happens before your request reaches the origin

F5's own phrasing: "Bot Defense examines the telemetry collected from requests before they are permitted to reach your application." A bot detection rule, in F5's own terms, "contains criteria that Bot Defense uses to determine whether a transaction is from a human or automated source," and that determination is made before the origin server does anything with the request at all.

Two outcomes follow. For traffic Bot Defense calls human or an explicitly allowed automated source, "Bot Defense adds a custom HTTP request header to the request and allows the traffic to continue to the origin," which gives the origin application a signal it can check without having to run its own detection. For traffic it calls automated, the configured mitigation, monitor, block, or redirect, applies instead; F5's docs describe this as configurable per endpoint policy instead of a single global switch.

## The 2020 shift, and what F5's newer material adds

The acquisition put Shape's retail-and-finance-tuned detection behind F5's broader application delivery footprint rather than as a standalone appliance customers ran themselves. F5's current product page frames the same underlying approach in language aimed at 2026 traffic: "real-time behavioral analysis, client-side intelligence, and platform-wide telemetry to detect and control bots and AI agents at the application interaction layer." The newer framing adds an explicit category the original Shape Security product predates: "Agent-aware classification," described as separating "humans, trusted agents, and harmful automation" by evaluating "behavior and intent, not static signatures or identity claims." That is F5 extending the same telemetry-then-classify pipeline to LLM-driven browser agents rather than a different mechanism.

## What an engine answers honestly, and what sits behind F5's own wall

The JavaScript-collected telemetry, whatever specific fields it reads, is read by whatever engine is running the page. A genuinely real Firefox, patched at the engine level rather than overridden from a content script, answers that telemetry the same way any other real instance of the browser would, because there is no simulated value standing in for the real one. That is the same argument made at length for [DataDome's device layer](datadome-explained.md) and [Kasada's VM checks](kasada-explained.md), and it applies here without anything Shape-specific changing it.

What it does not touch: the server-side evaluation itself, which happens on F5's infrastructure and is not observable from the client at all; the mitigation policy an operator configures per endpoint, which this project has no way to see or influence; and the mobile-SDK path, which is a native library embedded in someone else's app, entirely outside what a browser engine's realness can speak to. F5's own docs do not publish the bot detection rule criteria beyond the category description above, so this page cannot say more specifically what the telemetry evaluation checks for, and does not guess.

`invisible_playwright` does not include, and does not sell, a service that defeats F5 Distributed Cloud Bot Defense's mitigation decisions. It is engine-level work: a real Firefox that answers whatever client-side telemetry it is asked for honestly, because the answers are not overridden.

## Short answers to the questions that lead here

**Is F5 Distributed Cloud Bot Defense the same thing as Shape Security?** Yes, functionally. F5 completed its acquisition of Shape Security in January 2020 for approximately one billion dollars, and the product has been renamed twice since (Shape Enterprise Defense, then Shape Integrated Bot Defense, now F5 Distributed Cloud Bot Defense) while the underlying detection engine is the one Shape built.

**Where is the telemetry sent?** For web traffic, it rides along with the request itself, in F5's own words "attached in the form of HTTP headers or included in the POST body." It is not a separate reporting call.

**Does F5 publish what its bot detection rules actually check?** Not beyond category-level language (behavioral analysis, client-side intelligence, platform-wide telemetry). The specific fields and scoring weights are not in F5's public documentation as retrieved for this page.

**What happens to traffic F5 classifies as human?** F5's docs state it "adds a custom HTTP request header to the request and allows the traffic to continue to the origin," giving the origin application a signal without the application running its own check.

**Does a real browser engine bypass F5 Bot Defense?** It answers the client-side telemetry collection honestly, the same argument that applies to any vendor's JavaScript-collected fingerprint. It has no bearing on the server-side evaluation, the mitigation policy, or the mobile-SDK path, none of which a browser engine can see or influence.

**What is "Agent-aware classification"?** F5's own newer language for extending the same telemetry pipeline to LLM-driven browser agents: classifying by "behavior and intent" rather than treating every non-human client the same way.

**Does invisible_playwright defeat F5 Distributed Cloud Bot Defense?** No. This project does not make that claim about F5 or any comparable vendor. It ships engine-level realness, not a bypass service.

**See also:** [How Kasada's bot detection actually works](kasada-explained.md) for the same obfuscated-telemetry-plus-server-side-scoring shape at a vendor whose client script has been reverse-engineered in more public detail; [How Akamai Bot Manager actually works](akamai-bot-manager-explained.md) for a documented case where a matching network fingerprint still was not enough; and [How DataDome's bot detection actually works](datadome-explained.md) for the three-layer network/device/session-memory structure this whole category shares.

## Sources

- F5, [Bot Defense Overview](https://docs.cloud.f5.com/docs-v2/bot-defense/concepts/about-bot-defense),
  retrieved 2026-08-30, for the telemetry collection paths (JavaScript, mobile SDK), the header/POST-body attachment mechanism, the "sophisticated code obfuscation" language, and the human-vs-automated mitigation outcomes.
- F5, [F5 Distributed Cloud Bot Defense product page](https://www.f5.com/products/distributed-cloud-services/bot-defense),
  retrieved 2026-08-30, for the "real-time behavioral analysis, client-side intelligence, and platform-wide telemetry" framing and the "Agent-aware classification" / AI-agent language.
- F5, [Get Started with Bot Defense](https://docs.cloud.f5.com/docs-v2/bot-defense/plan-bot-advanced/overview),
  retrieved 2026-08-30, for the bot detection rule and telemetry-examination description.
- Press coverage of the acquisition: [F5 Completes Acquisition of Shape Security](https://www.f5.com/company/news/press-releases/f5-completes-acquisition-of-shape-security),
  F5's own press release, retrieved 2026-08-30, for the January 2020 completion date and deal framing.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright), a Firefox
patched at the C++ level. F5's own documentation stops at category-level language for what its
telemetry evaluation checks; this page stops there too rather than filling the gap with
reverse-engineered specifics nobody has published for this particular vendor as of this writing.*
