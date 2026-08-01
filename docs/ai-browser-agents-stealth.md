---
title: "AI browser agents and stealth: what fits and what does not"
description: "browser-use, Stagehand, Skyvern, crawl4ai and Maxun, checked from their source. Most drive Chromium over CDP, which decides what a stealth engine can do for them, and what still applies regardless."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 1
---


# AI browser agents and stealth: what fits and what does not

If your agent works on your laptop and gets challenge pages on a server, there are two
separate questions and they get answered as one. The first is whether you can swap the
browser underneath. The second is whether swapping it would fix your problem.

For most of these tools the first answer is no, for a reason that is structural rather
than accidental. The second answer is often no as well, and for a different reason.

This page is what each project actually exposes, checked by reading its source, the split
that decides all of it, what applies no matter which engine you run, the signal specific
to agent-driven sessions, and the one route that does accept a different engine.

## The split that decides everything: CDP or Playwright

Browser automation divides into two families and the boundary matters more than any
configuration option.

**Tools that speak the Chrome DevTools Protocol** talk directly to a Chromium-family
browser. CDP is Chromium's own protocol. Firefox does not implement it in the way these
drivers expect, so an `executablePath` pointing at a Firefox build does not produce a
working session: the driver would be speaking a language the browser does not answer.

**Tools built on Playwright** go through an abstraction that already supports three
engines. Swapping the binary is then a question of whether the tool exposes the launch
option, not of whether the protocol matches.

Almost every AI browser agent is in the first family. That is a reasonable engineering
choice, since CDP gives fine-grained control that agent loops want, and it is also why
"can I use a stealth Firefox with my agent" usually has a short answer.

## What each project actually exposes

Read from each repository, 2026-07-27 and 2026-07-28. Star counts are from the same
check.

| Project | Stars | Family | Custom binary | Firefox |
|---|---|---|---|---|
| browser-use | 107k | CDP, Chrome | `executable_path` on `BrowserProfile` | no |
| Stagehand | 24k | CDP, Chromium | `executablePath`, documented | no |
| Skyvern | 23k | CDP | not exposed | no |
| crawl4ai | 75k | Playwright | **no `executable_path`** | `browser_type="firefox"` |
| Maxun | 17k | Playwright | not exposed | not exposed |

Three others that come up in these searches are not Playwright tools at all:
`agent-browser` and `obscura` are written in Rust, and `BrowserOS` has no Playwright
dependency in its manifest. Star count does not tell you what a project drives.

### browser-use

The configuration is real: `executable_path`, `channel`, `user_data_dir`, `proxy`, `args`
and `headless` on `BrowserProfile`. The surrounding code is Chrome executable discovery
and Chrome profile handling, and the channel type is a Chromium channel.

The useful move there is pointing `executable_path` at your **real installed Chrome**
rather than the bundled Chromium, and pointing `user_data_dir` at a profile that has been
used. [The full version is here](browser-use-detection.md).

### Stagehand

Exposes `executablePath` and documents it, which is more than most. The rest of the
codebase is CDP: local CDP discovery, CDP tailing, a CDP client. Chromium references
outnumber Firefox ones by a wide margin, and the Firefox mentions are in an OpenAPI
schema and a client for someone else's API rather than in a launch path.

So the option exists and it expects a Chromium build.

### Skyvern

CDP throughout. There is no exposed way to point at a different binary, and the protocol
would not match if there were.

### crawl4ai

The only one of these built on Playwright with Firefox genuinely selectable, through
`browser_type="firefox"`. What is missing is any field naming a binary, so you get
Playwright's managed build. Its own stealth mode applies `playwright-stealth`, which its
documentation notes does not support Firefox, so that combination is largely inert.
[The full version is here](crawl4ai-stealth-custom-browser.md).

### Maxun

Playwright is in the manifest and no executable path is exposed. As a no-code platform
that is a coherent product decision rather than an oversight.

## What applies regardless of the engine

Here is the part that matters more than any of the above, because it is true for every
row in that table.

On a server, all of these are true at once and none of them is about automation:

- **No GPU**, so WebGL reports a software renderer.
  [What those strings give away](webgl-renderer-strings.md).
- **A container font set** that does not match the claimed platform.
  [Why installing more is not the fix](headless-fonts-differ.md).
- **No audio device**, and no speech voices.
  [The seven audio values](audiocontext-fingerprinting.md) and
  [the voice list](speech-synthesis-voices.md).
- **A screen with no taskbar** and a default resolution.
  [The relationships that must hold](screen-size-headless-tells.md).
- **Codec support** describing a slim build, including whether decoding is hardware
  accelerated. [Three surfaces in one API](codec-fingerprinting.md).
- **A TLS handshake** decided before any of this.
  [Which nothing above the network stack can reach](ja3-ja4-tls-fingerprint.md).

