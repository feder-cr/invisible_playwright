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

Seven groups, roughly in the order a real investigation goes: what the browser itself
gives away, then the rendering surfaces that need more than a property check, then the
network, then the automation layer driving the browser, then the newer AI-agent
angle, then the detectors themselves read from source, then how to test any of this
without fooling yourself.

- [**Browser Identity**](guides-browser-identity.md) - Navigator, screen, headers,
  permissions: the properties a site reads first.
- [**Canvas, WebGL, Fonts and Audio**](guides-canvas-webgl-fonts-audio.md) - the
  highest-entropy surfaces, because they are drawn, not just declared.
- [**Network, Proxy and WebRTC**](guides-network-proxy-webrtc.md) - everything that
  happens outside the JS engine: IP, DNS, timezone, TLS.
- [**The Automation Layer**](guides-automation-layer.md) - the driver itself as a
  surface: what the way a browser is piloted reveals.
- [**AI Agents and Frameworks**](guides-ai-agents.md) - agents that drive a browser,
  and what applies whichever one you picked.
- [**Detectors, Explained**](guides-detectors-explained.md) - not how to beat them,
  how they actually work, read from their own source.
- [**Testing and Troubleshooting**](guides-testing-troubleshooting.md) - what to
  check, in what order, before assuming a fix worked.

Looking for a specific tool instead? [Comparisons](comparisons.md) covers Camoufox,
Patchright, nodriver and playwright-stealth against this project, and the case for
Firefox over Chromium generally.
