---
title: "scrapy-playwright vs a patched Firefox for stealth"
description: "scrapy-playwright runs stock browser in Scrapy keeping automation fingerprint. Point handler at patched Firefox and keep scheduler while browser looks real."
parent: "Comparisons"
nav_order: 30
---


# scrapy-playwright vs a patched Firefox for stealth

This reads like a versus and it is not one. `scrapy-playwright` and a patched
Firefox solve two different problems, and the useful setup uses both. Scrapy owns
the crawl: the scheduler, the retry middleware, the item pipelines, the throttling.
The browser owns the fingerprint: what the TLS handshake, the driver layer and the
JavaScript environment report to the page. Pit them against each other and you pick
one problem to solve and leave the other open.

The confusion is worth clearing up because it changes what you install. If you think
of `scrapy-playwright` as the stealth layer, you will be surprised when a
well-orchestrated crawl still gets a different page than a human does. It is not the
orchestration that is detected.

## What scrapy-playwright actually is

`scrapy-playwright` is a Scrapy download handler. It runs Playwright inside Scrapy's
async engine so that a request marked `meta={"playwright": True}` is fetched by a real
browser instead of Scrapy's plain HTTP client, and the rendered page comes back to
your `parse` method. That is a genuinely useful thing: you keep everything Scrapy is
good at, and you get a JavaScript-capable fetch where you need one.

What it launches, by default, is Playwright's own bundled browser. That browser is a
stock build driven by an automation driver, and it carries the fingerprint that comes
with that: the driver layer is visible, and on a server the machine tells (no GPU, no
audio device, a screen size nobody has) are visible too. `scrapy-playwright` does not
change any of that, because changing it is not its job. It is a handler, not a
disguise.

So the split is clean. `scrapy-playwright` decides *when and in what order* pages get
fetched and *what happens to the results*. The browser it drives decides *what the
page sees*. A detector reads the second one.

## Where the stock browser gives itself away

The tells fall into two groups, and neither is fixed by orchestration.

- **Driver-layer tells.** `navigator.webdriver`, leftover automation globals, an
  untrusted synthetic event. Modern tooling papers over the obvious ones, but the
  handshake and the driver seam are still there to be read.
- **Machine tells.** A software WebGL renderer, an empty font list for the platform
  you claim, no audio hardware, a device pixel ratio no real display has. These
  survive every in-page patch because they are not JavaScript's to change.

You can confirm which group you are hitting the same way you would for any Playwright
job: open a public suite such as CreepJS or sannysoft in the browser Scrapy launched,
and compare it field by field against a stock browser on the same machine. A verdict
alone will mislead you, for reasons the [bot-detection testing
guide](how-to-test-bot-detection.md) works through. The point here is only that the
mismatch lives in the browser, not in the crawler wrapped around it.

## Swap the browser, keep the crawler

Because `scrapy-playwright` forwards `PLAYWRIGHT_LAUNCH_OPTIONS` straight into
Playwright's own [`launch()`](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch),
you can hand it a different Firefox binary and a full set of
stealth preferences without touching your spiders. Scrapy's scheduler, retry
middleware and item pipelines stay exactly as they were; only the engine under the
handler changes.

```python
# settings.py
from invisible_playwright import ensure_binary, get_default_stealth_prefs

DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

PLAYWRIGHT_BROWSER_TYPE = "firefox"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "executable_path": str(ensure_binary()),
    "firefox_user_prefs": get_default_stealth_prefs(seed=42, humanize=True),
    "headless": True,
}
```

`ensure_binary()` returns the path to the patched Firefox, downloading and caching it
on first use; `get_default_stealth_prefs(seed=42)` builds the same seed-derived
fingerprint prefs the wrapper would inject. The spider is unchanged:

```python
import scrapy


class ExampleSpider(scrapy.Spider):
    name = "example"

    def start_requests(self):
        yield scrapy.Request("https://example.com/", meta={"playwright": True})

    def parse(self, response):
        yield {"title": response.css("title::text").get()}
```

This settings-only route is the quick version and it has real limits: it does not
resolve `timezone="auto"` against your exit, and it cannot do authenticated SOCKS.
The provider route that carries all of that, plus the persistent-context wiring, is in
[using invisible_playwright with scrapy-playwright](integrations/scrapy-playwright.md).
For a one-off script outside Scrapy, the same engine is a two-line launch:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/")
    print(page.title())
