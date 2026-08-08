---
title: "Feed invisible_playwright pages into a RAG index"
description: "Use invisible_playwright as the fetch stage of a RAG pipeline so JS-rendered, gated pages return real HTML instead of empty documents, then chunk and embed the text."
parent: "AI Agents and Frameworks"
grand_parent: "Guides"
nav_order: 5
---


# Feed invisible_playwright pages into a RAG index

This page uses invisible_playwright as the fetch stage of a retrieval pipeline: it
renders each page the way a real Firefox would, presents a real-browser fingerprint so
the request is not challenged on sites that gate content, and hands you the finished
HTML to extract, chunk and embed. It also states plainly what the browser does not do,
because a fetch stage that quietly fails is worse than one that fails loudly.

A retrieval pipeline is only as good as the documents that reach it, and the most
common reason a RAG index is full of empty or half-filled documents is the fetch stage.
A plain HTTP loader asks the server for HTML and gets back a shell: the markup that
matters is assembled in the browser by JavaScript that the loader never runs. On sources
that also gate content behind a fingerprint check, the loader gets less than a shell,
because the request never looked like a browser in the first place.

## Why a plain HTTP loader returns empty documents

Two different failures produce the same empty document, and they need different fixes.

The first is rendering. A modern page ships a small HTML skeleton and a bundle of
JavaScript, and the text you want is written into the DOM after that JavaScript runs
against an API. An HTTP loader downloads the skeleton and stops. There is nothing wrong
with the request; the content simply does not exist yet at the moment the loader reads
it. You embed the skeleton, retrieval returns the skeleton, and the model answers from
navigation chrome.

The second is gating. Some sources inspect the request before they will serve the real
body: the TLS handshake, the header order, the JavaScript environment, whether the thing
asking looks like an automated client. A bare HTTP client fails that inspection on the
handshake alone and is handed a challenge page or a stub. Again the document that reaches
your index is empty, but no amount of waiting for JavaScript fixes it, because the
JavaScript was never allowed to run.

A real browser addresses both at once: it executes the page's JavaScript, and it makes a
request that reads as a genuine Firefox rather than a script. That is the whole reason to
put a browser in front of the index instead of an HTTP loader.

## The fetch stage: render with a real browser fingerprint

Switching from plain Playwright is a two-line change, and after that every method is the
standard Playwright API. The `browser` object is a real Playwright `Browser`.

```python
from invisible_playwright import InvisiblePlaywright

def fetch_rendered_html(url, seed=42):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        return page.content()

html = fetch_rendered_html("https://example.com/article")
```

