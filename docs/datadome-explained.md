---
title: "How DataDome's bot detection actually works"
description: "DataDome scores a request on three layers before the page finishes loading: TLS and HTTP fingerprint, a JavaScript device and behavior collector, and a cookie that remembers the verdict. Read from DataDome's own docs and confirmed reverse engineering."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 24
---


# How DataDome's bot detection actually works

DataDome is not a library you install and read the source of. It is a paid
service run partly on its own servers, so most of what circulates about it
online is reverse-engineered from the outside rather than read from a
repository. That is a real gap in a corpus like this one: DataDome sits in
front of a large share of retail, travel and ticketing traffic, and "datadome
bypass" and "datadome cookie playwright" are two of the most consistently
searched anti-bot terms there are.

This page separates what DataDome documents about its own product from what
independent reverse engineering has confirmed, and says which is which. The
short version: a request is scored on three layers before the page finishes
loading, the TLS/HTTP handshake, a JavaScript collector that checks the
device and, over the session, your behavior, and a cookie that remembers the
verdict so the first two do not run again. None of the three are secret in
outline. The scoring model behind them is.

Read from DataDome's own documentation on 2026-08-30
([`docs.datadome.co`](https://docs.datadome.co/docs/device-check), the
Device Check, JavaScript Tag, cookie and pre-activation checklist pages) and
from practitioner reverse engineering also read 2026-08-30 (Scrapfly,
ZenRows, and a public GitHub tool built to talk to DataDome's own challenge).
Anything sourced only to the second group is marked as such below.

## Three layers, in the order a request meets them

| Layer | What it checks | Runs where |
|---|---|---|
| Network | TLS handshake (JA3/JA4-class fingerprint), HTTP/2 frame order, IP and ASN reputation | On the wire, before any page content is served |
| Device and behavior | Canvas, WebGL, audio and font signals, automation-framework tells, then mouse, scroll, click and typing patterns over the session | An in-page JavaScript tag |
| Session memory | An encrypted `datadome` cookie holding the last verdict | Your cookie jar, read on every subsequent request |

A request that fails the network layer badly enough never needs the other
two. A request that looks fine on the network but has no cookie, or an
expired one, gets the JavaScript layer. A request with a valid cookie mostly
skips straight through: DataDome says plainly that once a device or visitor
has been verified, "DataDome remembers the outcome and does not repeat the
verification."

## The network layer: before your JavaScript even runs

The TLS `ClientHello`, sent before either side has exchanged a single
application byte, encodes which cipher suites, TLS version and extensions
the client supports, and it differs by TLS library in ways JavaScript cannot
touch, because JavaScript has not started running yet. DataDome's own
engineering blog (read via its Security Boulevard reprint, since the
original page on `datadome.co` blocked direct retrieval this session) states
the fingerprint is "quite stable and unique for a device type, browser, and
OS," and that the same model catches two patterns: bots whose TLS stack has
a fingerprint nothing else shares, and clients claiming to be one thing
(Chrome 102, say) while their handshake belongs to something else. [How JA3
and JA4 actually work, and why neither can be patched from inside the
browser](ja3-ja4-tls-fingerprint.md); [the HTTP/2 SETTINGS frame and header
order sit one layer above it, and DataDome reads that too](http2-fingerprint-detection.md).

Same source: DataDome states its engine processes on the order of three
trillion signals a day (2022 figure). A separate 2026 write-up from
Scrapfly, a scraping infrastructure vendor, cites five trillion a day and
"over 85,000 customer-specific and use-case-specific models," one set per
protected site instead of one global model. Four years apart and not
necessarily in tension, but neither figure is audited, so treat both as
vendor reporting, not something this page verified independently.

IP and ASN sit in the same layer conceptually: whether the address is
residential, mobile or datacenter, its distance from the claimed timezone,
how many recent requests share it. [What ASN and IP reputation actually
score, and how fast it decays, is covered separately](asn-and-ip-reputation-in-bot-detection.md);
DataDome's docs do not detail IP scoring, so this leans on the practitioner
writeups, not DataDome's own pages.

## The device check: what the JavaScript tag actually collects

DataDome's own JavaScript Tag documentation describes an inline script meant
to sit at the very start of `<head>`, before any other resource loads, so it
can intercept `XMLHttpRequest` and `fetch` calls and show a challenge before
the page's own requests go out:

```html
<script>
  window.ddjskey = 'YOUR_DATADOME_JS_KEY';
  window.ddoptions = {};
</script>
<script src="https://js.datadome.co/tags.js" async></script>
```

`ddjskey` is the site's own public key, set by whoever integrates DataDome,
not something generated per visitor. According to DataDome's own docs, the
tag collects two kinds of data: "behavioral data from the client... such as
mouse movements or key strokes," and "generic information about the OS, the
browser itself, the GPU, etc." It also runs, in DataDome's own words,
consistency checks aimed specifically at Headless Chrome, Puppeteer,
Puppeteer Extra Stealth and modified Selenium builds. The payload is POSTed
to `api-js.datadome.co/js/`.

DataDome does not publish the individual checks inside that collector, so
the specifics here come from reverse engineering, not `docs.datadome.co`.
ZenRows' own deobfuscation of a live DataDome bundle reports "over 35
functions" prefixed `dd_`, gathering canvas, WebGL and font signals alongside
the automation-framework checks DataDome's docs already name. Scrapfly's
breakdown, published as part of its own bypass product, adds more: dual-
probing 19 MIME types twice, checking WebGL parameter coherence, reading
`navigator.keyboard.getLayoutMap()`, battery status, `speechSynthesis` voice
lists, camera/microphone permission state, and two separate sets of
User-Agent Client Hints. A vendor selling a bypass product has an obvious
incentive to make its own reverse engineering sound thorough, so read this
list as corroborated in outline, two unrelated writeups converge on canvas,
WebGL, fonts and automation-framework checks, not verified line by
line.

DataDome's own **Device Check** feature is the name for the invisible
version of this: it fires, per DataDome's docs, "when a bot is detected...
but the evidence of bot activity is not strong enough to block it," or when
the requested resource looks sensitive on its own. It is enabled by default,
"collect[s] hundreds of signals and perform[s] several checkpoints on the
device and environment," and returns one of three outcomes: block, allow, or
escalate to a visible challenge. DataDome states plainly that "no personal
information is collected" by this step.

## The cookie, and what happens when it is missing

DataDome's own cookie documentation is specific: named `datadome`,
documented at roughly 128 bytes, expiring after about a year, its value
encrypted and, per DataDome, holding no personally identifying information.
The same page instructs site owners never to mark it `HttpOnly` or move its
`Domain`/`Path`, because DataDome's own JavaScript needs to read and write it
directly, the opposite of how a typical session-security cookie is usually
configured. DataDome describes the cookie's job as enabling "both
server-side and client-side detection to assess the legitimacy of a
request." [Reading and setting cookies through a Playwright context, and why
a hand-set cookie can look unearned next to one a real visit produced, has
its own page](read-set-cookies-playwright-context.md).

For what happens when that cookie is missing, expired, or fails validation,
DataDome's pre-activation docs name two default challenge types per
protected endpoint: **Slider**, the primary one, and **Device Check** as the
automatic fallback if Slider is turned off for that endpoint. If an operator
disables both for an endpoint, requests there are simply allowed through,
per DataDome's own docs, a genuine gap and not a weakness anyone has to
guess at.

The page a browser sees when neither a valid cookie nor a passing Device
Check is present is what people who reverse-engineer DataDome call the
"interstitial" or the "5-second challenge", terms from that community, not
DataDome's own published vocabulary in what this page could retrieve. A
concrete example of that reverse engineering: a public GitHub project,
`Datadome-Interstital-Encryptor`, builds exactly the encrypted payload this
challenge expects. Its README shows the inputs, a `datadome` cookie, a
per-site hash, and named "signals" added one at a time before the tool
encrypts them into a payload submitted back in exchange for a fresh
`datadome` cookie that lets the next request through. The tool proves the
shape of the exchange is real; it does not reveal what DataDome's server
does with the signals, because that part is not public from either side.

## Behavior does not stop being scored once you are past the door

A passing Device Check or a valid cookie is not a permanent pass. Per
Scrapfly's description, DataDome keeps scoring mouse trajectories, scroll
velocity, click coordinates, typing cadence and dwell time across the whole
session, comparing that behavioral signature against models trained on that
specific site's own legitimate traffic, which is what the per-customer-model
figure above describes. [What mouse-dynamics behavioral biometrics actually
score, distribution by distribution rather than field by field, is covered
separately](mouse-dynamics-behavioural-biometrics.md). This is also where an
automation *script* creates its own signal, independent of the engine it
drives: a click that always lands dead-center, a scroll that jumps to the
same offset on a fixed interval, or fields filled at an inhumanly constant
rhythm all read as machine regularity, no matter how faithfully the engine
underneath renders canvas or answers TLS.

## What an engine answers honestly, and what sits outside its reach

This is the honest boundary, and it matters more here than almost anywhere
else in this corpus, because DataDome checks span from the TLS library up
through months of session history.

**What a genuinely real Firefox engine answers correctly, by construction,
with nothing spoofed at the JavaScript layer:** the TLS handshake and the
HTTP/2 frame order, because they come from a real Firefox stack rather than
an impersonation of one; canvas, WebGL and AudioContext output, because they
come out of the real rendering and audio paths instead of a patched return
value; native function `toString()` output, `navigator.webdriver` and the
rest of the automation-framework tells DataDome's own docs name, because
there is no CDP-driven Chromium underneath pretending; and font enumeration
and OS-backed values like `navigator.keyboard.getLayoutMap()`, because they
come from a real OS-facing path instead of a JavaScript-level override. That
is a claim about which category of check has nothing to find, not a claim
that any specific vendor's model is defeated.

**What sits entirely outside what any browser engine, real or patched, can
answer:** IP reputation and ASN, a property of the network path rather than
the browser; DataDome's server-side ML score and its per-site models, which
run on DataDome's own infrastructure and are not observable from the client
at all; and account or session history, since the `datadome` cookie's own
roughly year-long lifetime exists specifically to reward continuity a single
fresh session cannot have. Behavioral scoring sits in a third category: it
is produced by whatever is driving the browser, not the browser itself, a
property of the automation code and its pacing, [which has its own, separate
discussion](ai-agent-timing-signal.md), not of engine realness.

And the interactive **Slider** challenge specifically is a puzzle a visitor
solves, not a fingerprint a browser answers. `invisible_playwright` is
engine-level work: a patched Firefox that answers the device and consistency
checks in the table above the way a real Firefox would. It does not include,
and does not sell, a captcha or puzzle-solving service, and nothing here
should be read as a claim that it clears DataDome's slider, its behavioral
scoring, or its server-side risk model. Those are different problems from
the one an honest engine solves, and no amount of engine fidelity
substitutes for a clean, reputable IP or human-paced interaction.

One 2026 wrinkle worth noting: DataDome's own pre-activation checklist now
lists **MCP** (Model Context Protocol) alongside Web Browsers, Mobile Apps
and APIs as a "Traffic Source," with a separate "Agentic Trust" setting for
scoring AI-agent traffic. It is the same three-layer model applied to a
newer client, not a new mechanism: an LLM-driven agent still opens a TLS
connection, still runs (or fails to run) the collector, and still produces a
behavioral trace, [which has a signature of its own that no fingerprint
fixes](ai-agent-timing-signal.md).

## Short answers to the questions that lead here

**What is DataDome?** A commercial anti-bot and WAF-adjacent service that
protects a site's traffic by scoring the TLS/HTTP handshake, an in-page
JavaScript device and behavior collector, and a session cookie, then
blocking, challenging or allowing each request.

**What triggers DataDome's challenge?** A missing or invalid `datadome`
cookie, a network-layer signal that looks wrong (an inconsistent TLS
fingerprint, a flagged IP), or a JavaScript-layer signal that looks wrong
(an automation-framework tell, an inconsistent device fingerprint). Any one
of the three can be enough on its own.

**What is the difference between Device Check and the Slider challenge?**
Per DataDome's own docs, Slider is the default, primary interactive
challenge; Device Check is the automatic, invisible fallback if Slider is
turned off for that endpoint, and can also run on its own when evidence
against a request is present but not strong enough to block outright.

**Does a real, unpatched browser engine beat DataDome?** It correctly
answers the device and consistency layer, the one an engine can honestly
answer. It says nothing about IP reputation, DataDome's server-side ML score,
session history, or the Slider puzzle itself, which are separate problems.

**Does invisible_playwright solve DataDome's captcha for me?** No. It is
engine-level realness, a patched Firefox that answers device checks
honestly. It does not include a puzzle-solving or captcha-solving service of
any kind.

**Why would a request still get challenged with a clean fingerprint?**
Because the fingerprint is one of three layers. A datacenter IP, a session
with no history, or a behavioral pattern that reads as machine-regular can
each trigger a challenge on their own, independent of whether the device
fingerprint was clean.

**See also:** [JA3 and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md),
[what ASN and IP reputation score, and how fast it decays](asn-and-ip-reputation-in-bot-detection.md),
[what mouse-dynamics behavioral biometrics actually measure](mouse-dynamics-behavioural-biometrics.md),
and [the checklist for when one site detects you](playwright-detected-as-bot.md).

## Sources

- DataDome's own documentation, read 2026-08-30: [Device Check](https://docs.datadome.co/docs/device-check),
  [JavaScript Tag](https://docs.datadome.co/docs/javascript-tag),
  [cookie and stored data](https://docs.datadome.co/docs/cookie-session-storage), and the
  [pre-activation checklist](https://docs.datadome.co/docs/checklist-before-activating-protection),
  for trigger conditions, JS Tag behavior, cookie name/size/expiry, and the
  Slider/Device Check pairing including the MCP/Agentic Trust category.
- DataDome's own engineering blog on TLS fingerprinting, read via its
  [Security Boulevard reprint](https://securityboulevard.com/2022/10/how-tls-fingerprinting-reinforces-datadomes-protection/)
  (the original `datadome.co` page returned a 403 to this session's fetch
  tool), read 2026-08-30, for the "three trillion signals a day" figure and
  the two TLS-mismatch patterns it detects.
- Press coverage of the Device Check launch, [Help Net Security, 2023-12-12](https://www.helpnetsecurity.com/2023/12/12/datadome-device-check/),
  read 2026-08-30, for the company's self-reported accuracy and
  SDK-deployment figures at launch.
- Scrapfly, read 2026-08-30: its [DataDome bypass product page](https://scrapfly.io/bypass/datadome)
  for the detection-layer chain and collector checks (codec dual-probe,
  keyboard layout map, WebGL coherence), and its [blog post on bypassing
  DataDome](https://scrapfly.io/blog/posts/how-to-bypass-datadome-anti-scraping)
  for the 85,000-model and five-trillion-signal figures, both self-reported
  by a vendor selling a competing bypass product.
- ZenRows' [DataDome bypass writeup](https://www.zenrows.com/blog/datadome-bypass),
  read 2026-08-30, for its own deobfuscation results (the `dd_`-prefixed
  function count) and its TLS/header check description.
- [`glizzykingdreko/Datadome-Interstital-Encryptor`](https://github.com/glizzykingdreko/Datadome-Interstital-Encryptor)
  on GitHub, read 2026-08-30, a real public reverse-engineering project, for
  the concrete shape of the interstitial payload (cookie, site hash, named
  signals) practitioners have independently worked out.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
DataDome does not publish its scoring model or the full list of what its
collector checks, so this page draws a line between DataDome's own
documentation and outside reverse engineering everywhere that line exists,
rather than presenting both as equally certain.*
