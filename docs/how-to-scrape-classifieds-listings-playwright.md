---
title: "How to scrape classifieds listings with Playwright"
description: "Scrape classifieds listings with Playwright: align the proxy region and timezone, harvest cards from the rotating feed, then drive a human contact-reveal click."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 31
---


# How to scrape classifieds listings with Playwright

To scrape classifieds listings with Playwright you align three location signals (proxy
exit, browser timezone, and the site's own location control) so the feed is the real
inventory for a place you can plausibly be, loop the rotating category feed and dedupe on
each listing's own id, then drive the contact-reveal click as a genuine human interaction.
When the revealed number is painted into an image, read it with OCR on the image bytes,
not a DOM text read.

Classifieds are not a search index. They are seller-posted cards in an infinite
category feed, scoped to a location you choose, and they rotate: a listing posted this
morning can be gone by the afternoon. The seller's phone or contact is usually hidden
until you click a reveal button, and on some boards that revealed value is painted into
an image so a plain text read comes back empty.

That shape breaks the naive approach in specific ways, and this page works through each:

| The obstacle | Why it breaks a naive scrape | The approach |
|---|---|---|
| Feed is location-scoped | The site shows one city's inventory; a mismatch is wrong data or a tell | Align proxy exit, timezone, and the site's location control |
| Feed is infinite and rotates | Cards shift and expire between passes | Loop the feed, dedupe on listing id, persist fields at harvest time |
| Contact is behind a reveal | A synthetic click gets a decoy value or nothing | Drive a trusted, human-looking reveal click, spaced out |
| Contact is a rendered image | `inner_text()` returns empty; there is no DOM string | OCR on the image bytes, a separate step from the browser |

## Why the location has to agree with itself

The first thing a classifieds site does is decide which city's inventory to show you. It
reads a location setting you picked, and it reads your exit IP, and if those disagree it
has two ways to respond: serve you the wrong city's feed, or treat the disagreement as a
signal. Either way you lose. Wrong-city cards are wrong data, and a feed for one city
served to an address in another is a tell before you have clicked anything.

So three things have to resolve to the same place: the proxy exit, the browser timezone,
and the location you set inside the site. When you pass a proxy, the wrapper derives the
timezone from the egress IP by default, so two of the three line up on their own:

```python
from invisible_playwright import InvisiblePlaywright

proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

# timezone auto-derives from the proxy egress IP; pass an explicit zone only to override
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/city/listings")
```

The third alignment, the site's own location control, is on the page and you set it the
way a visitor would. This matters more than it looks: many boards remember the choice in
a cookie or a query parameter, and if you skip it the site falls back to geolocating your
IP, which puts you right back at the mismatch. There is a whole page on
[everything that has to agree once you pin a region](timezone-proxy-mismatch.md), because
timezone is several surfaces a detector cross-checks, not one value.

## Set the site's location the way a visitor does

Drive the site's location selector rather than injecting a value. The selector is usually
a control that opens, takes a typed city, and offers a suggestion to click:

```python
page.click("[data-testid='location-picker']")
page.fill("input[name='city']", "Portland")
page.click("li.location-suggestion:has-text('Portland')")
page.wait_for_load_state("networkidle")
```

The typing and the click here are not incidental. The city you type should be one the
proxy region can actually explain, and the click that confirms it is the first behavioural
event the site sees. On a real engine that click carries a trusted event and arrives after
a pointer that traveled to the control, which is
[why the reveal and confirm clicks are not synthesizable from JavaScript alone](playwright-clicks-istrusted.md).

## Harvest the cards from the category feed

Once the feed is scoped, the cards are a repeating DOM structure and you read them in a
loop. Because the feed is infinite and rotates, treat "how many cards" as "how many you
scroll to", and dedupe on the listing's own id rather than on position, since positions
shift as new listings arrive:

```python
seen = {}

def harvest_visible(page):
    for card in page.query_selector_all("article.listing-card"):
        href = card.query_selector("a.listing-link").get_attribute("href")
        listing_id = href.rsplit("/", 1)[-1]
        if listing_id in seen:
            continue
        seen[listing_id] = {
            "id": listing_id,
            "url": href,
            "title": card.query_selector("h2").inner_text(),
            "price": card.query_selector(".price").inner_text(),
        }

# scroll the feed, harvesting what each pass reveals
for _ in range(20):
    harvest_visible(page)
    page.mouse.wheel(0, 2400)
    page.wait_for_timeout(900)

harvest_visible(page)
print(f"harvested {len(seen)} listings")
```

The loading and de-duplication mechanics of an endless feed are their own topic, covered
in [scraping an infinite scroll feed](how-to-scrape-infinite-scroll-playwright.md). The
point specific to classifieds is expiry: the id you captured on pass one may 404 on the
detail visit an hour later, so persist the card fields you already have and do not assume
the detail page will still be there.

## Drive the contact-reveal click per listing

The phone or contact lives on the detail page behind a reveal button. Visit each id,
click reveal, wait for the value to appear, and read it. The click is where a real browser
earns its keep: the reveal often fires a request the server risk-scores, and a pointer
that jumps straight to the button with a synthetic event is exactly the pattern that gets
a session shown a decoy number or nothing at all.

```python
def reveal_contact(page, listing):
    page.goto(listing["url"], wait_until="domcontentloaded")
    reveal = page.query_selector("button.reveal-contact")
    if reveal is None:
        return None
    reveal.click()   # pointer arcs to the button, trusted event fires
    page.wait_for_selector(".contact-value:visible", timeout=8000)
    return page.query_selector(".contact-value").inner_text()

for listing in list(seen.values()):
    listing["contact"] = reveal_contact(page, listing)
    page.wait_for_timeout(1500)   # space the reveals; do not machine-gun them
```

That the click looks human is not a slogan here, it is the whole reason a patched engine
is doing the driving. The pointer travels to the control on a curved path rather than
teleporting, which is [what human mouse movement means in practice](human-mouse-movement.md),
and the resulting event is trusted rather than synthetic. Space the reveals out. A hundred
reveal clicks a minute from one session is a velocity signal no fingerprint can launder.

You can prove the identity underneath this is stable, which is what lets you retry a failed
reveal without becoming a new visitor mid-run. Pin the seed, load a fingerprinting page
twice, and the [FingerprintJS](fingerprintjs-visitor-id.md) visitor ID is byte-for-byte
identical both times. Same seed, same machine, so a listing that failed to reveal can be
retried as the same person rather than a suspicious second one:

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    ids = []
    for _ in range(2):
        p = browser.new_page()
        p.goto("https://example.com/fingerprint")
        ids.append(p.evaluate("() => window.__visitorId"))
        p.close()
    assert ids[0] == ids[1]   # identical: the identity is seed-stable
```

## When the contact is a picture, not text

Here is the honest limit. Some boards render the revealed phone number into an image
precisely to defeat text scraping, so the reveal click succeeds, the pixels appear, and
`inner_text()` on that element returns an empty string because there is no text to return.
A real browser does not fix this. It got you the trusted click and the loaded image; it
cannot turn a picture into a string.

The fix is OCR, run on the image bytes, not a DOM read:

```python
el = page.query_selector("img.contact-image")
if el is not None:
    png = el.screenshot()          # bytes of the rendered image
    # hand `png` to an OCR step; the DOM has no string to give you
    contact = run_ocr(png)         # your OCR of choice
```

Keep the two problems separate in your head. Being a real, consistent browser is what
gets you past the location check and makes the reveal click land. Reading a number out of
an image is a different job with a different tool, and no amount of stealth substitutes
for it. Anyone promising that a browser alone extracts image-rendered contacts is selling
you the click and calling it the number.

## Conclusion

Classifieds reward getting the boring parts right. Align the proxy exit, the timezone and
the site's location setting so the feed you harvest is the real inventory for a place you
can plausibly be. Loop the feed and dedupe on listing id, because it moves and expires
under you. Drive the reveal as a human interaction, because that click is risk-scored.
And when the contact is a picture, reach for OCR rather than expecting the DOM to hand you
a string it does not have. A seed-stable real browser makes the first three
reproducible; the fourth is a separate, honest piece of work.

## Short answers to the questions that lead here

**Why do I get listings for the wrong city?** Because the site geolocated your exit IP
and it disagreed with the location you thought you set. Align the proxy region, the
timezone and the site's own location control, and set that control by driving it rather
than injecting a value.

**Why is the phone number empty when I read the element?** Two reasons. Either you read
before the reveal click completed, or the number is rendered as an image and there is no
text in the DOM to read. The second needs OCR on the image bytes.

**Can the browser read the contact image for me?** No. A real browser gets you the reveal
click and loads the picture; turning that picture into a string is OCR, a separate step.

**Why do listings disappear between the feed and the detail page?** Because classifieds
expire and rotate within hours. Persist the card fields at harvest time and expect some
detail URLs to 404 by the time you visit them.

**How do I retry a failed reveal without looking like a new bot?** Pin the seed. The
identity is reproducible, so a retry is the same visitor rather than a suspicious second
one that appeared out of nowhere.

**Does clicking reveal fast get me blocked?** It can. The reveal often fires a
risk-scored request, so space the clicks out instead of machine-gunning the feed.

## Sources

- Playwright documentation, [Mouse.wheel()](https://playwright.dev/python/docs/api/class-mouse#mouse-wheel),
  retrieved 2026-08-28, for the wheel-based scroll the harvest loop drives.
- Playwright documentation, [ElementHandle.screenshot()](https://playwright.dev/python/docs/api/class-elementhandle#element-handle-screenshot),
  retrieved 2026-08-28, for capturing the image bytes an OCR step reads.
- [FingerprintJS](https://github.com/fingerprintjs/fingerprintjs), the open-source library
  this project's consistency gate runs against when it asserts that one seed produces one
  identical visitor ID across runs.
- This project's quickstart and configuration pages for the real launch API, the proxy
  dict, and the timezone that auto-derives from the egress IP.
- The behavioural notes in this set on trusted events and pointer motion, which are why a
  reveal click on a real engine reads differently from a synthetic one.

**See also:** [scraping geo-targeted content](how-to-scrape-geotargeted-content-playwright.md)
for the region alignment in general, [pinning fingerprint fields](pinning.md) for holding
one value while the rest stays seed-derived, and
[the infinite-scroll feed mechanics](how-to-scrape-infinite-scroll-playwright.md) the
harvest loop above depends on.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The location-alignment and
image-OCR distinctions above are ones this project measured rather than guessed at.*
