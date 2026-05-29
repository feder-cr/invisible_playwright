# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-05-28

### Added
- Public config helpers in `invisible_playwright.config`: `get_default_stealth_prefs(seed, *, pin, locale, timezone, extra_prefs, humanize, virtual_display)` returns a complete `firefox_user_prefs` dict; `get_default_args()` returns the baseline CLI args list (currently empty). Both also re-exported at the package root.
- `invisible_playwright.ensure_binary` re-exported at the package root for parity with the `cloakbrowser.download.ensure_binary` integration pattern that downstream projects (Skyvern, Crawlee, agno) already expect.
- These helpers let third-party fetchers (changedetection.io plugins, Crawlee `BrowserPool` subclasses, agno toolkits) drive `playwright.firefox.launch(executable_path=..., firefox_user_prefs=...)` themselves without depending on the `InvisiblePlaywright` context manager owning the lifecycle.
- `tests/unit/test_config_public.py`: 14 unit tests covering deterministic seed, locale / timezone / pin / extra_prefs / humanize variations, and round-trip via the public namespace.

### Unchanged
- `InvisiblePlaywright` context manager surface is identical (backwards compatible).
- `BINARY_VERSION` stays at `firefox-7`. Python-only release; no new Firefox build.

## [0.1.8] - 2026-05-23

