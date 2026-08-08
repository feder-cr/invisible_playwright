---
title: "Playwright download files with Firefox and the tell"
description: "Download files with Playwright and Firefox using expect_download. accept_downloads is on by default, no save dialog appears, and the file is not page-visible."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 15
---


# Playwright download files with Firefox and the tell

To download a file with Playwright and this Firefox, arm `page.expect_download()` before
the click that triggers the download, read the `download` object off `.value` after the
block, and call `download.save_as(path)`. `accept_downloads` is on by default, so nothing
extra needs enabling, and no save dialog or download shelf appears.

Most download tutorials stop at the code that saves the file. This one includes the
part they leave out: which of the things your automation does while downloading a file
are visible to the page, and which are not. That distinction is the difference between
a how-to that works and a how-to that gets you flagged, and it turns out to be less
alarming than the framing usually suggests.

The mechanics come first, because they are short, and the honest caveat comes after,
because it is the reason the page exists.

## The two-line switch, with downloads already on

Switching from plain Playwright to this wrapper is the same two-line change everywhere,
and downloads need nothing extra. In Playwright, `accept_downloads` defaults to on for
every context, and creating a page through this wrapper creates that context for you, so
a download is captured without any option being set.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/reports")
    # accept_downloads is already true, nothing to configure here
```

The `browser` returned is a real Playwright `Browser`, so every method below is the
stock Playwright API documented upstream. There is no wrapped subset to learn, which is
the same promise the rest of these guides make.

## The expect_download pattern, end to end

A download is an event, not a return value, so you arm a listener before the click that
triggers it and read the result after. That is what `page.expect_download()` does: it
opens a context manager, you perform the action inside it, and the download object is
available on `.value` once the block exits.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/reports")

    with page.expect_download() as download_info:
        page.click("#download-report")   # the click that starts the download

    download = download_info.value
    print("server suggested:", download.suggested_filename)
    download.save_as("report.pdf")       # write it where you want it
```

Three properties of the `download` object cover almost every real need:

- `download.suggested_filename` is the name the server proposed, useful when you want to
  keep it rather than name the file yourself.
- `download.save_as(path)` writes the file to a path you choose and waits for the write
  to finish.
- `download.path()` returns the location Playwright already streamed it to, if you would
  rather move it than re-save it.

The async form is identical in shape, with `await` on the calls that perform work:

```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(seed=42) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com/reports")

    async with page.expect_download() as download_info:
        await page.click("#download-report")

    download = await download_info.value
    await download.save_as("report.pdf")
```

If you want every download in a run to land in the same directory automatically, create
the context yourself with an explicit `downloads_path`, then take pages from it:

```python
with InvisiblePlaywright(seed=42) as browser:
    context = browser.new_context(accept_downloads=True, downloads_path="./downloads")
    page = context.new_page()
    # every download this context captures streams under ./downloads
```

## What actually happens: no download shelf appears

No download shelf, save prompt, or progress row appears under this wrapper, because the
file is taken before that interface is ever built. In a browser a human drives, clicking
a download link produces visible chrome: a shelf, a panel, a progress row, a completion
chime. That machinery exists to inform a person.

Under automation none of it is wanted, and in this fork none of it runs. The engine
carries download-interception hooks in the layer that decides what to do with an
incoming file, so a triggered download is routed straight to the automation channel and
handed to Playwright as a `download` object. The browser never opens a shelf, never
prompts for a save location, and never paints a progress row, because the file was taken
before any of that UI would have been built.

The practical consequence is that downloads are handled instantly and headlessly with no
dialog to dismiss and no timing to wait on beyond the transfer itself. You do not script
around a save dialog because there is no save dialog.

## The honest part: what a download does and does not reveal

**For the most part, an automated download is not a detection surface, because the
file landing on disk is not observable to the page.** JavaScript running in the
document cannot see your filesystem, cannot read where the file went, and cannot time
the write, so once the browser has begun the transfer, what your code does with the
bytes happens entirely outside the page's view.

That is the caveat this page exists to state plainly, and it earns the reasoning
behind it rather than a bare claim. The suppressed UI and the instant, dialog-free
handling described above are real behaviours, and they are machine behaviours, not
human ones: a person cannot accept a file in zero milliseconds with no visible
interface, which is exactly why it is fair to ask the question in the first place. The
shelf that did not appear is the browser's own UI, not a page element, so the document
cannot query its absence either.

