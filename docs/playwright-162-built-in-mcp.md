---
title: "Playwright 1.62 Ships MCP In the Box"
description: "Playwright 1.62 bundles the Playwright MCP server and a new playwright-cli into the core package, runnable via npx playwright mcp with no separate install. What that changes for driving invisible_playwright's Firefox."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 18
---


# Playwright 1.62 Ships MCP In the Box

Playwright 1.62, released 24 July 2026, bundles the Playwright MCP server directly
into the core `playwright` package, alongside a new `playwright-cli`. Both now run
with `npx playwright mcp` and `npx playwright cli`, with nothing extra to install.
Before this release, wiring an agent to Playwright meant installing the separate
`@playwright/mcp` package and keeping its version in step with your Playwright
install by hand. That separate package still exists; 1.62 additionally ships the same
capability inside Playwright itself.

The claim in this page's title was checked against Playwright's own release notes and
GitHub release page this session, not assumed from memory, because a wrong version
number here is exactly the kind of thing worth catching before it ships. What was
verified: the version (1.62), the mechanism (bundled into core, two new `npx`
subcommands), and the scope (the same browser-automation server, not a different,
narrower tool). All three checked out as stated.

## What the release notes actually say

Playwright's own release notes, under a section titled "Command line & MCP," state it
plainly:

> "Playwright now bundles the Playwright MCP server and playwright-cli, runnable via
> npx playwright mcp and npx playwright cli."

The same release bumped the bundled browsers to Chromium 151.0.7922.34, Firefox 153.0
and WebKit 26.5. That Firefox version number is Playwright's own bundled, unmodified
build, used when you launch `playwright.firefox.launch()` with no `executablePath`. It
has no bearing on which Firefox invisible_playwright ships; this project pins its own
patched binary independent of whatever Playwright bundles, the same relationship
described in
[stock Playwright, patched Firefox: how they connect](stock-playwright-patched-binary.md).

## Same server, moved inside the package

The bundled command is the same browser-automation MCP server that `@playwright/mcp`
already shipped as a standalone package: the same tool surface an agent calls to drive
a page - navigate, click, type, take a snapshot or a screenshot - translated into the
ordinary Playwright API calls that do the work underneath. What changed in 1.62 is
packaging and distribution, not a new or smaller feature set: the server that used to
live in its own npm package, versioned separately from Playwright itself, now ships
inside the `playwright` package proper, which is what removes the version-drift
problem of the two moving out of sync.

## What this means for a patched Firefox

The existing page on this project,
[give an MCP browser server a stealth Firefox engine](mcp-browser-server-stealth-firefox.md),
documents the standalone-package invocation:

```bash
npx @playwright/mcp@latest --browser firefox --executable-path "$FIREFOX"
```

Because 1.62 describes this as the same server moved into the core package rather than
a rewrite, the same idea should carry over to the bundled entrypoint:

```bash
npx playwright mcp --browser firefox --executable-path "$FIREFOX"
```

Being precise about what was and was not confirmed this session: the version number,
the bundling, and the scope (same automation server) are all sourced directly from
Playwright's own release notes and GitHub release page. What was not independently
re-verified is whether the bundled `npx playwright mcp` entrypoint parses
`--browser` and `--executable-path` identically to the standalone package - those flag
names are documented for the standalone `@playwright/mcp` command, and this page has
not separately confirmed flag-for-flag parity for the newly bundled one. If the
bundled command rejects a flag the standalone one accepted, that is the first thing to
check against the CLI's own `--help` output before assuming the launch config is wrong.

