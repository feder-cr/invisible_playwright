---
title: "Splash is unmaintained, and it was never a real browser"
description: "Splash is a QtWebKit render service last pushed in August 2024, so every JavaScript surface reports an engine no real browser ships, which no spoof reaches."
parent: "Comparisons"
nav_order: 17
---


# Splash is unmaintained, and it was never a real browser

Splash is a QtWebKit render service whose default branch was last pushed in August 2024,
and QtWebKit is an engine no consumer browser ships. Both facts matter, but the second is
the one no spoof reaches: every JavaScript-visible surface reports an engine that matches
no real audience, and that mismatch lives below any header, so no user-agent string or
page-level plugin corrects it.

Splash is a render service: you hand it a URL over an HTTP API, it runs the page and
hands back HTML, a screenshot or a rendered DOM. For a long time that was a reasonable
way to execute JavaScript on a server without driving a full browser yourself. It is
still packaged, still starred, and still recommended in old answers.

This page is about two separate problems with reaching for it in 2026. The first is
that it has stopped moving. The second is deeper and would matter even if it had not:
the engine it renders with is one that no person browses the web with, so the identity
it presents matches no real population before you have set a single header.

## What Splash actually is

Splash renders pages with QtWebKit, the WebKit binding that ships with the Qt5
toolkit. That is not a detail of how it is built, it is the whole product: a scriptable
WebKit executed headless and exposed over HTTP.

The consequence is that everything a page can read in JavaScript comes from QtWebKit.
The `navigator.userAgent` string, `navigator.vendor`, the way the canvas rasterizes a
glyph, the WebGL renderer string and the WebGL pixels behind it, the set of supported
codecs, the exact rounding of a layout measurement: all of it is QtWebKit's, because
there is no other engine underneath to borrow from.

## QtWebKit is an engine nobody browses with

QtWebKit is a WebKit fork that no consumer browser ships today, so the real-world
audience running it is zero, not merely small. This is the part that a user-agent string
hides and does not fix.

Every browser a real visitor uses is one of three engines: Blink (Chrome and the
Chromium family), WebKit proper (Safari), or Gecko (Firefox). QtWebKit is a fourth
lineage. It forked from WebKit years ago, and it is not the WebKit that ships in any
consumer browser today. When you measure a real audience, the count of visitors on
QtWebKit is zero, not small.

So a detector does not need a cleverly hidden tell. The engine is the tell. A canvas
hash that no Blink, WebKit or Gecko build has ever produced, a WebGL renderer string
that pairs with no real driver, a set of feature-detection results that line up with no
shipped browser: these are not spoofable properties sitting on top of a normal engine.
They are what the engine genuinely is. You can set `navigator.userAgent` to a current
Chrome string, and the first canvas or WebGL read disagrees with it, because the pixels
come from somewhere the header cannot touch. This is the same failure mode as
[a renderer string that says NVIDIA while the pixels say software](renderer-string-vs-render.md),
except here the entire engine is the mismatch rather than one field of it.

It is also why "Splash plus a stealth plugin" does not close the gap. A page-level
plugin rewrites the answers JavaScript gives to a handful of known questions. It does
not change which engine drew the canvas or handed back the WebGL renderer, and those
are exactly the surfaces that give a non-consumer engine away.

## The maintenance date, which is the easy half

Checked at the source rather than from memory: the repository's last push to its
default branch is dated **2024-08-02**. As of this writing that is very close to two
years with no commit, on a project of roughly 4,200 stars. The repository is not
archived, so nothing announces the state on the page. You have to read the date.

Two years is a long time for a browser engine specifically, because the population it
has to blend into moves every few weeks. New engine versions ship, feature detection
shifts, the shape of a normal fingerprint drifts. An engine frozen in 2024 does not
drift with it. But the staleness is the smaller problem here. A frozen Blink would at
least be a frozen version of an engine real people ran. A frozen QtWebKit is a frozen
version of an engine they never ran at all.

## Why a header or user-agent spoof cannot reach it

A header or user-agent spoof cannot reach QtWebKit's tells because it rewrites the
request, not the engine, and the canvas, WebGL renderer and feature-detection results are
computed by the engine. It helps to separate the two layers, because people spend effort
on the wrong one.

The layer you can rewrite from outside the engine is the request and a few named
JavaScript properties: the user-agent header, the `Accept-Language` header, a pinned
`navigator.platform`. These are cheap to change and cheap to check against each other.

The layer you cannot rewrite from outside is what the engine computes: how it draws,
how it measures, what its graphics stack reports, how it answers hundreds of small
capability questions. Those come from the compiled engine. To change them you change
the engine, and if you are running a render service the engine is the service.

| Layer | What it includes | Can a spoof change it? |
|---|---|---|
| Request and named JS properties | user-agent header, `Accept-Language`, `navigator.platform` | Yes: cheap to set, cheap to cross-check |
| What the engine computes | canvas pixels, WebGL renderer, layout rounding, feature detection | No: only changing the engine changes these |

This is why the durable difference between a render service and a real browser is not
"how new is it". It is which of these two layers you are standing on. A tool built on a
consumer engine starts from a body that a real audience actually has and works on the
handful of outside-the-engine tells. A QtWebKit service asks you to defend the one
layer you cannot reach.

