---
title: "Playwright mobile emulation on Firefox and isMobile"
description: "Why Playwright iPhone and mobile presets misbehave on Firefox: isMobile is unsupported upstream and the seeded engine owns the screen size, not the client."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 17
---


# Playwright mobile emulation on Firefox and isMobile

Playwright mobile emulation does not work on Firefox the way it does on Chromium. The
`isMobile` option is unsupported by Playwright on Firefox, so an iPhone-class device
preset raises an error instead of applying, and the screen size is owned by the seeded
engine, so forcing it on a single context is accepted at the protocol level and then
ignored. Both are expected behaviour, not bugs in your code.

If you copy a mobile-emulation snippet that works on Chromium and point it at Firefox,
it does not behave the same way. An iPhone preset that produces a convincing mobile
context on one engine either raises an error or quietly gives you a desktop context on
the other. This is not a bug in your code and it is mostly not a bug in the browser: it
is two separate facts about how mobile emulation works on Firefox, and they are worth
knowing before you build a mobile scraper on top of the assumption that they are the
same everywhere.

This page is those two facts, a runnable demonstration of each, and what to reach for
instead.

## What "mobile emulation" means in Playwright

A device preset in Playwright is not a mode. It is a plain dictionary of context
options, and `browser.new_context(**device)` just spreads that dictionary. An iPhone
class preset expands to roughly this set of fields:

```python
# What a mobile device preset expands into (an iPhone-class descriptor):
mobile = {
    "viewport": {"width": 390, "height": 844},
    "screen": {"width": 390, "height": 844},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                  "Mobile/15E148 Safari/604.1",
    "default_browser_type": "webkit",
}
```

