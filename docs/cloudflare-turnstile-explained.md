---
title: "How Cloudflare Turnstile actually works"
description: "Turnstile is not a puzzle widget, it is a signal-gathering decision engine: non-interactive JS challenges, proof-of-work, a risk-scored escalation to a checkbox, and a server-side token check. Read from Cloudflare's own docs."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 23
---


# How Cloudflare Turnstile actually works

Turnstile is not a captcha with the images swapped for a checkbox. Most of the time
there is no checkbox at all: the widget runs a set of quiet JavaScript checks in the
background, decides the visitor is probably fine, and hands back a token before a
human would even notice anything happened. The checkbox is the fallback path, shown
only when those quiet checks come back uncertain.

This page is about that mechanism, read from Cloudflare's own developer
documentation rather than guessed at from a blocked request. Not how to get past it,
how it decides.

## What the widget actually is

A Turnstile deployment has two halves. The client-side widget is a script loaded
from `https://challenges.cloudflare.com/turnstile/v0/api.js` and dropped into a page
either implicitly, by adding a `cf-turnstile` container div and letting the script
scan for it on load, or explicitly, by calling `turnstile.render()` yourself when
you need control over timing, such as a form that only appears after some other
interaction. Each widget carries a public sitekey and is bound to a private secret
key that never leaves your server.

When the widget finishes its checks, it produces a token and writes it into a hidden
`cf-turnstile-response` field (or hands it to a callback you registered). That token
is not proof of anything by itself. It has to be checked, server-side, against
Cloudflare's Siteverify API, which is the second half of the deployment and the part
a lot of first attempts skip.

## The part that runs before you ever see a checkbox

Cloudflare's own description of the mechanism is that it runs "a series of small
non-interactive JavaScript challenges" to gather signals about the visitor and the
browser environment, and names the categories directly: proof-of-work, proof-of-space,
probing for web APIs, and other checks aimed at browser quirks and human behavior.

**Proof-of-work** is a small computational puzzle the browser has to solve, the same
family of idea as Hashcash: spend a measurable amount of CPU on something that is
cheap for the server to verify. A script that can run arbitrary JavaScript pays this
cost the same as a real browser does; a client with no JS engine at all cannot pay it
and never produces a token. A gate built on that idea alone exists in the open, and
[Anubis is readable end to end](anubis-proof-of-work-explained.md) in a way Turnstile
is not, which makes it the cheapest way to see what this layer can and cannot decide.

**Proof-of-space** is the storage-based cousin of the same idea, trading disk or
memory instead of CPU cycles.

**Web API probing** and **browser-quirk detection** are the categories that matter
most for anything driving a real browser: calling real, specified APIs and checking
whether the answers are internally consistent with each other and with the engine the
page claims to be. Cloudflare does not publish the exact list of properties it reads,
and the widget script itself is not meant to be human-readable, so anyone claiming a
precise inventory of what it checks is going further than Cloudflare's own
documentation does. What is documented is the shape of the approach, not the field
names. The shape is the part worth internalising: a value being wrong matters less
than two values disagreeing, which is why a mismatch like
[a platform string that contradicts the oscpu beside it](navigator-platform-oscpu-consistency.md)
is a stronger signal than either field on its own.

The point of running all of this before showing anything is stated plainly in
Cloudflare's docs too: the goal is to "fine-tune the difficulty of the challenge to
the specific request and avoid showing a visual or interactive puzzle to a user" who
does not need one. Most visitors, automated or not, never see a widget at all if the
non-interactive layer is satisfied.

## The three modes, and when a checkbox appears

A site owner picks one of three widget modes when they create a sitekey, and the
mode decides whether an interactive step exists at all.

| Mode | What the visitor sees | When it appears |
|---|---|---|
| Managed | Nothing, or a checkbox | Non-interactive checks run first; a checkbox appears only if visitor risk is high enough |
| Non-Interactive | A loading spinner, never a checkbox | Verification always runs silently in the background |
| Invisible | Nothing at all | No visible widget or loading indicator under any outcome |

Managed is the one most sites use and the recommended default in Cloudflare's own
guidance, because it is the only mode that can show an interactive step, and it only
does so when the signal-gathering layer above flags the visitor as worth a second
look. Non-Interactive and Invisible never show a checkbox by design, no matter what
the signals say; a site that wants a hard human gate has to use Managed.

