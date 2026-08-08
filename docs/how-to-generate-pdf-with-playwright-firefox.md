---
title: "How to generate a PDF with Playwright and Firefox"
description: "page.pdf() is Chromium-only in Playwright, so it fails on a Firefox engine. Why the call is unavailable, plus the two capture routes that do run on Firefox."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 15
---


# How to generate a PDF with Playwright and Firefox

To generate a PDF with Playwright on a Firefox engine you cannot use `page.pdf()`: it is
Chromium-only and raises on Firefox. Use one of two routes that do run on Firefox - a
full-page screenshot wrapped into a PDF (a faithful image, no selectable text), or a
`page.content()` capture rendered by a standalone HTML-to-PDF library (real, selectable
text).

Search for this and the top answer is always the same one line: `page.pdf()`. Copy it,
run it against a Firefox engine, and it raises. The advice is not wrong in general, it is
wrong for the engine you are on, and the reason is worth understanding rather than working
around, because it decides which of the real alternatives you should reach for.

This page is why that call cannot work on Firefox, what "cannot" actually means here, and
the two capture routes that do run - one that produces a faithful image of the rendered
page, one that keeps the text selectable.

## The wrong answer this page exists to correct

The canonical Playwright snippet is:

```python
# Chromium only. On a Firefox page this raises at the call site.
pdf_bytes = page.pdf(format="A4")
```

