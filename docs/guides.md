---
title: "Guides"
description: "Standalone explainers on browser fingerprinting and bot detection, written against the current source of the thing each one describes. Useful whether or not you use invisible_playwright."
has_children: true
nav_order: 3
---

# Guides

Written to be useful whether or not you use this project. If a page only made sense
as an advert for it, it would not be worth reading. Each one is checked against the
current source of the thing it describes - a detector's own code, a framework's
launch options, Playwright's own documentation - and several record something we got
wrong first, measured and fixed rather than assumed.

Eight groups, roughly in the order a real investigation goes: what the browser itself
gives away, then the rendering surfaces that need more than a property check, then the
network, then the automation layer driving the browser, then the newer AI-agent
angle, then the detectors themselves read from source, then the practical scraping
how-tos, then how to test any of this without fooling yourself.

- [**Browser Identity**](guides-browser-identity.md) - Navigator, screen, headers and
  permissions: properties a site reads before anything is drawn, and checks against
  each other.
- [**Canvas, WebGL, Fonts and Audio**](guides-canvas-webgl-fonts-audio.md) - surfaces
  that are drawn or rendered rather than declared, which makes them harder to fake
  than a plain property.
- [**Network, Proxy and WebRTC**](guides-network-proxy-webrtc.md) - everything outside
  the JS engine: proxy authentication, DNS, WebRTC candidates, timezone, TLS, and what
  a container gives away.
- [**The Automation Layer**](guides-automation-layer.md) - the driver itself as a
  surface: the tell lives in how the browser is piloted, not in what it reports.
- [**AI Agents and Frameworks**](guides-ai-agents.md) - agent frameworks that drive a
  browser, checked from source: which use Playwright, which are CDP/Chromium-only.
- [**Detectors, Explained**](guides-detectors-explained.md) - how well-known detectors
  work - sannysoft, CreepJS, BotD, FingerprintJS, reCAPTCHA v3 - read from their own
  source.
- [**Scraping with Playwright**](guides-scraping-with-playwright.md) - practical
  how-tos: blocked headless browsers, infinite scroll, proxy rotation, sessions behind
  a login, on stock Playwright.
- [**Testing and Troubleshooting**](guides-testing-troubleshooting.md) - what to
  check, and in what order, when automation is detected or a preference silently does
  nothing.

Looking for a specific tool instead? [Comparisons](comparisons.md) covers Camoufox,
Patchright, nodriver and playwright-stealth against this project, and the case for
Firefox over Chromium generally.
