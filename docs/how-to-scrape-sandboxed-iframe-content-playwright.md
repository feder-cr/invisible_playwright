---
title: "How to Scrape a Sandboxed Iframe with Playwright"
description: "An iframe with a sandbox attribute can block Playwright's own injected scripts with 'Blocked script execution', even when the frame is same-origin. What the sandbox tokens actually restrict, a real GitHub issue on this, and how to detect it before debugging selectors."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 148
---


# How to Scrape a Sandboxed Iframe with Playwright

Not every broken iframe is a cross-origin problem. [The usual iframe failure this site
covers](cross-origin-iframe-unreachable.md) is about process isolation: a frame served
from a different domain than its parent, living in a separate process, invisible to
Playwright's frame tree. This page is about a narrower failure that trips people up
precisely because it does not need any of that. It can happen on a frame that shares the
exact same origin as the page around it, because the restriction is not about where the
frame came from, it is a permission the page author wrote directly onto the `<iframe>` tag:
the HTML `sandbox` attribute.

## What the sandbox attribute actually does

`sandbox` is a standard HTML attribute, and an empty `sandbox=""` is the strictest form:
per MDN, it applies "all restrictions," treating the framed document "as a special origin
that always fails the same-origin policy" and blocking scripts, forms, popups and storage
by default. Everything after that is opt-back-in, one space-separated token at a time. The
tokens that matter most for scraping:

| Token | What it restores |
|---|---|
| `allow-scripts` | Lets scripts run inside the frame at all |
| `allow-same-origin` | Treats the frame as its real origin instead of a forced-opaque one, restoring storage and same-origin API access |
| `allow-forms` | Lets form submission actually submit, instead of silently failing |
| `allow-popups` | Lets the frame open `window.open()` or `target="_blank"` |
| `allow-modals` | Lets `alert()`, `confirm()`, `prompt()` and `<dialog>` work |
| `allow-downloads` | Lets a download-triggering link inside the frame actually download |
| `allow-top-navigation` | Lets the frame navigate the whole page, not just itself |
| `allow-popups-to-escape-sandbox` | Lets a popup opened from the frame escape the sandbox's own restrictions |

A frame with `sandbox="allow-scripts allow-same-origin"` runs scripts and behaves like a
normal same-origin document. A frame with bare `sandbox=""`, or `sandbox="allow-forms"` and
nothing else, does not run scripts at all, and no selector, wait, or retry changes that,
because nothing is broken. The page author asked for exactly this.

One detail worth knowing, even though it is usually not the scraper's problem: MDN's own
security note says combining `allow-scripts` and `allow-same-origin` on a frame is
"strongly discouraged" when the two documents share an origin, because scripted
same-origin access lets the framed document remove its own `sandbox` attribute, "making it
no more secure than not using the sandbox attribute at all." That warning is aimed at site
authors defeating their own sandbox, not at Playwright, but it explains why a same-origin
sandboxed frame in the wild more often carries only one of the two tokens than both.

## Why this bites automation specifically, even same-origin

A same-origin, unsandboxed iframe is a five-minute job: [`frame_locator`, `content_frame()`,
and `frame.evaluate()` all just work](how-to-scrape-iframe-content-playwright.md), because
nothing about the origin stops a script from running there. A `sandbox` attribute without
`allow-scripts` removes that assumption without touching the origin. The frame is still
reachable in Playwright's frame tree, `content_frame()` still returns a real `Frame`, and
`frame_locator` still finds elements, because none of that requires scripts to run. What
breaks is anything that depends on script execution inside the frame: a `frame.evaluate()`
call, or an automation layer's own injected instrumentation, hits the browser's sandbox
enforcement and gets refused, the same as any other unwanted script.

The browser's console message names the cause directly, and it is not Playwright-specific,
it is the standard sandbox-violation message any browser produces:

```
Blocked script execution in 'about:blank' because the document's frame is sandboxed
and the 'allow-scripts' permission is not set.
```

It fires the same way whether the script came from the page's own code, from
`page.evaluate()` targeting the wrong frame, or from an automation tool's instrumentation
reaching into every frame on the page.

## The real case: Playwright issue #33343

