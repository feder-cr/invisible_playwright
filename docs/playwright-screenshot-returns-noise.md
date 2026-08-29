---
title: "Playwright screenshot returns noise: readback fix"
description: "Why Playwright page.screenshot() returned a noise PNG instead of the page, and the principal-split canvas readback fix that made captures clean again."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 5
---


# Playwright screenshot returns noise: readback fix

[`page.screenshot()`](https://playwright.dev/python/docs/api/class-page#page-screenshot)
returned a full-frame PNG of colored static, tens of
megabytes of it, because a canvas anti-fingerprint defense also rewrote the
browser's own screenshot readback. The noise was the same every time for a given
seed and different for a different seed. The automation ran, the navigation
succeeded, the file was written, and the file was garbage. The fix keyed the
transform on a security boundary the engine already draws, exempting the
privileged screenshot read while leaving every web-content read spoofed.

This is a closed bug in this product, written up because the shape of it is
instructive.

The cause was a fingerprint defense that was doing its job a little too well,
and could not tell the browser's own screenshot machinery apart from a web page
reading its own canvas. The fix was to teach it the difference. Here is the
whole chain: the symptom, why a canvas defense reached a screenshot at all, and
the principal split that fixed it without weakening the defense by a single
byte.

## The symptom: a screenshot that came back as static

The report was concrete. A script navigated, waited for the page to settle, and
called `page.screenshot(path="out.png")`. The call did not error. The PNG was
roughly 60 MB, which is already wrong for a normal page capture, and opening it
showed uniform per-pixel noise across the entire frame with no page visible
underneath.

Two details narrowed it immediately. First, the noise was stable for a fixed
seed and changed with the seed, so it was not random corruption; it was
deterministic and it was ours. Second, the page itself worked: text extraction,
clicks and DOM queries all returned correct data. Only the pixels coming back
through the screenshot path were wrong.

That combination points away from the network, the proxy and the page, and
straight at a pixel transform that should only touch web content but was
touching the capture as well.

## Why a canvas defense reached the screenshot at all

To keep a canvas fingerprint from revealing the host OS, this build rewrites the
pixels a web page reads back from a canvas. When a page draws to a canvas and
then reads it, the bytes it gets are substituted with a per-seed value, so the
resulting hash depends only on the seed and not on whether the underlying
rasterizer is a Windows, Linux or macOS one. That is what keeps a canvas hash
consistent with the rest of the spoofed machine. The mechanism is described in
[canvas fingerprint noise](canvas-fingerprint-noise.md), and its cross-platform
purpose in [canvas and WebGL cross-platform consistency](canvas-webgl-cross-platform-consistency.md).

The trap is in the word "readback". A screenshot is also a readback. When
Playwright captures a page, the browser renders the current window into an
offscreen surface and reads the pixels out, and internally that read went
through the same code path as a page reading its own canvas. The pixel
substitution did not distinguish the two, so it rewrote the screenshot pixels
exactly as if the page had asked for them. The bigger the viewport, the bigger
the noise PNG, which is where the tens of megabytes came from.

So the defense was not broken. It was correct and it was blind: it protected
every readback, including the one readback that is the automation looking at its
own page, which no detector ever sees and which therefore never needed
protecting.

## The fix: split the privileged reader from the web page

The distinction that solves this already exists inside the browser. A read that
originates from web content runs under that page's origin. A read that
originates from the browser's own machinery, such as the screenshot capture,
runs under the browser's trusted system context, the same privileged context
that Firefox itself exempts from its built-in canvas fingerprinting protection.
The two are not guesses about intent; they are a property the engine already
carries on every readback.

The fix reuses exactly that boundary. Before substituting pixels, the canvas
code now checks whether the read is coming from a privileged, trusted context
(the browser's own system context, or its internal chrome and resource origins)
or from ordinary web content. Privileged reads are passed through untouched, so
the screenshot is the real page, byte for byte. Web-content reads are
substituted exactly as before, so the fingerprint stays spoofed. It is the same
exemption boundary the browser's own canvas protection uses, applied to our own
transform rather than invented for it.

The whole fix is that one distinction:

| Read origin | Pixels returned | Who can trigger it |
|---|---|---|
| Privileged, trusted context (the screenshot capture, internal chrome and resource origins) | The real page, unchanged | Only the browser's own machinery |
| Ordinary web content | Substituted per seed, so the hash matches the spoofed machine | Any page, including a detector |

The result, verified on the shipped binary at a fixed seed: screenshots are
identical to a build from before the substitution existed, while a web page's
canvas read at that same seed returns the same spoofed bytes it did the release
before. The screenshot got its page back and the fingerprint lost nothing.

## Reproduce it: capture the page and check the bytes

The whole point of a seed is that a bug like this is reproducible rather than
anecdotal. Fix the seed, capture, and assert two things at once: the screenshot
is a sane size and it is a real image, not a wall of noise.

```bash
pip install invisible-playwright
```

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")

    png = page.screenshot()          # bytes, no per-seed static in them
    with open("capture.png", "wb") as f:
        f.write(png)

    # A full-frame noise PNG barely compresses and runs to tens of MB.
    # A real page capture at this viewport is a few hundred KB.
    print("screenshot bytes:", len(png))
    assert len(png) < 5_000_000, "screenshot is suspiciously large - open it"
```

If you want to see both halves of the split in one run, read a canvas from
inside the page as well. The screenshot stays clean while the in-page read stays
spoofed and seed-stable, which is the behavior the fix guarantees:

```python
from invisible_playwright import InvisiblePlaywright

def canvas_hash(page):
    return page.evaluate("""() => {
        const c = document.createElement('canvas');
        c.width = 300; c.height = 150;
        const ctx = c.getContext('2d');
        ctx.textBaseline = 'top';
        ctx.font = '16px sans-serif';
        ctx.fillStyle = '#069';
        ctx.fillText('readback split demo', 10, 10);
        return c.toDataURL();
    }""")

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    page.screenshot(path="clean.png")   # the real page
    first = canvas_hash(page)            # spoofed, per-seed

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    second = canvas_hash(page)

assert first == second, "same seed must give the same canvas readback"
```

Open `clean.png`. Reading the log is not enough here; the original bug was
caught by opening the PNG, because the byte count alone was merely surprising
while the pixels were unambiguous. That habit is worth keeping for any capture,
as the [testing guide](how-to-test-bot-detection.md) argues at more length.

## What the fix does not change

Worth stating plainly, because a "we stopped rewriting pixels" headline invites
the wrong conclusion. The anti-fingerprint substitution is unchanged for every
read that a detector can actually make. A page that draws to a canvas and hashes
the result gets the same per-seed value it always did, so a canvas hash still
looks like the spoofed machine rather than the host that built the binary. Only
the browser's own privileged captures, which no page and no detector can issue,
were exempted.

Nothing about the screenshot leaks the real machine either, because a screenshot
is a picture of the page, not a probe of the host: it carries whatever the page
rendered, and the surfaces a page could interrogate to unmask the host, such as
the [renderer string versus the actual render](renderer-string-vs-render.md),
are governed by their own defenses that this change does not touch. The split is
narrow on purpose. It restores one trusted path and leaves every untrusted one
exactly as spoofed as it was.

## Conclusion

A screenshot that comes back as per-seed static is not flakiness and not a bad
proxy. In this product it was a single, closed defect: a canvas fingerprint
defense that protected every pixel readback, including the browser's own
screenshot capture, and rewrote the capture into noise. The fix was not to
weaken the defense but to give it the distinction the engine already draws,
between a privileged trusted read and a web-content read, and to exempt only the
former. Screenshots are byte-for-byte clean, the fingerprint is byte-for-byte
spoofed, and both were proven on the shipped binary at the same seed.

The general lesson outlives the bug: a defense that transforms output has to
know who is asking, and the safest boundary to key on is one the platform
already enforces rather than one you invent.

## Short answers to the questions that lead here

**Why does page.screenshot() return noise or a huge PNG?** In older builds a
canvas anti-fingerprint transform also rewrote the browser's own screenshot
readback, producing a full-frame per-seed noise image tens of megabytes in size.
Update to a build with the privileged-readback exemption and the capture returns
the real page.

**Is the screenshot noise random or corruption?** Neither. It was deterministic
per seed, which is exactly what identified it as the fingerprint substitution
rather than a decode or transport error.

**Does fixing screenshots weaken the fingerprint?** No. Only privileged, trusted
reads (the screenshot path) were exempted. A web page's canvas read is still
substituted per seed, verified identical to the previous release at the same
seed.

**My clicks and text extraction work but the screenshot is wrong. Why?** Because
the page and the DOM were never affected; only the pixel readback path was. That
split is the tell that points at a capture-side transform rather than the page.

**How do I confirm the capture is clean?** Fix a seed, capture, check the byte
size is in the hundreds of KB rather than tens of MB, and open the PNG. A text
log will not show you a wall of noise; the pixels will.

**Can I still read a canvas for my own use in the page?** Yes, and it returns a
stable per-seed value. That read is web content, so it is spoofed by design; if
you need faithful pixels for real work, capture them through a screenshot, which
is the exempted path.

## Sources

- This project's patch notes for the canvas readback path, including the
  firefox-17 change that exempted privileged, trusted reads from pixel
  substitution, and the before/after verification on the released binary at a
  fixed seed.
- The engine's own canvas fingerprinting protection, whose privileged-context
  exemption this fix reuses rather than reinvents. Mozilla documents the
  underlying system-principal check in the [resistFingerprinting implementation notes](https://firefox-source-docs.mozilla.org/toolkit/components/resistfingerprinting/resistfingerprinting/implementation.html), retrieved 2026-08-28.

**See also:** [canvas fingerprint noise](canvas-fingerprint-noise.md) for what
the substitution is protecting against, [canvas and WebGL cross-platform
consistency](canvas-webgl-cross-platform-consistency.md) for why the transform
exists at all, and [the checklist for being detected on one site](playwright-detected-as-bot.md)
when the problem is the page rather than the capture.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. This bug shipped,
was caught by opening a PNG, and was closed by keying on a boundary the engine
already draws.*
