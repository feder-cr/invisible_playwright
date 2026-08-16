<p align="center">
  <a href="https://feder-cr.github.io/invisible_playwright/"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/scrapeorbit-banner.png" alt="ScrapeOrbit - find and scrape any company on Earth" width="760"></a>
</p>
<p align="center">
  <b>Find and scrape any company on Earth.</b> No login, no signup. Just search and scrape.<br>
  <a href="https://feder-cr.github.io/invisible_playwright/">Open ScrapeOrbit</a>
</p>

<h2></h2>

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/banner-dark.png">
  <img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/banner-light.png" alt="invisible_playwright" width="720">
</picture>
<h3 align="center">Free antidetect browser stealth for Playwright: undetected Firefox fingerprint, headless or headed.<br>
Python web scraping and captcha bypass. Open source, and it passes every bot detection test.</h3>
</div>

![invisible_playwright - 5/5 detection suites passed](https://raw.githubusercontent.com/feder-cr/invisible_playwright/7a8693c6b4386e9a84dd93bedc479ca8654482e1/docs/screenshots/hero.gif)

## How it works

Anti-bots ask two questions, and reCAPTCHA, hCaptcha and Cloudflare Turnstile score the answers. invisible_playwright answers yes to both.

**1. Is this a real browser?** Yes. It is Firefox, patched at the C++ source level.

- The browser fingerprint is set inside the engine, not injected into the page: navigator, screen, GPU/WebGL, canvas, fonts, audio, WebRTC, timezone, network. Headless or headed, the same values either way.
- No JS shim, no override, no seam to read.

**2. Is a real person using it?** Yes. The actions are humanized in the driver.

- Every click, hover and drag follows a natural mouse path with human timing, no teleporting cursor.
- Each input is byte-identical to a real mouse: real input source, pressure, trusted events.

Driven by the standard Playwright API. Full breakdown: [feder-cr/firefox_antidetect_patch](https://github.com/feder-cr/firefox_antidetect_patch).

---

## Still seeing captchas or anti-bot? It's the proxy.
Once the browser is handled it stops being the variable. If you are still getting challenged, the tell is no longer the browser, it is the IP you come from. Around 90% of proxies are public: anyone can rent the same address, so it is already known and sits on the blocked-IP lists sites check. A perfect browser on a known IP still loses.

---

## Install

```bash
pip install invisible-playwright
python -m invisible_playwright fetch      # one-time ~238 MB download (~544 MB unpacked), sha256-verified
```

Supported platforms: **Windows x86_64**, **Linux x86_64 / arm64**, **macOS arm64 / x86_64**. On macOS the app is ad-hoc signed (not notarized): if Gatekeeper complains, clear the quarantine flag once with `xattr -dr com.apple.quarantine` on the cached `Firefox.app`.

---

## Usage
### Random fingerprint per session
**100% Playwright-compatible** - sync and async, all methods, zero API changes. If you already use Playwright, switching is two lines:

```diff
- from playwright.sync_api import sync_playwright
- with sync_playwright() as p:
-     browser = p.firefox.launch()
+ from invisible_playwright import InvisiblePlaywright
+ with InvisiblePlaywright() as browser:
```

Every session gets a distinct fingerprint (GPU, audio, fonts, screen, ~200 fields) and Bezier-curve mouse motion.

**Sync**
```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(proxy={"server": "socks5://...", "username": "u", "password": "p"}) as browser:
    page = browser.new_page()
    page.goto("https://example.com")
    page.click("#submit")   # mouse arcs to the button on a Bezier curve
```

**Async**
```python
from invisible_playwright.async_api import InvisiblePlaywright

async with InvisiblePlaywright(proxy={"server": "socks5://...", "username": "u", "password": "p"}) as browser:
    page = await browser.new_page()
    await page.goto("https://example.com")
    await page.click("#submit")
```

The `browser` object is a `playwright.sync_api.Browser` / `playwright.async_api.Browser` - every Playwright method works as-is.

Log the seed to replay a run:

```python
sf = InvisiblePlaywright()
with sf as browser:
    print("seed =", sf.seed)
    # ...
```

### Reproducible fingerprint

```python
with InvisiblePlaywright(seed=42) as browser:
    ...   # same GPU, same canvas hash, same audio context, every run
```

### Proxies

```python
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}
with InvisiblePlaywright(proxy=proxy) as browser:
    ...
```

Schemes supported: `socks5`, `socks4`, `http`, `https`. DNS is routed through the proxy by default, no local leak.

### Timezone

The browser timezone follows `timezone=`:

```python
# default: timezone is auto-derived from the egress IP (proxy egress if a
# proxy is set, otherwise the host's own public IP)
with InvisiblePlaywright(proxy=proxy) as browser:
    ...

# explicit IANA zone always wins, the only way to force a specific zone
with InvisiblePlaywright(proxy=proxy, timezone="America/New_York") as browser:
    ...
```

### Pinning specific fingerprint fields

By default everything comes from `seed`. To force specific values while the rest stays seed-derived:

```python
with InvisiblePlaywright(
    seed=42,
    pin={
        "gpu.renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4090 Direct3D11)",
        "gpu.vendor":   "Google Inc. (NVIDIA)",
        "screen.width":  2560,
        "screen.height": 1440,
        "hardware.concurrency": 16,
    },
) as browser:
    ...
```

Full list of pinnable keys, how pinning interacts with the Bayesian sampler, and common patterns are in **[docs/pinning.md](docs/pinning.md)**.

---

## CLI

The installed command is `invisible-playwright`, with a hyphen. `python -m
invisible_playwright` works identically and needs nothing on PATH.

```bash
invisible-playwright fetch    # download the engine if missing, check every cached
                              # one against the seal, print the path
invisible-playwright version  # wrapper, core and engine versions, and where the
                              # engine is cached
```

## Documentation, guides and comparisons

All of it reads better, and is searchable, in
**[the wiki](https://github.com/feder-cr/invisible_playwright/wiki)**,
organised into four sections instead of one flat list:

- **[Documentation](docs/documentation.md)** -
  installation, the two-line switch from plain Playwright, proxy/timezone
  configuration, pinning specific fields, the CLI.
- **[Guides](docs/guides.md)** - how
  detection actually works, in seven groups: browser identity, canvas/WebGL/fonts/
  audio, network and WebRTC, the automation layer, AI agents, the detectors themselves
  explained from source, and testing.
- **[Comparisons](docs/comparisons.md)** -
  against Camoufox, Patchright, nodriver and playwright-stealth, and the case for
  Firefox over Chromium generally.
- **[Integrations](docs/integrations/)** -
  Scrapy, Crawlee, Robot Framework, CodeceptJS, test runners, Playwright MCP, and the
  frameworks it does not fit, by name.

If you don't know where to start: [Three ways to make Playwright undetected](docs/playwright-stealth-levels.md)
is the map most other pages link back to, [Playwright detected as a bot on one site](docs/playwright-detected-as-bot.md)
is the troubleshooting order, and [navigator.webdriver is not the tell you think it is](docs/navigator-webdriver-explained.md)
explains the most famous property in this space and why patching it alone buys you
almost nothing.
- [crawl4ai stealth and custom browser engines](docs/crawl4ai-stealth-custom-browser.md) - browser_type accepts firefox but there is no executable_path; where the adapter seam is
- [Why headless browsers render different fonts](docs/headless-fonts-differ.md) - the three causes, the per-platform font sets, and why the fix is not installing more fonts
- [How to make Linux and macOS report real Windows fonts](docs/bundled-fonts-cross-platform.md) - one manifest, three font backends convinced not to ask the host, and the four seams still open
- [measureText and TextMetrics as a fingerprinting surface](docs/measuretext-textmetrics-fingerprinting.md) - ten-plus numbers from one call needing no permission prompt, and the two mistakes we made fixing it
- [What privacy.resistFingerprinting really does](docs/resist-fingerprinting.md) - and why this project sets it to false on purpose
- [The ChromeDriver cdc_ variable](docs/cdc-variable-explained.md) - why renaming it is not removing it, and what that generalises to
- [What bot.sannysoft.com actually checks](docs/sannysoft-explained.md) - row by row, and the canvas-in-iframe test nobody reads
- [How CreepJS decides you are lying](docs/creepjs-explained.md) - four detection techniques, and why blocking the probe is itself recorded
- [Firefox preferences that silently do nothing](docs/firefox-prefs-not-applying.md) - five reasons, starting with the one that cost us a real bug
- [What BotD actually detects](docs/botd-explained.md) - twenty detectors, and why most are not about bots at all
- [Why a FingerprintJS visitor ID changes](docs/fingerprintjs-visitor-id.md) - it is a hash of 41 components, so one moving moves all of it

## Related projects

The open-source neighbours, and what each one is for.

**On the Firefox side**

- **[Camoufox](https://github.com/daijro/camoufox)** - an anti-detect Firefox that also patches at the C++ level. It covers a wider surface and ships its own fingerprint database; this project derives a fingerprint from a seed with a Bayesian sampler, so one number reproduces one machine. [Full comparison](docs/vs-camoufox.md).
- **[LibreWolf](https://librewolf.net)** - a Firefox fork with privacy defaults. It ships a configured binary for people to browse with; this ships source patches plus an automation wrapper.
- **[arkenfox/user.js](https://github.com/arkenfox/user.js)** - Firefox hardening through preferences. Where a preference is enough, use it; this project patches C++ where one is not.

**On the Chromium side**

- **[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)** - a patched Playwright fork, so the stealth work lands in the driver rather than in the browser binary. [Full comparison](docs/vs-patchright.md).
- **[nodriver](https://github.com/ultrafunkamsterdam/nodriver)** - the successor to `undetected-chromedriver`, driving Chrome over CDP directly and removing the WebDriver-flavoured tells. [Full comparison](docs/vs-nodriver.md).
- **[playwright-stealth](https://github.com/Mattwmaster58/playwright_stealth)** - an init-script patch applied before the page loads. Its own maintainer calls it a proof-of-concept; [full comparison](docs/vs-playwright-stealth.md).
- **[puppeteer-extra-plugin-stealth](https://github.com/berstend/puppeteer-extra)** - the original of this init-script lineage, still widely recommended. Its repository's last substantive commit is from mid-2024; [what that means in practice](docs/puppeteer-extra-stealth-unmaintained.md).
- **[selenium-stealth](https://github.com/diprajpatra/selenium-stealth)** - the same approach on Selenium/CDP. Its repository's last commit is from December 2021; [what that means in practice](docs/selenium-stealth-unmaintained.md).
- **[pyppeteer](https://github.com/pyppeteer/pyppeteer)** - the unofficial Python port of Puppeteer. Its own README says it's unmaintained and points to `playwright-python` instead; [what that recommendation is actually about](docs/pyppeteer-unmaintained-playwright.md).
- **[rebrowser-patches](https://github.com/rebrowser/rebrowser-patches)** - fixes the `Runtime.enable` CDP leak on Chromium, independently converging on close to the same fix Patchright uses. [Full comparison](docs/vs-rebrowser-patches.md).
- **[fingerprint-suite](https://github.com/apify/fingerprint-suite)** - generates a coherent fingerprint with a Bayesian network, close to this project's own generation approach, then injects it into a Playwright or Puppeteer page on either Chromium or Firefox. [Full comparison](docs/vs-fingerprint-suite.md).
- **[playwright-with-fingerprints](https://github.com/bablosoft/playwright-with-fingerprints)** - injects fingerprint values sourced from a remote paid service, Windows-only, pinned to a specific Chromium build. [Full comparison](docs/vs-playwright-with-fingerprints.md).

Which of these fits depends on the layer your problem is at, and on whether you need Firefox or Chromium. [Three ways to make Playwright undetected](docs/playwright-stealth-levels.md) works through what each layer can and cannot reach, including what this one costs.

If you are picking between engines rather than tools, note that a large share of AI agent frameworks drive Chromium over CDP, which decides the question for you: [AI browser agents and stealth](docs/ai-browser-agents-stealth.md).

---

## License

MIT - see [LICENSE](https://github.com/feder-cr/invisible_playwright/blob/main/LICENSE). The patched Firefox binary is distributed under the MPL-2.0 (Firefox upstream license). The C++ patches against mozilla-central that produce that binary are at [feder-cr/firefox_antidetect_patch](https://github.com/feder-cr/firefox_antidetect_patch).

---

## Disclaimer

This project is for educational purposes only. It is provided as-is, with no warranties. I take no responsibility for how it is used. Use it at your own risk and in compliance with the laws of your jurisdiction.

---

<p align="center">
  Built by <a href="https://it.linkedin.com/in/federico-elia-5199951b6">Federico Elia</a>
  &nbsp;<a href="https://it.linkedin.com/in/federico-elia-5199951b6"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/badges/linkedin.svg" alt="LinkedIn"></a>
</p>

<p align="center">
  <a href="https://github.com/feder-cr/invisible_playwright/actions/workflows/tests.yml"><img src="https://github.com/feder-cr/invisible_playwright/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="https://github.com/feder-cr/invisible_playwright/blob/main/LICENSE"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/badges/license.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/badges/python.svg" alt="Python 3.11+"></a>
  <a href="https://github.com/feder-cr/firefox_antidetect_patch/releases"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/main/docs/badges/firefox.svg" alt="Firefox 151.0"></a>
  <a href="https://github.com/feder-cr/invisible_playwright/stargazers"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/badges/docs/badges/stars.svg" alt="GitHub stars"></a>
  <a href="https://github.com/feder-cr/invisible_firefox/releases/tag/usage-counter"><img src="https://raw.githubusercontent.com/feder-cr/invisible_playwright/badges/docs/badges/launches.svg" alt="browser launches"></a>
</p>
