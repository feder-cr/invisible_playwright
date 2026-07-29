---
title: "invisible_playwright vs Patchright: driver vs engine"
description: "Patchright patches the Playwright driver and stays on Chromium. This project patches Firefox itself. They fix different tells at different layers, and for some jobs the honest answer is both, on different engines."
parent: "Comparisons"
nav_order: 5
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Comparisons",
      "item": "https://feder-cr.github.io/invisible_playwright/comparisons.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "invisible_playwright vs Patchright: driver vs engine"
    }
  ]
}
</script>

# invisible_playwright vs Patchright: driver vs engine

This comparison looks different from most on this site, because these two tools do not
compete for the same job. [Three ways to make Playwright undetected](playwright-stealth-levels.md)
puts them on different rungs: Patchright patches the **driver**, this project patches the
**engine**. Same automation problem, two layers apart.

That makes "which is better" the wrong question. The useful one is which tells live at
which layer, and what each tool leaves for you to solve elsewhere.

## What Patchright actually changes

Patchright is a drop-in fork of Playwright itself, not a browser. It patches the
automation framework's own code so the **Chromium DevTools Protocol** stops leaking the
things that give it away: it avoids `Runtime.enable`, which is one of the most commonly
checked CDP artifacts, by executing JavaScript through isolated execution contexts
instead; it disables `Console.enable` outright, at the cost of console functionality not
working under Patchright at all; and it changes the default launch flags, adding
`--disable-blink-features=AutomationControlled` and dropping `--enable-automation`, so
`navigator.webdriver` is never set to begin with rather than being set and then patched
back to false.

That last distinction is the entire case for driver-level patching, and it is worth
stating precisely because [the levels page](playwright-stealth-levels.md) makes the same
point about our own layer choice: a value that was never set is not the same as a value
that was set and then hidden. There is nothing for a detector to find, versus something a
detector has to be persuaded is normal.

## Why these do not compete: engine and layer are both different

**Patchright is Chromium-only.** Its own documentation says so directly: Firefox and
WebKit are not supported. So if your job needs Firefox, this is not a decision between
two options, it is already decided.

**The layer is different too.** Patchright's target is the CDP session between the
driver and the browser. This project's target is the browser's own C++, which is a
different protocol entirely: [Firefox does not speak CDP](firefox-vs-chromium-antidetect.md)
in the way Chromium does; Playwright drives it through Juggler, an internal Firefox
protocol we patch directly and whose [own compatibility contract with the Playwright
client breaks in its own specific way](playwright-protocol-drift.md), unrelated to
anything CDP does. Every tell Patchright fixes is specific to the CDP session,
which means it is not a tell our project has, and not one this project's patches address
either, because it does not arise on this engine.

## What Patchright fixes that we never had to

Worth naming concretely, because it shows how CDP-specific the whole category is.
`Runtime.enable` and `Console.enable` are CDP domain calls; Firefox has no CDP domains to
enable. The automation-controlled blink feature flag is a Chromium build flag; Firefox
has nothing named after it. None of these three fixes has a Firefox equivalent to patch,
because the surface they patch does not exist here.

That does not mean Firefox has no automation-layer tells of its own. It has different
ones, and we found four in the driver layer of our own stack rather than the engine:
JIT-disabling timing, driver frames appearing in stack traces, evaluated code labelled as
a debugger evaluation, and a helper script that ran in the page's realm before the page's
own code did. [Those four are documented here](debugger-timing-detection.md). The lesson
generalises past either tool: every automation layer has its own protocol, and every
protocol leaks its own way.

## What Patchright does not touch, by its own account

Patchright is explicit that it patches the driver, not the machine. The GPU Chromium
reports, the fonts it enumerates, the audio stack, the canvas output: all of that is
still whatever the underlying Chromium build actually has, because nothing in Patchright
changes it. Pairing driver-level stealth with page-level or engine-level fingerprint work
is normal, and Patchright's own documentation does not claim otherwise.