Swapping the browser engine does not change a single item on that list. If your agent
fails in a container and works on your laptop, this is almost certainly where the
difference is, and it is the same for a CDP tool and a Playwright tool.
[The container walkthrough](playwright-docker-detection.md) goes through it in order.

## The signal that is specific to agents

Separate from everything above, and not fixed by any fingerprint work.

An agent thinks between actions: it reads the page, calls a model, waits, then acts. The
resulting rhythm has a shape no person produces.

- Long pauses clustered around **model latency** rather than around reading speed.
- **No movement during the pause.** A person drifts, scrolls slightly, hovers. An agent's
  pointer is motionless for two seconds and then arrives.
- Actions landing **dead centre** on targets, because coordinates come from an
  accessibility tree rather than from a hand.
- **No wasted actions.** People hover over things they do not click and change their
  minds.

This explains blocks that arrive **after** a few interactions rather than at first load,
and that timing is the cheapest diagnostic you have.
[Human-like pointer motion](human-mouse-movement.md) covers what can be done about the
mechanical half, and it is honest that the deliberation pattern is a different problem.

## This stopped being a theoretical concern in 2026

Agent-driven browsing used to be rare enough that most detection stacks didn't bother
building a dedicated signal for it. That changed this year. Fingerprint, the
commercial fingerprinting vendor behind FingerprintJS, shipped a product in early
2026 specifically aimed at classifying AI agent traffic - not bot detection in
general, agent detection as its own category, with its own signal set. Academic
research published in the same period has been measuring the same target directly:
building multi-layer fingerprints specifically to separate an LLM-driven browsing
session from a human one, using exactly the rhythm-based signals described above
plus whatever else the page surface gives away.

The practical upshot for anyone running an agent against a real site: the timing
and pointer-behavior signal in the previous section is not a corner case anymore.
It is an active, funded area of detection work, aimed specifically at the traffic
pattern an agent loop produces, independent of whatever engine sits underneath it.
Fixing the browser's fingerprint and fixing the agent's behavior are now both live
requirements, not one obvious fix and one theoretical afterthought.

## The route that does accept a different engine

If you want an engine whose fingerprint is set in its own source and you want an agent to
drive it, the working combination today is MCP rather than CDP.

Microsoft's `playwright-mcp` accepts a browser and an executable path and speaks MCP, so
any agent framework that talks MCP can drive a patched Firefox through it.
[That integration is written up here](integrations/playwright-mcp.md).

The trade is explicit: you give up the agent loop of whichever framework you were using
and get an engine-level fingerprint. Whether that is worth it depends on which of the two
problems at the top of this page you actually have.

## Conclusion

The honest summary is that the agent ecosystem standardised on CDP, and CDP means
Chromium. A Firefox engine does not drop into browser-use, Stagehand or Skyvern, and
crawl4ai is one missing field away rather than one protocol away.

More importantly, for most people the engine is not the binding constraint. The container
is. Every item in the machine list above is true whichever tool you picked, and none of
them is fixed by a stealth setting inside it.

Work out which problem you have first: a block at the first request points at the machine
or the address, and a block after several actions points at behaviour.

## Short answers to the questions that lead here

**Can I use a stealth Firefox with browser-use?** No. It drives Chrome over CDP.

**Does Stagehand support a custom browser?** Yes, `executablePath`, and it expects a
Chromium build.

**Can crawl4ai use a patched engine?** Not through configuration. It accepts
`browser_type="firefox"` but has no field naming a binary.

**Why does my agent work locally and fail in Docker?** Because the container answers
differently about the GPU, the fonts, the audio device and the screen. The agent is
identical.

**Which AI agent framework is most stealth-friendly?** The question is usually the wrong
one. Pick on the agent loop, then fix the machine, then consider the engine.

**Is there any agent route to an engine-level fingerprint?** MCP, through Microsoft's own
Playwright MCP server, which takes a browser and an executable path.

**See also:** [browser-use in detail](browser-use-detection.md),
[crawl4ai in detail](crawl4ai-stealth-custom-browser.md),
[Playwright MCP](integrations/playwright-mcp.md), and
[the checklist for being detected on one site](playwright-detected-as-bot.md).

## Sources

- `browser_use/browser/profile.py` and `chrome.py`; Stagehand's
  `packages/core/lib/v3/launch/local.ts` and its browser configuration docs; Skyvern's
  CDP client; `crawl4ai/async_configs.py`; Maxun's package manifest. All read
  2026-07-27 and 2026-07-28.
- Star counts from the GitHub API on the same dates.
- The machine-level surfaces are each documented on their own page, linked above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. This page exists because eight separate articles all
concluding "does not fit" would have been worse for you than one that says why.*
