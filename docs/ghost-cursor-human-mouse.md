---
title: "ghost-cursor human mouse paths with Playwright"
description: "ghost-cursor draws human-like Bezier mouse paths for Playwright. Where invisible_playwright fingerprint realness ends and pointer-behaviour realism begins."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 29
---


# ghost-cursor human mouse paths with Playwright

Two things get a session flagged, and they are not the same thing. One is what the
browser reports about itself: the GPU, the fonts, the screen, the driver flags. The
other is what the browser does once it is on the page: where the pointer goes, how
fast, and whether it ever moves at all before a click.

ghost-cursor addresses the second one. It generates human-like mouse paths using
Bezier curves with realistic acceleration and overshoot, so a click arrives at a
button along a plausible arc instead of appearing on it. This page is about how that
layer fits with a browser that is already real at the fingerprint level, and, just as
important, what it does not fix.

## Fingerprint realness and behaviour realness are two problems

invisible_playwright makes the browser look like a real Firefox driven by a real
person. The patch lives in the C++ engine, so the values a detector reads come out
consistent with each other: the GPU string matches the pixels it draws, the fonts
match the platform, the driver flags read like a browser nobody automated. That is
why it passes most fingerprint, TLS and driver checks. A page-level property patch
cannot claim the same, because it is editing answers after the engine has already
given a different one.

None of that is a statement about behaviour. Fingerprint realness answers "what is
this browser"; behaviour realness answers "is a person driving it". They are
measured by different code, they fail independently, and you need both. A perfectly
consistent fingerprint that teleports its pointer from corner to corner is a real
browser being driven by something that is obviously not a person, and some sites
watch only for that.

## What invisible_playwright already does to the pointer

Switching from stock Playwright is two lines, and the pointer behaviour changes with
it:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # the pointer arcs to the button on a Bezier curve
```

The `browser` object is a real Playwright `Browser`, so every method you already use
works unchanged. What is different is that a `page.click()` routed through this class
does not jump the cursor onto the target: a built-in cursor engine moves it there
along a curved path first. You can select which engine drives that motion, or turn it
off, with an environment variable:

```bash
# python (default) drives the built-in path engine; "off" hands motion back to you
export INVPW_CURSOR_ENGINE=off
```

So for the ordinary case, click paths are already handled and you do not have to add
anything. The reason this page exists is that click paths are one slice of pointer
behaviour, not all of it.

## Where the gap still is

The built-in arc covers the pointer motion for clicks the class drives. Several
common patterns fall outside that:

- **Moving without clicking.** If you call
  [`page.mouse.move(x, y)`](https://playwright.dev/python/docs/api/class-mouse#mouse-move)
  directly, that is a raw Playwright call and it teleports: Playwright's own docs
  say the default single-step call "emits a single `mousemove` event at the
  destination location," which is exactly the jump a detector can catch. Exploratory
  drift, moving toward an element to trigger a hover state, or repositioning between
  actions are all straight jumps unless something draws them.
- **Hover before click.** A person's pointer is usually near a control before they
  commit to it. A driver that only ever moves at the moment of the click has a
  different signature from one that approaches first.
- **The shape and timing you want.** If you need a specific approach, an overshoot
  and correction, or a particular pace, you want to own the path rather than accept a
  default.

For all of these you set `INVPW_CURSOR_ENGINE=off` so the two engines are not both
trying to move the mouse, and you drive the pointer yourself. ghost-cursor is a
convenient way to do that, because it produces the curved, accelerating motion for
arbitrary moves and not only for clicks.

## Layering ghost-cursor for full control of the path

ghost-cursor started as a Node library; the Python port `python-ghost-cursor` drives
a Playwright page directly. Because invisible_playwright hands you a genuine
Playwright `Page`, the port attaches to it with no adapter:

```bash
pip install invisible-playwright python-ghost-cursor
```

```python
import os
from invisible_playwright import InvisiblePlaywright
from python_ghost_cursor.playwright_sync import create_cursor

# hand pointer motion to ghost-cursor so the two engines do not fight over the mouse
os.environ["INVPW_CURSOR_ENGINE"] = "off"

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    cursor = create_cursor(page)
    cursor.move("#menu")     # curved approach, no click, sets up a hover
    cursor.click("#submit")  # Bezier path to the target, then the click
