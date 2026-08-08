---
title: "How to scrape iframe content with Playwright"
description: "How to scrape iframe content with Playwright: use frame_locator for same-origin frames, and why cross-origin iframes fail for a process-isolation reason."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 21
---


# How to scrape iframe content with Playwright

Scraping an iframe splits into two cases that look identical in the page source and
behave nothing alike in code. One is a five-minute job with `frame_locator`. The other
returns `None`, throws a permission error, or times out, and no selector you write will
change that, because the reason is not in the page at all.

This page is the difference between the two, how to tell which one you are looking at,
the code that works for the easy case, and what is actually happening in the hard one.

## The two cases, and why they need different code

An iframe is either same-origin with the page around it or cross-origin. That single
property decides everything about how you reach into it.

- **Same-origin**: the iframe is served from the same scheme, host and port as the
  parent page. Your driver can walk straight into it and read its DOM.
- **Cross-origin**: the iframe comes from a different domain than the page embedding it,
  which is the normal shape of a third-party widget: a consent banner, an embedded
  video, a payment form, a support chat. Reaching into it is where automation breaks.

The two cases behave in opposite ways at every call you make against the frame:

| What you check | Same-origin iframe | Cross-origin iframe |
|---|---|---|
| Origin vs parent | Same scheme, host and port | Different domain |
| `content_frame()` | Returns a real `Frame` | Returns `None` |
| `frame_locator(...).click()` | Works, auto-waits | Times out; `force=True` does nothing |
| `frame.evaluate(...)` | Runs in the frame's context | Throws a cross-origin permission error |
| Root cause | DOM is reachable from the parent process | Process isolation can put the frame in a separate OS process |
| The fix | `frame_locator` / `content_frame` | Engine-level same-process loading, not any selector |

The trap is that both look the same when you inspect the HTML. `<iframe src="...">`
tells you nothing until you compare the two origins. So the first thing any iframe
scraper should do is check which case it is in, before writing a single selector.

## The same-origin case: frame_locator and content_frame

