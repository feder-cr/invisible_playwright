---
title: "Cloudflare Bot Management, Explained"
description: "Cloudflare Bot Management is the always-on scoring engine behind Cloudflare's edge, not the Turnstile widget: a 1-99 bot score from JA3/JA4, HTTP fingerprinting, ML and behavior, computed on nearly every request. Read from Cloudflare's own docs."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 44
---


# Cloudflare Bot Management, Explained

Cloudflare Bot Management is not a widget anyone sees. It is the scoring system running in
the background on almost every request to a Cloudflare-protected site, whether that site
ever shows a visitor a challenge or not. [Turnstile](cloudflare-turnstile-explained.md), the
checkbox-or-nothing widget covered elsewhere on this site, is a separate product a site
opts into and embeds on specific pages. Bot Management is the always-on system behind the
scenes that produces a score, which can feed a WAF rule, a Turnstile risk decision, or
nothing visible at all, depending on how the site owner wired it up.

Cloudflare sells both under the same "bot" umbrella, and a lot of practitioner writing
blends them into one paragraph. This page keeps them apart: what the scoring engine
measures, the score itself, the three product tiers it ships in, and where it hands off to
Turnstile rather than replacing it. Built from Cloudflare's own developer documentation and
one independent technical write-up, retrieved 2026-08-30.

## The bot score: 1 to 99, and what the number means

Cloudflare's own definition is direct: "a score of 1 means Cloudflare is quite certain
the request was automated, while a score of 99 means Cloudflare is quite certain the
request came from a human." Lower is more automated, not the other way around, which
trips people up the first time they read a Bot Analytics dashboard.

The documented bands:

| Score | Category |
|---|---|
| 1 | Automated, high confidence |
| 2-29 | Likely automated |
| 30-99 | Likely human |
| Verified bot | A non-malicious, declared automation (search engine crawlers, uptime monitors) scored separately from the numeric band |
| 0 | Not computed at all for this request |

That last row matters more than it looks: a score of 0 is not "definitely human," it means
Bot Management never ran a scoring pass on that request, for reasons Cloudflare does not
fully enumerate. Reading 0 as a clean bill of health is a misreading of the vendor's own
scale.

Granular, per-request scores are an Enterprise-with-Bot-Management feature. Sites on lower
tiers see traffic bucketed into groupings in a dashboard, not the raw 1-99 number, which the
tier section below spells out.

## What actually produces the score

Cloudflare names four detection engines, and they do not all run on every request or plan:

| Engine | What it does | Plan requirement |
|---|---|---|
| Heuristics | Matches every request against "a growing database of malicious fingerprints"; runs on all requests, all plans | All plans |
| JavaScript Detections (JSD) | Lightweight, invisible client-side script injection aimed at "headless browsers and other malicious fingerprints"; feeds its result to the other engines | All plans, optional |
| Machine Learning (ML) | Cloudflare states this engine "accounts for the majority of all detections," using supervised learning over "headers, session characteristics, and browser signals" to set the final 1-99 score | Business and Enterprise |
| Anomaly Detection (AD) | Unsupervised learning against a per-domain traffic baseline to flag outliers | Enterprise, and Cloudflare has been deprecating it |

