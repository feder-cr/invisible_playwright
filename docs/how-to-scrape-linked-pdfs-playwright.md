---
title: "Download and read PDFs linked from a page with Playwright"
description: "Download PDFs linked from a page with Playwright: find every link by content type, fetch through page.request so cookies carry over, then extract the text."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 149
---


# Download and read PDFs linked from a page with Playwright

Downloading PDFs linked from a page is three jobs, not one: find every link that
actually resolves to a PDF, even ones hidden behind a redirect or a bare query
string; fetch the bytes through the page's own session so an auth wall does not
refuse you; then run the file through a PDF library to pull the text out. Skip the
session part and a plain HTTP client gets HTML or a 403 instead of a document.

## Find every link that actually resolves to a PDF

A `.pdf` extension in the URL is a hint, not a guarantee. Plenty of real PDF links lack
one: a download endpoint like `/reports/export?id=42&format=pdf`, or a redirector that
sends a 302 elsewhere before the bytes arrive. Trusting the extension misses both, and
the DOM gives you the absolute URL for free, so start there:

```python
candidates = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
```

Measured on a local page carrying a relative link, an absolute one with a query string
and one non-PDF link, that call returned all three fully resolved, query string intact.
The extension filter narrows the list cheaply; the content type is what confirms it, and
the next section is where that happens.

## Getting the bytes: one route works, three do not

This is the part where most examples on the web are wrong for Firefox, so here is what
was measured on 5 September 2026, all four routes against the same local page.

**What works: `fetch` from inside the page.** The request leaves the browser itself, with
the session's cookies attached, and comes back as bytes you can write to disk.

```python
import base64
from pathlib import Path

res = page.evaluate("""async (u) => {
    const r = await fetch(u, {credentials: 'include'});
    const buf = new Uint8Array(await r.arrayBuffer());
    let s = ''; for (const x of buf) s += String.fromCharCode(x);
    return {status: r.status, type: r.headers.get('content-type'), b64: btoa(s)};
}""", url)

if "application/pdf" in (res["type"] or ""):
    Path("report.pdf").write_bytes(base64.b64decode(res["b64"]))
```

That returned `200`, `application/pdf` and 1388 bytes on the test file, and the content
type in the same response is your filter: one request answers both "is it a PDF" and
"give me the PDF". Base64 through `evaluate` is the price of moving binary across the
protocol boundary; for very large files, prefer the download route below.

**What does not work, and why it matters:**

`page.goto(pdf_url)` returns `None` instead of a response. Firefox hands the PDF to its
built-in viewer, so there is no ordinary navigation response to call `.body()` on. The
URL changes, the viewer renders, and your code gets nothing. Measured: `response is
None`, `document.contentType` confirming the viewer took over.

`page.expect_download()` around a click on a plain link times out, for the same reason:
the viewer opens instead of a download starting. It is the right API when the server
sends `Content-Disposition: attachment`, and the wrong one for a link the browser thinks
it can display.

`page.request` / `APIRequestContext` is refused outright on this engine, by design. The
error says why: HTTP performed outside the page is not browser automation and carries
none of the browser's identity, so a request that looks nothing like the browser making
it would be a hole in the very thing this engine exists to keep consistent. On stock
Playwright it works; here you use the in-page `fetch` above.

## Why a plain requests.get so often comes back as HTML or a 403

Pointing a separate Python client at the same URL frequently fails where the browser
succeeds, for two independent reasons. First, a PDF gated behind a login has no
session to present: a bare `requests.get()` carries none of the cookies the browser
earned by signing in, so the server answers with a login page instead of the file.
Second, even a public PDF can sit behind a defense that fingerprints the TLS
handshake and header order, and a generic client's handshake does not match a
browser's; the response is an interstitial or a flat `403` instead of bytes. Fetching with `fetch` from inside the page avoids both, because the request leaves the
browser itself: same cookies, same handshake, same header order as every other request
that page has already made. That is also why this engine refuses the out-of-page request
API rather than offering it as a convenience.

## Name files deterministically, so a rerun does not duplicate work