Two of those fields are the interesting ones for Firefox. `is_mobile` is what makes the
[meta viewport tag](https://developer.mozilla.org/en-US/docs/Web/HTML/Viewport_meta_tag)
take effect and turns on touch semantics. `screen` is the physical
display the page believes it is running on, which is distinct from the `viewport`, the
part of that display the content occupies. On Chromium both take effect. On Firefox one
of them is rejected outright by Playwright, and the other is overridden by the browser.

| Preset field | What it controls | Chromium | Firefox |
|---|---|---|---|
| `is_mobile` | Meta viewport tag and touch semantics | Applied | Rejected by Playwright, the call raises |
| `screen` | The display size the page reports | Applied | Accepted at the protocol level, then overridden by the seeded engine |

## Why the iPhone preset does not translate to Firefox

An iPhone-class preset that still carries `is_mobile` does not silently degrade when
spread into a Firefox context: Playwright rejects the option outright, and the call
raises before a context is even created.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(**mobile)
    # playwright._impl._errors.Error:
    #   Browser.new_context: options.isMobile is not supported in Firefox
```

The `default_browser_type` field is metadata that says these numbers were measured on a
WebKit build, so already the preset is describing a browser you are not running. But the
hard stop is `is_mobile`. Playwright itself refuses `isMobile` on Firefox, so a preset
that carries it cannot be applied to a Firefox context at all. Strip that one field and
the call succeeds, which is the first practical thing to know: reuse the numeric fields
of a preset if you like, but never spread it whole.

## isMobile is unsupported on Firefox, and that is upstream

`isMobile` not working on Firefox is a property of Playwright's own Firefox support,
[documented upstream in Playwright's own API reference](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-is-mobile),
and it holds for stock Playwright against a stock Firefox exactly as it holds here. This is the part people misattribute. It is not something a stealth layer
adds or could remove; the automation protocol Playwright speaks to Firefox has no mobile
emulation on this axis to begin with.

So a "mobile Firefox" context is not really mobile. You can set a narrow viewport and a
mobile user agent, but the meta viewport tag is not honoured and the touch-event surface
is not what a phone reports. Setting the user agent alone makes this worse rather than
better: a string that announces a mobile Safari build, delivered by a Gecko engine, is a
[user-agent contradiction a detector reads directly](playwright-user-agent.md), because
the engine gives itself away in a dozen other places the header cannot reach. If you
genuinely need a mobile browser, the honest answer is a WebKit or Chromium context, not a
Firefox one dressed up as a phone.

## The engine owns the screen size, not the client

The screen size on a Firefox context belongs to the seeded engine, not to whatever a
client script asks for. Recent Playwright versions send a `screenSize` alongside the
viewport on the commands that build and resize a context, part of the same 1.61 change
that also [added fields a strict wire protocol rejected](playwright-protocol-drift.md),
and Firefox accepts that field at the protocol level without acting on it. Drop
`is_mobile` and try to force just the screen dimensions to see it happen:

```python
from invisible_playwright import InvisiblePlaywright

mobile.pop("is_mobile")               # so new_context stops raising
mobile.pop("default_browser_type")    # not a context option

with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(**mobile)   # screen 390 x 844 requested
    page = context.new_page()
    page.goto("https://example.com")
    print(page.evaluate(
        "() => [screen.width, screen.height, devicePixelRatio]"
    ))
    # -> [1512, 982, 2]   the seed's desktop screen, not 390 x 844 @ 3
```

You asked for a 390 by 844 display at a device pixel ratio of 3. The page reports the
desktop screen that `seed=42` generates, at that seed's pixel ratio. The `viewport` you
requested is applied, because the viewport is genuinely the client's to set; the `screen`
is not, because the screen is a fingerprint field.

That is the deliberate part, and the reason is consistency. Every screen-related value a
session reports, `screen.width`, `screen.height`, `availWidth`, `availHeight`,
`devicePixelRatio`, the media queries that read them, is derived from the seed so that
they all agree with each other and with the rest of the identity. Letting a
per-context call overwrite one of them would produce exactly the kind of internally
inconsistent screen that [is itself a headless tell](screen-size-headless-tells.md): a
width that no longer matches the available width, a pixel ratio that does not match the
resolution, a screen smaller than the viewport drawn on it. So the extra field is
accepted at the protocol level for compatibility and then ignored, and the seed remains
the single owner of the screen. `isMobile` on that same command is always desktop for the
same reason.

If you want a different screen, the supported path is to change the identity, not to
override one field of it. A [seed pins the whole coherent set](pinning.md) at once, so a
different display comes with an available area, a pixel ratio and media-query answers that
still agree.

## What to do instead

Three concrete takeaways:

- Do not spread a device preset into a Firefox context. Pull the fields you actually want
  (a narrow `viewport`) and drop `is_mobile`, `default_browser_type`, and usually the
  mobile `user_agent`.
- Do not expect `is_mobile` to do anything on Firefox. It is unsupported in stock
  Playwright, so treat a Firefox context as desktop and design the scrape around that.
- Do not fight the screen size per context. Choose it with the seed and let every derived
  value follow, rather than forcing one number and creating a contradiction with the rest.

The general shape is the same one that runs through all of this: a value you can set is
not the same as a value that is coherent with everything around it, and mobile emulation
on Firefox is a place where the two come apart in a way Chromium habits do not prepare you
for.

## Conclusion

Mobile emulation is one of the sharpest examples of why a Chromium snippet cannot be
pointed at Firefox unchanged. `isMobile` is unsupported at the Playwright layer, so an
iPhone preset raises before it does anything, and the screen dimensions are owned by the
seeded engine, so the one field you might expect to still work is accepted and then
overridden to keep the fingerprint consistent. Neither is a limitation to route around; they
are the difference between a browser that can be told to look like anything and a browser
whose story holds together. If the job needs a real phone, use a real mobile engine. If it
needs Firefox, treat it as the desktop it is.

## Short answers to the questions that lead here

**Does Playwright mobile emulation work on Firefox?** Not the way it does on Chromium.
`isMobile` is unsupported by Playwright on Firefox, so a device preset that carries it
raises instead of applying.

**Why does my iPhone device preset throw an error?** Because the preset includes
`is_mobile: True`, and Playwright refuses that option on a Firefox context. Remove that
field and the context builds, but it is still a desktop context.

**I set the screen size and the page reports a different one. Why?** Because the screen is
a seed-derived fingerprint field, and per-context overrides of it are accepted at the
protocol level and then ignored, so the whole set of screen values stays consistent.

**Can I make Firefox report a mobile user agent?** You can set the string, but the engine
still answers as Firefox everywhere else, so a mobile Safari user agent on Gecko is a
contradiction a detector can read.

**How do I change the screen size then?** Change the seed. A seed pins the full coherent
set of screen values at once, instead of overriding one field and desyncing it from the
others.

**Is this specific to invisible_playwright?** The `isMobile` limitation is upstream
Playwright and applies to stock Firefox too. The screen ownership is this project's
deliberate choice, so the fingerprint cannot be broken by a client-side viewport call.

## Sources

- Playwright's own API reference for the `is_mobile` context option, which
  [states outright that the option is not supported in Firefox](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-is-mobile),
  read from the library rather than from a tutorial.
- The Playwright 1.61 viewport commands that began sending a screen size, and this
  project's decision to accept those fields at the protocol level and ignore them so the
  seeded screen stays authoritative.
- This project's release gates, which compare screen and viewport values against a stock
  browser field by field.

**See also:** [the wire-protocol change behind those new fields](playwright-protocol-drift.md),
[why an inconsistent screen is a tell on its own](screen-size-headless-tells.md), and
[the checklist for a single site that blocks you](playwright-detected-as-bot.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The screen-ownership rule
here exists because the alternative, a client that can overwrite one fingerprint field,
is how a coherent identity comes apart.*
