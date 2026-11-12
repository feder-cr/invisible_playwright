---
title: "How to scrape microdata and RDFa markup with Playwright"
description: "The microdata markup is supported and its DOM API is gone: walk [itemscope] and [itemprop] with page.evaluate, apply the nine-case value rule instead of textContent, resolve itemref, and read RDFa Lite's five separate attributes."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 116
---


# How to scrape microdata and RDFa markup with Playwright

To scrape microdata and RDFa markup with Playwright, walk the DOM yourself: select
every element carrying `itemscope` that does not also carry `itemprop`, gather its
properties including the ones `itemref` pulls in from elsewhere in the document, and
read each property through the nine-case value table instead of `textContent`, because
seven of those nine cases put the value in an attribute. RDFa needs the same traversal
against a completely different set of five attributes: `vocab`, `typeof`, `property`,
`resource` and `prefix`.

There is no shortcut call, and that is the whole difficulty. JSON-LD is one
`querySelectorAll` and a `json.loads`. Microdata looks like it should be comparable,
because the HTML specification once defined a DOM API for this exact job. That API is
gone from every engine while the markup it read is still supported and still published.

So the work splits in two: know what the five attributes mean, then implement the value
rules by hand. Get the second half wrong and nothing throws, you just collect empty
strings where the prices were.

## Microdata is not deprecated, its DOM API is

Google's own documentation still lists all three formats. The introduction to
structured data on developers.google.com (updated 2025-12-10, retrieved 2026-08-28)
names JSON-LD, Microdata and RDFa as supported and says all three are equally fine for
Google, with JSON-LD merely recommended. The structured data policies page, updated
2026-07-10, carries the same list. Neither page deprecates the markup.

What died is a different object with a similar name. Mozilla bug 909633, "Remove HTML
Microdata API", is RESOLVED FIXED, and the removal shipped in Firefox 49. Chrome never
shipped that API at all. The section defining it is gone from the WHATWG
specification. `document.getItems()` therefore does not exist in any browser Playwright
drives, and the per-element helpers went with it.

That is the confusion in one sentence: the markup is alive and the JavaScript for
reading it is gone. Conflating the two is the error in circulation. The one thing
Google does call obsolete is neither of them, it is data-vocabulary.org markup.

## The five attributes, and why itemref breaks a subtree parse

Five attributes, one job each.

| attribute | what it does |
|---|---|
| `itemscope` | starts a new item on this element |
| `itemtype` | the item's type, given as one or more URLs |
| `itemid` | a global identifier for the item |
| `itemprop` | marks the element as a property, and the name can be a space-separated list |
| `itemref` | space-separated element IDs whose subtrees also supply properties to this item |

`itemref` is the one that changes your architecture. It lets an item claim properties
from elements that are not its descendants, so `card.query_selector_all('[itemprop]')`
is not a parse of that card. It is a parse of a document that happens not to use
`itemref`. The crawl starts at the item element, adds the referenced elements by ID,
and runs against the document rather than a detached subtree.

Two more details cost a re-run each. An element carrying both `itemscope` and
`itemprop` is a nested item, and its descendants belong to that inner item, so the walk
stops descending there. That rule is why a
[breadcrumb trail](how-to-scrape-breadcrumb-hierarchies-playwright.md) in microdata
comes out as a chain instead of a soup. And `itemprop="name alternateName"` declares
two properties with one value, so a name maps to a list.

## The value table that textContent gets wrong

The value of a property is decided by the first case that matches the element. This is
the table, identical in the WHATWG microdata section and on MDN's `itemprop` page, both
retrieved 2026-08-28.

| condition | value read from |
|---|---|
| element has `itemscope` | the nested item itself |
| `meta` | `content` attribute |
| `audio`, `embed`, `iframe`, `img`, `source`, `track`, `video` | `src`, parsed as URL |
| `a`, `area`, `link` | `href`, parsed as URL |
| `object` | `data` attribute |
| `data` | `value` attribute |
| `meter` | `value` attribute |
| `time` | `datetime` value |
| anything else | descendant text content |

Seven of those nine rows take the value out of an attribute. `textContent` sees none of
them, and it fails quietly.

`<meta itemprop="price" content="42">` holds no text at all, so naive extraction hands
you an empty string where the price was. `<time itemprop="datePublished"
datetime="2026-01-01">Jan 1st</time>` hands you `Jan 1st`, the string written for a
human, instead of the ISO date the markup exists to publish. Every `img` returns
nothing and every `a` returns its link text where a URL belongs. On a
[product page](how-to-scrape-ecommerce-product-pages-playwright.md) that is the price,
the image and the canonical link, all wrong, none of them raising.

