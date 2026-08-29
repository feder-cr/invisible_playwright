---
title: "What BotD actually detects, and what it does not"
description: "What BotD's nineteen detectors actually check, read from its own source: mostly whether a browser is telling the truth about which engine it is, not automation."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 3
---


# What BotD actually detects, and what it does not

BotD is the open-source bot detector from the FingerprintJS team. It is small, it is
readable, and reading it changes how you think about this problem, because most of
its nineteen detectors are not looking for automation at all.

They are checking whether your browser is telling the truth about **which browser it
is**.

Read from source on 2026-08-29 ([`fingerprintjs/BotD`](https://github.com/fingerprintjs/BotD), `src/detectors/` and `src/sources/`).

## The nineteen detectors

```
app_version              distinctive_properties   document_element_keys
error_trace              eval_length              function_bind
languages_inconsistency  mime_types_consistence   notification_permissions
plugins_array            plugins_inconsistency    process
product_sub              rtt                      user_agent
webdriver                webgl                    window_external
window_size
```

Exactly one of those, [`webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
is the check everybody knows. A handful look for
specific named tools. The rest are engine identity and internal consistency.

| Detector group | The question it asks | Examples named here |
|---|---|---|
| The one everyone knows | Is the automation flag set? | `webdriver` |
| Engine identity | Does the browser behave like the engine its user agent claims? | `eval_length`, `product_sub` |
| Named tool fingerprint | Does a value the engine reveals name a specific automation tool? | `error_trace`, `function_bind`, `window_external`, `app_version` |
| Internal consistency | Does a value a real browser always produces show up, intact and correctly typed? | `languages_inconsistency`, `mime_types_consistence`, `plugins_inconsistency`, `rtt`, `window_size` |

## The interesting group: what engine are you really?

Some values are decided by the JavaScript engine and cannot be changed from
JavaScript without being obvious. Two of them, BotD computes and checks against what
your user agent claims. A handful of others read that same kind of engine-only value
and test it for one named tool's leftover fingerprint, regardless of what the user
agent says.

**`eval_length`.** `eval.toString().length` is a fixed number per engine, the same
category of leak as [any other native function's own source](tostring-native-code-detection.md).
The check compares that number against the engine your user agent claims: 37 must come
from WebKit or Gecko, 39 from Internet Explorer, 33 from Chromium, and any other pairing
is a bot. A Chromium engine claiming to be Firefox still reports 33, the length the check
reserves for Chromium, and fails on a value nobody thinks to spoof.

**`product_sub`.** If the browser claims to be Chrome, Safari, Opera or WeChat, then
[`navigator.productSub`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/productSub)
must be `20030107`. Firefox reports `20100101`. One
comparison, no ambiguity ([why a real Firefox reports `20100101`](navigator-vendor-productsub-firefox.md)).

**`error_trace`.** Stack traces are formatted differently by different engines, and
the detector tests the string against a single named signature: PhantomJS.

**`function_bind`** checks whether `Function.prototype.bind` exists as a real
function at all, not what it returns; PhantomJS is the one that fails this, so a
missing bind reads as the same tool `error_trace` looks for. **`window_external`**
matches `window.external.toString()` against the name of a specific commercial
scraping platform, Sequentum. **`app_version`** tests that same kind of version
string for three more names: headless, Electron, SlimerJS.

None of these are secret. They are just values a spoofing layer does not think about,
because they are not on the list of "things bots are known for".

## The second group: does your story hold together?

This group mostly asks whether a value a real browser always produces is missing,
empty, or malformed, not whether a value looks unusual on its own.

**`languages_inconsistency`** fires when `navigator.language` and its older fallbacks
are all undefined, something a real browser never leaves empty.
**`plugins_inconsistency`** fires when a browser claiming to be desktop Chrome reports
zero plugins, when even a stock install ships the built-in PDF viewer.
**`mime_types_consistence`** checks that `navigator.mimeTypes` and each of its entries
carry the real `MimeTypeArray` and `MimeType` prototypes, not a plain object standing
in for them.

**`rtt`**, **`window_size`**: values a real machine and a real window have, and a
container often does not.

**`document_element_keys`** and **`distinctive_properties`**: attributes and globals
that specific automation tools leave behind, which is the closest BotD gets to
looking for bots directly.

## What this means if you are on the other side

Three things follow: spoofing a user agent alone tends to make a browser easier to
detect rather than harder, being a genuine instance of the engine you claim passes
this whole detector class for free, and the risk BotD does not touch moves to other
signals entirely. They are the same three that turn up everywhere in this area.

**[Spoofing a user agent alone is worse than not spoofing it](is-changing-user-agent-enough.md).** Change the string and
you have to change `productSub`, `eval.toString().length`, the stack-trace format,
`window.external`, and the plugin and MIME arrays to match, or you have created the
exact contradiction BotD is built to find. Most user-agent spoofing makes a browser
easier to detect, not harder.

**Being a real instance of what you claim passes this whole class for free.** A real
Firefox reporting Firefox has the right `productSub`, the right `eval` length, the
right stack format and the right plugin array, because they are not claims, they are
the engine. There is nothing to keep consistent because nothing was changed.

**The remaining risk moves elsewhere.** Passing every one of these nineteen says
nothing about whether [your GPU string is plausible](webgl-renderer-strings.md), your
fonts match your platform, or [your timezone agrees with your IP](timezone-proxy-mismatch.md).
Those are the ones that decide modern outcomes and BotD does not look at them.

## Checking your own

BotD is on npm and takes about four lines to run. Do it on the machine that will do
the work instead of your laptop, and read which detectors fired rather than the
verdict.

If any of the engine-identity or named-tool-fingerprint groups fire, stop and fix
that first: it means either the browser you are presenting is not the browser you
are running, or a specific automation tool's own fingerprint just showed up, and no
amount of work on the other surfaces compensates for that.

## Short answers to the questions that lead here

**What is BotD?** The open-source bot detector from the FingerprintJS team. It runs in
the page and returns a verdict rather than a fingerprint.

**Is BotD the same as FingerprintJS?** No. FingerprintJS
[identifies a visitor across sessions](fingerprintjs-visitor-id.md). BotD answers a
different question: is this automation.

**Why does most of it not look like bot detection?** Because most detectors check which
browser engine you really are, by testing behaviours that differ between engines. A
browser claiming one engine and behaving like another is the finding.

**Can I pass it by setting `navigator.webdriver` to undefined?** No. That is
[one detector out of nineteen](navigator-webdriver-explained.md).

**Does passing BotD mean I am undetected?** It means one open-source detector found
nothing. Commercial systems combine far more, including things no in-page script can
see.

## Sources

- BotD's own GitHub repository, [`fingerprintjs/BotD`](https://github.com/fingerprintjs/BotD),
  `src/detectors/` and `src/sources/`, read 2026-08-29, for the nineteen detector names and
  the values each one compares.
- [MDN: `Navigator.webdriver`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/webdriver),
  retrieved 2026-08-29, for the one detector everybody already knows.
- [MDN: `Navigator.productSub`](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/productSub),
  retrieved 2026-08-29, for the fixed value Firefox reports that the `product_sub`
  detector checks against.
- [PhantomJS](https://github.com/ariya/phantomjs), read 2026-08-29, the named signature
  the `error_trace` detector matches against in a stack trace, and the tool a missing
  `Function.prototype.bind` reads as under `function_bind`.
- This project's own detection gates, which run BotD against the product before every
  release and read which detectors fired rather than only the verdict.

**See also:** [what sannysoft checks](sannysoft-explained.md), which is the older
list of the same kind, and [how CreepJS detects tampering](creepjs-explained.md),
which takes the consistency idea considerably further.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
We gate every release on BotD, so the detector names and the compared values above
come out of its source and not out of memory.*
