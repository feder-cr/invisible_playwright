---
title: "invisible_playwright vs rebrowser-patches: the same CDP fix"
description: "rebrowser-patches fixes the Runtime.enable CDP leak on Chromium, the same way Patchright does. It reaches neither Firefox nor fonts, GPU, canvas or audio."
parent: "Comparisons"
nav_order: 11
---


# invisible_playwright vs rebrowser-patches: the same CDP fix

**invisible_playwright and rebrowser-patches do not compete; they work at
different layers.** rebrowser-patches fixes a Chromium CDP leak (`Runtime.enable`)
inside Puppeteer and Playwright's own source, while invisible_playwright patches
Firefox itself at the C++ level. rebrowser-patches does not support Firefox and
does not claim to touch fonts, GPU, canvas or audio, so the two solve different
problems rather than the same one differently.

Like the [Patchright comparison](vs-patchright.md), this one is between tools at
different layers rather than direct competitors. What makes rebrowser-patches worth
its own page next to that comparison is a detail worth knowing on its own: the two
projects fix the same underlying leak, on the same protocol, by close to the same
method, having been built independently of each other.

## What rebrowser-patches actually changes

rebrowser-patches targets Puppeteer's Node.js bindings and Playwright's Node.js and
Python bindings on Chromium, patching the library's own source rather than anything
in the browser binary. Its core fix addresses the CDP command `Runtime.enable`:
automation libraries call it to manage the execution contexts they need for
evaluating JavaScript on a page, and that call itself is a documented, widely
deployed detection signal, checked for specifically by commercial anti-bot products
because ordinary browsing never triggers it.

The fix disables the automatic `Runtime.enable` call and instead manually creates
execution contexts with IDs the page can't correlate back to the automation
session, resolving the ones it needs at evaluation time through other means. Two
smaller patches address related CDP fingerprints: the generic `sourceURL` given to
injected scripts, and the naming of an internal "utility world" context Playwright
creates, both of which can otherwise read as automation-shaped rather than
organic.

## Where this lines up with Patchright almost exactly

[Patchright's own fix](vs-patchright.md) for the identical `Runtime.enable` leak is
described in its documentation as executing JavaScript through isolated execution
contexts instead of the default path - manually managed context creation, avoiding
the automatic call that gives the session away. That is functionally the same
strategy rebrowser-patches converged on, for the same root cause, on the same
protocol. Neither project is a fork of the other; both independently landed on
"stop calling `Runtime.enable` automatically and manage contexts by hand" as the
fix, which says more about how narrow the actual solution space is for this
specific leak than it does about either project copying the other.

## Why this doesn't compete with an engine-level project

Every rebrowser-patches fix operates on the CDP session between the driver and a
Chromium-family browser. Firefox has no CDP domains to patch in the first place -
[it speaks Juggler instead](firefox-vs-chromium-antidetect.md), an entirely
different automation protocol with its own separate leaks, unrelated to anything
`Runtime.enable` touches. rebrowser-patches doesn't support Firefox, by design
rather than by gap: there is no equivalent surface on this engine for these
specific patches to apply to.

rebrowser-patches is also explicit, in its own documentation, about what it
doesn't touch: proxies, user agent consistency, and canvas/WebGL fingerprinting
are named directly as separate concerns the patches don't address, alongside a
warning that page-level JavaScript overrides of browser internals (via
[`Proxy`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Proxy)
objects or [`Object.defineProperty`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object/defineProperty)) are themselves detectable - the same
overwrite-is-a-signal point [made elsewhere on this site](tostring-native-code-detection.md)
about any page-level patch, regardless of which tool applies it.

## How to actually choose

- **Need Firefox specifically?** This project or [Camoufox](vs-camoufox.md). Neither
  rebrowser-patches nor Patchright reaches this engine at all.
- **Committed to Chromium, chasing the `Runtime.enable` leak specifically?**
  Either rebrowser-patches or Patchright fixes it, by close to the same method;
  picking between them is mostly about which project's release cadence and
  packaging you'd rather depend on, not about a difference in what gets fixed.
- **Committed to Chromium, but the tells are about the machine** (fonts, GPU,
  canvas, audio)**?** Neither project reaches that surface, by its own account.
  That needs engine-level work, which today means Firefox.

## Conclusion

rebrowser-patches and this project don't overlap in any way that makes "which one"
a real question - different engine, different protocol, different layer. What's
worth taking from the comparison is narrower and more useful: two projects, built
independently, looking at the same CDP leak, arrived at essentially the same fix.
That's a reasonable signal that the fix itself is close to the only sound answer
for this specific leak, on this specific protocol - which is exactly the kind of
convergence worth noticing when two unrelated projects find it separately.

## Short answers to the questions that lead here

**Is rebrowser-patches the same as Patchright?** No, separate projects with no
shared code, but they fix the identical `Runtime.enable` CDP leak by close to the
same technique, arrived at independently.

**Does rebrowser-patches work with Firefox?** No. Its patches apply to Puppeteer
and Playwright's Chromium-side CDP session, which has no equivalent on Firefox.

**Does rebrowser-patches fix fingerprinting?** No, by its own documentation.
Proxies, user agent, and canvas/WebGL fingerprinting are explicitly named as
separate concerns it doesn't address.

**Can I use rebrowser-patches and a fingerprint tool together on Chromium?** Yes,
and that's the pattern its own documentation points toward - the CDP-session fix
and machine-level fingerprint work solve different problems.

**See also:** [invisible_playwright vs Patchright: driver vs engine](vs-patchright.md),
for the closely related comparison this page assumes as context, and
[why every override carries its source](tostring-native-code-detection.md), for
the general version of the page-level-patch caveat rebrowser-patches states about
its own remaining gaps.

## Sources

- [rebrowser-patches' own repository and documentation](https://github.com/rebrowser/rebrowser-patches),
  retrieved 2026-08-29, for its stated scope, its `Runtime.enable` fix mechanism, and its
  own named gaps (proxies, user agent, canvas/WebGL fingerprinting, and the detectability
  of page-level JavaScript overrides).
- [The Patchright comparison](vs-patchright.md) on this site, for the parallel
  fix and its own sourcing.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, on two unrelated projects finding the same
answer to the same leak from opposite directions.*
