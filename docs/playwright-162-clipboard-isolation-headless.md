---
title: "Playwright 1.62: Headless No Longer Shares the OS Clipboard"
description: "Playwright 1.62 virtualizes the clipboard in headless mode, so navigator.clipboard no longer reads or writes the real machine's clipboard. What that fixes, and the one honest Firefox caveat in its own PR."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 44
---


# Playwright 1.62: Headless No Longer Shares the OS Clipboard

Playwright 1.62, released 24 July 2026, changed a detail easy to overlook and
occasionally expensive to hit: a headless browser calling `navigator.clipboard` used
to read and write the actual clipboard of the machine it ran on. Playwright's own
release notes describe the fix plainly:

> "The clipboard is now isolated from the operating system in headless mode, so tests
> that use navigator.clipboard no longer read or overwrite the clipboard of the
> machine running them."

That quote, the version number, and the mechanism behind it were checked against
Playwright's own release notes and the pull request that implemented the change this
session, not assumed.

## What was actually happening before this

A headless browser is still, underneath, a browser process running on a real
operating system, and before 1.62, a script calling
`navigator.clipboard.writeText()` or `readText()` in a headless session touched that
operating system's real, shared clipboard - the same one every other process on the
machine can read from and write to.

On a single developer machine running one test at a time, this rarely mattered. It
becomes a real problem the moment more than one automated browser session runs on the
same host concurrently, which is the ordinary case on a CI runner or any shared
automation box: session A writes a value to the clipboard expecting to read it back
later in the same test, and session B, running at the same time on the same machine,
overwrites it first. The result is not a clean failure with an obvious cause. It is a
test that reads back the wrong string and looks, to whoever is debugging it, like a
logic bug in the test itself, not two unrelated browser processes fighting over
one piece of shared OS state.

## How the fix works: a per-context virtual clipboard

