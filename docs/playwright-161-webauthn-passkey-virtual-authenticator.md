---
title: "Playwright 1.61's WebAuthn Virtual Authenticator"
description: "Playwright 1.61 added browserContext.credentials, a virtual passkey authenticator with no hardware needed. Whether it looks different to a detecting site depends on whether that site asks for attestation at all."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 43
---


# Playwright 1.61's WebAuthn Virtual Authenticator

Playwright 1.61, released 15 June 2026, added `browserContext.credentials`, a virtual
WebAuthn authenticator that answers `navigator.credentials.create()` and
`navigator.credentials.get()` ceremonies without a physical security key or a
platform authenticator like Windows Hello. Its own release notes state:

> "New Credentials virtual authenticator, available via browserContext.credentials,
> lets tests register passkeys and answer navigator.credentials.create() /
> navigator.credentials.get() ceremonies in the page."

That version number and that feature description were checked against Playwright's
own release notes and its GitHub release page this session, not assumed. Both match
what is claimed here.

## What the feature actually does

`browserContext.credentials` exposes a `Credentials` object on the browser context.
Playwright's own API reference describes the property as a:

> "Virtual WebAuthn authenticator for this context. Lets tests seed credentials and
> intercept navigator.credentials.create() / navigator.credentials.get() ceremonies."

Its `install()` method is documented as "preventing all real authenticators from
working in this context" once armed - meaning a page's passkey prompts get answered by
Playwright's virtual authenticator instead of falling through to whatever the host
machine has available. A genuinely useful detail for anyone building a reusable
session: captured credentials can travel in `storage_state`, private keys included, so
a passkey registered once in a setup step can be seeded into later test runs the same
way a saved cookie jar already is.

The practical value is real and specific. Before this, testing a passkey or
passwordless flow meant either skipping it in CI (no fingerprint reader, no hardware
key available), stubbing it out at the network layer (never actually exercising the
real ceremony), or relying on the older, Chromium-only CDP `WebAuthn` domain by hand.
Playwright's own description places this API across Chromium, Firefox and WebKit, not
one engine.

## Whether it reaches invisible_playwright's Firefox

This is the part where the honest answer is a "test it," not a "yes."

Playwright does not talk to Firefox the way it talks to Chromium. There is no DevTools
Protocol on Firefox; Playwright drives it through Juggler, Firefox's own internal
automation protocol, and
[that protocol is closed-world](playwright-protocol-drift.md): a command the server
was never told to expect gets rejected outright, not silently ignored. A brand-new
capability like a virtual WebAuthn authenticator is, almost by definition, a new set
of Juggler commands that did not exist in an older build. For it to work against this
project's Firefox, two separate things have to both be true: Playwright's own
`browser_patches/firefox/juggler` has to define those commands (which the browser
Playwright bundles for 1.61 clearly does, since the feature ships and is documented
across all three engines), and this project's patched Firefox has to have picked up
that specific upstream Juggler change through its own periodic sync.

Whether the pinned binary this project ships has that sync is not something this page
can promise without testing it directly against a released build. If
`browserContext.credentials.install()` raises or times out here, the first thing to
check is not that the feature is broken in general - it is whether the Juggler sync
that carries new Firefox automation commands has caught up to this specific Playwright
version yet, the same class of gap
[the protocol-drift page](playwright-protocol-drift.md) describes for other new client
fields.

## Does a virtual authenticator look different to a detecting site?

This is worth answering carefully rather than assuming an answer either way, because
the honest result is not a flat yes or no. It depends entirely on one setting the
relying party controls: whether it asks for attestation at all.

**The AAGUID identifies a device model, and most sites never look at it.** Every
WebAuthn credential carries a 16-byte Authenticator Attestation GUID identifying the
authenticator's model, not the specific device. MDN's own description of the field is
direct about when it matters:

> "A relying party can use this to find out the characteristics of the authenticator by
> looking up its metadata statement via the FIDO metadata service. This is relevant in
> certain situations such as enterprise deployments or where regulatory requirements
> dictate a certain type of authenticator be used; it should be ignored otherwise."

**Attestation defaults to "none," and almost nobody asks for more.** A relying party
requests an attestation conveyance preference when it starts the ceremony, and "none"
is both the specification's default and, in practice, close to universal for consumer
login and signup. The reasoning cited across current WebAuthn guidance is practical
rather than security-driven: the passkey providers that mint most real-world
credentials, platform password managers on phones and desktops, do not return usable
device attestation in the first place, so a relying party that insisted on strong
attestation would simply reject a large share of its own legitimate users at
enrollment. A site using "none," which is most of them, has explicitly said it does not
care what produced the credential.

**Where it would actually be checked: attestation conveyance set to "direct," verified
against a trust anchor.** A minority of relying parties, concentrated in banking,
government and enterprise device-management deployments, request stronger attestation
and validate the returned certificate chain against a trusted root, or check the
AAGUID against an allowlist. That is a fundamentally different threat model from
general bot or scraping detection: it exists specifically to answer "is this a
certified, tamper-resistant authenticator," not "is a person present." A virtual
authenticator, Playwright's or anyone else's, has no legitimate manufacturer
certificate chain to present in that scenario, and would fail or stand out precisely
because that check is doing its job as designed.

