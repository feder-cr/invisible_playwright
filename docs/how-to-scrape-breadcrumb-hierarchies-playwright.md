---
title: "How to scrape breadcrumb hierarchies with Playwright"
description: "Scrape breadcrumb hierarchies with Playwright: read the trail from the markup instead of the truncated text, drop the self crumb, keep the category id from the href, and merge trails on the full prefix."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 115
---


# How to scrape breadcrumb hierarchies with Playwright

**To scrape breadcrumb hierarchies with Playwright, pull the trail out of the markup with
`text_content()` instead of the rendered text, keep each crumb's `href` because it usually
carries the category id the label does not, drop the final crumb because it is the page and
not a category, and merge the trails from many leaf pages on their full prefix rather than
on the last segment.** Do that and a few hundred product pages hand you the site's category
tree without fetching a single category page.

Breadcrumbs are the cheapest structural data a site gives away. Every leaf page carries its
own path from the root, already ordered, already labelled, and usually tied to the ids the
site uses internally. Collect enough leaves and the tree reassembles itself. The alternative
is walking every category page and paginating each one, which costs far more requests to
learn the same shape and only shows you the branches the navigation menu chooses to expose.

The catch is that a trail is not a fact about the page. It is a fact about the visit. The
same product reached from two departments shows two different trails, and the visible text
is frequently not the whole trail to begin with. Both failures are quiet: the parse succeeds,
the rows look fine, and the tree built from them is wrong in a way no exception reports.

## Why leaf pages are a cheaper tree than category pages

A category crawl needs one request per node plus pagination on each node, and it discovers
only what is linked. Unlinked, seasonal or deprecated categories still appear in the
breadcrumbs of the products that sit inside them, so a leaf sweep finds branches a menu
crawl cannot reach.

The complement is a URL list. A sitemap gives you addresses with no hierarchy at all, while
breadcrumbs give hierarchy but only for the leaves you actually fetch. Pairing them is the
usual shape of this job: take the leaf URLs from
[the sitemap](how-to-scrape-a-sitemap-playwright.md), then read one trail per leaf. Coverage
then depends on picking leaves that spread across the site rather than a thousand products
from the same department.

## Read the markup, not the rendered text

On a narrow layout the middle of a trail collapses. The site rarely deletes those crumbs. It
hides them in CSS and paints an ellipsis in their place, so the reader sees
`Home / ... / Blue running shoes` while the full path sits in the DOM untouched.

This is where the choice of call decides the outcome. `inner_text()` returns `element.innerText`,
which is rendered text and therefore skips anything CSS has hidden. `text_content()` returns
`node.textContent`, which ignores styling completely and gives back the hidden crumbs. Same
element, same page, two different answers, and only one of them is the data.

```python
from invisible_playwright import InvisiblePlaywright

CRUMB_JS = """
() => {
  const root = document.querySelector(
    'nav[aria-label*="readcrumb" i], [class*="breadcrumb"], ol[itemtype*="BreadcrumbList"]'
  );
  if (!root) return [];
  const items = root.querySelectorAll('li');
  const nodes = items.length ? items : root.querySelectorAll('a');
  return [...nodes].map(el => {
    const a = el.matches('a') ? el : el.querySelector('a');
    return {
      // textContent, not innerText: CSS-collapsed crumbs are still in here
      text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
      href: a ? a.href : null,                 // resolved absolute by the DOM
      current: el.getAttribute('aria-current') === 'page'
               || (a && a.getAttribute('aria-current') === 'page'),
    };
  });
}
"""

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/p/12345", wait_until="domcontentloaded")
    crumbs = page.evaluate(CRUMB_JS)
```

Where the remedy stops: some pages measure the container in JavaScript and remove the middle
crumbs from the DOM instead of hiding them. `text_content()` cannot recover a node that no
longer exists, and the only fix there is a wide viewport set before navigation so the collapse
never triggers. Check which kind you have by counting crumbs at two widths before writing a
parser around either one.