What is on firmer ground is *why* this should work at all on a patched Firefox: MCP
adds no new browser-automation protocol commands of its own. Every tool the server
exposes - navigate, click, snapshot - is Playwright's existing, already-supported API,
the same calls
[stock Playwright already sends over Juggler](playwright-connect-over-cdp-firefox.md)
to any Firefox it drives. Bundling the MCP server into core Playwright is a packaging
and CLI change, not a new wire-protocol surface, so it does not introduce the kind of
[protocol drift](playwright-protocol-drift.md) that a genuinely new Firefox command
would. That is a real, structural difference from
[Playwright 1.61's WebAuthn virtual authenticator](playwright-161-webauthn-passkey-virtual-authenticator.md),
covered separately, which does add new commands and does depend on this project's
patched Firefox having synced that specific Juggler change from upstream.

## The engine layer versus the profile layer, still

Pointing an MCP server, bundled or standalone, at the patched binary gets you the
engine: the real Firefox TLS handshake, the driver layer that does not announce
itself, the C++-level fingerprint work baked into the compiled binary. It does not, on
its own, get you the per-session profile this project's own launcher writes when a
session starts through `invisible_playwright` directly - the seed-derived screen
metrics, the timezone and language matched to the exit, the roughly 400 correlated
fingerprint fields. That split, and when each layer is enough, is the full subject of
[the existing MCP page](mcp-browser-server-stealth-firefox.md) and is not repeated in
depth here.

## What bundling does not fix

Same honest limits as any other launch method, and worth restating because MCP does
not touch any of them:

- **IP reputation.** A real Firefox engine on a datacenter address is still on a
  datacenter address, bundled server or not.
- **Per-account quotas and rate limits.** Counted server-side, invisible to anything
  the browser reports.
- **Agent rhythm.** An MCP client that navigates, reads and clicks at a constant
  model-latency cadence produces the same regular, non-human timing whether the server
  came from a separate package or from inside `playwright` itself. That is
  [a behavior problem, not a packaging one](ai-agent-timing-signal.md).

## Conclusion

Playwright 1.62 moved the MCP browser-automation server, and a new `playwright-cli`,
inside the core package, so `npx playwright mcp` runs with no separate install and no
version to keep in sync by hand. It is the same server, not a smaller or different
one, and because MCP's tools are just Playwright's existing API underneath, there is no
new wire protocol for a patched Firefox to have missed. Point `--browser firefox` and
`--executable-path` at the pinned engine the same way the standalone package already
supported, and you get the engine-level realness this project builds; the per-seed
profile, the proxy, and the agent's own pacing are still yours to add on top.

## Short answers to the questions that lead here

**What version of Playwright bundled MCP into the core package?** 1.62, released 24
July 2026. The MCP server and a new `playwright-cli` both ship inside the `playwright`
package, runnable via `npx playwright mcp` and `npx playwright cli`.

**Is the bundled MCP server different from the standalone @playwright/mcp package?**
No, by Playwright's own description it is the same server moved inside core rather
than a separate, smaller tool. The standalone package still exists independently.

**Does bundling MCP mean it now works better with a patched Firefox?** It does not
change the underlying mechanism. MCP's tools are Playwright's existing API calls, which
already worked against a patched Firefox before 1.62; bundling only changes how you
install and invoke the server.

**Can I point the bundled npx playwright mcp at invisible_playwright's binary?** The
same `--browser firefox --executable-path <path>` flags documented for the standalone
package should apply, since it is described as the same server. Flag-for-flag parity
on the bundled entrypoint specifically was not independently re-tested this session.

**Does Firefox 153.0 in the 1.62 bundle mean invisible_playwright is on that
version?** No. That is Playwright's own bundled, unmodified Firefox, used only when no
`executablePath` is given. This project pins and ships its own patched binary
separately.

**Will an MCP agent driving this Firefox be undetectable?** No tool here makes that
claim. MCP removes the packaging friction of driving a stealth engine from an agent; it
does not change your IP, your account's rate limits, or the rhythm of the agent's own
actions.

## Sources

- Playwright's own [release notes](https://playwright.dev/docs/release-notes),
  version 1.62 section ("Command line & MCP"), retrieved 2026-08-30, for the exact
  bundling quote and the bundled browser version numbers.
- [github.com/microsoft/playwright, release tag v1.62.0](https://github.com/microsoft/playwright/releases/tag/v1.62.0),
  retrieved 2026-08-30, confirming the release date and the same MCP/CLI bundling
  language.
- [github.com/microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp),
  retrieved 2026-08-30, for the standalone server's own tool surface
  (`browser_navigate`, `browser_click`, `browser_snapshot`, and related tools), used
  here to confirm scope rather than a different, narrower feature.
- This project's own [Firefox-via-MCP page](mcp-browser-server-stealth-firefox.md) and
  its documented `--browser` / `--executable-path` invocation for the standalone
  package, which this page extends rather than duplicates.

**See also:** [Give an MCP browser server a stealth Firefox engine](mcp-browser-server-stealth-firefox.md)
for the full engine-versus-profile breakdown and the standalone-package config this
page builds on, [Playwright 1.61's WebAuthn virtual authenticator](playwright-161-webauthn-passkey-virtual-authenticator.md)
for a 1.6x feature that, unlike this one, does depend on new Juggler support reaching
the patched binary, and [computer-use agents and browser fingerprint detection](computer-use-agents-browser-detection.md)
for what an agent-driven session still needs to handle itself.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. A bundled CLI is a
convenience, not a new capability; the engine underneath it is doing what it was
already doing.*
