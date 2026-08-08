---
title: "What bot.sannysoft.com actually checks, row by row"
description: "What bot.sannysoft.com actually tests, group by group: which rows still mean something in 2026, which are 2018 relics, and the canvas check nobody reads."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 1
---


# What bot.sannysoft.com actually checks, row by row

bot.sannysoft.com runs three groups of checks: a legacy pass/fail table of automation
flags, an unscored dump of `navigator` and `screen` properties, and a set of canvas
tests that quietly run twice, once in the page and once inside an iframe, then compare
the results. Passing the table proves only that you are not an unmodified headless
browser from around 2018; the canvas consistency check and the property dump are the
parts that still decide anything in 2026.

Everyone in browser automation ends up on this page, and most people read the colour
of the rows instead of what they mean. Here is what each group tests, which ones are
still meaningful in 2026, and the one nobody looks at that is the most interesting.

Read from the live page on 2026-07-27, by its element ids, not from memory.

## Group one: the legacy automation table

Eleven rows, the oldest and best-known set. In page order:

| Row | What it reads |
|---|---|
| **User Agent** | whether the string contains `HeadlessChrome` |
| **WebDriver** | [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver) |
| **WebDriver Advanced** | the same value read through a less obvious path, to catch a shallow override |
| **Chrome** | whether `window.chrome` exists and looks whole, on a Chromium build |
| **Permissions** | whether the Notifications permission state and `Notification.permission` agree |
| **Plugins Length** | `navigator.plugins.length` being zero, which old headless builds reported |
| **Plugins is of type PluginArray** | the prototype of `navigator.plugins`, catching an array pretending to be one |
| **Languages** | `navigator.languages` being empty |
| **WebGL Vendor** and **WebGL Renderer** | the [unmasked strings](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info) |
| **Broken Image Dimensions** | the natural size of an image that failed to load |

Almost all of these are historical. They describe headless Chrome as it behaved
around 2018, and every tool in this space fixes them on day one. Passing them is
table stakes: it proves you are not using an unmodified headless browser, and
nothing else.

Two of the eleven still carry weight, and they are the ones that are not about
automation at all:

**WebGL Vendor and Renderer** name your graphics hardware. A software rasterizer
here is a server tell that no amount of automation-flag fixing hides. There is a
[longer page on that](webgl-renderer-strings.md).

**[Permissions](https://developer.mozilla.org/en-US/docs/Web/API/Permissions_API)** is
the interesting shape: it does not ask a single question, it asks two and compares the
answers. That pattern, cross-checking two sources that must agree, is what everything
modern is built on.

## Group two: the property dump

The long list below the table is not pass/fail. It prints values:
`navigator.userAgent`, `appName`, `vendor`, `appCodeName`, `platform`, `language`,
`plugins`, `cookieEnabled`, `doNotTrack`, `sendBeacon`, `javaEnabled`,
`getUserMedia`, `mediaDevices`, `screen.width`, `screen.height`, `screen.colorDepth`,
battery details, and more.

Nothing here is scored, which is why people scroll past it. It is also where the
real information is, because these values have to agree with each other and with the
platform you claim:

- `navigator.platform` says `Win32`, and the font set is the Linux default.
- `screen.width` and `height` are a resolution no display ships in.
- `colorDepth` is 24 while the claimed GPU implies something else.
- `vendor` is empty on a build whose user agent says Chrome.

A detector does not need a red row to draw a conclusion from that list. It needs two
values that cannot both be true.

## Group three: canvas, and the part worth the page

The canvas section is the most revealing test on the page, and it is not a
fingerprint test at all: it checks whether your canvas gives the same answer in two
different execution contexts. The element ids give this away: `canvas1`, `canvas2`,
`canvas3`, `canvas4`, `canvas5`, and crucially **`canvas3-iframe`, `canvas4-iframe`,
`canvas5-iframe`**.

Three of the canvas tests are run twice: once in the page, once inside an iframe, and
the results compared.

That is not a fingerprint test. It is a **consistency** test, and it is aimed
squarely at the way most anti-fingerprinting works. If your canvas protection is a
script injected into the top-level document, the iframe gets a different execution
context, and either the patch is not there or the noise it adds is seeded
differently. Either way the two readings disagree, and a real browser's readings
never do.

The same idea appears in `media1` versus `media`, and in the battery rows: ask the
same question from two places and see whether the answers match.

This is why "does it pass sannysoft" is a weaker question than it looks. The table
rows can all be green while the iframe comparison quietly shows that something is
rewriting values in one context and not another.

## What a real run against this page actually shows

Opened directly against a live Playwright-driven Chromium session, all eleven
rows in group one came back green - `WebDriver` and `WebDriver Advanced` both
read "passed," and `navigator.webdriver` resolved through a genuinely native,
unmodified getter (`function get webdriver() { [native code] }`), not a patched
one. Nothing in that specific session had touched the property; it simply
reported `false` on its own.

Worth being precise about what that does and does not establish. It confirms the
point this page keeps making rather than undermining it: clearing group one is
the easy part, achievable without any stealth layer doing anything at all in this
case. It says nothing about which exact configuration produced that result, and a
different launch configuration on the same underlying browser could read
differently - the honest scope of this observation is "this is what one real,
directly-checked session showed," not "this is guaranteed behaviour for every
Playwright session everywhere." [Test your own setup directly](how-to-test-bot-detection.md)
rather than trusting either this page's description or a general claim about
what Playwright does by default.

## What passing actually proves

That you are not running an unmodified headless browser from several years ago.

It does not prove your fingerprint is coherent, that your GPU string is plausible,
that your font set matches your claimed platform, that your timezone agrees with your
IP, or that your canvas gives the same answer everywhere it is asked. Those are the
things that decide modern outcomes, and only the last one is on this page at all.

Use it as a smoke test. If a row is red, you have a real problem and should fix it.
If every row is green, you have learned that you cleared the 2018 bar, which is worth
exactly what it sounds like.

## Checking the part that matters

Open the page, then read the canvas section instead of the table. If the in-page and
in-iframe hashes differ, whatever is protecting your canvas is doing it in one
context only, and that inconsistency is more detectable than the original value would
have been.

An engine that computes the value below the JavaScript layer does not have this
problem, because there is no per-context patch to be missing: every context asks the
same code and gets the same answer. The case for doing it at that layer is exactly
this, and it is the same case as
[the three levels page](playwright-stealth-levels.md), seen from the detector's side.

## Short answers to the questions that lead here

**Is passing bot.sannysoft.com enough?** No. It is a useful smoke test that catches
obvious mistakes. Serious detection reads far more than these rows.

**What is the canvas-in-iframe row for?** It runs the same canvas test in the page and
in an iframe and compares them. A patch applied to one context and not the other shows
up here, and most people never look at it.

**Why are some rows green on a real browser and red on mine?** Because the page reads
values a real browser answers plainly, and a spoofed one sometimes answers strangely.
Compare against a stock browser before assuming a red row is a bug.

**Should I aim for all green?** Aim for the same colours a stock browser produces.
All green is not the target; matching is.

**What does it not check?** Behaviour, the network layer, the TLS handshake, and
whether your hardware is plausible. All of those decide more than this table does.

**See also:** [how detectors decide you are a bot](how-do-websites-detect-bots.md),
[what the WebGL vendor and renderer strings give away](webgl-renderer-strings.md),
[why identical WebGL parameters are their own tell](webgl-parameters-are-identical.md),
and [how to test your own setup against bot detection](how-to-test-bot-detection.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
sannysoft is a release gate here. The element ids above were read off the page, not
remembered.*
