---
title: "Browser fingerprinting and bot detection, from the source"
description: "Documentation and standalone explainers for invisible_playwright, an undetected Playwright wrapper on a stealth-patched Firefox. Each page is checked against the current source of the thing it describes."
nav_order: 1
permalink: /overview.html
---

# Browser fingerprinting and bot detection, from the source

Documentation for [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
plus a set of standalone explainers. Each one is written against the current source of
the thing it describes, and several of them record something we got wrong first.

## Where to go

- **[Documentation](documentation.md)** - install it, the two-line switch from plain
  Playwright, proxy and timezone configuration, pinning specific fields, the CLI.
- **[Guides](guides.md)** - how detection actually works, in seven groups: browser
  identity, canvas/WebGL/fonts/audio, network and WebRTC, the automation layer, AI
  agents, the detectors themselves explained from source, and testing.
- **[Comparisons](comparisons.md)** - against Camoufox, Patchright, nodriver and
  playwright-stealth, and the case for Firefox over Chromium generally.
- **[Integrations](integrations/)** - running this inside Scrapy, Crawlee, Robot
  Framework, CodeceptJS, test runners and Playwright MCP, including which frameworks
  it does not fit and why.

## If you don't know where to start

Three pages that most other pages here eventually link back to:

- [Three ways to make Playwright undetected](playwright-stealth-levels.md) - the map
  underneath every comparison on this site: page, driver or engine, and what each
  level cannot reach.
- [Playwright detected as a bot on one site: a checklist](playwright-detected-as-bot.md) -
  a troubleshooting order, so you check the free things before the expensive ones.
- [navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md) -
  the most famous property in this space, and why patching it alone buys you almost
  nothing.

## The idea these pages keep returning to

Three things turn up on almost every page, so they are worth stating once here.

**Consistency beats rarity.** Detectors rarely ask whether a value is unusual. They
ask whether two values that must agree, do. A user agent claiming one platform on a
font set from another is caught by a comparison, not by a blocklist.

**Suppressing a signal is a signal.** A browser that refuses to answer is louder than
one that answers plainly, because refusing is rare. CreepJS literally records a
blocked probe as a lie.

**Server tells are not automation tells, and they need different fixes.** A software
WebGL renderer or a Linux font set under a Windows user agent says nothing about
automation and everything about where the browser is running. No stealth plugin
touches either.
