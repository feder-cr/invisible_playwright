---
title: "How to extract clean article text with Playwright"
description: "Extract clean article text with Playwright: wait for the body to render, then run a readability pass over page.content() to drop nav, ads and cookie chrome."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 62
---


# How to extract clean article text with Playwright

Do not read the visible text off the page - read the fully rendered HTML and
run a readability pass over it. That order is what stands between the
headline, the byline and the paragraphs you actually want, and what a naive
extractor hands back instead: the nav bar, the cookie banner, the newsletter
box, the "you might also like" strip, with the actual body somewhere in the
middle, and sometimes not there at all.

This page is about getting the body and only the body, reliably, run after run.

## Why a naive innerText grabs the furniture

The obvious approach reads the visible text of the page and hopes the article
dominates it:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/some-article")
    text = page.inner_text("body")   # everything, in DOM order
    print(len(text), "characters")
```

Two things go wrong here.

First, `body` is the whole document. The menu labels, the cookie copy, the
footer links and the related-articles rail are all real text nodes, and they
come back interleaved with the paragraphs you actually want. On a typical
content page that furniture is the majority of the characters, not a rounding
error.

Second, and worse, the article body is frequently injected by JavaScript after
the initial HTML arrives. A hydration pass, a lazy loader, a "read more" that
expands the rest of the piece. If you read too early you capture the shell -
the chrome that shipped in the first response - and miss the paragraphs that
appeared a moment later. The extractor returns a confident, non-empty string
that happens to be all furniture and no article.

## Render first: wait for the body, then take page.content()

The fix has two halves. Let the page finish rendering, then hand the *rendered*
HTML to the extractor rather than the initial response.

[`page.content()`](https://playwright.dev/python/docs/api/class-page#page-content)
serialises the live DOM as it stands now, after scripts have run and injected
their nodes. That is the difference between it and an HTTP `GET`, which only
ever sees the pre-JavaScript shell.

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/some-article", wait_until="networkidle")

    # If the body arrives via a specific container, wait for it explicitly
    # rather than trusting a network heuristic.
    page.wait_for_selector("article, main, [role=main]")

    html = page.content()   # the fully rendered DOM, injected paragraphs included
```

`wait_until="networkidle"` covers the common case, though
[Playwright's own docs](https://playwright.dev/python/docs/api/class-page#page-goto)
flag it as discouraged; an explicit `wait_for_selector` on the container that
holds the article is stronger, because "the network went quiet" and "the body
is on the page" are not the same event. If the article only materialises as
you scroll, that is a different
problem with its own page: see
[how to scrape infinite scroll with Playwright](how-to-scrape-infinite-scroll-playwright.md).

Note there is no special API here. `InvisiblePlaywright` hands you a real
Playwright `Browser`, so `goto`, `wait_for_selector` and `content()` are the
stock methods documented upstream, working exactly as they do in plain
Playwright.

## Run a readability pass over the rendered HTML

Now that `html` is the full rendered document, a readability algorithm can find
the main content block and discard the rest. The `readability-lxml` package is
a direct port of the well-known algorithm and takes an HTML string:

```bash
pip install readability-lxml lxml
```

```python
from readability import Document
from lxml import html as lxml_html

doc = Document(html)          # html from page.content() above
title = doc.title()
article_html = doc.summary()  # main article as HTML, furniture stripped

# Flatten to plain text if that is what you need
tree = lxml_html.fromstring(article_html)
paragraphs = [p.text_content().strip() for p in tree.iter("p")]
article_text = "\n\n".join(p for p in paragraphs if p)

print(title)
print(len(article_text), "characters of body")
```

The whole thing end to end:

```python
from invisible_playwright import InvisiblePlaywright
from readability import Document
from lxml import html as lxml_html

def extract_article(url, seed=42):
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.wait_for_selector("article, main, [role=main]")
        html = page.content()

    doc = Document(html)
    tree = lxml_html.fromstring(doc.summary())
    body = "\n\n".join(
        p.text_content().strip()
        for p in tree.iter("p")
        if p.text_content().strip()
    )
    return doc.title(), body
```

The measurable difference is the point. On one rendered content page, a naive
`inner_text("body")` came back around 9,100 characters, most of them menu
labels, cookie copy and related-links. The readability pass over the same
rendered DOM returned about 4,300 characters that were the article and nothing
else. Same page, same load: the gain is entirely in *what* you read and in
reading it *after* the body was injected.

## Why a real engine sees paragraphs an HTTP fetch never receives

There is a reason this recipe uses a browser at all rather than `requests` plus
readability. The clean-extraction problem and the not-getting-blocked problem
share a root cause, and it is worth being explicit about.

An HTTP fetch receives the pre-JavaScript shell: the markup the server sent
before any script ran. On a page whose body is hydrated or lazy-injected, that
shell simply does not contain the paragraphs. No readability algorithm can
recover text that never arrived. A real engine runs the scripts, so the
injected body is actually in the DOM when `page.content()` serialises it. That
is the mechanism, not a marketing claim: you extract the article because the
engine rendered it.

The fingerprint side of this is subtler. Sites do not always serve every
visitor the same document. A session that reads as automated can be handed a
stripped page, an interstitial, or a challenge instead of the article - and a
stripped page passes right through a readability pass and hands you a confident,
wrong result. Extraction quality and session credibility are the same problem
wearing two hats.

This is where a consistent, real-looking fingerprint earns its place. Because
`InvisiblePlaywright` derives every surface - GPU, canvas, audio, fonts, screen
- from one seed, `seed=42` gets you the same coherent machine on every run, so
the same full article is served each time rather than the article once and a
stripped variant the next. That reproducibility is also what makes a broken
extraction debuggable: a failing run replays exactly instead of vanishing into
the next random draw. The general version of "get the real page, not the bot
page" is its own topic:
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md),
and the seed mechanics are in the [quickstart](quickstart.md).

