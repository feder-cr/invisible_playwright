---
title: "How to scrape RSS and Atom feeds with Playwright"
description: "Scrape RSS and Atom feeds with Playwright: find the feed URL in the page, fetch the XML through the browser past the same protection, and parse both schemas."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 60
---


# How to scrape RSS and Atom feeds with Playwright

To scrape an RSS or Atom feed with Playwright, do three things in order: read the feed
URL from the page's `<link rel="alternate">` tag instead of guessing a path, navigate to
that URL in the same browser and read `page.content()` so the XML comes back past the
same protection as the page, then parse the result as XML and branch on the root element
(`<rss>` versus `<feed>`), because the two schemas share almost nothing.

A feed looks like the easy case. It is structured XML, it is meant to be read by
machines, and for years the advice was to skip the browser entirely and fetch the
URL with a plain HTTP client. That advice quietly stopped working, because the feed
endpoint now usually sits behind the same edge protection as the HTML pages it
belongs to, and a bare client fetching the XML gets a challenge instead of a
document.

This page covers the three parts people get wrong: finding the feed URL in the first
place, fetching the XML in a way that survives the protection, and parsing a format
that is really two incompatible formats wearing the same name.

## Find the feed URL from the page, do not guess it

Read the feed URL from the page itself instead of guessing a path: a site that
publishes a feed advertises it in the document head with a
[`<link rel="alternate">`](https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel)
element whose `type` is `application/rss+xml` or `application/atom+xml`.

There is no fixed path for a feed. `/rss`, `/feed`, `/atom.xml`, `/index.xml` and a
dozen others are all in use, and guessing wastes requests against exactly the kind of
endpoint that counts requests. Read that attribute instead:

```python
from urllib.parse import urljoin
from invisible_playwright import InvisiblePlaywright

FEED_SELECTOR = (
    "link[rel='alternate'][type='application/rss+xml'], "
    "link[rel='alternate'][type='application/atom+xml']"
)

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/blog")

    href = page.get_attribute(FEED_SELECTOR, "href")
    if href is None:
        raise SystemExit("no feed advertised on this page")

    # the href is often relative, so resolve it against the page URL
    feed_url = urljoin(page.url, href)
    print("feed:", feed_url)
```

`page.get_attribute` returns the first match, which is what you want when a site
advertises both an RSS and an Atom version of the same content. If you need every
feed on the page, `page.query_selector_all(FEED_SELECTOR)` gives them all and you can
read `href` from each.

## Fetch the XML through the browser, not a side client