## Read one property, by the table

The value function is small and it is the part worth getting exactly right.

```python
# Both JS chunks are raw strings. \s is not a Python escape, so a plain string
# makes the interpreter complain long before the regex reaches the browser.

VALUE_FN = r"""
  const URL_SRC = new Set(['AUDIO', 'EMBED', 'IFRAME', 'IMG', 'SOURCE', 'TRACK', 'VIDEO']);
  const URL_HREF = new Set(['A', 'AREA', 'LINK']);

  function propValue(el, seen) {
    if (el.hasAttribute('itemscope')) return readItem(el, seen);   // a nested item
    const tag = el.tagName;
    if (tag === 'META') return el.getAttribute('content') || '';
    if (URL_SRC.has(tag)) return el.src || '';       // IDL src, already absolute
    if (URL_HREF.has(tag)) return el.href || '';     // IDL href, already absolute
    if (tag === 'OBJECT') return el.data || '';
    if (tag === 'DATA' || tag === 'METER') return el.getAttribute('value') || '';
    if (tag === 'TIME') return el.dateTime || el.textContent.trim();
    return el.textContent.trim();
  }
"""
```

Three lines are less obvious than they look. The IDL properties `src`, `href` and
`data` already return absolute URLs, which is what "parsed as URL" asks for, so you
want `el.src` and not `el.getAttribute('src')`. `<meter>` is the reverse: its `value`
IDL property is a number, so `getAttribute('value')` returns the string the table
names. A `<time>` with no `datetime` attribute takes its value from its own text, so
that fallback is correct rather than lazy.

The `seen` set is threaded through because a nested item is itself a value, and two
items can point at each other through `itemref`. The walker below owns it.

## Walk the items, itemref included

```python
from invisible_playwright import InvisiblePlaywright

WALK_FN = r"""
  function properties(root, seen) {
    const results = [];
    const memory = new Set([root]);
    const pending = Array.from(root.children);
    const refs = (root.getAttribute('itemref') || '').split(/\s+/).filter(Boolean);
    for (const id of refs) {
      const target = root.ownerDocument.getElementById(id);
      if (target) pending.push(target);      // properties from outside the subtree
    }
    while (pending.length) {
      const el = pending.shift();
      if (memory.has(el)) continue;          // an itemref can point back at us
      memory.add(el);
      if (!el.hasAttribute('itemscope')) pending.push(...el.children);
      if ((el.getAttribute('itemprop') || '').trim()) results.push(el);
    }
    return results.sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
  }

  function readItem(el, seen) {
    if (seen.has(el)) return null;           // two items referencing each other
    seen.add(el);
    const item = {
      type: (el.getAttribute('itemtype') || '').split(/\s+/).filter(Boolean),
      id: el.getAttribute('itemid') || null,
      properties: {},
    };
    for (const prop of properties(el, seen)) {
      const value = propValue(prop, seen);
      const names = prop.getAttribute('itemprop').split(/\s+/).filter(Boolean);
      for (const name of names) {            // one element, several property names
        if (!item.properties[name]) item.properties[name] = [];
        item.properties[name].push(value);
      }
    }
    return item;
  }
"""

MICRODATA_JS = "() => {" + VALUE_FN + WALK_FN + r"""
  return Array.from(document.querySelectorAll('[itemscope]'))
    .filter(el => !el.hasAttribute('itemprop'))     // nested items are properties
    .map(el => readItem(el, new Set()));
}"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/product/some-item")
    items = page.evaluate(MICRODATA_JS)
```

The property crawl is the specification's algorithm with shorter names. Start from the
item element's children, add every element `itemref` names, walk breadth-first, refuse
to descend into a nested `itemscope`, and remember what you visited so a reference
pointing back terminates instead of looping. The sort restores tree order, which the
`itemref` additions destroy.

The top-level filter is the other half of the shape. An `[itemscope]` element that also
carries `itemprop` is somebody else's property, so mapping over every `[itemscope]`
without that filter reports nested items twice: once inside their parent, once as
roots. The chunks are separate strings only for readability; `page.evaluate` takes one
expression, so they are concatenated.

## RDFa Lite is five different attributes

