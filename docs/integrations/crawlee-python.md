---
title: "Using invisible_playwright with Crawlee for Python"
description: "Crawlee for Python drives browsers through a plugin, so a different engine is a subclass rather than a fork. Verified against crawlee on master."
parent: "Integrations"
nav_order: 2
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Integrations",
      "item": "https://feder-cr.github.io/invisible_playwright/integrations/README.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Using invisible_playwright with Crawlee for Python"
    }
  ]
}
</script>

# Using invisible_playwright with Crawlee for Python

Crawlee drives browsers through a plugin, so a different browser backend is a
subclass instead of a fork. This page is the integration guide for
`invisible_playwright`, hosted here rather than in Crawlee's own documentation,
which is where Crawlee asks third-party integrations to live.

Written against `crawlee` on `master` as of 2026-07-27, `invisible-playwright`
0.6.0 and `invisible-core` 18.12.0. If Crawlee changes
`PlaywrightBrowserPlugin.new_browser`, this page is the thing that goes stale, so
check the signature before blaming the engine.

## What you need

```bash
pip install crawlee[playwright] invisible-playwright
```

The patched Firefox binary is downloaded on first launch and cached, so the first
run is slow and every later one is not. Nothing else to install: the package brings
its own engine and drives it with stock Playwright.

## Two routes, and they are not equivalent

Crawlee exposes `default_browser_path` in its `Configuration` (env
`CRAWLEE_DEFAULT_BROWSER_PATH`), passed straight to `browser_type.launch` as
`executable_path`. So the one-liner works:

```python
from crawlee.configuration import Configuration

Configuration.get_global_configuration().default_browser_path = str(ensure_binary())
```

**That gets you the patched engine and not the profile.** The binary is only half of
it: the fingerprint lives in the `firefox_user_prefs` that the profile generates from
a seed, and `default_browser_path` has no way to pass them. You get a browser that is
harder to fingerprint than stock Firefox and that still reports whatever the build
defaults to, with no seeding and no reproducibility.

Use the one-liner if you only want the engine. Use the plugin below if you want the
thing this package is actually for.

## The plugin

Crawlee's extension point is `PlaywrightBrowserPlugin`. Override `new_browser`,
launch whatever browser you want, and hand it back in a
`PlaywrightBrowserController`. The subclass below keeps every other behaviour of
the base plugin, including incognito pages and the persistent-profile path.

```python
import asyncio

from crawlee.browsers import (
    BrowserPool,
    PlaywrightBrowserController,
    PlaywrightBrowserPlugin,
    PlaywrightPersistentBrowser,
)
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from invisible_playwright import (
    ensure_binary,
    get_default_args,
    get_default_stealth_prefs,
)
from typing_extensions import override


class InvisiblePlaywrightPlugin(PlaywrightBrowserPlugin):
    """Launches the patched Firefox, keeps the rest of PlaywrightBrowserPlugin."""

    def __init__(self, *args, seed: int | None = None, **kwargs) -> None:
        # browser_type is forced: this plugin only knows how to launch Firefox.
        kwargs['browser_type'] = 'firefox'
        super().__init__(*args, **kwargs)
        self._seed = seed

    @override
    async def new_browser(self) -> PlaywrightBrowserController:
        if not self._playwright:
            raise RuntimeError('Playwright browser plugin is not initialized.')

        options = dict(self._browser_launch_options)
        options.pop('executable_path', None)
        options.pop('firefox_user_prefs', None)
        proxy = options.pop('proxy', None)
        options['executable_path'] = str(ensure_binary())
        options['args'] = [*options.get('args', []), *get_default_args()]
        options['firefox_user_prefs'] = get_default_stealth_prefs(
            seed=self._seed,
            humanize=True,
            proxy=proxy,
        )
        if proxy and not proxy['server'].lower().startswith('socks'):
            options['proxy'] = proxy

        if self._use_incognito_pages:
            browser = await self._playwright.firefox.launch(**options)
        else:
            browser = PlaywrightPersistentBrowser(
                self._playwright.firefox, self._user_data_dir, options
            )

        return PlaywrightBrowserController(
            browser,
            use_incognito_pages=self._use_incognito_pages,
            max_open_pages_per_browser=self._max_open_pages_per_browser,
            # The fingerprint is compiled into the engine, so Crawlee's own
            # generator would be a second, disagreeing opinion. Leave it off.
            fingerprint_generator=None,
        )


async def main() -> None:
    crawler = PlaywrightCrawler(
        max_requests_per_crawl=10,
        # seed=None gives a fresh identity per run. Pass an int to reproduce one.
        browser_pool=BrowserPool(plugins=[InvisiblePlaywrightPlugin(seed=1)]),
    )

    @crawler.router.default_handler
    async def request_handler(context: PlaywrightCrawlingContext) -> None:
        context.log.info(f'Processing {context.request.url} ...')
        await context.push_data({'url': context.request.url})
        await context.enqueue_links()

    await crawler.run(['https://quotes.toscrape.com/'])


if __name__ == '__main__':
    asyncio.run(main())
```

