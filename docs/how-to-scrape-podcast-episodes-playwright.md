---
title: "How to scrape podcast episode listings with Playwright"
description: "Scrape podcast episode listings with Playwright: find the RSS feed behind the player, key episodes on the guid, and parse durations and episode numbers that arrive in several shapes."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 126
---


# How to scrape podcast episode listings with Playwright

To scrape podcast episode listings with Playwright, find the show's RSS feed instead of
reading the player: take it from the `<link rel="alternate" type="application/rss+xml">`
tag or out of the player's own configuration object, open that URL in the same browser,
then key every episode on its `<guid>` and treat the audio URL, the byte length and the
duration as values that can differ between two fetches of the same episode.

A podcast page looks like a list and is really two datasets wearing one layout. The
player you can see is a virtualised list holding recent episodes and nothing else,
rebuilt as you scroll. The feed behind it carries the archive, in machine-readable form,
in a single request. Scraping the visible list gets you a slice of a dataset you could
have had whole, and it does that silently: nothing errors, the rows simply stop.

This page is about the parts a generic feed reader gets wrong on podcasts. Finding the
feed when there is no link tag. Picking an identifier that survives the show changing
host. Parsing a duration that arrives as an integer in one item and as `01:04:12` in the
next. And where the approach stops, which is at a private feed carrying a per-listener
token.

## Find the feed behind the player before writing a selector

The feed URL is the highest-value thing on the page, because finding it collapses the
whole job into one request against a document that already holds every field you want.
Look in two places, in that order.

First the document head, where a show that publishes a feed advertises it as
`<link rel="alternate" type="application/rss+xml">`. Second, and this is the part
generic advice skips, the player's own configuration. Plenty of players are
client-rendered apps that ship no link tag at all, and the feed URL sits in an inline
script blob or on a data attribute of the player element.

```python
import re
from urllib.parse import urljoin
from invisible_playwright import InvisiblePlaywright

FEED_LINK = (
    "link[rel='alternate'][type='application/rss+xml'], "
    "link[rel='alternate'][type='text/xml']"
)
FEED_IN_SCRIPT = re.compile(r"""https?://[^"'\s]+?(?:\.xml|/rss|/feed)[^"'\s]*""")


def find_feed_url(page):
    # query_selector returns None at once. get_attribute() would WAIT for the
    # selector and raise a timeout, and a missing link tag is the common case.
    node = page.query_selector(FEED_LINK)
    if node:
        return urljoin(page.url, node.get_attribute("href"))

    for handle in page.query_selector_all("script"):
        text = handle.text_content() or ""
        if "rss" not in text and "feed" not in text:
            continue
        found = FEED_IN_SCRIPT.search(text)
        if found:
            return found.group(0)

    player = page.query_selector("[data-feed-url]")
    return player.get_attribute("data-feed-url") if player else None


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/show/example", wait_until="networkidle")

    feed_url = find_feed_url(page)
    if not feed_url:
        raise SystemExit("no feed advertised; the rendered list is all there is")

    page.goto(feed_url)
    xml_text = page.content()
```

Two notes on that fetch. Firefox draws a bare XML document through its own pretty-print
viewer, so `page.content()` can hand back viewer markup wrapped around the document; if
the string does not begin with `<?xml` or `<rss`, take the served bytes from the response
instead, which is the technique in
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md). And
the generic side of this, including the Atom branch a podcast feed almost never needs,
is in [scraping RSS and Atom feeds](how-to-scrape-rss-atom-feeds-playwright.md).

## The player shows a window, the feed holds the archive

The visible episode list is virtualised. It is not a short list you can scroll to the end
of, it is a fixed pool of DOM nodes recycled as the viewport moves, so rows that scroll
out of view are removed from the document entirely.

Watch the count and the shape of the problem is obvious. `page.locator(".episode-row").count()`
sits near constant while the scroll position travels, because the page destroys as many
rows as it creates. A scraper that scrolls and reads at the end collects the last window
and calls it the show. A scraper that reads on every scroll step collects whatever
survived between two reads, with gaps it cannot see. The mechanics of doing this properly,
when there is no feed to fall back on, are in
[scraping virtual scrolling tables](how-to-scrape-virtual-scrolling-tables-playwright.md).

The feed has no window. It carries every item the host publishes, oldest to newest, in
one document. The honest caveat is that some hosts cap a feed at the most recent N items
by a setting on the show, so compare the item count in the XML against any total the page
displays instead of assuming the archive is complete. A capped feed is still a longer
list than the player, and the cap is visible; the player's truncation is not.

