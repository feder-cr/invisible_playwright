---
title: "How to scrape video listings and metadata with Playwright"
description: "Scrape video listings and metadata with Playwright: pull exact duration, views and upload date from JSON-LD, fall back to overlays, page the grid."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 48
---


# How to scrape video listings and metadata with Playwright

To scrape video listings and metadata with Playwright, read each card's structured
data first: the `VideoObject` in the page's JSON-LD carries the exact upload date,
duration and view count in machine form, so use the rendered DOM only for fields it
omits, and page the grid by watching the card count stabilise rather than seeking an end
that does not exist. Leave the player embed alone, because pulling the stream is a
separate and much heavier problem than reading the card.

A video grid looks like a simple list and behaves like anything but. The thumbnails
lazy-load into an infinite wall that never has an end you can seek to, and the fields you
actually want are scattered across four different places: the duration is painted on top
of the thumbnail, the view count is abbreviated in text, the real numbers sit in JSON-LD,
and the player itself is a separate embed that reports nothing until something interacts
with it.

This page is about reading the listing metadata cleanly, which is the part that is
tractable, and being honest about where it stops. It does not touch the player, because
pulling the stream is a different and much heavier problem than reading the card.

## Where each field actually lives

Before writing a selector, look at what a single card is made of. On a typical grid the
same logical record is spread across surfaces that update at different times:

- **Duration** is a text overlay drawn on the thumbnail, usually the last DOM node to
  arrive because it waits on the image. It is formatted for humans (`12:04`), not for you.
- **View count** is abbreviated in the visible text (`1.2M views`), so the DOM gives you a
  rounded, locale-formatted string rather than an integer.
- **Upload date, exact view count, title and uploader** are almost always present in a
  `application/ld+json` block as a `VideoObject`, in machine form, before any of the
  visible text has finished rendering.
- **Everything about the media itself** lives in the player embed, which is a separate
  frame that stays inert until it is played.

The practical consequence: read the structured data first, use the visible DOM only for
what structured data omits, and leave the player alone.

## Read the listing from JSON-LD, not the rendered text

The single highest-value move is to stop parsing human-formatted strings and read the
`VideoObject` blocks the page already ships for search engines. They carry ISO-8601
durations and dates and unrounded counts, and they are stable across redesigns in a way
that CSS class names are not.

```python
import json
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/channel/videos", wait_until="domcontentloaded")

    # Pull every JSON-LD block the page emitted and keep the VideoObjects.
    blocks = page.eval_on_selector_all(
        'script[type="application/ld+json"]',
        "nodes => nodes.map(n => n.textContent)",
    )

    videos = []
    for raw in blocks:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # A block is sometimes a single object, sometimes a list, sometimes a graph.
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "VideoObject":
                videos.append({
                    "title": item.get("name"),
                    "upload_date": item.get("uploadDate"),      # ISO-8601, exact
                    "duration": item.get("duration"),           # e.g. PT12M4S
                    "views": (item.get("interactionStatistic") or {}).get(
                        "userInteractionCount"
                    ),
                })

    print(json.dumps(videos, indent=2))
```

The `duration` field comes back as an ISO-8601 period (`PT12M4S`), which you can parse
with `isodate` or a small regex, and `uploadDate` as a real date rather than "3 weeks
ago". This is the same reason [capturing the page's own XHR responses](how-to-capture-xhr-api-responses-playwright.md)
often beats scraping the DOM: the machine-readable copy is already on the wire. The
general pattern for pulling these blocks, including the single-object, list and `@graph`
shapes the loop above handles, is written up in [extracting JSON-LD structured
data](how-to-extract-json-ld-structured-data-playwright.md).

When a field is genuinely absent from the structured data, fall back to the overlay. The
duration overlay is a good example, because some grids omit it from JSON-LD on the listing
and only include it on the watch page:

```python
cards = page.query_selector_all("[data-video-id]")
for card in cards:
    overlay = card.query_selector(".thumbnail .duration-badge")
    duration_text = overlay.inner_text().strip() if overlay else None
    # "12:04" -> seconds, done in your own code, not trusted from the page
```

Read the structured field when it exists and the overlay only when it does not. Do not
average the two or prefer the prettier one.

## Page the lazy-loading grid

The grid has no last page. New rows are appended as a sentinel near the bottom scrolls
into view, so the job is to scroll, wait for the count to grow, and stop when it stops
growing rather than when you reach an end that does not exist.

```python
def load_all_cards(page, selector="[data-video-id]", quiet_rounds=3, pause_ms=1200):
    seen = 0
    stable = 0
    while stable < quiet_rounds:
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(pause_ms)
        count = len(page.query_selector_all(selector))
        if count > seen:
            seen = count
            stable = 0
        else:
            stable += 1
    return seen

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/channel/videos", wait_until="domcontentloaded")
    total = load_all_cards(page)
    print("loaded", total, "cards")
```

Two things make this reliable instead of flaky. First, stop on a stable count over several
rounds, not on a single round that added nothing, because one slow network round can look
like the end and then keep loading. Second, keep the pause realistic. A grid scrolled at
machine speed with a fixed interval between wheel events is a behavioural tell on its own,
independent of any fingerprint. The general shape of paging one of these walls, including
when to prefer the network feed over scrolling, is covered in
[scraping an infinite-scroll page](how-to-scrape-infinite-scroll-playwright.md).

Because the identity here is seeded (`seed=42`), a run that loads 480 cards and then breaks
on card 481 replays as the same 481 cards, so you are debugging the site rather than a new
random machine each time.

