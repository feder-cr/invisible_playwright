---
title: "browser-use gets detected: what you can and cannot change"
description: "browser-use drives Chrome over CDP. What BrowserProfile lets you change and what stays out of reach. This page moved to the invisible_playwright_mcp wiki."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 2
---


# browser-use gets detected: what you can and cannot change

This page moved to the invisible_playwright_mcp wiki, where the agent-experience content now
lives:

**[browser-use getting blocked: what you can and cannot change](https://github.com/feder-cr/invisible_playwright_mcp/wiki/browser-use-getting-blocked)**

The unchanged short answer: browser-use exposes a real set of levers
(`executable_path`, `user_data_dir`, `proxy`, `headless`, `args`), all of
them Chromium-family, and none of them reaches the machine underneath - the
GPU, fonts, audio and screen a server answers with. A stealth-patched Firefox
is not a drop-in for its CDP driver; the route that accepts a different
engine is MCP.

The mechanism layer stays on this wiki:
[Playwright in Docker and the datacenter machine](playwright-docker-detection.md),
[WebGL renderer strings](webgl-renderer-strings.md), and
[why headless fonts differ](headless-fonts-differ.md).
