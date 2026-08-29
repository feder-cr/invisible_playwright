---
title: "invisible_playwright vs playwright-with-fingerprints"
description: "playwright-with-fingerprints injects real-device fingerprints from a remote service into a Windows-only, ageing Chromium build. What that trade costs."
parent: "Comparisons"
nav_order: 13
---


# invisible_playwright vs playwright-with-fingerprints

`playwright-with-fingerprints` and `invisible_playwright` answer the same
question - where does a believable fingerprint come from - at different layers.
`playwright-with-fingerprints` fetches a fingerprint from a remote service and
injects it into a running, Windows-only Chromium session. `invisible_playwright`
derives the fingerprint locally from a seed inside a patched Firefox engine and
reports it from the engine itself. That single difference decides platform
support, engine coverage, and whether an external service sits in your
dependency chain.

This page is for anyone weighing the two, and it leans on what
`playwright-with-fingerprints`'s own README states directly rather than on
black-box testing.

## invisible_playwright vs playwright-with-fingerprints at a glance

The two tools converge on the goal - a fingerprint that looks like a real
device - and diverge on where the value is produced and where it lands.

| Dimension | playwright-with-fingerprints | invisible_playwright |
|---|---|---|
| Fingerprint source | Remote hosted service returning real-device values | Local seed, generated offline |
| Where the value lands | Injected into the page of a running session | Reported by the patched engine itself |
| Engine | Chromium, pinned to a specific ageing build | Firefox, patched at the C++ level |
| Platform | Windows only (by its own docs) | Linux and Windows |
| External dependency | The remote service must be reachable each session | None between the seed and the fingerprint |
| Stability (self-stated) | Beta; critical bugs to be expected | - |

## What playwright-with-fingerprints actually does

playwright-with-fingerprints sources fingerprint values from a remote service and
applies them through the page, rather than generating them locally. The plugin calls out to
`FingerprintSwitcher`, a hosted service the same organisation runs, which returns
a set of browser property values sourced from real devices. The plugin then
replaces the corresponding properties in a running Chromium session with those
values through the page - the same injection layer
[every init-script stealth tool on this site uses](vs-playwright-stealth.md),
with a real-device-sourced value on the other end of the swap instead of a
locally generated one.

That is a genuinely different answer to "where does a believable fingerprint
come from" than most tools give. It does not change which layer the value lands
on once it is applied.

## What its own README states, read directly

Three details are worth naming plainly, because they come from the project's own
documentation rather than from testing it.

**Windows only.** Stated directly: the plugin cannot be installed or used on
Linux, macOS, or any system besides Windows. A pipeline that runs on Linux
containers, which is most automation infrastructure, is not a candidate for this
tool at all, independent of anything else.

**Pinned to a specific, ageing Chromium build.** The README names a supported
engine version explicitly - `146.0.7680.80` at the time this page was checked.
[Playwright's own default managed Chromium is several major versions ahead of
that as of mid-2026](chromium-is-not-chrome.md). Whether that gap matters depends
entirely on whether a target checks Chromium version consistency against anything
else the session reports, and a pinned, ageing engine version is exactly the kind
of detail that stops mattering right up until something checks it.

**Self-described as beta.** The README states plainly that bugs, including
critical ones, should be expected. That is an honest disclosure, not a criticism,
but it is worth weighing against a use case where a mid-session failure has a real
cost.

## The dependency this project does not have

Because playwright-with-fingerprints' values come from a remote service it calls
out to, that service is part of the runtime path on every session. Their availability and
their consistency depend on that service being reachable and returning what the
plugin expects, every time one is needed. That is a real architectural difference
from every locally-seeded approach covered elsewhere on this site. A locally
derived identity - [a seed producing the same fingerprint every time,
offline](pinning.md) - has no equivalent external dependency to fail.

## How to actually choose

- **Windows-only pipeline, comfortable depending on an external fingerprint
  service, do not need Firefox?** This tool does something none of the
  locally-generated options do: fingerprints sourced from real devices rather
  than sampled statistics.
- **Need Linux or macOS, or want no external service in the dependency chain?**
  This tool is not an option regardless of anything else about it - the platform
  restriction alone decides it.
- **Concerned about engine-version consistency checks?** [Verify what Chromium
  version the plugin's pinned engine actually reports against what a current
  session claims](chromium-is-not-chrome.md), rather than assuming either answer,
  and [test which layer a target actually checks](how-to-test-bot-detection.md)
  instead of guessing which gap matters.

## Conclusion

playwright-with-fingerprints answers "where does a believable fingerprint come
from" by sourcing it from real devices through a hosted service, rather than
generating it locally - a genuine, different answer, not a strictly worse one.
Its own documentation states the terms plainly: Windows only, a specific pinned
Chromium build, and a service that has to be reachable every session. Where
those terms are acceptable, it does something none of the locally-generated
options on this site do. Where they aren't - Linux infrastructure, no interest
in an external dependency, or a need for engine-level coverage - the fingerprint
source isn't the axis to be deciding on in the first place.

## Short answers to the questions that lead here

**Is playwright-with-fingerprints the same kind of tool as invisible_playwright?**
No. Both aim at a believable fingerprint, but playwright-with-fingerprints injects
remote, real-device values into a Windows-only Chromium page, while
invisible_playwright derives the fingerprint locally from a seed inside a patched
Firefox engine and reports it from the engine itself.

**Does playwright-with-fingerprints work on Linux or macOS?** No, by its own
documentation. Windows only.

**Where do its fingerprint values come from?** A remote, hosted service the same
project operates, rather than a local generator - checked directly in its README,
not assumed.

**What Chromium version does it support?** A specific, named, pinned version that
was already several releases behind Playwright's own current default when checked.

**Is it stable?** Its own README says it is in beta, with bugs including critical
ones to be expected.

**See also:** [invisible_playwright vs fingerprint-suite](vs-fingerprint-suite.md),
another locally-generated fingerprint approach at the same injection layer, and
[Chromium is not Chrome, and detectors know the difference](chromium-is-not-chrome.md),
for why an engine version pin is worth checking rather than assuming is harmless.

## Sources

- [`bablosoft/playwright-with-fingerprints`](https://github.com/bablosoft/playwright-with-fingerprints),
  its own repository and README, read directly, for the platform restriction, the
  pinned engine version, the beta status, and the remote-service dependency,
  retrieved 2026-08-29.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, with no remote service in the path between a
seed and the fingerprint it produces.*
