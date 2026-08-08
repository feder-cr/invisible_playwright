---
title: "How to upload files with Playwright, and verify it landed"
description: "Upload files with Playwright using set_input_files or expect_file_chooser, why the driver fires trusted isTrusted events, and how to verify it landed."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 11
---


# How to upload files with Playwright, and verify it landed

To upload a file with Playwright, call `page.set_input_files(selector, path)` when the
page has an `<input type="file">`, or use `page.expect_file_chooser()` when the input
only appears after a click. Both run through the automation driver, so the file attaches
with trusted (`isTrusted: true`) events and no native OS window ever appears on screen.

Uploading a file looks like it should be the fiddly part of an automation, and it is
usually the opposite: the driver does the OS dialog for you and you never see a native
window. The part worth understanding is not the syntax, it is why the correct method is
also the safe one, and where it is not enough on its own.

This page covers the two upload paths Playwright gives you, why both are handled at the
driver level rather than by faking a page event, multiple files and in-memory buffers,
the one honest caveat about behaviour, and how to confirm the file actually attached.

Everything here uses stock Playwright. `InvisiblePlaywright` returns a real
`playwright.sync_api.Browser`, so every method below is the upstream method, documented
exactly as upstream documents it.

| Situation | Method to use |
|---|---|
| The page exposes an `<input type="file">` you can select | `set_input_files(selector, path)` |
| The input only exists after clicking a custom button | `expect_file_chooser()`, then `set_files(...)` |
| Several files at once | pass a list of paths (or buffer dicts) in one call |
| Content generated at runtime, no file on disk | pass a `{name, mimeType, buffer}` dict to either method |

## The simple case: set_input_files

If the page has an `<input type="file">` element, you do not click it and you do not
open a dialog. You point the input at a path.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/upload")

    page.set_input_files("input[type=file]", "report.pdf")
    page.click("#submit")
```

`set_input_files` takes the selector and one or more paths, and it works whether or not
the input is visible. Many sites hide the real `<input>` behind a styled button; you can
still target the hidden input directly by selector, and you do not have to make it
visible first.

Clearing a selection is the same call with an empty list:

```python
page.set_input_files("input[type=file]", [])
```

Using a locator instead of a raw selector reads better when the input is nested and you
have already scoped to a form:

```python
form = page.locator("form#profile")
form.locator("input[type=file]").set_input_files("avatar.png")
```

## When the input is hidden behind a custom control: the file chooser

Some upload widgets have no reachable `<input>` at all until you click their button,
because the input is created on demand. For those, listen for the chooser the click
opens instead of hunting for a selector.

```python
with page.expect_file_chooser() as fc_info:
    page.click("text=Add a file")     # the click that opens the native dialog
file_chooser = fc_info.value
file_chooser.set_files("report.pdf")
```

`expect_file_chooser()` arms a listener, the click inside the `with` block triggers the
browser's file dialog, and `set_files` answers it. The native OS window never appears on
screen; Playwright intercepts the chooser and fills it from the driver. This is the path
to reach for when the DOM does not expose an input you can target directly.

Note that the click here is a real driver click, so on a browser that draws pointer
motion it arcs to the button on a curve like any other click rather than teleporting to
it.

## Why this path is trusted, not a synthetic event

`set_input_files` and `expect_file_chooser` are the right methods, and a hand-rolled
JavaScript alternative is not, because only the driver path produces trusted events. A
file assigned by page script fires a `change` event carrying
[`isTrusted: false`](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted),
which is a permanent, browser-set tell; the driver path fires `isTrusted: true`, exactly
as a human picking a file from the OS dialog would.

You cannot upload a file by constructing an event in page JavaScript. For security, the
browser will not let page script assign a real file to an input's `files` property, and
any `change` event you dispatch yourself arrives carrying `isTrusted: false`. That flag
is set by the browser and is not writable, so a scripted dispatch is permanently marked
as script-made. It is the same distinction that separates
[a driver click from a fake one dispatched from the page](human-mouse-movement.md):
`element.dispatchEvent(...)` is `isTrusted: false` forever, while input driven through
the automation driver is generated inside the browser and comes out genuinely trusted.

`set_input_files` and `expect_file_chooser` both go through the driver, not through page
JavaScript. The file assignment and the `change`/`input` events that follow it are
produced by the browser's own input path, so they carry `isTrusted: true` exactly as a
human picking a file from the OS dialog would. That means the upload is not the
cheapest-check tell that a scripted `dispatchEvent` would be: a page that reads
`event.isTrusted` on the file input sees a trusted event, because it is one.

`InvisiblePlaywright` does not change any of this; it is standard Playwright behaviour.
What the patched engine adds is elsewhere - the pointer motion, the fingerprint - not in
how the file attaches. The upload path is safe on plain Playwright and stays safe here.

## Multiple files, buffers, and files you never wrote to disk

`set_input_files` accepts a list, so multi-file inputs are one call:

```python
page.set_input_files("input[type=file]", ["a.pdf", "b.png", "c.csv"])
```

You do not need a file on disk at all. Pass a dict with a name, a MIME type and the
bytes, and the upload comes straight from memory - useful when the content is generated
in the run and there is no reason to touch the filesystem:

```python
page.set_input_files(
    "input[type=file]",
    files=[{
        "name": "data.csv",
        "mimeType": "text/csv",
        "buffer": b"col_a,col_b\n1,2\n3,4\n",
    }],
)
```

The same buffer form works on the file chooser:

```python
with page.expect_file_chooser() as fc_info:
    page.click("text=Attach")