## Parse the core fields and the podcast namespace together

A podcast feed is RSS 2.0 with a second namespace bolted on, and the fields worth having
are split across both. The core RSS elements carry identity, the audio file and the date.
The `itunes:` namespace carries everything a podcast has and a blog post does not. Atom
barely appears here, because `<enclosure>` is an RSS element and hosts emit RSS.

| Field | Where it lives |
|---|---|
| Episode identity | `<guid>`, core RSS |
| Audio file, size, MIME type | `<enclosure url= length= type=>`, core RSS |
| Publication date | `<pubDate>`, RFC 822, core RSS |
| Duration | `<itunes:duration>`, a free-form string |
| Season and episode number | `<itunes:season>`, `<itunes:episode>`, both optional |
| Episode type | `<itunes:episodeType>`: full, trailer or bonus |
| Show notes | `<content:encoded>`, falling back to `<description>` |

```python
import xml.etree.ElementTree as ET

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


def parse_episodes(xml_text):
    channel = ET.fromstring(xml_text).find("channel")
    if channel is None:
        raise ValueError("not an RSS document: no <channel> element")
    return [_episode(item) for item in channel.findall("item")]


def _episode(item):
    enclosure = item.find("enclosure")
    guid = item.find("guid")
    return {
        "guid": guid.text if guid is not None else None,
        # isPermaLink defaults to true when the attribute is absent, which is
        # a claim about the string, not a promise that it resolves.
        "guid_is_permalink": guid is not None and guid.get("isPermaLink", "true") == "true",
        "title": item.findtext("title"),
        "published": item.findtext("pubDate"),                 # RFC 822
        "audio_url": enclosure.get("url") if enclosure is not None else None,
        "audio_bytes": enclosure.get("length") if enclosure is not None else None,
        "audio_type": enclosure.get("type") if enclosure is not None else None,
        "duration_raw": item.findtext(ITUNES + "duration"),
        "season_raw": item.findtext(ITUNES + "season"),
        "episode_raw": item.findtext(ITUNES + "episode"),
        "episode_type": item.findtext(ITUNES + "episodeType") or "full",
        "notes": item.findtext(CONTENT + "encoded") or item.findtext("description"),
    }
```

Every one of those lookups can come back `None`, and `findtext` gives you that instead of
an exception. One ordering trap: document order in a feed is a convention, not a rule, and
re-published items land wherever the host writes them. Sort on the parsed `pubDate`, never
on position in the file.

## Key every episode on the guid, never on the title or the audio URL

The `<guid>` is the identifier the host commits to keeping stable, and it is the only
field in the item that is meant to be one. Everything else that looks like a key is a
description of the episode that can be rewritten.

Titles repeat, and they repeat in the way that hurts most. "Introduction", "Part One",
"Q and A", "Season finale" and a live-show title reused each year all collide across
seasons, so a title key silently merges two different episodes into one row. The audio URL
is worse, because it fails all at once: when a show moves to a new host every enclosure in
the archive is rewritten to a new domain in a single publish, and a URL key reports the
entire back catalogue as new that day.

```python
from urllib.parse import urlsplit


def episode_key(row):
    """Stable identity, plus the name of whichever path produced it."""
    if row["guid"]:
        return row["guid"], "guid"

    if row["audio_url"]:
        # Drop the query and any per-request path prefix the host adds.
        parts = urlsplit(row["audio_url"])
        return parts.path.rsplit("/", 1)[-1], "audio_filename"

    return f"{row['title']}|{row['published']}", "title_and_date"
```

Return the source alongside the key. A row keyed on `guid` is an identity the publisher
asserts; a row keyed on a filename is a guess, and knowing which one you stored is what
lets you decide later whether a duplicate is real. That key is also the natural stop
condition for [incremental runs that fetch only new episodes](how-to-scrape-only-new-items-incremental-playwright.md).

## Ad insertion rewrites the audio URL and moves the duration

Fetch the same feed twice and the enclosure URL can differ both times. Hosts that insert
ads assemble the audio file at request time and encode the assembly in the URL, as a
prefix domain, an extra path segment or a token in the query string.

The consequences reach further than the URL. The `length` attribute describes a file that
is built per request, so the byte count moves with the ad load. `itunes:duration` can move
too, because a show that plans for a longer break publishes the padded figure. Neither is
a fact about the episode. They are observations about one fetch.

