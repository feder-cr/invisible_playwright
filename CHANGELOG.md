# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.7] - 2026-07-28

### Changed
- Requires `invisible-core==18.8.0`, which carries the work of the last two days: the prefs builder split into fourteen named steps with its output hash-identical across 400 profiles, the GeoIP fetch moved out of `download`, `invisible_core.pin` as a public module, and a publish gate whose index cross-check finally asks about the package it is gating.
- Nothing in this package's behaviour changed. The imports moved: it asks `invisible_core` for what that package exports instead of reaching into its private modules, and its own back-compat shims are for users again rather than for its own source.

### Fixed
- The suite could not tell a collapsed selection from a pass in one place and the install e2e imported the package it tests the install of in another; both are CI-only and neither reached a user.

## [0.4.6] - 2026-07-27

### Changed
- This package can no longer be published without a gate. A release used to be a bare `twine upload` of whatever happened to be in a directory - which is how 0.4.4 reached the index built from a tree that predated the fix it was meant to carry. The gate is `invisible_core.release`, one implementation for all three packages, and the pre-push hook runs it on a release tag. What makes it work is that `publish` builds and uploads *what it just built*, so a stale directory cannot be what ships.
- `PUBLISHED.json` records what actually reached the index, back-filled from the artifacts themselves.
- Requires `invisible-core==18.6.0`.

## [0.4.5] - 2026-07-27

### Fixed
- An environment holding an older `invisible-core` failed on the browser launch path with `ImportError: cannot import name 'IANA_TO_POSIX_TZ' from 'invisible_core'` - a symbol name, from a package whose version the reader did not choose. It now states which core version this build needs, keeps the original error inside the message for whoever is debugging, and ends with the command that fixes it. **0.4.4 has this bug and cannot be corrected in place; a PyPI filename is never re-uploaded. Use 0.4.5.**

## [0.4.4] - 2026-07-27

### Changed
- The process-lifetime machinery moved into `invisible-core`, shared with the profile manager. Both packages launch the same browser and both had the same leak; the manager had it a day longer because the fix lived here. `invisible_playwright._reaper` stays as a re-export, so nothing you import changes.
- The IANA-to-POSIX timezone table is the core's now. This package carried a byte-identical copy of all ten entries, including the Arizona row that exists because mapping it to `MST7MDT` made libc apply DST and an identification service deduce a Denver origin - and the core's own copy carried a comment admitting it had been copied from here. Two copies with a documented keep-in-sync obligation and nothing enforcing it.

### Fixed
- The lifetime guard counted an attempted job assignment as a successful one, so a process it had failed to adopt was never retried and the count it reported was a number of tries. Measured on the sibling package, that reported eight processes held while eight survived the kill. This package shipped the same accounting.

## [0.4.3] - 2026-07-27

### Fixed
- `async with InvisiblePlaywright(...)` no longer leaves browsers behind when the runner is killed. The lifetime guard added in 0.4.0 reached the sync entry point only, so every async user kept the entire leak while this file said it was closed. If you drive this package with `async with` on Windows, upgrading is the fix.
- The guard itself stopped adopting the process tree after the first process it found, so part of the tree stayed outside the kernel job even on the sync path. Found by killing the runner mid-session and counting the browsers still carrying that session's token: sync 0/0/0/0, async 2/0/0/2 - the same code on both, differing only in timing, which is what made an intermittent race look like a working feature. After the fix: ten sessions, zero survivors on both.
- `INVPW_TRUE_HEADLESS` now works on both entry points. It was read in the async class alone, so a documented environment variable applied or did not depending on whether you wrote `with` or `async with`.

### Changed
- The two entry points share their session logic instead of restating it: prefs, the launch environment and the IANA-to-POSIX timezone table live in one module both call. 80.6% of the async class was the sync class retyped, which is how a fix reaches one of them and a release note describes both.
- Requires `invisible-core==18.3.0`.

## [0.4.2] - 2026-07-26

### Changed
- The lifetime guard is a strategy object instead of a module of flags. `SessionToken` (a value object), `find_processes` (the only place psutil appears) and `LifetimeGuard` with a `JobObjectGuard` / `NullGuard` pair: `os.name` is tested in exactly one place, so no launcher code branches on the platform, and the Null implementation reports that it guarantees nothing rather than doing nothing while looking successful. Behaviour unchanged.
- The stroke planner is six named stages instead of one 203-line function, and the idle planner is a table of episodes instead of an if/elif chain with the weights written as bare literals in the branch conditions. Both verified byte-identical against recorded output, which caught two float-associativity regressions that no other test could see - `a * b * c` and `a * (b * c)` differ in the last bit, and that is enough to change a rounded pixel and every draw after it. A permanent fingerprint test now covers 576 cases and 16135 waypoints.