RDFa Lite, a W3C Recommendation retrieved 2026-08-28, covers the same ground with an
entirely different set of attributes. `vocab` sets the default vocabulary for a subtree,
so a bare `property="name"` resolves against it. `typeof` starts a subject. `property`
marks a property. `resource` names the thing being described. `prefix` declares the
short names that make `property="og:title"` mean something, which is why
[Open Graph metadata](how-to-extract-open-graph-metadata-playwright.md) turns up inside
RDFa markup as well as in plain `<meta>` tags.

None of the microdata rules carry over, so a parser written for one reads nothing from
the other. What a scraper usually wants is flat name and value pairs, and that is a
short walk.

```python
RDFA_JS = r"""
() => {
  function value(el) {
    if (el.hasAttribute('content')) return el.getAttribute('content');
    if (el.hasAttribute('resource')) return el.getAttribute('resource');
    if (el.hasAttribute('href')) return el.href;
    if (el.hasAttribute('src')) return el.src;
    return el.textContent.trim();
  }

  function subjectOf(el) {                     // the nearest enclosing typeof
    return el.parentElement ? el.parentElement.closest('[typeof]') : null;
  }

  return Array.from(document.querySelectorAll('[typeof]')).map(subject => {
    const vocabHolder = subject.closest('[vocab]');
    const out = {
      typeof: subject.getAttribute('typeof'),
      vocab: vocabHolder ? vocabHolder.getAttribute('vocab') : null,
      resource: subject.getAttribute('resource') || null,
      properties: {},
    };
    for (const el of subject.querySelectorAll('[property]')) {
      if (subjectOf(el) !== subject) continue;  // belongs to a nested typeof
      const names = el.getAttribute('property').split(/\s+/).filter(Boolean);
      for (const name of names) {
        if (!out.properties[name]) out.properties[name] = [];
        out.properties[name].push(value(el));
      }
    }
    return out;
  });
}
"""

rdfa = page.evaluate(RDFA_JS)
```

Say where that stops. It collects pairs. It does not resolve `prefix` or `vocab` into
full IRIs and it does not emit triples, so `og:title` comes back as the literal string
with the vocabulary reported beside it. If you need real RDF out of the document, run a
conformant RDFa processor over the HTML rather than growing the function above.

## JSON-LD costs one selector, microdata costs a traversal

This argument survives whatever any search engine prefers. JSON-LD is one
`querySelectorAll('script[type="application/ld+json"]')` and a parse. The block is
self-contained, so its position in the document is irrelevant and you can read it out of
raw HTML with no DOM at all. Microdata and RDFa are attributes scattered across a
rendered tree, and reading them correctly costs a traversal, a per-element value rule,
and an `itemref` resolution that is document-scoped by definition. Same information,
two very different extraction costs. So when a page ships both, take
[the JSON-LD](how-to-extract-json-ld-structured-data-playwright.md) and keep the
traversal as the fallback.

What happens when a page carries two formats describing the same entity is not settled
by any primary source. Google's structured data policies page does not address
combining formats, and the widely repeated claim that Google will not merge attributes
across formats has no primary source behind it. Do not build on it. Keep the three
extractions apart, label which format each field came from, and make the merge a
decision in your own code.

```python
def collect(page):
    """Keep the three formats apart and label where every field came from."""
    return {
        "json_ld": page.locator(
            'script[type="application/ld+json"]').all_text_contents(),
        "microdata": page.evaluate(MICRODATA_JS),
        "rdfa": page.evaluate(RDFA_JS),
    }


def pick(sources, order=("json_ld", "microdata", "rdfa")):
    """First populated source wins, and the caller learns which one it was."""
    for name in order:
        if sources.get(name):
            return name, sources[name]
    return None, None
```

## Where this stops helping

Three limits, and the first one eats an afternoon.

The elements holding microdata values are frequently invisible. `<meta itemprop>` and
`<link itemprop>` never render, so any Playwright call that waits for visibility waits
forever on markup that is perfectly present. Wait for attachment instead, and read
values with `get_attribute()` and `text_content()`, neither of which runs a visibility
check.

```python
page.goto(url, wait_until="domcontentloaded")

# state="attached", not the default "visible": <meta itemprop> never renders, so a
# visibility wait on structured markup times out on a completely healthy page.
page.wait_for_selector(
    "[itemscope], [typeof], script[type='application/ld+json']",
    state="attached",
    timeout=10000,
)
```

Second, this reads the rendered DOM, so the attributes have to exist when you read. A
page that builds its markup client-side has no `[itemscope]` in the initial HTML, and
the wait above is what makes that deterministic. That is a different failure from markup
absent because the page you received was never the real one, which
[scraping without getting blocked](how-to-scrape-without-getting-blocked.md) deals with.

