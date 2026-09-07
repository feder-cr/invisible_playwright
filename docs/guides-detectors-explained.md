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
- [How Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md) - a signal-gathering decision engine, not a puzzle: non-interactive JS checks, proof-of-work, and a risk-scored escalation to a checkbox.
- [How DataDome's bot detection actually works](datadome-explained.md) - TLS/HTTP fingerprint, a JS device and behavior collector, and a cookie that remembers the verdict.
- [How Kasada's bot detection actually works](kasada-explained.md) - an obfuscated bytecode VM and a proof-of-work token that keeps renewing through the session.
- [PerimeterX (now HUMAN Bot Defender): what the sensor actually checks](perimeterx-explained.md) - the sensor script and `_px` cookie chain, from the vendor's own SDK source.
- [How Akamai Bot Manager actually works](akamai-bot-manager-explained.md) - the `_abck` cookie and Bot Score, and a real case where a matching TLS/JA3/JA4 fingerprint was not enough.
- [Imperva (Incapsula), Explained](imperva-incapsula-explained.md) - an obfuscated JS sensor and a three-step cookie/JS/CAPTCHA escalation, still known by its old Incapsula cookie names.
- [F5 Distributed Cloud Bot Defense (Shape), Explained](f5-shape-distributed-cloud-bot-defense-explained.md) - JavaScript and mobile-SDK telemetry, with mitigation that only kicks in once server-side evaluation finishes.
- [Fastly Bot Management, Explained](fastly-bot-management-explained.md) - server-side header analysis at the CDN edge plus an opt-in JS probe aimed at headless browsers.
- [reCAPTCHA Enterprise vs reCAPTCHA v3](recaptcha-enterprise-vs-v3.md) - the same 0.0-1.0 score plus an explainable risk-analysis API, Account Defender, and paid-by-default access.
- [Arkose Labs (FunCaptcha), Explained](arkose-labs-funcaptcha-explained.md) - a risk engine decides puzzle difficulty before you ever see the 3D puzzle.
- [AWS WAF Bot Control, Explained](aws-waf-bot-control-explained.md) - Common tier static analysis, Targeted tier a token-backed challenge pipeline with ML rules.
- [Anubis: The Proof-of-Work Firewall, Explained](anubis-proof-of-work-explained.md) - a real, open-source SHA-256 puzzle gate now in front of many git forges.
- [Cloudflare's AI Labyrinth, Explained](cloudflare-ai-labyrinth-explained.md) - an endless maze of real but irrelevant pages instead of an outright block.
- [Private Access Tokens (Privacy Pass), Explained](private-access-tokens-explained.md) - a device-attestation replacement for CAPTCHAs, built on the IETF's Privacy Pass protocol.
- [Beyond robots.txt: How Sites Actually Block AI Crawlers Now](beyond-robots-txt-anti-crawler-mechanisms.md) - rate limiting, behavioral scoring, IP/ASN blocking and decoy mazes underneath the advisory-only file.
- [Vercel Bot Protection (BotID), Explained](vercel-bot-protection-botid-explained.md) - a client-side challenge plus an optional Kasada-powered Deep Analysis tier, including real false-positive reports.
- [GeeTest v4 (Slide/Click Captcha), Explained](geetest-v4-explained.md) - drag, tap-in-order or match-three, encrypted client-side and verified server-side.
- [Honeypot Fields and Hidden Links: How Sites Trap Scrapers](honeypot-fields-explained.md) - a field or link a real visitor never sees, that a script reading raw DOM will trigger.
- [hCaptcha, Explained](hcaptcha-explained.md) - an invisible risk pass first, a visual puzzle only when unsure, and a data-labeling business underneath.
- [hCaptcha Enterprise vs Standard](hcaptcha-enterprise-vs-standard.md) - the same mechanism, plus custom difficulty tuning, dedicated risk models and an SLA instead of the free tier's labeled-data economy.
- [Cloudflare Bot Management, Explained](cloudflare-bot-management-explained.md) - the always-on scoring engine behind Cloudflare's edge, distinct from the Turnstile widget: a 1-99 bot score from JA3/JA4, HTTP fingerprinting, ML and behavior.

- [Queue-it virtual waiting rooms, explained for automation](queue-it-virtual-waiting-room-explained.md) - How a virtual waiting room splits the redirect, the return token and the JavaScript half from the server half.

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