## [0.4.0] - 2026-07-26

### Added
- Pointer movement is generated in this package, from the session seed, instead of by the engine. Existing code gets it with no edit: the six internal funnels every pointer action already goes through are wrapped, so a `page.click` written a year ago moves through the new generator. A plain `sync_playwright()` browser in the same process is untouched.
- Movement between actions: drift while reading, motion while the wheel turns, overshoot and correction, and movements that end on nothing. If every movement ends on something clickable, the set of endpoints is itself a signature however good each path is.
- `INVPW_CURSOR_ENGINE` selects the generator: `python` (default), `binary` (the previous behaviour) or `off`.
- The browser process tree is tied to this process's lifetime on Windows, so it cannot outlive a runner that was killed. Measured: eight survivors on the first attempt and twelve on the second before, zero after. `psutil` becomes a dependency for this - an optional reaper is absent exactly on the machines that need it, and silently. **This reached the SYNC entry point only. `async with` kept the whole leak through 0.4.2 - see 0.4.3.**

### Changed
- Install is now `pip install invisible-playwright`, from the index. The git URL is gone from the README, the CLI docs and the generated release notes. Nothing about the package changes for someone who was already installing it from git, except that pip can now see what it is holding.
- `invisible-core` is declared as an exact version specifier (`invisible-core== an exact version`) instead of a `git+https://...` direct reference. A direct reference carries no version, so there is nothing for `pip check` to compare and a broken environment reports clean; a real specifier is reported, exit 1, and a plain reinstall of this package repairs it. The cost is that a binary bump is now a release of every package that pins the core, which is deliberate.
- Requires `invisible-core>=18.2.0`, which bounds the timezone lookup as a step rather than only per request.

### Fixed
- `hover()` no longer fails intermittently on Windows. The approach now completes before the automation layer's hit-target check is installed, so the only event inside its window is that layer's own move, on target. Measured on a page whose target needs a scroll: 3 failures out of 3 before, 0 out of 3 after.

## [0.3.5] - 2026-07-24

### Changed
- Playwright pin is back to `>=1.55,<=1.61.0`: the floor drop to `1.40` shipped in 0.3.4 is reverted. The conservative CI-tested floor (1.55) is kept together with the tested upper cap (1.61.0).

## [0.3.4] - 2026-07-24

### Changed
- Playwright floor lowered to `>=1.40,<=1.61.0`. The `1.55` floor was never a real compatibility bound - it was only the single minor that 0.3.1 had pinned. The `firefox-18` binary was smoke-tested (launch, `new_context`, navigate, evaluate, click) against 1.40, 1.45, 1.50 and 1.54 and passed on all four, so anyone on an older client keeps working; the upper cap stays at the tested 1.61.0. Reverted in 0.3.5.

## [0.3.3] - 2026-07-24

### Changed
- Playwright pin is now `>=1.55,<=1.61.0` (was `>=1.55,<1.62`). The open upper bound would let an untested version below 1.62 install and break a fresh setup the way 1.61 first did; the cap is now the exact version validated against this binary, 1.61.0. It moves forward on purpose, once a newer Playwright is tested against the binary.
- The expected Firefox version in the tests is derived from the constant instead of a hardcoded literal, so a binary bump no longer needs the test edited alongside it.

## [0.3.2] - 2026-07-24

### Changed
- Playwright pin moved to `>=1.55,<1.62` (was `>=1.55,<1.56`). The `firefox-18` binary rebases onto Firefox 151, which is what the latest Playwright (1.61) pairs with, so 1.61 now drives the binary natively - no more pinning to an older client. Both ends of the range were tested against the new binary (full browser suite on 1.61; drift-free protocol on 1.55). `scripts/playwright_pin.txt` -> 1.61.0.
- README badges are served as SVGs from the repo instead of a third-party badge service, so the header no longer depends on an external endpoint; the dynamic Firefox-version badge, which was the flaky one, is now static.

### Fixed
- The CI font gate checks that the whitelisted faces actually LOAD, not only that the family enumerates. Enumerating a font is not loading it, so the previous check could pass on a build that could name a family it could not render.

## [0.3.1] - 2026-07-13

