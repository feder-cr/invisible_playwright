---
title: "DOM-reading vs screenshot agents: which stealth helps"
description: "DOM-reading and screenshot browser agents get detected differently: which stealth signal helps depends on whether the agent reads the DOM or clicks by pixel."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 15
---


# DOM-reading vs screenshot agents: which stealth helps

Which stealth signal helps depends on how the agent perceives the page, not on the model
behind it. A DOM-reading agent acts on selectors, so it is exposed at the DOM and driver
layer, and a patched engine's automation-tell fixes are what carry it. A screenshot agent
clicks by coordinate instead, so it is exposed at the graphics surface and in its rhythm,
and canvas, WebGL and GPU realness are what carry it. Both agents drive the same browser
to the same page and get caught by completely different things, even though they inherit
the same engine and the same patch underneath.

This page is about matching the fix to the way the agent sees. It ends with a short,
runnable example and one honest caveat: the engine changes what the page can measure about
the browser, and it does not change your address or your pacing. Those you supply.

## Two ways an agent sees a page

An agent has to turn a web page into something a model can act on, and there are two
common ways to do it.

A **DOM-reading agent** asks the browser for structure. It pulls the serialized DOM, or an
accessibility snapshot, or the visible text, hands that to the model, and gets back an
action expressed against an element: click this selector, type into that field. Text and
accessibility-tree agents live here.

A **screenshot agent** asks the browser for a picture. It captures the rendered viewport,
sends the image to a vision model, and gets back an action expressed in pixels: click at
these coordinates, then these. Computer-use style agents live here.

The distinction matters because detection is layered, and the two ways of seeing walk
through different layers on their way to acting.

## What a DOM-reading agent gives away

When the agent acts on selectors, the surface that matters most is the one it is reading
from: the DOM and the JavaScript environment around it.

This is where the classic automation flags live. A `navigator.webdriver` that reads
`true`, leftover automation globals, an object that a driver injected and forgot to clean
up. A DOM-reading agent does not care about these directly, but the page it is reading can,
and a page that finds one serves the agent a different DOM, or a challenge, or nothing.
[These flags are mostly solved and mostly misunderstood](navigator-webdriver-explained.md):
a clean browser reports `undefined`, and setting the flag to `false` is a value with its
own signature rather than an absence.

So for this mode, the DOM-level and driver-level tells are the ones that decide the run.
The good news is that this is exactly the layer a patched engine covers, because those
signals are properties the browser reports and the browser is the thing that was patched.
The agent reads the same clean structure a real browser would hand back, without a stealth
plugin sitting between the page and the model.

## What a screenshot agent gives away

A vision agent never reads `navigator.webdriver`, so at first glance the DOM flags look
irrelevant to it. They mostly are. What it exposes instead is everything that has to be
true for the picture to look like a real machine drew it, plus the rhythm of how it acts.

The picture is drawn by the graphics stack. A server with no GPU announces itself through
the rendered image and through the values that describe the renderer, and
[a renderer string that says one thing while the pixels say another](renderer-string-vs-render.md)
is a mismatch no property patch hides. Canvas and WebGL realness, which a DOM agent can
skate past if it never touches those APIs, are load-bearing here because the whole
interaction is mediated by what the machine can actually draw.

The rhythm is the second half. A vision agent clicks by coordinate, and if the pointer
jumps from point to point without passing through the space between, or the pauses between
actions are all shaped like model latency, that pattern is visible to anything watching
behaviour rather than fingerprints. This is
[the pause shaped like inference time](ai-browser-agents-stealth.md), and it is the same
family of signal whether the agent sees text or pixels. The engine gives you Bezier-curve
pointer motion for free, but the spacing of your actions is your loop, not the browser's.

## What the engine changes, and what it does not

The single fact worth carrying away: invisible_playwright changes the engine fingerprint
that both modes inherit, and which part of that helps depends on how the agent perceives
the page.

For a DOM-reading agent, the win is that the DOM and the JavaScript environment read as a
genuine Firefox, so the automation and driver tells that would have flagged the read are
not there. For a screenshot agent, the win is that the canvas, WebGL and the rest of the
graphics surface are consistent and look like a real machine, so the picture and the values
that describe it agree. Same engine, same patch, different part of it doing the work.

