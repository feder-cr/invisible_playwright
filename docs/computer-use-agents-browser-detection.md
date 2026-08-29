---
title: "Computer-use agents and browser fingerprint detection"
description: "Computer-use agents click by pixel, not DOM, so driver flags are moot for them. Engine fingerprint and action rhythm remain the detectable signals."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 6
---


# Computer-use agents and browser fingerprint detection

A computer-use agent (CUA) does not read the DOM to decide where to act. It takes a
screenshot, sends the image to a vision model, gets back a pixel coordinate, clicks
there, and takes another screenshot. That loop is the whole architecture of OpenAI's
Computer-Using Agent and of Anthropic's computer-use tool, and it changes which
detection signals matter and which stop mattering.

This page is about that shift: why the automation flags people usually worry about are
moot for a coordinate agent, what is still checkable underneath it, and where
invisible_playwright helps and where you still have to do the work yourself.

## Why the driver flags stop mattering

A CUA never triggers the DOM-automation flags sites check for, because its click is a
coordinate delivered through the input pipeline the same way a real click is, not a
script reaching into the page. Most bot-detection advice targets that automation layer
anyway: [`navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
leftover automation globals, DOM mutations that only a driver produces, an untrusted
event dispatched from code rather than from hardware. Those signals exist because a
selector-driven script reaches into the page and touches elements directly.

A CUA does not do that. It never calls `querySelector`, never sets an element's
value from script, never dispatches a synthetic event at a node it located by selector.
So the whole family of DOM-automation tells is [mostly not the thing that catches
it](navigator-webdriver-explained.md) - not because the agent hides them, but because
it never generates them in the first place.

That is the part people get wrong. They read that agents are hard to detect and assume
the fingerprint is solved. It is not solved. It is bypassed by the input method, and
two other surfaces are completely untouched by that fact.

## What stays checkable: the engine fingerprint

The agent clicks by coordinate, but the click still lands in a browser, and that
browser answers JavaScript. The site's own scripts still ask the engine what it is:
the WebGL renderer string, the canvas hash, the audio context, the installed fonts, the
screen geometry, the codec list. None of that goes through the DOM the agent avoids. It
goes through the ordinary JS surface every page can read, and a stock automation build
running on a server answers those questions like a server.

This is the surface invisible_playwright is built for. It is a Firefox patched at the
C++ level so the engine renders like a real Windows Firefox: the GPU strings, the
canvas and audio hashes, the font set and the screen all report values a real desktop
would report, derived from one seed so they stay consistent with each other. A
detector cross-checking those fields against one another finds agreement instead of the
contradictions a [half-patched or double-patched stack](playwright-stealth-levels.md)
produces.

The two-line launch is the same whether a human or a vision model drives it:

```python
from invisible_playwright import InvisiblePlaywright

# seed=42 makes the whole fingerprint reproducible run to run
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # the screenshot -> click(x, y) -> screenshot loop a CUA runs
    png = page.screenshot()          # frame you hand to the vision model
    x, y = 480, 320                  # coordinate the model returns
    page.mouse.click(x, y)           # lands through the real input pipeline
    png = page.screenshot()          # next frame
```

`browser` here is a real Playwright
[`Browser`](https://playwright.dev/python/docs/api/class-browser), so
[`page.screenshot()`](https://playwright.dev/python/docs/api/class-page#page-screenshot)
and [`page.mouse.click()`](https://playwright.dev/python/docs/api/class-mouse#mouse-click)
are the ordinary documented methods - the same ones a coordinate-clicking agent already
calls. There is no special CUA API to adopt. You feed the screenshot to whatever model
you use and feed its coordinate back to `page.mouse.click`.

## What stays checkable: the action rhythm

The second surface the input method does not fix is timing, and it is the one specific
to agents.

A CUA's loop has a shape. Screenshot, then a pause while a model processes the image and
decides, then a click, then another screenshot. That thinking pause is roughly the same
length every step because it is model latency, not human hesitation, and it repeats with
a regularity no person produces. A human pause varies with what is on screen, drifts,
gets distracted, double-checks. A model's pause is a distribution centered on its
inference time.

invisible_playwright gives the pointer a Bezier-curve path between coordinates rather
than a teleport, which removes the crudest motion tell. It does not reshape the
step-to-step rhythm of your agent loop, because that rhythm is produced by your model
and your code, not by the browser. If your loop clicks every 1.9 seconds because that is
how long the model takes, the engine cannot know to vary it. You add the variance:
jitter the delay between steps, and do not click the instant a screenshot returns. This
is [the pause shaped like model latency](ai-agent-timing-signal.md), and it is
behaviour, which no fingerprint layer reaches.

## What invisible_playwright does not do

Being honest about the boundary is the point, because a coordinate agent makes it easy
to believe more is handled than actually is.

- **It does not change your IP.** The engine can look like a real Windows desktop and
  still be sitting on a datacenter address that a site distrusts on sight. A perfect
  fingerprint on a known-bad exit still loses. Bring a clean proxy; the engine cannot
  supply one.
- **It does not fix per-account quotas or rate limits.** Those are counted server-side
  against your account and address, and no browser property touches them.
- **It does not pace your agent.** The step rhythm above is yours to vary. The engine
  handles the pointer path, not the schedule your loop runs on.
- **It does not see the DOM for you.** A CUA works by pixels precisely because it does
  not read structure. invisible_playwright does not add DOM understanding; it makes the
  browser those pixels are rendered in look real.

What it does do is remove the engine fingerprint from the list of things that give you
away, so that TLS, the driver layer and the render surface read as a genuine Firefox.
That is why it passes most fingerprint checks. It is not why a session with a bad IP and
a metronome rhythm passes, because it does not claim to fix those.

## A measurement worth trusting more than a verdict

The useful test for a CUA setup is not "did the suite say human". It is a field-by-field
comparison against a stock browser on the same machine, because the agent's own loop can
introduce timing the browser knows nothing about.

Open a fingerprinting page under invisible_playwright and under a stock Firefox on the
same host, and diff the reports. On the engine surface the two should agree: same GPU
family, a canvas hash that is stable across two reads in the same session (a hash that
changes per call is [a randomiser announcing itself](canvas-fingerprint-noise.md)), a
font list that belongs to the platform the user agent claims. Where they still differ,
after the address, is your candidate. A [software WebGL renderer or a
datacenter-shaped machine](playwright-docker-detection.md) survives every property patch
because it is not the engine's to change, and it is the most common reason a clean-looking
agent still gets a different page. Run it ten times, not once, because this domain is not
deterministic and a single green run is not a pass.

## Conclusion

Vision and coordinate agents move the detection problem, they do not remove it. The
DOM-automation flags that dominate ordinary Playwright advice are moot for them, because
a click delivered by pixel never generates those flags. What remains is the engine
fingerprint the site reads through its own JavaScript, and the machine-regular rhythm of
the screenshot-click loop. invisible_playwright answers the first: it makes the browser
render like a real Windows Firefox, seed-reproducible and self-consistent. The second is
yours - vary the timing - and so is the IP - bring a clean one. Handle all three and the
agent looks like a person on a real machine. Handle only the browser and you have a real
browser clicking like a robot.

## Short answers to the questions that lead here

**Do computer-use agents get detected as bots?** Not usually through `navigator.webdriver`
or DOM automation flags, because a coordinate click never produces them. They get caught
on the engine fingerprint, on the IP, or on the regular timing of the screenshot-click
loop.

**Does invisible_playwright work with OpenAI or Anthropic computer-use?** Yes. It returns
a real Playwright `Browser`, so `page.screenshot()` and `page.mouse.click(x, y)` - the
two calls a coordinate agent needs - work exactly as documented, on a Firefox that
renders like a real Windows desktop.

**If the agent clicks by pixel, is the fingerprint irrelevant?** No. The click method
avoids DOM tells, but the site still reads the engine through JavaScript. GPU, canvas,
audio, fonts and screen are all still checkable, and a stock server build answers them
like a server.

**Will it stop my agent being rate-limited?** No. Quotas and rate limits are counted
server-side against your account and address. No browser property changes them.

**How do I make the timing look human?** Jitter the delay between loop steps and do not
click the instant a screenshot returns. The engine varies the pointer path; the
step-to-step rhythm is produced by your model and your code, so you vary that.

**Does it fix my IP?** No. It makes the browser look real; it does not change the exit.
A real-looking browser on a datacenter address still loses, so bring a clean proxy.

## Sources

- OpenAI's own announcement, [Computer-Using Agent](https://openai.com/index/computer-using-agent/),
  retrieved 2026-08-28, and Anthropic's own documentation, [Computer use tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool),
  retrieved 2026-08-28, both of which drive a browser through a screenshot to
  coordinate-click to screenshot loop rather than by DOM selector.
- This project's own fingerprint gates and the stock-browser comparison method used to
  separate an engine tell from a machine tell from a timing tell.

**See also:** [why AI agents have their own timing signal](ai-agent-timing-signal.md) for the
agent-specific timing tell in depth, [browser-use and what you can change](browser-use-detection.md)
for a selector-driven agent by contrast, and [why you can be blocked with a clean
fingerprint](why-blocked-with-a-clean-fingerprint.md) for the surfaces past the browser.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine makes the
browser real; the pacing and the proxy are still yours to bring.*
