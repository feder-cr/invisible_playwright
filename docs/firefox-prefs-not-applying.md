---
title: "Firefox preferences that silently do nothing"
description: "A Firefox preference you set can be silently ignored, with no error or log entry. The reasons it happens, in order, and how to confirm which one you hit."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 3
---


# Firefox preferences that silently do nothing

You set a preference, the browser starts, and the value is not there. No error, no
warning, nothing in the log. This page is the list of reasons, in the order they
actually happen.

It matters here because this project delivers its entire fingerprint through
preferences. Every failure mode below is one we have hit.

## The six reasons a Firefox preference silently does nothing

A Firefox preference set with no visible effect almost always falls into one of six
cases: the name is not one that build reads, the value is compiled in at build time,
it was written to the wrong file, an enterprise policy locked it, another layer
overrides it, or it was set in the parent process and never crossed into the content
process. The single confirmation step for all of them is the same: read the value back
from the page, not from the profile.

| # | Reason | What you see | How to confirm |
|---|---|---|---|
| 1 | Name not read by this build | Value stored in `about:config`, page unchanged | Read it back from the page |
| 2 | Compiled in at build time | Same name behaves differently across builds | Compare a patched build with a stock one |
| 3 | Wrong file (`prefs.js` vs `user.js`) | Value reverts after a restart | Put it in `user.js` |
| 4 | Enterprise policy lock | Value cannot be changed at all | Look for a `policies.json` |
| 5 | Another layer is louder | Pref applied, behaviour still differs | Check for injected scripts or extensions |
| 6 | Parent set, content process empty | Empty string in the page, no error | Read it in the realm that needs it |

## 1. The preference does not exist in that build

A preference name the build never reads is the most common cause of this, and it is
silent by design.

Firefox has no schema for preferences. `about:config` will happily let you create
`my.invented.pref` and store a value, and so will an automation tool. Nothing reads
it, nothing complains, and it shows up in the profile looking exactly like a
preference that works.

So a typo, a renamed pref after an upstream version bump, or a pref that only exists
in a patched build you are not running, all produce the same result: the value is
stored and ignored.

**We shipped this.** Seven tests in this repository pinned preference names the
engine never reads. They asserted the names were written, the names were written, and
the engine ignored all seven. The tests were green for as long as they existed. A
test that checks a preference was *set* is not testing anything; the check has to be
that the browser's observable behaviour changed.

The way to catch it is to read the value back from the page instead of from the
profile, with a real page-visible API such as
[`navigator.hardwareConcurrency`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/hardwareConcurrency)
or
[`Intl.DateTimeFormat().resolvedOptions()`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat/resolvedOptions):

```js
// what the page sees is the only thing that counts
navigator.hardwareConcurrency
Intl.DateTimeFormat().resolvedOptions().timeZone
```

## 2. It is a build-time preference, not a runtime one

Some values are compiled in. Firefox's `StaticPrefs` mechanism generates C++ from a
list at build time, and a subset of those are read once at startup or baked into the
binary. Setting them later, or setting them in a profile, does nothing.

This is why a patched build and a stock build can respond differently to the same
preference: the patch may have made a value readable that upstream compiles as a
constant, or the reverse.

## 3. `user.js` versus `prefs.js`, and who writes last

Two files in a profile directory hold preferences, and they behave differently:

- **`prefs.js`** is Firefox's own. It is rewritten when the browser exits. Editing it
  while the browser is running is pointless, because your edit is overwritten on
  close.
- **`user.js`** is yours. It is read at every startup and applied over whatever
  `prefs.js` says.

So if a value keeps reverting, you almost certainly wrote it to `prefs.js` on a
running profile. Put it in `user.js` instead and it wins on every launch.

Automation tools that accept a preference dictionary generally build one of these
for you, which is why the two-file distinction only bites when you are hand-editing a
[persistent profile](persistent-profiles.md).

## 4. Enterprise policy overrides everything

A `policies.json` next to the binary, or an OS-level policy on a managed machine,
takes precedence over both files and can lock a preference so nothing can change it.
This is rare on a developer machine and common on a corporate one, and it produces a
value that looks set in `about:config` while the browser silently ignores it.

## 5. The preference works and something else is louder

The value is applied, and the observable behaviour still does not change, because a
different layer answers first. A JavaScript shim injected into the page that
redefines the same property will win, because it runs after the browser has already
reported the value. Two mechanisms setting the same thing is
[the recurring theme of these pages](playwright-stealth-levels.md).

## 6. It works in the parent process and is empty in the content process

This failure mode costs the most time to find, because it produces no error anywhere.

Firefox runs as a tree of processes - one parent, one content process per
site-isolated origin, plus a handful of others - and a preference set in the parent
is not automatically visible everywhere. Numeric and boolean preferences generated
through Firefox's static preference mechanism get baked into a shared snapshot handed
to every content process at launch, so those propagate for free. **String
preferences do not go through that snapshot.** A content process asking for one that
was never explicitly allowed through gets back an empty string - not an error, not a
fallback to a default, just silence, so this is easy to spend a long time on before
noticing the value never crosses the process boundary at all.

This matters most for exactly the values people reach for at runtime: a
[GPU vendor and renderer string](renderer-string-vs-render.md), a spoofed identifier,
anything that is text rather than a number or a flag.
If the string travels through the same static-preference mechanism as a number (with
a declared default), it gets the same free propagation. If it's read as a plain
dynamic string preference instead, it needs to be explicitly allowed through, or every
content process silently sees nothing.

## A debugging order that works

1. **Read it back from the page, not from the profile.** If `about:config` shows your
   value but the page does not see it, the preference is not the one the engine
   reads. That is reason one.
2. **Check the value exists in a stock browser.** Open `about:config` in a normal
   Firefox of the same version and search for the name. If it is not there as an
   existing entry, you are inventing it.
3. **Check nothing else sets the same surface.** A stealth plugin, a framework's
   fingerprint generator, an extension.
4. **Check the profile is the one you think.** Automation tools frequently create a
   temporary profile and discard it, so the persistent one you edited is not the one
   that launched.

## Why we do it this way anyway

Preferences are the contract between the patched engine and the Python side of this
project. Nothing about the fingerprint is hardcoded in either: the profile generates
values, the preferences carry them, and the engine reads them.

That has a real cost, which is this whole page. It also has the property that made it
worth paying: the same binary can be any machine, and changing what it claims does
not mean rebuilding it. A tool that hardcodes its fingerprint has none of these
failure modes and cannot vary at all.

## Short answers to the questions that lead here

**Why is my Firefox preference ignored?** Usually because it is read once at startup,
because something writes it after you do, because it is the wrong file, because the
preference no longer exists, or because it exists but nothing reads it.

**How do I check whether a preference actually applied?** Read the value back from the
page, not from the profile. The profile records your intent; the page shows the result.

**What is the difference between `user.js` and `prefs.js`?** `user.js` is applied at
every start and overrides. `prefs.js` is written by the browser and can be rewritten
under you.

**Does `about:config` show the truth?** It shows the current value in the parent
process, which is not always what a content process or a given realm is using.

**Why would a documented preference do nothing?** Because it was removed upstream and
left in place, or it was never wired to anything. A preference existing is not evidence
that anything reads it.

**See also:** [what `privacy.resistFingerprinting` changes](resist-fingerprinting.md),
which is a preference whose effects are large and mostly unwanted here, and
[the three levels](playwright-stealth-levels.md) for why a preference and a page
script fight.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
Reason one is the one that cost us a real bug, so it is first rather than last.*
