---
title: "Detectors, Explained"
description: "How specific, well-known detectors actually work - sannysoft, CreepJS, BotD, FingerprintJS, reCAPTCHA v3 - read from their own source rather than reverse-engineered from behaviour."
parent: "Guides"
has_children: true
nav_order: 6
---


# Detectors, Explained

Not "how to beat" any of these - how they actually work, read from the tool's own
source rather than guessed at from its output. Understanding what a detector is
really checking, row by row or module by module, generalises further than any single
workaround does: most of what these tools check is not automation at all, it is
whether a browser is telling the truth about what it claims to be.

## Named detectors and trust scores

- [What BotD actually detects, and what it does not](botd-explained.md) - what BotD's twenty detectors check, read from source: mostly engine truth, not automation.
- [How CreepJS decides you are lying](creepjs-explained.md) - CreepJS asks whether a browser tells the truth; how it detects a lie, from source.
- [What bot.sannysoft.com actually checks, row by row](sannysoft-explained.md) - which rows still mean something in 2026, which are relics, and the canvas check nobody reads.
- [What BrowserLeaks actually tests, surface by surface](browserleaks-explained.md) - canvas hash, WebGL, WebRTC, fonts, ClientRects; why a unique panel is not a fail.
- [BrowserLeaks canvas and WebGL hash, explained](browserleaks-canvas-webgl-hash.md) - the signature is a hash of a pixel readback, not your GPU.
- [Why a FingerprintJS visitor ID changes](fingerprintjs-visitor-id.md) - a visitor ID is a hash of 41 components; why it changes or stays.
- [reCAPTCHA v3 score: why a fresh browser scores badly](recaptcha-v3-score.md) - a fresh automated browser scores low even with a clean fingerprint. The reason is history.
- [Browser trust scores explained: what the number means](browser-trust-score-explained.md) - CreepJS trust, FingerprintJS confidence and reCAPTCHA v3 score measure different things; one green is not the rest.

## What a fingerprint is and how accurate it is

- [What is a browser fingerprint?](what-is-a-browser-fingerprint.md) - the join of dozens of low-entropy attributes that identify a browser with no cookie.
- [What data does a website collect about your browser?](what-data-websites-collect-about-your-browser.md) - the JS-accessible surface a page reads, plus the passive TLS/HTTP2 fingerprint the server sees.
- [How accurate is browser fingerprinting?](how-accurate-is-browser-fingerprinting.md) - a uniqueness-versus-stability trade-off; confidence drops when signals contradict each other.
- [getClientRects fingerprinting: subpixel geometry as ID](getclientrects-fingerprinting.md) - subpixel float geometry hashes into a cross-platform fingerprint and betrays a faked OS.
- [speechSynthesis voices as a cross-platform fingerprint](speech-synthesis-voices-fingerprint.md) - getVoices() leaks the real OS; a Windows agent with a Linux voice list contradicts itself.

## How detection decides bot from human

- [How do websites detect bots?](how-do-websites-detect-bots.md) - the four independent layers sites use, and which two a real-browser build neutralises.
- [Do websites know you are using a script?](do-websites-know-you-are-using-a-script.md) - automation-layer tells like navigator.webdriver, CDP or BiDi artifacts, synthetic events and unnatural timing.
- [Can a website detect typing by keystroke timing?](keystroke-timing-detection-playwright.md) - yes: detectors histogram per-key dwell and flight times; uniform gaps are the tell.
- [What are mouse-dynamics behavioural biometrics?](mouse-dynamics-behavioural-biometrics.md) - scoring the distribution of pointer velocity, curvature and pause across many events, not one field.
- [Notification.permission as a bot-detection signal](notification-permission-detection.md) - detectors cross-check permissions.query against Notification.permission; a real browser reports one coherent state.
- [Can a website detect Clipboard API access?](can-a-website-detect-clipboard-api-access.md) - a page sees navigator.clipboard, but the async API is a gesture gate, not a value fingerprint.
- [Can a website detect a virtual machine?](can-a-website-detect-a-virtual-machine.md) - how a page infers a VM from software GPU renderers, odd core counts and missing audio.

## The network layer: IP, proxy and VPN

- [Can websites detect a datacenter or proxy IP?](can-websites-detect-a-datacenter-proxy-ip.md) - yes, directly at the network layer; no fingerprint hides the IP the connection arrives on.
- [Does a VPN stop browser fingerprinting?](does-a-vpn-stop-browser-fingerprinting.md) - a VPN changes the IP, not the fingerprint; canvas, WebGL, fonts and timezone survive the tunnel.
