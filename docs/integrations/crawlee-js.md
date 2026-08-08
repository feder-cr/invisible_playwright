---
title: "Using invisible_playwright with Crawlee for JavaScript"
description: "Crawlee for JavaScript takes a browser type and a bag of Playwright launch options, so swapping the engine is configuration, not a subclass."
parent: "Integrations"
nav_order: 3
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
      "name": "Using invisible_playwright with Crawlee for JavaScript"
    }
  ]
}
</script>

# Using invisible_playwright with Crawlee for JavaScript

Crawlee's JavaScript side takes a browser type and a bag of Playwright launch
options, so swapping the engine is configuration instead of a subclass. Both halves
fit: the patched binary and the seeded preferences.

Verified against `apify/crawlee` on `master`
(`packages/playwright-crawler/src/internals/playwright-launcher.ts`):

```ts
launcher?: BrowserType;                 // "pass it by this property as e.g. require('playwright').firefox"
launchOptions?: LaunchOptions & ...;    // Playwright's own LaunchOptions
```

Written against `invisible-playwright` 0.4.6.

## The two values, from a JavaScript project

The engine and the identity are produced by the Python package even though the
crawler is Node. Get them once, at install or deploy time:

```bash
pip install invisible-playwright
invisible-playwright fetch > .browser-path
python -c "import json;from invisible_playwright import get_default_stealth_prefs;print(json.dumps(get_default_stealth_prefs(seed=1, humanize=True)))" > prefs.json
```

Yes, a Python dependency in a Node project. That is the real cost of this route,
and if it is unacceptable, the answer is a Chromium-based tool rather than a
workaround.

## The crawler

```js
import { PlaywrightCrawler } from 'crawlee';
import { firefox } from 'playwright';
import { readFileSync } from 'node:fs';

const executablePath = readFileSync('.browser-path', 'utf8').trim();
const firefoxUserPrefs = JSON.parse(readFileSync('prefs.json', 'utf8'));

const crawler = new PlaywrightCrawler({
    launchContext: {
        launcher: firefox,
        launchOptions: {
            executablePath,
            firefoxUserPrefs,
            headless: true,
        },
    },
    browserPoolOptions: {
        // Crawlee generates a fingerprint and matching headers on the assumption
        // that it owns the browser's identity. Here it does not.
        useFingerprints: false,
    },
    async requestHandler({ page, request, enqueueLinks, pushData }) {
        await pushData({ url: request.url, title: await page.title() });
        await enqueueLinks();
    },
});

await crawler.run(['https://crawlee.dev']);
```

## `useFingerprints: false` is not optional

Crawlee ships fingerprint generation and header generation, and they are good. They
are also a second opinion about who this browser is, produced independently of the
one compiled into the engine and derived from the seed.

Two independent opinions do not average out. They disagree, and the disagreement
between a generated header set and an engine-level fingerprint is a stronger signal
than either being slightly unusual on its own. Turn it off and let one component own
the identity.

The same argument applies to `userAgent` anywhere in your config: do not set it.

## Persistent profiles

`useIncognitoPages` and the persistent-context path both work as they normally do,
and `launchOptions` is typed to accept `launchPersistentContext` parameters too, so a
`userDataDir` belongs there. Pair a stable profile with a stable seed: the profile
keeps cookies and storage, the seed keeps the machine those cookies belong to. One
without the other is a session whose history and hardware disagree about how long it
has existed.

## What this route gives up

The same two things every non-Python route gives up:

- **Humanised pointer motion.** The preference enables it, but the paths are drawn
  from the seed by the Python driver, so from Node the pointer teleports.
- **Timezone and locale resolved from the proxy exit.** The Python wrapper resolves
  those at launch against the IP you actually leave from. What you get instead is
  whatever `prefs.json` was generated with, so if Crawlee is rotating proxies across
  countries the browser will contradict the exit.

That second point matters more in Crawlee than in most places, because rotating
proxies is a normal thing to do here. If your proxy pool spans countries, either
generate a `prefs.json` per region and select it per session, or accept that the
timezone will be wrong for most of them.

## Checking it worked

```js
const ua = await page.evaluate(() => navigator.userAgent);
const gpu = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl');
    const dbg = gl.getExtension('WEBGL_debug_renderer_info');
    return gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
});
```

The user agent should say Firefox at the version `invisible-playwright version`
reports. The renderer should be consumer hardware; if it says basic or software, the
prefs did not apply or the machine has no GPU, and both are worth knowing before you
trust a run.

## Short answers to the questions that lead here

**Can I use a patched Firefox with Crawlee in Node without Python?** Not for the setup
step. The engine and the preferences are produced by the Python package. Once you have
the binary path and `prefs.json`, the crawler itself is pure Node.

**Why `useFingerprints: false`?** Because Crawlee's fingerprint generator and the
engine both want to decide who this browser is, and their answers will not match. The
disagreement is a stronger signal than either value being unusual on its own.

**Does `launcher: firefox` mean the stock Firefox?** It means Playwright's Firefox
*class*, which is the thing that knows how to speak to a Firefox. `executablePath`
decides which binary it actually starts.

**Can I use persistent profiles?** Yes, through `launchOptions` and a `userDataDir`.
Pair a stable profile with a stable seed, or the session's history and its hardware
disagree about how long it has existed.

**What breaks if my proxy pool spans countries?** The timezone and the locale, which
are frozen into `prefs.json` when it is generated. Produce one per region and select
it per session, or accept the mismatch.
