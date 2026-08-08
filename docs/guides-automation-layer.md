---
title: "The Automation Layer"
description: "The driver itself as a fingerprinting surface: what patching a page cannot fix, because the tell lives in how the browser is piloted, not in what it reports."
parent: "Guides"
has_children: true
nav_order: 4
---


# The Automation Layer

A different category from the rest of this site: not what the browser reports, but
what the act of automating it leaves behind. A debugger attached for evaluation
changes timing. A driver's own artefacts show up in stack traces. A protocol version
mismatch breaks silently on one specific call. None of this is a value you can
override from the page, because the page is not where the tell originates.

## What the driver reveals about itself

- [Can Websites Detect Playwright?](can-websites-detect-playwright.md) - Which webdriver, protocol and fingerprint signals catch a run, and what a patched Firefox fixes.
- [Does Playwright Change My Browser Fingerprint?](does-playwright-change-my-fingerprint.md) - Stock Playwright inherits the host's real values instead of randomizing, leaking past a spoofed user agent.
- [Does Playwright Get Detected on the First Request?](does-playwright-get-detected-first-request.md) - The TLS handshake and HTTP/2 settings form a network fingerprint detectable before any JavaScript runs.
- [Does Playwright Leave Traces a Website Can See?](does-playwright-leave-traces.md) - Which traces are page-visible, why the control-channel WebSocket is not, and how a patched Firefox reads as ordinary.
- [Does Playwright Set navigator.webdriver to True?](does-playwright-set-navigator-webdriver.md) - Stock Playwright reports it true; why patching it in JavaScript is itself detectable.
- [Does Playwright Support Firefox Stealth?](does-playwright-support-firefox-stealth.md) - Put the spoofs in a patched browser and keep the standard Playwright API.
- [Is Playwright Firefox Harder to Detect Than Chromium?](is-playwright-firefox-harder-to-detect-chromium.md) - The one structural difference in injection, and where engine choice stops helping.
- [Why Playwright's bundled Firefox is easy to detect](playwright-bundled-firefox-detectable.md) - Its version markers and runtime traits differ from a released build, and swapping the binary removes the tell.
- [Function.prototype.toString and the [native code] check](tostring-native-code-detection.md) - Why toString spoofing fails the [native code] check, and what an engine-level build fixes.
- [The ChromeDriver cdc_ variable, and why renaming it fails](cdc-variable-explained.md) - The cdc_ variable is a one-line test; renaming it raises the bar but leaves the tell.
- [Why an attached debugger makes automation detectable](debugger-timing-detection.md) - A service reported developer_tools true with no devtools open: the framework's attached debugger, four separate leaks.
- [Playwright isTrusted: are automated clicks real?](playwright-clicks-istrusted.md) - A JS-dispatched event never can be, but Firefox driven through Juggler synthesizes input on the native path.

## Connecting, protocol, and process lifecycle

- [Stock Playwright, patched Firefox: how they connect](stock-playwright-patched-binary.md) - Joining unmodified Playwright to a patched binary through a prefs and environment contract.
- [Playwright connect_over_cdp does not work with Firefox](playwright-connect-over-cdp-firefox.md) - Firefox ships no DevTools Protocol; the Juggler-based connect() path that replaces it, with runnable code.
- [Why a Playwright upgrade broke 97 of 133 tests overnight](playwright-protocol-drift.md) - One undeclared field broke tests while the browser launched fine: what protocol drift is and how to catch it.
- [Firefox launches but Playwright can't drive it: packaging gap](juggler-missing-packaged-build.md) - A build launches and screenshots fine yet fails at launch with TargetClosedError; the cause is packaging, not the driver.
- [Orphaned Firefox processes on Windows: the killed-runner leak](orphaned-browser-process-windows.md) - These come from a killed test runner; the fix binds the browser to an OS job object.
- [Execution context was destroyed, and when it means detection](execution-context-destroyed.md) - Usually a race condition, but sometimes a redirect to a challenge; how to tell the two apart.
- [Why content_frame() returns None for a cross-origin iframe](cross-origin-iframe-unreachable.md) - One shared cause behind None, throws and timeouts: process isolation, not a permissions bug.

## Human-like input and interaction

- [Human-like mouse movement: Bezier curves are the easy part](human-mouse-movement.md) - Every pointer event carries fields saying where it came from, and a perfect curve can still fail.
- [ghost-cursor human mouse paths with Playwright](ghost-cursor-human-mouse.md) - Where fingerprint realness ends and pointer-behaviour realism begins.
- [Why humanized mouse movement can fail on hover()](hover-mouse-movement-bug.md) - A hit-target check moves the pointer first, collapsing the humanized path to a near-teleport; the cause and fix.
- [Drag and drop elements in Playwright with drag_to](drag-and-drop-playwright-firefox-drag-to.md) - How to drag and drop, why the pointer events come back trusted, and the honest limit on programmatic paths.
- [Playwright dialog and popup handling without a tell](playwright-dialog-popup-handling.md) - The instant always-cancel default for JS dialogs is both a functional bug and a non-human tell.

## Sessions, profiles, and per-context identity

- [Why automating login is riskier than reusing a session](automating-login-vs-session-reuse.md) - A login form runs the most monitored flow; a saved session skips it if the fingerprint still matches.
- [Can I Use My Real Browser Profile With Playwright?](can-i-use-my-real-browser-profile-playwright.md) - Reusing your daily profile leaks personal cookies and the host fingerprint; use a dedicated seed-fixed profile.
- [Save and reuse login with storage_state in Playwright](save-reuse-login-storage-state-playwright.md) - Save and restore cookies and localStorage to skip the login form, plus the seed and exit IP that must match.
- [Playwright persistent profile: what it fixes and breaks](persistent-profiles.md) - Keeps logins but carries three traps: a stale seed, a permission disabling WebRTC protection, and an inconsistent age.
- [Isolate identities with a browser context per session](isolate-identities-browser-context-per-session.md) - One context per account keeps cookies, storage and cache from bleeding; storage isolation is not IP isolation.
- [Playwright new_page vs new_context: the viewport tell](playwright-new-page-vs-new-context.md) - new_page can ship the stock viewport and skip your per-context fingerprint defaults; new_context does not.
- [Read and set cookies in a Playwright context](read-set-cookies-playwright-context.md) - Read the cookie jar and seed a consent flag or auth token, plus the caveat on hand-set attributes.
- [Set geolocation and permissions per Playwright context](set-geolocation-permissions-per-playwright-context.md) - Set coordinates and grant the permission per context, and why they must agree with your IP, timezone and locale.
- [Playwright mobile emulation on Firefox and isMobile](playwright-firefox-mobile-emulation.md) - Why iPhone and mobile presets misbehave: isMobile is unsupported and the seeded engine owns the screen size.

## Network interception and traffic

- [When to use an HTTP client vs a real browser](http-client-vs-real-browser.md) - A decision flowchart: an HTTP client when the data is in the response, a real browser when JavaScript builds the page.
- [Migrating from requests + BeautifulSoup to a browser](migrate-requests-beautifulsoup-to-browser.md) - Move a scraper to a real browser when the data is JavaScript-rendered, while keeping BeautifulSoup as the parser.
- [Intercept and mock network requests with page.route](intercept-and-mock-requests-page-route-playwright.md) - Intercept, mock, abort or rewrite requests, with the honest caveat on forged responses and edited headers.
- [Record and replay HTTP traffic with HAR in Playwright](record-replay-http-traffic-har-playwright.md) - Record to a HAR and replay offline, why the replay is a stale fixture, and why a HAR holds real session data.
- [Handle HTTP basic auth in Playwright (http_credentials)](http-basic-auth-playwright-http-credentials.md) - Pass credentials to answer a 401 challenge, why that differs from an HTML login form, and how to keep the secret out of code.

## Files, downloads, and recorded artifacts

- [Playwright download files with Firefox and the tell](playwright-download-files-firefox.md) - Using expect_download: accept_downloads defaults on, no save dialog appears, and the file is not page-visible.
- [Playwright set_input_files uploads and the tell](playwright-set-input-files-upload.md) - No native OS picker opens, plus the trusted change-event caveat most upload tutorials skip.
- [Record a Playwright trace to debug a failed scrape](record-playwright-trace-debug-scraper.md) - Record a trace with screenshots and DOM snapshots, and keep the content-leaking artifact out of production.
- [Record a video of a Playwright browser session](record-video-of-playwright-session.md) - Record a .webm with record_video_dir; it works headless and never changes what the site sees.
