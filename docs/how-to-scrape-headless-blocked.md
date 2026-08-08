---
title: "How to scrape a site that blocks headless browsers"
description: "Headless mode is rarely the block. Launch headless with a real fingerprint, then fix the GPU, font and screen tells that actually cause it, on your deploy box."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 2
---


# How to scrape a site that blocks headless browsers

To scrape a site that blocks headless browsers, launch headless with a real,
seed-consistent fingerprint and then fix the machine tells that travel with a
headless server: a software GPU, a font set that belongs to no real desktop, a
screen size no monitor produces, and a missing audio device. Headless mode itself is
rarely the signal a site checks, and switching to headful on the same server leaves
every one of those tells in place.

At a glance, these are the tells that carry code fixes here, and the fix that holds for
each versus the one that backfires:

| Tell | Why it appears in headless | The fix that holds (and the one that backfires) |
|---|---|---|
| Software GPU in the WebGL renderer string | No graphics hardware, so the machine falls back to a software rasterizer | Give the host a real GPU, or stay honest; faking the string makes the pixels contradict it |
| A font set that belongs to no real desktop | A bare container has no fontconfig to resolve or substitute a requested font | A bundled cross-platform font stack; installing more fonts is a stronger tell, not a weaker one |
| Screen and viewport numbers no monitor produces | Headless has no monitor, so `availHeight == height` claims a desktop with no taskbar | Let the seeded profile derive the screen; setting your own viewport recreates the tell |

The instinct, once a site starts serving a different page in headless mode, is to
switch to headful and hope the resources are worth it. That fixes less than people
expect, because the word "headless" is doing the wrong job in that sentence. A site
does not have a sensor for the word. It has a sensor for the things that usually
travel with it: a software GPU, a font set that belongs to no real desktop, a screen
size nobody has, no audio device. Run headful on the same server and every one of
those is still true.

This page is a tutorial in the order you would actually work it: launch headless with
a real fingerprint, confirm the page loads the way a human's would, then walk the
checklist of machine tells that are the real cause, with code for each one.

## Launch headless, and check what you actually got

Launching headless takes one call, and the object you get back is a standard Playwright
`Browser` with a real fingerprint already applied. Start here, then verify what the page
actually rendered before assuming the mode was the block.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    print(page.title())
```

`browser` here is a real Playwright `Browser`, so every standard method works exactly
as documented upstream. What is different is what `headless=True` means underneath:
this engine renders on the code path a visible window uses, and hides the window
itself rather than switching to a stripped rendering mode. On Windows that is a
compositor-level cloak on the window; on Linux it is a private virtual display; on
macOS the window stays transparent with occlusion checks pinned. The point of all
three is the same, and it is worth being specific about it because the same product
did not always get this right: for several releases, `headless=True` on Windows
rendered the browser on the real desktop anyway, because the hiding mechanism moved
the launching *thread* to an invisible desktop while the browser's own child
processes inherited the *parent process's* desktop instead, and stayed visible
regardless. The fix had to move to the window itself, in the browser binary, because
only the window's own owning process can set that attribute. See
[headless vs headful](headless-vs-headful.md) for the full account and why it stayed
unnoticed for as long as it did.

None of that buys you a GPU, though. A hidden window on a server with no graphics
hardware still renders in software, and that is where the rest of this page lives.

## Confirm it is actually the mode, not the machine

Before changing anything, separate the two. Open a fingerprint page in both modes on
the same box you deploy to and diff the reports field by field:

```python
with InvisiblePlaywright(seed=42, headless=False) as browser:
    page = browser.new_page()
    page.goto("https://example.com/fingerprint")
    headful_report = page.evaluate("() => window.__fp_report()")

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com/fingerprint")
    headless_report = page.evaluate("() => window.__fp_report()")

diff = {k: (headful_report[k], headless_report[k])
        for k in headful_report if headful_report[k] != headless_report[k]}