## Take the structured trail when the page ships one

Many pages publish the same path as a `BreadcrumbList` in a `application/ld+json` block, and
that copy is better than the DOM in two ways: it is not styled, so nothing can be hidden from
it, and each entry carries an explicit `position`. Read the position and sort on it. The array
order is not guaranteed and some templates emit it reversed.

```python
import json

def crumb_from_list_item(it):
    item = it.get("item")
    if isinstance(item, str):
        name, href = it.get("name", ""), item
    elif isinstance(item, dict):
        name, href = it.get("name") or item.get("name", ""), item.get("@id")
    else:
        name, href = it.get("name", ""), None    # no item at all: this entry is the page
    return {"text": name, "href": href, "current": item is None}

def breadcrumb_from_ld(page):
    for handle in page.query_selector_all('script[type="application/ld+json"]'):
        raw = handle.text_content()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blocks = data if isinstance(data, list) else [data]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for node in block.get("@graph", [block]):
                types = node.get("@type", "")
                types = types if isinstance(types, list) else [types]
                if "BreadcrumbList" not in types:
                    continue
                items = sorted(node.get("itemListElement", []),
                               key=lambda it: it.get("position", 0))
                return [crumb_from_list_item(it) for it in items]
    return None
```

The full mechanics of pulling typed nodes out of a page, including the `@graph` wrapper that
holds several unrelated records, are in
[extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md).
One caveat belongs here though: the structured trail is usually the site's canonical path,
not the one the visitor saw. That makes it right for tree building and wrong if you wanted
to know how someone arrived.

## The last crumb is the page, not a category

Almost every trail ends with the current page's own title. Keep it and you invent a level:
every product becomes a category with exactly one child, the tree grows one row per item, and
depth counts stop meaning anything. The page announces which crumb this is, in at least four
ways, and any one of them is enough.

| Signal | What it looks like in the markup |
|---|---|
| No anchor | the final `li` holds a `span` or bare text, never an `a` |
| `aria-current="page"` | set on the last crumb by the standard breadcrumb pattern |
| Self-referencing href | after resolution it equals the current URL without the query |
| No `item` in the JSON-LD | the last `ListItem` carries a `name` and nothing else |

Drop on any of those, and drop the missing-anchor case only when the crumb is last. Plenty of
sites render the root as unlinked text partway through, and a blanket "no href means skip"
rule quietly removes real ancestors.

## The href carries the id the label does not

Labels are display strings. They get renamed, translated, capitalised differently between
templates, and reused across branches. The href is the stable key, and on many leaf pages the
trail is the only place a parent category id appears at all, because the page's own URL
carries the product id and nothing above it.

```python
import re
from urllib.parse import urljoin, urlparse

CATEGORY_ID = re.compile(r"/c/(\d+)|[?&](?:cat|node|categoryId)=([^&]+)")

def canonical(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"

def category_key(href):
    if not href:
        return None
    m = CATEGORY_ID.search(href)
    if m:
        return m.group(1) or m.group(2)
    return urlparse(href).path.rstrip("/") or None

def normalize(crumbs, page_url):
    here, trail = canonical(page_url), []
    for i, c in enumerate(crumbs):
        href = urljoin(page_url, c["href"]) if c["href"] else None
        last = i == len(crumbs) - 1
        if c["current"] or (href and canonical(href) == here) or (last and href is None):
            continue                       # the page itself is not a level of the tree
        trail.append({"label": c["text"], "key": category_key(href), "href": href})
    return trail
```

Key the tree on `key` and carry `label` alongside it. When two branches both call a node
"Accessories" the ids keep them apart, and when a label is retitled next quarter the tree does
not fork. The href normalisation here is the same problem as building
[a crawl frontier](how-to-extract-links-crawl-frontier-playwright.md), and for the same reason:
a raw attribute is not an identity until it has been resolved and stripped.

## The same product, two different trails

