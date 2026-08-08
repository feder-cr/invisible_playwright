---
title: "invisible_playwright vs Ulixee Hero"
description: "invisible_playwright vs Ulixee Hero: replayed emulation on stock Chromium vs a fingerprint decided natively in patched Firefox, with no injected seam to detect."
parent: "Comparisons"
nav_order: 15
---


# invisible_playwright vs Ulixee Hero

invisible_playwright and Ulixee Hero both automate a browser that reads as an ordinary
one, but they differ in where the fingerprint is produced. Ulixee Hero replays real-browser
data captured in its datasets onto a stock headless Chromium at the automation layer;
invisible_playwright decides each value natively inside a patched Firefox, so there is no
injected override for a consistency check to catch. They get there from opposite directions,
and the difference is architectural rather than cosmetic, so it is worth naming precisely
instead of ranking the two on a feature grid.

Ulixee Hero (about 1,551 stars at the time of writing) drives Chromium and sources its
stealth from emulation data. invisible_playwright drives a Firefox that was patched at the
C++ level, and decides each value inside the engine. This page is about what that
distinction actually changes when a page inspects you, and where it does not help at all.

## What Ulixee Hero is

Hero is a scriptable headless browser with its own high-level API, built around Chromium.
Its own README is direct about both halves of the design: "The powerful Chrome engine sits
under the hood," and its realism comes from captured "Browser Profile Data" collected
through the project's Unblocked and DoubleAgent datasets. Those datasets record how real
browsers actually answer the questions a detector asks, and Hero replays the recorded
answers.

That is a coherent and honest design. It also fixes the shape of the comparison: a
captured value has to be applied to a running headless Chromium somewhere above the
engine, because the engine itself is stock. The value is replayed, not produced.

## Emulation replayed onto Chromium vs a value decided in the engine

This is the whole comparison, so here it is plainly.

Emulation-from-captured-data means: a real browser once reported some GPU string, some
canvas output, some set of navigator properties, and that recording is now applied on top
of a headless Chromium at automation time. The engine underneath would answer differently
on its own, so something has to sit between the engine and the page and substitute the
recorded answer.

Deciding the value in C++ means: the engine is modified so that the answer it produces
natively is the answer you want. There is no substitution step, because nothing is being
corrected after the fact. The property is not overridden on the way out; it is what the
engine computes.

Both can yield the same string in the report. The difference is whether a seam exists
between "what the engine would say" and "what the page is told," because a seam is a thing
a detector can look for.

## The injected seam a consistency check looks for

Modern detection stopped reading values a while ago and started asking whether a value was
tampered with. This is exactly what [CreepJS is built to do](creepjs-explained.md): it
takes a clean copy of the built-ins from a fresh iframe, walks descriptors and prototypes,
inspects stack traces, and checks whether a function that should be native still looks
native. It is not asking "is this GPU unusual," it is asking "was this getter replaced."

Anything that replays a captured value onto a stock engine has to install that value
through some override, and an override is precisely the artifact this class of check is
hunting: a property descriptor that is not shaped like the engine's own, a `toString` that
no longer reads as native code, a stack frame that appears where a genuine call would not
have one. A perfectly accurate replayed value can still be caught by the seam used to
replay it, not by the value.

When the value is decided in the engine instead, there is no override to find. The getter
is the engine's own getter, native by construction, because nothing patched it at runtime.
That does not make a browser magically undetectable, but it removes an entire category of
tell: the one that fires on the mechanism of spoofing rather than on the content of it.
This is also why the engine choice matters underneath all of it -
[a stock Chromium is not the Chrome a real user runs](chromium-is-not-chrome.md), and
[Firefox versus Chromium is a structural decision, not a preference](firefox-vs-chromium-antidetect.md).

## The same run, reproducible either way

Here is the practical side, using the real API. Switching an existing Playwright script is
two lines, and the returned object is a genuine Playwright `Browser` with every standard
method:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

The `seed` is the reproducibility knob. Every fingerprint field - GPU, canvas hash, audio
context, fonts, screen - is derived from it, so the same seed gives the same machine on
every run. That is what turns a flaky failure into a debuggable one: you can replay the
exact identity that failed instead of hoping the next random draw reproduces it.

If you do not pass a seed you still get one; log it to replay the session later:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)
    page = browser.new_page()
    page.goto("https://example.com")
