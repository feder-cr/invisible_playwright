---
title: "How to handle popups and modals in Playwright"
description: "Handle popups and modals in Playwright: separate in-page modals, native alert/confirm dialogs, and target=_blank tabs, and use the right API for each."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 13
---


# How to handle popups and modals in Playwright

Handling popups in Playwright means matching one of three mechanisms to what the page is
actually doing: an in-page modal is DOM you locate and click, a native dialog (`alert`,
`confirm`, `prompt`, `beforeunload`) is caught with `page.on("dialog")`, and a real popup
tab is caught with `context.expect_page()` before the click that opens it. Pick the wrong
one and you get a hang, a swallowed dialog, or a `Page` you never receive.

The word "popup" covers three completely different things, and the reason people get
stuck is that they reach for one mechanism when the page is doing another. An overlay
`div` is not a native dialog, and a native dialog is not a new browser tab. Each has its
own API, and using the wrong one produces a hang, a swallowed dialog, or a `Page` object
you never receive.

This page separates the three, gives the real code for each, and closes with the one
stealth detail that matters when a popup opens a second tab: it keeps a single coherent
identity instead of looking like a different browser.

All of the examples use the wrapper, which returns a real Playwright `Browser`, so every
method below is the stock Playwright method documented upstream:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
```

## Three different things people call a "popup"

Before any code, name what you are actually looking at. Open the page and check which of
these it is, because the fix is different for each:

- **An in-page modal.** A cookie banner, a newsletter box, a "you have unsaved changes"
  panel drawn with HTML and CSS. It lives in the DOM. There is no browser event, nothing
  to catch. You interact with it like any other element.
- **A native dialog.** The small browser-drawn box from `alert()`, `confirm()`,
  `prompt()`, or the `beforeunload` "leave this page?" prompt. It is not in the DOM at
  all, so no selector will ever find it. It arrives as a `dialog` event.
- **A real popup tab.** A new browser tab or window from `window.open()` or a link with
  `target="_blank"`. It is a whole new `Page`, and you have to be listening at the moment
  it opens or you lose the handle to it.

Ninety percent of "my popup handler does not work" is one of these three being treated as
another. Sort that first.

## The in-page modal is just DOM

This one needs no special API, and that is the point. If the modal is HTML, it is an
element, so you locate it and act on it exactly like a button in the page body:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # A cookie / consent overlay drawn in the page. Wait for it, then dismiss it.
    accept = page.get_by_role("button", name="Accept")
    accept.wait_for(state="visible")
    accept.click()
```

If clicks on the page underneath do nothing, the modal is probably still there with a
transparent backdrop intercepting events. Do not force the click through it - close the
modal first, then act. A forced click that lands on an invisible overlay is also a
behavioural tell, because a human cannot click what they cannot see.

For a modal that only sometimes appears, guard on visibility rather than assuming it:

```python
box = page.locator(".newsletter-modal")
if box.is_visible():
    box.get_by_role("button", name="Close").click()
```

No events, no listeners. If a selector can reach it, it is this category and you are done.

## The native dialog: page.on("dialog")

`alert`, `confirm`, `prompt` and `beforeunload` are drawn by the browser, not the page.
No selector reaches them. They surface as a `dialog` event on the page, and you decide
what to do in a handler:

```python
def on_dialog(dialog):
    print("dialog type:", dialog.type)      # "alert", "confirm", "prompt", "beforeunload"
    print("dialog text:", dialog.message)
    dialog.accept()                          # or dialog.dismiss()

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.on("dialog", on_dialog)
    page.goto("https://example.com")
    page.click("#delete")                    # triggers confirm(); handler accepts it
```

Two facts that catch people out:

- **With no handler registered, Playwright auto-dismisses every dialog.** [Upstream
  Playwright documents this default explicitly](https://playwright.dev/python/docs/dialogs):
  if there is no listener for `page.on("dialog")`, every dialog is dismissed automatically.
  So if a `confirm()` gate is being answered "cancel" and you never told it to, that is why.
  Register a handler the moment you want a different answer.
- **`prompt()` needs a value.** Pass it through `accept`:

```python
page.on("dialog", lambda d: d.accept("my answer"))
```

For a `beforeunload` prompt, the type is `"beforeunload"` and you usually want
`dialog.accept()` to let the navigation proceed. Register the handler before the action
that navigates away, not after.

## The real popup: context.expect_page()

A link with `target="_blank"` or a `window.open()` call opens a new `Page`. The trap is
timing: the page is created the instant you click, so you have to be inside a listening
block when the click happens. Register after the click and the popup is already gone.
[Playwright's own pattern for this](https://playwright.dev/python/docs/pages) is the same
context-manager shape used below: wrap the triggering action inside the `expect_page` (or
`expect_popup`) block rather than reading the result after it.

The stock pattern is a context manager that waits for the new page while your action runs
inside it:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    context = page.context
    with context.expect_page() as new_page_info:
        page.click("a[target=_blank]")       # opens the second tab

    popup = new_page_info.value               # this is a real Playwright Page
    popup.wait_for_load_state()
    print(popup.url)
    popup.click("#confirm")                   # drive it like any other page
    popup.close()
```

`context.expect_page()` catches any new page in the browser context, which is what a
`target="_blank"` link and a `window.open()` both produce. If you prefer to scope the
wait to the page that spawned it, `page.expect_popup()` does the same thing for popups
opened by that specific page:

```python
with page.expect_popup() as popup_info:
    page.click("#open-preview")
popup = popup_info.value
```

Either way, the object you get back is a full `Page`. Every method works on it, and you
can keep the original tab and the popup open at once and drive both.

## Why the popup keeps one coherent identity

A popup handled with `context.expect_page()` never leaves the browser context its identity
is derived from, which is the whole reason it matches. Handling the popup by launching a
second browser does leave that context, and the two tabs then report different machines to
anything checking consistency between them.

A tempting way to "handle" a popup is to launch a second browser for it. Do not. A freshly
launched browser draws a fresh identity: a different GPU string, a different canvas hash, a
different audio device, a different screen, roughly 400 fields that will not match the tab
that opened it. Two tabs from one user action reporting two different machines is a
contradiction a consistency-oriented detector reads immediately, and
[CreepJS is built to notice exactly that kind of internal disagreement](creepjs-explained.md).

A popup opened with `context.expect_page()` stays inside the same browser context, so it
inherits the same seed-derived fingerprint as the tab that spawned it. The GPU string, the
canvas and audio hashes, the font list, the timezone and language all match, because they
come from one seed for the whole context. The second tab reads as the same person opening a
second tab, which is what it is.

You can confirm this rather than take it on faith. Read a stable surface in both tabs and
compare:

```python
with context.expect_page() as new_page_info:
    page.click("a[target=_blank]")
popup = new_page_info.value
popup.wait_for_load_state()

main_ua = page.evaluate("() => navigator.userAgent")
popup_ua = popup.evaluate("() => navigator.userAgent")
assert main_ua == popup_ua

# Same idea across a heavier surface: render the same canvas in both and diff the hash.
```

Run the same identity twice with a fixed `seed` and the match holds across restarts too,
which is what makes a failure reproducible. That reproducibility is the same property that
makes the whole approach debuggable, covered in the [quickstart](quickstart.md).

## Conclusion

Popups are three problems wearing one name. Decide which one you have before you write a
line: a DOM overlay you locate and click, a native dialog you catch with
`page.on("dialog")`, or a real tab you catch with `context.expect_page()` while the click
that opens it runs inside the block. Get the category right and each is a few lines of
stock Playwright.

The stealth payoff is quiet but real: keep the popup in the same context and it carries one
coherent identity instead of announcing a second, mismatched browser. Handling a popup
correctly and handling it safely turn out to be the same thing.

## Short answers to the questions that lead here

**How do I handle an alert or confirm in Playwright?** Register `page.on("dialog",
handler)` and call `dialog.accept()` or `dialog.dismiss()` inside it. With no handler,
Playwright auto-dismisses every dialog by default.

**How do I catch a new tab opened by target=_blank?** Wrap the click in
`with context.expect_page() as info:` and read the new page from `info.value` after the
block. Registering after the click is too late.

**What is the difference between a modal and a dialog?** A modal is HTML in the page, so a
selector finds it. A native dialog (`alert`, `confirm`, `prompt`, `beforeunload`) is drawn
by the browser and never appears in the DOM, so only the `dialog` event reaches it.

**My dialog handler does nothing. Why?** Most likely the box is an in-page modal, not a
native dialog, so `page.on("dialog")` never fires. Check whether a selector can reach it;
if it can, treat it as DOM.

**Should I open a second browser for the popup?** No. A second browser gets a different
fingerprint across roughly 400 fields and the two tabs stop matching. Keep the popup in the
same context with `context.expect_page()` so it inherits one identity.

**How do I answer a prompt() with text?** Pass the value to accept:
`page.on("dialog", lambda d: d.accept("value"))`.

## Sources

- Playwright's own docs on [dialog events](https://playwright.dev/python/docs/dialogs)
  (the no-listener auto-dismiss default) and on [handling new pages and
  popups](https://playwright.dev/python/docs/pages) (`context.expect_page()` and
  `page.expect_popup()`), read from the upstream documentation rather than a rendered
  example.
- This project's own fingerprint model, in which every surface of a browser context is
  derived from a single seed, so a same-context popup and its parent report the same
  machine.

**See also:** [running a separate identity per browser context](playwright-proxy-per-context.md)
for when you genuinely do want two different machines, and
[pinning fingerprint fields](pinning.md) for forcing a specific value while the rest stays
seed-derived.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The rule that a popup should
inherit its opener's identity is one you feel the day a detector flags two tabs as two
machines.*
