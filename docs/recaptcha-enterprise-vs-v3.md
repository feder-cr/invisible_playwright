---
title: "reCAPTCHA Enterprise vs reCAPTCHA v3"
description: "reCAPTCHA Enterprise is not a harder version of v3's score, it is the same 0.0-1.0 model plus an explainable risk-analysis API, Account Defender, and a paid-by-default access model. What's different, from Google's own docs."
parent: "Detectors, Explained"
grand_parent: "Guides"
nav_order: 31
---


# reCAPTCHA Enterprise vs reCAPTCHA v3

This page assumes you already know what reCAPTCHA v3's score measures and why a fresh automated browser scores badly even with a clean fingerprint. [That is covered in full on its own page](recaptcha-v3-score.md), and this one does not repeat it. What follows is specifically what changes when a site upgrades to the Enterprise tier: what Enterprise adds, what it costs, and which parts are still, underneath, the same score-based model.

The short version: Enterprise is not a stricter v3. It is v3's scoring model wrapped in an assessment API that explains itself, extended with account-level behavioral detection v3 does not have, and sold as a metered Google Cloud product instead of a free embed.

## The core score has not changed, what you get back has

Both products are built on the same underlying idea: a request gets a risk score between 0.0 and 1.0. What differs is what the response contains and how much of the model's reasoning is visible to the site that integrated it.

**reCAPTCHA v3, free tier:** a score, and that is close to the whole payload. The site decides what to do with a 0.1 versus a 0.9 on its own, without Google explaining why the number landed where it did.

**reCAPTCHA Enterprise:** the score plus, per Google's own account, **score reasons**, specific factors annotating why an assessment came out where it did, and **risk analysis** that goes beyond a bare number into an explainable assessment. One community writeup on Google's own developer forum frames it as "detailed risk analysis, score reasons, fraud detection signals" layered on top of v3's model instead of replacing it. Migrating an existing v3 integration to Enterprise, per Google's own materials, can be done "in 5-10 minutes without code changes" for the score-based path, because the underlying score contract does not change; what changes is the tier of API you are calling and what comes back in the response.

Enterprise also formalizes three key types instead of one implicit mode: **score-based** keys (v3's model, silent), **checkbox** keys (an explicit widget, which Google's own documentation now recommends against: "We do not recommend using checkbox keys because they increase user friction and don't significantly improve accuracy"), and **policy-based challenge** keys, which combine the score with configurable thresholds per action to decide automatically when to escalate to a visible challenge, rather than leaving that decision entirely to the integrating site's own code.

## Account Defender: the feature v3 has no equivalent of

This is the clearest capability gap between the two tiers, and it is not a bigger score, it is a different question. reCAPTCHA v3 scores a single request. **Account Defender**, per Google's own product blog announcing it, is built to answer whether "an action aligns or deviates from the account owner's typical behavior," a question that only makes sense once you have an account to compare against, not a stateless page load.

Mechanically, per Google's own description: a site owner assigns each user a `hashedAccountId`, a hash rather than a plaintext username, and Account Defender builds a **site-specific model** of that account's typical behavior over time. It is aimed specifically at two failure modes v3's per-request score is not built to catch: **account takeover** (a login that looks nothing like this account's history) and **synthetic or bulk fake-account creation** (a wave of new accounts sharing behavioral fingerprints with each other). Google's materials describe it returning behavioral-validation signals ("this is expected for this user"), suspicious-activity flags on logins or account creation, and, when paired with the separate Annotations API, feeding back real outcomes (a wrong password, a passed two-factor check) so the model improves against ground truth rather than only its own predictions. Google frames a direct payoff for this: a documented use case reduces unnecessary MFA prompts by only stepping up the accounts Account Defender actually flags, which the company describes as cutting both user friction and SMS-authentication costs.

None of this exists in the free v3 product. There is no account concept in a bare score, only a request.

## Pricing: the free tier is smaller than v3's used to feel

reCAPTCHA Enterprise is a metered Google Cloud product, not a drop-in free script tag. Per Google's own billing documentation:

| Volume (per calendar month, per organization) | Cost |
|---|---|
| 0 to 10,000 assessments | Free |
| 10,001 to 100,000 assessments | $8 flat |
| Over 100,000 assessments | $0.001 per assessment ($1.00 per 1,000) |

The free allowance is an organization-wide aggregate, not per site or per key: Google's own wording is explicit that "the limit aggregates use across all accounts and all sites." A business running several properties under one organization shares one 10,000-assessment pool across all of them. Beyond the free tier, cost scales with traffic in a way a purely free reCAPTCHA embed never did, which is the practical reason a small site might stay on v3 rather than move up: Enterprise's extra signal is a paid feature, not a free upgrade.

