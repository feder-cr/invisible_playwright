---
title: "How to Handle a PDF That Opens in a New Tab with Playwright"
description: "Catch the new page with context.expect_page() when a link opens Firefox's PDF viewer, then pull the real bytes with context.request or a response listener instead of screenshotting the viewer."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 144
---


# How to Handle a PDF That Opens in a New Tab with Playwright

A link that opens a PDF in a new tab hands you two separate problems, and most
attempts at this fail because they only solve the first one. The first problem is
catching the tab at all, before it opens and closes out of reach. The second, the one
that actually matters, is that once you have the tab, what you are looking at is
Firefox's built-in viewer rendering the file, not the file. A screenshot of that
viewer is a picture of a picture. What you want is the bytes.

This page covers both: catching the popup with the standard Playwright event, and then
getting the real PDF, either by fetching it directly or by reading it off the network
response, instead of settling for whatever the viewer happened to render on screen.

## Step 1: catch the tab before it opens and closes on you

A link with `target="_blank"`, or a `window.open()` call, creates a brand new `Page`
the instant it fires. If you are not already listening when that happens, you get
nothing back. The fix is Playwright's own pattern for this: wrap the click inside
`context.expect_page()` so the listener is armed before the action runs.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/reports")

    with page.context.expect_page() as new_page_info:
        page.click("a[href$='.pdf']")   # opens the viewer in a new tab

    pdf_tab = new_page_info.value        # a real Playwright Page
    pdf_tab.wait_for_load_state()
```

This part is not specific to PDFs. It is the same mechanism covered in
[how to handle popups and modals in Playwright](how-to-handle-popups-and-modals-playwright.md):
register the listener, run the triggering click inside the block, read the result off
`.value` after. Register it after the click and the tab is already open and unreachable.

If the site instead navigates the *current* tab straight to the PDF URL - no new tab,
just a normal `page.goto()` that happens to land on a PDF - skip this step entirely.
There is no popup to catch, and everything below still applies to the one page you
already have.

## Step 2: know what that tab actually contains

Once you have `pdf_tab`, resist the urge to `pdf_tab.screenshot()` and call it done.
Firefox renders PDFs with its bundled viewer, pdf.js, which is a real web page in its
own right - toolbar, page canvas, scroll container - not the document. A screenshot of
it captures whatever fits in the viewport at that scroll position: usually one page of
a document that may have twenty, none of it selectable or extractable as text. For a
quick visual confirmation that a PDF opened, that is fine. For anything that needs the
actual content, it is close to useless, for the same underlying reason
[`page.pdf()` cannot produce text from a rendered page either](how-to-generate-pdf-with-playwright-firefox.md):
a picture of a document is not the document.

What you actually want is the byte stream the browser downloaded before it ever handed
those bytes to the viewer. There are two ways to get it, and which one applies depends
on whether you have a direct link to work with.

## Route 1: fetch the URL directly, and never load the viewer at all

If the link has a real `href`, the simplest and most reliable route skips the viewer
completely. Read the URL from the anchor before you click anything, then fetch it with
Playwright's own request context instead of opening it in a page:

```python
from pathlib import Path
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/reports")

    pdf_url = page.get_attribute("a[href$='.pdf']", "href")
    if not pdf_url.startswith("http"):
        pdf_url = page.url.rsplit("/", 1)[0] + "/" + pdf_url.lstrip("/")

    response = page.context.request.get(pdf_url)
    Path("report.pdf").write_bytes(response.body())
```

`page.context.request` is a Playwright `APIRequestContext` tied to the same browser
context - the same cookies, and if you launched with a proxy, the same proxied exit as
every page load in the session. That matters for exactly the reason it matters for any
other download: fetching the file with a separate, unproxied HTTP client would send it
out from your real address instead of the browser's, which is
[the same self-inflicted mismatch covered on the file-download page](how-to-download-files-playwright.md).
Using `context.request` here keeps the PDF fetch inside the one identity the rest of
the session already presents.

Once you have the bytes, extract text with any PDF library:

```python
from pypdf import PdfReader
import io

reader = PdfReader(io.BytesIO(response.body()))
text = "\n".join(p.extract_text() or "" for p in reader.pages)
```

This route never opens pdf.js, never spends time rendering anything, and works exactly
the same whether the original link used `target="_blank"` or not, because you never
click it in the first place. It is the one to reach for whenever a stable URL exists.

## Route 2: read the response when there is no stable link

Some PDFs are not a link with an `href` you can grab. A button posts a form and the
server streams a PDF back as the response to that POST, or the tab's URL is a
short-lived, signed link you cannot reconstruct by hand. In that case, catch the bytes
off the response as the new tab loads it, instead of trying to re-derive the URL:

```python
from invisible_playwright import InvisiblePlaywright