Patchright is also candid about two of its own remaining gaps: init scripts injected for
page setup "can be detected by timing attacks," and running the full Playwright test
suite against Patchright, it "passes most, but not all" of it. That second point has a
practical cost worth flagging if you are choosing this route: a patched driver is a
fork you now depend on tracking Playwright's own releases, and "most, but not all" means
some API surface may behave differently than documented Playwright behaviour.

## The Chromium-is-not-Chrome question, restated for a fork

Because Patchright is a Playwright fork, it launches the same open-source Chromium
Playwright ships by default, with the same missing proprietary codecs, unless you also
set `channel="chrome"` yourself. [That gap is not a driver-level problem](chromium-is-not-chrome.md)
and patching the driver does not close it. It is worth keeping the two concerns separate
when evaluating any Chromium-based stealth tool: driver-level patches fix automation
tells, they do not fix codec or DRM tells, and nothing in Patchright's README claims they
do.

## How to actually choose

- **Need Firefox specifically?** This project or Camoufox; Patchright does not run on
  either engine.
- **Committed to Chromium, and the tells you are chasing are CDP-specific?** Patchright
  fixes exactly that category, and does it without a browser fork to maintain.
- **Committed to Chromium, but the tells are about the machine** (fonts, GPU, canvas,
  audio)**?** A driver patch does not reach that surface on any engine. You need
  something at the engine layer, and today that means Firefox.
- **Need proprietary codecs on Chromium regardless of driver?** Set `channel="chrome"`
  yourself; no driver-level fork changes that Playwright default.
- **Undecided?** Test against your actual target with both approaches and read what each
  one still gets flagged for, using [a method that checks the signal fired, not just that
  a verdict came back clean](how-to-test-bot-detection.md).

## Conclusion

Patchright and this project solve adjacent problems on different engines. Patchright
closes the CDP-session gap on Chromium at essentially no engineering cost to you: it is a
drop-in fork. This project closes the surfaces no driver patch can reach, at the cost of
a browser you now depend on us to keep building. Neither replaces the other's layer, and
on Chromium specifically, driver-level and page-level stealth are commonly used together
for exactly that reason.

## Short answers to the questions that lead here

**Is Patchright better than invisible_playwright?** They are not comparable directly.
Patchright patches the Chromium driver; this project patches Firefox's engine. Pick by
engine first, then by which tells matter for your target.

**Does Patchright work with Firefox?** No. Its own documentation states only
Chromium-based browsers are supported.

**Does Patchright fix fingerprinting?** No, by its own scope. It fixes CDP-session
automation tells, not fonts, GPU, canvas, or audio, which is a different layer.

**Can I use Patchright and a fingerprint tool together?** Yes, and that is the normal
pattern on Chromium: driver-level stealth for the automation tells, something else for
the machine-level ones.

**Does Patchright fix the Chromium-is-not-Chrome codec problem?** No. That is a
Playwright launch setting (`channel="chrome"`), unrelated to what the driver patch
changes.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
for the full map this comparison sits inside; [Firefox or Chromium for anti-detect](firefox-vs-chromium-antidetect.md),
for the engine question Patchright's Chromium-only scope decides for you; and
[four leaks from our own driver layer](debugger-timing-detection.md), for what the
equivalent problem looks like on Firefox.

## Sources

- Patchright's own GitHub documentation, read 2026-07-28, for its stated scope
  (Chromium-only), its `Runtime.enable` / `Console.enable` / launch-flag fixes, and its
  own admitted gaps (init-script timing attacks, "passes most, but not all" of the
  Playwright test suite).
- This project's own patch catalogue and `debugger-timing-detection.md` for the
  equivalent Firefox-side automation-layer fixes.
- Where a claim about Patchright's internals could not be checked beyond its own
  documentation, this page does not assert one.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Patchright solves a real problem on Chromium that this
project does not have on Firefox, and does not try to solve a problem it never claimed to.*
