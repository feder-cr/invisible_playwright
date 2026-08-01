---
title: "invisible_playwright vs playwright-with-fingerprints: whose device, whose engine"
description: "playwright-with-fingerprints injects fingerprint values sourced from a remote paid service into a Windows-only Chromium session, pinned to a Chrome build several versions behind current. Different layer, different engine coverage, different dependency."
parent: "Comparisons"
nav_order: 13
---


# invisible_playwright vs playwright-with-fingerprints: whose device, whose engine

`playwright-with-fingerprints` takes a different approach from most of the tools on
this site: instead of generating a fingerprint locally, it fetches one from a remote
service and injects it into the page. Worth understanding what that trade actually
buys, and what its own documentation says it costs.

## What it actually does

The plugin calls out to `FingerprintSwitcher`, a hosted service the same
organisation runs, which returns a set of browser property values sourced from real
devices. The plugin then replaces the corresponding properties in a running Chromium
session with those values through the page - the same injection layer
[every init-script stealth tool on this site uses](vs-playwright-stealth.md), with a
real-device-sourced value on the other end of the swap instead of a locally generated
one.

That's a genuinely different answer to "where does a believable fingerprint come
from" than most tools give. It does not change which layer the value lands on once
it's applied.

## What its own README states, read directly

Three details worth naming plainly, because they come from the project's own
documentation rather than from testing it:

**Windows only.** Stated directly: the plugin cannot be installed or used on Linux,
macOS, or any system besides Windows. A pipeline that runs on Linux containers, which
is most automation infrastructure, is not a candidate for this tool at all,
independent of anything else.

**Pinned to a specific, ageing Chromium build.** The README names a supported engine
version explicitly - `146.0.7680.80` at the time this page was checked. [Playwright's
own default managed Chromium is several major versions ahead of that as of
mid-2026](chromium-is-not-chrome.md). Whether that gap matters depends entirely on
whether a target checks Chromium version consistency against anything else the
session reports - and a pinned, ageing engine version is exactly the kind of detail
that stops mattering right up until something checks it.

**Self-described as beta.** The README states plainly that bugs, including critical
ones, should be expected. That's an honest disclosure, not a criticism - but it's
worth weighing against a use case where a mid-session failure has a real cost.

## The dependency this project doesn't have

The fingerprint values come from a remote service this plugin calls out to, not from
a local generator. That's a real architectural difference from every locally-seeded
approach covered elsewhere on this site: the values, their availability, and their
consistency depend on that service being reachable and returning what the plugin
expects, on every session that needs one. A locally derived identity - [a seed
producing the same fingerprint every time, offline](pinning.md) - has no equivalent
external dependency to fail.

## How to actually choose

- **Windows-only pipeline, comfortable depending on an external fingerprint service,
  don't need Firefox?** This tool does something none of the locally-generated
  options do: fingerprints sourced from real devices rather than sampled statistics.
- **Need Linux or macOS, or want no external service in the dependency chain?** This
  tool is not an option regardless of anything else about it - the platform
  restriction alone decides it.
- **Concerned about engine-version consistency checks?** [Verify what Chromium
  version the plugin's pinned engine actually reports against what a current session
  claims](chromium-is-not-chrome.md), rather than assuming either answer.

## Short answers to the questions that lead here

**Does playwright-with-fingerprints work on Linux or macOS?** No, by its own
documentation. Windows only.

**Where do its fingerprint values come from?** A remote, hosted service the same
project operates, rather than a local generator - checked directly in its README,
not assumed.

**What Chromium version does it support?** A specific, named, pinned version that
was already several releases behind Playwright's own current default when checked.

**Is it stable?** Its own README says it's in beta, with bugs including critical ones
to be expected.

**See also:** [invisible_playwright vs fingerprint-suite](vs-fingerprint-suite.md),
another locally-generated fingerprint approach at the same injection layer, and
[Chromium is not Chrome, and detectors know the difference](chromium-is-not-chrome.md),
for why an engine version pin is worth checking rather than assuming is harmless.

## Sources

- `bablosoft/playwright-with-fingerprints`'s own repository and README, read
  directly, for the platform restriction, the pinned engine version, the beta
  status, and the remote-service dependency.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, with no remote service in the path between a
seed and the fingerprint it produces.*