Google's own documentation, as retrieved for this page, does not distinguish score-based assessment pricing from other assessment types (SMS, phone fraud checks, Account Defender lookups) within this tier table; treat the numbers above as the general assessment rate rather than a guarantee that every Enterprise feature bills identically.

## What this means for anything actually being scored

Nothing about Enterprise's extra machinery changes what a browser engine can honestly answer versus what it cannot, because Enterprise sits on top of the same behavioral-and-history model v3 uses, not a different mechanism. [The reasoning on the v3 page](recaptcha-v3-score.md) applies without modification: a fresh, historyless profile scores low because it looks like the base rate of automation, not because any fingerprint check failed, and a real engine answering every browser API honestly does not manufacture a browsing history, cookies from prior visits, or an account's own behavioral baseline. Account Defender specifically cannot be addressed by engine realness at all: it depends on a `hashedAccountId` and a history of actions tied to it, which is a property of the account and the site's own tracking, not of the browser running any single session.

`invisible_playwright` does not include an Account Defender workaround or a v3/Enterprise score-inflation feature, and this project makes no claim that engine-level realness changes an Enterprise assessment's score reasons or an Account Defender verdict. What honest engine behavior affects is exactly what it affects on the free tier: it removes fingerprint-level reasons to distrust a session, not the history-based ones.

## Short answers to the questions that lead here

**Is reCAPTCHA Enterprise just a stricter version of v3?** No. It is the same underlying score-based model, exposed through a richer API (score reasons, explainable risk analysis) and extended with account-level features v3 does not have, most notably Account Defender.

**Do I need to rewrite my integration to move from v3 to Enterprise?** For the score-based path, Google's own materials describe a migration on the order of 5 to 10 minutes with no code changes, because the score contract itself does not change.

**What does Account Defender actually add?** A behavioral model tied to a hashed account identifier, aimed at account takeover and synthetic/bulk account creation, questions a stateless per-request score was never built to answer.

**Is reCAPTCHA Enterprise free?** Up to 10,000 assessments per calendar month per organization (aggregated across every site and key under that organization), then $8 flat up to 100,000, then $1 per 1,000 beyond that, per Google's own billing documentation.

**Does Enterprise's checkbox mode work better than v3?** Google's own current guidance recommends against the checkbox key type specifically, citing added user friction without a meaningful accuracy gain, in favor of the score-based or policy-based challenge key types.

**Does a real browser engine answer Enterprise's assessment any differently than v3's?** No. Both sit on the same underlying score, and the reasoning that already applies to v3, engine realness clears fingerprint-level suspicion, not history-based suspicion, applies to Enterprise's score component unchanged. Account Defender is outside what any browser engine can address at all.

**See also:** [reCAPTCHA v3 score: why a fresh browser scores badly](recaptcha-v3-score.md) for the underlying scoring mechanism this page builds on, and [browser trust scores explained](browser-trust-score-explained.md) for how a reCAPTCHA-style score differs from a CreepJS-style consistency check.

## Sources

- Google Cloud, [Billing information for Google Cloud Fraud Defense](https://docs.cloud.google.com/recaptcha/docs/billing-information),
  retrieved 2026-08-30, for the exact free-tier size and the tiered pricing table quoted above.
- Google Cloud, [Create a key](https://docs.cloud.google.com/recaptcha/docs/create-key-website),
  retrieved 2026-08-30, for the score-based/checkbox/policy-based-challenge key types and the guidance against checkbox keys.
- Google Cloud Blog, ["Use Account Defender in reCAPTCHA Enterprise to protect accounts"](https://cloud.google.com/blog/products/identity-security/use-account-defender-in-recaptcha-enterprise-to-protect-accounts/),
  retrieved 2026-08-30, for the `hashedAccountId` mechanism, the account takeover / synthetic account detection framing, the Annotations API, and the MFA-friction reduction use case.
- Google Cloud Community, ["reCAPTCHA Enterprise: Choosing Between V2 and V3"](https://security.googlecloudcommunity.com/fraud-defense-recaptcha-41/recaptcha-enterprise-choosing-between-v2-and-v3-a-best-practice-guide-5123),
  retrieved 2026-08-30, for the score-reasons and explainable-assessment framing versus the free-tier score alone.
- Google's own reCAPTCHA v3 documentation, [reCAPTCHA v3](https://developers.google.com/recaptcha/docs/v3), retrieved 2026-08-28 (previously cited on the v3 page), for the underlying 0.0-1.0 scoring model both tiers share.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright), a Firefox
patched at the C++ level driven by stock Playwright. Enterprise's extra machinery is billing and
explainability on top of a score this project already writes about on the v3 page; nothing here
should be read as a claim that engine realness moves an Account Defender verdict, which depends on
account history this project has no access to.*
