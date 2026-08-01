---
title: "browser-use gets detected: what you can and cannot change"
description: "browser-use drives Chrome over CDP, so a Firefox engine cannot be dropped in. What its BrowserProfile actually lets you change, what it cannot reach, and the tell that is specific to AI agents."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 2
---


# browser-use gets detected: what you can and cannot change

If your browser-use agent works locally and gets challenge pages on a server, the
problem is usually not the agent and not the prompt. It is the browser it drives and the
machine it runs on, and only some of that is reachable from configuration.

This page is what `BrowserProfile` actually exposes, what those settings fix, what stays
out of reach, the signal that is specific to agent-driven sessions, and the honest answer
about swapping in a different engine.

## What browser-use gives you to change

Read from `browser_use/browser/profile.py`, which is the configuration surface:

| Field | What it does |
|---|---|
| `executable_path` | which browser binary to launch |
| `channel` | a Chromium channel |
| `user_data_dir` | a persistent profile directory |
| `proxy` | proxy settings for the session |
| `headless` | headless or headed |
| `args` | extra command-line arguments |

That is a real set of levers, and three of them matter more than people use them for.

### executable_path, and what it expects

`executable_path` exists, and it expects a **Chromium-family** browser. The surrounding
code in `browser_use/browser/chrome.py` is entirely Chrome logic: finding a Chrome
executable, deriving the Chrome user-data directory from it, enumerating Chrome profiles.
The channel type is a Chromium channel.

The practical use of that field is pointing at your **real installed Chrome** rather
than the bundled Chromium. Those are not the same browser: the codec set differs, the
branding differs, and a real Chrome install is a far more common thing to be than a
bundled test build.

### user_data_dir, which is underused

Pointing this at a profile that has genuinely been used is the single highest-value
change available in the configuration, because it is the only one that addresses history
rather than hardware.

An agent session starting from an empty profile is a browser that has never been
anywhere, which is
[most of why a fresh browser scores badly](recaptcha-v3-score.md). The traps that come
with persistent profiles are covered separately in
[what a persistent profile fixes and breaks](persistent-profiles.md), and the pairing
rule there applies here too.

## What configuration cannot reach

Everything about the machine. None of the fields above changes any of these, and on a
server all of them are true at once:

- **No GPU**, so WebGL reports a software renderer.
  [What those strings mean](webgl-renderer-strings.md).
- **A container font set** that does not match the claimed platform.
  [Why installing more is not the fix](headless-fonts-differ.md).
- **No audio device**, so the audio values fall back to defaults.
  [The seven values](audiocontext-fingerprinting.md).
- **A screen with no taskbar** and a default resolution.
  [The relationships that have to hold](screen-size-headless-tells.md).
- **Codec support** that describes a slim build rather than a desktop install, including
  whether decoding is hardware accelerated.
  [Three surfaces in one API](codec-fingerprinting.md).
- **A TLS handshake** decided before any of this by the network stack.
  [Which no page-level layer can touch](ja3-ja4-tls-fingerprint.md).

This is why "it works on my laptop and not in Docker" is the most common shape of the
problem. The agent is identical; the machine is not.
[The container version of this list](playwright-docker-detection.md) goes through it in
order.

## The tell that is specific to agent-driven sessions

Here is the part that does not apply to ordinary scraping and does apply to browser-use.

An agent thinks between actions. It reads the page, calls a model, waits for a response,
then acts. That produces an interaction rhythm with a very particular shape:

- **Long, similar pauses before each action**, clustered around model latency rather than
  around reading speed.
- **No movement during the pause.** A person reading a page moves the pointer, scrolls
  slightly, drifts. An agent's pointer is perfectly still for two seconds and then
  arrives exactly on a button.
- **Actions that land dead centre** on their targets, because coordinates come from the
  accessibility tree rather than from a hand.
- **No wasted actions.** People scroll past things, hover over what they do not click,
  and change their minds.

None of that is fixed by a stealth setting, and it is worth knowing because it explains
blocks that arrive **after** a few interactions rather than at the first page load. That
timing is the diagnostic:
[a block at first load points at the fingerprint or the address, a block after an
interaction points at behaviour](playwright-detected-as-bot.md).

## Can I use a stealth-patched Firefox with browser-use?

No, and it is better to say so plainly than to publish a workaround that does not work.

browser-use drives its browser over the Chrome DevTools Protocol, with Chrome-specific
arguments and Chrome profile handling. A Firefox binary in `executable_path` is not a
drop-in: the driver would be speaking a protocol the browser does not implement.

If you specifically want an engine whose fingerprint is set in its own source, the route
that works for agents is a different driver. Microsoft's `playwright-mcp` accepts a
browser and an executable path and speaks MCP, so an agent framework that talks MCP can
drive a patched Firefox through it.
[That integration is written up here](integrations/playwright-mcp.md).

That is a genuine trade: you give up browser-use's agent loop and get an engine-level
fingerprint. Which one matters depends on whether your blocks are arriving because of the
browser or because of the behaviour, and the timing test above tells you which.

## Conclusion

browser-use exposes enough configuration to fix the two things configuration can fix:
which browser binary runs, and whether the profile has a past. Point it at a real Chrome
and at a profile that has been used, and you have taken the available wins.

Everything else divides into two piles. The machine-level tells are shared with every
other automation tool and are fixed by changing the machine or the engine, neither of
which is a browser-use setting. The agent-rhythm tells are specific to this class of tool
and are not fixed by any fingerprint work at all.

Knowing which pile you are in takes one experiment: note whether the block arrives at the
first request or after a few actions.

## Short answers to the questions that lead here

**Why does my browser-use agent get blocked on a server but not locally?** Because the
server has no GPU, few fonts, no audio device and a default screen, and the agent is
identical in both places.

**Can I set a custom browser in browser-use?** Yes, `executable_path` in
`BrowserProfile`, and it expects a Chromium-family browser. Pointing it at your real
Chrome rather than the bundled Chromium is worth doing.

**Can I use a stealth Firefox with browser-use?** No. It drives over CDP, which Firefox
does not implement in the way the driver expects.

**Does a persistent profile help?** Yes, more than most settings, because it is the only
one that addresses having no history. Read the traps first.

**Why do blocks arrive after a few actions rather than immediately?** That usually points
at behaviour rather than fingerprint, and agent pacing has a recognisable shape.

**Does adding a proxy fix it?** It changes where you come from. If the browser is
announcing a datacenter through its GPU and fonts, the address was not the problem.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
which is the order to work in,
[Playwright in Docker](playwright-docker-detection.md), for the server case, and
[Playwright MCP](integrations/playwright-mcp.md), for the agent route that does accept a
different engine.

## Sources

- `browser_use/browser/profile.py`, which defines the configuration fields listed above,
  and `browser_use/browser/chrome.py`, which is the Chrome executable and profile
  handling. Read 2026-07-27.
- The machine-level surfaces are each documented on their own page, linked above.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. This page says our engine does not fit browser-use,
because it does not, and a guide claiming otherwise would waste your afternoon.*