### Fixed
- [#48](https://github.com/feder-cr/invisible_playwright/issues/48): Playwright 1.61 adds an `isMobile` field to the `Browser.setDefaultViewport` Juggler command that the FF150 binary does not accept, which kills the session. The range narrows from `>=1.40,<1.61` to the CI-tested 1.55.x (`>=1.55,<1.56`, single-sourced from `scripts/playwright_pin.txt`) so a fresh install always gets a compatible client. Widened again in 0.3.2, once `firefox-18` rebased onto Firefox 151.

### Changed
- The patched-Firefox source repo is now `feder-cr/firefox_antidetect_patch` (it was `feder-cr/invisible_firefox`, a name the profile manager took over). Release notes, CI references and the release URLs the download tests mock all point at the new name; the binaries themselves are unchanged.
- The wrapper no longer injects the font-list / system-UI environment variables at launch: the binary ships its own font bundle and is self-contained on that front. The integration tests were adapted and the font-sampling tests dropped.

### Added
- `scripts/ci_font_gate.py`: asserts the Windows font persona on every OS, so a Linux or macOS runner catches a font regression that would otherwise only show up on Windows.

## [0.3.0] - 2026-07-03

### Changed
- Pure config (seed -> fingerprint -> prefs, binary download, proxy, geo) is split out into a standalone `invisible-core` package with zero Playwright, so a profile manager can reuse it without pulling Playwright in. The wrapper depends on it and replaces the moved modules with full-alias shims: existing imports (public API, submodules, private names) and `isinstance` checks keep working unchanged. `tests/test_backcompat.py` locks that contract with 6 guards.
- `BINARY_VERSION` walks `firefox-7` -> `firefox-13` across this cycle: `firefox-8`, then `firefox-9` (`firefox-8` was found broken and is refused outright, not merely superseded), `firefox-10`, `firefox-11`, `firefox-12` (the cross-OS render-parity build) and `firefox-13` (geo-aware locale/Intl + the Windows font bundle + the audio gate).
- Playwright is capped at `<1.61` (`>=1.40,<1.61`) and the pin is single-sourced from `scripts/playwright_pin.txt` instead of being written in two places.
- WebGL personas: only the GPU buckets that survive the tampering checks are shipped, and the render-noise seed is decoupled from the persona seed.
- The dead `zoom.stealth.normalize_date_now` baseline pref is dropped.
- New runtime dependencies: `requests[socks]` (SOCKS egress lookup), `maxminddb` (mmdb reader), `tzdata` (IANA database for `zoneinfo`, which Windows lacks). After the split they arrive transitively through `invisible-core`.

### Added
- `timezone="auto"`: the browser timezone is auto-derived from the egress IP. By default (no explicit timezone) it ALWAYS resolves - from the proxy egress when a proxy is set, otherwise from the host's own public IP - so the zone can never disagree with the IP (the classic `timezone_mismatch` signal). An explicit `"Area/City"` is the only way to force a specific zone. On failure: with a proxy the launch raises (no silent host-TZ fallback behind a foreign proxy); without a proxy it falls back to the host TZ so a transient lookup can't break the launch.
- The egress IP is mapped to its IANA zone with an offline mmdb (`daijro/geoip-all-in-one`). It always tracks the upstream weekly rebuild: on every launch the current latest release tag is resolved from the `releases/latest/download` permalink (no GitHub API → no rate limit) and pulled only if newer than the cache, older copies pruned. Offline → the cached copy is reused; never a pinned tag (daijro prunes old releases, so a pin eventually 404s). `STEALTHFOX_GEOIP_MMDB` points at your own `.mmdb` to skip the download.
- `resolve_session_timezone(timezone, proxy)` and `ensure_geoip_mmdb()` re-exported at the package root (plus `GeoTimezoneError`) so integrations that own their launch can reproduce the resolution.
- `tests/test_geo.py` (37) + `tests/test_geoip_update.py` (freshness / auto-update / offline fallback) unit tests.
- Cross-OS render parity (needs `firefox-12`): the same font/canvas/WebGL fingerprint now renders consistently on Windows, Linux and macOS, so a Windows persona looks identical regardless of the host the binary runs on. Each whitelisted font renders a distinct canvas image (font-detection probes that dedup by rendered image keep every name), the standard Windows fonts (Calibri, Franklin Gothic, Gadugi, Javanese Text, Myanmar Text) are always present so the detected font set matches a real Windows install, and the per-seed render-noise leaves a solid-colour reference render byte-exact while still varying real fingerprint renders.
- GPU persona applied on every platform: Linux/macOS hosts now present a coherent Windows GPU (renderer + WebGL parameters) instead of the host's real adapter; pool re-rooted on a real-device GPU mix.
- `tests/test_canvas_render_stealth.py`, `tests/test_webgl_noise_active.py` and new `tests/test_sampler.py` cases: regression guards for per-font canvas distinctness, solid-readback purity under render-noise, and the always-present standard-font invariant.
- macOS support in the wrapper (x86_64 + arm64): the release pipeline builds five targets and the wrapper resolves the archive for the host it runs on.
- Headless is cloaked on Windows and macOS and runs under Xvfb on Linux, instead of the flagged headless mode; CI carries a cloak guard and a WebGL-masking guard. The cloak applies on the async path too.
- The e2e suite runs the real detectors offline (BotD, FingerprintJS, fpscanner, CreepJS, vendored under `tests/vendor/`) and a hermetic SOCKS5 auth + routing e2e, all on CI on every push. The 15 hand-rolled BotD imitations are dropped now that the real one runs.
- `fetch --force` on the CLI, to re-download an archive over a cached one.

### Fixed
- WebRTC behind a proxy ships the validated realness configuration, with CI guards: a fully blocked WebRTC is itself a tell, so the guard asserts the positive form and not merely the absence of a leak.
- The TLS ClientHello matches stock Firefox again: cipher `0xC009` was being offered and stock Firefox does not offer it.
- The humanize prefs were written under the wrong namespace.
- The font pool uses the real Windows 11 family name `franklin gothic medium`; the previous name does not exist on a real install.

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
- [#20](https://github.com/feder-cr/invisible_playwright/issues/20): cross-origin iframes were unreachable from Playwright. `element_handle.content_frame()` returned `None`, `frame.evaluate()` threw cross-origin SOP errors, and `frame_locator(...).click()` timed out even with `force=True`. Root cause: FF150 defaults `fission.webContentIsolationStrategy=1` (`IsolateEverything`), which site-isolates every cross-origin iframe into a separate `webIsolated` content process even when `fission.autostart=False`. The parent's Juggler FrameTree then has a Frame placeholder with no docShell and no URL - every protocol op that needs to enter the iframe fails. Fix: pin `fission.webContentIsolationStrategy=0` (`IsolateNothing`) in the baseline prefs. The setting can be flipped back per session via `extra_prefs={"fission.webContentIsolationStrategy": 1}`.

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
- [#13](https://github.com/feder-cr/invisible_playwright/issues/13): every page that threw an uncaught JS error (e.g. bunny.net) crashed the Playwright client with `TypeError: Cannot read properties of undefined (reading 'url')`. Root cause: upstream Playwright Juggler added a required `location` field to the `Page.uncaughtError` event in the 2026-05-07 roll ([microsoft/playwright@c8604ec](https://github.com/microsoft/playwright/commit/c8604ecd97)); our fork was carrying the pre-roll schema in every `firefox-N` build. Fix matches upstream - Runtime.js builds the `errorLocation`, PageAgent.js forwards it on both worker and runtime error paths, Protocol.js declares the schema field. Reporter: [@dionorgua](https://github.com/dionorgua).

### Changed
- `BINARY_VERSION` bumped from `firefox-3` to `firefox-4`. JS-only change inside `chrome/juggler/`; `xul.dll` and `firefox.exe` are byte-identical to `firefox-3`.

## [0.1.3] - 2026-05-19

### Changed
- `BINARY_VERSION` bumped from `firefox-2` to `firefox-3`. The new archives on both Windows and Linux are built from a clean clone of [feder-cr/firefox_antidetect_patch#stealth/150](https://github.com/feder-cr/firefox_antidetect_patch/tree/stealth/150) - the consolidated source-of-truth fork (renamed from `feder-cr/firefox`; the companion `feder-cr/firefox-stealth` patches repo was deleted, all patches now live as commits on top of `mozilla-firefox/firefox`).
- The patched Firefox archive now ships the **proper C++ implementation** of `windowUtils.jugglerSendMouseEvent`, replacing the JS shim from 0.1.2.

### C++ fixes landed in this release
- **C1+C2**: `setDownloadInterceptor` IDL + cpp (re-landed for FF150).
- **C4**: 5 `nsIDocShell` stealth attributes (`fileInputInterceptionEnabled`, `overrideHasFocus`, `bypassCSPEnabled`, `forceActiveState`, `disallowBFCache`).
- **C5**: `LauncherProcessWin.cpp` + `nsWindowsWMain.cpp` juggler-pipe handle inheritance - without this, the Playwright pipe disconnects immediately on launch.
- **C6**: `juggler-navigation-started-renderer` / `-browser` observer notifications in `nsDocShell.cpp` and `CanonicalBrowsingContext.cpp` - without these, `Page.ready` never fires and `ctx.new_page()` hangs.
- **C7 (partial)**: storage stub for `nsIDocShell.languageOverride`. Workaround `InvisiblePlaywright(locale="")` recommended until full BC FIELD port lands.

### Verified
- Both archives built from same source: feder-cr/firefox_antidetect_patch commit `68906f1f9c55`.
- Windows + Linux smoke suite green: launch, `ctx.new_page()`, `page.mouse.{move,down,up,click,wheel}`, `navigator.webdriver=false`, sannysoft 32/33 PASS.
- SHA256 published in `checksums.txt` on the `firefox-3` release.

### Notes
- This is the first release with a native Linux build of the patched binary (previous `firefox-3` draft mentioned shipping the Linux firefox-2 archive byte-for-byte; that no longer applies - Linux now has the full C++ patch series).

## [0.1.2] - 2026-05-18

### Changed
- `BINARY_VERSION` bumped from `firefox-1` to `firefox-2`. The patched Firefox archive on GitHub Releases now contains the JS fix from 0.1.1 (every `page.mouse.*` / `page.click()` / `locator.click()` / `mouse.wheel()` failure on the FF150 binary). Users on 0.1.1 must run `python -m invisible_playwright clear-cache && python -m invisible_playwright fetch` to pick up the new archive.

### Verified
- Archive integrity tests on both platforms: Windows zip extracted + booted via Playwright (`mouse.move + click + page.click(selector)` all succeed end-to-end), Linux tarball file-level checks (firefox/libxul.so sizes, byte-identity of patched JS files against Windows source). 21/21 assertions pass.
- SHA256 published in `checksums.txt` on the `firefox-2` release.

## [0.1.1] - 2026-05-18

### Fixed
- **Critical**: every `page.mouse.*`, `page.click(selector)`, `locator.click()`, `page.hover()`, `mouse.wheel()` failed on the patched Firefox 150 binary with `win.windowUtils.jugglerSendMouseEvent is not a function`. The Juggler JS was porting calls to a Playwright-specific C++ method that was never landed in the FF146→FF150 port; replaced with the Mozilla chrome-scope `win.synthesizeMouseEvent` helper which is present in FF150. Six call sites patched across `juggler/protocol/PageHandler.js` and `juggler/content/PageAgent.js`. Reporter: [@trob9](https://github.com/trob9) - [#9](https://github.com/feder-cr/invisible_playwright/issues/9).
- `_linkedBrowser.scrollRectIntoViewIfNeeded()` is now guarded at both call sites in `PageHandler.js` (`dispatchMouseEvent` and `dispatchWheelEvent`) - the method is not present on the shipped FF150 `<browser>` element, so the unguarded call threw before the mouse event was dispatched.

### Added
- `tests/test_mouse.py`: 12-case regression suite covering every patched code path (mouse.move/click/dblclick/right-click, modifiers, locator.click/hover, wheel, manual mousedown+up, off-viewport move, humanize intermediate moves, scroll-and-click on offscreen element). Test cases inspired by `microsoft/playwright-python/tests/async/test_click.py`.
- Community standards: `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.github/ISSUE_TEMPLATE/*`, `.github/PULL_REQUEST_TEMPLATE.md`.

### Notes
- The Stealthfox humanize Bezier expansion continues to fire intermediate `mousemove` events; the swap to `synthesizeMouseEvent` does not change the human-trajectory behavior (verified by test).
- The reCAPTCHA v3 score (0.90) and FingerprintPro / CreepJS results documented in the README are unaffected - `synthesizeMouseEvent` is a legitimate Mozilla helper that does not increase the anti-detect surface.
- A binary refresh of the patched Firefox archive on GitHub Releases is required for users to receive this fix (the Juggler JS is shipped inside the archive). The `BINARY_VERSION` will be bumped to `firefox-2` in that release.

## [0.1.0] - 2026-05-13

### Added
- Initial public release.
- `InvisiblePlaywright` sync and async context managers - drop-in replacement for `playwright.sync_api.Browser` / `async_api.Browser`.
- StealthFox humanize hook: Bezier-curve mouse trajectories enabled by default.
- `_fpforge` Bayesian fingerprint sampler with ~400 fields per session.
- CLI: `invisible-playwright fetch | path | version | clear-cache`.
- Pinnable fingerprint fields via `pin={...}` (see `docs/pinning.md`).
- SOCKS5 / SOCKS4 / HTTP / HTTPS proxy support with auth.
- Linux x86_64 and Windows x86_64 binary support.

[0.4.3]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.3
[0.1.1]: https://github.com/feder-cr/invisible_playwright/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.1.0
[0.4.4]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.4
[0.4.5]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.5
[0.4.6]: https://github.com/feder-cr/invisible_playwright/releases/tag/v0.4.6
