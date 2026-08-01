---
title: "invisible_playwright vs nodriver and undetected-chromedriver"
description: "Both are Chrome-only tools with their own API, not Playwright forks. One patches a chromedriver binary, the successor drops the binary and speaks CDP directly. Neither claims to touch fingerprinting or your IP, and both say so themselves."
parent: "Comparisons"
nav_order: 6
---


# invisible_playwright vs nodriver and undetected-chromedriver

These two are one lineage, not two separate comparisons. `undetected-chromedriver` patched
Selenium's chromedriver binary to stop Chrome announcing itself as automated.
`nodriver`, from the same author, is its successor: it removes the chromedriver binary
and the WebDriver protocol entirely, and speaks the Chrome DevTools Protocol directly.

The comparison with this project is unusual for a reason worth stating up front: these
are not Playwright tools. They have their own API. That changes what "choosing between
them" actually means.

## What they are, and the thing that makes this comparison different

Every other tool on this site's comparison pages is a Playwright fork or a browser you
launch through Playwright. `nodriver` and `undetected-chromedriver` are neither. Their own
documentation is explicit: nodriver's pitch is "no more webdriver, no more selenium," with
its own async interface and its own methods (`tab.find()`, `tab.select()`, `tab.xpath()`).
There is no `page.goto()` here, because there is no Playwright underneath it.

So the decision is not "which of these two produces a less detectable browser." It is
whether you are willing to leave the Playwright ecosystem at all. If your project has
existing Playwright code, page objects, or test infrastructure, adopting either of these
means rewriting the automation layer, not swapping a launch call the way
[most of the tools on this site's comparison pages](playwright-stealth-levels.md) let you.

## What undetected-chromedriver actually patches

Selenium drives Chrome through `chromedriver`, a separate binary, and that binary used to
leave literal tells in the page: a global variable named after the string `cdc_`,
placed there by the driver itself. Early versions of undetected-chromedriver removed and
renamed that variable; later versions changed approach and instead prevent the tell from
being injected in the first place, which the project's own changelog frames as the more
robust fix. [We cover the same variable, and why renaming beats removing, on our own
version of this problem](cdc-variable-explained.md) - it turns out the exact lesson
generalises past Selenium entirely: a tell you rename is still a tell if its presence is
what gets checked, not its name.

## What nodriver changes by dropping the binary

nodriver's argument is that removing the chromedriver binary and the WebDriver protocol
removes an entire category of tells at once, rather than patching them one at a time.
Talking to Chrome over CDP directly, with no intermediate driver process, also removes a
process-tree and port signature that a patched driver still has to explain away. Its own
documentation frames the performance gain as a side effect of the same architectural
change, not the point of it.

## What neither one claims to touch

Both projects are unusually direct about their own scope, more so than most tools in this
space, and it is worth taking them at their word rather than assuming more.
undetected-chromedriver's documentation states plainly that the package does not hide your
IP address and does not handle device fingerprinting. Nothing in either project's
documentation claims to change the GPU string Chrome reports, the fonts it enumerates, the
canvas output, or the audio pipeline. That is a different layer of the problem, the one
[Camoufox and this project work at instead](vs-camoufox.md), and pairing a CDP-native
driver with separate fingerprint work is the normal pattern for people using either of
these two.

## The engine question these two do not raise, and the one they do

Both drive Chrome or Chromium specifically, which puts them in the same
Chromium-is-not-Chrome position as any Playwright-Chromium launch:
[the default binary lacks the proprietary codecs a real Chrome install has](chromium-is-not-chrome.md),
and using either tool does not change that on its own. Where these two differ from a
Playwright launch is that they are commonly pointed at an existing installed **Chrome**
binary rather than a downloaded Chromium, since neither ships a browser of its own the way
Playwright bundles one; if you point either at a real Chrome install, the codec gap does
not apply to you in the first place.

If your target needs Firefox rather than Chrome for other reasons -
[four structural ones are covered here](firefox-vs-chromium-antidetect.md) - neither of
these tools is relevant regardless of which one you'd otherwise pick, since both are
Chrome/Chromium-only by design.

## How to actually choose

- **Have existing Playwright code and don't want to rewrite it?** Neither of these. Stay
  in Playwright and pick a tool at the layer you actually need, from
  [the three-level map](playwright-stealth-levels.md).
- **Starting fresh, committed to Chrome, and comfortable with a new API?** nodriver is
  the actively developed one; undetected-chromedriver is the older, Selenium-based
  lineage it replaced.
- **Need the fingerprint layer solved too** (fonts, GPU, canvas, audio)**?** Neither
  claims to do this, by their own documentation. Pair either with something at that
  layer, or use a tool that already covers both.
- **Need Firefox?** Off the table for both; they are Chrome/Chromium tools specifically.

## Conclusion

undetected-chromedriver and nodriver solve the same problem this project solves at the
Playwright-driver layer for Camoufox and its comparisons, but they do it by leaving
Playwright's API entirely rather than patching around it. That is a real cost if you have
existing Playwright infrastructure, and a real benefit if you are starting fresh and want
the shortest path to a CDP-native Chrome. Neither claims the fingerprint layer, and their
own documentation says so.

## Short answers to the questions that lead here

**Are nodriver and undetected-chromedriver Playwright tools?** No. Both have their own
API and do not run through Playwright at all.

**Is nodriver a replacement for undetected-chromedriver?** Yes, from the same author,
dropping the chromedriver binary and the WebDriver protocol in favor of talking CDP
directly.

**Do these fix browser fingerprinting?** No, by their own documentation. They address the
automation-driver layer, not fonts, GPU, canvas, or audio.

**Do they hide my IP?** No. undetected-chromedriver states this explicitly; treat proxy
and IP handling as a separate problem for either tool.

**Can I use Firefox with either?** No, both are Chrome/Chromium-specific.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md),
for where a CDP-native tool sits relative to a driver patch or an engine patch;
[the ChromeDriver `cdc_` variable, explained](cdc-variable-explained.md), for the exact
tell undetected-chromedriver was built to remove; and
[invisible_playwright vs Camoufox](vs-camoufox.md), for the comparison at the fingerprint
layer neither of these tools works at.

## Sources

- nodriver's own GitHub documentation, read 2026-07-28, for its CDP-direct architecture,
  its own API surface, and its supported browsers.
- undetected-chromedriver's own GitHub documentation, read 2026-07-28, for its patch
  mechanism, its explicit statement on IP address handling, and its versioned change in
  approach to the driver variable.
- This project's own `cdc-variable-explained.md` for the equivalent tell on our own stack.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. nodriver and undetected-chromedriver solve a real
problem neither one overclaims, at a layer this project does not compete at directly.*
