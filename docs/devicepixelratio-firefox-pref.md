---
title: "window.devicePixelRatio: the pref that spoofs it"
description: "Set window.devicePixelRatio in Firefox with the layout.css.devPixelsPerPx string pref, why plausible pref keys do nothing, and the three values it must match."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 22
---


# window.devicePixelRatio: the pref that spoofs it

In Firefox, [`window.devicePixelRatio`](https://developer.mozilla.org/en-US/docs/Web/API/Window/devicePixelRatio) is spoofed by one about:config pref,
`layout.css.devPixelsPerPx`, set as a string such as `"1.25"`. The keys that live in a
`screen` or a `dpr` namespace look right but are never read, so setting one changes
nothing. Getting the value to move is the easy half; keeping it consistent with the two
other places a device pixel ratio shows up is what keeps a session unremarkable.

There is a whole class of Firefox device-pixel-ratio spoofs that share one property:
they set a pref, the code runs without error, and `window.devicePixelRatio` does not
change. The value stays at whatever the machine reports, the spoof is announced nowhere,
and the only way you find out is by reading the property back.

This page is about the one pref that does move it, the reason the plausible-looking keys
do not, and the three values that have to tell the same story once you get it moving.

## Why a dpr spoof can run and change nothing

Firefox reads a fixed set of preferences at startup. A pref name that is not declared in
the build is not an error: you can set it, it lands in the profile, and no code ever asks
for it. It is a note left in a room nobody visits.

Device pixel ratio is the surface where this bites hardest, because the name that
suggests itself is almost never the name the engine reads. People reach for something in a
`screen` or a `dpr` namespace, set it, and move on without reading the value back. We did
a version of this ourselves in an earlier build: the wrapper wrote a dpr value to a key
that Firefox does not read, so it was a silent no-op for every session until we noticed
the property had never actually moved. We removed the dead write and kept only the pref
below. The lesson outlived the bug: a pref you set is not a pref that took effect, and
[the general version of that trap has its own page](firefox-prefs-not-applying.md).

## The pref Firefox actually reads

`window.devicePixelRatio` in Firefox is driven by the native about:config pref
`layout.css.devPixelsPerPx`. It is a string pref, and the value is the ratio written as
text:

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.firefox.launch(firefox_user_prefs={
        # a STRING, not a bare float: "1.25", not 1.25
        "layout.css.devPixelsPerPx": "1.25",
    })
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.evaluate("window.devicePixelRatio"))   # 1.25
    browser.close()
```

Two things about this pref that catch people out. It is a string, so pass the number as
text; a bare numeric value is a different kind of pref value and the parser will not treat
it the same way. And it is a global scale for the whole browser, not a per-context knob,
which is exactly why it has to be kept consistent with the per-context values Playwright
sets separately. That is the next section.

## The three values that must tell the same story

Setting the pixel ratio is the easy half. The half that gets a session flagged is
consistency, because a device pixel ratio shows up in three places that a detector can
read independently and compare:

1. `window.devicePixelRatio`, driven by the pref above.
2. Playwright's per-context [`device_scale_factor`](https://playwright.dev/python/docs/api/class-browser#browser-new-context), which scales the rendered viewport.
3. The CSS [resolution media feature](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/resolution), for example `matchMedia("(resolution: 1.25dppx)")`,
   which the browser answers from its own scaling.

If the pref says 1.25 and the context was created with `device_scale_factor=1.0`, the
number reported by `devicePixelRatio` and the geometry the page actually renders at
disagree. A real display never does that. The media query is the third witness: it has to
resolve to the same ratio the property reports, or a script that reads both has found a
contradiction that no real machine produces.

This is the same failure mode as a user agent claiming one platform on an engine
reporting another. The individual value is plausible; the disagreement is the tell. It is
also why [an impossible screen geometry is a datacenter signal](screen-size-headless-tells.md)
rather than an automation one, and why
[the CSS resolution query is worth reading directly](css-media-query-fingerprinting.md)
instead of trusting the single property.

## Measuring the agreement with a seed

The point of a seed-reproducible browser is that you stop hand-tuning these values and
start reading them back. With `invisible_playwright` the pixel ratio, the context scale
factor and the media-query answer are all derived from one seed and kept in agreement,
so the useful thing to write is not a setter but a check that the three witnesses match:

```python
from invisible_playwright import InvisiblePlaywright