```python
# The fields a host does not rewrite between two fetches of the same feed.
STABLE_FIELDS = ("title", "published", "season", "episode", "episode_type", "notes")


def changed(previous, current):
    """Diff on stable fields only, or every run reports every episode changed."""
    return {
        name: (previous.get(name), current.get(name))
        for name in STABLE_FIELDS
        if previous.get(name) != current.get(name)
    }
```

Store `audio_url`, `audio_bytes` and the parsed duration with a `fetched_at` stamp beside
them, in their own table or their own columns, and diff on `STABLE_FIELDS`. Skip that and
a nightly job produces a change log in which nothing is signal: every episode changed,
every night, forever.

## Season and episode numbers live in two places

`itunes:season` and `itunes:episode` are optional, and a large share of shows never emit
them. Those shows still number their episodes, in the title text, in half a dozen shapes:
`S2E14`, `2x14`, `Ep. 14`, `#14`, `Episode 14`.

So a parser needs both paths, and it needs to record which one answered. A number lifted
out of a title is inference; a number from the field is a declaration by the publisher.
Anchor the pattern on a marker rather than taking the first integer in the string, because
titles are full of years, part counts and prices that a bare `\d+` will happily return.

```python
import re

TITLE_NUMBER = re.compile(
    r"""
      s(?P<season>\d{1,2})\s*[ex]\s*(?P<epx>\d{1,4})   # S2E14, s02 e14
    | (?P<season2>\d{1,2})x(?P<epx2>\d{1,4})           # 2x14
    | \b(?:ep|episode)\b\.?\s*\#?(?P<ep>\d{1,4})       # Ep. 14, Episode 14
    | \#(?P<ephash>\d{1,4})\b                          # #14
    """,
    re.IGNORECASE | re.VERBOSE,
)


def resolve_numbers(row):
    """Fields first, title second, and always say which one answered."""
    if row["episode_raw"]:
        return {
            "season": int(row["season_raw"]) if row["season_raw"] else None,
            "episode": int(row["episode_raw"]),
            "number_source": "itunes_fields",
        }

    found = TITLE_NUMBER.search(row["title"] or "")
    if not found:
        return {"season": None, "episode": None, "number_source": "absent"}

    parts = found.groupdict()
    season = parts["season"] or parts["season2"]
    episode = parts["epx"] or parts["epx2"] or parts["ep"] or parts["ephash"]
    return {
        "season": int(season) if season else None,
        "episode": int(episode) if episode else None,
        "number_source": "title_text",
    }
```

Keep `number_source` in the stored row. When two episodes claim number 14, the column
tells you in one query whether the collision came from the publisher or from a regex, and
that decides whether you fix the data or the pattern.

## Duration arrives as seconds, as a clock, and as prose

`itunes:duration` is a string by specification, and one feed can carry every shape at once,
because different tools wrote different items over the years. Expect `3600`, `01:04:12`,
`4:12` and, less often, `1 hr 4 min`.

Two parts means minutes and seconds by convention, three means hours, minutes and seconds.
Parse to an integer at the edge, keep the raw string next to it, and return `None` rather
than `0` when nothing parses, because a zero will end up in an average and a `None` will
not.

```python
import re

WORDS = re.compile(r"(?:(\d+)\s*h)?\D*(?:(\d+)\s*m)?\D*(?:(\d+)\s*s)?", re.IGNORECASE)


def duration_seconds(raw):
    """itunes:duration is free text. Return seconds, or None if unparseable."""
    if not raw:
        return None
    text = raw.strip()

    if text.isdigit():                      # already seconds
        return int(text)

    if ":" in text:                         # MM:SS or HH:MM:SS
        try:
            numbers = [int(part) for part in text.split(":") if part.strip()]
        except ValueError:
            return None
        seconds = 0
        for value in numbers:
            seconds = seconds * 60 + value
        return seconds

    found = WORDS.match(text.lower())       # "1 hr 4 min"
    if found and any(found.groups()):
        hours, minutes, secs = (int(g) if g else 0 for g in found.groups())
        return hours * 3600 + minutes * 60 + secs
    return None
```

The publication date deserves the same treatment. `pubDate` is RFC 822 and
`email.utils.parsedate_to_datetime` reads it, which puts every episode on one comparable
timestamp instead of a locale-shaped string, the same normalise-at-the-edge habit that
[cleaning scraped prices and dates](how-to-clean-scraped-prices-and-dates-playwright.md)
applies to the rest of a dataset.

## Where this stops: private and paywalled feeds