```

Async is [the same shape](https://playwright.dev/python/docs/library), so async test suites
port without a rewrite:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
```

Because the browser object is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), nothing above this line
changes: your existing page objects, selectors and fixtures keep working. There is no
separate automation dialect to learn, which is a difference from a tool that ships its own
API.

## One honest caveat

None of the above touches your exit address, and that is the caveat to keep in front of
you. invisible_playwright decides the browser; it does not decide the network. A
natively-consistent Firefox coming from a datacenter range that a scoring system already
distrusts will still lose, and it will lose for a reason no fingerprint field can fix.

Around 90% of proxies are public, which means their addresses are known and blocked before
you send a single request through them, so pairing a good browser with a bad exit throws
away the browser. [Configuration](configuration.md) covers how the timezone is derived
from the egress IP so the two stories agree, and the broader point is worth stating flat:
the engine layer and the network layer are separate problems, and a browser that wins on
one can still be sunk by the other.

## How to actually choose

- **Have existing Playwright code and want to keep it?** invisible_playwright is a two-line
  launch swap; Hero has its own API, so adopting it means writing against that API instead.
- **Committed to Chromium for a specific reason?** Hero is a Chromium tool by design; this
  project is Firefox. If your target genuinely requires the Chromium engine, that decides
  it regardless of the stealth layer.
- **Worried about tamper-detection rather than value-accuracy?** This is the axis this page
  is about: deciding values in the engine removes the injected-override seam that a
  consistency check hunts for. Weigh that against Hero's captured-data accuracy for your
  own targets, measured rather than assumed.
- **Need the network handled too?** Neither tool is your IP layer. Treat the exit as a
  separate decision either way.

## Conclusion

Ulixee Hero and invisible_playwright are both serious answers to the same question, and the
honest difference between them is not which reports "better" values. Hero replays values
captured from real browsers onto a stock Chromium at the automation layer; this project
decides values inside a patched Firefox, so the value is native by construction and there
is no injected seam between the engine and the page. That distinction is invisible on a
report that only reads values, and it is the whole game on a check that asks whether a
value was overridden. Choose on that axis, on the engine you actually need, and remember
that neither one is your exit address.

## Short answers to the questions that lead here

**Is Ulixee Hero a Playwright tool?** No. It has its own high-level API and drives
Chromium under the hood, per its own README. invisible_playwright is a two-line swap on top
of stock Playwright.

**What is the real difference between them?** Where the value is produced. Hero replays
captured browser-profile data onto a stock engine; this project decides the value inside a
patched Firefox, so there is no runtime override to detect.

**Does replaying a captured value get detected?** Not for being wrong - the value can be
accurate. It can be caught by the override used to apply it, which is exactly what a
consistency check like CreepJS inspects for.

**Does invisible_playwright use Chromium?** No, it is Firefox patched at the C++ level. If
your target specifically needs the Chromium engine, that is a reason to look elsewhere.

**Will either of these fix a bad IP?** No. Both are browser-layer tools. A datacenter or
already-blocked exit sinks a perfect browser, and that is a separate problem.

**Can I reproduce an exact run for debugging?** With invisible_playwright, yes - pass a
`seed` and every fingerprint field comes back identical, so a failed run replays exactly.

## Sources

- Ulixee Hero's own GitHub README, for "The powerful Chrome engine sits under the hood,"
  its captured "Browser Profile Data," and the Unblocked and DoubleAgent datasets it draws
  that data from. Star count read from the repository at the time of writing.
- This project's own [CreepJS notes](creepjs-explained.md) for what a tamper check inspects
  and why an override is the artifact it hunts, rather than the value.
- This project's [Quickstart](quickstart.md) and [Configuration](configuration.md) for the
  real API used in the examples above.
- Playwright's own [`Browser` class reference](https://playwright.dev/python/docs/api/class-browser),
  for the stock object the examples above hand back unchanged.
- Playwright's [sync and async Python API](https://playwright.dev/python/docs/library),
  for the parity behind the async example above.

**See also:** [three ways to make Playwright undetected](playwright-stealth-levels.md) for
where an engine-level patch sits relative to a data-replay approach, and
[why a stock Chromium is not the Chrome a real user runs](chromium-is-not-chrome.md) for
the engine gap underneath any Chromium-based tool.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The comparison here is the
one that matters: a value decided in the engine has no injected seam, and a value replayed
onto a stock engine does.*
