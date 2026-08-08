---
title: "Can scrollbar width reveal my operating system?"
description: "Native scrollbar width is a layout metric decided by the OS and theme, not by navigator strings, and it leaks the real platform with no JavaScript override to hide it."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 35
---


# Can scrollbar width reveal my operating system?

Short version: yes, it can, and it does it without touching a single `navigator`
property. A classic Windows scrollbar is about seventeen pixels wide. An overlay
scrollbar, the kind that fades in over the content and reserves no space, is zero
pixels wide until you hover it. Those two numbers describe two different operating
systems, and a page can read the difference in three lines of script that no property
patch intercepts.

This matters because the value is not a string you can set. It is a layout
measurement, produced by the real toolkit as it draws the page, and that is exactly
what makes it a useful tell and a hard one to fake convincingly.

## The measurement, and why nothing overrides it

Put a scrollable element on the page and the browser lays it out with a content box
inside a border box. If a classic scrollbar is present, it eats space from the content
box. The gap between the two widths is the scrollbar:

```javascript
const el = document.createElement("div");
el.style.cssText = "width:100px;height:100px;overflow:scroll;position:absolute;top:-9999px;";
document.body.appendChild(el);
const scrollbarWidth = el.offsetWidth - el.clientWidth;
el.remove();
// classic Windows: ~17. overlay scrollbars: 0.
```

There is no `navigator.scrollbarWidth`, no property to redefine, no getter to shadow.
The number falls out of geometry after the fact. You cannot lie about it in JavaScript
the way you can lie about `navigator.platform`, because you are not the one who
computed it. The layout engine did, using the platform's own scrollbar metrics.

That independence is the whole point. A detector that has already read your user agent
now has a second, unrelated witness for the same claim, and the two have to agree.

## Why it is an operating-system tell specifically

Scrollbar geometry is not a browser decision. It comes from the platform theme.

- Windows, with overlay scrollbars turned off (the traditional desktop look), draws a
  solid scrollbar roughly seventeen pixels wide that reserves its own column.
- Overlay scrollbars, common on other desktop themes and the mobile default, reserve
  no space at all and measure zero until interacted with.
- The exact width shifts with the theme, the display scaling, and the toolkit version,
  so it is not a single magic constant so much as a family of platform-consistent
  values.

So the measurement does not just say "a scrollbar exists". It narrows the platform,
and it does so from a completely different subsystem than the string-based signals.
This is the same family of leak as [what CSS media queries expose about the
machine](css-media-query-fingerprinting.md): a value the page reads directly from the
rendering environment, out of reach of anything that patches JavaScript objects.

It also pairs badly with a mismatched user agent. If [`navigator.platform` claims one
OS while the build was compiled on another](navigator-platform-oscpu-consistency.md),
a zero-width scrollbar under a Windows user agent is a second sentence contradicting
the first. Detectors rarely flag an unusual value. They flag two values that should
agree and do not.

## Why a real Windows build passes this without a patch

There are two ways to make a Windows persona report a Windows scrollbar. One is to
intercept the layout math and inject seventeen pixels where the real value would have
been zero. That is fragile: it has to be consistent with the theme, the scaling, the
element type, and every other layout metric a page might cross-check, and any one of
those left unpatched becomes the new tell.

The other way is to run a browser that is genuinely a Windows build, so the toolkit
computes Windows scrollbar metrics natively, the same way it computes them for a human
on a Windows desktop. Nothing is injected because nothing needs to be. The width is
right because it was produced by the same code path that produces it for a real user,
and it stays consistent with the screen metrics, the fonts, and the media queries
because they all come from the same real platform.

That is the approach this project takes: the fingerprint reads as a real browser
because in the ways that matter it is one, which is [why a coherent identity holds up
where a patched-together one leaks somewhere you did not check](why-blocked-with-a-clean-fingerprint.md).
It is the same reason [font rendering matches the claimed OS instead of the host
machine](headless-fonts-differ.md): the metrics are the platform's, not a spoof
layered on top.

## Measuring it yourself with invisible_playwright

Switching from stock Playwright is a two-line change, and the returned object is a
real Playwright `Browser`, so `page.evaluate` works exactly as documented upstream.
Here is the scrollbar measurement run against a live browser:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    scrollbar_width = page.evaluate("""() => {
        const el = document.createElement("div");
        el.style.cssText =
            "width:100px;height:100px;overflow:scroll;position:absolute;top:-9999px;";
        document.body.appendChild(el);
        const w = el.offsetWidth - el.clientWidth;
        el.remove();
        return w;
    }""")

    print("scrollbar width =", scrollbar_width)