[`page.pdf()`](https://playwright.dev/python/docs/api/class-page#page-pdf) is a Chromium-only
method. Playwright exposes it on the page object for every engine, but the implementation is
backed by Chromium's headless print-to-PDF path, and on a Firefox or WebKit page the call
fails immediately with an error saying PDF generation is supported only in headless Chromium.
There is no option flag, no `channel`, and no stealth setting that changes that, because
nothing about stealth is involved. The method simply has no Firefox implementation behind it.

This engine is a patched Firefox driven by stock Playwright, so the honest answer is: the
call is unavailable, full stop. Pretending otherwise - shelling out to a hidden Chromium
just to satisfy the snippet - would defeat the entire point of running one consistent
engine.

## Why page.pdf() is Chromium-only, and what "unavailable" really means

`page.pdf()` is Chromium-only because PDF export is a compiled rendering pipeline, not a
value the page exposes that a script can rewrite. `navigator.userAgent` is a string, so you
can set it to anything; PDF generation is not a string - it is a pipeline that has to exist
in the browser's compiled code, and Chromium ships one wired to the automation protocol
while Firefox does not expose an equivalent to Playwright.

> A missing feature is missing machine code, not a property you can override. You can lie
> about what the browser *is*; you cannot conjure a *capability* it does not carry.

That is [the same capability-not-value split](chromium-is-not-chrome.md) that makes a
Widevine check decisive: the page asks the build to do something, and reads the real answer
rather than a claimed one. PDF export sits on the capability side of that line. Trying to
"patch it in" from JavaScript is the same category error as trying to patch a
[renderer string onto pixels a rasteriser never drew](renderer-string-vs-render.md).

So the fix is not to force the missing call. It is to pick a route that uses a pipeline
Firefox actually has.

## Alternative 1: full-page screenshot, then wrap it into a PDF

If what you need is a visual record of the page as it rendered - an invoice, a receipt, a
snapshot for an audit trail - the most direct route is a
[full-page screenshot](how-to-take-full-page-screenshots-playwright.md), then a
one-line wrap into a PDF container.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")
    page.screenshot(path="page.png", full_page=True)
```

[`page.screenshot(full_page=True)`](https://playwright.dev/python/docs/api/class-page#page-screenshot)
is standard Playwright and works on every engine, because it reads the rendered surface
rather than driving a print pipeline. It returns the whole
scroll height, not just the viewport. Then wrap the image in a PDF with any small image
library:

```python
import img2pdf

with open("page.pdf", "wb") as f:
    f.write(img2pdf.convert("page.png"))
```

The result is a PDF whose single page is a pixel-accurate picture of the site. The one thing
it is not is a text document: you get an image, so the text is not selectable or searchable.
For a great many "save the page as a PDF" tasks that is exactly what was wanted anyway.

## Alternative 2: the browser's own print pipeline

Firefox does have a print-to-PDF backend - it is what a human gets from the print dialog's
"Save to PDF" destination. You reach it two ways, neither of which is `page.pdf()`.

The first keeps the automation session. Capture the DOM you already have with
[`page.content()`](https://playwright.dev/python/docs/api/class-page#page-content) and hand
it to a standalone HTML-to-PDF renderer, which lays the markup out into real, selectable
text:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="networkidle")
    html = page.content()

# render the captured markup to a text-bearing PDF
from weasyprint import HTML
HTML(string=html, base_url="https://example.com").write_pdf("page.pdf")
```

The second is Firefox's own headless print flag, which drives the browser's print pipeline
end to end for a plain URL:

```bash
firefox --headless --print-to-pdf=page.pdf https://example.com
```

Be honest about the trade with that last one: it launches a fresh browser with no cookies,
no proxy and none of the session state your automated run built up, so it only fits pages a
cold visitor can reach. When the PDF has to reflect a logged-in or proxied view, stay inside
the Playwright session and use the `page.content()` route above.

## When you actually need selectable text

Pick the route by what the PDF is *for*, not by which is fewer lines.

| Route | Output | Selectable text | Keeps cookies / proxy / session | Best for |
|---|---|---|---|---|
| Full-page screenshot wrapped into a PDF | Pixel image of the page | No | Yes | A faithful visual record: an invoice, a receipt, an audit snapshot |
| `page.content()` rendered by an HTML-to-PDF library | Re-laid-out document | Yes | Yes | Text that must be selected, copied or indexed |
| Firefox `--print-to-pdf` | Browser-printed document | Yes | No | A quick archive of a public URL a cold visitor can reach |

In words: for a faithful picture of the page - the layout, the fonts, the images, exactly as
they rendered - take a full-page screenshot and wrap it into a PDF; it cannot be searched, and
that is fine for a visual record. For text you can select, copy or index, capture
`page.content()` and render the markup, trading pixel fidelity (the renderer re-lays-out the
HTML) for real text. For a throwaway archive of a public URL, Firefox's `--print-to-pdf`.

There is no route that is both a byte-for-byte photo of the rendered page and a
text-searchable document, because those are two different outputs. Choosing the wrong one is
the second most common mistake here, after reaching for `page.pdf()` in the first place.

## A measurement: the screenshot route is seed-deterministic

Here is the property that makes the screenshot route more than a fallback. Because every
surface of this engine is derived from one seed, a full-page screenshot taken under a fixed
seed is reproducible: the same `seed=42` renders the same canvas, the same fonts and the
same GPU-drawn pixels, run after run. Diff two PNGs from two runs of the code above and the
only bytes that differ are the ones the site itself changed - the capture adds no
per-run noise of its own.

That was not always true. Full-page capture on this build once returned a frame of colored
static instead of the page, a [now-closed readback bug](playwright-screenshot-returns-noise.md)
that is worth reading for the shape of it. It returns the real page today, and the fact that
it does so deterministically is what lets a screenshot double as a visual regression check,
not just a one-off export.

## Conclusion

`page.pdf()` is Chromium-only, and on a Firefox engine that is not a bug to route around, it
is a capability the build does not carry. The productive move is to stop trying to summon it
and pick the pipeline Firefox has: a full-page screenshot wrapped into a PDF when you want a
faithful image, or a `page.content()` capture rendered to markup when you need selectable
text. Both are standard Playwright plus one small library, and both run on the same
[single, consistent engine](firefox-vs-chromium-antidetect.md) you launched - no hidden
second browser, no lie about a feature that is not there.

## Short answers to the questions that lead here

**Why does page.pdf() fail in Playwright with Firefox?** Because it is a Chromium-only
method backed by Chromium's headless print-to-PDF path. On a Firefox or WebKit page it raises
immediately - the method exists on the object but has no Firefox implementation behind it.

**Can I patch PDF support into Firefox?** Not from JavaScript or from any stealth setting. A
missing rendering pipeline is missing machine code, not a property you can override.

**How do I make a PDF of a page with Firefox then?** Take a full-page screenshot and wrap the
image in a PDF, or capture the HTML with `page.content()` and render it with a standalone
HTML-to-PDF library.

**Will the screenshot PDF have selectable text?** No. It is an image of the page. If you need
searchable text, render the captured markup instead of screenshotting.

**Does the browser have its own print-to-PDF?** Yes - Firefox's headless `--print-to-pdf`
drives it, but that launches a fresh browser with no cookies or proxy, so it only suits public
URLs a cold visitor can reach.

**Is the screenshot the same every run?** Under a fixed seed, yes. The same seed renders the
same pixels, so two captures differ only where the site itself changed.

## Sources

- Playwright's [`page.pdf()`](https://playwright.dev/python/docs/api/class-page#page-pdf),
  [`page.screenshot()`](https://playwright.dev/python/docs/api/class-page#page-screenshot) and
  [`page.content()`](https://playwright.dev/python/docs/api/class-page#page-content) API
  reference, read from Playwright's own documentation rather than a rendered example.
- This project's own capture path and its closed full-frame-noise readback bug, linked above.
- A direct call of `page.pdf()` on a Firefox page in this build, which raises at the call
  site rather than returning bytes.

**See also:** [why Chromium is not Chrome](chromium-is-not-chrome.md) for the capability
split this rests on, [Firefox or Chromium for anti-detect](firefox-vs-chromium-antidetect.md)
for the engine trade-off, [the screenshot readback fix](playwright-screenshot-returns-noise.md)
for why full-page capture returns the real page now, and
[how to take full-page screenshots with Playwright](how-to-take-full-page-screenshots-playwright.md)
for the capture step this PDF route builds on.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The point is not that Firefox
is missing something - it is that a capability you cannot fake is more honest to work with
than a string you can.*