## The managed challenge: a human gate, not a fingerprint gate

When Managed mode does decide to show something, Cloudflare's own documentation
describes it as a "simple checkbox that the visitor must click to proceed", explicitly
not an image puzzle or text to transcribe. This is the point worth being precise
about: the checkbox is not another fingerprint probe layered on top of the earlier
ones. It is a click, gated behind whatever risk score the earlier signals produced,
and a click carries its own provenance:
[a click dispatched from page JavaScript is never trusted](playwright-clicks-istrusted.md),
while one produced by the driver or the engine is.

That distinction matters for what a real engine can and cannot do about it. Everything
in the non-interactive layer, the proof-of-work, the API probing, the consistency
checks, is answered by whatever is running the JavaScript. A genuine browser engine
answers those honestly because it is not simulating the answers, it is producing
them the normal way, the same way it would for any other page. The interactive
checkbox tier is a different kind of gate: it exists specifically for the visitors
the earlier layer could not clear on signal alone, and clicking it is a human action,
not a value a browser reports. No amount of engine-level fidelity changes what that
tier is checking for, because it stopped checking the browser and started checking
for a click.

## The token, and why passing the widget is not the finish line

A successful pass, interactive or not, produces a token. Cloudflare's own
documentation is direct about what that token is worth on its own: "You must call
the Siteverify API to complete your Turnstile implementation. The client-side widget
alone does not protect your forms." The token proves the widget ran and returned
success; it proves nothing to your server until your server checks it.

The check is a server-to-server call:

```
POST https://challenges.cloudflare.com/turnstile/v0/siteverify
```

with the site's secret key and the token as the two required parameters (an optional
`remoteip` and an `idempotency_key` for safe retries are also accepted). The response
is JSON: a `success` boolean, a `challenge_ts` timestamp, the `hostname` the challenge
ran on, an `action` and `cdata` if the integration set them, and an `error-codes`
array when it fails.

Two properties of the token matter for anyone thinking about this mechanically.
It expires 300 seconds after it is issued, and it is single-use: presenting the same
token to Siteverify twice returns a `timeout-or-duplicate` error on the second call.
A token cannot be harvested once and replayed later, and a token generated for one
page load does not answer for the next one.

## What one real deployment looked like

Security researcher Troy Hunt wrote up putting invisible Turnstile in front of an API
endpoint that was under sustained bot traffic, and the numbers are a useful reality
check on what the mechanism actually filters in practice. Over roughly five months
and more than 100 million challenges issued, he reported a 91% pass rate among issued
tokens, around 990,000 requests rejected for arriving without a valid token, and
traffic that had previously spiked to 121,300 requests in a five-minute window during
attacks dropping to zero abnormal spikes after deployment. He also noted that 40% of
the requests that failed validation traced back to just five distinct JA3 TLS
fingerprints, a network-layer signal outside anything the JavaScript layer measures.

That is one deployment, on one endpoint, and it is not a claim that every Turnstile
deployment behaves identically. It is real, named, and checkable, which is more than
most numbers floating around this topic can claim.

## Where an engine-level browser actually helps, and where it does not

The non-interactive layer is built to read real signals from a real JavaScript
environment: whether a proof-of-work computation lands where a real engine's timing
puts it, whether the web APIs it probes answer the way the engine they claim to be
actually answers, whether the browser-quirk checks find the quirks a genuine build of
that engine has. A browser that is genuinely the engine it presents, patched at the
level that produces those answers rather than overridden from a content script,
answers this layer the same way any other real instance of that engine does, because
there is no simulated answer to catch, only the real one.

What that does not change is the interactive tier. If Managed mode's risk scoring
decides a visitor needs to click the checkbox, an honest engine fingerprint does not
make that requirement disappear, because the requirement was never about the
fingerprint in the first place. It is a human-verification gate sitting downstream of
the signal layer, and no amount of realness upstream removes a gate that is checking
for something else entirely.

None of this is a claim that any tool solves Turnstile, and a captcha-solving promise
is not one this project makes: `invisible_playwright` does not click checkboxes for
you or defeat the interactive tier. What a real, engine-level Firefox does is answer
the non-interactive layer honestly, the same way a real Firefox always would, which is
the layer that decides whether most visitors ever see a checkbox at all.

