---
title: "Does Playwright Support Firefox Extensions?"
description: "Playwright's documented extension API, launch_persistent_context plus --load-extension, is Chromium-only. Loading an .xpi into Firefox means a persistent profile, not a launch flag."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 145
---


# Does Playwright Support Firefox Extensions?

Not the way it supports them on Chromium. Playwright's documented extension-loading
API, a persistent context launched with `--disable-extensions-except` and
`--load-extension`, is explicitly Chromium-only, and Firefox has no first-class
equivalent in the public API. Firefox can still end up running an extension, but the
route there is a persistent browser profile with the `.xpi` already installed in it,
not a launch-time argument Playwright resolves for you.

This gets asked because the Chromium path is well documented and looks like it should
generalize, and because the honest answer involves a handful of old, still-open
GitHub issues instead of a clean feature page. Here is what Playwright actually
supports, what does not work, and the one route that does.

## What Playwright's documented extension support actually is

Playwright's own extension-loading guide is unambiguous about scope:

> "Extensions only work in Chromium when launched with a persistent context."

The mechanism it documents is `chromium.launchPersistentContext()` with two Chromium
command-line flags:

- `--disable-extensions-except=${pathToExtension}`
- `--load-extension=${pathToExtension}`

That page makes no mention of Firefox or WebKit anywhere. This is not an omission in
the docs; it reflects what actually exists. Chrome and Chromium extensions are loaded
through Chromium's own command-line surface, which Playwright forwards through
untouched. There is no Firefox equivalent of `--load-extension` for Playwright to
forward to, because Firefox does not expose one to launch-time flags in the first
place.

## What has actually been tried, and where it stalls

Firefox extension support has been requested since early in Playwright's life, and the
GitHub issue history is a reasonably complete map of where it breaks:

- **[Issue #2644](https://github.com/microsoft/playwright/issues/2644)**, "Support
  browser extension loading in Firefox," is closed without the feature having shipped
  as a first-class API.
- **[Issue #7297](https://github.com/microsoft/playwright/issues/7297)**, "Support
  running extensions with Firefox," reports the same shape of failure from a different
  angle: passing Chromium-style extension flags to a Firefox launch starts the browser,
  but Playwright cannot then connect to drive it.
- **[Issue #15299](https://github.com/microsoft/playwright/issues/15299)** describes
  launching Firefox with an extension already producing a confirmation popup asking a
  human to approve the install, which blocks unattended automation outright.
- **[Issue #9202](https://github.com/microsoft/playwright/issues/9202)**, closed, is
  specifically about unsigned extensions: even after setting Firefox's own
  `xpinstall.signatures.required` preference to `false`, a Firefox instance launched by
  Playwright still refused to install one.

None of these landed a documented, supported Firefox extension-loading API. Read
together, they describe the practical failure modes rather than a single missing
flag: Chromium's launch-flag approach does not translate, a raw XPI install can
trigger a manual confirmation dialog, and even the preference meant to relax Firefox's
signature enforcement has not reliably worked through Playwright's launch path. Treat
any of the four as evidence of the state of things at the time it was filed rather than
a guarantee about the exact behavior of every Firefox build going forward.

## The route that does work: a persistent profile

Firefox stores installed extensions inside its profile directory, the way it stores
cookies, history and every other piece of per-profile state. Nothing about that
mechanism runs through Playwright's launch arguments at all, which is exactly why it
still works: you are not asking Playwright to load an extension, you are asking
Firefox to open a profile that already has one installed.

```python
from invisible_playwright import InvisiblePlaywright

# first run: launch headed, pointed at a profile you intend to keep,
# and install the .xpi through Firefox's own UI - drag it onto the
# window, or use about:addons - the normal way a person would
with InvisiblePlaywright(seed=42, profile_dir="/path/to/profile", headless=False) as browser:
    page = browser.new_page()
    page.goto("about:addons")
    # install manually here, then close the browser

# every run after that: the same profile directory carries the
# extension forward, because it was never Playwright's to load
with InvisiblePlaywright(seed=42, profile_dir="/path/to/profile") as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

Without `profile_dir`, this project builds a fresh profile from the seed on every
launch, which is exactly why an extension installed in one run disappears in the
next - there is no profile left afterward to have kept it. The `profile_dir` /
persistent-profile pattern, including its other traps (a stale seed, a stored
permission that quietly changes WebRTC behavior), is covered in full in
[Playwright persistent profile: what it fixes and breaks](persistent-profiles.md).

**invisible_playwright does not add extension-loading support on top of this.** There
is no extension argument in the wrapper's launch call. What you are using is Firefox's
own, ordinary "the profile already has this installed" behavior, the same mechanism a
real desktop Firefox uses, reached through the persistent-profile option this project
already exposes for other reasons.

## The signature question, and why to test it rather than assume it

A release build of Firefox enforces that extensions be signed by Mozilla, and the
`xpinstall.signatures.required` preference is the documented way to relax that on
builds that honor it (Developer Edition, Nightly, and unbranded/ESR builds
traditionally do; a standard release build may not). Whether the specific patched
Firefox binary this project ships behaves like one of those relaxed builds for
signature purposes is not something this page can state without checking that exact
binary, so if an unsigned `.xpi` refuses to install, that preference is the first thing
to check, and confirming it against your own pinned build is the only way to know for
certain.

## Should you, though: extensions as a fingerprint surface

Getting an extension to load is a mechanical question. Whether it is a good idea is a
separate one, and this project already has an answer, at length, on
[browser extensions as a fingerprint surface](browser-extension-fingerprint.md): an
installed extension is detectable through the web-accessible resources it exposes,
through what it visibly changes on the page, and through the same override traces any
injected script leaves behind. That page also covers the specific trap of stacking a
*stealth* extension on top of an engine that already decides the same fingerprint
values in C++ - two components disagreeing with each other, which is easier to spot
than either value alone. If the extension you want is functional rather than
stealth-related, that page's guidance still applies: know what it changes, and check it
against a clean profile before trusting it in production.

## Conclusion

Chromium extension loading in Playwright is a documented, supported feature reached
through a command-line flag on a persistent context. Firefox has no equivalent, and a
handful of long-open GitHub issues describe exactly where the naive attempts stall:
the flags do not translate, a raw install can demand a manual confirmation click, and
signature enforcement has been an independent obstacle on top of both. The route that
actually works sidesteps Playwright's launch arguments entirely - install the `.xpi`
into a persistent Firefox profile through the browser's own UI once, then keep
launching against that same profile directory. It is Firefox's own mechanism, not a
Playwright feature, and it is the only one of the options here with a real track
record.

## Short answers to the questions that lead here

**Can I load a Firefox extension with Playwright the same way I load a Chrome
extension?** No. Chromium's `--load-extension` / `--disable-extensions-except` flags on
`launchPersistentContext` are Chromium-only per Playwright's own documentation. There
is no Firefox equivalent.

**Is Firefox extension support ever coming to Playwright?** Several feature requests
for it are years old and closed without a shipped API. There is no committed feature
here to point to, only the workaround below.

**How do I actually get an extension into a Playwright-driven Firefox?** Install it
once into a persistent profile through Firefox's own add-ons UI, headed, then relaunch
every session against that same profile directory with `profile_dir`.

**Why did my extension disappear on the next run?** Because a fresh profile is built
from the seed on every launch unless you pass `profile_dir`. Without a persistent
profile, there is nothing to carry the install forward.

**My unsigned extension won't install. Why?** Standard Firefox release builds require
Mozilla-signed extensions. The documented relaxation is the
`xpinstall.signatures.required` preference, but whether the specific build you are
running honors it is worth testing rather than assuming.

**Does invisible_playwright add its own extension support?** No. It exposes a
persistent-profile option for other reasons, and Firefox's own profile-based extension
storage rides along on top of that. There is no extension-specific argument in the
wrapper.

**Is adding an extension a good idea for a stealth session?** Only for functionality,
not for stealth. An extension is its own detectable fingerprint surface, and a stealth
extension stacked on an already-patched engine tends to make sessions easier to spot,
not harder.

## Sources

- Playwright's own [Chrome extensions documentation](https://playwright.dev/docs/chrome-extensions),
  retrieved 2026-08-30, for the "Chromium when launched with a persistent context"
  scope statement and the exact launch flags quoted above.
- [microsoft/playwright issue #2644](https://github.com/microsoft/playwright/issues/2644),
  retrieved 2026-08-30, "Support browser extension loading in Firefox," closed without
  a shipped equivalent.
- [microsoft/playwright issue #7297](https://github.com/microsoft/playwright/issues/7297),
  retrieved 2026-08-30, on Playwright failing to connect after a Firefox launch with
  extension flags.
- [microsoft/playwright issue #15299](https://github.com/microsoft/playwright/issues/15299),
  retrieved 2026-08-30, on a manual confirmation popup blocking unattended extension
  install.
- [microsoft/playwright issue #9202](https://github.com/microsoft/playwright/issues/9202),
  retrieved 2026-08-30, on unsigned extensions still being refused after setting
  `xpinstall.signatures.required` to `false`.
- This project's own `profile_dir` mechanism and its fingerprint-surface notes, linked
  above.

**See also:** [Browser extensions are a fingerprint surface](browser-extension-fingerprint.md)
for how a loaded extension is detected and why a stealth extension is usually the wrong
move, and [Playwright persistent profile: what it fixes and breaks](persistent-profiles.md)
for the full mechanics and traps of the `profile_dir` option this page's workaround
depends on.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. The honest answer here is a persistent profile and
a handful of old GitHub issues, not a launch flag, and pretending otherwise would just
cost someone an afternoon finding that out the hard way.*
