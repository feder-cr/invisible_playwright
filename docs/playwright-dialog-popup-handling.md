---
title: "Playwright dialog and popup handling without a tell"
description: "Playwright auto-dismisses every JS dialog unless you register page.on('dialog'). That instant always-cancel default is both a functional bug and a non-human tell."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 14
---


# Playwright dialog and popup handling without a tell

Playwright dismisses every JavaScript dialog automatically unless you register a
`page.on("dialog", ...)` handler, and that default is an instant, always-cancel answer
returned in zero milliseconds. It breaks any flow gated on a `confirm()` and hands a
watching page a clean non-human timing signal at the same time. The fix is to register a
listener, decide the answer per dialog, and wait a human amount of time before you
respond.

Every guide tells you to register `page.on("dialog", ...)`. Almost none of them tell
you what happens when you do not, which is the part that bites twice: your click
through a confirmation never proceeds, and a page watching the clock can see that the
answer came back in zero milliseconds and was always "cancel".

This page is what Playwright does with a dialog by default, why that default reads as
non-human, and how to handle `alert`, `confirm`, `prompt`, `beforeunload` and real
popup windows in a way that is both correct and unremarkable. The examples use
`invisible_playwright`, whose `browser` object is a real Playwright `Browser`, so
`page.on("dialog", ...)` behaves exactly as it does upstream.

## What Playwright does with a dialog by default