When the iframe shares the parent's origin, Playwright gives you two ways in and both
work exactly as documented. [`frame_locator`](https://playwright.dev/python/docs/api/class-framelocator)
is the modern one and it auto-waits, so it is what you want in almost every case:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/page-with-iframe")

    # frame_locator drills into the iframe by CSS selector, then locates inside it
    frame = page.frame_locator("iframe#content")
    heading = frame.locator("h1").inner_text()
    print(heading)
```

`frame_locator` chains: the selector before it finds the iframe element, the locator
after it runs inside the frame's document. There is no separate "switch to frame" step
to forget, and it waits for both the frame and the target element on its own.

The older element-handle route is still useful when you already hold the iframe element
for another reason:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/page-with-iframe")

    handle = page.query_selector("iframe#content")
    frame = handle.content_frame()   # a Frame object for a same-origin iframe
    if frame is not None:
        rows = frame.query_selector_all("table tr")
        print(len(rows), "rows inside the frame")
```

For a same-origin iframe, [`content_frame()`](https://playwright.dev/python/docs/api/class-elementhandle)
returns a real `Frame`. Keep the `is not None` check anyway, because the exact same call
is what fails in the cross-origin case, and a scraper that assumes success will crash on
the first third-party widget it meets.

## Reading and extracting from inside the frame

Once you hold a same-origin `Frame`, it behaves like a miniature page. Every locator and
extraction method you use on `page` works on `frame`, and
[`frame.evaluate`](https://playwright.dev/python/docs/api/class-frame) runs JavaScript in
the frame's own execution context:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")

    frame = page.frame_locator("iframe#report")

    # wait for content the frame renders after its own fetch, then extract
    frame.locator(".loaded").wait_for(state="visible")
    cells = frame.locator("td.value").all_inner_texts()
    print(cells)

    # or drop into the frame's JS context for structured data in one hop
    handle = page.query_selector("iframe#report")
    data = handle.content_frame().evaluate(
        "() => Array.from(document.querySelectorAll('td.value')).map(td => td.textContent)"
    )
    print(data)
```

The seed matters here for a reason that has nothing to do with the frame and everything
to do with debugging it. Passing `seed=42` gives you the same fingerprint every run, so
when an extraction breaks you can replay the identical session instead of chasing a fresh
random identity each time. That is the whole point of a
[reproducible fingerprint](quickstart.md) when a scrape starts failing intermittently.

## The cross-origin case, and the process boundary behind it

Cross-origin iframes fail because Firefox's site-isolation feature can run the iframe in
a completely separate operating-system process from the page around it, as a security
boundary between origins - not because of anything wrong in your selectors. When you
point the same code at a cross-origin iframe, three things fail together and they look
like three separate bugs:

- `element_handle.content_frame()` returns `None`.
- `frame.evaluate(...)` throws a permission error naming a cross-origin object.
- `frame_locator(...).click()` times out, and `force=True` changes nothing.

They are one bug: Playwright tracks frames from the parent process, so when the iframe's
browsing context lives in a different, isolated process, the parent-side frame tree
registers only a placeholder for it: no URL, no reference to the real document, no
execution context wrapping the frame's global object. Every one of the three failing
operations needs exactly the piece that placeholder does not have, which is why fixing
one never fixes the others. [The full root-cause walk-through is here](cross-origin-iframe-unreachable.md),
including two plausible fixes that were wrong.

You will see people "solve" this by disabling JavaScript. It appears to work because a
static frame has no isolated process to escape into, but it defeats the point of driving
the page at all: the widget you were trying to read no longer runs. A suppressed feature
is not a working one, which is the same false-pass trap that shows up
[all over browser detection](how-to-test-bot-detection.md).

This is the case invisible_playwright handles at the engine level. It loads cross-origin
iframes into the same process as the page around them, so the frame tree reaches every
frame and `content_frame()` returns a real `Frame` where stock Firefox returns `None`.
The measurement that pinned this down was a single controlled comparison on one page:
with isolation active, four frame-tree entries came back with empty URLs and no reachable
content frame; with same-process handling, five entries with full URLs and a working
`content_frame()`. Same page, same selectors, one setting different. That is a real
trade against the isolating strategy's security boundary, and it is the right trade for a
single-purpose automation session and the wrong one for a general-purpose browser.

## How to tell which case you are in

Before you debug selectors, print the frame tree. An entry with an empty URL sitting
where a real iframe should be is the signature of the isolated case:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/page-with-widgets")

    for f in page.frames:
        print(repr(f.url) or "(empty - possibly isolated)", "| name:", f.name)
```

If the frame you want shows a full URL, you are in the same-origin path and the earlier
code applies. If it shows an empty URL and no name, you are looking at the process
boundary, not a mistake in your own code, and the fix is at the engine level rather than
in any selector or timeout you can write. A cross-origin consent frame that behaves this
way is common enough that
[handling consent banners](how-to-handle-cookie-consent-banners-playwright.md) gets its
own page.

One more thing to rule out while you are here: an intermittent
["Execution context was destroyed" error](execution-context-destroyed.md) inside a frame
is usually a navigation race, not the isolation boundary, and it has a different fix.

## Conclusion

Iframe scraping is two problems wearing one tag. Same-origin frames are a solved,
five-minute job: `frame_locator` to drill in, ordinary locators and `frame.evaluate` to
extract, a `content_frame()` `None` check so a third-party widget does not crash you.
Cross-origin frames fail for a reason that lives in process isolation, not in your
selectors, and the honest test is to print the frame tree and read the URL. Fix the
identity with a seed so a broken run replays exactly, and you will spend your time on the
extraction instead of guessing which of the two cases you were ever in.

## Short answers to the questions that lead here

**Why does `content_frame()` return `None` on an iframe that clearly has content?**
The iframe is almost certainly cross-origin and running in a separate, isolated process,
so the driver's frame tree holds only a placeholder for it with no real reference.

**How do I scrape a same-origin iframe?** `page.frame_locator("iframe#id")` to drill in,
then ordinary locators inside it, or `handle.content_frame()` for a `Frame` you can call
`evaluate` on. Both work as documented.

**Why does `frame.evaluate()` throw a cross-origin permission error?** Same root cause as
the `None`: the execution context the driver is trying to reach was never wired up,
because the frame lives in another process.

**Does `force=True` fix a `frame_locator` timeout on a cross-origin iframe?** No. The
element is not reachable through the frame tree the driver is using, and forcing the
click does not change what tree exists.

**Should I just disable JavaScript to make the iframe reachable?** No. It appears to work
only by stopping the widget from running, which defeats the point of automating the page.

**How do I tell same-origin from cross-origin before writing selectors?** Print
`page.frames` and read each `url`. A full URL is the reachable case; an empty URL where an
iframe should be is the isolated one.

## Sources

- This project's own patch history for the cross-origin frame-tree behaviour, the
  controlled same-page comparison that produced the four-empty versus five-full frame
  counts, and the regression suite that locks the fixed behaviour in.
- Playwright's own API reference for the calls involved:
  [`FrameLocator`](https://playwright.dev/python/docs/api/class-framelocator),
  [`ElementHandle.content_frame`](https://playwright.dev/python/docs/api/class-elementhandle),
  and [`Frame.evaluate`](https://playwright.dev/python/docs/api/class-frame), read from
  their documented behaviour rather than paraphrased.

**See also:** [why content_frame() returns None for a cross-origin iframe](cross-origin-iframe-unreachable.md)
for the full root cause, and [handling cookie consent banners](how-to-handle-cookie-consent-banners-playwright.md)
for the most common cross-origin frame you will actually need to click.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The same-origin code here
is ordinary Playwright; the cross-origin case is the one that took real engine work.*