Here is the honest boundary. The engine is designed to look like a real browser driven by
a real person, which is why it passes most fingerprint, TLS and driver-layer checks: those
read as a real Firefox because they are answered by a real, patched Firefox. Neither mode
changes two things that sit outside the browser entirely:

- **The network exit.** Your IP reputation, the ASN, the country. A perfect browser on a
  known datacenter address still loses, and that is true whether the agent reads text or
  pixels. Bring a clean proxy.
- **The loop timing.** How fast the agent acts, how uniform its pauses are, whether it ever
  scrolls or waits like a person would. The engine draws a human pointer arc, but the
  cadence of your actions is yours to pace.

Neither is a browser property, so no engine patch reaches them. They are the caveat that
belongs next to every honest description of what this does:
[what fits and what does not](ai-browser-agents-stealth.md).

## A minimal example

The launch is the same two lines for both agents. The seed makes the identity
reproducible, so a failing run can be replayed rather than guessed at.

```bash
pip install invisible-playwright
```

A DOM-reading agent reads structure and acts on a selector:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # what a text / accessibility-tree agent would hand to the model
    tree = page.accessibility.snapshot()
    text = page.inner_text("body")

    # the model returns an action against a selector
    page.click("a")   # pointer arcs to the link on a Bezier curve
```

A screenshot agent reads pixels and acts on coordinates:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # what a vision agent would hand to the model
    shot = page.screenshot()

    # the model returns pixel coordinates; the arc between clicks is drawn for you
    page.mouse.click(220, 140)
```

The `browser` is a real Playwright `Browser`, so `accessibility.snapshot()`,
`inner_text()`, [`screenshot()`](https://playwright.dev/python/docs/api/class-page#page-screenshot)
and [`mouse.click()`](https://playwright.dev/python/docs/api/class-mouse#mouse-click) are
all the standard upstream methods.
The only change from plain Playwright is the two-line launch. What differs between the two
snippets is not the stealth, it is which surface the agent is leaning on, and therefore
which part of the shared engine work is carrying it.

## Conclusion

Detection is layered, and an agent only walks through the layers its way of seeing touches.
A DOM-reading agent is exposed at the DOM and driver layer, which is where the automation
flags live and where a patched engine does its most visible work. A screenshot agent is
exposed at the graphics surface and in its rhythm, where canvas and GPU realness and the
cadence of its clicks decide the run. The engine changes the fingerprint both inherit, so
the fix is the same install either way, but knowing which half is load-bearing tells you
where to look when a run still fails. And when it fails for the two reasons the engine does
not touch, the address or the pacing, no amount of fingerprint work will move it.

## Short answers to the questions that lead here

**Does the stealth engine help a vision agent at all?** Yes, through the graphics surface.
The canvas, WebGL and renderer read as a real machine, which is what a screenshot agent
leans on, even though it never reads a single DOM flag.

**Do DOM automation flags matter to a screenshot agent?** Mostly not, because it never
reads them. They matter to a text or accessibility-tree agent, which acts on the DOM the
flags live in.

**Which mode is harder to keep undetected?** Neither is strictly harder. They are exposed
in different places, so a screenshot agent needs the graphics surface and its rhythm to be
right, while a DOM agent needs the driver layer clean.

**Will this make my agent undetectable?** No. It makes the browser look like a real one,
which passes most fingerprint and driver checks. It does not touch your IP reputation or
your loop timing, and both of those get sessions blocked on their own.

**Does the engine fix my agent's timing?** It draws a human pointer arc between clicks. It
does not pace your actions. Uniform pauses shaped like model latency are still yours to
smooth out.

**Do I change my code depending on the agent type?** No. The launch is identical. The DOM
versus pixel difference is in how your agent already reads the page, not in how you start
the browser.

## Sources

- This project's quickstart and configuration pages for the launch API, the seed, and the
  proxy and timezone handling used above.
- The project's own notes on agent behaviour, where the model-latency pause and the
  boundary between fingerprint and pacing are documented from measurement rather than
  guesswork.

**See also:** [what fits and what does not for AI browser agents](ai-browser-agents-stealth.md),
[why navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
and [can websites detect Playwright](can-websites-detect-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine changes what the
page can measure about the browser; the address and the pacing are still yours to bring.*
