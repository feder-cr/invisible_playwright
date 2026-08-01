---
title: "Three ways to make Playwright undetected"
description: "Stealth tools for Playwright get compared as a flat list. They actually operate at three different levels, page, driver and engine, and the level decides what each one can and cannot reach."
parent: "Comparisons"
nav_order: 1
---


# Three ways to make Playwright undetected

Every few months someone posts a list of stealth tools for Playwright, and the list
is always flat: eight names, a table of stars, a recommendation. That framing hides
the only thing that matters, which is that these tools do not do the same kind of
thing at all. They operate at three different levels, and the level determines what
they can and cannot fix.

Here is the map I wish I had when I started.

## Level 1: patch the page

The oldest approach. Before the site's own JavaScript runs, inject a script that
redefines the things that give automation away. `navigator.webdriver`, the plugin
array, the permissions API, the WebGL vendor strings.

**Tools:** `playwright-stealth` and its forks on the Python side, the
`puppeteer-extra-plugin-stealth` lineage on the Node side.

**What it fixes:** the obvious property checks, and it fixes them in about four
lines of setup. If you are being blocked by something naive, this is often enough
and you should try it first.

**Where it stops:** everything you redefine is a JavaScript object living in the
same runtime as the code inspecting it. A redefined property is an own property
where a native one would be inherited. A replacement getter is a function whose
source you can print. Patch `Function.prototype.toString` to hide that, and the
patched `toString` is now itself the anomaly. You are in a race inside the
opponent's runtime, and the opponent gets to look at everything you did.

It also does nothing about the machine. Fonts, GPU, timezone, audio stack, TLS
handshake: all still the real ones.

## Level 2: patch the driver

Instead of lying about the automation, stop announcing it. Strip the command-line
flags that make the browser set `navigator.webdriver` at all, remove the bindings
the automation framework injects into the page's global scope, stop using the
execution-context isolation pattern that leaves observable traces.

**Tools:** [Patchright](vs-patchright.md), the drop-in Playwright fork.
[`rebrowser-playwright` and the rebrowser patches](vs-rebrowser-patches.md) take a
similar line, converging on close to the same fix independently.

**What it fixes:** a whole class of tells at the source instead of covering them.
The difference is real and worth understanding with an example. At level 1 the
property becomes `false`, which is a value no clean browser reports. At level 2 it
becomes `undefined`, because nothing ever set it. There is nothing to detect
because there is no override.

**Where it stops:** at the browser binary. The engine still reports the GPU it is
actually running on, the fonts actually installed, the screen actually attached. If
you are on a server, that reads like a server. Level 2 makes your automation look
like a normal browser on that machine, which only helps if that machine looks
normal.

## Level 3: patch the engine and rebuild

Change the values in the browser's C++ source, compile, and ship the binary.

**Tools:** Camoufox, and `invisible_playwright`, which I maintain. Both are patched
Firefox builds.

**What it fixes:** the distinction between a spoofed value and a real one
disappears, because the engine reports through the same native code path a normal
build uses. `Function.prototype.toString` says `[native code]` because it is native
code. More importantly, it reaches surfaces that JavaScript cannot: what the font
subsystem enumerates, what the WebGL implementation reports, how the audio pipeline
rounds, what the network layer sends.

That reach is the actual argument for level 3. Not "it is harder to detect", but
"there are entire surfaces the other two levels cannot touch at all".

**Where it stops, and the honest costs:**

- You maintain a browser fork against upstream. Every Firefox release is work.
- The binary is hundreds of megabytes and has to be downloaded and cached.
- You are locked to one engine. Both current level-3 tools are Firefox, and some
  sites genuinely serve different code to Firefox, or need Chrome-specific
  features. If your target is one of those, the cleanest possible Firefox is the
  wrong tool.
- The build is a fingerprint too. A patched engine that reports values no real
  build ever reported is worse than an unpatched one.

That last point deserves more attention than it gets. A stealth build is only as
good as the plausibility of what it claims. If the GPU string it reports is a
software rasterizer, the browser has just announced it is running on a machine with
no graphics hardware, and no amount of C++ patching elsewhere undoes that. I know
because I found exactly that in my own persona pool: a small share of seeds were
handing out the Windows software renderer as if it were a graphics card. Real data
sampled from real users contains servers, and if you sample naively you inherit
them.

## The axis nobody puts in the comparison tables

Determinism.

Most of these tools generate a random fingerprint per launch. That is fine for
blending in, and useless for debugging. When a run fails, you cannot tell whether
the site changed, the proxy changed, or you drew a different machine this time.

Seeding the fingerprint fixes that. Same seed, same navigator, same screen, same
GPU, same fonts, same canvas output, run after run. It turns "it worked yesterday"
into something you can bisect. It is also the only way to run a fair A/B between
two tools, because otherwise the variance between draws is larger than the
difference you are trying to measure.

If you are evaluating tools in this space, ask whether the fingerprint is
reproducible before you ask what its detection rate is. A number without
reproducibility is an anecdote.

## How to actually choose

Not by star count. By this order of questions:

1. **Does your target need Chromium?** If yes, level 3 is out today and you are
   choosing between levels 1 and 2. Take level 2.
2. **Are you running on a machine that looks like a normal desktop?** If not, level
   2 will not save you, because the machine is the problem and level 2 does not
   touch the machine.
3. **Do you need to reproduce a session?** If yes, you need a seeded fingerprint,
   and that narrows the field fast.
4. **Can you afford a large binary and a fork's maintenance?** If not, be honest
   about it. Level 1 shipped today beats level 3 abandoned in three months.

And whichever you pick, measure it yourself. Run one of the open test suites,
[CreepJS](creepjs-explained.md) or [BotD](botd-explained.md) or [sannysoft](sannysoft-explained.md), and read the whole report instead of the headline. The
published comparisons in this space are mostly written by companies selling a
managed alternative, and the ones that are not are usually a single run on a single
machine from a single IP. Your setup is not their setup.

## Short answers to the questions that lead here

**Does `playwright-stealth` support Firefox?** No. The evasion modules target Chromium
APIs. This is the single most common surprise for people who need Firefox.

**What is the difference between patching the page, the driver and the engine?** Where
the lie lives. In the page it is a script the page can find. In the driver it is
outside the page but still above the browser. In the engine there is nothing to find,
because no lie was told.

**Is engine-level always better?** It is more thorough and much more expensive. You
maintain a browser build against upstream. Level one shipped today beats level three
abandoned in three months.

**Can I combine two stealth tools?** Do not. Two layers rewriting the same properties
argue with each other, and the disagreement is easier to detect than either one alone.

**Which level do I actually need?** Whichever one clears your target. Test before
choosing, because the honest answer is usually lower than the marketing suggests.

**See also:** [how CreepJS detects tampering](creepjs-explained.md), which explains why level one has a ceiling, and [WebGL renderer strings](webgl-renderer-strings.md) for a surface no level reaches by lying.

---

*I maintain `invisible_playwright`, MIT, on PyPI: a Firefox patched at the C++ level
and driven by stock Playwright, with a seeded deterministic fingerprint. I am
obviously not neutral, so the costs of level 3 above are written from the inside
rather than the outside.*