A subscriber feed is not a harder version of this problem. It is a different problem, and
nothing above solves it.

A private feed URL carries a token minted for one listener, and that token is the paying
account. Some hosts put the feed behind HTTP basic credentials instead. Either way the
server is checking entitlement, not plausibility, so a session that looks perfectly real
still gets a 401 or a 403 without a valid token, and no amount of fingerprint work moves
that number. Logging into a member web player is a separate route with its own mechanics,
covered in [scraping behind a login](how-to-scrape-behind-login-playwright.md), and it
does not hand you the private feed.

The practical shape of the limit: if the public feed carries 300 episodes and the paid
feed carries 340, those 40 are not recoverable by scraping harder. Record the gap and stop
there rather than building something that pretends to close it.

## Conclusion

Podcast listings reward one decision above all others. Find the feed, and the virtualised
player, the lazy images and the missing archive all stop being your problem, because the
document you end up parsing was written for machines and holds the whole show. After that
the work is choosing fields that survive a second fetch: key on the `guid`, because titles
repeat across seasons and audio URLs get rewritten wholesale when a show changes host, and
treat the URL, the byte length and the duration as observations with a timestamp rather
than facts. Parse season and episode numbers from both the fields and the title, and store
which path answered. Then be plain about the subscriber feeds, which stay closed.

## Short answers to the questions that lead here

**Where do I find a podcast's RSS feed URL?** Usually in the page head as
`<link rel="alternate" type="application/rss+xml">`. When the player is a client-rendered
app with no link tag, the URL is in an inline script config or on a data attribute of the
player element.

**Why does the player show fewer episodes than the feed?** Because the list is virtualised
and often server-capped as well. The DOM holds a recycled window of rows, not the archive,
so scrolling reads a moving slice while the feed carries every published item in one
document.

**What is the stable identifier for an episode?** The `<guid>`. Titles collide across
seasons and the audio URL changes when the show migrates host, so both make keys that
either merge distinct episodes or declare the whole archive new.

**Why does the audio URL change between two runs?** Dynamic ad insertion. The host
assembles the file per request and encodes that in the URL, which also moves the
`length` attribute and can move the stated duration, so none of the three should be
diffed as if it were fixed.

**The feed has no episode numbers. Where are they?** In the title text, as `S2E14`, `2x14`,
`Ep. 14` or `#14`, because `itunes:season` and `itunes:episode` are optional. Parse both
paths and store which one produced the number.

**How do I parse itunes:duration?** As free text, because it is. Handle bare seconds,
`MM:SS` and `HH:MM:SS`, treat two colon-separated parts as minutes and seconds, and return
`None` rather than zero when nothing parses.

## Sources

- Playwright's [`Page.goto`](https://playwright.dev/python/docs/api/class-page#page-goto),
  [`Page.content()`](https://playwright.dev/python/docs/api/class-page#page-content) and
  [`Page.query_selector`](https://playwright.dev/python/docs/api/class-page#page-query-selector),
  used exactly as documented upstream, since the browser this library returns is a real
  Playwright `Browser`. Retrieved 2026-08-28.
- Playwright's [locator documentation](https://playwright.dev/python/docs/locators) for
  `count()` on a recycled list, which reports what the DOM holds now and not what the page
  has rendered over time. Retrieved 2026-08-28.
- The RSS 2.0 specification for `<guid>` and its `isPermaLink` default, `<enclosure>` and
  `<pubDate>`, with the date format defined by
  [RFC 822](https://datatracker.ietf.org/doc/html/rfc822); and the `itunes:` namespace
  document, which defines `duration`, `season`, `episode` and `episodeType` as optional
  elements and `duration` as a string rather than a number.
- This project's own method for judging whether a fetch worked: compare against a stock
  browser rather than reading a verdict, described in
  [how to test whether your browser is detected](how-to-test-bot-detection.md).

**See also:** [scraping RSS and Atom feeds](how-to-scrape-rss-atom-feeds-playwright.md)
for the generic discovery and Atom parsing this page builds on,
[scraping virtual scrolling tables](how-to-scrape-virtual-scrolling-tables-playwright.md)
for reading a recycled list when no feed exists,
[incremental scraping](how-to-scrape-only-new-items-incremental-playwright.md) for using
the guid as a stop condition, and
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for
taking the served bytes instead of the serialized document.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. Keying episodes on the audio
URL is a mistake that shipped before it was fixed: a show changed host, every enclosure was
rewritten in one publish, and that night's run reported the entire archive as new.*