[`microsoft/playwright#33343`](https://github.com/microsoft/playwright/issues/33343), filed
October 29, 2024, is a documented, real instance of exactly this message, worth reading in
full rather than assuming what a closed issue title implies: the actual resolution is more
specific, and more useful, than "fixed" or "won't fix."

The reporter saw the "Blocked script execution... allow-scripts permission is not set"
error on a page where a third-party plugin (Google's IMA ad SDK) injected an OMID
viewability iframe with an empty `sandbox` attribute, something the reporter could not
reconfigure. A Playwright maintainer reproduced it and confirmed the cause directly: "the
error message is indeed caused by Playwright trying to inject a script into the sandboxed
iframe," the same mechanism described above.

What makes the issue worth reading past the headline: the maintainer's first-pass read was
that the console error was cosmetic ("I think it's safe to ignore the error, as long as
Playwright's still functioning fine"), and a later commenter confirmed it only surfaces
with the inspector or `page.pause()` open. The reporter's actual production problem was
something else: video playback inside the ad SDK's frame was blocked by the SDK's own
automation detection, unrelated to the sandbox warning. The maintainer's conclusion: "If
software actively prevents automation (which makes sense for Adtech!), there's nothing
Playwright can do about it."

**Current status, checked this session:** closed, GitHub's own `state_reason` recorded as
`completed`, closed October 31, 2024, two days after filing. No code fix or pull request is
linked. The resolution is a diagnosis, not a patch: the sandbox console error is a benign
side effect of Playwright's own instrumentation hitting a sandboxed frame, and the
reporter's real blocker was a deliberate anti-automation measure in third-party ad
software, a problem no browser engine change makes go away.

The lesson generalizes past this one ad SDK: a "Blocked script execution" message next to a
sandboxed iframe is not automatically why a scrape is failing. It can be exactly that, a
real `frame.evaluate()` call refused for lack of `allow-scripts`, or noise from the
automation layer's own housekeeping sitting beside an unrelated failure. Confirming which
one, before spending an afternoon on it, is the point of the next section.

## Checking before you debug selectors

Read the attribute directly, from the parent page, before assuming a `frame_locator` call
or a `.evaluate()` failure is a selector problem:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/page-with-sandboxed-widget")

    for handle in page.query_selector_all("iframe"):
        tokens = page.evaluate("el => Array.from(el.sandbox)", handle)
        src = handle.get_attribute("src") or "(no src)"
        print(src, "sandbox tokens:", tokens or "(sandbox present, empty: all restrictions)")
```

[`HTMLIFrameElement.sandbox`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLIFrameElement/sandbox)
is a live `DOMTokenList` reflecting the attribute, readable from the parent page's own
script without ever entering the frame, which is what makes this check safe to run against
a frame you cannot script. An empty list from a *present* `sandbox` attribute means every
restriction applies; `allow-scripts` missing means any `frame.evaluate()` against that
frame is working against a restriction the page author wrote on purpose, not a bug to
chase.

If `allow-scripts` is present but the frame still will not cooperate, check for
`allow-same-origin` next: without it, the frame authenticates as a forced-opaque origin, so
storage- or origin-dependent behavior can fail even though scripts run fine. If the frame
is also cross-origin, [the process-isolation failure mode](cross-origin-iframe-unreachable.md)
applies on top of this, not instead of it.

A same-origin iframe missing `allow-scripts` cannot be scripted the way a normal page can,
by design, and that is not a bug to fix in Playwright, this project's Firefox build, or
your own code. `content_frame()` and DOM reads that skip `evaluate()` still work; beyond
that, the honest options are reading what the frame's markup already exposes, or getting
the data another way, such as the network request the frame makes to fetch its own
content.

## Short answers to the questions that lead here

**Why do I get "Blocked script execution... sandboxed... allow-scripts permission is not
set"?** The iframe carries a `sandbox` attribute without the `allow-scripts` token, so the
browser refuses to run any script inside it, including Playwright's own instrumentation or
a `frame.evaluate()` call. Standard HTML sandboxing behavior, not a bug.

**Does this only happen on cross-origin iframes?** No. It happens on same-origin frames
too, because `sandbox` restricts scripts regardless of origin, which is what makes it easy
to confuse with [the separate cross-origin, process-isolation failure](cross-origin-iframe-unreachable.md).

**Is `microsoft/playwright#33343` still open?** No, closed October 31, 2024, `state_reason`
`completed`, no fix shipped. A maintainer confirmed the console error is benign; the
reporter's real problem was a third-party ad SDK's own automation detection, unrelated to
the sandbox warning.

**How do I check if an iframe is sandboxed before writing selectors?** Read
`iframe.sandbox` from the parent page; it is a live `DOMTokenList`. An empty list on a
present attribute means every restriction applies; check specifically for `allow-scripts`
and `allow-same-origin`.

**Can I work around a missing `allow-scripts` token?** Not from the outside. DOM reads that
skip `evaluate()` still work; anything needing a script inside that frame does not,
regardless of which tool drives the browser.

**See also:** [How to scrape iframe content with Playwright](how-to-scrape-iframe-content-playwright.md)
for the same-origin case this page assumes as a starting point, and [Why content_frame()
returns None for a cross-origin iframe](cross-origin-iframe-unreachable.md) for the
separate process-isolation failure that can compound with sandboxing on a frame that is
both cross-origin and sandboxed.

## Sources

- [`microsoft/playwright#33343`](https://github.com/microsoft/playwright/issues/33343),
  "Empty sandboxed iframes throw 'Blocked script execution' errors in Playwright,"
  retrieved 2026-08-30 via the GitHub API for its current state, close date and
  `state_reason`, and via the issue thread for the maintainer's diagnosis and the
  reporter's actual root cause.
- MDN, [`<iframe>`: the Inline Frame element, `sandbox`
  attribute](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe#sandbox),
  retrieved 2026-08-30, for the full token list, the empty-attribute behavior, and the
  `allow-scripts`/`allow-same-origin` security note quoted above.
- MDN, [`HTMLIFrameElement.sandbox`](https://developer.mozilla.org/en-US/docs/Web/API/HTMLIFrameElement/sandbox),
  retrieved 2026-08-30, confirming it is a live, readable `DOMTokenList` reflecting the
  attribute from the parent page's own script.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. A sandboxed frame without
`allow-scripts` is not a detection to engineer around; it is HTML doing exactly what its
author asked it to do.*
