---
title: "Cypress, WebdriverIO, TestCafe and Nightwatch integration"
description: "Cypress, WebdriverIO, TestCafe and Nightwatch: four mechanisms that do not all carry the same amount of the fingerprint, verified from source."
parent: "Integrations"
nav_order: 6
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
      "name": "Cypress, WebdriverIO, TestCafe and Nightwatch integration"
    }
  ]
}
</script>

# Cypress, WebdriverIO, TestCafe and Nightwatch integration

Four test runners, four mechanisms, and they do not all carry the same amount.
Verified from source on 2026-07-27. None of the four mentions this project, so
nothing here is a correction of somebody else's page.

| Runner | Engine | Seeded prefs | Mechanism |
|---|---|---|---|
| Cypress | yes | **yes** | `--browser <path>` plus `before:browser:launch` |
| WebdriverIO | yes | **yes** | `moz:firefoxOptions` with `binary` and `prefs` |
| TestCafe | yes | **no** | the `path:` browser provider |
| Nightwatch | yes | **yes** | `firefox_binary` plus `moz:firefoxOptions.prefs` |

Get the two values first, the same way for all four:

```bash
pip install invisible-playwright
invisible-playwright fetch
python -c "import json;from invisible_playwright import get_default_stealth_prefs;print(json.dumps(get_default_stealth_prefs(seed=1, humanize=True)))" > prefs.json
```

## Cypress

Cypress launches Firefox through geckodriver and builds a `moz:firefoxOptions` with
`binary: browser.path` and `prefs: launchOptions.preferences`
(`packages/server/lib/browsers/firefox.ts`). Both halves are reachable: the binary
from the command line, the preferences from the launch hook.

```js
// cypress.config.js
const { defineConfig } = require('cypress')
const prefs = require('./prefs.json')

module.exports = defineConfig({
  e2e: {
    setupNodeEvents(on) {
      on('before:browser:launch', (browser, launchOptions) => {
        if (browser.family === 'firefox') {
          Object.assign(launchOptions.preferences, prefs)
        }
        return launchOptions
      })
    },
  },
})
```

```bash
npx cypress run --browser /absolute/path/from/invisible-playwright fetch
```

**The Cypress-specific trap:** do not set `userAgent` in the config. Cypress reads it
and writes `general.useragent.override` into the same preference set
(`firefox.ts`, the `if (ua)` branch), so it silently wins over the profile's own user
agent and produces a browser whose stated identity disagrees with everything else it
reports. Leave it unset and the engine's own value stands.

Cypress also version-sniffs a browser given by path to place it in a known family. A
patched Firefox reports a normal Firefox version, so this works, but if a future
build changed the version string, this is the step that would break first.

## WebdriverIO

The cleanest of the four. `moz:firefoxOptions` is a W3C capability and it takes both
values directly, so this is standard WebDriver, not anything runner-specific.

```js
// wdio.conf.js
const prefs = require('./prefs.json')

exports.config = {
  capabilities: [{
    browserName: 'firefox',
    'moz:firefoxOptions': {
      binary: '/absolute/path/from/invisible-playwright fetch',
      prefs,
      args: ['-headless'],
    },
  }],
}
```

WebdriverIO merges its own defaults into `prefs` before launching
(`packages/wdio-utils/src/node/startWebDriver.ts`), so if you find a value not
behaving as expected, that merge is the first place to look rather than the profile.

## TestCafe

TestCafe can run any browser given a path, through its `path:` provider:

```bash
npx testcafe "path:/absolute/path/from/invisible-playwright fetch" tests/
```

**This carries the engine and not the identity.** A search of the TestCafe source
for a Firefox preference mechanism returns nothing: there is no supported way to
inject `firefoxUserPrefs`, so the browser reports the build's own defaults rather
than a seeded profile.

That means every run of the same build looks identical, on every machine that runs
it. For a test suite pointed at your own staging environment, that is fine and you
should not care. For anything where the identity matters, this is the wrong layer,
and one of the other three runners will do the job properly.

If you need it anyway, the workaround is the same as everywhere else: write the prefs
into a profile's `user.js` once and pass that profile's directory. It pins one
identity for that profile and not one per session, and it is a workaround rather
than a supported path.

## Nightwatch

Nightwatch reads a `firefox_binary` setting and calls `options.setBinary()` with it,
and builds its `FirefoxOptions` from the declared capabilities
(`lib/transport/selenium-webdriver/options.js`), so `moz:firefoxOptions.prefs`
carries the profile. Both halves, through standard WebDriver.

```js
// nightwatch.conf.js
const prefs = require('./prefs.json')

module.exports = {
  test_settings: {
    default: {
      desiredCapabilities: {
        browserName: 'firefox',
        'moz:firefoxOptions': { prefs, args: ['-headless'] },
      },
      webdriver: {
        firefox_binary: '/absolute/path/from/invisible-playwright fetch',
      },
    },
  },
}
```

The binary can be given either as `webdriver.firefox_binary` or as a top-level
`firefox_binary` setting: the code checks both, in that order.

## What all four give up

The two things no route outside the Python wrapper carries:

- **Humanised pointer motion.** The preference enables it; the paths are drawn from
  the seed by the driver. In all four runners the pointer teleports.
- **Timezone and locale resolved from the proxy exit.** Computed at launch by the
  wrapper. The prefs file is frozen at whatever it was generated with.

For test suites both are usually irrelevant, because a suite normally runs against a
known environment and does not need to convince anything. Say so out loud when
choosing: the reason to use this engine in a test runner is a realistic rendering and
font stack, not evasion.

## Short answers to the questions that lead here

**Can Cypress use a custom Firefox binary?** Yes, with `--browser <path>`, and the
preferences go through the `before:browser:launch` hook into
`launchOptions.preferences`.

**Why must I not set `userAgent` in Cypress?** Cypress writes it into
`general.useragent.override`, which silently wins over the profile's own user agent and
produces a browser whose stated identity disagrees with everything else it reports.

**Does WebdriverIO support Firefox preferences?** Yes. `moz:firefoxOptions` is a W3C
capability taking both `binary` and `prefs`, so this is standard WebDriver and not
anything runner-specific.

**Can TestCafe carry the profile?** No. It runs any browser by path, but a search of
its source finds no supported way to inject Firefox preferences, so you get the build's
own defaults. Fine for a staging suite, wrong wherever identity matters.

**Does Nightwatch work?** Yes, through `firefox_binary` plus `moz:firefoxOptions.prefs`.
The binary can be given at `webdriver.firefox_binary` or as a top-level setting; the
code checks both, in that order.
