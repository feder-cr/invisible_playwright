---
title: "AI Agents and Frameworks"
description: "Agent frameworks that drive a browser, checked from their own source rather than assumed - which ones use Playwright at all, which are CDP/Chromium-only, and what applies whichever one you picked."
parent: "Guides"
has_children: true
nav_order: 5
---


# AI Agents and Frameworks

The newest category here, and the one with the most room to grow. An AI agent that
drives a browser inherits every fingerprinting surface a human-scripted session
does, plus one of its own: the rhythm of an agent's actions is not a human's rhythm,
and that is checkable independently of any fingerprint work. Each page here is
verified against the framework's actual source before anything is claimed about it -
several candidates that looked like a fit by star count turned out not to use
Playwright, or not to touch Firefox, at all.