Hash the source URL into the filename and every PDF gets a stable name that survives a
rerun without a second copy appearing under a different one:

```python
import hashlib
name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".pdf"
```

The tempting alternative is the last path segment, and it breaks on the first URL with a
query string: `annex.pdf?v=2` becomes a filename with a `?` in it, which Windows refuses
outright. Strip anything from the first `?` or `#` before you use it, or hash and move
on. Deriving names from link text is worse still: free-form, frequently duplicated, and
sometimes absent.

## Extract the text once you have the bytes

With the file on disk, or just the bytes still in memory, a PDF library turns the
pages into text. `pypdf` is a common choice for this:

```python
from pypdf import PdfReader

reader = PdfReader("pdfs/report.pdf")
text = "\n".join(page.extract_text() or "" for page in reader.pages)
```

Run against the file fetched above, that returned one page and the exact string the PDF
was built with. pypdf describes itself on PyPI as "a pure-python PDF library capable of
splitting, merging, cropping, and transforming PDF files", and `extract_text()` on a page
object is its own documented example. A file returning an empty string from
`extract_text()` on every page usually has no real text layer: a scanned image saved
as a PDF rather than text ever typeset. That case needs OCR, a different tool
entirely and outside what a text-extraction library does.

## When the link opens a viewer tab instead of downloading anything

Some PDF links, especially ones without a download attribute, open Firefox's
built-in viewer in a new tab rather than triggering a download at all. That is a
distinct problem, catching the popup and then getting real bytes out from under the
viewer, and it has [its own page](how-to-handle-pdf-opens-new-tab-playwright.md)
rather than a paragraph here.

## Conclusion

Bulk PDF collection is finding, fetching and reading, and each step fails for a
different reason if skipped. Confirm a link is really a PDF by content type rather
than URL shape, fetch it through the page's own request context so a login or a bot
check does not refuse a stranger, name the result deterministically, and hand the
bytes to a PDF library once they are on disk. The one case deferred entirely here, a
PDF opening in Firefox's own viewer tab, has a dedicated page of its own.

## Short answers to the questions that lead here

**How do I download all PDF links from a page with Playwright?** Collect every
anchor's `href`, fetch each through `page.request`, and keep the ones whose response
`content-type` is `application/pdf`. That catches redirectors and disguised
query-string links a `.pdf` check would miss.

**Why does requests.get return HTML instead of the PDF?** Either the PDF needs a
login the separate client never presents, or a bot check on the handshake and
headers is refusing a client that is not a real browser.

**How do I get a PDF that requires being logged in?** Fetch it with `page.request`
after signing in. The request context shares the browser's cookie jar automatically,
so the session carries over without copying anything by hand.

**How do I name downloaded PDFs so reruns do not duplicate them?** Hash the source
URL into the filename, or read the `filename` parameter off `Content-Disposition` if
the server sets one.

**What if the PDF opens in a viewer tab instead of downloading?** That is a separate
situation, catching the new tab and pulling real bytes out from under Firefox's
viewer, covered on its own page.

**See also:** [how to handle a PDF that opens in a new
tab](how-to-handle-pdf-opens-new-tab-playwright.md), [how to download files with
Playwright](how-to-download-files-playwright.md), and [reading and setting cookies in
a Playwright context](read-set-cookies-playwright-context.md).

## Sources

- Playwright, Downloads, https://playwright.dev/python/docs/downloads - `expect_download`
  and `save_as`, the route that applies when the server sends the file as an attachment.
  Read 5 September 2026.
- pypdf on PyPI, https://pypi.org/project/pypdf/ - the quoted project description and the
  `PdfReader` / `extract_text()` example. Read 5 September 2026, version 6.17.0.
- Our own measurement, 5 September 2026, on a locally served page and two generated PDFs:
  in-page `fetch` returned 200 and `application/pdf`; `page.goto()` on a PDF returned no
  response object because Firefox's viewer took over; `expect_download` timed out on a
  plain link; the out-of-page request API is refused by this engine; and pypdf read the
  text back from the fetched bytes.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Most of the PDF links
that ever gave me trouble were not broken; they just were not PDFs yet when I fetched
them.*