pdf_bytes = None

def capture_pdf(response):
    global pdf_bytes
    if "application/pdf" in (response.headers.get("content-type") or ""):
        pdf_bytes = response.body()

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/reports")

    with page.context.expect_page() as new_page_info:
        page.click("#generate-pdf")       # posts a form; server streams a PDF back

    pdf_tab = new_page_info.value
    pdf_tab.on("response", capture_pdf)
    pdf_tab.wait_for_load_state()

if pdf_bytes:
    from pathlib import Path
    Path("report.pdf").write_bytes(pdf_bytes)
```

Register the `response` listener on the new tab as early as possible - ideally right
after you get it back from `new_page_info.value`, before calling
`wait_for_load_state()` - because the main-frame response that carries the actual PDF
bytes can arrive before your next line executes. Filter on content type rather than
assuming: a redirect or an error page can land in the same tab and you do not want to
write its HTML to a `.pdf` file and find out later.
[`Response.body()`](https://playwright.dev/python/docs/api/class-response) returns the
raw buffer regardless of what the browser's UI ends up doing with it, so the same
method works whether the viewer eventually renders it or not - the capture happens at
the network layer, one level below the tab's on-screen content.

## Choosing between the two routes

| Situation | Route |
|---|---|
| The link has a real, fetchable `href` | Route 1: `context.request.get()`, skip the viewer entirely |
| The PDF is generated by a POST or a signed, short-lived URL you cannot reconstruct | Route 2: catch it off the `response` event in the new tab |
| You only need visual confirmation the PDF opened, not its content | A screenshot of the viewer tab is enough, and nothing above is necessary |

Route 1 is simpler, faster, and never touches pdf.js, so prefer it whenever a plain URL
is available. Reach for route 2 only when there genuinely is no stable link to fetch
directly.

## Conclusion

A PDF that opens in a new tab is two ordinary Playwright problems stacked on top of
each other: catching a popup, which `context.expect_page()` already solves, and
getting real content out of what the popup shows, which the viewer itself cannot give
you. Fetch the URL directly through `context.request` when you can, so the file never
passes through pdf.js at all; fall back to reading the `response` event on the new tab
when there is no direct link to fetch. Either way, the bytes are what you were after,
and a screenshot of the viewer was never going to be a substitute for them.

## Short answers to the questions that lead here

**How do I catch a PDF that opens in a new tab with Playwright?** Wrap the click that
opens it in `with page.context.expect_page() as info:`, then read the new `Page` from
`info.value`. Register the listener before the click, not after.

**Can I just screenshot the PDF viewer tab?** You can, and it only captures whatever
fits in the current viewport, usually one page, with no selectable text. For content
extraction, fetch the real bytes instead.

**What is the most reliable way to get the actual PDF bytes?** If the link has a real
URL, fetch it directly with `page.context.request.get(url)` and read
`response.body()`. This never opens the viewer at all.

**What if there is no direct link to the PDF?** Listen on the new tab's `response`
event, filter for `content-type: application/pdf`, and read `response.body()` off the
matching response as it loads.

**Does fetching the PDF through context.request keep it on my proxy?** Yes.
`context.request` shares the browser context, so the fetch uses the same cookies and
proxied exit as the rest of the session, avoiding the address mismatch a separate HTTP
client would introduce.

**Why shouldn't I just use page.pdf() instead?** `page.pdf()` generates a new PDF from
a page's own rendered output and is Chromium-only in Playwright; it has nothing to do
with retrieving a PDF that already exists at a URL. See
[how to generate a PDF with Playwright and Firefox](how-to-generate-pdf-with-playwright-firefox.md)
for that separate question.

## Sources

- Playwright's own documentation on [pages and popups](https://playwright.dev/docs/pages)
  (`context.expect_page()`), retrieved 2026-08-30, for the popup-capture pattern used
  above.
- Playwright's [`Response` API reference](https://playwright.dev/python/docs/api/class-response),
  retrieved 2026-08-30, for `Response.body()` returning the raw buffer independent of
  how the page renders it.
- Playwright's [`APIRequestContext` documentation](https://playwright.dev/python/docs/api/class-apirequestcontext),
  retrieved 2026-08-30, for `context.request` sharing the browser context's cookies and
  proxy configuration.

**See also:** [how to handle popups and modals in Playwright](how-to-handle-popups-and-modals-playwright.md)
for the general popup-catching pattern this page builds on,
[how to download files with Playwright](how-to-download-files-playwright.md) for why a
transfer should stay on the browser's own proxied exit, and
[how to generate a PDF with Playwright and Firefox](how-to-generate-pdf-with-playwright-firefox.md)
for the different problem of producing a PDF rather than consuming one.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The viewer tab is a real
page and Playwright will happily hand it to you; it is just not the document you
actually wanted.*
