---
title: "AI agent retry loops trip rate limits, not fingerprints"
description: "Retry and re-plan loops multiply requests into a volume signal; throttling belongs in the agent loop. This page moved to the invisible_playwright_mcp wiki."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 17
---


# AI agent retry loops trip rate limits, not fingerprints

This page moved to the invisible_playwright_mcp wiki, where the agent-experience content now
lives:

**[Agent retry loops trip rate limits, not fingerprints](https://github.com/feder-cr/invisible_playwright_mcp/wiki/agent-retry-loops-rate-limits)**

The one-paragraph version: an agent that retries and re-plans multiplies its
requests, and volume is scored server-side against your address and account.
No browser property changes a request count. Throttle in the loop that
produces the requests.

Related mechanism pages that stay on this wiki:
[why you can be blocked with a clean fingerprint](why-blocked-with-a-clean-fingerprint.md)
and [ASN and IP reputation in bot detection](asn-and-ip-reputation-in-bot-detection.md).
