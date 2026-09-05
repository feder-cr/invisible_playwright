---
title: "Missing fonts in Docker break Playwright screenshots"
description: "Docker images ship almost no fonts, so Playwright renders missing glyphs as boxes or the wrong family. How to install real coverage and verify it rendered."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 28
---


# Missing fonts in Docker break Playwright screenshots

A slim container image ships almost no fonts, so a browser inside it either renders missing glyphs as empty boxes, known as tofu, or silently substitutes one fallback family for everything the page asks for. The fix is installing real coverage for the scripts you need, then confirming the browser picked it up, not just that files exist on disk.

## Why a slim base image turns text into boxes or the wrong font

This is one of the four machine-level tells covered in [how to run Playwright in Docker without getting detected](how-to-run-playwright-docker-undetected.md); this page is the long version of the fonts one.

Two different failures share one root cause, and they look nothing alike on screen. When the page asks for a font family the system has nothing matching, the rendering stack falls back through a chain: to a generic family (serif, sans-serif, monospace), then to whatever font actually exists on the box.

If that fallback font can draw the requested characters, the page renders in the wrong typeface with different letter shapes and widths, and nothing about it looks broken. It just looks like a different font, quietly substituted.

The second failure is louder. When no installed font covers a character at all, commonly a CJK ideograph or an emoji on an image carrying only Latin fonts, the renderer draws the font's own placeholder for a missing glyph: an empty box sometimes called tofu. That box is not a bug in your code; it is the honest rendering of "no glyph exists for this codepoint here."

## Which font packages actually cover what you need

Debian and Ubuntu-based images, which most Playwright containers are, ship almost nothing beyond a couple of core families. Covering Latin, CJK and emoji properly needs a short, specific package list rather than one catch-all install.

| Content | Package (Debian/Ubuntu) | What breaks without it |
|---|---|---|
| Latin, Cyrillic, Greek | `fonts-noto` | Text falls back to whatever the base image already has, often nothing close to the platform you claim |
| CJK (Chinese, Japanese, Korean) | `fonts-noto-cjk` | Every CJK glyph renders as an empty box; there is no partial-match fallback for ideographs |
| Emoji | `fonts-noto-color-emoji` | Emoji render as boxes or a flat placeholder instead of color art |
| Metric-compatible Latin business fonts | `fonts-liberation` | Pages that name Arial, Times New Roman or Courier New get a substitute with different character widths |

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-liberation \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m invisible_playwright fetch

COPY script.py .
CMD ["python", "script.py"]
```

`fc-cache -f` rebuilds the font cache after installing packages; skip it and a freshly added family can still be invisible to anything that asked before the cache refreshed. This applies whether you launch through `invisible_playwright` or plain `playwright.sync_api`: the package list and the cache rebuild are what fix the fonts, not which Python class calls `launch()`.

## Confirming the font you expect is the one that rendered

Installing packages is necessary and not sufficient. Confirm the browser is actually using what you installed, from three angles.

First, check the font system itself sees the family, from inside the running container:

```bash
docker exec <container> fc-list | grep -i noto
```

An empty result means the package never installed or the cache never rebuilt, regardless of what the Dockerfile claims to do. The package names above were read from Debian's own archive on 5 September 2026: `fonts-noto` is the metapackage that pulls in everything, `fonts-noto-core` is the leaner "No Tofu" core set, `fonts-noto-cjk` covers CJK, `fonts-noto-color-emoji` is Google's colour emoji font, and `fonts-liberation` supplies metric-compatible substitutes for Times, Arial and Courier. The image itself is a starting point to adapt, not a recipe we have built for your application; the `fc-list` check below is the part that tells you whether your version of it worked.

Second, do not reach for `document.fonts.check()` here, and this is the part worth
measuring before you trust it. The CSS Font Loading API looks like a presence test and is
not one. MDN defines it as returning true "if you can render some text using the given
font specification without attempting to use any fonts in this FontFaceSet that are not
yet fully loaded", which is a statement about web fonts still downloading, not about
whether a family exists on the machine.

Measured on 5 September 2026 against a local page: `document.fonts.check()` returned
`true` for `"Noto Sans"`, `true` for `"Arial"`, and `true` for a family invented on the
spot that exists nowhere. A family that needs no loading is trivially "ready", so the
answer is `true` whether it exists or not.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42, headless=True) as browser:
    page = browser.new_page()
    page.goto("http://127.0.0.1:8746/f.html")

    for family in ("Noto Sans", "Arial", "A Family That Does Not Exist"):
        print(family, page.evaluate(
            "f => document.fonts.check('16px \"' + f + '\"')", family))
    # all three print True
```

