---
title: "How to scrape numbers rendered as images with Playwright"
description: "Read prices and figures a site renders as images or sprites with Playwright: recover the value from the markup the page already carries before reaching for pixels, and know when to stop."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 168
---


# How to scrape numbers rendered as images with Playwright

When a site renders a number as an image, look for the value in the **markup around the
image** before trying to read pixels. Sites that do this almost always leave the number
somewhere accessible, because a figure that no screen reader can announce is a figure that
fails accessibility law in most of their markets.

That is the practical order of attack, and it resolves the majority of cases in a few
lines. Optical recognition is the last resort, not the first, and this page is partly about
recognising when the right answer is to stop.

## Look in the accessible layer first

```python
from invisible_playwright import InvisiblePlaywright

PROBE = """
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const img = el.tagName === 'IMG' ? el : el.querySelector('img');
  return {
    alt: img ? img.getAttribute('alt') : null,
    title: el.getAttribute('title'),
    ariaLabel: el.getAttribute('aria-label'),
    dataset: Object.assign({}, el.dataset),
    srOnly: el.querySelector('.sr-only, .visually-hidden')?.textContent.trim() || null,
    describedBy: (() => {
      const id = el.getAttribute('aria-describedby');
      return id ? document.getElementById(id)?.textContent.trim() : null;
    })(),
  };
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/1234")
    found = page.evaluate(PROBE, ".price-image")
```

In practice one of `alt`, `aria-label`, a `data-` attribute or a visually hidden span
carries the digits. Check all of them rather than the first, because which one is used
varies by framework and sometimes within a single page.

## Then check whether it is a sprite, not an image of a number

A common technique renders each digit as a background-position offset into one sprite
image. The number is then recoverable arithmetically, with no recognition involved:

```python
OFFSETS = """
() => Array.from(document.querySelectorAll('.price .digit')).map(d => {
  const s = getComputedStyle(d);
  return { x: s.backgroundPositionX, y: s.backgroundPositionY, w: s.width };
})
"""
    digits = page.evaluate(OFFSETS)
```

Each distinct offset maps to one digit. Calibrate once on a page where you know the value
from elsewhere, build the offset-to-digit map, and every other page on that site decodes
exactly. This is more reliable than recognition because it is a lookup, and it breaks
loudly rather than silently when the site changes the sprite.

Be aware that the mapping is often deliberately shuffled per session or per page load, and
in that case it is a defence rather than an artefact. That distinction matters for what you
do next.

## Custom fonts with remapped glyphs

A third variant ships a font where the glyph for "7" draws a 3. The DOM text is then
misleading rather than absent: you read a number and it is wrong, with no error anywhere.

The tell is a page-specific `@font-face` with an obfuscated family name applied to exactly
the elements carrying figures. If you find one, the DOM text cannot be trusted for those
elements, and you either decode the font's character map or you stop. Reading the text and
hoping is the one option that produces a database full of confidently wrong numbers.

## If you do render, render the element and nothing else

Where the value genuinely exists only as pixels and you have a legitimate reason to read it,
capture the element rather than the page:

```python
    element = page.query_selector(".price-image")
    element.screenshot(path="price.png")
```

An element screenshot is smaller, deterministic and much easier to feed to recognition than
a full page. The general capture mechanics, including the traps around lazy content and
scroll position, are in
[taking full page screenshots](how-to-take-full-page-screenshots-playwright.md).

⛔ **Do not detect change by hashing the PNG. We measured this and it nearly produced a
false conclusion.** Building a canvas comparison bench, we hashed the PNG bytes of each
capture and got **three different md5 values across three runs of the same page**, on
unmodified retail Firefox. The obvious reading was that Firefox randomises canvas output
by itself.

It was wrong. The three PNG files had **identical byte length** and differing content: the
variation was encoder metadata, not pixels. Decoding to a pixel array and comparing that
instead, all three runs were **stable**, and so were the other two arms of the bench.

The practical rule that falls out of it, for any pipeline that watches a rendered number
for change:

```python
from PIL import Image
import hashlib, io

def pixel_digest(png_bytes):
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    return hashlib.sha256(img.tobytes()).hexdigest()      # pixels, not the file
```

A file hash answers "are these two files identical", which is not the question. The
question is whether the image changed, and only the decoded pixels answer that. The same
trap appears whenever a screenshot is used as evidence rather than as a picture, which is
also why
[a screenshot that comes back as noise](playwright-screenshot-returns-noise.md) is worth
recognising as its own failure rather than as a changed page.