Cloudflare's own docs name the categories and stay vague on field-level detail: the company
does not publish which headers, timing values or JavaScript properties the ML engine
weighs, or what JSD's script reads once injected. A Scrapfly write-up, working from the
outside, describes the JS-side probing as covering canvas and WebGL rendering output
alongside mouse-movement and click-cadence behavior, reconstructed from observation, the
same caveat this corpus applies to [Akamai's sensor payload](akamai-bot-manager-explained.md)
and [Imperva's reese84 cookie](imperva-incapsula-explained.md).

One documented mechanism sits underneath all of this: the `__cf_bm` cookie, necessary for
Bot Management (and the free Bot Fight Mode) to function, generated per-site, expiring
after 30 minutes of inactivity, and used, in Cloudflare's own words, to "smooth out the bot
score and reduce false positives" by carrying a session's established pattern forward
instead of re-deriving it cold on every request.

## The network layer: JA3/JA4 as one input, not the whole score

TLS fingerprinting is a documented, named input. Cloudflare's own description of JA3/JA4:
they "identify TLS clients based on how they initiate connections," and the resulting
fingerprint is "a stable identifier across different destination IPs, ports, and
certificates." JA4 exists because JA3 decayed: modern browsers randomize TLS extension
order per connection, which used to make JA3 hashes inconsistent for the same real browser,
and JA4 fixes that by sorting extensions before hashing instead of hashing arrival order.
The mechanics of both, and why one can be patched and the other cannot, are covered in
depth on [the JA3/JA4 page](ja3-ja4-tls-fingerprint.md) and
[the HTTP/2 fingerprint page](http2-fingerprint-detection.md); this page only needs that
Cloudflare treats JA3/JA4 and HTTP/2 metadata (SETTINGS values, pseudo-header order) as
inputs, exposed to Enterprise customers as `cf.bot_management.ja3_hash` and `ja4` fields
for custom WAF rules.

A matching TLS fingerprint is one input clearing, not a passed score. The same point is
made at length for a comparable vendor, where a byte-identical JA3/JA4/HTTP2 fingerprint
still did not stop a block on one deployment: see
[the documented Camoufox case](akamai-bot-manager-explained.md#the-case-that-shows-matching-fingerprint-is-not-passing).
Cloudflare's own ML engine is described the same way, as combining "headers, session
characteristics, and browser signals," never any one of those alone.

## Three tiers, and what each one actually adds

Cloudflare sells bot detection at three levels; the free and mid tiers use the same
underlying engines as Enterprise Bot Management, with less control over what happens next.

| Tier | Plan | What it adds |
|---|---|---|
| Bot Fight Mode | Free | Challenges or blocks obvious bots; no per-category actions, no custom rules |
| Super Bot Fight Mode | Pro, Business | Adds, in Cloudflare's own phrasing, "configurable actions per bot category, bot analytics, and the ability to create exceptions using WAF custom rules," across three groupings: "Definitely automated," "Likely automated," "Verified bots" |
| Bot Management | Enterprise, paid add-on | Per-request numeric scores instead of groupings, JA3/JA4 signal fields for custom rules, dedicated Bot Analytics; Cloudflare recommends it specifically for "API or mobile app traffic" |

A site on Super Bot Fight Mode and one on full Bot Management run the same engines
described above; the difference is what the owner can see and do with the result, not a
different sensor underneath. The two are also mutually exclusive on a zone: enabling
Enterprise Bot Management replaces the Bot Fight Mode / Super Bot Fight Mode toggles
rather than stacking with them.

## Where Turnstile actually fits into this

This distinction is worth being precise about, because the two products get merged in
casual writing constantly. Bot Management is the always-on scoring system: it runs whether
or not the page shows anything, and its output is a number a site owner's rules can act on.
[Turnstile](cloudflare-turnstile-explained.md)
is a widget a site chooses to embed on a specific page, most often a login, checkout, or
comment form, with its own non-interactive checks (proof-of-work, proof-of-space, API
probing, covered in full on the Turnstile page) and its own token verified server-side
against Siteverify. The two often work together: a site can wire a low Bot Management
score into a rule that shows Turnstile specifically to the traffic already flagged,
rather than to everyone. But Turnstile is not how Bot Management works, and passing one
is not evidence about the other. A visitor with a clean Turnstile pass on one page can
still carry a low background bot score on the next request if that page has no Turnstile
embedded to act on it, because the underlying score is computed independently of whether
any widget is present at all.

## The ecosystem around it, and what it actually claims

A real, actively maintained open-source project,
[`cloudscraper`](https://github.com/VeNoMouS/cloudscraper) (6.7k stars at the time of
writing), is worth naming because it shows what practitioner tooling in this space actually
targets: its README describes handling Cloudflare's older "I'm Under Attack Mode"
JavaScript challenges (v1-v3) and, since its 3.0 release, Turnstile CAPTCHA challenges,
using a JavaScript engine to solve the puzzle payload served. That is a different problem
from the one this page describes. Solving a visible challenge is downstream of a decision
Bot Management already made to show one; it says nothing about the numeric score itself,
never exposed to the client, or the session history the ML engine weighs alongside the
current request. Reliably solving every challenge a site serves is not the same as learning
what score triggered it.

## The honest boundary

An engine-level, patched Firefox like this project answers the client-observable layer
honestly: the TLS handshake and the resulting JA3/JA4 hash come from the real network
stack, not a JavaScript override, and the canvas, WebGL, font and navigator surfaces JSD's
script can read are produced by an actually-genuine engine rather than simulated. That is
real, and only part of what Cloudflare's own documentation says the ML engine weighs.

What stays outside anything a browser build touches: the bot score itself, a server-side
number never disclosed to the client that generated it; IP and ASN reputation, which
travels with the exit connection, not the browser; `__cf_bm` cookie state accumulated
across a session; and whatever the supervised model learned from "billions of daily
requests," which no outside party has visibility into. `invisible_playwright` does not
claim to defeat, bypass, or predict Cloudflare's bot score. A real engine changes whether
the client-side half of that score is computed on honest inputs or spoofed ones; it has no
effect on the half that never reaches the client at all.

## Short answers to the questions that lead here

**Is Cloudflare Bot Management the same thing as Turnstile?** No. Bot Management runs on
requests regardless of whether a widget is present. Turnstile is a separate, opt-in
challenge widget a site embeds on specific pages. The two can be wired together, but
neither is a mode of the other.

**What does a Cloudflare bot score of 1 mean, and what does 99 mean?** 1 is Cloudflare's
highest-confidence "this was automated" reading; 99 is the highest-confidence "this was a
human" reading. A score of 0 means no scoring pass ran at all, not a clean result.

**What is Super Bot Fight Mode?** The Pro/Business-tier product, using the same detection
engines as Enterprise Bot Management, adding per-category actions across three bot
groupings, bot analytics, and WAF custom-rule exceptions the free Bot Fight Mode lacks.

**Does a matching TLS (JA3/JA4) fingerprint mean a request passes Cloudflare's scoring?**
No. It is one documented input among several (heuristics, JavaScript detections, machine
learning, and historically anomaly detection). The same "necessary, not sufficient" pattern
is documented in detail for a comparable vendor in
[the Akamai Bot Manager case study](akamai-bot-manager-explained.md).

**Can a browser automation tool guarantee a good Cloudflare bot score?** No. The score is
computed server-side from reputation and session history a browser build has no way to see
or influence.

**Does invisible_playwright bypass Cloudflare Bot Management?** No. It does not solve
Turnstile's interactive tier, does not predict or spoof the bot score, and makes no claim
to defeat the scoring pipeline as a whole. It answers the client-observable inputs to that
pipeline (TLS handshake, canvas, WebGL, fonts, navigator surface) honestly, because they
come from a genuinely real engine rather than a script overriding them.

**See also:** [How Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md)
for the widget product this page deliberately does not re-cover; [How Akamai Bot Manager
actually works](akamai-bot-manager-explained.md) for a comparably-structured scoring
pipeline and a documented case where a matching network fingerprint was not enough; [JA3
and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) and [HTTP/2
fingerprint: the layer above the TLS handshake](http2-fingerprint-detection.md) for the
network-layer inputs above; and [Browser trust scores explained](browser-trust-score-explained.md)
for why a Cloudflare bot score, a CreepJS trust score and a reCAPTCHA v3 score do not imply
each other.

## Sources

- Cloudflare, [Bot scores](https://developers.cloudflare.com/bots/concepts/bot-score/),
  retrieved 2026-08-30, for the 1-99 scale, the documented score bands, and the `__cf_bm`
  cookie's role.
- Cloudflare, [Bot detection engines](https://developers.cloudflare.com/bots/concepts/bot-detection-engines/),
  retrieved 2026-08-30, for the Heuristics, JavaScript Detections, Machine Learning and
  Anomaly Detection descriptions and plan requirements.
- Cloudflare, [Bot Management for Enterprise](https://developers.cloudflare.com/bots/plans/bm-subscription/),
  retrieved 2026-08-30, for the Enterprise tier's capabilities and its mutual exclusivity
  with Bot Fight Mode / Super Bot Fight Mode.
- Cloudflare, [Super Bot Fight Mode overview](https://developers.cloudflare.com/bots/get-started/super-bot-fight-mode/),
  retrieved 2026-08-30, for plan availability, the three bot groupings, and the difference
  from free Bot Fight Mode.
- Cloudflare, [JA3/JA4 fingerprint](https://developers.cloudflare.com/bots/additional-configurations/ja3-ja4-fingerprint/),
  retrieved 2026-08-30, for the TLS fingerprint description and the exposed
  `cf.bot_management.ja3_hash` / `ja4` fields.
- Cloudflare, [Cloudflare Cookies](https://developers.cloudflare.com/fundamentals/reference/policies-compliances/cloudflare-cookies/),
  retrieved 2026-08-30, for the `__cf_bm` cookie's per-site generation, 30-minute
  inactivity expiry, and stated purpose.
- Scrapfly, ["How Cloudflare Detects Bots: TLS, HTTP/2, Canvas, and Turnstile
  Explained"](https://scrapfly.io/blog/posts/how-cloudflare-detects-bots), retrieved
  2026-08-30, a practitioner write-up (not Cloudflare's own documentation) for the
  reconstructed canvas/WebGL and behavioral detail behind JavaScript Detections.
- [`VeNoMouS/cloudscraper`](https://github.com/VeNoMouS/cloudscraper), GitHub, retrieved
  2026-08-30, for what a real, actively maintained practitioner tool in this space targets
  (IUAM JavaScript challenges and Turnstile CAPTCHA pages), distinct from the scoring
  engine this page describes.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level and driven by stock Playwright. Bot Management's actual
score never reaches the client that earned it; what this page can speak to is the half of
the pipeline a real browser engine touches, and it stops exactly where that half stops.*