The mechanism is documented in the pull request that implemented it,
[microsoft/playwright#41741](https://github.com/microsoft/playwright/pull/41741),
"feat(browser-context): add a virtual clipboard," which exposes a new
`browserContext.clipboard` and describes the approach directly:

> "it leverages a per-context store that shims and shares navigator.clipboard, native
> ControlOrMeta+c/ControlOrMeta+v... and document.execCommand across every page"

In other words, this is not a browser-engine patch reaching into the operating
system's clipboard API to sandbox it. It is a script, installed into every page in the
context via `addInitScript`, that intercepts calls to `navigator.clipboard` and
routes them to a store that lives inside that one browser context instead of the real
OS clipboard. Two contexts get two separate virtual clipboards; concurrent sessions on
one machine stop interfering with each other, because none of them are touching the
same underlying resource anymore.

## The part worth knowing if you are on Firefox: one named exception

Because the mechanism shims what a page can already do rather than requiring a new
low-level browser command, the same PR describes it working across engines, with one
specific, named carve-out for Firefox:

> "with one exception as Firefox does not expose clipboardData on a synthesized paste
> event"

and:

> "navigator.clipboard is only redirected where the browser already exposes it"

Read together, the practical shape is this: the virtualized `navigator.clipboard`
object itself should behave the same on Firefox as anywhere else, but a script relying
specifically on `clipboardData` off a *synthesized* `paste` event - not the modern
`navigator.clipboard` API, the older, keyboard-shortcut-driven paste-event path this
project's shim also intercepts - has one documented Firefox-specific gap. If a test
reads paste data off `event.clipboardData` rather than `navigator.clipboard.readText()`
and behaves differently on this project's Firefox than it does on Chromium, this is
the first place to look before assuming a regression in the patched engine itself.

## Why this is a real fix and only a narrow stealth footnote

Be precise about what kind of problem this solves, because it is easy to overstate.
This is primarily a test-isolation and CI-hygiene fix: it stops parallel automated
sessions on a shared host from stomping on each other's clipboard state, which is a
correctness and security-hygiene issue independent of anything a website can observe.
Prior to this change, a real desktop Firefox and a headless automated one interacted
with the clipboard the same way - both touched the real OS clipboard - so the old
behavior was not, on its own, a fingerprinting tell a page could read. No ordinary web
page can inspect "what does the browser's clipboard implementation talk to
underneath," only what `navigator.clipboard` itself returns when the page asks.

The one place the old behavior mattered for anything resembling detection was
exactly the concurrent-session case above: an operator running many headless
invisible_playwright sessions on one shared machine, some of which use the clipboard,
was exposed to genuine cross-session data leakage between otherwise-unrelated browser
identities. That is worth knowing if you run
[several sessions concurrently on one host](run-invisible-playwright-concurrently-asyncio.md)
and any of them touch the clipboard, and it is a correctness argument, not a
fingerprinting one - nothing here suggests a website could previously detect
automation by probing the clipboard, and nothing about the fix changes what a website
can observe about a session either.

One structural note worth flagging for readers of this project's other pages on
detection layers: the fix itself is implemented as an `addInitScript`-injected
JavaScript shim over `navigator.clipboard`, which is, mechanically,
[a page-level override](playwright-stealth-levels.md) of exactly the kind this
project's own docs describe as detectable in principle - an own property or a
non-native function where a real browser would have neither. In practice this is a
low-risk theoretical point rather than a live concern: the shim only activates when a
script inside the automated session calls the Clipboard API, it is not something an
arbitrary third-party page triggers on its own, and no target site has a reason to be
probing for a testing framework's own internal clipboard virtualization. It is worth
knowing the shape of the mechanism rather than treating it as invisible by default.

## Conclusion

Playwright 1.62 replaced headless mode's real-OS-clipboard behavior with a per-context
virtual clipboard, implemented as a script shim over `navigator.clipboard` and the
native copy/paste shortcuts, with one documented gap on Firefox around
`clipboardData` on synthesized paste events. The main, well-evidenced benefit is
isolation between concurrent automated sessions sharing one machine, not a
fingerprinting fix - the old behavior was not something a website could read, and the
new one does not change what a website can observe either. If you run several
invisible_playwright sessions in parallel on shared infrastructure and any of them
touch the clipboard, this closes a real cross-session data leak that existed before
1.62.

## Short answers to the questions that lead here

**What version of Playwright isolated the clipboard in headless mode?** 1.62, released
24 July 2026. Headless sessions now get a per-context virtual clipboard instead of
sharing the real operating system's clipboard.

**Did the old behavior let a website detect automation?** No. A website can only read
what `navigator.clipboard` itself returns to page-level JavaScript, not whether the
browser's implementation was talking to the real OS clipboard underneath. This was a
CI-isolation and hygiene fix, not a fingerprinting one.

**What problem did this actually cause before 1.62?** Two automated sessions running
concurrently on the same shared machine, if both used the clipboard, could overwrite
each other's clipboard content, since both were reading and writing one real,
machine-wide clipboard.

**Does this apply to Firefox, or only Chromium?** The mechanism is a cross-engine
script shim, not a Chromium-specific patch, and Playwright's own pull request describes
it working across engines with one named exception: Firefox does not expose
`clipboardData` on a synthesized `paste` event.

**How is the isolation implemented?** As a per-context virtual clipboard installed via
`addInitScript`, shimming `navigator.clipboard`, the native copy/paste keyboard
shortcuts, and `document.execCommand`, rather than a browser-engine-level sandbox.

**Does this affect invisible_playwright's stealth surface at all?** Not in a way this
page can point to concretely. The mechanism is a page-level shim, which is
mechanically the kind of thing this project's docs generally flag as detectable in
principle, but nothing about a website's own automated-detection surface reads a
testing framework's internal clipboard plumbing, so the practical exposure here is
close to none.

## Sources

- Playwright's own [release notes](https://playwright.dev/docs/release-notes),
  version 1.62 section (Announcements), retrieved 2026-08-30, for the exact clipboard
  isolation quote above.
- [microsoft/playwright pull request #41741](https://github.com/microsoft/playwright/pull/41741),
  "feat(browser-context): add a virtual clipboard," retrieved 2026-08-30, for the
  implementation mechanism, the `addInitScript`-based shim description, and the
  Firefox `clipboardData` exception quoted above.
- This project's own notes on page-level overrides as a detectable class, and on
  running concurrent sessions on shared infrastructure, linked above.

**See also:** [Three ways to make Playwright undetected](playwright-stealth-levels.md)
for the page-versus-driver-versus-engine framing this fix's own mechanism sits inside,
[run invisible_playwright concurrently with asyncio](run-invisible-playwright-concurrently-asyncio.md)
for the shared-host scenario this fix actually protects, and
[Playwright 1.62 Ships MCP In the Box](playwright-162-built-in-mcp.md) for the other
confirmed change in the same release.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. A real fix, worth
knowing about if you run sessions in parallel on shared machines, and worth not
overselling as a stealth feature it was never built to be.*
