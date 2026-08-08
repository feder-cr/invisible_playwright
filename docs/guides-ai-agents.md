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

## Framework integrations

- [Give a LangChain agent an invisible_playwright browser](langchain-agent-invisible-playwright-browser.md) - hand PlayWrightBrowserToolkit a launched browser so its tools inherit a real fingerprint.
- [smolagents: hand the agent an invisible_playwright tool](smolagents-invisible-playwright-tool.md) - smolagents' vision browser drives Chromium via helium, so register a custom tool instead.
- [Stagehand and stealth: why a Firefox engine won't drop in](stagehand-firefox-engine-fit.md) - Stagehand is TypeScript and Chromium-channel only, so a Python-launched Firefox cannot drop in.
- [crawl4ai stealth mode and custom browser engines](crawl4ai-stealth-custom-browser.md) - crawl4ai takes browser_type firefox but no executable_path, so you get Playwright's managed build.
- [Give an MCP browser server a stealth Firefox engine](mcp-browser-server-stealth-firefox.md) - point Playwright MCP at a patched Firefox with browserName plus executablePath, minus the profile.

## Computer-use and screenshot agents

- [Back a computer-use agent with a real browser engine](back-computer-use-agent-real-browser.md) - implement the screenshot-and-click Computer interface over an invisible_playwright page, and its honest limits.
- [Computer-use agents and browser fingerprint detection](computer-use-agents-browser-detection.md) - clicking by pixel makes driver flags moot; the engine fingerprint and action rhythm stay checkable.
- [DOM-reading vs screenshot agents: which stealth helps](dom-reading-vs-screenshot-agents.md) - which stealth signal helps depends on whether the agent reads DOM or clicks pixels.

## How AI agents get detected

- [AI browser agents and stealth: what fits and what does not](ai-browser-agents-stealth.md) - most agents drive Chromium over CDP, so a stealth Firefox does not drop in.
- [browser-use gets detected: what you can and cannot change](browser-use-detection.md) - browser-use drives Chrome over CDP: what BrowserProfile changes, what stays out of reach.
- [Why AI browser agents have their own timing signal](ai-agent-timing-signal.md) - a think-act loop emits machine-regular gaps and instant pointer jumps no fingerprint fixes.
- [AI agent retry loops trip rate limits, not fingerprints](agent-retry-loops-rate-limits.md) - retry and re-plan loops multiply requests into a volume signal; throttle in the agent loop.

## Running agents at scale and on servers

- [Running an AI browser agent headless on a server](headless-browser-agent-on-a-server.md) - the engine covers the GPU-less headless fingerprint; IP reputation stays yours to supply.
- [Run parallel browser agents with distinct fingerprints](parallel-browser-agents-distinct-fingerprints.md) - each agent gets its own reproducible fingerprint, but one shared exit IP still links them.
- [Give each AI agent a reproducible browser identity](reproducible-agent-browser-identity-seed.md) - one seed keeps a repeated task on one stable device fingerprint.
- [Give a browser agent a persistent logged-in session](persistent-logged-in-session-browser-agent.md) - reuse saved storage_state or profile plus a pinned seed to stay logged in.
- [Feed invisible_playwright pages into a RAG index](feed-invisible-playwright-pages-into-rag-index.md) - fetch JS-rendered gated pages as real HTML, then chunk and embed the text.