```

The identity stays reproducible: `seed=42` gives the same GPU, canvas hash, audio
context and fonts on every run, so if a session is flagged you can replay it and tell
a change in the site from a change in the machine. The pointer path from ghost-cursor
is not seed-locked to that identity, but the fingerprint underneath it is, which is
the part that has to stay stable for a bisect to mean anything.

If you would rather keep the built-in engine for clicks and add ghost-cursor only for
the moves between them, leave `INVPW_CURSOR_ENGINE` at its default and use the cursor
for `move` calls alone. Either arrangement is fine; what you must avoid is two engines
animating the same click, which produces a doubled or contradictory motion that is
worse than either alone.

## The honest boundary: what pointer realism does not fix

A human-shaped mouse path removes one tell. It does not remove the others, and it is
worth being explicit so a smooth cursor is not mistaken for a solved session.

- **IP reputation.** A real browser with a lifelike pointer, arriving from a
  datacenter range or a public proxy everyone else is using this minute, still loses.
  The pointer path is invisible to the part of the system that scores the address.
  You supply a clean exit; see [configuration](configuration.md) for how the proxy is
  set and how the timezone is derived from it.
- **Pacing and rate.** ghost-cursor shapes a single motion. It says nothing about how
  many actions you take, how close together, or whether a form is filled in eighty
  milliseconds. Per-account quotas and rate limits are counted server-side and no
  cursor library touches them. Human pacing between actions is yours to add.
- **Session rhythm overall.** Scrolling, reading pauses, the gap between landing and
  the first interaction, and for automated agents
  [the pause shaped like model latency](ai-browser-agents-stealth.md) are all
  behaviour that a mouse-path library does not cover.

The rule from the rest of this site holds here: stealth means looking real, not
suppressing a signal. A pointer that never moves is not a neutral absence, it is a
signal, in the same way that a
[trust score is built from many surfaces agreeing](browser-trust-score-explained.md)
rather than from any one of them being silent. ghost-cursor fills one of those
surfaces. It does not fill the rest, and no honest page would claim it makes a session
undetectable.

## Conclusion

Fingerprint realness and behaviour realness are separate problems with separate
fixes. invisible_playwright covers the first at the engine level and already arcs the
pointer for the clicks it drives; ghost-cursor lets you own the full pointer path when
you need motion beyond those clicks. Layer them by handing motion to one engine at a
time, keep the seed fixed so failures are reproducible, and remember that a lifelike
cursor is one honest improvement among several the reader still has to supply: a clean
exit, human pacing, and a session rhythm that is not all one speed.

## Short answers to the questions that lead here

**Does invisible_playwright already move the mouse on a curve?** Yes. A `page.click()`
routed through the class arcs the pointer to the target on a Bezier path by default.
ghost-cursor is for pointer motion beyond those clicks, or when you want to own the
path yourself.

**Do I still need ghost-cursor then?** Only for what the built-in click arc does not
cover: moving without clicking, hovering before a click, or a specific shape and
pace. For plain clicks you do not have to add anything.

**How do I stop the two cursor engines fighting?** Set `INVPW_CURSOR_ENGINE=off`
before launch when ghost-cursor is driving the pointer, so only one engine animates
the mouse. Never let both animate the same click.

**Does a human mouse path make me undetectable?** No. It removes the teleport-click
tell and nothing else. IP reputation, rate limits, per-account quotas and overall
timing are separate, and you still supply a clean proxy and human pacing.

**Does ghost-cursor need a special browser build?** No. invisible_playwright returns a
standard Playwright `Page`, so `python-ghost-cursor` attaches to it the same way it
would to any Playwright page.

**Will the pointer path be the same for a fixed seed?** The fingerprint is seed-locked
and reproduces exactly; the ghost-cursor path is not tied to the seed. The identity
underneath stays stable, which is what a bisect depends on.

## Sources

- This project's quickstart and configuration pages for the real launch API, the
  built-in cursor engine, and the `INVPW_CURSOR_ENGINE` variable.
- [Playwright's own mouse API documentation](https://playwright.dev/python/docs/api/class-mouse#mouse-move)
  for how `page.mouse.move()` behaves with no engine layered on top of it.
- The public ghost-cursor project and its Python port for the Bezier-path pointer
  motion, read from their own documentation.
- The release notes for this site's separation of fingerprint surfaces from
  behavioural ones, which is why pointer realism is documented as a layer you add
  rather than a claim the engine makes for you.

**See also:** [what a browser trust score actually measures](browser-trust-score-explained.md),
[stealth for AI browser agents](ai-browser-agents-stealth.md) for the timing tells a
cursor path does not cover, and [the automation layer](guides-automation-layer.md) for
why the driver itself is a surface.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It looks like a real
browser and already arcs the pointer for its clicks; the rest of behaviour, and a
clean exit, are still yours to supply.*
