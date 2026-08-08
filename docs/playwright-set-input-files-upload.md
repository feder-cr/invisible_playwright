---
title: "Playwright set_input_files uploads and the tell"
description: "Upload files with Playwright set_input_files in Firefox: no native OS picker opens, plus the trusted change-event caveat most upload tutorials skip."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 19
---


# Playwright set_input_files uploads and the tell

To upload a file with Playwright in this Firefox, call
`page.set_input_files(selector, path)`. It attaches the file straight to the
`<input type=file>`, and no native OS file picker opens for a driven input, so there is
nothing to click through or dismiss. That is the whole mechanic, and it is stock Playwright.

Most upload tutorials stop at the one-liner: call `set_input_files`, pass a path, done.
This one includes the part they leave out. There are two separate things happening when
you upload a file under automation, and only one of them is page-visible. Knowing which
is which is the difference between a how-to that works and a how-to that gets you
flagged, and as with the download side it turns out to be less alarming than the framing
usually suggests.

The mechanics come first, because they are short, and the honest caveat comes after,
because it is the reason the page exists.

## The two-line switch, uploads need nothing extra

Switching from plain Playwright to this wrapper is the same two-line change everywhere,
and file uploads need nothing added. `set_input_files` is a stock Playwright method, and
the `browser` returned by this wrapper is a real Playwright `Browser`, so the method
behaves exactly as documented upstream. There is no wrapped subset to learn, which is the
same promise the rest of these guides make.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/upload")
    page.set_input_files("input[type=file]", "report.pdf")
```

That is the whole happy path. The seed makes the run reproducible, so a failed upload can
be replayed on the same identity rather than a fresh random one, which is the debugging
habit worth keeping from the start.

## The set_input_files pattern, end to end

`set_input_files` attaches one or more files directly to an `<input type=file>` element.
It takes a single path, a list of paths for a multi-file input, or an empty list to clear
a previous selection.

| Upload scenario | What to pass / call |
|---|---|
| Single file | `set_input_files(selector, "photo.png")` |
| Multiple files into one input | `set_input_files(selector, ["a.pdf", "b.pdf"])` |
| Clear a previous selection | `set_input_files(selector, [])` |
| Bytes generated at runtime, no temp file | pass a dict with `name`, `mimeType`, `buffer` |
| Element created late or re-rendered | `page.locator(selector).set_input_files(path)` |
| Custom button that opens the chooser | `page.expect_file_chooser()` then `set_files` |

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/upload")

    # single file
    page.set_input_files("#avatar", "photo.png")

    # multiple files into one multi-input
    page.set_input_files("#attachments", ["a.pdf", "b.pdf"])

    # clear the selection
    page.set_input_files("#avatar", [])

    page.click("#submit")
```

You can also set files from memory rather than from disk, which is useful when the bytes
are generated at runtime and you do not want a temp file:

```python
page.set_input_files("#avatar", {
    "name": "photo.png",
    "mimeType": "image/png",
    "buffer": open("photo.png", "rb").read(),
})
```

Prefer a `Locator` when the element is created late or re-rendered, so Playwright waits
for it rather than failing on a selector that is not there yet:

```python
page.locator("#avatar").set_input_files("photo.png")
```

The async form is identical in shape, with `await` on the calls that perform work:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com/upload")
    await page.locator("#avatar").set_input_files("photo.png")
    await page.click("#submit")
```

## What actually happens: no native picker opens

No native OS file picker opens when `set_input_files` drives a file input in this fork; the
selection is attached to the element directly instead. Here is why that matters. In a
browser a human drives, clicking a file
input opens the operating system's file chooser: a modal window, drawn by the OS, that
the person navigates to pick a file. That dialog is native chrome, it is not part of the
page, and a script cannot reach into it.

Under automation none of that is wanted, and in this fork none of it runs. The engine
intercepts the file chooser at the layer that would otherwise ask the OS to open the
native dialog, so a file input that is being driven never triggers a system window.
`set_input_files` supplies the selection straight through the automation channel and the
file is attached to the element directly. There is no dialog to appear, no path to type
into an OS field, and nothing to dismiss.

The practical consequence is that uploads are handled instantly and headlessly. You do
not script around a system picker because, for a driven input, there is no system picker.
This is the upload-side mirror of the download-side behaviour covered in
[downloading files with Playwright and Firefox](playwright-download-files-firefox.md),
where the browser's own download shelf never appears for the same reason.

## The honest part: the picker is invisible, the event is not

A page cannot detect the missing file picker, but it can read whether the resulting upload
events are trusted. The suppressed OS dialog reveals nothing, because JavaScript in the
document could never see it either way; the observable signal is `isTrusted` on the `change`
and `input` events that firing the upload produces. This is the caveat that upload snippets
leave out, and the whole reason this page exists.

It is tempting to think the suppressed native dialog is a detection risk. It is not, and
the reason is precise: **the OS file chooser is not observable to the page in the first
place.** A native window drawn by the operating system is not a DOM element. JavaScript in
the document cannot see it open, cannot see it fail to open, and cannot time it. Whether a
human clicked through a dialog or a driver attached the file directly, the page sees the
same absence of that window, because it never had visibility into it either way.

What the page *can* observe is the result of the upload, and this is the part the
tutorials never mention. Attaching a file to an input fires `input` and `change` events on
that element, and a listener can read `event.isTrusted` on them. On a real user's
selection those events are trusted, `isTrusted` is `true`, because the browser generated
them internally in response to a genuine input change. An event synthesised from page
JavaScript, `element.dispatchEvent(new Event("change"))`, carries `isTrusted: false`
permanently, because
[that flag is set by the browser and cannot be assigned](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted).
That is the same one-line check that
[separates a real pointer from a scripted one](human-mouse-movement.md), applied to the
upload surface.

This is exactly where the mechanism above earns its keep. Because the selection is
supplied through the engine's own input path rather than dispatched from the page, the
resulting `change` and `input` events are generated by the browser and come out trusted.
A naive "humanised upload" that skips `set_input_files` and fires its own `change` event
fails the cheapest possible check the moment a listener reads one flag:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/upload")

    page.evaluate("""
        window.__trusted = null;
        document.querySelector('#avatar')
                .addEventListener('change', e => { window.__trusted = e.isTrusted; });
    """)

    page.set_input_files("#avatar", "photo.png")
    print("change.isTrusted =", page.evaluate("window.__trusted"))
    # -> change.isTrusted = True
```

