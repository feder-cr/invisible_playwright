---
title: "PerimeterX (now HUMAN Bot Defender): what the sensor actually checks"
description: "PerimeterX was renamed HUMAN Bot Defender after a 2022 merger. What its sensor script and _px cookie chain actually collect, read from its own SDK source and documented network traffic, and what no browser can answer for you."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 26
---


# PerimeterX (now HUMAN Bot Defender): what the sensor actually checks

PerimeterX is not a company anymore, not in the sense that mattered when people
started writing about `_px3` cookies and press-and-hold challenges. HUMAN Security
merged with PerimeterX in July 2022, and the product now ships as HUMAN Bot Defender.
The sensor script, the cookie names and the collector endpoints carry the old `px`
naming almost unchanged, which is why a search for "PerimeterX" and a search for
"HUMAN Bot Defender" surface the same infrastructure, sometimes on the same page,
four years later.

This page is about the mechanism, read from PerimeterX's own open-source
server-side SDKs and from network traffic practitioners have documented, not from
a rendered block page. It is not about beating it. What a script collects and what
it does with the result are two different questions, and the second one is mostly
answered on a server you never get to inspect.

Read on 2026-08-30 from the [PerimeterX GitHub organisation's WSGI middleware
source](https://github.com/PerimeterX/perimeterx-python-wsgi) and cross-checked
against [ScrapeOps'](https://scrapeops.io/web-scraping-playbook/how-to-bypass-perimeterx/)
and [ZenRows'](https://www.zenrows.com/blog/perimeterx-bypass) documented traffic
captures, the two practitioner writeups that agree in the most detail.

## Three layers, and the browser only sees one of them

HUMAN Bot Defender scores a request on three inputs that arrive at different
points in the connection, and only the last of the three is something a browser
controls.

**IP and network reputation, before a byte of the page loads.** The origin
server's own SDK calls PerimeterX's API directly, server to server, to score the
inbound connection: `/api/v3/risk` for the check itself, `/api/v1/collector/s2s`
to report what happened afterward. This call never touches the browser at all.
It happens between the site's server and HUMAN's, which is the same shape of
check documented on [ASN and IP reputation in bot detection](asn-and-ip-reputation-in-bot-detection.md):
a verdict formed before any script the browser runs gets a turn.

**TLS and HTTP/2, still before JavaScript.** Every TLS handshake carries a cipher
and extension order, and every HTTP/2 connection carries a SETTINGS frame and a
header-priority scheme. Both practitioner writeups describe HUMAN Bot Defender
reading these as a JA3-class fingerprint and matching it against the claimed
browser, the same mechanism covered in more depth on
[JA3/JA4 TLS fingerprinting](ja3-ja4-tls-fingerprint.md). A client whose TLS stack
does not match the User-Agent it sends is caught here, before the sensor script
has even loaded.

**The client-side sensor, once the page is actually rendering.** This is the
layer a real browser engine answers, and the rest of this page is about it.

## The sensor script and the cookie chain

Once the page loads, PerimeterX injects an obfuscated collection script that
posts an encrypted payload to a collector endpoint on the site's own
`<appId>.px-cdn.net` subdomain. The response sets a chain of cookies, whose names
are defined directly in the vendor's own SDK source:

```
_pxvid   visitor ID, long-lived
_pxhd    session indicator, set on the first response
_px3     the current clearance cookie (legacy versions: _px, _px2)
_pxde    a data-enrichment cookie for extra collected fields
```

Alongside them, the SDK defines header names for a mobile-app variant of the same
check: `x-px-authorization`, `x-px-original-token`, and `x-px-first-party` for
telling the collector a request came from the site's own JavaScript instead of
a third party. None of these strings are guessed; they are read directly out of
`px_constants.py` in the linked repository.

On the server side, the same SDK defines four outcomes a risk check can return,
also as literal constants: `j` for a JavaScript challenge, `c` for a captcha,
`r` for a rate limit, and `b` for an outright block. A "sensitive route" (the
SDK's own term) calls the risk API on every single view regardless of prior
clearance; everything else relies on the cookie chain until it expires.

## What the sensor script actually probes

Cross-referencing the ScrapeOps and ZenRows traffic captures, the collected
fields fall into the same three families every canvas-and-WebGL fingerprinter
uses, plus a behavioral layer most of them skip:

- **Rendering surfaces.** A canvas hash (`canvasfp` in the ZenRows capture),
  `webglVendor`, `webglRenderer`, the WebGL version, its supported extensions and
  its shader language version. The same class of check covered on
  [canvas fingerprint noise](canvas-fingerprint-noise.md): small hardware or
  driver differences move the hash, so the value is a device signature, not a
  content signature.
- **Environment consistency.** `navigator.webdriver`, whether built-in functions
  still say `[native code]` when stringified (the same test explained on
  [native code toString detection](tostring-native-code-detection.md)), whether a
  Node-only global like `process` is reachable, and whether the claimed platform's
  usual font list is actually present.
- **Input-event telemetry.** Mouse movement deltas, keyboard press timing, and
  for touch input the raw `movementX`/`movementY`, client coordinates and
  timestamps of each event, collected across the whole visit rather than at one
  click. This is the same distributional idea covered on
  [mouse-dynamics behavioural biometrics](mouse-dynamics-behavioural-biometrics.md):
  the script is not asking whether one event looks right, it is building a shape
  over many of them.

None of this is exotic. It is the same fingerprint surface [BotD](botd-explained.md)
and [CreepJS](creepjs-explained.md) read, applied by a vendor with a server-side
risk model behind it instead of a public score.

## The press-and-hold challenge

When the accumulated score crosses a threshold, HUMAN Bot Defender's most visible
artifact appears: a widget asking the visitor to press and hold a button. Both
writeups agree it records timing and pointer telemetry for the duration of the
hold, then submits that alongside the rest of the sensor payload for a fresh
score.

Past that, be honest about the limit: HUMAN does not publish the challenge's
internal validation logic, and the more specific technical claims circulating in
scraping-community writeups (proof-of-work computation, pressure or capacitance
analysis) were not confirmed against anything HUMAN has published themselves in
the sources checked for this page. Treat them as unverified until a primary
source says otherwise. What is consistently documented is the trigger condition
and the data collected during the hold, and that is what is stated above.

## What a real engine answers honestly, and what nothing can

A genuine Firefox, patched at the C++ level rather than scripted from the page,
answers the rendering-surface and environment-consistency families by
construction: the canvas and WebGL hash come out of a real GPU pipeline instead
of a spoofed return value, `navigator.webdriver` is whatever the engine actually
reports, built-in functions really do say `[native code]` because nothing
patched them from JavaScript, and the TLS/HTTP2 stack is a real Firefox network
stack rather than an imitation of one. That is the same "do not lie in
JavaScript" property covered at length on [how CreepJS decides you are
lying](creepjs-explained.md), and it is why passing this class of check is mostly
a question of being the real thing rather than performing it.

Three things sit entirely outside what any browser engine can reach, and no
amount of engine-level correctness changes them:

- **The server-to-server risk call.** The origin's own request to
  `/api/v3/risk` happens between two servers you are not one of. Nothing the
  browser presents is even part of that exchange.
- **IP and account reputation.** Whether the connecting address, or the account
  behind it, has a history HUMAN's model has already scored. See [ASN and IP
  reputation in bot detection](asn-and-ip-reputation-in-bot-detection.md) for how
  that layer works on its own.
- **The behavioral model's threshold.** HUMAN does not publish where the score
  tips into a challenge or a block, and it should not be assumed to be a fixed
  number; it is a model trained on traffic this project has no visibility into.

invisible_playwright is a real Firefox with human-shaped input primitives, not a
service that reads or solves a press-and-hold widget, and nothing here should be
read as a claim otherwise. What it changes is the honesty of the rendering and
environment-consistency layer above. What it does not touch is the server-side
risk model, the IP reputation feeding it, or the account history a target site
may already hold on you.

## Short answers to the questions that lead here

**Is PerimeterX still a thing, or is it all HUMAN now?** Both names are in use.
HUMAN Security acquired PerimeterX in a July 2022 merger and the product is now
HUMAN Bot Defender, but the sensor, the `_px` cookie names and the collector
domains kept the old naming, so both terms lead to the same system.

**What does the `_px3` cookie actually do?** It is the current clearance cookie
the sensor sets after a payload scores clean. Its predecessors, `_px` and `_px2`,
are older cookie-format versions defined in the same SDK.

**Does a clean canvas and WebGL fingerprint mean I pass?** It means one layer
passes. The network-layer server-to-server risk call and the behavioral score
built up over the visit are scored independently and are not something a
rendering fingerprint touches.

**What triggers the press-and-hold challenge?** The accumulated risk score
crossing a threshold HUMAN does not publish. Both documented traffic captures
agree it then records timing and pointer telemetry for the duration of the hold.

**Can a real browser engine defeat HUMAN Bot Defender on its own?** No single
layer does. A real engine answers the rendering and environment-consistency
checks honestly by construction, which removes one whole failure class, but the
server-to-server risk call and IP/account reputation sit entirely outside what
any browser presents.

**Is naming PerimeterX or HUMAN allowed here?** Yes. This project names detection
vendors openly; what it will not do is claim to solve or defeat a vendor's
product, which this page does not do.

## Sources

- [`PerimeterX/perimeterx-python-wsgi`](https://github.com/PerimeterX/perimeterx-python-wsgi),
  the vendor's own open-source server-side SDK, `perimeterx/px_constants.py` and
  `perimeterx/middleware.py`, read 2026-08-30, for the literal cookie names,
  header names, API endpoints (`/api/v3/risk`, `/api/v1/collector/s2s`,
  `/api/v2/risk/telemetry`) and the four action codes (`j`/`c`/`r`/`b`).
- [ScrapeOps, "How To Bypass PerimeterX"](https://scrapeops.io/web-scraping-playbook/how-to-bypass-perimeterx/),
  retrieved 2026-08-30, for the collector-script and clearance-cookie flow and
  the WebGL/canvas rendering check.
- [ZenRows, "How to Bypass PerimeterX (HUMAN Security)"](https://www.zenrows.com/blog/perimeterx-bypass),
  retrieved 2026-08-30, for the specific sensor fields (`canvasfp`, `webglVendor`,
  `webglRenderer`, touch-event deltas), the TLS/HTTP2 fingerprinting claim, and
  the press-and-hold trigger and telemetry description.
- [Help Net Security, "HUMAN and PerimeterX merge to protect customers from bot
  attacks and fraud"](https://www.helpnetsecurity.com/2022/07/28/human-security-perimeterx/),
  retrieved 2026-08-30, for the July 2022 merger date and the confirmation that
  PerimeterX's technology was folded into HUMAN rather than kept as a separate
  brand.

**See also:** [how CreepJS decides you are lying](creepjs-explained.md) for the
same "do not lie in JavaScript" property applied to a public tool,
[mouse-dynamics behavioural biometrics](mouse-dynamics-behavioural-biometrics.md)
for the input-telemetry layer in depth, and [ASN and IP reputation in bot
detection](asn-and-ip-reputation-in-bot-detection.md) for the network-layer check
that runs before the sensor script ever loads.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The cookie names, header names and API paths above are quoted from PerimeterX's
own SDK source, not paraphrased from a bypass forum, because a vendor's own
constants file does not go stale the way a screenshot of a block page does.*