CHECK = r"""
() => {
  const dpr = window.devicePixelRatio;
  // does the CSS resolution query agree with the property?
  const mq = matchMedia(`(resolution: ${dpr}dppx)`).matches;
  return {
    devicePixelRatio: dpr,
    resolutionQueryAgrees: mq,
    screen: `${screen.width}x${screen.height}`,
    // physical pixels implied by the scale
    physical: `${Math.round(screen.width * dpr)}x${Math.round(screen.height * dpr)}`,
  };
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    report = page.evaluate(CHECK)
    print(report)
    assert report["resolutionQueryAgrees"], "dpr and the resolution media query disagree"
```

Run it twice with the same seed and every field comes back identical, which is what makes
a failure reproducible: if a page reacts to the pixel ratio, you can replay the exact same
ratio rather than hoping the next random draw lands on it. Run it against a stock browser
on the same machine and diff the report field by field; anything that differs other than
the address is a candidate, and `resolutionQueryAgrees` being `false` on either side is a
straight fail, not a score to weigh.

## What a mismatch looks like from the outside

It helps to picture what a script sees when the three values are not aligned, because it
explains why "just set devicePixelRatio to a normal number" is not enough.

A common real-display pairing is a 1920x1080 screen at a device pixel ratio of 1.0, or a
scaled laptop panel at 1.25 or 1.5. A detector does not check whether your number is one
of those. It checks whether the number, the rendered geometry and the media query point
at the same display. A browser reporting 2.0 while rendering at 1.0 scale, or answering
the resolution query as if it were 1.0 while `devicePixelRatio` says 2.0, has described a
screen that cannot exist. That is a positive signal of tampering, and it is worse than
reporting a boring, honest 1.0, because an inconsistency is information a plain value is
not. The whole reason to read the value back after setting it is to be sure you produced a
real display and not an impossible one.

## Conclusion

Device pixel ratio is a small field with a large blast radius. The pref that moves it in
Firefox is `layout.css.devPixelsPerPx`, set as a string; the plausible-sounding keys in
other namespaces are notes in an empty room. Getting the value to change is the easy part.
The part that keeps a session unremarkable is making the property, the context scale
factor and the CSS resolution media query agree, and then reading all three back to prove
they do, against a stock browser, more than once.

## Short answers to the questions that lead here

**How do I set window.devicePixelRatio in Firefox?** Set the about:config string pref
`layout.css.devPixelsPerPx` to the ratio as text, for example `"1.25"`. It is the only
pref that actually drives the property.

**I set a dpr pref and nothing changed. Why?** Almost certainly you set a pref name the
engine does not read. A pref that is not declared in the build is accepted silently and
never consulted. Read `window.devicePixelRatio` back to confirm it moved.

**Does device_scale_factor set devicePixelRatio?** It scales the rendered viewport for a
context, and it must match the pref, but it is a separate value. If they disagree the
browser reports a display that cannot exist.

**What device pixel ratio should I use?** One that a real display has, like 1.0, 1.25 or
1.5, and the same value in all three places. The specific number matters less than the
three witnesses agreeing.

**Can a site detect a spoofed dpr?** Not from a plausible value on its own. It detects the
contradiction when the property, the render geometry and the resolution media query point
at different displays.

**Why does the ratio have to be a string?** `layout.css.devPixelsPerPx` is a string pref;
pass the number as text, `"1.25"`, not as a bare float.

## Sources

- [MDN: `Window.devicePixelRatio`](https://developer.mozilla.org/en-US/docs/Web/API/Window/devicePixelRatio) -
  the ratio of physical pixels to CSS pixels that the property reports.
- [MDN: the `resolution` media feature](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/resolution) -
  the `dppx` unit used in the media-query check above.
- [Playwright: `device_scale_factor` on `browser.new_context`](https://playwright.dev/python/docs/api/class-browser#browser-new-context) -
  the per-context knob that must agree with the pref.
- Firefox's `layout.css.devPixelsPerPx` pref itself, and the dead dpr write once shipped
  here, read from the browser's own behaviour rather than from a rendered report.

**See also:** [why a pref you set can quietly do nothing](firefox-prefs-not-applying.md),
[what an impossible screen geometry gives away](screen-size-headless-tells.md), and
[reading the CSS resolution query directly](css-media-query-fingerprinting.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The dead dpr write in this
page is one we shipped ourselves, and only caught by reading the value back.*