Run the same probe against a page-JS `dispatchEvent` and it prints `False`. The value is
binary and decisive: assert that it is present and `true`, never merely that no error was
thrown, which is the same principle
[the guide on testing whether your browser is detected](how-to-test-bot-detection.md)
works through for every surface.

## Uploads behind a real file chooser dialog

Some pages do not expose the `<input type=file>` directly, or they open the chooser from a
custom button and only reveal the input after a click. For those, Playwright arms a
listener for the chooser and sets the files on the object it hands back:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/upload")

    with page.expect_file_chooser() as fc_info:
        page.click("#choose-file")     # the button that would open the OS dialog

    file_chooser = fc_info.value
    file_chooser.set_files("photo.png")
    page.click("#submit")
```

The interception is the same: the click that would have opened a system window is caught,
the chooser is delivered to your code as a `FileChooser` object, and `set_files` attaches
the selection through the automation channel. The trusted-event note applies here too, and
for the same reason, because the file still lands on the input through the browser's own
path.

If the upload runs behind a proxy, nothing about it changes: the request that carries the
file follows the same network path as the page that triggered it, configured exactly as in
[the configuration guide for proxies and timezone](configuration.md). The upload adds no
separate network setup.

## Conclusion

Uploading files with Playwright and this Firefox is the ordinary Playwright pattern:
`set_input_files` on a selector or a `Locator`, a list for multiple files, an in-memory
dict when the bytes are generated at runtime, and `expect_file_chooser` for pages that
gate the input behind a button. What the fork adds is that the native OS file picker never
opens for a driven input, so there is no system dialog to script around.

The part the tutorials skip is the honest one. The picker itself is not page-observable,
so its absence reveals nothing, but the `change` and `input` events it produces are, and
they have to be trusted. Because the selection is supplied through the engine's input path
rather than dispatched from the page, those events come out trusted, which is the specific
thing a hand-rolled `dispatchEvent` upload gets wrong. Assert `isTrusted` is present and
`true`, and the upload looks like what it is meant to look like.

## Short answers to the questions that lead here

**How do I upload a file with Playwright and Firefox?** Call
`page.set_input_files(selector, path)`, or `page.locator(selector).set_input_files(path)`
when the element is created late. Pass a list for a multi-file input, or an empty list to
clear the selection.

**Does set_input_files open a file dialog?** No. It attaches the file directly to the
`<input type=file>`, and in this fork the native OS chooser is intercepted, so no system
window ever opens for a driven input.

**Can a website tell I uploaded a file with automation?** Not from the missing picker,
which the page cannot observe anyway. What it can read is `isTrusted` on the resulting
`change` and `input` events. Attaching through `set_input_files` produces trusted events;
a page-JS `dispatchEvent` produces `isTrusted: false`.

**How do I upload a file that does not exist on disk?** Pass a dict with `name`,
`mimeType` and `buffer`, and Playwright attaches the in-memory bytes as a file without a
temp file on disk.

**How do I handle a custom upload button that opens a chooser?** Arm
`page.expect_file_chooser()` around the click, read `.value` after the block, and call
`set_files` on the returned `FileChooser`.

**Should I fire my own change event to look human?** No. A hand-dispatched event is
`isTrusted: false`, which is a cheaper tell than the one you were trying to avoid. Let the
browser fire it by attaching the file through `set_input_files`.

## Sources

- The upstream Playwright upload API (`set_input_files` on `Page`, `Locator` and
  `ElementHandle`, the in-memory file payload, and `expect_file_chooser` with
  `FileChooser.set_files`), which this wrapper exposes unchanged:
  [Playwright Python "Inputs" guide, Upload files](https://playwright.dev/python/docs/input).
- This project's engine, whose file-chooser interception attaches a driven selection
  through the automation channel instead of opening the operating system's native picker.
- The `isTrusted` flag on `change` and `input` events, set by the browser and not
  assignable from page JavaScript, which is what makes a properly attached upload
  distinguishable from a dispatched one:
  [MDN, Event: isTrusted property](https://developer.mozilla.org/en-US/docs/Web/API/Event/isTrusted).

**See also:** [downloading files with Playwright and Firefox](playwright-download-files-firefox.md)
for the same distinction on the download side, [the detection checklist](playwright-detected-as-bot.md)
for the behaviour around the click, and [human mouse movement](human-mouse-movement.md)
for where the trusted-event check first shows up.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The upload works like stock
Playwright; the honest note about which half of it the page can actually see is the part
worth keeping.*
