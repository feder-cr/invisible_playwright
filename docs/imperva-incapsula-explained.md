---
title: "Imperva (Incapsula), Explained"
description: "Imperva's bot management, still known by its old Incapsula cookie names, scores a session with an obfuscated JavaScript sensor and a three-step cookie/JS/CAPTCHA escalation. Read from Imperva's own docs and confirmed reverse engineering."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 28
---


# Imperva (Incapsula), Explained

Imperva is a commercial web application firewall and bot management vendor, and its bot product still carries the fingerprints of Incapsula, the company Imperva acquired in 2014 and folded its WAF business into. The cookie names it sets today, `incap_ses_*` and `visid_incap_*`, are Incapsula's own naming, over a decade later. Whether a search leads with "Imperva" or "Incapsula" it is the same detection stack underneath.

Imperva made a second, later acquisition in the same product line: it announced the purchase of Distil Networks on June 4, 2019, a separate bot-detection vendor founded in 2011 that Imperva's own press release called "the industry-recognized leader in Bot Management." Distil ran briefly as its own platform inside Imperva before being fully folded into what Imperva now sells as Advanced Bot Protection, the same product line already covered on this page. A search for the legacy "Distil Networks" name today is looking for the same detection stack this page describes, not a separate, still-active product.

Like DataDome, Imperva is not a library you can read. What follows separates Imperva's own published claims from what independent reverse engineering has worked out from the outside, and marks each claim accordingly.

## The shape of it: escalating challenges, not one check

Imperva's own 2016 explanation of Incapsula client classification, still live on its blog, describes a **sequential escalation** rather than a single test. A visitor is only asked to do more once the cheaper check fails:

1. **Cookie challenge.** The server sets a cookie and waits to see it echoed back. Imperva's own words: "Web browsers typically will store and resend this cookie. Most bots do not support cookies and therefore will not respond."
2. **JavaScript cookie challenge.** The server responds with a cookie that requires a small piece of JavaScript to run before it can be produced. Per Imperva: "Web browsers typically will execute the JavaScript instructions, on the other hand most bots do not support a JS engine and therefore will not respond."
3. **CAPTCHA.** The last resort, only reached once the first two fail to clear a visitor.

Imperva states plainly that "over half and up to 80% of malicious bots cannot pass the cookie or JavaScript challenges," which is the real point of the ordering: cheap checks filter out the bots that never bothered to run a browser at all, before anyone pays for a CAPTCHA render or a human's attention.

## The sensor: what a genuine browser answers, and what nobody but Imperva sees end to end

Imperva's own product marketing states its current Advanced Bot Protection detects "over 700 dimensions to separate between human, good, and bad bot traffic, creating a unique fingerprint" that it built through "direct client interrogation, behavior analysis, machine learning (ML), connection characteristics, and threat intelligence feeds." That is a real, sourced number, and it is also a ceiling on what this page can verify: Imperva does not publish the list of 700, so nobody outside the company can confirm what each one measures.

What independent reverse engineering has converged on, read from a practitioner writeup on bypassing this specific stack (treat this as reconstructed from the outside, not from Imperva's own source):

- **A JA3/JA4-class TLS fingerprint**, evaluated before any page content is served, the same network-layer mechanism [described in depth on the JA3/JA4 page](ja3-ja4-tls-fingerprint.md). A default `requests`-style HTTP client's handshake does not resemble a browser's, and that mismatch is visible at the TLS layer alone.
- **An obfuscated JavaScript sensor** whose payload becomes the `reese84` cookie once submitted, collecting canvas and WebGL rendering output, AudioContext behavior, and navigator-level properties (plugin count, MIME types, language, platform, hardware concurrency, device memory), plus timing signals: an operation that completes in exactly 0ms, or with unnaturally uniform timing between calls, reads as scripted rather than run on real hardware.
- **A cookie chain** beyond the classic pair: `_Incapsula`, `nlbi_*` for load-balancer routing, and `utmvc` for session consistency, alongside the `incap_ses_*`/`visid_incap_*` pair Incapsula has used since before the Imperva acquisition.
- **The `x-iinfo` response header**, which the practitioner sourcing describes as carrying encoded classification data about how the request was scored.

Two things are worth being direct about. First, none of the specific field values above come from Imperva's own documentation, only the category names (fingerprinting, behavior analysis, ML) do; the practitioner detail is reconstructed and should be read that way, the same caveat this corpus applies to [DataDome's collector internals](datadome-explained.md) and [Kasada's bytecode VM](kasada-explained.md). Second, a passing score here is not always visible as a blocked request: the same practitioner sourcing notes that Imperva's block page can return `200 OK` rather than `403`, with the rejection only legible in the page body (an `incapsula_main_message` div, "Powered By Incapsula" text, an incident ID). A scraper that checks only the status code can collect block pages as if they were data.

## What an engine answers honestly, and what does not touch a browser at all

The sensor's rendering and environment-consistency checks, canvas output, WebGL parameters, AudioContext behavior, navigator fields, are the same category [covered at length for CreepJS](creepjs-explained.md) and [PerimeterX](perimeterx-explained.md): a genuinely real browser engine, patched below the JavaScript layer rather than scripted from a content script, produces these values as an honest side effect of actually being that engine. There is no override to catch, because nothing overrode anything.

That says nothing about the network-layer fingerprint, which depends on the TLS stack and the exit connection, not the rendering engine; nothing about Imperva's threat-intelligence feeds, which score reputation this project has no visibility into and no way to influence; and nothing about the CAPTCHA tier itself, which is a human-verification gate, not a fingerprint check, the same structural point made about [Cloudflare Turnstile's checkbox](cloudflare-turnstile-explained.md).