### Fixed
- [#20](https://github.com/feder-cr/invisible_playwright/issues/20): cross-origin iframes were unreachable from Playwright. `element_handle.content_frame()` returned `None`, `frame.evaluate()` threw cross-origin SOP errors, and `frame_locator(...).click()` timed out even with `force=True`. Root cause: FF150 defaults `fission.webContentIsolationStrategy=1` (`IsolateEverything`), which site-isolates every cross-origin iframe into a separate `webIsolated` content process even when `fission.autostart=False`. The parent's Juggler FrameTree then has a Frame placeholder with no docShell and no URL — every protocol op that needs to enter the iframe fails. Fix: pin `fission.webContentIsolationStrategy=0` (`IsolateNothing`) in the baseline prefs. The setting can be flipped back per session via `extra_prefs={"fission.webContentIsolationStrategy": 1}`.

### Added
- `tests/test_cross_origin_iframe.py`: 4 unit + 5 e2e regression sentinels for cross-origin iframe interaction. The e2e layer runs entirely offline against two local HTTP servers on `127.0.0.1` (two ports = two SOP origins) and covers `page.frames` URL tracking, `content_frame()`, `frame.evaluate()`, `frame_locator(...).locator(...)`, and end-to-end `dispatch_event("click")` for plain, sandboxed and titled iframes. A future FF upgrade or fingerprint A/B that flips the pref back to `1` will fail the suite before shipping.

### Unchanged
- `BINARY_VERSION` stays at `firefox-7`. Python-only release; no new Firefox build was needed.

## [0.1.7] - 2026-05-21

### Fixed
- [#18](https://github.com/feder-cr/invisible_playwright/issues/18): Tab crash when running with `headless=True` on Windows on pages that trigger cross-process navigation. Two separate bugs that only manifested together: (1) the Chromium content sandbox at default level 6 puts content processes on `kAlternateWinstation`, but the wrapper hides the browser window on its own alt-desktop (`CreateDesktop` for headless on Windows). Mismatched desktops → cross-process navigations couldn't reparent windows → content process exits cleanly and Playwright fires `page.on('crash')`. (2) The canvas2d `getImageData` stealth spoof wrote to a read-only mapped `DataSourceSurface`. On GPU-backed canvases that memory is write-protected → segfault during the final `getImageData` at page unload. Wrapper now sets `security.sandbox.content.level=4` in the alt-desktop workaround set, and `firefox-7` ships the source fix that moves the noise to the JS array's writable backing buffer.

### Changed
- `BINARY_VERSION` bumped from `firefox-5` to `firefox-7`. `firefox-6` was rolled back when its partial fix turned out to be wrong (the iframe-burst hypothesis was a dead end; bisection in the evening found the real two-bug cause documented above).

## [0.1.6] - 2026-05-21

### Added
- `profile_dir=` kwarg on `InvisiblePlaywright` (sync + async). When set, the session uses `firefox.launch_persistent_context()` so cookies, localStorage, sessionStorage, extensions, cache and prefs are kept on disk between runs. `__enter__` returns a `BrowserContext` directly: `with InvisiblePlaywright(profile_dir=p) as ctx: ctx.new_page()`. Pair with a stable `seed=` to also pin the fingerprint identity across runs. First run creates the dir; subsequent runs reuse it.

### Fixed
- `launch_persistent_context(timezone_id="…")` no longer times out at 180s. Root cause: `juggler/content/main.js` calls `docShell.overrideTimezone(...)` on every navigation; the patched Firefox up to firefox-4 didn't expose that IDL method on `nsIDocShell`, so the call threw `TypeError: docShell.overrideTimezone is not a function`. On the non-persistent path the error fired *after* launch and was harmless; on the persistent path it blocked the launch handshake. `firefox-5` ships the C++ method (see `patch.md` section 19); this release removes the firefox-4 era Python workaround that was filtering `locale`/`timezone_id` out of the persistent context kwargs.

### Changed
- `BINARY_VERSION` bumped from `firefox-4` to `firefox-5`. The Python source delta is JS/Python only; the new Firefox build adds 50 lines of C++ in `docshell/base/nsIDocShell.idl` + `nsDocShell.cpp`.

## [0.1.5] - 2026-05-20

### Fixed
- [#15](https://github.com/feder-cr/invisible_playwright/pull/15): `python -m invisible_playwright fetch` raised `RuntimeError: no SHA256 for firefox-150.0.1-stealth-linux-x86_64.tar.gz in checksums.txt` for every user because the parser kept the `*` binary-mode prefix that `sha256sum` writes in front of filenames. Now `.lstrip("*")` is applied to the key. Reporter + patch: [@LostBoxArt](https://github.com/LostBoxArt). Unrelated to the `firefox-N` binary; existing caches still work, only first-time fetches were broken.

## [0.1.4] - 2026-05-20

### Fixed
- [#13](https://github.com/feder-cr/invisible_playwright/issues/13): every page that threw an uncaught JS error (e.g. bunny.net) crashed the Playwright client with `TypeError: Cannot read properties of undefined (reading 'url')`. Root cause: upstream Playwright Juggler added a required `location` field to the `Page.uncaughtError` event in the 2026-05-07 roll ([microsoft/playwright@c8604ec](https://github.com/microsoft/playwright/commit/c8604ecd97)); our fork was carrying the pre-roll schema in every `firefox-N` build. Fix matches upstream — Runtime.js builds the `errorLocation`, PageAgent.js forwards it on both worker and runtime error paths, Protocol.js declares the schema field. Reporter: [@dionorgua](https://github.com/dionorgua).

### Changed
- `BINARY_VERSION` bumped from `firefox-3` to `firefox-4`. JS-only change inside `chrome/juggler/`; `xul.dll` and `firefox.exe` are byte-identical to `firefox-3`.

## [0.1.3] - 2026-05-19

### Changed
- `BINARY_VERSION` bumped from `firefox-2` to `firefox-3`. The new archives on both Windows and Linux are built from a clean clone of [feder-cr/invisible_firefox#stealth/150](https://github.com/feder-cr/invisible_firefox/tree/stealth/150) — the consolidated source-of-truth fork (renamed from `feder-cr/firefox`; the companion `feder-cr/firefox-stealth` patches repo was deleted, all patches now live as commits on top of `mozilla-firefox/firefox`).
- The patched Firefox archive now ships the **proper C++ implementation** of `windowUtils.jugglerSendMouseEvent`, replacing the JS shim from 0.1.2.

### C++ fixes landed in this release
- **C1+C2**: `setDownloadInterceptor` IDL + cpp (re-landed for FF150).
- **C4**: 5 `nsIDocShell` stealth attributes (`fileInputInterceptionEnabled`, `overrideHasFocus`, `bypassCSPEnabled`, `forceActiveState`, `disallowBFCache`).
- **C5**: `LauncherProcessWin.cpp` + `nsWindowsWMain.cpp` juggler-pipe handle inheritance — without this, the Playwright pipe disconnects immediately on launch.
- **C6**: `juggler-navigation-started-renderer` / `-browser` observer notifications in `nsDocShell.cpp` and `CanonicalBrowsingContext.cpp` — without these, `Page.ready` never fires and `ctx.new_page()` hangs.
- **C7 (partial)**: storage stub for `nsIDocShell.languageOverride`. Workaround `InvisiblePlaywright(locale="")` recommended until full BC FIELD port lands.

### Verified
- Both archives built from same source: feder-cr/invisible_firefox commit `68906f1f9c55`.
- Windows + Linux smoke suite green: launch, `ctx.new_page()`, `page.mouse.{move,down,up,click,wheel}`, `navigator.webdriver=false`, sannysoft 32/33 PASS.
- SHA256 published in `checksums.txt` on the `firefox-3` release.

### Notes
- This is the first release with a native Linux build of the patched binary (previous `firefox-3` draft mentioned shipping the Linux firefox-2 archive byte-for-byte; that no longer applies — Linux now has the full C++ patch series).

## [0.1.2] - 2026-05-18

### Changed
- `BINARY_VERSION` bumped from `firefox-1` to `firefox-2`. The patched Firefox archive on GitHub Releases now contains the JS fix from 0.1.1 (every `page.mouse.*` / `page.click()` / `locator.click()` / `mouse.wheel()` failure on the FF150 binary). Users on 0.1.1 must run `python -m invisible_playwright clear-cache && python -m invisible_playwright fetch` to pick up the new archive.

### Verified
- Archive integrity tests on both platforms: Windows zip extracted + booted via Playwright (`mouse.move + click + page.click(selector)` all succeed end-to-end), Linux tarball file-level checks (firefox/libxul.so sizes, byte-identity of patched JS files against Windows source). 21/21 assertions pass.
- SHA256 published in `checksums.txt` on the `firefox-2` release.

## [0.1.1] - 2026-05-18

### Fixed
- **Critical**: every `page.mouse.*`, `page.click(selector)`, `locator.click()`, `page.hover()`, `mouse.wheel()` failed on the patched Firefox 150 binary with `win.windowUtils.jugglerSendMouseEvent is not a function`. The Juggler JS was porting calls to a Playwright-specific C++ method that was never landed in the FF146→FF150 port; replaced with the Mozilla chrome-scope `win.synthesizeMouseEvent` helper which is present in FF150. Six call sites patched across `juggler/protocol/PageHandler.js` and `juggler/content/PageAgent.js`. Reporter: [@trob9](https://github.com/trob9) — [#9](https://github.com/feder-cr/invisible_playwright/issues/9).
- `_linkedBrowser.scrollRectIntoViewIfNeeded()` is now guarded at both call sites in `PageHandler.js` (`dispatchMouseEvent` and `dispatchWheelEvent`) — the method is not present on the shipped FF150 `<browser>` element, so the unguarded call threw before the mouse event was dispatched.

### Added
- `tests/test_mouse.py`: 12-case regression suite covering every patched code path (mouse.move/click/dblclick/right-click, modifiers, locator.click/hover, wheel, manual mousedown+up, off-viewport move, humanize intermediate moves, scroll-and-click on offscreen element). Test cases inspired by `microsoft/playwright-python/tests/async/test_click.py`.
- Community standards: `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`.

### Notes
- The Stealthfox humanize Bezier expansion continues to fire intermediate `mousemove` events; the swap to `synthesizeMouseEvent` does not change the human-trajectory behavior (verified by test).
- The reCAPTCHA v3 score (0.90) and FingerprintPro / CreepJS results documented in the README are unaffected — `synthesizeMouseEvent` is a legitimate Mozilla helper that does not increase the anti-detect surface.
- A binary refresh of the patched Firefox archive on GitHub Releases is required for users to receive this fix (the Juggler JS is shipped inside the archive). The `BINARY_VERSION` will be bumped to `firefox-2` in that release.

## [0.1.0] - 2026-05-13

### Added
- Initial public release.
- `InvisiblePlaywright` sync and async context managers — drop-in replacement for `playwright.sync_api.Browser` / `async_api.Browser`.
- StealthFox humanize hook: Bezier-curve mouse trajectories enabled by default.
- `_fpforge` Bayesian fingerprint sampler with ~400 fields per session.
- CLI: `invisible-playwright fetch | path | version | clear-cache`.
- Pinnable fingerprint fields via `pin={...}` (see `docs/pinning.md`).
- SOCKS5 / SOCKS4 / HTTP / HTTPS proxy support with auth.
- Linux x86_64 and Windows x86_64 binary support.

[Unreleased]: https://github.com/feder-cr/invisible_playwright/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/feder-cr/invisible_playwright/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.1.0