## Short answers to the questions that lead here

**Does Cloudflare Turnstile detect headless browsers?** The non-interactive layer is
built to catch the gap between a browser's claimed engine and its actual JavaScript
environment, which is exactly the kind of gap an incomplete or scripted environment
tends to have. Cloudflare does not publish a specific "headless" flag; it publishes
signal categories, not a checklist.

**What is the difference between Turnstile's three modes?** Managed can show an
interactive checkbox when the earlier signals look risky; Non-Interactive always
verifies silently behind a spinner and never shows a checkbox; Invisible shows nothing
at all under any outcome. Only Managed has a human-click tier.

**Does passing the checkbox mean I passed everything?** No. The checkbox produces a
token, and the token only counts once your server has validated it against
Siteverify. A token that is never checked server-side protects nothing.

**Can a Turnstile token be reused or harvested for later?** No. It expires after 300
seconds and Siteverify rejects a second submission of the same token with a
`timeout-or-duplicate` error.

**Is Turnstile the same thing as a captcha?** No, and Cloudflare's own framing leans
on that distinction: it is a low-friction check that tries to avoid ever showing a
visual or interactive puzzle, reserving the checkbox for visitors its non-interactive
signals could not already clear.

**Does a real browser engine bypass Turnstile?** It answers the non-interactive
signal layer the way any genuine instance of that engine does, because those answers
are not overridden, they are the real ones. It has no effect on the interactive
checkbox tier, which is a human-verification gate, not a fingerprint check.

**Where does the actual detection logic live?** Cloudflare does not publish the exact
properties the widget reads or the scoring behind the risk decision. What is
documented is the category list (proof-of-work, proof-of-space, web API probing,
browser-quirk and behavior checks) and the three-mode structure; the internals below
that are not public.

**See also:** [navigator.webdriver is not the tell you think it is](navigator-webdriver-explained.md),
for the one property every detector checks first and why patching it alone changes
little; [Function.prototype.toString and the \[native code\] check](tostring-native-code-detection.md),
for why a JavaScript-level override of a Web API is itself detectable; [Playwright
detected as a bot: the checklist to fix it](playwright-detected-as-bot.md), for a
troubleshooting order when one site starts showing automation a different page; and
[what mouse-dynamics behavioral biometrics score](mouse-dynamics-behavioural-biometrics.md),
for the kind of human-behavior signal Cloudflare's category list names but does not
detail.

## Sources

- Cloudflare, [Turnstile overview](https://developers.cloudflare.com/turnstile/),
  retrieved 2026-08-30, for the non-interactive challenge categories (proof-of-work,
  proof-of-space, web API probing, browser-quirk and behavior detection) and the
  stated goal of avoiding a visual puzzle where possible.
- Cloudflare, [Turnstile widgets](https://developers.cloudflare.com/turnstile/concepts/widget/),
  retrieved 2026-08-30, for the Managed, Non-Interactive and Invisible mode
  definitions and when each one can show a checkbox.
- Cloudflare, [Turnstile challenge types](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/turnstile/),
  retrieved 2026-08-30, for the signal-based decision framing and the description of
  the interactive challenge as a checkbox click.
- Cloudflare, [Embed the widget](https://developers.cloudflare.com/turnstile/get-started/client-side-rendering/),
  retrieved 2026-08-30, for the widget script URL, implicit vs. explicit rendering,
  and the `cf-turnstile-response` token field.
- Cloudflare, [Server-side validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/),
  retrieved 2026-08-30, for the Siteverify endpoint, its parameters and response
  fields, and the 300-second single-use token behavior.
- Troy Hunt, ["Fighting API Bots with Cloudflare's Invisible Turnstile"](https://www.troyhunt.com/fighting-api-bots-with-cloudflares-invisible-turnstile/),
  retrieved 2026-08-30, for the real deployment numbers quoted above: challenge
  volume, pass rate, rejected requests, and the JA3-fingerprint concentration among
  failures.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level and driven by stock Playwright. This page describes
a mechanism, not a workaround: the product does not solve captchas or click Turnstile's
interactive tier for you, and any tool that claims to defeat Cloudflare wholesale is
promising more than the docs above actually say.*
