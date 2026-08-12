---
title: "Using invisible_playwright with CodeceptJS"
description: "CodeceptJS spreads whatever sits under the browser's own config key straight into Playwright's launch options, so one firefox block carries both the binary and the seeded prefs."
parent: "Integrations"
nav_order: 1
---

# Using invisible_playwright with CodeceptJS

CodeceptJS spreads whatever you put under the browser's own key straight into
Playwright's launch options, so a `firefox` block carries both halves: the patched
binary and the seeded preferences.

Written against CodeceptJS 4.x (`lib/helper/Playwright.js`,
`_getOptionsForBrowser` returns `{ ...config[config.browser] }`) and
`invisible-playwright` 0.6.0.

CodeceptJS's own [Playwright docs](https://github.com/codeceptjs/CodeceptJS/blob/4.x/docs/playwright.md)
already mention this project as an example of pointing at a custom executable. This
page is the full wiring.

## The two values

The engine and the identity come from the Python package even though the tests are
JavaScript. Get them once:

```bash
pip install invisible-playwright
invisible-playwright fetch    # downloads it if missing, prints the binary path as its last line
python -c "import json;from invisible_playwright import get_default_stealth_prefs;print(json.dumps(get_default_stealth_prefs(seed=1, humanize=True)))" > prefs.json
```

Regenerate `prefs.json` when you upgrade the package: the pref set moves with the
engine. Keep the seed written down next to it, because that number is the identity.

## codecept.conf.js

```js
const fs = require('fs')

exports.config = {
  helpers: {
    Playwright: {
      url: 'https://example.com',
      browser: 'firefox',
      show: false,
      firefox: {
        executablePath: '/absolute/path/from/invisible-playwright fetch',
        firefoxUserPrefs: JSON.parse(fs.readFileSync('prefs.json', 'utf8')),
      },
    },
  },
  tests: './*_test.js',
}
```

That is the whole integration. Everything under `firefox` is forwarded verbatim, so
any Playwright launch option belongs there too, including `proxy`.

## Your tests do not change

```js
Feature('search')

Scenario('finds a thing', async ({ I }) => {
  I.amOnPage('/')
  I.fillField('q', 'codeceptjs')
  I.click('Search')
  I.see('results')
})
```

The helper API does not change, and that is the point of doing it at this layer.

## Three things to get right

**Do not set a user agent in the helper config.** It comes from the engine's real
version and from the same seeded profile as everything else. Overriding only that
string is the standard way to make a browser contradict itself, and CodeceptJS makes
it easy to do by accident because `userAgent` sits right next to the options above.

**One seed per identity, not per run.** A fixed seed makes a failing test
reproducible: you can tell the site changing from the browser changing. Rerolling per
run means every red test has two possible causes and no way to separate them, which
is the opposite of what a test suite is for.

**`restart` and `keepBrowserState` interact with the profile.** CodeceptJS can keep a
browser between scenarios. That is fine and usually what you want, but note the
session then accumulates cookies and storage across tests, which is a correlation
surface if the suite is pointed at a real site instead of a staging one.

## What you give up compared to driving it from Python

The same two things every non-Python route gives up, and you should know them before
you pick this layer:

- **Humanised pointer motion.** The preference enables it, but the paths are drawn
  by the Python driver from the seed. From CodeceptJS the pointer still teleports.
- **Timezone and locale resolved from the proxy exit.** Those are computed at launch
  by the Python wrapper. Here you get whatever `prefs.json` was generated with, so a
  proxy in another country will disagree with the browser unless you regenerate per
  proxy.

If the suite is JavaScript and rewriting it is not on the table, this is the right
trade. If you are choosing freely and the fingerprint has to hold up against
something that looks at behaviour, drive it from Python.

## Checking it worked

Add a scenario that visits a fingerprint page and asserts on the user agent and the
WebGL renderer. The user agent should say Firefox and the version should match
`invisible-playwright version`. If the renderer comes back as a software or basic
renderer, either the prefs did not load or the machine has no GPU, and both are worth
knowing before the suite reports green.

## Short answers to the questions that lead here

**Does CodeceptJS support a custom Firefox executable?** Yes. Anything under the
`firefox` key in the Playwright helper config is spread straight into Playwright's
launch options, so `executablePath` and `firefoxUserPrefs` both arrive.

**Where exactly does the config go?** Under `helpers.Playwright.firefox`, not at the
helper's top level. At the top level it is a CodeceptJS option; one level down it is a
Playwright one.

**Can I set a proxy?** Yes, in the same block, as Playwright's `proxy` object, and
that is the whole answer for an `http://` or `https://` endpoint. For a `socks5://`
one, leave it out of the helper config and pass it to the prefs generator instead,
`get_default_stealth_prefs(seed=1, humanize=True, proxy={...})`. The SOCKS
credentials travel in the preferences, and a proxy set through Playwright's option is
applied to every request ahead of them with no username or password attached.

**Why should I not set `userAgent` here?** Because CodeceptJS puts it right next to the
options above, which makes it easy to set by accident, and it is the one value that has
to come from the engine rather than from you.

**Do my existing tests need changes?** No. The helper API is the same. This is a
launch-time swap and nothing else.

**Does `keepBrowserState` cause problems?** Not by itself, but the session then
accumulates cookies and storage across scenarios, which is a correlation surface if the
suite points at a real site instead of a staging one.