Third, be careful with the measurement trick too. The usual advice is to measure a string
in the family you installed and again in one you did not, expecting different widths. On
the same run those two widths came back **identical to the last decimal**, because this
engine serves declared text metrics rather than whatever the host happens to have, which
is the whole point of the [`measureText` surface](measuretext-textmetrics-fingerprinting.md)
being stable here. On a stock browser the two numbers do usually differ, so the trick is
not worthless in general; it just cannot be your check on this engine.

What is left is the check that actually answers the question: `fc-list` inside the
container, and your eyes on the screenshot.

## Why a different font set makes text metrics non-comparable across machines

`measureText()` and the wider `TextMetrics` surface return values that depend on the exact font file behind them, not just the family name you asked for. Two machines with different font sets can answer the identical `measureText()` call with different numbers for a string set to the identical CSS family, because the family name resolved to a different underlying file on each one.

That breaks any comparison across environments: a screenshot test that expects pixel-identical output between a laptop and a CI container, or a golden-image diff between two container builds, is really testing "do these two machines have the same fonts installed", not "did the code regress". A failing test can be a real bug or a font package that quietly changed between builds, and nothing about the failure alone tells you which.

The fix is not clever measurement. It is making the font set itself identical, and reproducible, everywhere the code runs, so a difference in the output means a difference in the code, not a difference in what happened to be on disk that day. When this shows up as a flaky screenshot-diff in CI instead of an obvious box, [recording a trace](record-playwright-trace-debug-scraper.md) and reading the filmstrip is faster than re-running the job hoping it passes.

## Conclusion

Boxes and the wrong typeface in a container screenshot are not a Playwright bug and not an automation tell; they are a base image with almost no fonts on it. Install the specific packages your content needs, rebuild the font cache, then confirm with `fc-list` inside the container and by opening the screenshot, because the two browser-side checks that look like they answer this question do not. A font set that differs between machines is rendering you cannot reproduce, and reproducing rendering is the whole point of running the same code somewhere else.

## Short answers to the questions that lead here

**Why do my Playwright screenshots show empty boxes for some characters?**
No installed font covers those codepoints, so the renderer draws the font's built-in placeholder glyph for a missing character, commonly for CJK text or emoji on an image that only ships Latin fonts.

**Why does text render in the wrong font instead of missing entirely?**
A similar-enough font exists on the system, so font fallback substitutes it silently. Nothing errors; the page just renders in a typeface with different letter shapes and widths than intended.

**Which font packages should I install in a Playwright Docker image?**
`fonts-noto` for broad Latin and script coverage, `fonts-noto-cjk` for Chinese, Japanese and Korean, `fonts-noto-color-emoji` for emoji, and `fonts-liberation` if pages expect Arial, Times New Roman or Courier New specifically.

**How do I check a font actually installed inside a running container?**
`docker exec <container> fc-list | grep -i <family>`. An empty result means the package never installed or the font cache never rebuilt after installing it.

**Why do screenshot or text-metric tests pass locally and fail in CI?**
The two environments likely have different font sets, so identical `measureText()` calls or screenshots differ for a font reason that has nothing to do with the code change under test.

**See also:** [how to run Playwright in Docker without getting detected](how-to-run-playwright-docker-undetected.md), [measureText and TextMetrics as a fingerprinting surface](measuretext-textmetrics-fingerprinting.md), and [recording a Playwright trace to debug a failed scrape](record-playwright-trace-debug-scraper.md).

## Sources

- Debian package `fonts-noto-core`, https://packages.debian.org/trixie/fonts-noto-core - described by Debian itself as the "No Tofu" font families with large Unicode coverage, which is where the word tofu in this page comes from. Read 5 September 2026 on the trixie (stable) page.
- MDN, `FontFaceSet.check()`, https://developer.mozilla.org/en-US/docs/Web/API/FontFaceSet/check - returns true "if you can render some text using the given font specification without attempting to use any fonts in this FontFaceSet that are not yet fully loaded", and takes a CSS font specification plus optional text. Read 5 September 2026.
- MDN, `CanvasRenderingContext2D.measureText()`, https://developer.mozilla.org/en-US/docs/Web/API/CanvasRenderingContext2D/measureText - returns a TextMetrics object "that contains information about the measured text (such as its width, for example)". Read 5 September 2026.
- Debian package search, https://packages.debian.org/ - the package names quoted in this page were read there on 5 September 2026; confirm the exact names for the release your base image is built on, because they differ between Debian releases.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright). The font problem here is not specific to this project: any Playwright container, patched engine or stock, inherits whatever the base image ships.*
