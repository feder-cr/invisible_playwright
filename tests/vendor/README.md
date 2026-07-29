# Vendored detection libraries (test-only)

These are upstream, unmodified, MIT-licensed browser-fingerprinting / bot-detection
libraries, vendored so the detector e2e tests run **hermetically and identically**
on a dev box and on a GitHub runner (no external CDN at test time - Firefox
tracking-protection blocks the openfpcdn.io CDN anyway, and we want CI offline).

They are served from a localhost HTTP server and loaded into the patched Firefox;
the tests assert the REAL detectors don't flag the stealth build (BotD: `bot===false`;
fpscanner: engine-agnostic rules clean; CreepJS: `headlessRating===0` + no JS-proxy
tells) and that the fingerprint is stable (FingerprintJS: same `visitorId` across
launches). CreepJS runs fully offline - the tests abort every non-loopback request,
so its optional crowd-comparison POST never fires and the verdict is computed locally.

| File | Package | Version | Source | License |
|---|---|---|---|---|
| `botd-2.0.0.esm.js` | `@fingerprintjs/botd` | 2.0.0 | https://cdn.jsdelivr.net/npm/@fingerprintjs/botd@2.0.0/dist/botd.esm.js | MIT |
| `fingerprintjs-5.2.0.umd.min.js` | `@fingerprintjs/fingerprintjs` | 5.2.0 | https://cdn.jsdelivr.net/npm/@fingerprintjs/fingerprintjs@5.2.0/dist/fp.umd.min.js | MIT |
| `fpscanner-1.0.6.es.js` | `fpscanner` | 1.0.6 | https://cdn.jsdelivr.net/npm/fpscanner@1.0.6/dist/fpScanner.es.js | MIT |
| `creepjs-10aa672.js` | `abrahamjuliot/creepjs` | git `10aa6724` | https://raw.githubusercontent.com/abrahamjuliot/creepjs/10aa6724cd33a1015db1574211890518cd04f0cc/docs/creep.js | MIT |

All MIT (FingerprintJS Inc. / Antoine Vastel / Abraham Juliot). To update: download
the pinned dist (jsdelivr for npm packages, raw.githubusercontent for CreepJS at a
commit SHA), drop it here, and bump the version in the filename + this table.
