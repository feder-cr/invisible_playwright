---
title: "puppeteer-extra-plugin-stealth hasn't shipped a real update since 2024"
description: "The plugin that popularized JavaScript-level stealth patches has had no meaningful commits in two years, while the detection it was built to defeat kept moving. What that means depends on which of its checks you're actually relying on."
parent: "Comparisons"
nav_order: 8
---


# puppeteer-extra-plugin-stealth hasn't shipped a real update since 2024

A lot of automation code still imports `puppeteer-extra-plugin-stealth`, and a lot of
tutorials still recommend it as the default answer to "how do I stop getting flagged."
The part that's easy to miss: its home repository's last substantive commit is from
July 2024. If you checked it two years ago and never checked again, this page is the
update.

This covers `playwright-extra` too, the Node package that wraps the same plugin for
Playwright instead of Puppeteer. Both ship from the same repository, so the
maintenance status and everything below applies to it identically.

## What the plugin actually does, and why that part hasn't changed

The plugin's approach has always been the same, and it's a reasonable one for what
it targets: before the page's own scripts run, inject a set of small JavaScript
patches that make individual automation-detectable properties answer the way a real
browser's would. `navigator.webdriver` returns `undefined` instead of `true`. A
handful of other CDP-visible artifacts get papered over the same way. Each patch is a
few lines, targeted at one specific property a detector might check.

That's still exactly what it does today, because the technique itself doesn't need
to change to keep working on the properties it already covers. `navigator.webdriver`
being `undefined` is still `navigator.webdriver` being `undefined` whether the patch
was written in 2021 or updated last week.

## Why "unmaintained" matters anyway, for a plugin whose old patches still work

The problem isn't that the existing patches stopped working. It's that detection
services don't stand still, and a plugin's whole value is in the list of checks it
covers - a list that was frozen the day active development stopped.

Two years is long enough for new CDP-visible artifacts to surface, for new browser
versions to change what a stock Chromium object looks like well enough that an old
patch becomes the tell instead of the fix, and for detection vendors to simply start
checking properties nobody had bothered to patch before. None of that requires the
old patches to break. It only requires the list of things worth checking to keep
growing while the list of things being patched stopped.

A maintained fork or a different plugin can, in principle, keep pace. An unmaintained
one structurally can't, and there's no version of "the patches still work" that
changes that.

## The ceiling this was always going to hit, maintained or not

This is worth separating from the maintenance question, because it's the more
durable point. [Patching individual properties from the page, one at a time, is a
different strategy from being a real browser](vs-playwright-stealth.md), and it has
the same shape whether the patch list is current or two years stale.

Every property this class of plugin fixes is fixed **after the fact**: the browser's
real, honest properties get overwritten by a script that runs before the page's own
code. That overwrite is itself detectable through several completely different
mechanisms - checking whether `Function.prototype.toString` on the patched getter
still returns `[native code]`, checking `Object.getOwnPropertyDescriptor` for a
descriptor shape a native property wouldn't have, or simply finding one of the
several dozen properties nobody wrote a patch for yet. A frozen patch list makes this
worse by construction, but a perfectly current one would still have the same
fundamental gap: the browser's TLS handshake, its real GPU-backed WebGL output, its
real font rendering are not JavaScript-visible properties, so no amount of page-level
patching touches any of them at all.

## What to check if you're relying on this today

1. **Confirm which version you're actually running**, and check it against the
   repository's own commit history rather than the version number alone - a version
   bump for packaging reasons isn't the same as a new detection patch.
2. **Test against the specific checks you care about**, not a general "is it
   detected" verdict. [A verdict-only test can't tell you which of dozens of
   possible checks is the one that matters for your case](how-to-test-bot-detection.md).
3. **Separate the property-level question from everything else.** If a site blocks
   you on TLS fingerprint, IP reputation, or behavioral signals, no update to this
   plugin, current or not, was ever going to be the fix - those aren't properties it
   touches at any version.

## Short answers to the questions that lead here

**Is puppeteer-extra-plugin-stealth still maintained in 2026?** Its home repository's
last substantive commits are from mid-2024. The project isn't formally archived, but
there's been no meaningful ongoing development since.

**Do the existing patches in puppeteer-extra-plugin-stealth still work?** The
patches that exist still do what they were written to do. What's missing is
everything a detector might check that wasn't on the list two years ago.

**What should I use instead?** Depends what you actually need. If the goal is
patching a short, specific list of CDP-visible properties and nothing more, a more
recently maintained fork covers the same ground. If the goal is not needing this
category of patch at all - because the browser's real properties were never
overwritten in the first place - that's a different architecture, not a newer
version of the same one.

**Does this affect Puppeteer specifically, or the whole category?** The specific
repository is Puppeteer-side, but the ceiling described above applies to any
page-level property-patching approach, on any driver, maintained or not.

**See also:** [invisible_playwright vs playwright-stealth: page vs engine](vs-playwright-stealth.md),
the same architectural question asked about this plugin's Playwright-side port, and
[three ways to make Playwright undetected](playwright-stealth-levels.md), which
places this whole category at one specific level rather than the top of it.

## Sources

- The plugin's own public repository and commit history, checked directly rather
  than assumed from its continued popularity in tutorials and Stack Overflow answers.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on why a two-year-old patch list and a same-day
patch list share the exact same structural gap.*