## Three things that will bite you otherwise

**`fingerprint_generator=None` is not optional.** Crawlee can generate a fingerprint
and matching headers, on the assumption that it controls the browser's identity.
Here it does not: the identity is compiled into the engine and derived from the
seed. Leaving the generator on produces two independent opinions about who this
browser is, and the disagreement between them is exactly what detectors read.

**The seed decides whether a failure is debuggable.** With `seed=None` every run is
a different machine, so a failing run tells you nothing: you cannot separate the
site changing from the identity changing. Pass an integer and the navigator, screen,
GPU, fonts, canvas and audio surfaces are identical every time. Change it when you
want a different machine, not when you want a different outcome.

**Do not set a user agent on the Crawlee side.** Same reason. The user agent comes
from the engine's real version and from the same seeded profile as everything else;
overriding only that string is the classic way to build a browser whose stated
identity and observable behaviour disagree.

## Proxies

Pass them the way Crawlee already expects, through the launch options the plugin
receives. A SOCKS5 endpoint has to reach `get_default_stealth_prefs` through its
`proxy=` argument, which writes the `network.proxy.*` preferences the patched
engine reads, and it must not also reach Playwright: Playwright configures the
proxy itself, without the SOCKS credentials, and that configuration wins over the
preferences. HTTP and HTTPS are the other way round and go through Playwright's
own `proxy=` argument.

If you use one, leave locale and timezone on their defaults. They resolve from the
exit IP and not from the host machine, which is what keeps the JS timezone, the
language list and the IP in agreement. Setting them by hand to your own machine's
values is how a session ends up claiming one country while leaving from another.

## When this is the wrong tool

If the target needs Chromium, this is the wrong engine and no amount of patching
changes it. Some sites serve different code to Firefox and some need Chrome-specific
behaviour. A clean Firefox is still a Firefox.

The binary is also a few hundred megabytes, cached per machine. Fine on a
workstation, worth thinking about in a container image you rebuild often.

## Short answers to the questions that lead here

**Does Crawlee for Python support a custom browser binary?** Yes, through the browser
plugin interface. The plugin owns the launch, so anything Playwright's `launch()`
accepts is reachable.

**Do I have to turn off Crawlee's fingerprint generation?** Yes. Crawlee generates a
fingerprint and matching headers on the assumption that it owns the browser identity.
Here the engine owns it, and two independent opinions about who this browser is do not
average out, they contradict each other.

**Can I keep using `PlaywrightCrawler` unchanged?** Yes. Only the launch changes. The
request handler, the storage and the enqueueing all behave as they normally do.

**How do I rotate identities across a crawl?** One seed is one machine. Rotate at the
session level with separate seeds, never per request, or the same visitor appears to
change hardware between two page loads.

**Does the proxy configuration still work?** Yes, but pass it where the browser can
see it. A proxy set only at the HTTP client layer does not reach a browser that makes
its own connections.