print(diff)
```

Whatever shows up in `diff` is the entire real-world difference between the two modes
on that machine. If it is empty, the mode was never your problem, and switching to
headful buys you nothing but memory and, on Linux, a display server. If it is not
empty, work down the checklist below in order, because that is also the order these
tells occur in and the order [the general checklist](playwright-detected-as-bot.md)
puts them in as step three.

## Fix one: the GPU string, and the harder problem behind it

A server with no graphics hardware falls back to a software rasterizer, and WebGL
says so in plain text:

```js
const gl = document.createElement('canvas').getContext('webgl');
const dbg = gl.getExtension('WEBGL_debug_renderer_info');
gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
// "ANGLE (Microsoft, Microsoft Basic Render Driver Direct3D11 vs_5_0 ps_5_0, D3D11)"
```

That string comes from the
[`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info)
extension, and it is [the loudest tell in a headless deployment](webgl-renderer-strings.md):
not automation, but truth - nothing was overridden, the machine really has no GPU.
Overriding the string in JavaScript to claim an NVIDIA card is not a fix, it
is a worse problem, because now the string disagrees with the pixels. A canvas hash
and a WebGL render are outputs, not values you can set, and a rasterizer draws
different antialiasing edges and different floating-point rounding than a real card
does. [We shipped exactly this contradiction once](renderer-string-vs-render.md): a
persona claimed a GTX 980, the render came from a software path on headless Linux,
and a commercial detector's tampering flag went from clean on Windows with a real GPU
to flagged on the exact same seed on a GPU-less host. The fix was never "claim it
harder." It was giving the machine a GPU, or being honest that the render is
software:

```python
with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com/fingerprint")
    vendor, renderer = page.evaluate("""() => {
        const gl = document.createElement('canvas').getContext('webgl');
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        return [gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
                gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)];
    }""")
    assert "Basic Render Driver" not in renderer, "software rasterizer, check the host has a real GPU"
```

If your seed does draw that check, the deploy target is the thing to fix, not the
string.

## Fix two: the font set

Ask for a platform in the user agent and a headless container will usually answer
with a different one, not because it removed fonts, but because a bare container has
no fontconfig telling it which real font to substitute when a page asks for one that
is not there. [The three causes are the same ones that produce a wrong canvas
hash](headless-fonts-differ.md): missing font resolution, a different rasteriser
configuration, and a measurement technique that inherits both. Check what your build
actually reports before assuming it matches the platform you claim:

```python
with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    families = ["Segoe UI", "Calibri", "Cambria", "Tahoma", "Verdana"]
    detected = page.evaluate("""(candidates) => {
        const span = document.createElement('span');
        span.style.position = 'absolute';
        span.style.fontSize = '72px';
        span.textContent = 'mmmmmmmmmmlli';
        document.body.appendChild(span);
        span.style.fontFamily = 'monospace';
        const base = span.offsetWidth;
        const out = [];
        for (const family of candidates) {
            span.style.fontFamily = `"${family}", monospace`;
            if (span.offsetWidth !== base) out.push(family);
        }
        span.remove();
        return out;
    }""", families)
    print(detected)
```

Installing more fonts is not the fix, and can make it worse: a large mixed set that
does not belong to the claimed platform is a stronger tell than a small honest one.
The fix that actually holds is a font stack that does not read the host at all, which
is what this product bundles into the engine on all three platforms, so the same
identity reports the same fonts whether the machine underneath is Windows, Linux or a
container with nothing installed.

## Fix three: the screen and viewport numbers