Breadcrumbs are often contextual. Reach a product from one department and the trail names that
department; reach the identical product from another and the trail names the other one. The
site decides from the referrer or from a parameter it put on the link, then paints the trail
to match. Nothing about the product changed.

That makes a trail a property of the visit. Merging trails collected under different entry
paths builds a tree where one leaf hangs under three parents, which is not what the site
believes and not something a later query can untangle. Test whether a site does this before
trusting a single row of your output.

```python
LEAF = "https://example.com/p/12345"
CATEGORY = "https://example.com/c/847"

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()

    page.goto(LEAF, wait_until="domcontentloaded")          # cold: no referrer at all
    cold = normalize(page.evaluate(CRUMB_JS), page.url)

    page.goto("about:blank")
    page.goto(LEAF, referer=CATEGORY, wait_until="domcontentloaded")
    warm = normalize(page.evaluate(CRUMB_JS), page.url)

    if [c["key"] for c in cold] != [c["key"] for c in warm]:
        print("contextual breadcrumbs: the trail follows the entry path, not the product")
```

If the two disagree, pick one and hold it for the whole run. For tree building, take the cold
visit: a direct navigation to the canonical URL with no referrer gets the site's default path.
For anything about visitor journeys, record the entry path in the same row as the trail so the
two never get averaged together. The trap sits in the ordinary crawl shape, because
[clicking through from list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
sends a referrer every time, so every trail you gather is contextual and each one looks correct
on its own.

## Merge on the full prefix, never on the last segment

Leaf names repeat across branches. Two departments both have "Accessories", "Sale" and
"New in", and grouping rows by their last crumb welds those into one node with several parents.
The merge has to walk the trail from the root and descend one level per crumb, so that two
identical names under different ancestors land in different child dictionaries and never meet.

```python
def merge_trails(trails):
    """Each trail is root-first. Nodes are created per prefix, never per name."""
    root = {}
    for trail in trails:
        node = root
        for crumb in trail:
            key = crumb["key"] or crumb["label"]
            child = node.setdefault(key, {"label": crumb["label"], "href": crumb["href"],
                                          "leaves": 0, "children": {}})
            child["leaves"] += 1
            node = child["children"]
    return root

def flatten(node, prefix=()):
    for key, child in node.items():
        path = prefix + (child["label"],)
        yield {"depth": len(path), "path": " > ".join(path),
               "key": key, "leaves": child["leaves"]}
        yield from flatten(child["children"], path)
```

`flatten()` gives one row per category with its depth, its full path as a single string and how
many leaves reached it. That path string is the column to store and index, because it is the
only value that is unique across the whole tree. Store the parent key next to it and the rows
answer both "what is under this node" and "what is this node's ancestry" without a recursive
query, which is the shape that survives loading
[into a database](how-to-scrape-into-a-database-playwright.md).

## One identity across a wide leaf sweep

Recovering a tree means fetching leaves from every corner of the site, which is a request
pattern built to stand out: one address hitting products across dozens of unrelated departments
in a few minutes. A seed-stable fingerprint keeps that sweep reading as one visitor rather than
a new machine per page, since `seed=42` produces the same GPU, canvas, audio and font profile on
every request in the run.

There is a second consistency requirement specific to this job. Because the trail depends on how
you arrived, a run where half the visits carry a referrer and half do not is sampling two
different populations, and the merged tree blends the two into nonsense. Fix the entry mode
once, at the top of the run, and apply it to every leaf.

```python
import random

def build_tree(leaf_urls, seed=42):
    rng = random.Random(seed)
    trails = []
    with InvisiblePlaywright(seed=seed) as browser:
        page = browser.new_page()
        for url in leaf_urls:
            page.goto(url, wait_until="domcontentloaded")   # direct, never clicked
            crumbs = breadcrumb_from_ld(page) or page.evaluate(CRUMB_JS)
            trail = normalize(crumbs, page.url)
            if trail:
                trails.append(trail)
            page.wait_for_timeout(rng.randint(600, 2400))
    return merge_trails(trails)
```

Seeding `random.Random` from the same value passed to the browser makes identity and rhythm one
reproducible thing, so a run that produced a strange tree can be replayed exactly. Prefer the
structured trail when it is present and fall back to the DOM, rather than picking one source per
site: page templates differ across a catalogue, and
[product pages](how-to-scrape-ecommerce-product-pages-playwright.md) in an older section often
ship neither the same markup nor the same schema block.

## Conclusion

A breadcrumb trail is a path, and treating it as anything less is where these scrapers go wrong.
Read it from the markup so a CSS collapse cannot cost you the middle of the path, prefer the
`BreadcrumbList` copy when the page ships one, and cut the final crumb because it names the page
rather than a category. Keep the href, since it usually holds the only category id the leaf
exposes. Then merge on the full prefix, never on the last name, and hold the entry mode steady
across the run so contextual trails do not blend two different answers into one tree. The parsing
is a morning's work. Knowing which trail you collected is what decides whether the tree is real.

## Short answers to the questions that lead here

**Why is my breadcrumb text missing the middle of the path?** The layout collapsed it and put an
ellipsis there. The crumbs are still in the DOM, hidden by CSS, and `inner_text()` skips hidden
text while `text_content()` returns it. Switch calls before you touch the selector.

**Should I read the DOM or the JSON-LD?** Read the `BreadcrumbList` when it exists, sorted by
`position`, and fall back to the DOM. Keep both paths in the same scraper, because templates
differ across one catalogue.

**Why does the same product show a different trail on different runs?** The trail is contextual
and follows how you arrived, usually the referrer or a parameter on the link. Go straight to
the canonical URL with no referrer and you get the site's default path.

**Do I keep the last crumb?** No. It is the page itself, not a category, and keeping it adds a
fake level with one child per product. Drop it on `aria-current="page"`, on a self-referencing
href, or on a missing `item` in the structured list.

**Why does my tree have one node with several parents?** You merged on the last crumb instead of
the full prefix. Leaf names repeat across branches, so descend one level per crumb from the root
and let each prefix own its own children.

**How many leaves do I need to recover the tree?** Enough to touch every branch, not enough to
touch every product. Spread the sample across departments, since a thousand leaves from one
department reconstruct one department.

## Sources

- Playwright's [`text_content`](https://playwright.dev/python/docs/api/class-locator#locator-text-content)
  and [`inner_text`](https://playwright.dev/python/docs/api/class-locator#locator-inner-text),
  retrieved 2026-08-28: the first returns `node.textContent`, the second returns rendered
  `element.innerText`, which is why a CSS-collapsed trail reads differently through each.
- Playwright's [`page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto),
  retrieved 2026-08-28, whose `referer` option is what lets you reproduce a contextual trail on
  demand instead of guessing at it.
- Playwright's [`page.evaluate`](https://playwright.dev/python/docs/api/class-page#page-evaluate)
  and [`query_selector_all`](https://playwright.dev/python/docs/api/class-page#page-query-selector-all),
  retrieved 2026-08-28, used exactly as documented upstream: the browser here is a real
  Playwright `Browser`.
- The schema.org `BreadcrumbList` and `ListItem` types, which define `position`, `name` and
  `item` and permit the final entry to omit `item`.

**See also:** [extracting JSON-LD structured data](how-to-extract-json-ld-structured-data-playwright.md)
for the structured copy of the trail, [extracting links and building a crawl frontier](how-to-extract-links-crawl-frontier-playwright.md)
for resolving and keying the hrefs, [crawling list pages to detail pages](how-to-crawl-list-to-detail-pages-playwright.md)
for the click-through shape that makes every trail contextual, and
[scraping a sitemap](how-to-scrape-a-sitemap-playwright.md) for the leaf URLs to feed the sweep.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The contextual trail is the one
that shipped wrong here: a tree built by clicking cards out of category listings, where every
trail agreed with the listing it came from, and the same leaf sat under three parents before
anyone checked.*
