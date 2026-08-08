---
title: "Browser Identity"
description: "Navigator, screen, headers and permissions - the properties a site reads before anything is drawn, and the ones that are checked against each other rather than on their own."
parent: "Guides"
has_children: true
nav_order: 1
---


# Browser Identity

The largest group here, because it is where most detection actually starts: the
plain-JavaScript properties a page reads in the first few milliseconds, before it
draws anything. Individually most of these are one line each. What makes them a
fingerprint is that a detector reads several and checks whether they agree - a
`navigator.webdriver` of `false` next to a font set that belongs to a different
operating system is caught by the comparison, not by either value alone.

## What a fingerprint is, and what does not defeat it

- [Can two devices share a browser fingerprint?](can-two-devices-share-a-browser-fingerprint.md) - Sharing a fingerprint hides you in a crowd of real users; a unique one tracks you.
- [Can you be fingerprinted in incognito mode?](can-you-be-fingerprinted-in-incognito-mode.md) - Private mode clears cookies but leaves canvas, fonts, timezone and TLS fully readable.
- [Does clearing cookies stop fingerprint tracking?](does-clearing-cookies-stop-fingerprinting.md) - No: fingerprinting is stateless and rebuilds the same identity after every clear.
- [Can a website tell you are running on a server?](can-a-website-tell-you-are-on-a-server.md) - How software WebGL, a missing audio device, headless metrics and datacenter ASN expose a server.
- [Headless vs headful: what is actually being detected](headless-vs-headful.md) - Headlessness is rarely the tell; the hardware and rendering signals around it are.
- [Is changing the user agent enough to avoid detection?](is-changing-user-agent-enough.md) - No: detectors cross-check the string against platform, TLS, WebGL renderer and Client Hints.
- [Playwright User Agent: Why You Should Not Set It](playwright-user-agent.md) - Setting a Playwright user agent does not change fonts, GPU, codecs or TLS.
- [fake-useragent is archived: what changes and what doesn't](fake-useragent-archived.md) - The Python package was archived in 2026; what breaks and what to use instead.
- [What privacy.resistFingerprinting actually does](resist-fingerprinting.md) - What it changes, what it breaks, and why this project leaves it off.
- [navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md) - A specified property, not a leak; patching it alone buys almost nothing.

## Navigator strings and headers that must agree

- [navigator.platform and oscpu on a spoofed OS](navigator-platform-oscpu-consistency.md) - platform, oscpu and appVersion come from the real OS, so a Linux build leaks Linux.
- [navigator.vendor and productSub: the Firefox tells](navigator-vendor-productsub-firefox.md) - vendor is empty and productSub is 20100101 on real Firefox; a spoof gets these wrong.
- [navigator.buildID and the stale build date tell](navigator-buildid-firefox-tell.md) - A Firefox-only build-date property; freezing it to a constant is a worse tell.
- [Is navigator.connection a fingerprint in Firefox?](navigator-connection-network-information-firefox.md) - The Network Information API is Chromium-only, so a real Firefox returns undefined.
- [Accept-Language header vs navigator.languages](accept-language-navigator-languages.md) - One Firefox pref feeds both, so moving one and not the other is a clear tell.
- [Client Hints and Sec-Fetch: headers that must agree](client-hints-sec-fetch.md) - Sec-CH-UA and Sec-Fetch come from browser state, cheap to compare and hard to fake.
- [hardwareConcurrency, deviceMemory and storage quota](hardware-concurrency-device-memory.md) - Three one-line reads that go wrong on a server and are cross-checked against each other.
- [navigator.maxTouchPoints and pointer consistency](navigator-maxtouchpoints-pointer.md) - maxTouchPoints reads 0 on a spoofed desktop and the pointer media queries must agree.

## Screen, display and rendering signals

- [Screen size and viewport tells in headless browsers](screen-size-headless-tells.md) - A headless browser invents screen and viewport values; which combinations never occur.
- [window.devicePixelRatio: the pref that spoofs it](devicepixelratio-firefox-pref.md) - Set it with the layout.css.devPixelsPerPx string pref, and the values it must match.
- [Color-gamut and HDR media queries as a fingerprint](color-gamut-hdr-media-query-fingerprint.md) - color-gamut and dynamic-range media features expose display capability and must agree with colorDepth.
- [CSS fingerprinting: what media queries reveal](css-media-query-fingerprinting.md) - Media queries and CSS system colours fingerprint a machine with no JavaScript.
- [Can scrollbar width reveal my operating system?](scrollbar-width-reveals-operating-system.md) - Native scrollbar width is set by the OS and theme, leaking the real platform.
- [Codec fingerprinting: canPlayType and MediaCapabilities](codec-fingerprinting.md) - The formats a browser claims to play reveal its build and platform.
- [prefers-reduced-motion and other OS-setting tells](prefers-reduced-motion-os-setting-tells.md) - How OS accessibility settings leak through pure CSS media features.

## Device and sensor APIs

- [Do accelerometer and gyroscope APIs leak on desktop?](accelerometer-gyroscope-desktop-leak.md) - Desktop Firefox stays silent; a spoofed desktop must not invent motion events.
- [Battery API fingerprint: does Firefox expose it?](battery-api-fingerprint-firefox.md) - Desktop Firefox removed the Battery Status API in 2017, so a battery object is fake.
- [Can the Gamepad API fingerprint or detect a bot?](gamepad-api-fingerprint-bot-detection.md) - Firefox returns an empty getGamepads() until a real gesture; match that stock shape.
- [Permissions API: the two answers that must agree](permissions-api-consistency.md) - The Permissions API and Notification.permission answer one question two ways.
- [speechSynthesis.getVoices() returns an empty array](speech-synthesis-voices.md) - An async timing gotcha in every browser, and a voice list that names the wrong OS.
- [Does storage quota estimate reveal disk size?](storage-quota-estimate-device-fingerprint.md) - storage.estimate() buckets as a device fingerprint; keep it in a plausible bucket.

## Automation surfaces beyond the page

- [BFCache and pageshow.persisted under browser automation](bfcache-pageshow-persisted.md) - Automation disables the back/forward cache, so pageshow.persisted is always false.
- [Service workers, storage partitioning and automation](service-workers-storage-partitioning.md) - Service workers survive cookie clears, and blocking them is a signal real browsers avoid.
- [Web Workers: where page-level fingerprint patches fail](web-workers-fingerprint.md) - A Web Worker is a separate realm, so page-level stealth patches never run there.
- [Browser extensions are a fingerprint surface](browser-extension-fingerprint.md) - An installed extension is a surface a page can detect three ways.
