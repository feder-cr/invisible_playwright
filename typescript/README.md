# invisible-playwright for TypeScript

Drive invisible_playwright's patched Firefox through the ordinary Playwright TypeScript API. The Python package prepares the sealed browser, seeded fingerprint, proxy/locale/timezone settings, and profile before Playwright starts; pages, locators, requests, and events remain standard Playwright objects.

## Install

Both runtimes are required. The browser is downloaded and verified automatically on the first launch; there is no TypeScript CLI or separate fetch step.

```bash
pip install invisible-playwright
npm install invisible-playwright
```

Node.js 18+ and Python 3.11+ are supported. If Python is not available as `python3` on Linux or `python` on Windows, pass `pythonExecutable` or set `INVPW_PYTHON`.

## Usage

```ts
import { InvisiblePlaywright } from "invisible-playwright";

const invisible = new InvisiblePlaywright({
  seed: 42,
  proxy: {
    server: "socks5://gate.example.com:1080",
    username: "user",
    password: "pass",
  },
});

try {
  const context = await invisible.launch();
  const page = context.pages()[0] ?? await context.newPage();
  await page.goto("https://example.com");
  await page.click("#submit");
  console.log("seed =", invisible.seed);
} finally {
  await invisible.close();
}
```

`launch()` returns a Playwright `BrowserContext`. A persistent context is required because the patched browser reads its fingerprint preferences from the Firefox profile before startup; sending `firefoxUserPrefs` after startup is deliberately rejected. The returned context and every page, locator, route, request, and event use Playwright's normal API.

## Options

- `seed`: reproducible fingerprint seed; omitted means a fresh random seed.
- `pin`: force selected fingerprint fields while the rest stay seed-derived.
- `headless`: same behavior as the Python wrapper. On Linux the default stealth path requires Xvfb; `INVPW_TRUE_HEADLESS=1` opts into Firefox's real headless rendering path.
- `proxy`: Playwright proxy object. SOCKS credentials and DNS routing are composed into the Firefox profile automatically.
- `humanize`: `true` by default, `false` to disable, or a number to cap pointer movement duration in seconds. The browser-side generator humanizes ordinary Playwright pointer calls.
- `locale` / `timezone`: default to automatic egress-derived values; explicit values win.
- `extraArgs` / `extraPrefs`: additional Firefox arguments or preferences.
- `binaryPath`: deliberately use a specific sealed binary.
- `profileDir`: reuse a persistent Firefox profile. Without it, the wrapper creates and removes an ephemeral profile.
- `showCursor`: show or hide the browser-chrome cursor marker.
- `pythonExecutable`: Python interpreter containing the matching `invisible-playwright` installation.

Always call `close()`. It closes the Playwright context, removes an ephemeral profile, and stops the Linux virtual display when one was created.