`invisible_playwright` does not include a CAPTCHA-solving service and makes no claim to defeat Imperva's classification pipeline as a whole. What a real, engine-level Firefox changes is the honesty of the rendering layer the sensor reads, which is one input among several Imperva's own materials say it weighs, not the deciding one.

## Short answers to the questions that lead here

**Is Imperva the same thing as Incapsula?** Functionally yes. Imperva acquired Incapsula in 2014, and the bot-management product still sets Incapsula's original cookie names (`incap_ses_*`, `visid_incap_*`) more than a decade later.

**What is the `reese84` cookie?** Per practitioner reverse engineering, it is the encrypted output of Imperva's obfuscated JavaScript sensor, submitted back to Imperva for validation. It is not documented in Imperva's own public materials under that name.

**Why does Imperva challenge with a cookie before a CAPTCHA?** Imperva's own numbers say the cheap checks alone stop most of the traffic worth stopping: "over half and up to 80%" of malicious bots never make it past the cookie or JavaScript step, by the company's own account, which makes a CAPTCHA the expensive last resort rather than the first line.

**Does a real browser engine bypass Imperva?** It answers the rendering and environment-consistency layer honestly, which is one of the "700 dimensions" Imperva says it scores. The TLS fingerprint, IP/ASN reputation, and Imperva's threat-intelligence feeds are separate inputs an engine build does not touch.

**Can a `200 OK` response still be a block?** Per the practitioner sourcing checked for this page, yes. Imperva's block page does not always carry a `403` status, so a scraper checking status codes alone can silently collect block pages.

**Does invisible_playwright solve Imperva's CAPTCHA?** No. This project does not sell or include a CAPTCHA-solving service for Imperva or any other vendor. It is engine-level realness work, not a puzzle-solving product.

**See also:** [How DataDome's bot detection actually works](datadome-explained.md) for the same three-layer shape (network, device/JS, cookie memory) at a different vendor; [PerimeterX (now HUMAN Bot Defender)](perimeterx-explained.md) for a comparable cookie-chain architecture read from open SDK source; and [JA3 and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) for the network layer that runs before any of Imperva's JavaScript loads.

## Sources

- Imperva, [How Incapsula Client Classification Challenges Bots](https://www.imperva.com/blog/archive/how-incapsula-client-classification-challenges-bots/),
  retrieved 2026-08-30, for the cookie/JavaScript-cookie/CAPTCHA escalation sequence and the "80%" figure, quoted directly above.
- Imperva, [Imperva to Acquire Distil Networks, the Industry-Recognized Leader in Bot Management](https://www.imperva.com/company/press_releases/imperva-to-acquire-distil-networks-the-industry-recognized-leader-in-bot-management/),
  retrieved 2026-08-30, for the June 4, 2019 announcement date, Distil's 2011 founding, and the "industry-recognized leader in Bot Management" quote.
- BusinessWire, [Imperva Unveils Fully Integrated Advanced Bot Protection Solution](https://www.businesswire.com/news/home/20200225005362/en/Imperva-Unveils-Fully-Integrated-Advanced-Bot-Protection-Solution),
  retrieved 2026-08-30, for the Distil platform's integration into Advanced Bot Protection.
- Imperva, [Advanced Bot Protection product page](https://www.imperva.com/products/advanced-bot-protection-management/),
  retrieved 2026-08-30, for the "700 dimensions" figure and the named signal categories (client interrogation, behavior analysis, ML, connection characteristics, threat intelligence).
- Imperva, [Good Bots In, Bad Bots Out](https://www.imperva.com/blog/bot-classification/),
  retrieved 2026-08-30, for the three-step classification framing (signature match, behavioral profiling, challenge-based verification).
- ScrapeBadger, ["How to Bypass Imperva (Incapsula) Anti-Bot Protection: Complete 2026 Guide"](https://scrapebadger.com/blog/how-to-bypass-imperva-incapsula-anti-bot-protection-complete-2026-guide),
  retrieved 2026-08-30, a practitioner reverse-engineering writeup (not Imperva's own documentation) for the `reese84` cookie, the specific sensor signals (canvas, WebGL, AudioContext, navigator fields, timing), the cookie chain (`_Incapsula`, `nlbi_*`, `utmvc`), the `x-iinfo` header, and the `200 OK` silent-block behavior.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright).
Imperva does not publish its 700-dimension list or its `reese84` payload format, so this page
draws a line between what Imperva's own materials state in category terms and what outside
reverse engineering has reconstructed in specifics, the same way this corpus treats DataDome
and Kasada.*