Fetch the feed by navigating to its URL in the same browser that loaded the page and
reading [`page.content()`](https://playwright.dev/python/docs/api/class-page#page-content),
not by handing the URL to a separate HTTP client. This is the
part that trips up feed scrapers built the old way. Once you have the feed URL, the
instinct is to hand it to a separate HTTP library, because it is "just XML". On a protected site that separate request is a fresh visitor with none of the
context the browser accumulated, and it draws the challenge the page navigation did
not.

Navigate to the feed URL in the same browser instead, and read the served document
with `page.content()`:

```python
with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/blog")

    href = page.get_attribute(FEED_SELECTOR, "href")
    feed_url = urljoin(page.url, href)

    page.goto(feed_url)
    xml_text = page.content()   # the served feed, past the same edge protection
```

The browser is a real browser: a Firefox patched at the C++ level, driven by stock
Playwright, presenting a consistent TLS handshake and fingerprint that a stripped-down
client cannot. The feed comes back because the request that asked for it is
indistinguishable from the request that asked for the page. If you want to keep the
raw response bytes exactly as served rather than the serialized DOM,
[capturing the response body directly](how-to-capture-xhr-api-responses-playwright.md)
with `page.on("response")` is the companion technique, and it avoids any question of
what the browser did to the document on the way in.

One caveat worth stating plainly, because it is the honest one: whether the feed
loads at all is downstream of whether the session looks real. If the HTML site is
challenging you, the feed will challenge you too, and the fix is the same fix as for
any blocked page rather than anything feed-specific. That whole order of operations is
[the checklist for scraping without getting blocked](how-to-scrape-without-getting-blocked.md),
and it applies here unchanged.

## Parse it as XML, and branch on the schema

Parse the feed with a standard XML parser and branch on the root element: `<rss>` is an
RSS document, `<feed>` is Atom, and the two share almost nothing structurally. "RSS feed"
is a colloquial name for both, and code that assumes one silently returns nothing on the
other. The differences that matter:

- **RSS** wraps items in a `<channel>`, each item is `<item>`, the date is
  `<pubDate>` in RFC 822 format, and the body is `<description>`.
- **Atom** has no channel, each item is `<entry>`, the date is `<updated>` in
  ISO 8601 format per [RFC 4287](https://datatracker.ietf.org/doc/html/rfc4287), the
  body is `<content>` or `<summary>`, and the link is an
  attribute (`<link href="...">`) rather than element text.

So the first thing to do after parsing is look at the root element and decide which
world you are in. Do not sniff the URL or the content type, read the tag. This is the
same root-element branch used when
[scraping a sitemap.xml through the browser](how-to-scrape-a-sitemap-playwright.md),
where `<sitemapindex>` and `<urlset>` split apart the same way:

```python
import xml.etree.ElementTree as ET

ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml_text):
    root = ET.fromstring(xml_text)
    tag = root.tag.split("}")[-1]   # strip any namespace prefix

    if tag == "rss":
        channel = root.find("channel")
        return [_rss_item(node) for node in channel.findall("item")]

    if tag == "feed":   # Atom's root element
        return [_atom_entry(node) for node in root.findall(ATOM + "entry")]

    raise ValueError("unrecognized feed root element: " + tag)


def _rss_item(item):
    enclosure = item.find("enclosure")
    return {
        "title": item.findtext("title"),
        "link": item.findtext("link"),
        "date": item.findtext("pubDate"),
        "body": item.findtext("description"),   # CDATA HTML, already unwrapped
        "media": enclosure.get("url") if enclosure is not None else None,
    }


def _atom_entry(entry):
    # Atom's link is an attribute; prefer rel="alternate" for the article link
    link = None
    media = None
    for node in entry.findall(ATOM + "link"):
        rel = node.get("rel", "alternate")
        if rel == "alternate":
            link = node.get("href")
        elif rel == "enclosure":
            media = node.get("href")

    body = entry.find(ATOM + "content")
    if body is None:
        body = entry.find(ATOM + "summary")

    return {
        "title": entry.findtext(ATOM + "title"),
        "link": link,
        "date": entry.findtext(ATOM + "updated"),
        "body": body.text if body is not None else None,
        "media": media,
    }
```

Two details save the most time. Atom lives in a namespace, so every tag lookup needs
the `{http://www.w3.org/2005/Atom}` prefix, which is why the RSS branch can use bare
tag names and the Atom branch cannot. And the item body arrives as CDATA-wrapped HTML;
a standard XML parser hands you the inner text already unwrapped, so `body` is an HTML
string you may still want to strip of tags depending on what you are storing.

## Handle media, dates and the fields that are not always there

Feeds are permissive, and every field above can be absent on a given item. The parser
above uses `findtext`, which returns `None` rather than raising, and guards
`enclosure` and `content` before touching them, because a podcast item has an
`<enclosure>` and a text-only post does not.

Media specifically is the field most worth normalizing. RSS attaches it through
`<enclosure url="..." type="..." length="...">`, Atom through
`<link rel="enclosure" href="...">`, and returning both as a single `media` key means
the rest of your code does not care which schema produced the item. If you need the
media type as well, read the `type` attribute off the same node.

Dates are the other normalization worth doing at parse time rather than later. RSS
`pubDate` is [RFC 822](https://datatracker.ietf.org/doc/html/rfc822)
(`Mon, 05 Aug 2026 12:00:00 +0000`) and Atom `updated` is
ISO 8601 (`2026-08-05T12:00:00Z`). Parsing both into a single `datetime` at the edge
keeps every downstream comparison honest, the same normalize-at-the-edge approach used
for [cleaning scraped dates into UTC](how-to-clean-scraped-prices-and-dates-playwright.md):

```python
from email.utils import parsedate_to_datetime
from datetime import datetime


def to_datetime(value):
    if value is None:
        return None
    try:
        return parsedate_to_datetime(value)        # RSS: RFC 822
    except (TypeError, ValueError):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))  # Atom: ISO 8601
```

## Make the run reproducible and put it where it deploys

A feed scraper is usually a scheduled job on a server, which means it inherits the two
things that make browser automation flaky in production: a non-deterministic identity
and a proxy path you did not test.

Pin the identity. Passing `seed=` makes every fingerprint field come back identical
run after run, so when a fetch starts getting challenged you can replay the exact same
browser and tell a site change from a machine change. Set the proxy the job will
actually use, and let the timezone follow the exit rather than pinning it by hand:

```python
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/blog")

    href = page.get_attribute(FEED_SELECTOR, "href")
    feed_url = urljoin(page.url, href)

    page.goto(feed_url)
    items = parse_feed(page.content())

    for item in items:
        print(to_datetime(item["date"]), item["title"])
```

The proxy scheme and the SOCKS5 authentication details are their own topic, covered in
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md);
the important part for a scheduled feed job is that the endpoint carries an explicit
port and that you confirm the exit inside the browser rather than assuming it. A
minimal install for the container it runs in:

```bash
pip install invisible-playwright
```

The engine downloads and caches itself on first use, so the first run in a fresh
container is slower than the rest.

## Conclusion

Scraping feeds well is three separate jobs that people collapse into one. Discover the
URL from the page's `<link rel="alternate">` rather than guessing a path. Fetch the XML
through the same real browser that loaded the page, because the feed sits behind the
same edge protection and a side client draws the challenge the browser does not. And
parse defensively, branching on `rss` versus `feed` at the root and normalizing media
and dates so the rest of your code never has to know which schema it got.

The feed being XML was never the hard part. Getting the XML, and getting it from a
session that looks real enough to be served, is.

## Short answers to the questions that lead here

**Where do I find a site's RSS feed URL?** In the page head, as a
`<link rel="alternate" type="application/rss+xml">` (or `application/atom+xml`)
element. Read its `href` and resolve it against the page URL, because it is often
relative.

**Why does fetching the feed with requests get blocked when the browser works?**
Because the feed endpoint usually sits behind the same edge protection as the HTML
site, and a plain HTTP client is a different-looking visitor. Navigate to the feed in
the browser and read `page.content()` instead.

**How do I tell RSS from Atom?** Read the root element. RSS has `<rss>` with a
`<channel>` of `<item>` elements; Atom has `<feed>` with `<entry>` elements in the
Atom namespace. Branch on that, do not assume.

**Why is my parser returning empty results on some feeds?** Almost always the schema.
Code written for RSS `<item>`/`<pubDate>` finds nothing in an Atom feed of
`<entry>`/`<updated>`, and Atom's namespace means bare tag lookups miss.

**How do I get the image or audio from a feed item?** RSS uses `<enclosure url="...">`;
Atom uses `<link rel="enclosure" href="...">`. Read both into one field so the rest of
your code does not branch.

**Why is the item body full of HTML tags?** Because feed bodies are CDATA-wrapped HTML.
A standard XML parser unwraps the CDATA for you and hands back the HTML string; strip
the tags yourself if you only want text.

## Sources

- The RSS 2.0 specification and the Atom Syndication Format
  ([RFC 4287](https://datatracker.ietf.org/doc/html/rfc4287)) for the element and
  namespace differences described above, and
  [RFC 822](https://datatracker.ietf.org/doc/html/rfc822) for the date format RSS reuses.
- The [`rel="alternate"` link relation](https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/rel)
  and [`Page.content()`](https://playwright.dev/python/docs/api/class-page#page-content)
  for the feed-discovery and fetch mechanics described above.
- This project's own testing method: discover from the page, fetch through the real
  browser past the same protection, and compare against a stock browser rather than
  reading a verdict, covered in
  [how to test whether your browser is detected](how-to-test-bot-detection.md).

**See also:** [how to capture XHR and API responses](how-to-capture-xhr-api-responses-playwright.md)
for reading a page's JSON the same way, and
[how to scrape without getting blocked](how-to-scrape-without-getting-blocked.md) for
the order to work in when the feed will not load at all.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The feed-behind-the-same-wall
problem is one we hit ourselves before we wrote it down.*