fc_info.value.set_files({
    "name": "note.txt",
    "mimeType": "text/plain",
    "buffer": b"generated in the run\n",
})
```

The async API is identical with `await` in front of each call:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com/upload")
    await page.set_input_files("input[type=file]", "report.pdf")
```

## The one behavioural caveat, and how to work with it

The honest limit is not about the fingerprint, it is about behaviour. `set_input_files`
does exactly one thing: it attaches the file and fires the trusted events. It does not
move the pointer, it does not open a visible dialog, and it takes no measurable time.
On a site that only checks the events, that is complete and correct. On a site that also
watches behaviour, an upload that appears with no interaction leading up to it can be as
out of place as [a click with no pointer motion before it](human-mouse-movement.md).

The fix is not to fake the dialog, which you cannot do trustedly anyway. It is to drive
the visible control the way a person would and let `set_input_files` do the attaching:

```python
page.hover("#upload-button")     # arcs to the control
page.click("#upload-button")     # the click a human makes
page.set_input_files("input[type=file]", "report.pdf")
```

Whether that matters at all depends on whether the site watches behaviour, which is a
question worth settling before adding motion you do not need - the ordering to work
through is in [the checklist for being detected on one site](playwright-detected-as-bot.md).
The pointer path itself has its own subtlety on the hover call specifically, covered in
[why humanized motion can collapse on hover()](hover-mouse-movement-bug.md).

## Verifying the upload actually landed

An upload that silently attached nothing is worse than one that errored, because it
looks fine. Assert the presence of the result rather than the absence of an error, the
same rule as [testing any other surface](how-to-test-bot-detection.md): a blank result
is a failure, not a pass.

Read the input's own file list back:

```python
count = page.eval_on_selector(
    "input[type=file]",
    "el => el.files.length",
)
assert count == 1, f"expected 1 file attached, got {count}"

name = page.eval_on_selector(
    "input[type=file]",
    "el => el.files[0]?.name",
)
assert name == "report.pdf"
```

Then confirm the server accepted it, by waiting for whatever the page shows on success
rather than assuming the submit worked:

```python
page.click("#submit")
page.wait_for_selector("text=Upload complete", timeout=15000)
```

If the flow is part of a longer authenticated session, do the login once and reuse the
saved state instead of re-authenticating each run - the pattern is in
[scraping behind a login](how-to-scrape-behind-login-playwright.md) - so the upload runs
against a warm session rather than the highest-scrutiny sequence on the site.

## Conclusion

File upload is one of the places where the correct Playwright method and the safe method
are the same method. `set_input_files` and `expect_file_chooser` both go through the
driver, so the file attaches with trusted events that a page reading `isTrusted` cannot
tell from a human's, while the JavaScript alternative is both blocked by the browser and
marked as script-made. Use the buffer form when the content is generated in the run, add
pointer motion only if the site watches behaviour, and verify the file list and the
success state instead of trusting a call that returned without raising.

## Short answers to the questions that lead here

**How do I upload a file with Playwright?** `page.set_input_files("input[type=file]",
"report.pdf")`. It works whether or not the input is visible, and it does not open a
native window.

**The upload button has no file input I can select. What now?** Use
`page.expect_file_chooser()`, click the button inside the `with` block, and call
`set_files` on the chooser the click opens.

**Can I upload without writing the file to disk?** Yes. Pass a dict with `name`,
`mimeType` and `buffer` (the bytes) instead of a path, to either `set_input_files` or the
chooser's `set_files`.

**Is set_input_files detectable as automation?** The events it fires are trusted
(`isTrusted: true`), because the driver produces them in the browser. A `change` event
you dispatch from page JavaScript is `isTrusted: false` permanently, which is the tell.

**How do I upload more than one file?** Pass a list of paths, or a list of buffer dicts,
in a single `set_input_files` call.

**How do I check the upload actually attached?** Read `el.files.length` back off the
input with `eval_on_selector`, then wait for the page's own success indicator rather than
assuming the submit worked.

## Sources

- The upstream Playwright API for
  [`set_input_files` and `expect_file_chooser`](https://playwright.dev/python/docs/input)
  and [`FileChooser.set_files`](https://playwright.dev/python/docs/api/class-filechooser#file-chooser-set-files),
  which `InvisiblePlaywright` exposes unchanged because the returned object is a real
  Playwright `Browser`.
- The [`isTrusted`](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted)
  event property, which the upload path's trust argument rests on: read-only, set by the
  browser, true only for events the browser itself generated.

**See also:** [human-like mouse movement and the trusted-event distinction](human-mouse-movement.md),
[the checklist for being detected on one site](playwright-detected-as-bot.md), and
[scraping behind a login](how-to-scrape-behind-login-playwright.md).

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The upload path is plain
Playwright; what makes it safe is the same trusted-event boundary the pointer work leans
on.*