Third, the table tells you what the markup means according to the specification, not
what a consumer extracted from it. Google documents which formats it accepts and does
not publish its extraction algorithm, so whether its parser applies the WHATWG value
table exactly is not something anyone outside can assert. Extract to the spec, and treat
what a search engine ingested as a separate question.

## Conclusion

The sentence worth retiring is "microdata is deprecated". The markup is supported and
still published; the DOM API that read it is gone from every engine; only one of those
two facts is what people mean. What follows is concrete. There is no shortcut call, so
you walk `[itemscope]` yourself, you resolve `itemref` at document scope instead of
inside a card, and you read every property through the value table because seven of its
nine rows sit in an attribute where `textContent` finds nothing. RDFa gets the same
treatment against five different attributes. And when the page ships JSON-LD too, take
it: one selector beats a traversal.

## Short answers to the questions that lead here

**Is microdata deprecated?** No. Google's structured data documentation still lists
JSON-LD, Microdata and RDFa as supported and calls all three equally fine, with JSON-LD
recommended. What was removed is the Microdata DOM API, and the two get conflated.

**Why is document.getItems() undefined?** Because that API was removed. Mozilla bug
909633 took it out at Firefox 49, Chrome never shipped it, and the section is gone from
the WHATWG specification. The markup stayed; the reader disappeared.

**Can I read textContent from every [itemprop]?** No. Seven of the nine value cases read
an attribute, so a `meta` price comes back empty and a `time` gives you the human label
instead of the ISO date, with nothing raising.

**Why does my per-card parse miss properties?** Most likely `itemref`. It lets an item
take properties from elements that are not its descendants, so a crawl scoped to one
card's subtree is incomplete on any document that uses it.

**Does RDFa work the same way?** No. It is a separate specification with five different
attributes: `vocab`, `typeof`, `property`, `resource` and `prefix`. A microdata parser
reads nothing from RDFa markup, and the value rules do not transfer.

**A page has JSON-LD and microdata for the same entity. Which wins?** Read the JSON-LD,
because it costs one selector rather than a traversal. Whether any consumer merges the
two has no primary source, so keep the extractions separate and decide in your own code.

## Sources

- Google, [Introduction to structured data markup](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data),
  updated 2025-12-10, retrieved 2026-08-28: JSON-LD, Microdata and RDFa are all
  supported, with JSON-LD recommended.
- Google, [structured data general policies](https://developers.google.com/search/docs/appearance/structured-data/sd-policies),
  updated 2026-07-10, retrieved 2026-08-28, which names data-vocabulary.org markup as
  the format no longer supported.
- WHATWG HTML, [the microdata section](https://html.spec.whatwg.org/multipage/microdata.html),
  retrieved 2026-08-28: the five attributes, the property crawl including `itemref`, and
  the value table above.
- MDN, the `itemprop` global attribute, retrieved 2026-08-28, whose value rules match
  the WHATWG table case for case.
- Mozilla, [bug 909633 "Remove HTML Microdata API"](https://bugzilla.mozilla.org/show_bug.cgi?id=909633),
  RESOLVED FIXED, shipped in Firefox 49, retrieved 2026-08-28.
- W3C, [RDFa Lite 1.1](https://www.w3.org/TR/rdfa-lite/), Recommendation, retrieved
  2026-08-28: `vocab`, `typeof`, `property`, `resource` and `prefix`.
- Playwright's [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate),
  [`page.wait_for_selector`](https://playwright.dev/python/docs/api/class-page#page-wait-for-selector)
  and [`all_text_contents`](https://playwright.dev/python/docs/api/class-locator#locator-all-text-contents),
  retrieved 2026-08-28 and used as documented upstream, because the browser this library
  returns is a real Playwright `Browser`.

**See also:** [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the cheap path, [extracting Open Graph metadata](how-to-extract-open-graph-metadata-playwright.md)
for the `meta` tags beside this markup, [scraping breadcrumb hierarchies](how-to-scrape-breadcrumb-hierarchies-playwright.md)
for nested items in practice, and [scraping e-commerce product pages](how-to-scrape-ecommerce-product-pages-playwright.md)
where the price and image cases bite hardest.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. An early version of this
extractor read `textContent` off every `[itemprop]` and produced rows where each price
was an empty string and each date was a human label. Nothing errored and the row count
was right, which is why it survived as long as it did.*