A JavaScript dialog - `alert()`, `confirm()`, `prompt()`, and the `beforeunload`
prompt - blocks the page until it is answered. In a normal browser a human answers it.
Under automation there is no human, so Playwright has to decide, and its default is to
[**dismiss every dialog automatically**](https://playwright.dev/python/docs/dialogs) the
moment one appears, unless you have registered a `dialog` listener on that page.

Dismiss is not neutral. For `confirm()` and for `beforeunload`, dismiss means
**cancel** - the call returns `false`. For `prompt()` it returns `null`. So this,
with no handler registered:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    answer = page.evaluate("() => window.confirm('Delete this item?')")
    print(answer)   # False, every time, with no handler registered
```

always prints `False`. If your automation clicks a button whose handler is
`if (confirm('Are you sure?')) doTheThing()`, the thing never happens, and nothing in
your log says why. The dialog opened and closed before your code saw it.

## The default is an instant, always-cancel answer, which reads as non-human

Here is the part the standard advice skips. A real person confronted with a
confirmation takes time to read it, and does not always click cancel. The automatic
dismissal does neither. It returns the same answer, `false`, in effectively zero time,
because there is no reading and no deciding - a listener fires and the dialog is torn
down in the same tick.

A page can measure both halves of that directly, in its own JavaScript, with no
special tooling:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    result = page.evaluate("""() => {
        const t0 = performance.now();
        const answer = window.confirm('Proceed?');
        return { ms: performance.now() - t0, answer };
    }""")
    print(result)   # {'ms': ~0.0, 'answer': False}  with no handler
```

Against a stock browser driven by a human, `ms` is in the hundreds to thousands and
`answer` varies with the question. Under the default dismissal, `ms` rounds to zero and
`answer` is constant. A site that puts a throwaway `confirm()` behind a button and
records the round-trip time gets a clean, cheap behavioural signal - the same family of
signal as a form filled in eighty milliseconds or a pointer that teleports. It is not a
fingerprint the identity layer can spoof; it lives entirely in
[the timing and shape of what your code does](human-mouse-movement.md), which is where
a growing share of detection now looks.

The functional bug and the tell are the same fact seen from two sides. Fix the bug
correctly and you remove the tell in the same move.

## Registering a handler that actually decides

The moment you attach a `dialog` listener, Playwright stops auto-dismissing and hands
the dialog to you. Now you choose the answer and, critically, when to give it.

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_dialog(dialog):
        # dialog.type is "alert", "confirm", "prompt" or "beforeunload"
        # dialog.message is the text the page passed in
        if dialog.type == "prompt":
            dialog.accept("my answer")   # fill the prompt and confirm
        else:
            dialog.accept()              # confirm / dismiss the alert

    page.on("dialog", on_dialog)

    page.goto("https://example.com")
    page.click("#delete")   # the confirm() behind this button now returns True
```

`dialog.accept()` returns `true` from `confirm()`; `dialog.dismiss()` returns `false`.
Pass a string to `accept()` to fill a `prompt()`. Read `dialog.message` and
`dialog.type` to decide per dialog rather than blanket-accepting, which matters when a
page uses a `beforeunload` prompt you want to allow but a destructive `confirm()` you
want to cancel.

Two things worth stating because they cause silent hangs:

- **You must answer.** Once a listener is registered, Playwright no longer
  auto-dismisses, so a dialog you receive and never `accept()`/`dismiss()` leaves the
  page blocked forever. If you register a handler, every path through it must respond.
- **Register before you trigger.** Attach the listener before the navigation or click
  that can raise the dialog. A handler added after the dialog has already appeared is
  too late; the default dismissal already ran.

For a single expected dialog, `page.once("dialog", ...)` handles exactly one and then
detaches, which avoids a broad handler swallowing a later dialog you did want to see.

## Answering on a human timescale, not in zero milliseconds

Registering a handler fixes the functional bug. To also close the timing tell, take the
time a person would before you answer. Because the page's `confirm()` call is blocked
until your handler responds, a delay inside the handler is measured by the page as the
user's think time:

```python
import random
import time

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    def on_dialog(dialog):
        # read time that scales a little with how much text there is
        base = 0.6 + min(len(dialog.message), 200) / 200 * 1.5
        time.sleep(random.uniform(base, base + 1.2))
        # do not answer the same way every single time for identical prompts
        if dialog.type == "confirm" and "cancel" in dialog.message.lower():
            dialog.dismiss()
        else:
            dialog.accept()

    page.on("dialog", on_dialog)
    page.goto("https://example.com")
    page.click("#confirm-action")
```

Now the round trip the page measures is a few hundred to a couple of thousand
milliseconds, it varies run to run, and the answer is not a constant. That is the
difference between "a listener fired" and "a person decided". Keep the variation honest:
a `time.sleep(1.0)` fixed to the millisecond is its own uniform-interval tell, the same
mistake as [keystrokes at a perfectly even cadence](keystroke-timing-detection-playwright.md).

One honest scope note. `invisible_playwright` makes the browser itself look like a real
Windows Firefox - GPU, audio, fonts, screen, roughly 400 fields from one seed - and it
arcs the mouse on a Bezier curve instead of teleporting it. It does **not** answer your
dialogs for you, because only your code knows whether a given confirmation should be
accepted or cancelled. Dialog timing is behaviour, and behaviour is yours to shape; the
identity layer cannot and should not guess it. This is the same division of labour that
runs through [the checklist for being detected on one site](playwright-detected-as-bot.md):
the machine and the automation are different problems with different owners.

## Popups that are not dialogs: window.open and target=_blank

"Popup" gets used for two unrelated things, and they need different handling. A
JavaScript dialog is the blocking `alert`/`confirm`/`prompt` above. A popup *window* -
`window.open()`, or a link with `target="_blank"` - is a whole new page, and Playwright
surfaces it as a `page` event on the browser context, not as a `dialog`.

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com")

    # wait for the new page the click will open
    with page.context.expect_page() as popup_info:
        page.click("#open-report")   # target=_blank or window.open
    popup = popup_info.value

    popup.wait_for_load_state()
    print(popup.title())
    popup.close()
```

The new page inherits the context, so it carries the same fingerprint and the same proxy
as the opener - you do not get a fresh, inconsistent identity in the popup, which would
be its own contradiction to detect. Handle it like any other page: wait for it, read it,
close it when done. If a site opens popups you never want, you can also close them as
they arrive by listening for the context's `page` event.

## Conclusion

The advice to register `page.on("dialog", ...)` is correct but incomplete. The default
it replaces is not a harmless auto-close: it returns an always-cancel answer in zero
milliseconds, which breaks any flow gated on a `confirm()` and hands a page a clean
non-human timing signal at the same time. Register a handler so you decide the answer,
respond on every path so the page never hangs, take a human amount of time before you
answer so the round trip is not instant, and treat popup windows as the separate pages
they are. Do that and dialogs stop being both a functional gotcha and a tell.

## Short answers to the questions that lead here

**Why does my Playwright confirm() always return false?** Because with no `dialog`
listener registered, Playwright auto-dismisses the dialog, and dismiss means cancel.
Register `page.on("dialog", d => d.accept())` and it returns true.

**Why does my click do nothing on a button with a confirmation?** The `confirm()` behind
it is being auto-dismissed before your code sees it, so the "yes" branch never runs.
Handle the dialog and accept it.

**Can a site tell I auto-dismissed a dialog?** Yes. It can time the `confirm()` call in
its own JavaScript. An answer returned in effectively zero milliseconds, and always the
same answer, is not how a person behaves.

**How do I answer a prompt() in Playwright?** Pass the text to accept:
`dialog.accept("the value")`. `dialog.dismiss()` returns null from `prompt()`.

**How do I handle window.open popups?** Those are new pages, not dialogs. Wrap the click
in `with page.context.expect_page() as info:` and use `info.value`.

**Does invisible_playwright handle dialogs for me?** No, and it should not - only your
code knows whether a given confirmation should be accepted or cancelled. It makes the
browser and the mouse look real; the dialog answer and its timing are yours.

## Sources

- Playwright's documented [`Dialog` handling](https://playwright.dev/python/docs/dialogs):
  "If there is no listener for `page.on('dialog')`, all dialogs are automatically
  dismissed."
- This project's own measurements of `confirm()` round-trip timing under the default
  dismissal versus a delayed handler, run through the shipped binary.

**See also:** [how to handle popups and modals in Playwright](how-to-handle-popups-and-modals-playwright.md)
for the broader overlay patterns, [Playwright detected as a bot on one site](playwright-detected-as-bot.md)
for the order to work a block in, [human mouse movement](human-mouse-movement.md) for
the rest of the behavioural surface, and
[how to test whether your browser is detected](how-to-test-bot-detection.md) for
measuring a timing signal like this one properly.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The instant always-cancel
default cost me a debugging afternoon before it cost me a session.*