## Verify readability kept the whole article

Readability is a heuristic, not a parser with a contract. It guesses which
block is the main content by scoring nodes, and it can guess slightly wrong in
ways that are invisible unless you check. Treat its output as a draft to
validate, not a result to trust.

Three cheap checks catch most of the failures:

- **Byline and headline survived.** These often sit in their own elements
  outside the main content block, and readability drops them more often than it
  drops paragraphs. If you need the author, extract it separately rather than
  hoping the summary kept it.
- **It did not truncate at an inline widget.** A pull-quote, an embedded video,
  a subscribe box or a mid-article "related" card can look like the end of the
  content to the scorer, and everything after it gets dropped. Compare the
  paragraph count, or the last paragraph, against what you can see rendered.
- **The result is not the furniture again.** On a very short page the nav can
  outscore the body. Assert a sane minimum length and that the text is not
  dominated by link labels.

The honest framing is the same one that applies to any detection or extraction
signal: assert that the *right* content is present, not merely that *something*
came back. An empty or truncated extraction that raises no error is the failure
mode to design against, and the way to see it is to compare the extracted text
against the rendered page rather than trusting the length of a string. The same
instinct is spelled out for detection in
[how to test whether your browser is detected](how-to-test-bot-detection.md).

## Conclusion

Clean article text is two decisions made in the right order. Render the page and
wait for the body to actually arrive, then run readability over `page.content()`
rather than over the initial HTTP response or the raw `body` text. A real engine
is what makes the first half possible, because it renders the JavaScript-injected
paragraphs an HTTP fetch never receives, and a consistent fingerprint is what
keeps the full article served across runs instead of a stripped variant. Then
verify: readability is heuristic, so confirm it kept the byline and the last
paragraph and did not stop at an inline widget.

## Short answers to the questions that lead here

**Why does my Playwright scraper only get the nav and footer?** Because you read
`body` text, which is the whole page in DOM order, and often you read it before
the article was injected. Wait for the body container, then run a readability
pass over `page.content()`.

**Should I use page.content() or the response HTML?** `page.content()`. The
response HTML is the pre-JavaScript shell; on a page that hydrates or
lazy-injects its body, the paragraphs are not in the response at all and only
exist in the rendered DOM.

**Why not just use requests and readability?** Because an HTTP fetch never runs
the page scripts, so a JavaScript-injected article is simply absent from what
you download. A real engine renders it, which is why extraction and
not-getting-blocked are the same problem.

**How do I know readability did not cut the article short?** Check the last
paragraph and the paragraph count against the rendered page. Inline widgets - a
pull-quote, a subscribe box, a related-links card - can look like the end of the
content and cause a silent truncation.

**How do I get the same article on every run instead of sometimes a stripped
page?** Drive it with a consistent fingerprint. Passing a fixed `seed` gives the
same coherent machine each run, so the session is served the same document
rather than an article once and a challenge the next time.

**Does readability get the byline and the date?** Not reliably. Those live
outside the main content block and are dropped more often than paragraphs.
Extract them with your own selectors rather than expecting the summary to keep
them.

## Sources

- The Playwright API for [`goto`](https://playwright.dev/python/docs/api/class-page#page-goto),
  `wait_for_selector` and [`content()`](https://playwright.dev/python/docs/api/class-page#page-content),
  used here exactly as documented upstream, since `InvisiblePlaywright` returns
  a real Playwright `Browser`.
- The `readability-lxml` package, a port of the readability algorithm, run over
  rendered HTML rather than a raw HTTP response.
- This project's own measurements comparing a naive `body` innerText against a
  readability pass on the same rendered DOM, and the rendering behaviour that
  makes JavaScript-injected body text present under a real engine and absent
  under an HTTP fetch.

**See also:** [how to scrape infinite scroll with Playwright](how-to-scrape-infinite-scroll-playwright.md)
for bodies that only load as you scroll, and [configuration](configuration.md)
for proxy and timezone handling when the same URL serves different content by
region.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The readability
step is ordinary open-source tooling; the part that matters is running it over a
DOM a real engine actually rendered.*