## Why the media stack has to match a real browser

Here is the part specific to video platforms, and the reason a spoofed navigator is not
enough on them.

Video sites fingerprint the media stack. They call
[`canPlayType`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/canPlayType)
and
[`MediaSource.isTypeSupported`](https://developer.mozilla.org/en-US/docs/Web/API/MediaSource/isTypeSupported_static)
across a spread of codecs and containers, read what the
browser claims to support, and check that the answers are internally consistent and
consistent with the platform the browser says it is. A browser that reports a Windows
Firefox user agent but answers the codec probes like a headless Chromium build, or like a
media stack with no hardware behind it, is caught by the mismatch even when the canvas
hash is clean and `navigator.webdriver` is gone. The codec answers are a fingerprint
surface in their own right, and they are one that page-level patching does not reach,
because they come from the engine's actual media support rather than from a JavaScript
property you can overwrite.

This is where a genuinely patched engine earns its place over a property spoofer. Because
this browser is a real Firefox built at the C++ level, it answers the codec probes the way
the Firefox it claims to be answers them, and because every surface is derived from one
seed those answers are stable across a session and reproducible across runs. There is
nothing to keep in sync between a fake navigator and a real media stack, because there is
no fake navigator. The mechanism, and how these probes are constructed, is written up in
[codec fingerprinting](codec-fingerprinting.md).

You can see the shape of the surface without a detector, just by asking the engine what it
supports:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="domcontentloaded")
    support = page.evaluate("""() => {
        const probes = [
            'video/mp4; codecs="avc1.42E01E"',
            'video/webm; codecs="vp9"',
            'video/mp4; codecs="av01.0.05M.08"',
        ];
        return probes.map(t => ({
            type: t,
            canPlay: document.createElement('video').canPlayType(t),
            mse: window.MediaSource ? MediaSource.isTypeSupported(t) : null,
        }));
    }""")
    for row in support:
        print(row)
```

Run that against a stock Firefox on the same machine and diff it field by field. The
answers should match, because the engine is the same engine. When they do, the codec
surface is not the thing that gives you away.

## The honest limit: metadata is not the stream

This how-to stops at the listing metadata on purpose.

Reading a `VideoObject` and a thumbnail overlay is a scraping problem: the data is in the
DOM or in a JSON blob and you extract it. Actually pulling the media stream is a different
and much larger problem. It means driving the player embed, dealing with segmented
delivery and its manifests, session tokens that expire, and often DRM, none of which is a
selector-and-parse job and some of which you may have no right to do at all. A truthful
codec fingerprint is necessary to get that far, but it is nowhere near sufficient, and it
is a separate undertaking from the one on this page.

So the boundary this page draws is deliberate: everything up to and including the
listing metadata is here; the stream is not. If your goal is a catalogue of what exists,
with durations, dates and counts, you are done at the end of the previous section. If your
goal is the bytes of the video, this page has only cleared the first and smallest hurdle.

## Conclusion

Scraping a video grid well is mostly about reading the right copy of each field. Take the
exact numbers from JSON-LD, fall back to the thumbnail overlay only for what structured
data omits, and never parse the human-formatted string when a machine-formatted one is
sitting in the same document. Page the wall by watching the count stabilise rather than
seeking an end that is not there. And treat the media stack as a fingerprint surface,
because on video platforms it is one: a real engine's honest codec answers are the
difference between a clean listing scrape and a session that is flagged before it reads a
single card. Then stop at the metadata, because the stream is a different problem wearing
the same URL.

## Short answers to the questions that lead here

**Where do I get the exact view count and upload date?** From the `VideoObject` in the
page's `application/ld+json` block, not the visible text. The text is rounded and
locale-formatted; the structured data is exact and ISO-8601.

**How do I scrape a video grid that never ends?** Scroll, wait for the card count to grow,
and stop when it stays the same over several rounds rather than looking for a last page,
because there is not one.

**Why do I get blocked on a video site even with a clean fingerprint?** Because video
platforms probe the codec and MediaSource stack, and a browser whose media answers do not
match the platform it claims is caught even with a clean canvas. See
[codec fingerprinting](codec-fingerprinting.md).

**Can I read the duration without playing the video?** Yes. It is a text overlay on the
thumbnail, or an ISO-8601 `duration` in the JSON-LD, and neither requires touching the
player.

**Can this download the video too?** No. This reads the listing metadata. Pulling the
stream means driving the player, segmented delivery and often DRM, which is a separate and
much heavier problem.

**Why seed the browser for a scrape?** So a run that breaks on card 481 replays as the
same 481 cards. A fixed seed makes a failure reproducible instead of a new random machine
every time.

## Sources

- The page's own `application/ld+json` `VideoObject`, read from the DOM rather than from
  rendered text.
- The engine's
  [`HTMLMediaElement.canPlayType`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/canPlayType)
  and
  [`MediaSource.isTypeSupported`](https://developer.mozilla.org/en-US/docs/Web/API/MediaSource/isTypeSupported_static)
  answers, compared field by field against a stock Firefox on the same machine.
- This project's fingerprint gates, which treat a codec surface that disagrees with the
  claimed platform as a failure rather than a pass.

**See also:** [scraping an infinite-scroll page](how-to-scrape-infinite-scroll-playwright.md),
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md),
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md),
and [codec fingerprinting](codec-fingerprinting.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The listing scrapes cleanly;
the stream is a different problem, and this page says so on purpose.*