## What a real engine changes, with code

The alternative is to drive a real consumer engine with stock automation, so the
answers below the header come from an engine a real audience runs, and the seed makes
them reproducible. `invisible_playwright` launches a Firefox patched at the C++ level
and returns an ordinary Playwright `Browser`, so every method is the one you already
know.

Read the surfaces that a render service cannot honestly produce, straight off the page.
The renderer name below comes from the
[`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
extension, which every engine answers from its own graphics stack rather than from a
header:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    ua = page.evaluate("() => navigator.userAgent")
    vendor = page.evaluate("() => navigator.vendor")

    renderer = page.evaluate(
        """() => {
            const gl = document.createElement('canvas').getContext('webgl');
            const ext = gl.getExtension('WEBGL_debug_renderer_info');
            return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
        }"""
    )

    print("userAgent:", ua)
    print("vendor:   ", vendor)
    print("renderer: ", renderer)
```

Because `seed=42` fixes the identity, the userAgent, the WebGL renderer and the canvas
hash come back identical on every run, so a failing page is reproducible instead of a
new draw each time. That reproducibility is the point of a seed and is covered in the
[quickstart](quickstart.md).

To confirm the engine claim is honest rather than a header, read the same value twice
in one session and compare it to a stock browser on the same machine. A render service
gives itself away on the second read or on the diff, never on the header alone:

```python
from invisible_playwright import InvisiblePlaywright

def canvas_hash(page):
    return page.evaluate(
        """() => {
            const c = document.createElement('canvas');
            const ctx = c.getContext('2d');
            ctx.textBaseline = 'top';
            ctx.font = '14px Arial';
            ctx.fillText('the quick brown fox', 2, 2);
            return c.toDataURL();
        }"""
    )

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    first = canvas_hash(page)
    second = canvas_hash(page)
    assert first == second, "a stable canvas is the cheapest tampering check there is"
    print("canvas stable:", first[:48], "...")
```

The method for reading these reports instead of trusting a verdict is its own page:
[how to test whether your browser is detected](how-to-test-bot-detection.md).

## One honest caveat

Running a real engine fixes the engine layer, not the network layer. The exit IP is not
a browser property and no engine touches it: a genuine Firefox on a datacenter address
that a page has already seen a thousand times today still loses, and that is a different
fight from the one this page is about. Work it in order rather than buying a proxy on
day one, which is the whole point of the
[detected-on-one-site checklist](playwright-detected-as-bot.md).

## Conclusion

Splash's problem is not only that its last push is dated 2024-08-02. It is that the
engine underneath it, QtWebKit, is one no consumer browser ships, so every
JavaScript-visible surface reports a render-service engine that matches no real
population. That mismatch lives below the request, so no user-agent string,
`Accept-Language` header or page-level plugin reaches it. If you need a page executed on
a server and you need it to look like a person's browser, the engine has to be one a
person actually runs.

## Short answers to the questions that lead here

**Is Splash still maintained?** Its default branch was last pushed on 2024-08-02, close
to two years ago at this writing, and the repository is not archived so nothing on the
page announces it. Read the date yourself before relying on it.

**Can I make Splash pass fingerprint checks with a user-agent override?** No. The
user-agent is a header. The canvas, the WebGL renderer and the engine's feature
detection come from QtWebKit, and they contradict any header you set.

**What is wrong with QtWebKit specifically?** It is a WebKit fork that no consumer
browser ships today, so the audience running it is zero, not small. Real visitors are on
Blink, WebKit proper or Gecko.

**Does adding a stealth plugin to Splash fix it?** A plugin rewrites a few named
JavaScript properties. It does not change which engine drew the canvas or reported the
GPU, which are the surfaces that give a non-consumer engine away.

**Is a render service the same as a headless browser?** Not for this purpose. A render
service exposes an engine over HTTP; if that engine is not one real people browse with,
the output looks like the service no matter how you call it.

**If I switch to a real engine, am I done?** For the engine layer, yes. The exit IP is
separate and no engine changes it, so a real browser on a burned datacenter address can
still be blocked.

## Sources

- The Splash repository, read at its own source rather than assumed from its continued
  recommendation in older tutorials: [scrapinghub/splash](https://github.com/scrapinghub/splash),
  retrieved 2026-08-28. Default-branch last push dated 2024-08-02, roughly 4,200 stars, not
  archived, described as a scriptable browser as a service rendering with the Qt5 toolkit.
- QtWebKit itself, the WebKit port for the Qt toolkit that Splash renders with, read at its
  own source: [qt/qtwebkit](https://github.com/qt/qtwebkit), retrieved 2026-08-28.
- This project's own testing notes on why an engine-level surface cannot be reached by a
  header or a page-level override, and why a stable value read twice is the cheapest
  tampering check.

**See also:** [Chromium is not Chrome, and detectors know the difference](chromium-is-not-chrome.md)
for the same engine-lineage problem on the automation side, and
[how to scrape a site that blocks headless browsers](how-to-scrape-headless-blocked.md)
for the machine-level tells that survive every header change.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The engine underneath is
the one thing a header cannot rewrite, which is the whole argument of this page.*
