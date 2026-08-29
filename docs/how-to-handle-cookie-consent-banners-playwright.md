---
title: "How to handle cookie consent banners in Playwright"
description: "The cookie consent accept button times out in Playwright because the banner is a cross-origin iframe in a separate process. Use frame_locator, not force=True."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 12
---


# How to handle cookie consent banners in Playwright

A cookie consent banner in Playwright usually will not click because it is a cross-origin
iframe running in a separate browser process, so your selector searches the wrong frame and
`force=True` cannot rescue it. The fix is to address the iframe first with [`frame_locator`](https://playwright.dev/python/docs/api/class-frame#frame-frame-locator),
then find the accept button inside it by its role and label.

Almost every guide answers this the same way: find the accept button, click it, move
on. Something like `page.click("#accept-cookies")`, and when that times out,
`page.click("#accept-cookies", force=True)`. On a lot of consent banners that second
line does nothing either, and the reason has nothing to do with the selector being
wrong or the button being covered.

The banner is usually served from a different domain than the page around it, which
makes it a cross-origin iframe, and a cross-origin iframe is not reachable the way a
normal element is. This page is why the click fails, why `force=True` cannot rescue
it, and the frame-aware pattern that actually works.

## Why the accept button will not click

A consent banner is a third-party widget. The management platform that renders it is
served from its own domain and dropped into the page inside an `<iframe>`, exactly the
same shape as a payment form, a support chat, or an embedded video. The button you
want lives inside that iframe, not on the top-level document.

That single fact changes how you have to reach it. `page.click("#accept")` searches
the main frame. The button is not in the main frame, so the selector never resolves
and the call sits there until it times out.

The instinct after a timeout is to force the click, but `force=True` only skips
Playwright's actionability checks (is the element visible, stable, not covered). It
does not change which frame the element is in. If the element cannot be found, there
is nothing to force.

So before anything else: a consent button that will not click is a frame problem, not
a click problem, and the fix is to tell Playwright which frame to look in.

## The three symptoms are one cause

Three different Playwright errors on a consent iframe all trace back to a single root cause:
the iframe runs in a separate process, so the driver holds only a placeholder for it with no
document reference and no execution context. If you have already tried to reach into the
iframe by hand, you may have collected a confusing set of failures that look unrelated:

- `element_handle.content_frame()` returns `None` on an iframe that clearly has
  content.
- `frame.evaluate(...)` throws a permission error that names a cross-origin object.
- `frame_locator(...).click()` times out, and `force=True` changes nothing.

These are not three bugs. They are one cause with three faces. Firefox's site
isolation can place a cross-origin iframe in a completely separate operating-system
process from the page that embeds it, as a security boundary between the two origins.

The automation driver builds its map of the page from the parent process, so when the
iframe lives somewhere else, the driver registers a placeholder for it: no URL, no
document reference, no execution context wrapping the iframe's globals. Every one of
those three operations needs precisely the piece the placeholder is missing, which is
why fixing one never fixed the others.

We measured the difference directly on the same URL. With the isolating strategy
active, the frame tree held four entries with empty URLs and no reachable content
frame. With every origin kept in one process, the same page produced five entries with
full URLs and a working `content_frame()`. The full root-cause writeup is in
[why content_frame() returns None for a cross-origin iframe](cross-origin-iframe-unreachable.md);
the short version is that whether you can reach the consent iframe at all is decided by
the browser's process model, not by your selector.

invisible_playwright ships the non-isolating strategy on purpose, so a cross-origin
consent iframe loads into the same process as the page and the driver's frame tree
reaches it. That is a deliberate trade for a single-purpose automation session, and it
is what makes the `frame_locator` pattern below resolve instead of time out.

## Target the frame, do not force the click

The correct tool is `frame_locator`. It addresses the iframe first, then finds the
element inside it, and it resolves lazily so you are not fighting a race between the
banner appearing and your selector running.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # Address the iframe by its src URL, then the button inside it.
    consent = page.frame_locator("iframe[src*='consent']")
    consent.get_by_role("button", name="Accept all").click()

    # The main page is now usable.
    page.goto("https://example.com/catalog")
```

Two things are doing the work here. `frame_locator("iframe[src*='consent']")` targets
the iframe by a stable part of its source URL rather than a generated id that changes
between loads. [`get_by_role("button", name="Accept all")`](https://playwright.dev/python/docs/api/class-locator#locator-get-by-role) then finds the button by its
accessible role and label, which survives a restyle better than a hashed class name.

If you do not know the iframe's URL yet, enumerate the frames once and read them off:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    for f in page.frames:
        print(f.url or "(empty - possibly isolated)", f.name)
```

An entry with a real, different-domain URL is your consent iframe, and its URL is the
substring to hand to `frame_locator`. An entry with an empty URL sitting where an
iframe should be is the isolated-process shape described above, and on a browser that
keeps origins in one process you should not see it.

## A worked example: accept, wait, then read the page

Clicking accept is only half the job. The content you came for often does not render,
or is hidden behind an overlay, until consent is recorded. So wait for a real
post-consent signal before you scrape, rather than sleeping a fixed number of seconds
and hoping.

```python
from invisible_playwright import InvisiblePlaywright

def scrape_after_consent(url, item_selector):
    with InvisiblePlaywright(seed=42) as browser:
        page = browser.new_page()
        page.goto(url)

        # Accept only if the banner is actually present this run.
        consent = page.frame_locator("iframe[src*='consent']")
        accept = consent.get_by_role("button", name="Accept all")
        if accept.count() > 0:
            accept.click()

        # Wait for the content, not for a timer.
        page.wait_for_selector(item_selector)
        return [el.inner_text() for el in page.query_selector_all(item_selector)]

for text in scrape_after_consent("https://example.com/list", ".item-title"):
    print(text)
```

The `accept.count() > 0` guard matters because the banner is conditional: it shows on a
fresh session and stays hidden once consent is stored. Code that unconditionally waits
for a banner that is not there this run will time out on the visits that were supposed
to be the easy ones. That storage behaviour is the next section.

## What a stored consent carries into the next run

Once you accept, the decision is written down, and where it is written decides how long
it lives. With a bare `storage_state` file or a fresh context each run, the consent
lives only as long as that session, so the banner reappears every time and the guard
above handles it cleanly.

With a persistent profile, the accepted consent persists across runs, which is often
what you want: no banner on visit two, one fewer interaction to script. The catch is
that a profile stores more than the consent flag: it stores permissions too, and a
permission accepted once and then reused is easy to forget you granted.

A stored camera or microphone grant, for instance, quietly switches off part of
Firefox's WebRTC address protection for that origin on every future run against the
profile, until you remove it. So a consent flow that also asked for a device permission
can carry a change you did not intend into every later session.

The rule that keeps this straight: pair one profile directory with one seed,
permanently, and audit a reused profile's stored permissions rather than trusting them.
[Playwright persistent profile: what it fixes and breaks](persistent-profiles.md) walks
through exactly what a profile carries and the one-minute audit for the permission trap.

## Conclusion

A cookie consent banner is not a stubborn button, it is content in a cross-origin
iframe, and the whole difficulty comes from that. `force=True` cannot help because the
problem is which frame the element is in, not whether the element is clickable. Target
the iframe with `frame_locator`, find the button by role and label, wait for a real
post-consent signal before scraping, and decide deliberately whether the acceptance
should persist. On invisible_playwright the cross-origin iframe is reachable by design,
so the pattern above resolves instead of timing out on a placeholder.

## Short answers to the questions that lead here

**Why does clicking the accept button time out?** Because the banner is inside a
cross-origin iframe and your selector is searching the main frame. Address the iframe
first with `frame_locator`, then the button.

**Does force=True help with a consent banner that will not click?** No. `force=True`
skips actionability checks; it does not change which frame the element lives in. If the
button cannot be found, there is nothing to force.

**Why does content_frame() return None on the consent iframe?** The iframe's browsing
context can run in a separate, isolated process, so the driver holds only a placeholder
for it with no real reference. It is the same cause as the click timing out.

**How do I find the right frame to target?** Enumerate `page.frames` once and read the
URLs. The consent iframe is the entry served from a different domain; use a stable part
of its URL in `frame_locator`.

**Should I wait a few seconds after accepting?** Wait for a signal, not a timer. Use
`wait_for_selector` on the content that only appears after consent, so the wait is as
long as it needs to be and no longer.

**Will the banner come back on the next run?** With a `storage_state` file or a fresh
context, yes, so guard the accept with a presence check. With a persistent profile it
stays accepted, along with any device permission the flow also stored.

## Sources

- This project's own patch history for the cross-origin iframe root cause, the frame
  tree measurement (four empty entries versus five populated), and the process-model
  fix that makes the iframe reachable.
- Playwright documentation, [Frame.frame_locator](https://playwright.dev/python/docs/api/class-frame#frame-frame-locator)
  and [Locator.get_by_role](https://playwright.dev/python/docs/api/class-locator#locator-get-by-role),
  retrieved 2026-08-28.
- Playwright documentation, [Page class reference](https://playwright.dev/python/docs/api/class-page),
  which documents `frames` and `wait_for_selector`, used unchanged here because the
  driven object is a real Playwright `Browser`, retrieved 2026-08-28.
- This project's notes on persistent profiles and the stored-permission trap, linked
  above.

**See also:** [why content_frame() returns None for a cross-origin iframe](cross-origin-iframe-unreachable.md)
for the full root cause, [how to scrape iframe content with Playwright](how-to-scrape-iframe-content-playwright.md)
for the same frame-targeting pattern applied to content rather than a button,
[Playwright persistent profile: what it fixes and breaks](persistent-profiles.md)
for what a stored consent carries forward, and [how to scrape data behind a login with Playwright](how-to-scrape-behind-login-playwright.md)
for the session-reuse pattern this one sits next to.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The consent banner is the
canonical cross-origin iframe, which is why "the button will not click" turned out to be
a process-model question.*