```

The object it returns is a real Playwright `Browser`, so every method works exactly as
documented upstream. Same seed, same fingerprint, every run, which is what makes a
failing crawl reproducible instead of a guessing game.

## The honest caveat: a real browser does not fix a loud crawl

A browser that looks real is one axis. It is not the only one, and Scrapy's defaults
push hard on a second axis that no fingerprint touches: pace.

Scrapy is built to be fast. Out of the box it runs many requests in parallel and puts
no per-domain gap between them, which is exactly the velocity pattern a rate-based
detector is built to catch. A perfectly real-looking browser that requests forty pages
a second from one address and one session still reads as automation, because no person
browses that way. Swapping the engine does nothing about this. You have to slow the
crawl down yourself.

```python
# settings.py - pace the crawl so it does not read as a machine
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 3            # seconds between requests to the same domain
AUTOTHROTTLE_ENABLED = True  # back off further when the site is slow
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
```

Treat those as a starting point, not a recipe; the right numbers depend on the site.
The wider subject of what a human session's timing looks like, and why one address
doing everything at once is the giveaway, is in [how to rate-limit your
scraper](how-to-rate-limit-your-scraper-playwright.md), and the concurrency tradeoffs
specifically in [scraping multiple pages in
parallel](how-to-scrape-multiple-pages-in-parallel-playwright.md).

And the browser is honest about what it does and does not cover. It makes the
fingerprint, TLS and driver layer read as a genuine Firefox, which is why most
detection checks pass. It does not fix your IP reputation, a per-account quota, a rate
limit, or your behaviour and timing. Those you supply: a clean exit, human pacing, and
not running a second stealth layer that argues with the engine. Picking a single layer
and pacing it is most of the job; see [the stealth
levels](playwright-stealth-levels.md) for why doubling up backfires.

## When each choice is the right one

Reach for plain `scrapy-playwright` with its bundled browser when the target does not
score the browser at all: an internal tool, a cooperative API behind a JavaScript
render, a site you own. The stock engine is simpler and you do not need the disguise.

Point the handler at the patched Firefox when the target reads the fingerprint and
serves automation a different page. You keep every Scrapy feature and the browser stops
announcing itself. And keep plain Scrapy, no browser at all, for pages that do not need
JavaScript: it is an order of magnitude faster and there is no fingerprint in play.

None of these is a route around detection in the abstract. They are three different
amounts of browser for three different amounts of scrutiny.

## Conclusion

`scrapy-playwright` versus a patched Firefox is a false choice. The handler orchestrates
the crawl and the browser carries the fingerprint, and the setup that works uses one
for each: keep Scrapy's scheduler, retry middleware and pipelines, and give the handler
an engine that reads as a real Firefox instead of the stock automation build. Then do
the part no engine does for you - pace the crawl with `AUTOTHROTTLE` and a per-domain
delay, and put it behind a clean exit. Looking real gets you past the fingerprint. It
does not excuse you from behaving like a person.

## Short answers to the questions that lead here

**Does scrapy-playwright make my scraper undetectable?** No. It runs Playwright's stock
browser inside Scrapy, which carries the usual automation fingerprint. It handles
orchestration; the fingerprint is a separate layer you have to supply.

**Can I use a custom Firefox binary with scrapy-playwright?** Yes. It forwards
`PLAYWRIGHT_LAUNCH_OPTIONS` into Playwright's `launch()`, so `executable_path` and
`firefox_user_prefs` reach the browser and your spiders do not change.

**Will swapping the browser stop me getting blocked?** It stops the fingerprint, TLS and
driver layer from giving you away, which is most detection checks. It does nothing about
IP reputation, rate limits, quotas or timing, and Scrapy's fast defaults make the timing
worse until you tune it.

**Why does my crawl still get blocked with a real-looking browser?** Usually pace or
exit. Scrapy's default concurrency with no per-domain delay is a machine-like request
pattern, and a datacenter IP is a datacenter IP no matter how the browser looks.

**Do I still need AUTOTHROTTLE?** Yes, and arguably more, because a browser fetch is
expensive and a real person is slow. Set `DOWNLOAD_DELAY`, cap
`CONCURRENT_REQUESTS_PER_DOMAIN`, and enable `AUTOTHROTTLE`.

**Should I also add a JavaScript stealth plugin?** No. If the engine already answers the
fingerprint questions, a page-level patcher answering them again produces contradictions
neither produces alone. Pick one layer.

## Sources

- `scrapy-playwright`'s own repository and README, which document the download handler,
  `PLAYWRIGHT_LAUNCH_OPTIONS`, and a pluggable browser-provider interface, verified
  against version 0.0.48.
- Playwright's own [`launch()` API reference](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch),
  for `executable_path` and `firefox_user_prefs`, the two options that carry the
  patched binary and its stealth preferences into the browser.
- Scrapy's documented throttling settings: `CONCURRENT_REQUESTS_PER_DOMAIN`,
  `DOWNLOAD_DELAY`, and the AutoThrottle extension.
- This project's own [quickstart](quickstart.md) and [configuration](configuration.md)
  pages for the launch API, the `get_default_stealth_prefs` and `ensure_binary`
  helpers, and the proxy and timezone behaviour.

**See also:** [using invisible_playwright with scrapy-playwright](integrations/scrapy-playwright.md)
for the full provider setup, [how to rate-limit your scraper](how-to-rate-limit-your-scraper-playwright.md)
for the pacing half of the job, and [the checklist for being detected on one
site](playwright-detected-as-bot.md) when a real-looking crawl still fails.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. It makes the browser look
real; the clean proxy and the human pacing are still yours to bring.*