```

Because the identity is derived from a seed, the same `seed=42` gives you the same
platform every run, so you can diff this value against a stock Windows Firefox on the
same machine and confirm they land in the same place. That comparison, against a real
browser rather than against a verdict, is the method that
[catches what a passing score hides](how-to-test-bot-detection.md).

The honest boundary of what you just measured: a correct scrollbar width tells the
page your rendering environment is consistent with the operating system you claim. It
says nothing about where your traffic exits, how many requests that account has made
this hour, or how the mouse got to the button. Those are separate witnesses, and this
one being right does not make them right.

## What this does and does not fix

The fingerprint, TLS handshake and driver layer of this project read as a genuine
Firefox, and the scrollbar metric is one small piece of why: it is correct for the
platform without a patch that could drift out of sync. That is real, and it is why
most fingerprint-based detection has nothing to catch.

It is also not the whole session. A perfectly Windows-shaped scrollbar on a datacenter
IP that a scoring endpoint has seen a thousand times today still loses, and it loses
for reasons that have nothing to do with layout:

- **IP reputation.** The scrollbar is a browser property. The exit address is not, and
  a clean browser on a flagged range is still on a flagged range.
- **Rate limits and per-account quotas.** These count requests, not pixels. No
  rendering detail changes how many actions an account is allowed.
- **Behaviour and timing.** Pointer motion, typing rhythm and pacing are watched
  independently of any static metric, and a machine-perfect scrollbar next to
  machine-perfect timing is its own contradiction.

You supply those: a clean residential exit, human pacing, request volumes an account
could plausibly produce. The browser removes the fingerprint tells so that the parts
only you control are the parts that decide the outcome.

## Conclusion

Scrollbar width is a small, honest example of a large idea. It is an
operating-system signal that lives in layout rather than in a property, so it ignores
every JavaScript-level disguise and simply reports what the toolkit drew. The durable
way to make it agree with a Windows persona is to render Windows scrollbars natively,
which a real Windows build does for free, rather than to patch the number and hope no
correlated metric gives the patch away.

And it is one witness among many. Getting it right is necessary and not sufficient:
the fingerprint being coherent is the part this project handles, and the exit, the
pacing and the volume are the parts you still have to bring.

## Short answers to the questions that lead here

**Can a website read my scrollbar width?** Yes, with no permission and no special API.
It measures [`offsetWidth`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/offsetWidth)
minus [`clientWidth`](https://developer.mozilla.org/en-US/docs/Web/API/Element/clientWidth)
on a scrollable element, and the gap is the scrollbar.

**Does it really reveal the operating system?** It narrows it. A roughly
seventeen-pixel classic scrollbar and a zero-width overlay scrollbar describe
different platforms and themes, and the value has to agree with your user agent.

**Can I override scrollbar width in JavaScript?** No. It is a layout measurement
computed by the rendering engine after the fact, not a property you can redefine, so
there is no getter to shadow.

**Why does a real Windows build pass this?** Because the toolkit computes Windows
scrollbar metrics natively, the same code path a human on Windows hits, so nothing is
injected and nothing drifts out of sync with the other platform metrics.

**Does fixing this make me undetectable?** No. It removes one fingerprint tell. It
does not touch IP reputation, rate limits, per-account quotas, or behaviour, and a
clean fingerprint on a bad IP still fails.

**Does the scrollbar affect my IP or rate limits?** Not at all. It is a rendering
property with no bearing on the network exit or how many requests an account is
allowed to make.

## Sources

- The [CSS Overflow](https://www.w3.org/TR/css-overflow-3/) and
  [CSSOM View](https://www.w3.org/TR/cssom-view-1/) specifications, which define
  `overflow: scroll` layout and the `offsetWidth` / `clientWidth` geometry the
  measurement reads.
- This project's own fingerprint parity checks, which compare a seeded identity's
  layout metrics against a stock Firefox on the same machine, field by field.

**See also:** [what CSS media queries reveal](css-media-query-fingerprinting.md) for
the same class of script-free layout tell, [navigator.platform on a spoofed
OS](navigator-platform-oscpu-consistency.md) for the string-based signal this one has
to agree with, and [screen metrics that give away a headless
machine](screen-size-headless-tells.md) for another geometry that has to match the
claimed platform.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The scrollbar is real
because the build is; the exit and the pacing are still yours to get right.*