**A weaker, secondary signal: the signature counter.** Some backend heuristics treat an
authenticator whose signature counter never increments as a soft signal worth a second
look, on the theory that a cloned or virtualized authenticator might behave that way.
This is not reliable on its own - real platform authenticators like Windows Hello and
Touch ID also commonly report a static or zero counter by design, so a low-confidence
heuristic like this one catches plenty of ordinary hardware alongside anything virtual.
It is worth knowing about, not worth treating as decisive either way.

**The honest summary:** for the overwhelming majority of real login and passkey-signup
flows, which request no attestation, nothing in the ceremony reveals whether the
authenticator behind it was virtual or physical, because the site never asked the
question that would show a difference. For the narrow set of relying parties that
specifically verify device attestation, a virtual authenticator is very likely
distinguishable, and that is by design on their end, not a flaw in Playwright's
implementation.

## What this is for, and what it is not

`browserContext.credentials` is a testing tool for a flow you already have valid
access to: verifying your own application's passkey registration and login work, the
way [automating an OTP or magic-link login](automating-email-otp-verification-login-playwright.md)
is a testing tool for a different second-factor flow. It does not let you forge
someone else's passkey or register a credential you were not otherwise able to
register; a virtual authenticator has to go through the same
`navigator.credentials.create()` ceremony a real one would, against an account you can
actually reach.

## Conclusion

Playwright 1.61 shipped a real, useful capability: a virtual WebAuthn authenticator
that lets a passkey or passwordless flow run in CI with no physical hardware, described
by Playwright's own docs as working across all three engines it drives. Whether it
reaches this project's specific patched Firefox depends on the same Juggler-sync
question that governs any brand-new automation command, and is worth testing directly
rather than assuming. And whether using it looks different from a real authenticator to
the site on the other end depends entirely on whether that site asked for attestation
in the first place - for nearly all of them, it did not, and there is nothing to tell
apart.

## Short answers to the questions that lead here

**What version of Playwright added the WebAuthn virtual authenticator?** 1.61,
released 15 June 2026, via `browserContext.credentials`.

**Does it work on Firefox, or only Chromium?** Playwright's own documentation
describes it as working across Chromium, Firefox and WebKit. Whether this project's
specific patched Firefox build has picked up the underlying Juggler commands is
something to test against the pinned binary rather than assume.

**Can a website tell a virtual authenticator from a real one?** Only if that website
requests and verifies attestation, which almost none of them do for ordinary login.
Attestation defaults to "none," and a site using that default receives nothing that
distinguishes a virtual authenticator from a real one.

**What would actually expose it?** A relying party requesting "direct" attestation and
validating the returned certificate chain against a trusted root, or checking the
AAGUID against a known-device allowlist. That is common in banking, government and
enterprise deployments, and rare for ordinary consumer sites.

**Is the signature counter a reliable tell?** No, only a weak one. Real platform
authenticators frequently report a static counter too, so this signal alone catches
plenty of legitimate hardware.

**Can this be used to get into someone else's passkey-protected account?** No. It
still has to complete the same registration or authentication ceremony a real
authenticator would, against an account you can actually reach; it does not forge or
bypass a credential you do not already have legitimate access to.

**Does credentials.install() disable a real authenticator on the machine?**
Yes, per Playwright's own documentation: once installed, it "prevents all real
authenticators from working in this context," so page prompts are answered by the
virtual one instead.

## Sources

- Playwright's own [release notes](https://playwright.dev/docs/release-notes),
  version 1.61 section, retrieved 2026-08-30, for the exact feature-announcement quote
  above.
- [github.com/microsoft/playwright, release tag v1.61.0](https://github.com/microsoft/playwright/releases/tag/v1.61.0),
  retrieved 2026-08-30, confirming the release date and feature description.
- Playwright's own [`BrowserContext` API reference](https://playwright.dev/docs/api/class-browsercontext),
  retrieved 2026-08-30, for the `credentials` property description and the
  `credentials.install()` behavior quoted above.
- MDN Web Docs, [authenticator data](https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API/Authenticator_data),
  retrieved 2026-08-30, for the AAGUID definition and its stated relevance limited to
  enterprise/regulatory checks.
- This project's own notes on Firefox's Juggler protocol being closed-world, and on
  new client capabilities depending on this project's own upstream sync, linked above.

**See also:** [Why a Playwright upgrade broke 97 of 133 tests overnight](playwright-protocol-drift.md)
for why a brand-new Firefox command is not guaranteed to reach a patched binary on the
day it ships, [Playwright 1.62 Ships MCP In the Box](playwright-162-built-in-mcp.md)
for a same-era feature that, unlike this one, needed no new Juggler surface at all, and
[automating an email OTP / verification-link login with Playwright](automating-email-otp-verification-login-playwright.md)
for the equivalent honest pattern applied to a different second-factor flow.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. Whether a new Playwright
capability reaches this engine on day one is a Juggler-sync question before it is
anything else, and it is worth answering with a test run rather than an assumption.*
