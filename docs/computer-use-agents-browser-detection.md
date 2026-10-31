---
title: "Computer-use agents and browser fingerprint detection"
description: "Clicking by pixel makes driver flags moot; the engine fingerprint and action rhythm stay checkable. This page moved to the invisible_playwright_mcp wiki."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 6
---


# Computer-use agents and browser fingerprint detection

This page moved to the invisible_playwright_mcp wiki, retargeted at the question people
actually search:

**[Claude computer use detected as a bot](https://github.com/feder-cr/invisible_playwright_mcp/wiki/claude-computer-use-detected-as-bot)**

The core of it, kept here in brief: a coordinate-clicking agent never
generates the DOM-automation tells, so `navigator.webdriver` advice mostly
does not apply to it. What remains checkable is the engine fingerprint of the
machine the click lands in, the address it arrives from, and the
screenshot-think-click rhythm.

The mechanism layer stays on this wiki:
[navigator.webdriver, explained](navigator-webdriver-explained.md),
[what a website can detect about a virtual machine](can-a-website-detect-a-virtual-machine.md),
and [the AI-agents integration pages](guides-ai-agents.md) for wiring a real
engine under a computer-use loop:
[back a computer-use agent with a real browser](back-computer-use-agent-real-browser.md).
