---
title: "Private Access Tokens (Privacy Pass), Explained"
description: "Private Access Tokens are a device-attestation replacement for CAPTCHAs, built on the IETF's Privacy Pass protocol. What the spec actually requires, how Apple's deployment works, and what it means for a real desktop browser, automated or not."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 41
---


# Private Access Tokens (Privacy Pass), Explained

A Private Access Token is not a browser check. It is a device check, and the browser's
job in the exchange is mostly to carry a message between a website's challenge and an
operating system's answer. That distinction is the whole page: almost everything else
this corpus covers, Turnstile's non-interactive layer, DataDome's collector, Akamai's
sensor, is a JavaScript environment answering questions a script can ask. Private Access
Tokens are built specifically so that no script gets to ask anything.

This page is read from the IETF's own Privacy Pass specification, from Apple, Cloudflare
and Fastly's own explanations of their deployment of it, and from independent technical
write-ups on where the mechanism currently runs. Not how to get past it. What it actually
proves, and, honestly, whether a real desktop automated browser is even likely to meet
one today.

## Privacy Pass is the protocol; Private Access Tokens is Apple's deployment of it

Privacy Pass is standardized across three IETF RFCs, all published in June 2024:
[RFC 9576](https://datatracker.ietf.org/doc/rfc9576/) defines the architecture,
[RFC 9577](https://datatracker.ietf.org/doc/rfc9577/) defines an HTTP authentication
scheme for carrying the tokens, and
[RFC 9578](https://datatracker.ietf.org/doc/rfc9578/) defines the issuance protocols
themselves, in both a privately-verifiable and a publicly-verifiable variant.

RFC 9576 names four roles: a **Client** ("an entity that seeks authorization to an
Origin"), an **Origin** ("an entity that consumes tokens presented by Clients and uses
them to make authorization decisions"), an **Issuer** ("an entity that issues tokens to
Clients for properties attested to by the Attester"), and an **Attester** ("an entity
that attests to properties of Clients for the purposes of token issuance"). "Private
Access Tokens" is Apple's own name for its specific deployment of that issuance
protocol, with Apple itself acting as the Attester. It is not a synonym for Privacy Pass
in general, and it is a different mechanism from Google's related but separate Private
State Token API (formerly Trust Tokens), which reuses some of the same cryptographic
building blocks under a different role split.

## The flow: a 401, a blind signature, and a token you present once

Apple's own developer documentation states plainly what the tokens are for: "Private
Access Tokens are powerful tools that prove when HTTP requests are coming from
legitimate devices without disclosing someone's identity. This proof can help you reduce
how often you show CAPTCHAs to people." The exchange, per Apple's own three-step
description and RFC 9577's header definitions, looks like this:

1. **The origin challenges.** A server that wants a token responds with `401` and a
   `WWW-Authenticate: PrivateToken` header carrying a base64url-encoded challenge
   (token type, issuer name, and the origin the token is valid for) and the issuer's
   public key.
2. **The client requests attestation, blindly.** The client cryptographically blinds
   the challenge and sends it to an Attester, which checks device properties it trusts
   without learning what site triggered the request. Fastly's own explanation calls out
   this separation directly: "the issuer signs the data without knowing what it is."
3. **The client redeems the token.** Once attestation succeeds, the client resends the
   original request with `Authorization: PrivateToken token="..."`, and the origin
   validates it against the issuer's public key.

The privacy property is real and by design: per Fastly, the Issuer never learns the
device's identity, and the Attester never learns which site triggered the check, so no
single party in the chain can link a person's browsing activity to their device. That is
a genuinely different design goal from a fingerprint collector, which exists specifically
to build that link.

## What "attestation" actually checks, and where it runs

This is the part worth being precise about, because the spec and Apple's specific
deployment of it say different things.

**The spec itself does not require hardware.** RFC 9576 states outright that "the type
of attestation procedure is a deployment-specific option and outside the scope of the
issuance protocol," and it lists examples ranging from "solving a CAPTCHA" to
"presenting evidence of Client device validity" to "proving properties about Client
state." Nothing in Privacy Pass mandates a hardware root of trust. A deployment could,
in principle, attest with something far weaker.

**Apple's own deployment chose hardware.** On Apple platforms, the Attester role is
backed by the Secure Enclave, Apple's dedicated cryptographic coprocessor. Apple has not
published the same low-level detail about Private Access Tokens specifically that it has
for its separate Managed Device Attestation feature, but both draw on the same
Secure-Enclave capability, and Apple's own documentation for that feature is explicit
about the mechanics: "the operating system generates a hardware-bound private key inside
the device's Secure Enclave," the key cannot be extracted "even in the case of a
compromised Application Processor," and validating the result means "evaluating that the
certificate chain is rooted with the expected Apple Certificate Authority." Private
Access Tokens' own privacy design, per Fastly's description above, means it does not
carry an identifying field like a serial number the way Managed Device Attestation does,
but the underlying hardware trust root, a key that never leaves the chip and a
certificate chain rooted in Apple's own CA, is the same building block. A jailbroken or
otherwise modified device cannot produce a valid attestation from it, by design.

The mechanical consequence: nothing here is a JavaScript-observable value. There is no
`navigator` property to read, no canvas to hash, no timing loop to measure. The check
happens in the OS networking stack and in silicon that the JavaScript engine cannot
reach even if it wanted to. This is a structurally different category from everything
else this corpus documents, and it is why "answer the check honestly at the engine
level," the approach this project and every C++-patched browser takes, has literally
nothing to answer here. There is no fingerprint layer to be honest about; the layer does
not run in the browser's script environment at all.

## What this means for a real desktop browser today

Coverage is the part most write-ups skip, and it matters more than the cryptography.

Private Access Tokens currently ship on **Apple platforms only**: Apple's own materials
name iOS 16, iPadOS 16 and macOS 13 (Ventura) with Safari. On the browsers this project
and most Playwright automation actually target, Windows and Linux desktop Firefox and
desktop Chrome, there is no Attester integration today. Mozilla opened a tracking issue
for Private Access Tokens on its `standards-positions` repository in January 2024, noting
plainly that this "hasn't received much scrutiny," and Mozilla's stated position on the
underlying Privacy Pass protocol is to "defer making a firm position until the protocol
and the novel cryptographic primitives it relies on have had more thorough security
analysis." Separately, Mozilla has taken a negative position specifically on Chrome's
related Private State Token API. Independent technical write-ups researching desktop
rollout describe both desktop Firefox and desktop Chrome as lacking the OS-level
integration Private Access Tokens depends on, partly because desktop browsers on
Windows and Linux typically run their own networking stack rather than the OS's, which is
exactly the layer this attestation needs to hook into.

That has a direct, practical consequence: a real, unmodified Firefox running on Windows
or Linux is in the same position as a real, unmodified Chrome on Windows or Linux,
neither ships an Attester today, so neither can produce a Private Access Token. One
independent analysis of the mechanism's rollout puts Safari's overall market share
around 20% against Chrome's 70%-plus, and a site cannot make a valid token a hard
requirement for the other roughly 80% of the web without losing most of its audience; it
has to treat token presentation as an optional fast lane, not a gate. A client that shows
up without one falls through to whatever the site's ordinary bot-mitigation path already is, which, on
most of the deployments this corpus covers, means a CAPTCHA or a challenge like
[Turnstile's non-interactive layer](cloudflare-turnstile-explained.md). Nothing about
that fallback path changes because the client happens to be automated rather than a
genuine desktop Firefox that also lacks Apple's attester; both look identical to a site
relying on Private Access Tokens as a signal, because both are, from that signal's point
of view, indistinguishable non-participants.

## Can this be spoofed? The honest boundary

No, and not for the reasons a fingerprint check can't be spoofed. A canvas value or a
`navigator` property is a number your patch chooses to return; forging it is a software
problem, hard but tractable. A Secure Enclave attestation is a signature computed with a
private key that, per Apple's own security materials, never leaves the hardware and
cannot be extracted even from a compromised application processor, verified against a
certificate chain rooted in a CA that only signs for hardware Apple manufactured. There
is no software path to a valid signature without that key, on any platform, patched or
not.

`invisible_playwright` does not claim to solve, forge, or bypass Private Access Tokens,
and no honest tool can claim that, because the thing that would need bypassing is not a
fingerprint at all. What this project does, patching Firefox at the C++ level so its
JavaScript-observable surface reads as a genuine Firefox build, is a complete non-answer
to a mechanism that runs entirely below the JavaScript layer. That is not a gap specific
to this project. It would be equally true of a perfectly genuine, unpatched Firefox on
the same desktop OS, since desktop Firefox does not have an Attester integration to use
in the first place.

## Short answers to the questions that lead here

**What are Private Access Tokens?** Apple's deployment of the IETF Privacy Pass issuance
protocol: a device proves it is genuine and unmodified via hardware-backed attestation,
and a website accepts a resulting cryptographic token instead of showing a CAPTCHA.

**Is this the same thing as Privacy Pass?** Privacy Pass is the underlying IETF standard
(RFC 9576-9578). Private Access Tokens is Apple's specific, named deployment of it, using
Apple as the Attester. Other deployments and related mechanisms, like Chrome's Private
State Token API, build on the same primitives with a different structure.

**Does this run in JavaScript?** No. The challenge and redemption travel as HTTP headers
(`WWW-Authenticate` / `Authorization`), and the attestation step happens in the OS
networking stack and, on Apple's deployment, in the Secure Enclave. There is no
JavaScript-observable fingerprint being checked.

**Will this affect my Windows or Linux Playwright automation today?** Almost certainly
not directly. Desktop Firefox and desktop Chrome do not currently have an Attester
integration, per Mozilla's own standards-positions tracking, so neither a real nor an
automated desktop browser on those platforms can present a valid token. Sites relying on
Private Access Tokens have to fall back to their ordinary challenge for everyone in that
position.

**Can a browser patch spoof device attestation?** No. The signature is computed with a
hardware-bound private key that never leaves the Secure Enclave and is verified against
a certificate chain rooted in Apple's own CA. There is no software substitute for
possessing that key.

**What is the difference between Private Access Tokens and the Private State Token API?**
Both build on Privacy Pass-family cryptography, but they split the Attester and Issuer
roles differently, and Mozilla has taken a negative position specifically on the Private
State Token API while only tracking, not rejecting, Private Access Tokens.

**Does invisible_playwright support or bypass Private Access Tokens?** Neither. It is
engine-level Firefox realness aimed at the JavaScript-observable surface. Private Access
Tokens operate beneath that surface entirely, so there is nothing at the engine level to
answer, honestly or otherwise.

**See also:** [How Cloudflare Turnstile actually works](cloudflare-turnstile-explained.md),
for the non-interactive JavaScript layer that a non-PAT client still has to answer; [how
Fastly's own bot management fits alongside this](fastly-bot-management-explained.md),
since Fastly is one of the token issuers named above; and
[what data websites actually collect about your browser](what-data-websites-collect-about-your-browser.md),
for how this compares to the JS-level signals the rest of this corpus documents.

## Sources

- IETF, [RFC 9576, The Privacy Pass Architecture](https://datatracker.ietf.org/doc/rfc9576/),
  retrieved 2026-08-30, for the four protocol roles and the definition of attestation as
  deployment-specific.
- IETF, [RFC 9577, The Privacy Pass HTTP Authentication Scheme](https://datatracker.ietf.org/doc/rfc9577/),
  retrieved 2026-08-30, for the `PrivateToken` challenge and redemption header formats.
- IETF, [RFC 9578, Privacy Pass Issuance Protocols](https://datatracker.ietf.org/doc/rfc9578/),
  retrieved 2026-08-30, for the privately- and publicly-verifiable token variants.
- Apple Developer, [Private Access Tokens](https://developer.apple.com/news/?id=huqjyh7k),
  retrieved 2026-08-30, for Apple's own three-step description, platform support (iOS 16,
  macOS Ventura), and the RSA Blind Signature token type.
- Apple Support, [Managed Device Attestation for Apple devices](https://support.apple.com/guide/deployment/managed-device-attestation-dep28afbde6a/web),
  retrieved 2026-08-30, for Apple's own description of the Secure Enclave-backed
  hardware attestation mechanics (the hardware-bound key, its non-extractability, and
  the Apple Certificate Authority root), used above as the documented building block
  Private Access Tokens' own attestation draws on, not as a claim that the two features
  are identical.
- Fastly, [Private Access Tokens: stepping into the privacy-respecting, CAPTCHA-less
  future we were promised](https://www.fastly.com/blog/private-access-tokens-stepping-into-the-privacy-respecting-captcha-less),
  retrieved 2026-08-30, for the role split and the blinded-signature privacy property.
- Cloudflare, [Privacy Pass](https://developers.cloudflare.com/privacy-pass/), retrieved
  2026-08-30, for Cloudflare's role as an Issuer and its framing of the protocol's
  original 2017 pioneering.
- HTTP Toolkit, [Apple already shipped attestation on the web, and we barely noticed](https://httptoolkit.com/blog/apple-private-access-tokens-attestation/),
  retrieved 2026-08-30, for the Secure Enclave-backed attestation flow, the jailbreak
  exclusion, desktop-coverage analysis, and the Safari/Chrome market-share figures cited
  above.
- Antti Snellman, [Web Environment Integrity vs. Private Access Tokens](https://www.snellman.net/blog/archive/2023-07-25-web-integrity-api-vs-private-access-tokens/),
  retrieved 2026-08-30, for the structural comparison with Google's Web Environment
  Integrity proposal.
- GitHub, [`mozilla/standards-positions` issue #954, Private Access Tokens](https://github.com/mozilla/standards-positions/issues/954),
  retrieved 2026-08-30, for Mozilla's tracking status and its stated deferral on Privacy
  Pass pending further cryptographic security analysis.

---

*From the notes of [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level. This page exists because "does a patched browser
beat Private Access Tokens" is a question with an honest, boring answer: on the desktop
platforms this project targets, there is currently no attestation layer there to beat,
and if there ever is one, no software patch is the thing that answers it.*