[`page.goto(..., wait_until="networkidle")`](https://playwright.dev/python/docs/api/class-page#page-goto)
waits for the network to settle so the JavaScript-written content is present before
`page.content()` reads the DOM. The `seed=42` argument fixes the identity: the GPU,
canvas, audio, fonts and screen come back identical on every run, which matters here for
a mundane reason. If ingestion of one source fails, you want to replay the exact fetch
that failed rather than a fresh random one, so the failure is reproducible instead of a
moving target.

The fingerprint work is why the request is not challenged on sites that gate content:
invisible_playwright is a Firefox patched at the C++ level, so the fingerprint, the TLS
handshake and the driver layer read as a genuine Firefox rather than an automated
client. It looks like a real browser because in the ways that a detector inspects, it is
one. If you want to confirm that for your own sources before you trust the pipeline,
[test whether the browser is detected](how-to-test-bot-detection.md) against the same
pages you intend to ingest, and compare the rendered body against what a stock browser
gets.

## From rendered HTML to chunks to a vector index

Rendered HTML is still full of navigation, cookie banners and boilerplate you do not want
in the index. Extract the main text first, then chunk, then embed. Keeping these stages
separate makes each one debuggable on its own.

```python
import trafilatura
from langchain_text_splitters import RecursiveCharacterTextSplitter

def html_to_chunks(html, url):
    text = trafilatura.extract(html) or ""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
    )
    return [
        {"text": chunk, "metadata": {"source": url}}
        for chunk in splitter.split_text(text)
    ]

chunks = html_to_chunks(html, "https://example.com/article")
```

The extraction step is worth its own attention: a good extractor drops the chrome and
keeps the prose, and getting it wrong pollutes retrieval with menu items. The mechanics
of pulling clean prose out of a rendered page are covered in
[extracting clean article text](how-to-extract-clean-article-text-playwright.md). Once you
have chunks with source metadata, embedding them into a vector store is the ordinary part
of the pipeline that does not change because of where the HTML came from.

## Wiring it into LlamaIndex or LangChain

Both frameworks accept documents from any source, so the browser fetch drops in as a
custom loader that yields the same `Document` objects a built-in loader would. Here it is
as a LangChain-style loader that renders each URL and returns one document per page for
the framework's own splitter and index to consume:

```python
from invisible_playwright import InvisiblePlaywright
from langchain_core.documents import Document
import trafilatura

def load_documents(urls, seed=42):
    docs = []
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        for url in urls:
            page.goto(url, wait_until="networkidle")
            text = trafilatura.extract(page.content()) or ""
            if text.strip():
                docs.append(Document(page_content=text, metadata={"source": url}))
    return docs

documents = load_documents([
    "https://example.com/a",
    "https://example.com/b",
])
```

Note the single `with` block around the whole loop: one browser session renders every URL
in turn rather than launching a fresh browser per page, which is both faster and gentler
on the source. The `if text.strip()` guard is deliberate. An empty extraction is a fetch
that did not work, and letting it into the index as a blank document is exactly the
failure this whole exercise is meant to remove. Log the empty ones and inspect them; an
empty result is a signal, not a pass. Frameworks that drive a browser as part of a larger
loop are covered under [AI agents and frameworks](guides-ai-agents.md).

## What the browser does not fix: IP reputation and rate

This is the honest boundary of the tool, and skipping it is how ingestion pipelines get
blocked a week after they start working.

The browser solves the render-and-fetch problem: it runs the JavaScript and it presents a
real-browser fingerprint. It does not launder IP reputation. If you ingest through a
datacenter address, or an address a thousand other clients are using this minute, a
perfect browser fingerprint still arrives from an address the source distrusts, and the
document comes back challenged. Reputation is a property of the network path, not of the
browser, so the fix is a clean exit rather than a better fingerprint. The general shape of
that problem is in [why scrapers get blocked despite proxies](web-scraping-getting-blocked-proxies.md).

The second thing it does not fix is rate. Ingesting a corpus is machine-speed by nature:
a loop over a URL list issues requests far faster and far more regularly than a person
reading pages, and that regularity is itself detectable independently of any fingerprint.
Pace the loop, add jitter between fetches, and do not point the whole corpus at one host
in one burst. An agent that fetches on a perfectly uniform interval carries
[the timing signature of automation](ai-browser-agents-stealth.md) no matter how real
each individual request looks. The browser makes each request credible; the spacing
between requests is yours to get right.

## Conclusion

A RAG index built on a plain HTTP loader inherits that loader's blind spot: it cannot see
JavaScript-rendered content, and it cannot get past a source that inspects the request
before serving the body. Putting invisible_playwright in the fetch stage removes both,
because it renders the page and makes the request read as a genuine Firefox. Then you
extract clean text, chunk it, and embed it like any other document.

Keep the boundary honest. The browser earns you the real HTML from guarded, JavaScript
heavy sources. A clean exit and a paced request loop are what keep that fetch working
past the first day, and those parts are yours to supply.

## Short answers to the questions that lead here

**Why does my RAG loader return empty pages?** Almost always because the content is
written by JavaScript the loader never runs, or because the source gates the body behind a
request check the loader fails. Render with a real browser and both go away.

**Does invisible_playwright get me past sites that challenge my scraper?** It helps with
the fingerprint, TLS and driver layer, which read as a real Firefox, so requests are not
challenged on those grounds. It does not fix a distrusted IP or a request rate that is
obviously automated. It helps with the browser half and not the network half.

**Can I use it as a LangChain or LlamaIndex document loader?** Yes. Wrap the fetch in a
function that returns the framework's `Document` objects. The browser call is standard
Playwright, so nothing else in your pipeline changes.

**How do I make ingestion reproducible?** Pass a fixed `seed`. The same seed produces the
same browser identity every run, so a fetch that failed can be replayed exactly instead of
being a different random draw each time.

**Do I still need a proxy?** For most gated sources, yes, and a clean one. The browser
does not change the address the request comes from, and IP reputation is decided on that
address, not on the fingerprint.

**Should I ingest as fast as the loop allows?** No. Machine-speed, evenly spaced requests
are a signal on their own. Pace the loop and add jitter, especially against a single host.

## Sources

- This project's quickstart and configuration pages, for the launch API, the seed
  behaviour and the proxy and timezone handling used above.
- This project's release gates, which taught the rule repeated here that an empty or
  suppressed result is a failure rather than a pass, and must be asserted against rather
  than ignored.

**See also:** [extracting clean article text](how-to-extract-clean-article-text-playwright.md)
for the stage after the fetch, [how to test whether the browser is detected](how-to-test-bot-detection.md)
before you trust a source, and [AI agents and frameworks](guides-ai-agents.md) for the
wider loop this fits into.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It looks like a real
browser because in the ways a detector inspects, it is one; the clean exit and the paced
loop are still yours to bring.*