Then treat the recognised value as a measurement with an error rate, not as a fact. Store
the image path or hash next to the number so a suspicious row can be checked by a human,
and set a confidence threshold below which you record a null rather than a guess.

## When to stop, and why that is a real answer

Sprite shuffling, per-session glyph remapping and image-only figures with no accessible
label are all deliberate anti-extraction measures. When you meet the deliberate version,
the useful question is whether you have a relationship with the site that entitles you to
the data, such as a contract, an API, or a published export.

There is a related honesty point that runs through this whole corpus. A browser that
behaves like a real browser gets past defences aimed at things that are not browsers, and
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md) covers where
that line sits. It does not, and should not, extend to defeating a measure whose only
purpose is to say no to bulk extraction of that specific field.

The number rendered as an image is usually a price. Prices are also usually available in a
feed, a partner API, or a structured markup block the same page emits for search engines,
which is worth checking before any of the above:
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
takes about a minute to rule in or out.

## A complete probe that tries the cheap routes in order

```python
import json, re, time
from invisible_playwright import InvisiblePlaywright

def recover_number(page, selector):
    """Return (value, method) using the cheapest route that works."""
    ld = page.eval_on_selector_all(
        "script[type='application/ld+json']",
        "els => els.map(e => e.textContent)",
    )
    for blob in ld:
        try:
            data = json.loads(blob)
        except Exception:
            continue
        offers = (data.get("offers") if isinstance(data, dict) else None) or {}
        if isinstance(offers, dict) and offers.get("price"):
            return offers["price"], "json-ld"

    probe = page.evaluate(PROBE, selector)
    if probe:
        for field in ("ariaLabel", "alt", "title", "srOnly", "describedBy"):
            text = probe.get(field)
            if text and re.search(r"\d", text):
                return text.strip(), f"accessible:{field}"
        for k, v in (probe.get("dataset") or {}).items():
            if re.fullmatch(r"[\d.,]+", str(v)):
                return v, f"dataset:{k}"

    digits = page.evaluate(OFFSETS)
    if digits and all(d.get("x") for d in digits):
        return decode_sprite(digits), "sprite"

    return None, "unresolved"

with InvisiblePlaywright(seed=42) as browser, open("prices.jsonl", "a", encoding="utf-8") as out:
    page = browser.new_page()
    for url in product_urls:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector(".price-image, .price", timeout=20000)
        value, method = recover_number(page, ".price-image")
        out.write(json.dumps({"url": url, "value": value, "method": method,
                              "observed_at": time.time()}, ensure_ascii=False) + "\n")
        out.flush()
        page.wait_for_timeout(2000)
```

Recording `method` on every row is the part that makes this maintainable. When a site
changes its rendering, the method column shows you exactly which pages moved from
`accessible:alt` to `unresolved`, which is a far better signal than a column of nulls
appearing with no explanation.

## Why this technique exists, and what that implies

Rendering a number as an image is expensive: it costs the site accessibility, translation,
selectable text and search visibility for that field. Sites accept those costs for two
different reasons, and the reason determines what you should do next.

**Legacy or laziness.** A price baked into a promotional banner, a figure in a chart image,
a table exported as a picture. There is no intent to stop anyone, the accessible label is
usually present, and reading it is ordinary extraction.

**Deliberate anti-extraction.** Per-session sprite shuffling, glyph-remapped fonts,
canvas-drawn digits with no accessible layer. These cost the site real money in
accessibility compliance, which is how you can tell the choice was intentional.

The second case is the one worth naming clearly. This corpus is about making a browser
behave like a browser so that defences aimed at non-browsers stop misfiring on you. That is
a different thing from defeating a control whose only purpose is to prevent bulk
extraction of one specific field, and the honest response there is to ask whether you have
a relationship with the site that entitles you to the data: a contract, an API, a partner
feed. The reasoning around that line runs through
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md).

## If you do recognise, treat the output as a measurement

Recognition output is not a fact, and the schema should say so:

| field | note |
|---|---|
| `value` | null when confidence is below your threshold, never a guess |
| `method` | `json-ld`, `accessible:alt`, `sprite`, `ocr`, `unresolved` |
| `confidence` | only present for `ocr` |
| `evidence` | path or hash of the element screenshot, for a human to check |

A null with an image to look at is a workable row. A confidently wrong price is not, and it
is the outcome that a pipeline without a threshold produces by default. Set the threshold
high enough that the nulls annoy you, then fix the extraction rather than lowering the bar,
because the alternative is a dataset whose errors are invisible and whose consumers have no
way to find them.