A headless browser has no monitor, so every screen value it reports was decided by
something other than a monitor, and [the relationships between them are the actual
signal](screen-size-headless-tells.md). The one that catches almost everyone:
[`screen.availHeight`](https://developer.mozilla.org/en-US/docs/Web/API/Screen/availHeight)
equal to `screen.height` means the browser is claiming a Windows desktop with no
taskbar, which almost nobody has.

```python
with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    screen = page.evaluate("""() => ({
        width: screen.width, height: screen.height,
        availWidth: screen.availWidth, availHeight: screen.availHeight,
        outerWidth: window.outerWidth, outerHeight: window.outerHeight,
        innerWidth: window.innerWidth, innerHeight: window.innerHeight,
        dpr: window.devicePixelRatio,
    })""")
    assert screen["availHeight"] < screen["height"], "no taskbar reported, this is a headless tell"
    assert screen["outerHeight"] > screen["innerHeight"] > 0, "no browser chrome reported"
    assert screen["innerWidth"] <= screen["width"], "viewport bigger than the screen it lives in"
```

Do not fix this by setting an explicit viewport and calling it done. `new_page()`
already opens a viewport that fits inside the seeded screen, with the screen, the
device pixel ratio and the colour scheme derived from the same profile as everything
else, so the taskbar relationship holds without anyone remembering to subtract one
value from another. Passing your own `viewport=` kwarg overrides that, and asking for
one larger than the seeded screen recreates the exact impossible relationship the
check above catches.

## Fix four: run it more than once, and read the screenshot

A single green run of the checks above is not a pass, because this domain is not
deterministic. Run the same identity ten times, and open the PNG rather than trusting
the text log:

```python
with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com/fingerprint")
    page.screenshot(path="fp_headless.png")
```

A screenshot shows what the page actually rendered, including the parts an extractor
never thought to look at. And [pin the seed while you debug](quickstart.md): the
same seed produces the same GPU string, the same fonts and the same screen every
time, so a run that fails once can be replayed exactly instead of hoping the next
random draw reproduces the failure.

## Conclusion

Headless mode is a rendering path, not a signal a site checks for directly. The block
comes from what the path usually runs on top of: a GPU that is not there, a font set
that belongs to no claimed platform, a screen that describes no monitor. Fix those
four in order, on the machine you actually deploy to, and confirm each one with the
same comparison this page opened with: headful against headless, on the same box,
field by field. If the diff is empty once you are done, the mode was never the
problem, and if it is not, you now know exactly which field to chase next.

## Short answers to the questions that lead here

**Does headless mode get detected?** Rarely by itself. What gets detected is the
software GPU, the smaller font set, the missing audio device and the odd screen size
that usually come bundled with it, and a headful browser on the same server still has
all four.

**Will switching to headful fix my block?** Sometimes, and less often than people
expect. It changes the rendering path and leaves every hardware tell in place, so it
fixes cases where the site checked the rendering path specifically and nothing else.

**Why does my renderer string say NVIDIA if the machine has no GPU?** Because
something set it to say that, and the pixels are still drawn by whatever rasterizer
is actually on the box. That mismatch is worse than an honest software string, because
it is a claim the render contradicts.

**Do I need to install more fonts in my container?** Only the ones that belong to the
platform you claim, and only if you can match the set closely. A bigger mixed set is a
stronger tell than a small honest one, not a weaker one.

**Why is `availHeight` equal to `height` a problem?** Because a real Windows desktop
has a taskbar cutting into the available area, and reporting no difference says the
machine has no taskbar, which almost nobody's does.

**How many times should I test before trusting a result?** At least ten. A verdict
that shows up once in ten runs is a verdict, and a single clean run in a
nondeterministic domain proves nothing.

**See also:** [the entry point for this whole cluster](how-to-scrape-without-getting-blocked.md),
[the general checklist for one site blocking you](playwright-detected-as-bot.md), and
[how to test whether your browser is detected](how-to-test-bot-detection.md) for the
comparison method used throughout this page.

## Sources

- [Headless vs headful](headless-vs-headful.md), for the compositor-level fix and the
  thread-versus-process bug behind the Windows regression described above.
- [WebGL renderer strings](webgl-renderer-strings.md) and
  [renderer string vs render](renderer-string-vs-render.md), for the GPU string and the
  tampering flag it produced when the render disagreed with it. The extension itself is
  documented at MDN's
  [`WEBGL_debug_renderer_info`](https://developer.mozilla.org/en-US/docs/Web/API/WEBGL_debug_renderer_info).
- [Why headless renders different fonts](headless-fonts-differ.md), for the three
  causes behind a mismatched font set.
- [Screen size and viewport tells in headless browsers](screen-size-headless-tells.md),
  for the taskbar relationship and the rest of the screen checklist, and MDN's
  [`Screen.availHeight`](https://developer.mozilla.org/en-US/docs/Web/API/Screen/availHeight)
  for the property itself.
- [Playwright in Docker](playwright-docker-detection.md), for the same six tells read
  as a container problem instead of a headless one.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The Windows hiding bug
described above shipped for several releases before anyone noticed, because the test
suite validating it used a different mechanism than the one it was meant to check.*