What the page can observe is everything up to and around the click: that a link was
activated, how the pointer arrived at it, whether the activation carried a trusted event,
and the rhythm of the actions before and after. Those are the same behavioural signals
any interaction exposes, and they are covered by the general order of work in
[the checklist for when Playwright is detected on one site](playwright-detected-as-bot.md).
The download itself adds little to that picture.

| Observable to the page | Not observable to the page |
|---|---|
| That a link or button was activated | The file landing on disk |
| How the pointer arrived at it | Where on disk the file was saved |
| Whether the click carried a trusted event | The timing of the disk write |
| The rhythm of actions before and after | The absent download shelf (browser UI, not a page element) |

So treat this page as a practical how-to first. It is not a claim that downloading is a
strong tell, and it is not a claim that it is invisible either. It is the accurate
middle: the file handling is not page-observable, the behaviour leading into it is, and
the honest move is to make the approach to the click look like the approach a human makes
rather than to worry about the transfer that follows. This is the same principle as
asserting a real signal instead of the absence of a wrong one, which
[the guide on testing whether your browser is detected](how-to-test-bot-detection.md)
works through in full.

## Downloads through a proxy and across a persistent profile

Two production concerns that come up as soon as the snippet leaves your laptop.

A download follows the same network path as the page that triggered it, so if the session
runs behind a proxy the file is fetched through that exit, with DNS routed through the
proxy as well. You configure it exactly as you configure any session, covered in
[the configuration guide for proxies and timezone](configuration.md); nothing about
downloads changes the proxy setup.

If the workflow authenticates once and downloads repeatedly, keep the session on a
[persistent profile](persistent-profiles.md) so cookies and storage survive between runs
and you are not solving a login before every file. The `expect_download` pattern is
unchanged; it just runs inside a browser that already remembers who it is.

## Conclusion

Downloading files with Playwright and this Firefox is the ordinary Playwright pattern:
`accept_downloads` is already on, you arm `expect_download` before the click, and you
read `suggested_filename`, `save_as` and `path` off the result. What the fork adds is
that the browser's download UI never appears, so there is no dialog to script around.

The part the tutorials skip is the honest one. The file on disk is not visible to the
page, so the download is a how-to more than a detection surface, but the suppressed
interface and the instant handling are machine behaviours. The signal that matters is the
behaviour approaching the click, not the transfer after it, and that is where to spend
your attention.

## Short answers to the questions that lead here

**How do I download a file with Playwright and Firefox?** Arm `page.expect_download()`
before the click that starts the download, then read `download_info.value` after the
block and call `save_as`. `accept_downloads` is on by default, so there is nothing extra
to enable.

**Do I need to set accept_downloads?** No. It defaults to on for every context, and
creating a page through this wrapper creates that context for you.

**Why does no download dialog or shelf appear?** The engine routes a triggered download
straight to the automation channel before any browser download UI is built, so the file
is handed to Playwright without a shelf, a prompt, or a progress row.

**Can a website tell I downloaded the file with automation?** Not from the file itself.
The bytes landing on disk are not observable to the page. What the page can see is the
click and the behaviour around it, which is the same for any interaction.

**Where does the file go?** Wherever `save_as` writes it, or to `download.path()` if you
let Playwright place it. Set `downloads_path` on the context to collect them all in one
directory automatically.

**Does the download go through my proxy?** Yes. It follows the same network path as the
page that triggered it, proxy and proxied DNS included, with no separate configuration.

## Sources

- [The Playwright download API](https://playwright.dev/python/docs/downloads)
  (`page.expect_download`, `Download.save_as`, `Download.suggested_filename`,
  `Download.path`), plus the `accept_downloads` context option, all of which this
  wrapper exposes unchanged.
- This project's engine, whose download handling routes an intercepted transfer to the
  automation channel rather than to a browser download shelf.
- The observability boundary between page JavaScript and the filesystem, which is what
  makes the file itself non-observable to the document.

**See also:** [the detection checklist](playwright-detected-as-bot.md) for the behaviour
around the click, [configuration](configuration.md) for running the session behind a
proxy, and [persistent profiles](persistent-profiles.md) for downloads that follow a
logged-in session.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The download works like
stock Playwright; the honest note about what is and is not page-observable is the part
worth keeping.*
